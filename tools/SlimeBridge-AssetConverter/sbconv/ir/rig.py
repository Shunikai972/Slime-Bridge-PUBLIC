"""Rig maths on the IR (Unity space: left-handed, column vectors, TRS -> 4x4)."""

import numpy as np


def quat_to_mat3(q):
    x, y, z, w = q
    n = x * x + y * y + z * z + w * w
    if n == 0:
        return np.eye(3)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array([[1 - (yy + zz), xy - wz, xz + wy],
                     [xy + wz, 1 - (xx + zz), yz - wx],
                     [xz - wy, yz + wx, 1 - (xx + yy)]])


def trs_matrix(t):
    m = np.eye(4)
    m[:3, :3] = quat_to_mat3(t['rotation']) * np.asarray(t['scale'])[None, :]
    m[:3, 3] = t['position']
    return m


def world_matrices(nodes, root=None):
    """local TRS chain -> world 4x4 per node index (nodes carry 'parent' and 'transform')."""
    world = {}

    def get(i):
        if i in world:
            return world[i]
        n = nodes[i]
        local = trs_matrix(n['transform']) if n.get('transform') else np.eye(4)
        p = n['parent']
        world[i] = (get(p) @ local) if (p is not None and p >= 0) else local
        return world[i]
    for i in range(len(nodes)):
        get(i)
    return world


def skin_vertices(positions, bone_indices, bone_weights, bone_world, bind_poses, mesh_world):
    """Rest-pose linear-blend skinning, result in world space. bone_world: (B,4,4) world matrix of the
    bone for each *mesh* bone slot; bind_poses: (B,4,4); mesh_world: (4,4) localToWorld of the renderer."""
    skin = np.einsum('bij,bjk->bik', bone_world, bind_poses)        # (B,4,4)
    n = positions.shape[0]
    hp = np.concatenate([positions, np.ones((n, 1))], axis=1)       # (N,4)
    out = np.zeros((n, 3))
    for k in range(bone_indices.shape[1]):
        w = bone_weights[:, k]
        if not w.any():
            continue
        m = skin[bone_indices[:, k]]                                # (N,4,4)
        out += w[:, None] * np.einsum('nij,nj->ni', m, hp)[:, :3]
    return out
