"""REBUILT stage: assemble the *runtime package* a game-side loader consumes.

Layout (all local, git-ignored, never shipped):
  runtime/<SRC>-to-<TGT>/<canonical_id>/
    package.json      index: ids, versions, file hashes, status, adapter requirements
    geometry/*.sbmesh lossless meshes (Unity space)         textures/*.png  source + baked textures
    rig.json          skeleton + bone-enum table             parts.json      parts/LODs/renderers/material slots
    materials.json    translated recipes (template + overrides + fidelity)
    face.json         expression -> eye/mouth recipes        palette.json    species palette
    animation/        controller + clips filtered to the skeleton, native state map
    adapter.json      gameplay behaviours to re-implement    preview.glb     geometry+skeleton+animation preview
    provenance.json   install fingerprints, source hashes, legal notice
The loader format is documented in docs/RUNTIME_PACKAGE.md."""

import hashlib
import json
import shutil
import time
from pathlib import Path

from . import __version__, convert as conv, pipeline
from .export.gltf import export_glb
from .ir.store import remap_uv0_from_uv1, sha16
from .ir.writer import PackageWriter
from .safefs import sha256_json

BUILD_VERSION = 6
LEGAL = ('Contains data derived from the player\'s own installation of Slime Rancher / Slime Rancher 2. '
         'Local use only: do not redistribute, upload or commit. Unofficial; not affiliated with Monomi Park.')


def finite(o):
    """Strict-JSON copy: Unity's infinite tangents (a stepped key) become +/-1e30 (the loaders treat |slope| >= 1e9 as a step)."""
    if isinstance(o, float):
        if o != o:
            return 0.0
        return 1e30 if o == float('inf') else (-1e30 if o == float('-inf') else o)
    if isinstance(o, dict):
        return {k: finite(v) for k, v in o.items()}
    if isinstance(o, list):
        return [finite(v) for v in o]
    return o


def rid(ref):
    return 'm' + hashlib.sha256(ref.encode()).hexdigest()[:12]


def build_runtime(ctx, src_game, name, force=False):
    tgt = conv.other(src_game)
    doc = conv.convert(ctx, src_game, name)
    cid = doc['species']['canonical_id']
    base = f'runtime/{conv.direction_key(src_game)}/{cid}'
    params = {'v': BUILD_VERSION, 'tool': __version__, 'convert': doc['params_key'], 'status': doc['status']}
    pkey = sha256_json(params)[:16]
    existing = ctx.out.read_json(f'{base}/package.json')
    if existing and not force and existing.get('params_key') == pkey:
        ctx.log(f'[{src_game}:{cid}->{tgt}] runtime package up to date (cache hit)')
        return existing
    ctx.log(f'[{src_game}:{cid}->{tgt}] building runtime package...')
    t0 = time.time()
    root = ctx.out.root
    ir = root / doc['source_package']
    cv = root / 'converted' / conv.direction_key(src_game) / cid
    m = json.loads((ir / 'species.json').read_text(encoding='utf-8'))
    w = PackageWriter(ctx.out, base, strict=True)

    def copy_from(src_dir, rel, dst_rel=None):
        data = (src_dir / rel).read_bytes()
        return w.put_bytes(dst_rel or rel, data)

    # geometry + parts ------------------------------------------------------------------------------------
    nodes = m['prefab']['nodes']
    app_root = m['prefab']['appearance_root']
    base_path = nodes[app_root]['path']
    rel_path = lambda i: None if i is None else (nodes[i]['path'][len(base_path) + 1:] if i != app_root else '')
    recipes = doc['recipes']
    mat_ids = {ref: rid(ref) for ref in recipes}
    recipes_by_id = {mat_ids[ref]: r for ref, r in recipes.items()}
    parts = []
    for part in m['appearance']['parts']:
        lods = []
        for lod in part['lods']:
            mats = lod.get('override_materials') or part['default_materials']
            rends = []
            for r in lod['renderers']:
                me = r.get('mesh')
                if me is None:
                    rends.append({'node': r['node'], 'type': r['type'], 'mesh': None})
                    continue
                slots = [mat_ids.get(x['ref']) if x else None for x in mats]
                baked = any(sl and recipes_by_id[sl]['plan'] == 'bake_multi' for sl in slots)
                f, uv_remap = None, None
                if baked:
                    remapped = remap_uv0_from_uv1((ir / me['file']).read_bytes())
                    if remapped is not None:
                        f = w.put_bytes(f"geometry/{sha16(remapped)}.sbmesh", remapped)
                        uv_remap = 'uv0 := uv1 (bake atlas); original uv0 kept as uv3'
                    else:
                        uv_remap = 'none needed: mesh has no uv1, the bake used its uv0 directly'
                if f is None:
                    f = copy_from(ir, me['file'], f"geometry/{Path(me['file']).name}")
                rends.append({'node': r['node'], 'type': r['type'], 'mesh_file': f, 'uv_remap': uv_remap, 'mesh_name': me['name'], 'vertex_count': me['vertex_count'],
                              'submeshes': me['submeshes'], 'skinned': me['skinned'], 'transform': r['transform'],
                              'cast_shadows': r.get('cast_shadows'), 'receive_shadows': r.get('receive_shadows'),
                              'material_slots': [mat_ids.get(x['ref']) if x else None for x in mats]})
            lods.append({'lod': lod['lod'], 'renderers': rends, 'bones': [rel_path(b) for b in lod['attached_bones']],
                         'root_bone': rel_path(lod['root_bone']), 'parent_bone': rel_path(lod['parent_bone']),
                         'rubber_type': lod.get('rubber_type')})
        parts.append({'element': part['element'], 'name': part['name'], 'type': part['type'], 'supports_faces': part['supports_faces'],
                      'face_rules': part['face_rules'], 'casts_shadows': part['casts_shadows'], 'lods': lods})
    w.put_json('parts.json', parts)

    rig_nodes = []
    skeleton = [app_root] + m['prefab']['skeleton']
    index = {i: k for k, i in enumerate(skeleton)}
    for i in skeleton:
        n = nodes[i]
        tr = {k: n['transform'][k] for k in ('position', 'rotation', 'scale')} if n['transform'] else None
        if i == app_root:  # the rig is expressed relative to the Appearance root; its own placement is kept aside
            tr = {'position': [0.0, 0.0, 0.0], 'rotation': [0.0, 0.0, 0.0, 1.0], 'scale': [1.0, 1.0, 1.0]}
        rig_nodes.append({'name': n['name'], 'path': rel_path(i), 'parent': index.get(n['parent']) if i != app_root else None, 'transform': tr})
    app_local = nodes[app_root]['transform']
    w.put_json('rig.json', {'root': 'Appearance', 'space': 'unity-left-handed', 'nodes': rig_nodes,
                            'appearance_local_transform': app_local and {k: app_local[k] for k in ('position', 'rotation', 'scale')},
                            'bone_enum': {k: rel_path(v) for k, v in m['prefab']['bone_table'].items()}})

    # materials + textures ---------------------------------------------------------------------------------------
    out_recipes = {}
    for ref, r in recipes.items():
        r = json.loads(json.dumps(r))
        for prop, t in r['target'].get('textures', {}).items():
            if t['transform'] == 'baked':
                t['file'] = copy_from(cv, t['file'], t['file'])
            else:
                t['file'] = copy_from(ir, t['file'], t['file'])
        out_recipes[mat_ids[ref]] = r
    w.put_json('materials.json', {'target': tgt, 'recipes': out_recipes,
                                  'colorspace': {'source': ctx.install(src_game)['color_space'], 'target': ctx.install(tgt)['color_space']}})
    face = m['appearance']['face']
    w.put_json('face.json', {'name': face['name'] if face else None,
                             'expressions': [{'expression': e['expression'], 'eyes': e.get('eyes') and mat_ids.get(e['eyes']['ref']),
                                              'mouth': e.get('mouth') and mat_ids.get(e['mouth']['ref'])} for e in (face['expressions'] if face else [])]})
    w.put_json('palette.json', {'palette': m['appearance']['palette'], 'splat_color': m['appearance']['extras'].get('splat_color'),
                                'species_color': m['species'].get('color')})

    icon = m['appearance'].get('icon')
    if icon:
        copy_from(ir, icon['file'], 'icon/' + Path(icon['file']).name)
        w.put_json('icon/icon-descriptor.json', icon['descriptor'])

    # animation ------------------------------------------------------------------------------------------------------------
    skel_paths = {n['path'] for n in rig_nodes if n['path']}
    clips_out = {}
    for asset, meta in m['animation']['clips'].items():
        clip = json.loads((ir / meta['file']).read_text(encoding='utf-8'))
        dropped = 0
        for grp in ('positions', 'rotations', 'eulers', 'scales', 'floats', 'pptr'):
            keep = [c for c in clip[grp] if c['path'] in skel_paths or (c['path'] == '' or grp in ('floats', 'pptr'))]
            dropped += len(clip[grp]) - len(keep)
            clip[grp] = keep
        clip['dropped_foreign_curves'] = dropped
        clip = finite(clip)
        f = w.put_json(f"animation/clips/{hashlib.sha256(json.dumps(clip, sort_keys=True).encode()).hexdigest()[:16]}.json", clip)
        clips_out[asset] = {'name': clip['name'], 'file': f, 'length': clip['length'], 'loop': clip['loop'], 'dropped_foreign_curves': dropped}
    w.put_json('animation/controller.json', {'controller': m['animation']['controller'], 'avatar': m['animation']['avatar'],
                                              'overrides': m['animation']['overrides'], 'clips': clips_out, 'plan': doc['animation']})
    w.put_json('adapter.json', doc['adapter'])
    # preview + provenance -----------------------------------------------------------------------------------------------
    glb_rel = f'preview.glb'
    tmp = ctx.out.resolve(f'{base}/preview.glb')
    glb_info = export_glb(ir, tmp)
    data = tmp.read_bytes()
    w.files[glb_rel] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
    w.put_json('provenance.json', {'legal': LEGAL, 'source_game': src_game, 'target_game': tgt, 'source_fingerprint': m['source']['fingerprint'],
                                   'source_files': m['provenance']['sources'], 'source_recognition': m['source']['recognition'],
                                   'tool_version': __version__, 'engines': m['source']['engine']})
    levels = doc['material_summary']['by_level']
    package = {
        'schema': 'sbconv-runtime', 'schema_version': 1, 'tool_version': __version__, 'params_key': pkey,
        'species': doc['species'], 'direction': doc['direction'], 'target_info': doc['target_info'],
        'material_fidelity': doc['material_summary'], 'animation_summary': {'clips': len(clips_out), 'unmapped_states': doc['animation'].get('unmapped_states'),
                                                                           'unmapped_params': doc['animation'].get('unmapped_params')},
        'adapter_requirements': {'missing_scripts': doc['adapter']['missing_in_target']},
        'preview': glb_info, 'legal': LEGAL, 'problems': doc['problems'],
        'status': 'REBUILT' if doc['status'] == 'CONVERTED' else 'INCOMPLETE',
    }
    package['files'] = dict(sorted(w.files.items()))
    w.put_json('package.json', package)
    pipeline.record(ctx, f'{src_game}:{cid}->{tgt}', game=src_game, target=tgt, canonical_id=cid, name=doc['species']['name'],
                    status=package['status'], stage='build', worst_material_fidelity=doc['material_summary']['worst_fidelity'],
                    seconds=round(time.time() - t0, 1), package=base)
    return package
