"""OFFLINE verification of a runtime package (see sbconv.build). Same honesty rule as verify.offline: it
proves internal consistency of what a loader will receive, not in-engine rendering."""

import hashlib
import json
from pathlib import Path

import numpy as np

from ..ir.curves import eval_vector
from ..ir.rig import world_matrices, skin_vertices
from ..ir.store import unpack_mesh
from .offline import Report


def pose_deformation(base, rig, parts, clips_meta, max_clips=8, samples=4):
    """Evaluate clips on the skeleton and skin the LOD0 meshes: returns {clip: worst displacement / mesh size} and
    the list of non-finite results. Proves curve decoding, path mapping and skinning agree in motion."""
    nodes = rig['nodes']
    by_path = {n['path']: i for i, n in enumerate(nodes)}
    rends = []
    for part in parts:
        lod = next((l for l in part['lods'] if l['lod'] == 0), None)
        for r in (lod['renderers'] if lod else []):
            if r.get('mesh_file') and r['skinned'] and lod['bones'] and all(b in by_path for b in lod['bones']):
                hdr, arrs = unpack_mesh((base / r['mesh_file']).read_bytes())
                if len(lod['bones']) == len(arrs['bind_poses']):
                    rends.append((hdr, arrs, [by_path[b] for b in lod['bones']]))
    out, bad = {}, []
    rest_world = world_matrices([{'parent': n['parent'], 'transform': n['transform']} for n in nodes])
    for asset, meta in list(clips_meta.items())[:max_clips]:
        clip = json.loads((base / meta['file']).read_text(encoding='utf-8'))
        length = max(clip['length'], 1e-3)
        worst = 0.0
        for t in np.linspace(0, length * 0.999, samples):
            local = [{'position': list(n['transform']['position']), 'rotation': list(n['transform']['rotation']), 'scale': list(n['transform']['scale'])}
                     for n in nodes]
            for grp, key, width in (('positions', 'position', 3), ('rotations', 'rotation', 4), ('scales', 'scale', 3)):
                for c in clip[grp]:
                    i = by_path.get(c['path'])
                    if i is None or not c['keys']:
                        continue
                    v = eval_vector(c['keys'], [t], width)[0]
                    if key == 'rotation':
                        v = v / max(np.linalg.norm(v), 1e-9)
                    local[i][key] = [float(x) for x in v]
            world = world_matrices([{'parent': n['parent'], 'transform': local[i]} for i, n in enumerate(nodes)])
            for hdr, arrs, bone_idx in rends:
                bw = np.stack([world[b] for b in bone_idx])
                sk = skin_vertices(arrs['positions'].astype(float), arrs['bone_indices'].astype(int), arrs['bone_weights'].astype(float),
                                   bw, arrs['bind_poses'].astype(float), np.eye(4))
                if not np.isfinite(sk).all():
                    bad.append(meta['name'])
                    continue
                ext = float(np.max(hdr['bounds_extent'])) or 1.0
                worst = max(worst, float(np.max(np.linalg.norm(sk - arrs['positions'], axis=1))) / (2 * ext))
        out[meta['name']] = worst
    return out, sorted(set(bad)), len(rends)


def verify_runtime(pkg_dir, templates=None, ir_dir=None, rest_pose_tolerance=0.02):
    base = Path(pkg_dir)
    R = Report()
    try:
        pk = json.loads((base / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        R.add('package.readable', False, str(e))
        return R, None
    R.add('package.schema', pk.get('schema') == 'sbconv-runtime' and pk.get('schema_version') == 1, f"{pk.get('schema')} v{pk.get('schema_version')}")
    bad = []
    for rel, meta in pk['files'].items():
        p = base / rel
        if not p.is_file():
            bad.append(f'{rel}: missing')
        elif hashlib.sha256(p.read_bytes()).hexdigest() != meta['sha256']:
            bad.append(f'{rel}: sha256 mismatch')
    R.add('files.integrity', not bad, f"{len(pk['files'])} files" + (f'; {bad[:4]}' if bad else ''))

    rig = json.loads((base / 'rig.json').read_text(encoding='utf-8'))
    parts = json.loads((base / 'parts.json').read_text(encoding='utf-8'))
    mats = json.loads((base / 'materials.json').read_text(encoding='utf-8'))
    paths = {n['path'] for n in rig['nodes']}
    R.add('rig.paths_unique', len(paths) == len(rig['nodes']), f"{len(rig['nodes'])} nodes")
    R.add('rig.parents_valid', all(n['parent'] is None or 0 <= n['parent'] < len(rig['nodes']) for n in rig['nodes']), '')

    # materials -----------------------------------------------------------------------------------------------------
    problems = []
    tshaders = (templates or {}).get('shaders')
    for rid, r in mats['recipes'].items():
        if r['plan'] == 'unsupported' or r['fidelity']['level'] == 'unsupported':
            if r['family'] not in ('body', 'face', 'special'):
                continue
            problems.append(f"{r['source']['name']}: unsupported ({r['fidelity']['notes'][-1][:80] if r['fidelity']['notes'] else ''})")
            continue
        sh = r['target'].get('shader')
        if tshaders is not None and sh not in tshaders and r['plan'] != 'passthrough':
            problems.append(f"{r['source']['name']}: target shader {sh} unknown in target install")
        for prop, t in r['target'].get('textures', {}).items():
            if not (base / t['file']).is_file():
                problems.append(f"{r['source']['name']}.{prop}: texture file missing")
        if r['plan'] not in ('passthrough', 'unsupported') and not r['target'].get('template'):
            problems.append(f"{r['source']['name']}: no native template to clone")
    R.add('materials.recipes_complete', not problems, f"{len(mats['recipes'])} recipes; {problems[:4]}")
    levels = pk['material_fidelity']['by_level']
    core = pk['material_fidelity'].get('unsupported_core', [])
    R.add('materials.no_unsupported_core', not core, f'body/face materials without target equivalent: {core}; histogram {levels}')
    if pk['material_fidelity'].get('unsupported_effects'):
        R.warn('materials.unsupported_effects', f"effects needing a hand-written adapter: {pk['material_fidelity']['unsupported_effects']}")
    bakes = [r for r in mats['recipes'].values() if r['plan'] == 'bake_multi']
    for r in bakes:
        cov = r.get('bake', {}).get('coverage', 0)
        if cov < 0.001:
            R.add('materials.bake_coverage', False, f"{r['source']['name']}: baked texture is empty ({cov:.4%} of UV space covered)")
        elif cov < 0.02:
            R.warn('materials.bake_coverage', f"{r['source']['name']}: only {cov:.1%} of the texture is covered (small part)")

    # parts / geometry / rig ------------------------------------------------------------------------------------------------
    nodes = rig['nodes']
    local_nodes = [{'parent': n['parent'], 'transform': n['transform']} for n in nodes]
    world = world_matrices(local_nodes)
    by_path = {n['path']: i for i, n in enumerate(nodes)}
    miss, worst, checked = [], 0.0, 0
    for part in parts:
        for lod in part['lods']:
            for b in lod['bones']:
                if b is None or b not in by_path:
                    miss.append(f"{part['element']}:{b}")
            for r in lod['renderers']:
                if r.get('mesh_file') is None:
                    continue
                if not (base / r['mesh_file']).is_file():
                    miss.append(f"{r['mesh_file']}")
                    continue
                if any(s is None or s not in mats['recipes'] for s in r['material_slots']):
                    miss.append(f"{part['element']} LOD{lod['lod']}: material slot unresolved")
                if len(r['material_slots']) < r['submeshes']:
                    R.warn('parts.material_slots', f"{part['element']} LOD{lod['lod']}: {len(r['material_slots'])} slot(s) for {r['submeshes']} submesh(es) (last slot repeats)")
                if any(sl and mats['recipes'][sl]['plan'] == 'bake_multi' for sl in r['material_slots'] if sl in mats['recipes']):
                    if not r.get('uv_remap'):
                        miss.append(f"{part['element']} LOD{lod['lod']}: baked material without UV remap (texture would be mis-mapped)")
                if lod['lod'] == 0 and r['skinned'] and lod['bones']:
                    hdr, arrs = unpack_mesh((base / r['mesh_file']).read_bytes())
                    if all(b in by_path for b in lod['bones']) and len(lod['bones']) == len(arrs['bind_poses']):
                        bw = np.stack([world[by_path[b]] for b in lod['bones']])
                        sk = skin_vertices(arrs['positions'].astype(float), arrs['bone_indices'].astype(int), arrs['bone_weights'].astype(float),
                                           bw, arrs['bind_poses'].astype(float), np.eye(4))
                        ext = float(np.max(hdr['bounds_extent'])) or 1.0
                        worst = max(worst, float(np.max(np.linalg.norm(sk - arrs['positions'], axis=1))) / (2 * ext))
                        checked += 1
    R.add('parts.references_resolved', not miss, f'{len(parts)} parts; unresolved: {miss[:4]}')
    if checked:
        R.add('rig.rest_pose_skinning', worst <= rest_pose_tolerance, f'{checked} skinned LOD0 renderer(s); worst deviation {worst:.4f}')

    # animation ---------------------------------------------------------------------------------------------------------------------
    an = json.loads((base / 'animation' / 'controller.json').read_text(encoding='utf-8'))
    bad_paths = []
    for asset, meta in an['clips'].items():
        clip = json.loads((base / meta['file']).read_text(encoding='utf-8'))
        for grp in ('positions', 'rotations', 'eulers', 'scales'):
            bad_paths += [c['path'] for c in clip[grp] if c['path'] not in paths]
    R.add('animation.paths_in_rig', not bad_paths, f"{len(an['clips'])} clips; stray paths {bad_paths[:3]}")
    defo, nonfinite, nr = pose_deformation(base, rig, parts, an['clips'])
    if nr:
        R.add('animation.deformation_finite', not nonfinite, f'{len(defo)} clip(s) skinned on {nr} renderer(s); non-finite: {nonfinite}')
        wild = {k: round(v, 2) for k, v in defo.items() if v > 3.0}
        big = {k: round(v, 2) for k, v in defo.items() if 2.0 < v <= 3.0}
        R.add('animation.deformation_bounded', not wild, f'worst per-clip displacement (fraction of mesh size): {({k: round(v, 2) for k, v in defo.items()})}' + (f'; implausible: {wild}' if wild else ''))
        if big:
            R.warn('animation.large_deformation', f'clips displacing vertices more than the mesh size (squash/stretch or a bad curve; inspect): {big}')
    plan = an['plan']
    if plan.get('unmapped_states'):
        R.warn('animation.native_states', f"target game has no native state for {plan['unmapped_states']}: adapter must drive these clips")
    if plan.get('unmapped_params'):
        R.warn('animation.native_params', f"target controller lacks parameters {plan['unmapped_params']}")

    face = json.loads((base / 'face.json').read_text(encoding='utf-8'))
    unresolved = [e['expression'] for e in face['expressions'] for k in ('eyes', 'mouth') if e.get(k) and e[k] not in mats['recipes']]
    R.add('face.recipes_resolved', not unresolved, f"{len(face['expressions'])} expressions")
    ad = json.loads((base / 'adapter.json').read_text(encoding='utf-8'))
    if ad['missing_in_target']:
        R.warn('adapter.missing_scripts', f"{len(ad['missing_in_target'])} source behaviour(s) have no script in the target game: {ad['missing_in_target'][:8]}")
    return R, pk
