"""Explicit material translation: source material -> *recipe* for the target game.

A recipe never contains shader code. It names a target shader that already exists in the target game, a
native template material to clone at runtime, the property overrides to apply, textures (copied or baked)
and an honest fidelity statement. Rules live in registry/material_rules.json and in the target game's own
template library (property contracts read from its install)."""

import json
import re
from pathlib import Path

import numpy as np

TRANSLATOR_VERSION = 5
RULES_PATH = Path(__file__).resolve().parent.parent / 'registry' / 'material_rules.json'
RULES = json.loads((Path(__file__).resolve().parent.parent / 'registry' / 'material_rules.json').read_text(encoding='utf-8'))
_INFRA = [re.compile(p) for p in RULES['infrastructure_props']]
FIDELITY = ('exact', 'equivalent', 'approximate', 'degraded', 'unsupported')


def is_infra(name):
    return any(r.match(name) for r in _INFRA)


def srgb_to_linear(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.asarray(c, dtype=float)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(np.maximum(c, 0), 1 / 2.4) - 0.055)


def convert_hdr(rgba, src_game, tgt_game):
    """HDR colour props: raw linear in the Linear project (SR2) vs display value in the Gamma project (SR1)."""
    rgb = np.array(rgba[:3], dtype=float)
    if src_game == tgt_game:
        return list(rgba)
    scale = max(1.0, float(rgb.max()))
    if src_game == 'SR2' and tgt_game == 'SR1':
        out = linear_to_srgb(rgb / scale) * scale          # keep >1 intensity, re-encode hue
    else:
        out = srgb_to_linear(rgb / scale) * scale
    return [float(x) for x in out] + [float(rgba[3])]


def rules_fingerprint():
    import hashlib
    return hashlib.sha256(RULES_PATH.read_bytes()).hexdigest()[:12] + f'.v{TRANSLATOR_VERSION}'


def family(mat):
    sh = mat.get('shader') or ''
    if sh.startswith('SR/AMP/Slime/Face/') or sh in ('SR/Slime/Eyes', 'SR/Slime/Mouth', 'SR/Slime/Eyes Feral'):
        return 'face'
    if sh == 'SR/Null Render':
        return 'null'
    if sh.startswith(('SR/AMP/Slime/Body', 'SR/AMP/Slime/Crystal Attachment', 'SR/Slime/Crystal Attachment')):
        return 'body'
    if sh.startswith(('SR/Slime/', 'SR/AMP/Slime/Mosaic', 'SR/Actor/Slime/')):
        return 'special'      # species-specific body shaders (Mosaic, Tarr, Glitch, Quantum...)
    return 'fx'


def shader_candidates(shader):
    """Same shader under both naming schemes used by the games ('SR/Slime/X' <-> 'SR/AMP/Slime/X')."""
    out = [shader]
    if shader.startswith('SR/Slime/'):
        out.append('SR/AMP/Slime/' + shader[len('SR/Slime/'):])
    if shader.startswith('SR/AMP/Slime/'):
        out.append('SR/Slime/' + shader[len('SR/AMP/Slime/'):])
    return out


def _features_sr2(mat):
    f = mat['floats']
    feats = {k for k, prop in RULES['sr2_body_feature_floats'].items() if f.get(prop, 0.0) > 0.5}
    coloring = next((k[len('_BODYCOLORING_'):] for k in mat['keywords'] if k.startswith('_BODYCOLORING_')), 'DEFAULT')
    return feats, coloring


def _features_sr1(mat):
    sh = (mat.get('shader') or '').replace('SR/AMP/Slime/Body/', '').replace('SR/AMP/Slime/Body', '')
    feats = {RULES['sr1_shader_tokens_to_sr2_features'][t] for t in re.split(r'\s+', sh) if t in RULES['sr1_shader_tokens_to_sr2_features']}
    return feats, sh


def _best_example(tpl_shader, wanted_feats, game):
    """Pick the native example whose feature toggles are closest to what we need (data-driven, deterministic)."""
    best, score = None, None
    for ex in tpl_shader.get('examples', []):
        feats = set()
        if game == 'SR2':
            feats = {k for k, prop in RULES['sr2_body_feature_floats'].items() if ex['floats'].get(prop, 0.0) > 0.5}
        sc = (len(feats ^ wanted_feats), ex['name'])
        if score is None or sc < score:
            best, score = ex, sc
    return best


class Translator:
    def __init__(self, src_game, tgt_game, templates, texture_info, load_texture, write_texture, mesh_users=None):
        """templates: target game's library; texture_info: ref->info (source package); load_texture(rel)->PIL/np;
        write_texture(png_bytes)->rel in the converted package; mesh_users(mat_ref)->meshes for baking."""
        self.src, self.tgt, self.tpl = src_game, tgt_game, templates
        self.tex_info, self.load_texture, self.write_texture, self.mesh_users = texture_info, load_texture, write_texture, mesh_users
        self.rules = RULES

    # -- generic helpers --------------------------------------------------------------------------
    def _contract(self, shader):
        return self.tpl['shaders'].get(shader)

    def _tex_entries(self, mat, allowed):
        out = {}
        for prop, t in mat['textures'].items():
            if t.get('file') and prop in allowed:
                info = self.tex_info.get(t['texture'], {})
                out[prop] = {'file': t['file'], 'scale': t['scale'], 'offset': t['offset'], 'format': info.get('format'),
                             'srgb': info.get('srgb', True), 'size': [info.get('width'), info.get('height')], 'transform': None}
        return out

    def _copy_semantic(self, mat, contract, notes):
        floats, colors, dropped = {}, {}, {'floats': [], 'colors': [], 'textures': [], 'infrastructure': 0}
        cf, cc = set(contract['floats']), set(contract['colors'])
        hdr = set(self.rules['hdr_color_props'])
        for k, v in mat['floats'].items():
            if is_infra(k):
                dropped['infrastructure'] += 1
            elif k in cf:
                floats[k] = v
            else:
                dropped['floats'].append(k)
        for k, v in mat['colors'].items():
            if is_infra(k):
                dropped['infrastructure'] += 1
            elif k in cc:
                colors[k] = convert_hdr(v, self.src, self.tgt) if k in hdr else v
            else:
                dropped['colors'].append(k)
        for k, t in mat['textures'].items():
            if t.get('file') and k not in contract['textures'] and not is_infra(k):
                dropped['textures'].append(k)
        return floats, colors, dropped

    @staticmethod
    def _recipe(mat, fam, plan, shader, fidelity, notes):
        return {'source': {'name': mat['name'], 'shader': mat['shader'], 'ref': mat['ref'], 'keywords': mat['keywords']},
                'family': fam, 'plan': plan, 'target': {'shader': shader}, 'fidelity': {'level': fidelity, 'notes': notes}}

    # -- faces -------------------------------------------------------------------------------------------
    def face(self, mat):
        notes = []
        key = f'{self.src}->{self.tgt}'
        names = set(self.rules['shader_names'][self.tgt]['face'])
        shader = mat['shader']
        if shader not in names:
            shader2 = self.rules['face_fallbacks'].get(key, {}).get(shader)
            if shader2 is None:
                shader2 = 'SR/AMP/Slime/Face/Mouth Default' if 'Mouth' in shader else 'SR/AMP/Slime/Face/Eyes Default'
            notes.append(f'shader {shader} does not exist in {self.tgt}; mapped to {shader2}')
            shader = shader2
        contract = self._contract(shader)
        level = 'exact' if shader == mat['shader'] else 'approximate'
        if contract is None:
            r = self._recipe(mat, 'face', 'face_direct', shader, 'unsupported', notes + ['no native template for this shader in the target game'])
            r['target'].update(floats={}, colors={}, textures={}, keywords=[])
            return r
        floats, colors, dropped = self._copy_semantic(mat, contract, notes)
        textures = self._tex_entries(mat, set(contract['textures']))
        if dropped['floats'] or dropped['colors'] or dropped['textures']:
            notes.append(f"properties without a counterpart in the target shader were dropped: {dropped['floats'] + dropped['colors'] + dropped['textures']}")
            if level == 'exact':
                level = 'equivalent'
        ex = _best_example(contract, set(), self.tgt)
        kws = [k for k in mat['keywords'] if k in set(contract['keywords'])] if contract['keywords'] else []
        r = self._recipe(mat, 'face', 'face_direct', shader, level, notes)
        r['target'].update(floats=floats, colors=colors, textures=textures, keywords=kws,
                           template=ex and {'ref': ex['ref'], 'name': ex['name']})
        r['dropped'] = dropped
        return r

    # -- bodies --------------------------------------------------------------------------------------------
    def body(self, mat):
        return self._body_sr2_to_sr1(mat) if (self.src, self.tgt) == ('SR2', 'SR1') else self._body_sr1_to_sr2(mat)

    def _sr1_shader_for(self, feats):
        order = ['quantum', 'phos', 'rad', 'normals', 'stripes', 'boom']
        tokens = {'quantum': 'Quantum', 'phos': 'PhosCore', 'rad': 'Rad', 'normals': 'Normal', 'stripes': 'Stripe', 'boom': 'BoomCracks'}
        names = set(self.rules['shader_names']['SR1']['body'])
        want = [f for f in order if f in feats]
        for cut in range(len(want) + 1):
            use = want if cut == 0 else want[:-cut]
            nm = 'SR/AMP/Slime/Body/' + (' '.join(tokens[f] for f in use) if use else 'Default')
            if nm in names:
                return nm, [f for f in want if f not in use]
        return 'SR/AMP/Slime/Body/Default', want

    def _body_sr2_to_sr1(self, mat):
        feats, coloring = _features_sr2(mat)
        notes = []
        needs_bake = coloring in ('MULTI', 'SLOOMBER') or 'twin' in feats
        if needs_bake:
            return self._bake_plan(mat, feats, coloring, notes)
        shader, lost = self._sr1_shader_for(feats)
        if lost:
            notes.append(f'SR1 has no shader variant combining all of {sorted(feats)}; dropped features: {lost}')
        contract = self._contract(shader)
        floats, colors, dropped = self._copy_semantic(mat, contract, notes) if contract else ({}, {}, {})
        textures = self._tex_entries(mat, set(contract['textures'] if contract else []))
        for prop, t in textures.items():
            if prop == '_StripeTexture':
                notes.append('SR2 _StripeTexture mask convention is not verified against SR1 (SR2 _ColorMask is inverted vs SR1 _StripeTexture; see docs/MATERIALS.md)')
        level = 'equivalent' if not lost and not (dropped and (dropped['floats'] or dropped['textures'])) else 'approximate'
        notes.append('SR2 is HDRP/Linear, SR1 is Built-in/Gamma: lighting, SSS and tonemapping differ; colours copied as authored')
        ex = _best_example(contract, set(), 'SR1') if contract else None
        r = self._recipe(mat, 'body', 'native_variant', shader, level, notes)
        r['target'].update(floats=floats, colors=colors, textures=textures, keywords=[], template=ex and {'ref': ex['ref'], 'name': ex['name']})
        r['dropped'] = dropped
        return r

    def _bake_plan(self, mat, feats, coloring, notes):
        from . import bake
        shader = 'SR/AMP/Slime/Body/Attachment'
        contract = self._contract(shader) or {'floats': [], 'colors': [], 'textures': ['_Diffuse'], 'keywords': [], 'examples': []}
        mask_prop = '_ColorMask'
        mt = mat['textures'].get(mask_prop)
        if coloring == 'MULTI' and (not mt or not mt.get('file')):
            return self._recipe(mat, 'body', 'bake_multi', shader, 'unsupported', ['_BODYCOLORING_MULTI material has no _ColorMask texture'])
        if coloring == 'SLOOMBER' or 'twin' in feats:
            notes.append(f'{coloring if coloring != "MULTI" else "twin"} effect has no SR1 equivalent: only the static palette is baked')
        users = self.mesh_users(mat['ref']) if self.mesh_users else []
        if not users:
            return self._recipe(mat, 'body', 'bake_multi', shader, 'unsupported', notes + ['no mesh uses this material: UV-space bake impossible'])
        img = np.asarray(self.load_texture(mt['file']).convert('RGBA'), dtype=np.float32) / 255.0 if mt and mt.get('file') else np.ones((256, 256, 4), np.float32)
        rgba, rep = bake.bake_multi(mat, users, img)
        from PIL import Image
        import io
        buf = io.BytesIO()
        Image.fromarray(rgba, 'RGBA').save(buf, 'PNG')
        rel = self.write_texture(buf.getvalue())
        floats, colors, dropped = self._copy_semantic(mat, contract, notes)
        notes += ['body colours baked into _Diffuse (UV space) using the documented colour model; view-dependent SR2 shading (SSS, rim, '
                  'twin/vortex animation) is not reproduced', 'baked texture is sRGB; _Color is forced to white and _COLORMASK is off']
        colors['_Color'] = [1.0, 1.0, 1.0, 1.0]
        r = self._recipe(mat, 'body', 'bake_multi', shader, 'approximate', notes)
        ex = _best_example(contract, set(), 'SR1')
        r['target'].update(floats=floats, colors=colors, keywords=[],
                           textures={'_Diffuse': {'file': rel, 'scale': [1, 1], 'offset': [0, 0], 'format': 'RGBA32', 'srgb': True,
                                                  'size': rep['size'], 'transform': 'baked'}},
                           template=ex and {'ref': ex['ref'], 'name': ex['name']})
        r['bake'] = rep
        r['dropped'] = dropped
        return r

    def _body_sr1_to_sr2(self, mat):
        feats, token = _features_sr1(mat)
        notes = []
        shader = 'SR/AMP/Slime/Body'
        contract = self._contract(shader)
        if mat['shader'] in ('SR/AMP/Slime/Body/Attachment', 'SR/AMP/Slime/Body/Quantum Attachment'):
            shader = 'SR/AMP/Slime/Crystal Attachment'
            contract = self._contract(shader)
            notes.append('SR1 Attachment maps to SR2 "Crystal Attachment" (diffuse + normal + saturation mask)')
        if contract is None:
            return self._recipe(mat, 'body', 'native_variant', shader, 'unsupported', ['target shader contract unavailable'])
        floats, colors, dropped = self._copy_semantic(mat, contract, notes)
        textures = self._tex_entries(mat, set(contract['textures']))
        keywords = []
        for k in sorted(feats):
            prop = self.rules['sr2_body_feature_floats'].get(k)
            if prop and prop in set(contract['floats']):
                floats[prop] = 1.0
                keywords.append(f"_{prop[1:].upper()}_ON")
        if 'stripes' in feats and '_StripeTexture' in textures:
            notes.append('SR1 _StripeTexture convention (bright = plain body) copied unchanged; SR2 stripe convention not verified')
        keywords.append('_BODYCOLORING_DEFAULT')
        lost = [k for k in feats if k not in self.rules['sr2_body_feature_floats']]
        level = 'equivalent'
        if dropped['floats'] or dropped['textures'] or lost:
            level = 'approximate'
            notes.append(f"properties without SR2 counterpart dropped: {dropped['floats'] + dropped['colors'] + dropped['textures']}")
        notes.append('SR1 is Built-in/Gamma, SR2 is HDRP/Linear: the cloned SR2 template supplies all HDRP infrastructure properties')
        ex = _best_example(contract, feats, 'SR2')
        r = self._recipe(mat, 'body', 'template_uber', shader, level, notes)
        r['target'].update(floats=floats, colors=colors, textures=textures, keywords=keywords, template=ex and {'ref': ex['ref'], 'name': ex['name']})
        r['dropped'] = dropped
        return r

    # -- everything else --------------------------------------------------------------------------------------------
    def other(self, mat, fam):
        shader = mat['shader']
        contract, notes = None, []
        for cand in shader_candidates(shader):
            contract = self._contract(cand)
            if contract is not None:
                if cand != shader:
                    notes.append(f'shader {shader} is named {cand} in {self.tgt}')
                    shader = cand
                break
        if contract is None:
            fb = self.rules.get('shader_fallbacks', {}).get(f'{self.src}->{self.tgt}', {}).get(shader)
            if fb and self._contract(fb) is not None:
                notes.append(f'no {self.tgt} equivalent of {shader}; documented fallback {fb} used')
                shader, contract = fb, self._contract(fb)
        if fam == 'null':
            r = self._recipe(mat, fam, 'passthrough', shader, 'equivalent', ['Null Render placeholder: face quad is drawn by the face materials'])
            r['target'].update(floats={}, colors={}, textures={}, keywords=[])
            return r
        if contract is None:
            return self._recipe(mat, fam, 'unsupported', None, 'unsupported',
                                [f'shader {shader} does not exist in {self.tgt}; effect material needs a hand-written adapter'])
        floats, colors, dropped = self._copy_semantic(mat, contract, notes)
        textures = self._tex_entries(mat, set(contract['textures']))
        ex = _best_example(contract, set(), self.tgt)
        r = self._recipe(mat, fam, 'fx_same_shader' if fam == 'fx' else 'special_same_shader', shader, 'approximate',
                         notes + ['shader exists in both games; blend/lighting model differs by render pipeline'])
        r['target'].update(floats=floats, colors=colors, textures=textures, keywords=[], template=ex and {'ref': ex['ref'], 'name': ex['name']})
        r['dropped'] = dropped
        return r

    def translate(self, mat):
        if not mat.get('shader'):
            return self._recipe(mat, 'unknown', 'unsupported', None, 'unsupported',
                                ['the source material has no shader reference (stripped or unfinished asset in the source game); '
                                 'nothing can be translated'])
        fam = family(mat)
        if fam == 'face':
            return self.face(mat)
        if fam == 'body':
            return self.body(mat)
        return self.other(mat, fam)  # includes 'special' and 'fx'
