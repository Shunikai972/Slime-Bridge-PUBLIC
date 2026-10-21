"""IR package -> SlimeBridge ``bridge-native-mesh`` v2 asset cache (the format the SlimeBridge SR1/SR2 plugins read).

Layout (identical to the repo's Lab/AssetCache/<dir>/<canonical_id>/):
  model.json  cache-manifest.json  dump-receipt.json  icon-descriptor.json  <icon>.source.png
  source-animation-provenance.json  tex-source-<id>-<name>.png ...  (+ sbconv-recipes.json: our material recipes)
The cache carries the *source* game's data (like the repo's own extractor); the receiving plugin applies its own material
rebinding. Our translated recipes ride along in ``sbconv-recipes.json`` (hash-covered, ignored by older readers).
Everything stays local and git-ignored."""

import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np

from .. import __version__
from ..ir.curves import eval_vector
from ..ir.store import unpack_mesh
from ..safefs import sha256_bytes

CACHE_SCHEMA = 'bridge-asset-cache-manifest'
MODEL_SCHEMA = 'bridge-native-mesh'
FLOAT_MAX = 3.4e38
MODEL_LIMIT = 14 * 1024 * 1024        # the plugin refuses model.json above 16 MiB; stay below with margin
CANONICAL_RE = re.compile(r'^slime\.[a-z0-9_.-]{1,80}$')


class BridgeExportError(ValueError):
    pass


def bridge_canonical_id(canonical_id, largo_style='slime-compat'):
    """The plugin accepts only ``slime.<...>`` ids. Largos (``largo.a.b``) are emitted as ``slime.largo.a.b`` unless
    ``largo_style='native'`` (then the plugin's canonical-id check must be extended to accept ``largo.``)."""
    if canonical_id.startswith('largo.') and largo_style == 'slime-compat':
        return 'slime.' + canonical_id
    return canonical_id


def _num(x):
    x = float(x)
    if not math.isfinite(x) or abs(x) > FLOAT_MAX:
        return None
    if x != 0 and abs(x) < 1.5e-45:
        return 0.0
    return round(x, 6) if abs(x) < 1e6 else x


def _rows(a, nd=None):
    return [[_num(v) if _num(v) is not None else 0.0 for v in row] for row in np.asarray(a, dtype=np.float64).tolist()]


def _safe(name):
    s = re.sub(r'[^A-Za-z0-9._-]+', '_', name).strip('._') or 'tex'
    return s[:60]


def _color(v):
    return [x if x is not None else 0.0 for x in (_num(c) for c in v)]


def _prop_dict(d):
    out = {}
    for k, v in d.items():
        if not isinstance(k, str) or not k or len(k) > 256 or any(ord(c) < 32 for c in k):
            continue
        n = _num(v) if not isinstance(v, (list, tuple)) else None
        if n is not None:
            out[k] = n
    return out


class _Ctx:
    def __init__(self, pkg, m):
        self.pkg, self.m = pkg, m
        self.files = {}      # cache file name -> bytes
        self.tex_name = {}   # package texture rel -> cache file name

    def texture(self, rel, ref):
        if rel in self.tex_name:
            return self.tex_name[rel]
        info = self.m['textures'].get(ref, {})
        pid = re.search(r'#(-?\d+)$', ref or '')
        pid = abs(int(pid.group(1))) if pid else 0
        name = f"tex-source-{pid}-{_safe(info.get('name', 'texture'))}.png"
        if name in self.files:
            name = f"tex-source-{pid}-{_safe(info.get('name', 'texture'))}-{hashlib.sha256(rel.encode()).hexdigest()[:6]}.png"
        self.files[name] = (self.pkg / rel).read_bytes()
        self.tex_name[rel] = name
        return name


def _material(cx, mat, face_role=None):
    pid = re.search(r'#(-?\d+)$', mat['ref'])
    out = {'name': mat['name'], 'source_path_id': abs(int(pid.group(1))) if pid else 0, 'source_material': True,
           'shader': mat['shader'] or '', 'colors': {k: _color(v) for k, v in mat['colors'].items() if isinstance(k, str) and k and len(k) <= 256},
           'floats': _prop_dict(mat['floats']), 'render_queue': max(-1, min(5000, int(mat.get('render_queue', -1)))),
           'keywords': list(mat['keywords']), 'textures': []}
    if face_role:
        out['face_role'] = face_role
    for prop, t in sorted(mat['textures'].items()):
        if t.get('file'):
            info = cx.m['textures'].get(t['texture'], {})
            out['textures'].append({'property': prop, 'file': cx.texture(t['file'], t['texture']), 'scale': [_num(t['scale'][0]) or 0.0, _num(t['scale'][1]) or 0.0],
                                    'offset': [_num(t['offset'][0]) or 0.0, _num(t['offset'][1]) or 0.0],
                                    'source_color_space': 1 if info.get('srgb', True) else 0, 'linear': not info.get('srgb', True)})
    return out


def _tr(t):
    return {'position': [_num(x) or 0.0 for x in t['position']], 'rotation': [_num(x) or 0.0 for x in t['rotation']], 'scale': [_num(x) or 0.0 for x in t['scale']]}


def build_model(pkg_dir, m, canonical):
    pkg = Path(pkg_dir)
    cx = _Ctx(pkg, m)
    nodes = m['prefab']['nodes']
    root = m['prefab']['appearance_root']
    if root is None:
        raise BridgeExportError('prefab has no Appearance root')
    skeleton_ids = m['prefab']['skeleton']
    index = {i: k for k, i in enumerate(skeleton_ids)}
    base_path = nodes[root]['path']
    rel = lambda i: nodes[i]['path'][len(base_path) + 1:]
    names = [nodes[i]['name'] for i in skeleton_ids]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup or any(re.search(r'[\\/\0]', n) for n in names):
        raise BridgeExportError(f'bone names must be unique and slash-free (duplicates: {dup[:5]})')
    if len(skeleton_ids) > 1024:
        raise BridgeExportError('more than 1024 bones')
    bones = []
    for i in skeleton_ids:
        n = nodes[i]
        t = n['transform'] or {'position': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
        q = np.asarray(t['rotation'], dtype=float)
        q = (q / np.linalg.norm(q)).tolist() if np.linalg.norm(q) > 0 else [0, 0, 0, 1]
        bones.append({'name': n['name'], 'parent': index.get(n['parent'], -1) if n['parent'] != root else -1,
                      'p': [_num(x) or 0.0 for x in t['position']], 'q': [_num(x) or 0.0 for x in q], 's': [_num(x) or 0.0 for x in t['scale']]})
    app_t = nodes[root]['transform'] or {'position': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
    prefab_name = nodes[m['prefab']['root']]['name']
    skinned, static = [], []
    face = m['appearance']['face']
    for part in m['appearance']['parts']:
        lod0 = next((l for l in part['lods'] if l['lod'] == 0), None)
        if not lod0:
            continue
        is_body = part['element'].startswith('DefaultBody') or part.get('type') == 3
        is_face = part['element'] == 'SlimeFace' or part.get('type') == 1
        role = 'body' if is_body else ('face' if is_face else part['element'])
        mats = lod0.get('override_materials') or part['default_materials']
        for r in lod0['renderers']:
            me = r.get('mesh')
            if not me:
                continue
            hdr, a = unpack_mesh((pkg / me['file']).read_bytes())
            tris = [a[sm['index_array']].astype(np.int64).tolist() for sm in hdr['submeshes'] if sm['topology'] == 0]
            doc = {'enabled': True, 'role': role, 'mesh_name': me['name'], 'vertices': _rows(a['positions']),
                   'normals': _rows(a['normals']) if 'normals' in a else [], 'tangents': _rows(a['tangents']) if 'tangents' in a else [],
                   'uv': _rows(a['uv0']) if 'uv0' in a else [], 'uv2': _rows(a['uv1']) if 'uv1' in a else [],
                   'colors': _rows(a['colors']) if 'colors' in a else [], 'triangles': tris}
            t = r['transform'] or {'position': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
            doc['local_position'], doc['local_rotation'], doc['local_scale'] = _tr(t)['position'], _tr(t)['rotation'], _tr(t)['scale']
            materials = []
            for k, mat in enumerate(mats):
                if mat is None:
                    continue
                fr = None
                if is_face:
                    fr = 'base-null' if mat['shader'] == 'SR/Null Render' else None
                materials.append(_material(cx, mat, fr))
            if is_face and face:
                seen = set()
                for e in face['expressions']:
                    for kind in ('eyes', 'mouth'):
                        x = e.get(kind)
                        if x and x['ref'] not in seen:
                            seen.add(x['ref'])
                            materials.append(_material(cx, x, 'eyes' if kind == 'eyes' else 'mouth'))
            doc['materials'] = materials
            is_skinned = r['type'] == 'SkinnedMeshRenderer' and 'bone_indices' in a and lod0['attached_bones'] and all(b is not None for b in lod0['attached_bones'])
            if is_skinned:
                slot_names = [nodes[b]['name'] for b in lod0['attached_bones']]
                bw = np.concatenate([a['bone_weights'].astype(np.float64), a['bone_indices'].astype(np.float64)], axis=1)
                sums = bw[:, :4].sum(axis=1)
                bw[:, :4] = bw[:, :4] / np.where(sums > 0, sums, 1.0)[:, None]
                doc['path'] = f"{prefab_name}/{role}_{me['name']}"
                doc['bind_poses'] = _rows(a['bind_poses'].reshape(-1, 16))
                doc['bone_weights'] = _rows(bw)
                doc['skin_bones'] = slot_names
                doc['root_bone'] = nodes[lod0['root_bone']]['name'] if lod0.get('root_bone') is not None else slot_names[0]
                skinned.append(doc)
            else:
                parent = lod0.get('parent_bone')
                parent_path = rel(parent) if parent is not None and parent != root else ''
                doc['path'] = (parent_path + '/' if parent_path else '') + f"{role}_{me['name']}"
                doc['parent_path'] = parent_path
                doc['binding_mode'] = 'rigid-parent'
                doc['bind_poses'], doc['bone_weights'] = [], []
                static.append(doc)
    if not any(p['role'] == 'body' for p in skinned + static):
        # species with their own body mesh (SR1 Quicksilver, Saber, Tarr...): the largest skinned non-face part is the body
        cands = [d for d in skinned if d['role'] != 'face']
        if not cands:
            raise BridgeExportError('no skinned body part in the species appearance')
        best = max(cands, key=lambda d: len(d['vertices']))
        best['role'] = 'body'
        best['path'] = f"{prefab_name}/body_{best['mesh_name']}"
    model = {'schema': MODEL_SCHEMA, 'schema_version': 2, 'source_game': m['source']['game'], 'source_kind': 'offline-unitypy',
             'reference_id': f"SlimeDefinition.{m['species']['name']}", 'display_name': m['species'].get('display') or m['species']['name'],
             'prefab_name': prefab_name, 'prefab_scale': float(m['species'].get('prefab_scale') or 1.0), 'visual_root': '',
             'visual_transform': {'local_position': _tr(app_t)['position'], 'local_rotation': _tr(app_t)['rotation'], 'local_scale': _tr(app_t)['scale']},
             'skeleton': {'bone_count': len(bones), 'bones': bones}, 'skinned_renderers': skinned, 'static_renderers': static}
    model['source_animation'] = _animation(pkg, m, [rel(i) for i in skeleton_ids])
    return model, cx


def _animation(pkg, m, bone_paths, rate=30.0):
    paths = set(bone_paths)
    ctrl = m['animation']['controller']
    default_asset = None
    if ctrl and ctrl['layers']:
        st = next((s for s in ctrl['layers'][0]['states'] if s['name'] == ctrl['layers'][0]['default_state']), None)
        if st and st['motion'] and st['motion']['kind'] == 'clip':
            default_asset = st['motion']['asset']
    clips, used = [], set()
    for asset, meta in m['animation']['clips'].items():
        clip = json.loads((pkg / meta['file']).read_text(encoding='utf-8'))
        dur = float(clip['length'])
        if dur <= 0 or dur > 3600:
            continue
        n = int(math.ceil(dur * rate)) + 1
        if n > 4096:
            n = 4096
        times = np.linspace(0.0, dur, n)
        times = np.unique(np.round(times, 6))
        times[-1] = min(times[-1], dur)
        name = clip['name']
        k = 2
        while name in used:
            name = f"{clip['name']}_{k}"
            k += 1
        used.add(name)
        channels = []
        for grp, prop, width in (('positions', 'local_position', 3), ('rotations', 'local_rotation', 4), ('scales', 'local_scale', 3)):
            seen = set()
            for c in clip[grp]:
                if c['path'] not in paths or not c['keys'] or c['path'] in seen:
                    continue
                seen.add(c['path'])
                v = eval_vector(c['keys'], times, width)
                if prop == 'local_rotation':
                    v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
                channels.append({'path': c['path'], 'property': prop, 'values': _rows(v)})
        if len(channels) > 3072:
            raise BridgeExportError(f'clip {name} has more than 3072 channels')
        clips.append({'name': name, 'duration': dur, 'loop': bool(clip['loop']), 'sample_rate': rate, 'times': [float(t) for t in times],
                      'channels': channels, '_asset': asset})
    if len(clips) > 64:
        clips = clips[:64]
    default = next((c['name'] for c in clips if c['_asset'] == default_asset), clips[0]['name'] if clips else '')
    for c in clips:
        c.pop('_asset')
    return {'default_clip': default, 'clips': clips}


def export(pkg_dir, out_dir, largo_style='slime-compat', recipes_path=None, icon_fallback=None):
    """Write the cache directory. Raises BridgeExportError with the precise reason if the species cannot be exported."""
    pkg = Path(pkg_dir)
    m = json.loads((pkg / 'species.json').read_text(encoding='utf-8'))
    canonical = bridge_canonical_id(m['species']['canonical_id'], largo_style)
    if not CANONICAL_RE.match(canonical):
        raise BridgeExportError(f'canonical id {canonical!r} is not accepted by the plugin ({CANONICAL_RE.pattern})')
    icon = m['appearance'].get('icon')
    icon_pkg = pkg
    fallback_note = None
    if not icon and icon_fallback:
        icon_pkg, icon, parent_id = Path(icon_fallback[0]), icon_fallback[1], icon_fallback[2]
        fallback_note = f'parent:{parent_id}'
    if not icon:
        reasons = [p['message'] for p in m['problems'] if p['code'] == 'ICON_UNAVAILABLE']
        raise BridgeExportError('icon is mandatory in the cache but unavailable: ' + (reasons[0] if reasons else 'not extracted (re-extract with the current tool)'))
    model, cx = build_model(pkg, m, canonical)
    size = 0
    raw = b''
    for rate in (30.0, 20.0, 15.0, 10.0):
        model['source_animation'] = _animation(pkg, m, _bone_paths(model), rate)
        raw = json.dumps(model, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(raw) <= MODEL_LIMIT:
            break
    if len(raw) > MODEL_LIMIT:
        raise BridgeExportError(f'model.json is {len(raw) / 1e6:.1f} MB (limit 16 MiB) even at 10 Hz sampling')
    files = {'model.json': raw}
    files.update(cx.files)
    icon_png = (icon_pkg / icon['file']).read_bytes()
    desc = icon['descriptor']
    files[desc['file']] = icon_png
    files['icon-descriptor.json'] = (json.dumps(desc, indent=2, allow_nan=False) + '\n').encode('utf-8')
    prov = {'selection': 'explicit-source-clip-ids', 'runtime_graph_reproduced': False,
            'source_references': {'controller_name': (m['animation']['controller'] or {}).get('name'),
                                  'clip_names': [c['name'] for c in model['source_animation']['clips']],
                                  'appearance_overrides': m['animation']['overrides'], 'prefab': m['prefab']['ref'], 'definition': m['species']['definition']},
            'source_files': m['provenance']['sources'], 'generator': f'sbconv {__version__}'}
    files['source-animation-provenance.json'] = (json.dumps(prov, indent=2, allow_nan=False) + '\n').encode('utf-8')
    if recipes_path and Path(recipes_path).is_file():
        files['sbconv-recipes.json'] = Path(recipes_path).read_bytes()
    receipt = {'schema': 'bridge-native-mesh-receipt', 'schema_version': 2, 'target': m['species']['name'], 'reference_id': model['reference_id'],
               'skinned_renderers': len(model['skinned_renderers']), 'static_renderers': len(model['static_renderers']), 'model': 'model.json',
               'model_sha256': sha256_bytes(raw),
               'vertex_total': sum(len(r['vertices']) for r in model['skinned_renderers'] + model['static_renderers']),
               'parts': [{'role': r['role'], 'mesh': r['mesh_name'], 'vertices': len(r['vertices'])} for r in model['skinned_renderers'] + model['static_renderers']],
               'materials': [r['materials'][0]['name'] if r['materials'] else None for r in model['skinned_renderers'] + model['static_renderers']],
               'textures': [{'file': n, 'sha256': sha256_bytes(b)} for n, b in sorted(files.items()) if n.endswith('.png') and n.startswith('tex-')],
               'source': {'kind': 'offline-unitypy', 'tool': f'sbconv {__version__}'}}
    files['dump-receipt.json'] = (json.dumps(receipt, indent=2, allow_nan=False) + '\n').encode('utf-8')
    manifest = {'schema': CACHE_SCHEMA, 'schema_version': 1, 'canonical_id': canonical, 'converter_version': f'sbconv-{__version__}',
                'source_game': m['source']['game'], 'source_run_id': 'sbconv-' + sha256_bytes(raw)[:16],
                'files': {n: sha256_bytes(b) for n, b in sorted(files.items())}, 'icon': desc, 'sbconv_canonical_id': m['species']['canonical_id']}
    if fallback_note:
        manifest['icon_fallback'] = fallback_note
    files['cache-manifest.json'] = (json.dumps(manifest, indent=2, allow_nan=False) + '\n').encode('utf-8')
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for n, b in files.items():
        (out / n).write_bytes(b)
    return {'canonical_id': canonical, 'files': len(files), 'model_bytes': len(raw), 'clips': len(model['source_animation']['clips'])}


def _bone_paths(model):
    bones = model['skeleton']['bones']
    out = []
    for i, b in enumerate(bones):
        parts, j = [b['name']], b['parent']
        while j != -1:
            parts.append(bones[j]['name'])
            j = bones[j]['parent']
        out.append('/'.join(reversed(parts)))
    return out
