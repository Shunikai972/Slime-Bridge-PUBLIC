"""Mesh decoding (via UnityPy's MeshHandler) into numpy arrays kept in raw *Unity space*
(left-handed, +Y up, UV origin bottom-left). Coordinate/handedness conversion happens only in the
exporters (see ``sbconv.ir.coords``), never here, so the IR is lossless."""

import hashlib
from dataclasses import dataclass, field

import numpy as np


class MeshDecodeError(ValueError):
    pass


@dataclass
class MeshData:
    name: str
    positions: np.ndarray                    # (N,3) float32
    normals: np.ndarray | None = None        # (N,3)
    tangents: np.ndarray | None = None       # (N,4)
    uvs: dict = field(default_factory=dict)  # channel -> (N,2)
    colors: np.ndarray | None = None         # (N,4) float32
    bone_indices: np.ndarray | None = None   # (N,4) uint16
    bone_weights: np.ndarray | None = None   # (N,4) float32
    submeshes: list = field(default_factory=list)  # [{'topology': int, 'indices': (M,) uint32, 'first_vertex', 'vertex_count'}]
    bind_poses: np.ndarray | None = None     # (B,4,4) float32 (Unity column-major semantics, stored row-major as given)
    bone_name_hashes: list = field(default_factory=list)
    root_bone_name_hash: int = 0
    bounds_center: tuple = (0.0, 0.0, 0.0)
    bounds_extent: tuple = (0.0, 0.0, 0.0)
    blend_shapes: list = field(default_factory=list)  # [{'name', 'frames': [{'weight','delta_positions','delta_normals','delta_tangents'}]}]
    source: dict = field(default_factory=dict)

    @property
    def vertex_count(self):
        return int(self.positions.shape[0])

    def content_hash(self):
        h = hashlib.sha256()
        for arr in (self.positions, self.normals, self.tangents, self.colors, self.bone_indices, self.bone_weights):
            if arr is not None:
                h.update(np.ascontiguousarray(arr).tobytes())
        for ch in sorted(self.uvs):
            h.update(np.ascontiguousarray(self.uvs[ch]).tobytes())
        for sm in self.submeshes:
            h.update(np.ascontiguousarray(sm['indices']).tobytes())
        return h.hexdigest()


def _arr(v, width, dtype=np.float32):
    if v is None:
        return None
    a = np.asarray(v, dtype=dtype)
    if a.ndim == 2 and width == 3 and a.shape[1] == 4:
        a = a[:, :3]  # some vertex formats pad positions/normals with an unused w component
    if a.ndim != 2 or a.shape[1] != width:
        raise MeshDecodeError(f'expected (N,{width}) got {a.shape}')
    return np.ascontiguousarray(a)


def decode_mesh(reader):
    """ObjectReader(Mesh) -> MeshData. Raises MeshDecodeError with the reason on any inconsistency."""
    from UnityPy.helpers.MeshHelper import MeshHandler
    m = reader.read()
    try:
        mh = MeshHandler(m)
        mh.process()
    except Exception as e:
        raise MeshDecodeError(f'{m.m_Name}: UnityPy mesh processing failed: {type(e).__name__}: {e}')
    if not mh.m_VertexCount or mh.m_Vertices is None:
        raise MeshDecodeError(f'{m.m_Name}: no vertices (mesh not readable / stripped)')
    pos = _arr(mh.m_Vertices, 3)
    n = pos.shape[0]
    md = MeshData(name=m.m_Name, positions=pos)
    md.normals = _arr(mh.m_Normals, 3) if mh.m_Normals is not None else None
    md.tangents = _arr(mh.m_Tangents, 4) if mh.m_Tangents is not None else None
    for ch in range(8):
        uv = getattr(mh, f'm_UV{ch}', None)
        if uv is not None and len(uv):
            a = np.asarray(uv, dtype=np.float32)
            if a.ndim == 2 and a.shape[0] == n:
                md.uvs[ch] = a[:, :2] if a.shape[1] >= 2 else a
    if mh.m_Colors is not None and len(mh.m_Colors) == n:
        md.colors = np.asarray(mh.m_Colors, dtype=np.float32)
    if mh.m_BoneIndices is not None and len(mh.m_BoneIndices) == n:
        md.bone_indices = np.asarray(mh.m_BoneIndices, dtype=np.uint16)
        md.bone_weights = np.asarray(mh.m_BoneWeights, dtype=np.float32)
    try:
        tris = mh.get_triangles()
    except Exception as e:
        raise MeshDecodeError(f'{m.m_Name}: cannot read indices: {e}')
    for sm, tri in zip(m.m_SubMeshes, tris):
        idx = np.asarray([i for t in tri for i in t], dtype=np.uint32)
        md.submeshes.append({'topology': int(sm.topology), 'indices': idx,
                             'first_vertex': int(sm.firstVertex), 'vertex_count': int(sm.vertexCount)})
    bp = getattr(m, 'm_BindPose', None)
    if bp:
        md.bind_poses = np.asarray([[[getattr(mat, f'e{r}{c}') for c in range(4)] for r in range(4)] for mat in bp], dtype=np.float32)
    md.bone_name_hashes = list(getattr(m, 'm_BoneNameHashes', None) or [])
    md.root_bone_name_hash = int(getattr(m, 'm_RootBoneNameHash', 0) or 0)
    aabb = getattr(m, 'm_LocalAABB', None)
    if aabb is not None:
        md.bounds_center = (aabb.m_Center.x, aabb.m_Center.y, aabb.m_Center.z)
        md.bounds_extent = (aabb.m_Extent.x, aabb.m_Extent.y, aabb.m_Extent.z)
    md.source = {'path_id': reader.path_id, 'file': getattr(reader.assets_file, 'name', '')}
    return md


def validate_mesh(md):
    """Geometric sanity checks; returns a list of problems (empty = clean)."""
    problems = []
    n = md.vertex_count
    if not np.isfinite(md.positions).all():
        problems.append('non-finite positions')
    for label, arr in (('normals', md.normals), ('tangents', md.tangents)):
        if arr is not None and not np.isfinite(arr).all():
            problems.append(f'non-finite {label}')
    if md.normals is not None:
        ln = np.linalg.norm(md.normals, axis=1)
        if (ln < 0.5).mean() > 0.01:
            problems.append(f'{(ln < 0.5).mean():.1%} degenerate normals')
    for ch, uv in md.uvs.items():
        if not np.isfinite(uv).all():
            problems.append(f'non-finite uv{ch}')
    tris = 0
    for i, sm in enumerate(md.submeshes):
        idx = sm['indices']
        if len(idx) == 0:
            problems.append(f'submesh {i} empty')
            continue
        if idx.max() >= n:
            problems.append(f'submesh {i} index {int(idx.max())} out of range ({n})')
        if sm['topology'] == 0:
            tris += len(idx) // 3
            if len(idx) % 3:
                problems.append(f'submesh {i} index count not multiple of 3')
    if not md.submeshes:
        problems.append('no submeshes')
    if md.bone_weights is not None:
        s = md.bone_weights.sum(axis=1)
        if (np.abs(s - 1.0) > 0.02).mean() > 0.01:
            problems.append(f'{(np.abs(s - 1.0) > 0.02).mean():.1%} vertices with weights not summing to 1')
        if md.bind_poses is not None and md.bone_indices.max() >= len(md.bind_poses):
            problems.append('bone index exceeds bind pose count')
    return problems
