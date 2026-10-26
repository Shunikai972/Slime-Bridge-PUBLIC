"""Synthetic IR package builder for tests: one skinned quad on a 2-bone skeleton, one clip, one material.
No game data involved."""

import hashlib
import json
from pathlib import Path

import numpy as np

from sbconv.extract.mesh import MeshData
from sbconv.ir.store import pack_mesh, sha16


def make_package(root, bind_offset=0.0):
    root = Path(root)
    (root / 'meshes').mkdir(parents=True, exist_ok=True)
    (root / 'textures').mkdir(exist_ok=True)
    (root / 'anim' / 'clips').mkdir(parents=True, exist_ok=True)
    pos = np.array([[-0.5, 0, 0], [0.5, 0, 0], [0.5, 1, 0], [-0.5, 1, 0]], dtype=np.float32)
    # bone 0 = root at origin, bone 1 = child raised by 0.5; weights blend by height
    w1 = np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32)
    md = MeshData(name='quad', positions=pos, normals=np.tile([0, 0, -1], (4, 1)).astype(np.float32),
                  uvs={0: np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32), 1: np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)},
                  bone_indices=np.array([[0, 1, 0, 0]] * 4, dtype=np.uint16),
                  bone_weights=np.stack([1 - w1, w1, np.zeros(4), np.zeros(4)], axis=1).astype(np.float32))
    md.submeshes = [{'topology': 0, 'indices': np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32), 'first_vertex': 0, 'vertex_count': 4}]
    bp = np.stack([np.eye(4), np.eye(4)]).astype(np.float32)
    bp[1][1, 3] = -0.5 + bind_offset            # inverse of a bone at y=+0.5
    md.bind_poses = bp
    md.bounds_center, md.bounds_extent = (0, 0.5, 0), (0.5, 0.5, 0.01)
    raw = pack_mesh(md)
    mrel = f'meshes/{sha16(raw)}.sbmesh'
    (root / mrel).write_bytes(raw)
    clip = {'name': 'Idle', 'legacy': False, 'sample_rate': 30.0, 'wrap_mode': 0, 'start': 0.0, 'stop': 1.0, 'loop': True, 'loop_blend': False,
            'cycle_offset': 0.0, 'has_root_motion_flags': {}, 'positions': [{'path': 'bone_root/bone_top', 'keys': [
                {'t': 0.0, 'tm': 0, 'wm': 0, 'v': [0, 0.5, 0], 'i': [0, 0, 0], 'o': [0, 0, 0]},
                {'t': 1.0, 'tm': 0, 'wm': 0, 'v': [0, 0.8, 0], 'i': [0, 0, 0], 'o': [0, 0, 0]}]}],
            'rotations': [], 'eulers': [], 'scales': [], 'floats': [], 'pptr': [], 'events': [],
            'curve_counts': {'positions': 1, 'rotations': 0, 'eulers': 0, 'scales': 0, 'floats': 0, 'pptr': 0, 'events': 0}, 'length': 1.0,
            'missing_paths': []}
    cb = (json.dumps(clip, sort_keys=True) + '\n').encode()
    crel = f'anim/clips/{hashlib.sha256(cb).hexdigest()[:16]}.json'
    (root / crel).write_bytes(cb)
    ident = {'position': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
    nodes = [
        {'name': 'prefab', 'path': 'prefab', 'parent': None, 'ref': 'f#1', 'active': True, 'layer': 0, 'tag': 0, 'transform': ident, 'components': [], 'children': [1]},
        {'name': 'Appearance', 'path': 'prefab/Appearance', 'parent': 0, 'ref': 'f#2', 'active': True, 'layer': 0, 'tag': 0,
         'transform': {'position': [3, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}, 'components': [], 'children': [2]},
        {'name': 'bone_root', 'path': 'prefab/Appearance/bone_root', 'parent': 1, 'ref': 'f#3', 'active': True, 'layer': 0, 'tag': 0, 'transform': ident, 'components': [], 'children': [3]},
        {'name': 'bone_top', 'path': 'prefab/Appearance/bone_root/bone_top', 'parent': 2, 'ref': 'f#4', 'active': True, 'layer': 0, 'tag': 0,
         'transform': {'position': [0, 0.5, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}, 'components': [], 'children': []},
    ]
    mat = {'ref': 'f#10', 'name': 'body', 'shader': 'SR/AMP/Slime/Body', 'shader_ref': None, 'keywords': [], 'render_queue': -1,
           'lightmap_flags': 0, 'floats': {'_Gloss': 0.3}, 'colors': {'_Color': [1, 1, 1, 1], '_MiddleColor': [0.5, 0.4, 0.3, 1]}, 'textures': {}, 'tags': {}, 'disabled_passes': []}
    manifest = {
        'schema': 'sbconv-ir', 'schema_version': 1, 'tool_version': 'test',
        'source': {'game': 'SR2', 'unity_version': '2021.3.28f1', 'backend': 'il2cpp', 'pipeline': 'HDRP', 'color_space': 'Linear',
                   'game_version': 'x', 'fingerprint': {'a.exe': 'f' * 64}, 'recognition': 'VALIDATED_BUILD', 'engine': 'test'},
        'species': {'name': 'Quad', 'kind': 'base', 'canonical_id': 'slime.quad', 'base_slimes': [], 'definition': 'f#0'},
        'prefab': {'ref': 'f#1', 'name': 'prefab', 'nodes': nodes, 'root': 0, 'appearance_root': 1, 'skeleton': [2, 3], 'bone_table': {'1': 2, '2': 3}, 'animators': []},
        'appearance': {'ref': 'f#20', 'face': None, 'palette': {}, 'extras': {}, 'parts': [{
            'index': 0, 'element': 'Body', 'name': 'Body', 'type': 3, 'material_inheritance': 0, 'casts_shadows': False,
            'supports_faces': False, 'face_rules': [], 'default_materials': [mat],
            'lods': [{'lod': 0, 'object': 'f#30', 'parent_bone': None, 'root_bone': 2, 'attached_bones': [2, 3], 'attached_bone_enums': [1, 2],
                      'override_materials': None, 'rubber_type': 0, 'attach_rubber_bone_effect': False,
                      'renderers': [{'node': 'quad', 'type': 'SkinnedMeshRenderer', 'transform': ident, 'cast_shadows': 1, 'receive_shadows': 1,
                                     'placeholder_materials': [],
                                     'mesh': {'file': mrel, 'name': 'quad', 'vertex_count': 4, 'submeshes': 1, 'triangles': 2, 'has_normals': True,
                                              'has_tangents': False, 'uv_channels': [0, 1], 'has_colors': False, 'skinned': True, 'bind_pose_count': 2,
                                              'content_sha256': md.content_hash(), 'source': 'f#40', 'problems': []}}]}]}]},
        'animation': {'controller': {'name': 'Ctrl', 'parameters': [{'name': 'go', 'type': 4, 'default_float': 0, 'default_int': 0, 'default_bool': False}],
                                     'layers': [{'name': 'Base', 'default_state': 'idle', 'states': [
                                         {'name': 'idle', 'speed': 1.0, 'motion': {'kind': 'clip', 'asset': 'Idle'}, 'transitions': [
                                             {'dst': 'idle', 'conditions': [{'mode': 1, 'param': 'go', 'threshold': 0}]}]}]}]},
                      'clips': {'Idle': {'name': 'Idle', 'file': crel, 'length': 1.0, 'loop': True, 'counts': clip['curve_counts'], 'events': 0, 'missing_paths': 0}},
                      'overrides': {}, 'avatar': None, 'animator_node': 1},
        'gameplay': {'behaviours': [], 'colliders': [], 'rigidbodies': []},
        'materials': {mat['ref']: mat}, 'textures': {}, 'meshes': {'f#40': {'file': mrel, 'name': 'quad', 'problems': []}},
        'provenance': {'sources': {'data.assets': 'a' * 64}}, 'problems': [], 'status': 'EXTRACTED',
    }
    files = {}
    for p in (root / 'meshes').glob('*'):
        files[f'meshes/{p.name}'] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
    files[crel] = {'sha256': hashlib.sha256(cb).hexdigest(), 'bytes': len(cb)}
    manifest['files'] = files
    (root / 'species.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return manifest
