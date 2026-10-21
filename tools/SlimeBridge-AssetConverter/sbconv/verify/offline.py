"""Offline verification of an IR package (no game, no Unity). A package only reaches OFFLINE_VERIFIED when
every check below passes; warnings are reported but do not hide failures. These checks prove internal
consistency (hashes, references, geometry, rig, animation) - NOT that the target game renders it
correctly; that needs UNITY_VERIFIED / GAME_RUNTIME_VERIFIED evidence."""

import hashlib
import json
from pathlib import Path

import numpy as np

from ..ir.rig import world_matrices, skin_vertices
from ..ir.store import unpack_mesh


class Report:
    def __init__(self):
        self.checks = []

    def add(self, cid, ok, detail='', level=None):
        self.checks.append({'id': cid, 'ok': bool(ok), 'level': level or ('pass' if ok else 'fail'), 'detail': detail})

    def warn(self, cid, detail):
        self.checks.append({'id': cid, 'ok': True, 'level': 'warn', 'detail': detail})

    @property
    def passed(self):
        return all(c['ok'] for c in self.checks)

    def summary(self):
        return {'passed': self.passed, 'checks': len(self.checks), 'failed': [c for c in self.checks if not c['ok']],
                'warnings': [c for c in self.checks if c['level'] == 'warn']}


def _load(base: Path):
    return json.loads((base / 'species.json').read_text(encoding='utf-8'))


def verify_package(base, rest_pose_tolerance=0.02):
    base = Path(base)
    R = Report()
    try:
        m = _load(base)
    except (OSError, ValueError) as e:
        R.add('manifest.readable', False, str(e))
        return R, None
    R.add('manifest.schema', m.get('schema') == 'sbconv-ir' and m.get('schema_version') == 1, f"{m.get('schema')} v{m.get('schema_version')}")

    # 1. integrity -------------------------------------------------------------------------------
    bad = []
    for rel, meta in m['files'].items():
        p = base / rel
        if not p.is_file():
            bad.append(f'{rel}: missing')
        elif hashlib.sha256(p.read_bytes()).hexdigest() != meta['sha256']:
            bad.append(f'{rel}: sha256 mismatch')
    R.add('files.integrity', not bad, f'{len(m["files"])} files' + (f'; {bad[:5]}' if bad else ''))
    R.add('provenance.sources', bool(m['provenance']['sources']) and all(len(v) == 64 for v in m['provenance']['sources'].values()),
          f"{len(m['provenance']['sources'])} source file hash(es)")
    R.add('provenance.fingerprint', bool(m['source']['fingerprint']), 'install fingerprint recorded')

    # 2. dependencies ------------------------------------------------------------------------------
    blocking = [p for p in m['problems'] if p['level'] == 'error']
    R.add('extraction.no_errors', not blocking, '; '.join(f"{p['code']}:{p['message'][:80]}" for p in blocking[:4]))
    missing_tex, unresolved_shader, no_shader = [], [], []
    for key, mat in m['materials'].items():
        if mat.get('shader_unresolved'):
            unresolved_shader.append(mat['name'])
        if not mat.get('shader'):
            no_shader.append(mat['name'])
        for prop, t in mat['textures'].items():
            if (t.get('texture') or t.get('unresolved')) and not t.get('file'):
                missing_tex.append(f"{mat['name']}.{prop}")
    R.add('materials.textures_present', not missing_tex, f'{len(m["materials"])} materials; missing: {missing_tex[:6]}')
    R.add('materials.shader_identified', not no_shader, f'unidentified shader on: {no_shader[:6]}')
    if unresolved_shader:
        R.warn('materials.shader_external', f'shader object lives outside the loaded sources for: {sorted(set(unresolved_shader))[:6]} (name still known)')

    # 3. geometry ----------------------------------------------------------------------------------
    meshes = {}
    for key, me in m['meshes'].items():
        try:
            hdr, arrs = unpack_mesh((base / me['file']).read_bytes())
        except Exception as e:
            R.add(f'mesh.{me["name"]}.readable', False, str(e))
            continue
        meshes[key] = (me, hdr, arrs)
        ok = np.isfinite(arrs['positions']).all() and hdr['vertex_count'] == arrs['positions'].shape[0]
        for sm in hdr['submeshes']:
            idx = arrs[sm['index_array']]
            ok = ok and len(idx) > 0 and idx.max() < hdr['vertex_count']
        if not ok:
            R.add(f'mesh.{me["name"]}.valid', False, 'non-finite data or indices out of range')
        for p in me.get('problems', []):
            R.warn(f'mesh.{me["name"]}.check', p)
    R.add('geometry.meshes_valid', all(c['ok'] for c in R.checks if c['id'].startswith('mesh.')), f'{len(meshes)} meshes decoded')

    # 4. rig + rest pose ------------------------------------------------------------------------------
    nodes = m['prefab']['nodes']
    par_ok = all(n['parent'] is None or 0 <= n['parent'] < len(nodes) for n in nodes)
    R.add('rig.hierarchy', par_ok, f'{len(nodes)} nodes')
    world = world_matrices(nodes)
    # Parts are parented under the Appearance root and bind poses are authored in that space, so the
    # rest pose is checked relative to it (the root's own placement in the prefab is irrelevant).
    app_root = m['prefab'].get('appearance_root')
    ref_inv = np.linalg.inv(world[app_root]) if app_root is not None else np.eye(4)
    worst = 0.0
    checked = 0
    for part in m['appearance']['parts']:
        lod0 = next((l for l in part['lods'] if l['lod'] == 0), None)
        if not lod0:
            continue
        for r in lod0['renderers']:
            me = r.get('mesh')
            if not me or r['type'] != 'SkinnedMeshRenderer' or me['source'] not in meshes:
                continue
            _, hdr, arrs = meshes[me['source']]
            if 'bone_indices' not in arrs or 'bind_poses' not in arrs:
                continue
            att = lod0['attached_bones']
            if any(a is None for a in att) or len(att) != len(arrs['bind_poses']):
                R.add(f'rig.{part["element"]}.bones', False, f'attached={len(att)} bindposes={len(arrs["bind_poses"])} unresolved={sum(a is None for a in att)}')
                continue
            bone_world = np.stack([ref_inv @ world[a] for a in att])
            skinned = skin_vertices(arrs['positions'].astype(float), arrs['bone_indices'].astype(int), arrs['bone_weights'].astype(float),
                                    bone_world, arrs['bind_poses'].astype(float), np.eye(4))
            ext = float(np.max(hdr['bounds_extent'])) or 1.0
            dev = float(np.max(np.linalg.norm(skinned - arrs['positions'], axis=1))) / (2 * ext)
            worst = max(worst, dev)
            checked += 1
            if dev > rest_pose_tolerance:
                R.add(f'rig.{part["element"]}.rest_pose', False, f'rest-pose skinning deviates {dev:.3f} of mesh size')
    if checked:
        R.add('rig.rest_pose_skinning', worst <= rest_pose_tolerance, f'{checked} skinned part(s); worst deviation {worst:.4f} of mesh size')
    else:
        R.warn('rig.rest_pose_skinning', 'no skinned LOD0 part to test')

    # 5. animation ----------------------------------------------------------------------------------------
    an = m['animation']
    if an['controller']:
        clips = an['clips']
        names = {s['name'] for l in an['controller']['layers'] for s in l['states']}
        params = {p['name'] for p in an['controller']['parameters']}
        bad_tr, bad_param, missing_clip = [], [], []
        for l in an['controller']['layers']:
            for s in l['states']:
                mo = s['motion']
                if mo and mo['kind'] == 'clip' and mo['asset'] not in clips:
                    missing_clip.append(mo['asset'])
                for t in s['transitions']:
                    if t['dst'] is not None and t['dst'] not in names:
                        bad_tr.append((s['name'], t['dst']))
                    bad_param += [c['param'] for c in t['conditions'] if c['param'] not in params]
        R.add('animation.controller_consistent', not (bad_tr or bad_param or missing_clip),
              f"{len(names)} states, {len(params)} params, {len(clips)} clips; bad transitions {bad_tr[:3]} params {bad_param[:3]} clips {missing_clip[:3]}")
        R.add('animation.clips_nonempty', all(c['length'] > 0 and sum(c['counts'].values()) > 0 for c in clips.values()),
              f"{len(clips)} clips; empty: {[c['name'] for c in clips.values() if c['length'] <= 0][:4]}")
    else:
        R.warn('animation.controller', 'no animator controller extracted for this species')
    # 6. face ---------------------------------------------------------------------------------------------------
    face = m['appearance']['face']
    if face:
        no_tex = [e['expression'] for e in face['expressions']
                  if any(x and not any(t.get('file') for t in x['textures'].values()) for x in (e.get('eyes'), e.get('mouth')))]
        R.add('face.expressions_textured', not no_tex, f"{len(face['expressions'])} expressions; without texture: {no_tex[:5]}")
    else:
        R.warn('face', 'species has no face data')
    return R, m
