"""Export -> re-import consistency: skin the exported GLB at rest pose with its own joints/weights/inverse
bind matrices and compare with the source geometry (Unity space restored). Catches handedness, winding,
joint-order and bind-pose export mistakes that a successful export does not reveal."""

import json
from pathlib import Path

import numpy as np

from ..export.gltf import read_back, S
from ..ir.store import unpack_mesh


def _node_world(g):
    n = len(g.nodes)
    parent = [None] * n
    for i, nd in enumerate(g.nodes):
        for c in nd.children or []:
            parent[c] = i
    from ..ir.rig import quat_to_mat3
    world = {}

    def get(i):
        if i in world:
            return world[i]
        nd = g.nodes[i]
        m = np.eye(4)
        m[:3, :3] = quat_to_mat3(nd.rotation or [0, 0, 0, 1]) * np.asarray(nd.scale or [1, 1, 1])[None, :]
        m[:3, 3] = nd.translation or [0, 0, 0]
        world[i] = (get(parent[i]) @ m) if parent[i] is not None else m
        return world[i]
    for i in range(n):
        get(i)
    return world


def verify_glb(pkg_dir, glb_path, tol=1e-3):
    pkg = Path(pkg_dir)
    m = json.loads((pkg / 'species.json').read_text(encoding='utf-8'))
    g, acc = read_back(glb_path)
    world = _node_world(g)
    results = []
    # source meshes by part name (LOD0)
    src = {}
    for part in m['appearance']['parts']:
        lod0 = next((l for l in part['lods'] if l['lod'] == 0), None)
        for r in (lod0['renderers'] if lod0 else []):
            if r.get('mesh'):
                src[f"{part['element']}:{r['node']}"] = r['mesh']['file']
    for ni, nd in enumerate(g.nodes):
        if nd.mesh is None or nd.name not in src:
            continue
        hdr, arrs = unpack_mesh((pkg / src[nd.name]).read_bytes())
        mesh = g.meshes[nd.mesh]
        prim = mesh.primitives[0]
        pos = acc(prim.attributes.POSITION).astype(np.float64)
        pos_u = pos * np.array([1, 1, -1])                       # back to Unity space
        entry = {'node': nd.name, 'vertices': len(pos), 'skinned': nd.skin is not None}
        if len(pos) != len(arrs['positions']):
            entry.update(ok=False, detail='vertex count mismatch')
            results.append(entry)
            continue
        if nd.skin is not None:
            skin = g.skins[nd.skin]
            ibm = acc(skin.inverseBindMatrices).astype(np.float64).reshape(-1, 4, 4).transpose(0, 2, 1)
            joints = acc(prim.attributes.JOINTS_0).astype(int)
            weights = acc(prim.attributes.WEIGHTS_0).astype(np.float64)
            skm = np.stack([world[j] @ ibm[k] for k, j in enumerate(skin.joints)])   # (B,4,4) in glTF space
            hp = np.concatenate([pos, np.ones((len(pos), 1))], axis=1)
            out = np.zeros((len(pos), 3))
            for k in range(4):
                out += weights[:, k:k + 1] * np.einsum('nij,nj->ni', skm[joints[:, k]], hp)[:, :3]
            # the exported rest pose is expressed relative to the Appearance root, which is identity there
            result = out * np.array([1, 1, -1])
        else:
            result = pos_u
        dev = float(np.max(np.linalg.norm(result - arrs['positions'], axis=1)))
        ext = float(np.max(hdr['bounds_extent'])) or 1.0
        entry.update(ok=dev / (2 * ext) <= tol, max_deviation=dev, relative=dev / (2 * ext))
        # triangle winding: exported triangles must be the Unity ones with swapped 2nd/3rd index
        idx_g = acc(prim.indices).reshape(-1, 3)
        idx_u = arrs[hdr['submeshes'][0]['index_array']].reshape(-1, 3)
        entry['winding_ok'] = bool(np.array_equal(idx_g[:, [0, 2, 1]], idx_u))
        entry['ok'] = bool(entry['ok'] and entry['winding_ok'])
        results.append(entry)
    return results
