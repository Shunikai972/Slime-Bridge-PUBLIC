"""Mesh data model: named parts with positions, normals, uvs, triangles and a semantic material.

A ``MeshPart`` is an indexed triangle list. Triangles are counter-clockwise when seen from the
side their normals point to (the glTF front-face convention). Every helper here is pure and
deterministic; ``validate_mesh`` never raises and returns unified diagnostics.
"""

import math
from dataclasses import dataclass, field

from bridge.core import diagnostics as D

from . import vec as V

SUBSYSTEM = 'meshgen'
ROUND_DIGITS = 6
_MAX_EXAMPLES = 5


@dataclass
class MeshPart:
    name: str
    material: str
    positions: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uvs: list = field(default_factory=list)
    indices: list = field(default_factory=list)

    @property
    def vertex_count(self):
        return len(self.positions)

    @property
    def triangle_count(self):
        return len(self.indices) // 3

    def add_vertex(self, position, normal, uv):
        self.positions.append(tuple(position))
        self.normals.append(tuple(normal))
        self.uvs.append(tuple(uv))
        return len(self.positions) - 1

    def add_triangle(self, a, b, c):
        self.indices.extend((a, b, c))

    def add_quad(self, a, b, c, d):
        """Quad a-b-c-d in counter-clockwise order, split along a-c."""
        self.indices.extend((a, b, c, a, c, d))

    def extend(self, other):
        """Append another part's geometry (materials are the caller's responsibility)."""
        offset = len(self.positions)
        self.positions.extend(other.positions)
        self.normals.extend(other.normals)
        self.uvs.extend(other.uvs)
        self.indices.extend(i + offset for i in other.indices)
        return self

    def copy(self, name=None, material=None):
        return MeshPart(name or self.name, material or self.material, list(self.positions),
                        list(self.normals), list(self.uvs), list(self.indices))


def merge(parts, name=None, material=None):
    """One part from several. All parts must share a material unless ``material`` is given."""
    parts = list(parts)
    if not parts:
        raise ValueError('nothing to merge')
    materials = {p.material for p in parts}
    if material is None and len(materials) != 1:
        raise ValueError(f'cannot merge parts with different materials: {sorted(materials)}')
    out = MeshPart(name or parts[0].name, material or parts[0].material)
    for part in parts:
        out.extend(part)
    return out


def merge_by_material(parts, prefix):
    """Group parts by material, merging each group into ``<prefix>.<material>`` (stable order)."""
    groups = {}
    for part in parts:
        groups.setdefault(part.material, []).append(part)
    return [merge(groups[m], name=f'{prefix}.{m}', material=m) for m in sorted(groups)]


def transform(part, matrix):
    """New part with an affine 3x4 transform applied (normals via the inverse transpose)."""
    nm = V.normal_matrix(matrix)
    out = MeshPart(part.name, part.material,
                   [V.apply_point(matrix, p) for p in part.positions],
                   [V.normalize(V.apply_vector(nm, n)) for n in part.normals],
                   list(part.uvs), list(part.indices))
    if V.determinant(matrix) < 0.0:  # mirrored: keep counter-clockwise front faces
        idx = out.indices
        out.indices = [v for t in range(0, len(idx), 3) for v in (idx[t], idx[t + 2], idx[t + 1])]
    return out


def triangle_normal(a, b, c):
    """Unnormalized geometric normal (length = 2 x area)."""
    return V.cross(V.sub(b, a), V.sub(c, a))


def compute_smooth_normals(part, by_position=False):
    """Area-weighted vertex normals. ``by_position`` also smooths across duplicated seam vertices."""
    key = (lambda i: V.rounded(part.positions[i], ROUND_DIGITS)) if by_position else (lambda i: i)
    acc = {}
    idx = part.indices
    for t in range(0, len(idx), 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        n = triangle_normal(part.positions[a], part.positions[b], part.positions[c])
        for v in (a, b, c):
            k = key(v)
            acc[k] = V.add(acc.get(k, (0.0, 0.0, 0.0)), n)
    out = part.copy()
    out.normals = [V.normalize(acc.get(key(i), (0.0, 0.0, 0.0)), fallback=part.normals[i] if part.normals
                               else V.Y) for i in range(part.vertex_count)]
    return out


def compute_flat_normals(part):
    """Unwelded copy where every triangle has its own three vertices and its face normal."""
    out = MeshPart(part.name, part.material)
    idx = part.indices
    for t in range(0, len(idx), 3):
        tri = idx[t:t + 3]
        n = V.normalize(triangle_normal(*(part.positions[i] for i in tri)))
        base = out.vertex_count
        for i in tri:
            out.add_vertex(part.positions[i], n, part.uvs[i] if part.uvs else (0.0, 0.0))
        out.add_triangle(base, base + 1, base + 2)
    return out


def bounds(parts):
    """(min, max) corners over every vertex of the given parts, or None when empty."""
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for part in parts:
        for p in part.positions:
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    if lo[0] == math.inf:
        return None
    return tuple(lo), tuple(hi)


def triangle_count(parts):
    return sum(p.triangle_count for p in parts)


def vertex_count(parts):
    return sum(p.vertex_count for p in parts)


def rounded_part(part, digits=ROUND_DIGITS):
    """Deterministic copy: normals renormalized, every float rounded to ``digits`` decimals."""
    return MeshPart(part.name, part.material,
                    [V.rounded(p, digits) for p in part.positions],
                    [V.rounded(V.normalize(n), digits) for n in part.normals],
                    [V.rounded(uv, digits) for uv in part.uvs],
                    list(part.indices))


def _diag(code, severity, message, subject, **details):
    return D.make(code, severity, SUBSYSTEM, message, subject=subject, details=details)


def validate_mesh(parts, *, subject='mesh', max_extent=100.0, degenerate_area=1e-9,
                  normal_tolerance=1e-3, max_degenerate_fraction=0.0, check_winding=True,
                  max_inverted_fraction=0.02):
    """Unified diagnostics for one part or a list of parts. Never raises.

    Checks: non-empty, matching attribute counts, finite values, indices in range and a multiple of
    three, triangle area above ``degenerate_area`` (m^2), unit normals, bounds within
    ``max_extent`` metres of the origin, and (optionally) that geometric face normals agree with
    the vertex normals (counter-clockwise winding).
    """
    if isinstance(parts, MeshPart):
        parts = [parts]
    out = []
    try:
        parts = list(parts)
    except TypeError:
        return [_diag('MESH_INVALID', 'error', 'mesh parts must be a list of MeshPart', subject)]
    if not parts or all(getattr(p, 'triangle_count', 0) == 0 for p in parts):
        return [_diag('MESH_EMPTY', 'error', 'mesh has no triangles', subject)]
    for part in parts:
        if not isinstance(part, MeshPart):
            out.append(_diag('MESH_INVALID', 'error', 'not a MeshPart', subject))
            continue
        out.extend(_validate_part(part, f'{subject}/{part.name}', max_extent, degenerate_area,
                                  normal_tolerance, max_degenerate_fraction, check_winding,
                                  max_inverted_fraction))
    return out


def _finite_tuple(value, size):
    return (isinstance(value, tuple) and len(value) == size and
            all(isinstance(c, (int, float)) and math.isfinite(c) for c in value))


def _validate_part(part, subject, max_extent, degenerate_area, normal_tolerance,
                   max_degenerate_fraction, check_winding, max_inverted_fraction):
    out = []
    n = part.vertex_count
    if part.triangle_count == 0 or n == 0:
        return [_diag('MESH_EMPTY', 'error', 'part has no triangles', subject)]
    if len(part.normals) != n or len(part.uvs) != n:
        out.append(_diag('MESH_ATTRIBUTE_MISMATCH', 'error', 'normal/uv count differs from vertex count',
                         subject, vertices=n, normals=len(part.normals), uvs=len(part.uvs)))
        return out
    bad = [i for i in range(n) if not (_finite_tuple(part.positions[i], 3) and
                                       _finite_tuple(part.normals[i], 3) and
                                       _finite_tuple(part.uvs[i], 2))]
    if bad:
        out.append(_diag('MESH_NON_FINITE', 'error', 'non-finite or malformed vertex attributes', subject,
                         count=len(bad), examples=bad[:_MAX_EXAMPLES]))
        return out
    if len(part.indices) % 3:
        out.append(_diag('MESH_INDEX_COUNT', 'error', 'index count is not a multiple of 3', subject,
                         indices=len(part.indices)))
        return out
    wrong = [k for k, i in enumerate(part.indices) if type(i) is not int or i < 0 or i >= n]
    if wrong:
        out.append(_diag('MESH_INDEX_OUT_OF_RANGE', 'error', 'triangle index outside the vertex range', subject,
                         count=len(wrong), examples=wrong[:_MAX_EXAMPLES], vertices=n))
        return out
    off_unit = [i for i, nn in enumerate(part.normals) if abs(V.length(nn) - 1.0) > normal_tolerance]
    if off_unit:
        out.append(_diag('MESH_NORMAL_NOT_UNIT', 'error', 'vertex normals are not unit length', subject,
                         count=len(off_unit), examples=off_unit[:_MAX_EXAMPLES]))
    degenerate, inverted = [], []
    idx, pos, nor = part.indices, part.positions, part.normals
    for t in range(0, len(idx), 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        g = triangle_normal(pos[a], pos[b], pos[c])
        area2 = V.length(g)
        if area2 * 0.5 <= degenerate_area:
            degenerate.append(t // 3)
            continue
        if check_winding:
            avg = V.add(V.add(nor[a], nor[b]), nor[c])
            if V.dot(g, avg) < 0.0:
                inverted.append(t // 3)
    tris = part.triangle_count
    if len(degenerate) > max_degenerate_fraction * tris:
        out.append(_diag('MESH_DEGENERATE_TRIANGLES', 'error', 'triangles with (near) zero area', subject,
                         count=len(degenerate), triangles=tris, examples=degenerate[:_MAX_EXAMPLES],
                         area_tolerance=degenerate_area))
    if check_winding and len(inverted) > max_inverted_fraction * tris:
        out.append(_diag('MESH_WINDING_INCONSISTENT', 'error',
                         'face winding disagrees with vertex normals', subject,
                         count=len(inverted), triangles=tris, examples=inverted[:_MAX_EXAMPLES]))
    box = bounds([part])
    if box is not None:
        extent = max(max(abs(c) for c in box[0]), max(abs(c) for c in box[1]))
        if extent > max_extent:
            out.append(_diag('MESH_BOUNDS_UNREASONABLE', 'error', 'mesh extends too far from the origin', subject,
                             extent=round(extent, 3), max_extent=max_extent))
    return out
