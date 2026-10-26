"""CONVERTED stage: adapt an extracted species (source game) to the constraints of the other game.

Produces ``converted/<SRC>-to-<TGT>/<canonical_id>/convert.json`` (+ baked textures) holding material recipes,
the adapted rig/animation plan and the list of gameplay behaviours the target runtime adapter must provide.
Nothing is copied from the source game's scripts: behaviour is reduced to metadata plus an adapter plan."""

import io
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__, pipeline
from .extract.species import canonical_id
from .ir.store import unpack_mesh
from .ir.writer import PackageWriter
from .materials.translate import Translator, family, rules_fingerprint
from .safefs import sha256_json

CONVERT_VERSION = 1


def direction_key(src):
    return f'{src}-to-{"SR2" if src == "SR1" else "SR1"}'


def other(game):
    return 'SR2' if game == 'SR1' else 'SR1'


def _used_materials(m):
    """Materials actually referenced by the species look: part materials (all LODs) + face expressions."""
    mats = {}
    for part in m['appearance']['parts']:
        for x in part['default_materials']:
            if x:
                mats[x['ref']] = x
        for lod in part['lods']:
            for x in lod.get('override_materials') or []:
                if x:
                    mats[x['ref']] = x
    face = m['appearance']['face']
    for e in (face['expressions'] if face else []):
        for k in ('eyes', 'mouth'):
            if e.get(k):
                mats[e[k]['ref']] = e[k]
    return mats


def _mesh_users(pkg, m):
    """material ref -> list of mesh dicts (LOD0 only) for UV-space baking."""
    users = {}
    for part in m['appearance']['parts']:
        lod0 = next((l for l in part['lods'] if l['lod'] == 0), None)
        if not lod0:
            continue
        mats = lod0.get('override_materials') or part['default_materials']
        for r in lod0['renderers']:
            me = r.get('mesh')
            if not me:
                continue
            hdr, arrs = unpack_mesh((pkg / me['file']).read_bytes())
            tris = [arrs[sm['index_array']].reshape(-1, 3).astype(int) for sm in hdr['submeshes'] if sm['topology'] == 0]
            for si, sm in enumerate(hdr['submeshes']):
                mat = mats[min(si, len(mats) - 1)] if mats else None
                if not mat:
                    continue
                d = {'positions': arrs['positions'], 'uv0': arrs['uv0'] if 'uv0' in arrs else np.zeros((len(arrs['positions']), 2), np.float32),
                     'uv1': arrs['uv1'] if 'uv1' in arrs else None,
                     'tris': [arrs[sm['index_array']].reshape(-1, 3).astype(int)] if sm['topology'] == 0 else []}
                users.setdefault(mat['ref'], []).append(d)
    return users


def _animation_plan(ctx, m, tgt):
    """Compare the source controller with the target game's native slime controller (state/param names)."""
    ctrl = m['animation'].get('controller')
    plan = {'source_controller': ctrl['name'] if ctrl else None, 'native_target_controller': None, 'state_map': {}, 'param_map': {}, 'clips': []}
    anim_t = ctx.anim(tgt)
    native = anim_t.controller('NewRigAnimator') if anim_t else None
    if native:
        plan['native_target_controller'] = native['name']
        tstates = {s['name']: s for l in native['layers'] for s in l['states']}
        tparams = {p['name'] for p in native['parameters']}
        if ctrl:
            for l in ctrl['layers']:
                for s in l['states']:
                    plan['state_map'][s['name']] = s['name'] if s['name'] in tstates else None
            for p in ctrl['parameters']:
                plan['param_map'][p['name']] = p['name'] if p['name'] in tparams else None
    if ctrl:
        plan['unmapped_states'] = sorted(k for k, v in plan['state_map'].items() if v is None)
        plan['unmapped_params'] = sorted(k for k, v in plan['param_map'].items() if v is None)
    for asset, meta in m['animation']['clips'].items():
        plan['clips'].append({'asset': asset, 'name': meta['name'], 'file': meta['file'], 'length': meta['length'], 'loop': meta['loop'],
                              'dropped_foreign_bone_curves': meta['missing_paths']})
    return plan


def _adapter_plan(ctx, m, tgt):
    scripts = set()
    for rel, rec in ctx.inventory(tgt).items():
        for _, _, t, n in rec['objects']:
            if t == 'MonoScript' and n:
                scripts.add(n)
    need = []
    for b in m['gameplay']['behaviours']:
        need.append({'script': b['script'], 'path': b['path'], 'target_has_script': b['script'] in scripts})
    missing = sorted({n['script'] for n in need if not n['target_has_script']})
    return {'behaviours': need, 'missing_in_target': missing, 'colliders': m['gameplay']['colliders'], 'rigidbodies': m['gameplay']['rigidbodies'],
            'note': 'Scripts missing in the target game cannot be copied: the SR1/SR2 runtime adapter must re-implement the behaviour '
                    '(e.g. Ringtail petrification) from this metadata.'}


def convert(ctx, src_game, name, force=False):
    tgt = other(src_game)
    manifest = pipeline.extract(ctx, src_game, name)
    cid = manifest['species']['canonical_id']
    key = f'{src_game}:{cid}'
    base = f'converted/{direction_key(src_game)}/{cid}'
    params = {'v': CONVERT_VERSION, 'tool': __version__, 'src': ctx.fingerprint_key(src_game), 'tgt': ctx.fingerprint_key(tgt),
              'extract': manifest.get('params_key'), 'rules': rules_fingerprint()}
    pkey = sha256_json(params)[:16]
    existing = ctx.out.read_json(f'{base}/convert.json')
    if existing and not force and existing.get('params_key') == pkey:
        ctx.log(f'[{key}->{tgt}] up to date (cache hit)')
        return existing
    ctx.log(f'[{key}->{tgt}] converting...')
    t0 = time.time()
    pkg = ctx.out.root / pipeline.ir_dir(src_game, cid)
    templates = ctx.templates(tgt)
    w = PackageWriter(ctx.out, base)
    tex_info = manifest['textures']
    users = _mesh_users(pkg, manifest)

    def load_tex(rel):
        return Image.open(pkg / rel)

    def write_tex(data):
        import hashlib
        rel = f'textures/{hashlib.sha256(data).hexdigest()[:16]}.png'
        w.put_bytes(rel, data)
        return rel

    tr = Translator(src_game, tgt, templates, tex_info, load_tex, write_tex, mesh_users=lambda ref: users.get(ref, []))
    recipes = {}
    for ref, mat in _used_materials(manifest).items():
        recipes[ref] = tr.translate(mat)
    levels = [r['fidelity']['level'] for r in recipes.values()]
    worst = max(levels, key=lambda l: ['exact', 'equivalent', 'approximate', 'degraded', 'unsupported'].index(l)) if levels else 'exact'
    tgt_defs = [r for r in ctx.catalog(tgt) if 'error' not in r]
    native = next((r for r in tgt_defs if canonical_id(r) == cid), None)
    doc = {
        'schema': 'sbconv-convert', 'schema_version': 1, 'tool_version': __version__, 'params_key': pkey,
        'direction': {'source': src_game, 'target': tgt},
        'species': manifest['species'],
        'target_info': {'native_definition_in_target': bool(native), 'native_name': native and native['name'],
                        'unity_version': ctx.install(tgt)['unity']['version'], 'pipeline': ctx.install(tgt)['graphics'].get('pipeline'),
                        'color_space': ctx.install(tgt)['color_space']},
        'source_package': pipeline.ir_dir(src_game, cid),
        'recipes': recipes,
        'material_summary': {'count': len(recipes), 'worst_fidelity': worst, 'by_level': {l: levels.count(l) for l in sorted(set(levels))},
                             'unsupported': [r['source']['name'] for r in recipes.values() if r['fidelity']['level'] == 'unsupported']},
        'animation': _animation_plan(ctx, manifest, tgt),
        'adapter': _adapter_plan(ctx, manifest, tgt),
        'scale': {'source_prefab_scale': manifest['species'].get('prefab_scale')},
        'problems': [],
        'status': 'CONVERTED',
    }
    core_unsupported = [r['source']['name'] for r in recipes.values() if r['fidelity']['level'] == 'unsupported' and r['family'] in ('body', 'face', 'special')]
    fx_unsupported = [r['source']['name'] for r in recipes.values() if r['fidelity']['level'] == 'unsupported' and r['family'] not in ('body', 'face', 'special')]
    if core_unsupported:
        doc['status'] = 'INCOMPLETE'
        doc['problems'].append({'level': 'error', 'code': 'MATERIAL_UNSUPPORTED',
                                'message': f'body/face materials without a target equivalent: {core_unsupported}'})
    if fx_unsupported:
        doc['problems'].append({'level': 'warning', 'code': 'EFFECT_UNSUPPORTED',
                                'message': f'effect materials without a target equivalent (hand-written adapter needed): {fx_unsupported}'})
    doc['material_summary']['unsupported_core'] = core_unsupported
    doc['material_summary']['unsupported_effects'] = fx_unsupported
    if manifest['status'] != 'EXTRACTED':
        doc['status'] = 'INCOMPLETE'
        doc['problems'].append({'level': 'error', 'code': 'SOURCE_INCOMPLETE', 'message': 'source extraction is INCOMPLETE'})
    doc['files'] = dict(sorted(w.files.items()))
    w.put_json('convert.json', doc)
    pipeline.record(ctx, f'{src_game}:{cid}->{tgt}', game=src_game, target=tgt, canonical_id=cid, name=manifest['species']['name'],
                    status=doc['status'], stage='convert', worst_material_fidelity=worst, seconds=round(time.time() - t0, 1),
                    package=base)
    return doc
