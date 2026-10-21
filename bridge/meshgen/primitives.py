"""Deterministic parametric primitives built from scratch (positions, normals, uvs, CCW triangles).

All lengths are metres. UVs are world-scale (1 UV unit = 1 m) unless noted, so tiling
textures added later keep a constant texel density. Every primitive returns a ``MeshPart``.

Building blocks:
  * ``rounded_rect_profile`` + ``sweep``: a rounded-rectangle cross-section swept along stations
    (rings, bevelled tubes, the walkable deck slab, curbs, light strips);
  * ``ring_arc``: a torus whose tube cross-section is a rounded rectangle (full or partial arc);
  * ``rounded_box``: a bevelled box with true quarter-round edges and spherical corners;
  * ``lathe``: surfaces of revolution (capsule, rounded cylinder, sphere) with pole fans;
  * ``clipped_disc``: a flat disc cut by a horizontal floor line (portal energy surface);
  * ``post`` / ``rail`` / ``pedestal``: small composites used by the gateway.
"""

import math

from . import vec as V
from .mesh import MeshPart, merge, transform

_POLE = 1e-9


# --- profiles and sweeps ------------------------------------------------------------------------

def rounded_rect_profile(half_w, half_h, radius, corner_segments, center=(0.0, 0.0)):
    """Closed counter-clockwise loop of ``(x, y, nx, ny, s)``; the first point is repeated last.

    ``s`` is the normalized perimeter parameter (texture U). The corner radius is clamped below
    the half sizes so flat sides never collapse to zero length (no duplicate points).
    """
    cs = max(1, int(corner_segments))
    r = max(1e-4, min(radius, 0.9 * half_w, 0.9 * half_h))
    cx, cy = center
    corners = ((half_w - r, half_h - r, 0.0), (-half_w + r, half_h - r, 0.5 * math.pi),
               (-half_w + r, -half_h + r, math.pi), (half_w - r, -half_h + r, 1.5 * math.pi))
    pts = []
    for ox, oy, a0 in corners:
        for k in range(cs + 1):
            a = a0 + 0.5 * math.pi * k / cs
            c, s = math.cos(a), math.sin(a)
            pts.append((cx + ox + r * c, cy + oy + r * s, c, s))
    pts.append(pts[0])
    total, acc = 0.0, [0.0]
    for i in range(1, len(pts)):
        total += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        acc.append(total)
    return [(x, y, nx, ny, acc[i] / total) for i, (x, y, nx, ny) in enumerate(pts)]


def circle_profile(radius, segments):
    n = max(3, int(segments))
    pts = []
    for k in range(n + 1):
        a = 2.0 * math.pi * k / n
        pts.append((radius * math.cos(a), radius * math.sin(a), math.cos(a), math.sin(a), k / n))
    return pts


def sweep(name, material, stations, profiles, closed=False, caps=(True, True), v_scale=1.0):
    """Sweep profile loops along stations.

    ``stations``: list of ``(origin, tangent, axis_a)`` or ``(origin, tangent, axis_a, b_scale)``.
    The profile's x maps to ``axis_a`` and y to ``axis_b = tangent x axis_a`` (scaled by
    ``b_scale`` for mitred joints). ``profiles`` is one loop for all stations or one per station
    (same point count). A closed sweep joins the last station back to the first (no caps).
    """
    part = MeshPart(name, material)
    stations = [tuple(s) + ((1.0,) if len(s) == 3 else ()) for s in stations]
    if isinstance(profiles[0][0], (int, float)):
        profiles = [profiles] * len(stations)
    if closed:
        stations = stations + [stations[0]]
        profiles = list(profiles) + [profiles[0]]
    count = len(profiles[0])
    if any(len(p) != count for p in profiles):
        raise ValueError('all sweep profiles need the same point count')
    frames, v = [], 0.0
    for i, (origin, tangent, axis_a, b_scale) in enumerate(stations):
        t = V.normalize(tangent)
        a = V.normalize(V.madd(axis_a, t, -V.dot(axis_a, t)))
        b = V.cross(t, a)
        if i:
            v += V.length(V.sub(origin, stations[i - 1][0]))
        frames.append((origin, t, a, b, b_scale, v / v_scale))
    rows = []
    for (origin, _t, a, b, b_scale, vv), profile in zip(frames, profiles):
        row = []
        for x, y, nx, ny, s in profile:
            p = V.madd(V.madd(origin, a, x), b, y * b_scale)
            n = V.normalize(V.add(V.scale(a, nx), V.scale(b, ny / b_scale)))
            row.append(part.add_vertex(p, n, (s, vv)))
        rows.append(row)
    for i in range(len(rows) - 1):
        r0, r1 = rows[i], rows[i + 1]
        for j in range(count - 1):
            part.add_quad(r0[j], r0[j + 1], r1[j + 1], r1[j])
    if not closed:
        if caps[0]:
            _cap(part, frames[0], profiles[0], end=False)
        if caps[1]:
            _cap(part, frames[-1], profiles[-1], end=True)
    return part


def _cap(part, frame, profile, end):
    origin, t, a, b, b_scale, _v = frame
    loop = profile[:-1]
    cx = sum(p[0] for p in loop) / len(loop)
    cy = sum(p[1] for p in loop) / len(loop)
    n = t if end else V.scale(t, -1.0)
    centre = part.add_vertex(V.madd(V.madd(origin, a, cx), b, cy * b_scale), n, (cx, cy))
    ids = [part.add_vertex(V.madd(V.madd(origin, a, x), b, y * b_scale), n, (x, y)) for x, y, *_ in loop]
    m = len(ids)
    for j in range(m):
        if end:
            part.add_triangle(centre, ids[j], ids[(j + 1) % m])
        else:
            part.add_triangle(centre, ids[(j + 1) % m], ids[j])


def ring_arc(name, material, inner_radius, width, depth, bevel, segments, corner_segments,
             start=0.0, end=2.0 * math.pi, center=(0.0, 0.0, 0.0), caps=True):
    """Ring in the XY plane (axis +Z) with a rounded-rectangle tube section.

    ``width`` is radial, ``depth`` is along Z. ``segments`` counts a full turn; a partial arc
    from ``start`` to ``end`` (radians, counter-clockwise from +X) gets proportionally fewer and
    flat end caps.
    """
    span = end - start
    full = abs(abs(span) - 2.0 * math.pi) < 1e-9
    n = max(3, int(math.ceil(segments * abs(span) / (2.0 * math.pi))))
    rm = inner_radius + 0.5 * width
    profile = rounded_rect_profile(0.5 * width, 0.5 * depth, bevel, corner_segments)
    stations = []
    for k in range(n if full else n + 1):
        phi = start + span * k / n
        c, s = math.cos(phi), math.sin(phi)
        er = (c, s, 0.0)
        tangent = (-s, c, 0.0) if span > 0 else (s, -c, 0.0)
        stations.append((V.madd(center, er, rm), tangent, er))
    return sweep(name, material, stations, profile, closed=full, caps=(caps, caps))


def swept_slab(name, material, path, width, bevel, corner_segments, x_offset=0.0):
    """Slab with rounded edges swept along ``path`` = [(z, y_top, thickness), ...] in the YZ plane.

    The slab's top surface follows ``y_top``; thickness may vary per station (tapered ramp toes).
    Joints are mitred so thickness stays constant through bends.
    """
    pts = [(0.0, y, z) for z, y, _t in path]
    dirs = [V.normalize(V.sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1)]
    stations, profiles = [], []
    for i, (z, y, thick) in enumerate(path):
        if i == 0:
            t, miter = dirs[0], 1.0
        elif i == len(path) - 1:
            t, miter = dirs[-1], 1.0
        else:
            t = V.normalize(V.add(dirs[i - 1], dirs[i]))
            miter = 1.0 / max(0.5, V.dot(t, dirs[i]))
        stations.append(((x_offset, y, z), t, V.X, miter))
        profiles.append(rounded_rect_profile(0.5 * width, 0.5 * thick, bevel, corner_segments,
                                             center=(0.0, -0.5 * thick)))
    return sweep(name, material, stations, profiles)


# --- bevelled box -------------------------------------------------------------------------------

def _box_axis_samples(h, r, bevel_segments):
    inner = h - r
    bevel = [r * math.tan(0.25 * math.pi * k / bevel_segments) for k in range(1, bevel_segments + 1)]
    return [-(inner + d) for d in reversed(bevel)] + [-inner, inner] + [inner + d for d in bevel]


# (normal axis, sign, u axis, v axis) with u x v == outward normal
_BOX_FACES = ((0, 1.0, 1, 2), (0, -1.0, 2, 1), (1, 1.0, 2, 0), (1, -1.0, 0, 2), (2, 1.0, 0, 1),
              (2, -1.0, 1, 0))


def rounded_box(name, material, half_extents, radius, bevel_segments=2, matrix=None):
    """Box with quarter-round edges of ``radius`` and spherical corners, centred at the origin
    (then transformed by ``matrix``)."""
    h = tuple(float(c) for c in half_extents)
    bs = max(1, int(bevel_segments))
    r = max(1e-4, min(radius, 0.95 * min(h)))
    inner = tuple(c - r for c in h)
    samples = [_box_axis_samples(h[k], r, bs) for k in range(3)]
    part = MeshPart(name, material)
    for axis, sign, ua, va in _BOX_FACES:
        su, sv = samples[ua], samples[va]
        grid = []
        for u in su:
            row = []
            for v in sv:
                p = [0.0, 0.0, 0.0]
                p[axis], p[ua], p[va] = sign * h[axis], u, v
                core = tuple(max(-inner[k], min(inner[k], p[k])) for k in range(3))
                n = V.normalize(V.sub(tuple(p), core), fallback=tuple(sign if k == axis else 0.0 for k in range(3)))
                pos = V.madd(core, n, r)
                row.append(part.add_vertex(pos, n, (pos[ua] + h[ua], pos[va] + h[va])))
            grid.append(row)
        for i in range(len(su) - 1):
            for j in range(len(sv) - 1):
                part.add_quad(grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1])
    return transform(part, matrix) if matrix is not None else part


# --- surfaces of revolution ---------------------------------------------------------------------

def lathe(name, material, profile, segments, matrix=None):
    """Revolve ``profile`` = [(radius, height, n_radial, n_height), ...] (bottom to top) about +Y.

    Points with zero radius become poles closed by triangle fans (no degenerate triangles).
    Consecutive duplicate points are dropped.
    """
    seg = max(3, int(segments))
    pts = []
    for r, y, nr, ny in profile:
        r = 0.0 if r < _POLE else r
        if pts and abs(pts[-1][0] - r) < 1e-12 and abs(pts[-1][1] - y) < 1e-12:
            continue
        pts.append((r, y, nr, ny))
    total, acc = 0.0, [0.0]
    for i in range(1, len(pts)):
        total += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        acc.append(total)
    total = total or 1.0
    part = MeshPart(name, material)
    rows = []
    for j, (r, y, nr, ny) in enumerate(pts):
        v = acc[j] / total
        if r == 0.0:
            n = V.normalize((0.0, ny, 0.0))
            rows.append([part.add_vertex((0.0, y, 0.0), n, ((i + 0.5) / seg, v)) for i in range(seg)])
            continue
        row = []
        for i in range(seg + 1):
            th = 2.0 * math.pi * i / seg
            s, c = math.sin(th), math.cos(th)
            row.append(part.add_vertex((r * s, y, r * c), V.normalize((nr * s, ny, nr * c)), (i / seg, v)))
        rows.append(row)
    for j in range(len(pts) - 1):
        pole0, pole1 = pts[j][0] == 0.0, pts[j + 1][0] == 0.0
        r0, r1 = rows[j], rows[j + 1]
        for i in range(seg):
            if pole0 and pole1:
                continue
            if pole0:
                part.add_triangle(r0[i], r1[i + 1], r1[i])
            elif pole1:
                part.add_triangle(r0[i], r0[i + 1], r1[i])
            else:
                part.add_quad(r0[i], r0[i + 1], r1[i + 1], r1[i])
    return transform(part, matrix) if matrix is not None else part


def capsule_profile(radius, cylinder_length, cap_segments):
    cs = max(1, int(cap_segments))
    half = 0.5 * cylinder_length
    pts = []
    for k in range(cs + 1):
        a = -0.5 * math.pi + 0.5 * math.pi * k / cs
        pts.append((radius * math.cos(a), -half + radius * math.sin(a), math.cos(a), math.sin(a)))
    for k in range(cs + 1):
        a = 0.5 * math.pi * k / cs
        pts.append((radius * math.cos(a), half + radius * math.sin(a), math.cos(a), math.sin(a)))
    return pts


def rounded_cylinder_profile(radius, height, bevel, bevel_segments):
    bs = max(1, int(bevel_segments))
    b = max(1e-4, min(bevel, 0.95 * radius, 0.475 * height))
    half = 0.5 * height
    pts = [(0.0, -half, 0.0, -1.0)]
    for k in range(bs + 1):
        a = -0.5 * math.pi + 0.5 * math.pi * k / bs
        pts.append((radius - b + b * math.cos(a), -half + b + b * math.sin(a), math.cos(a), math.sin(a)))
    for k in range(bs + 1):
        a = 0.5 * math.pi * k / bs
        pts.append((radius - b + b * math.cos(a), half - b + b * math.sin(a), math.cos(a), math.sin(a)))
    pts.append((0.0, half, 0.0, 1.0))
    return pts


def _segment_matrix(p0, p1):
    d = V.sub(p1, p0)
    ln = V.length(d)
    ax, ay, az = V.frame_from_y(d if ln > 1e-9 else V.Y)
    return V.from_axes(ax, ay, az, V.lerp(p0, p1, 0.5)), ln


def capsule(name, material, p0, p1, radius, segments, cap_segments):
    """Cylinder from ``p0`` to ``p1`` with hemispherical caps (a sphere when p0 == p1)."""
    m, ln = _segment_matrix(p0, p1)
    return lathe(name, material, capsule_profile(radius, ln, cap_segments), segments, m)


def sphere(name, material, center, radius, segments, rings=None):
    return capsule(name, material, center, center, radius, segments, rings or max(2, segments // 4))


def rounded_cylinder(name, material, p0, p1, radius, bevel, segments, bevel_segments):
    """Cylinder from ``p0`` to ``p1`` with flat ends joined by a rounded bevel."""
    m, ln = _segment_matrix(p0, p1)
    return lathe(name, material, rounded_cylinder_profile(radius, ln, bevel, bevel_segments), segments, m)


# --- flat ---------------------------------------------------------------------------------------

def clipped_disc(name, material, center, radius, segments, y_min, chord_segments=6):
    """Disc in the XY plane facing +Z, cut below ``y_min``. Convex, fan-triangulated.

    UVs are planar over the disc's bounding square (0..1), so a shader can derive radius/angle.
    """
    cx, cy, cz = center
    part = MeshPart(name, material)
    rel = (y_min - cy) / radius
    if rel <= -1.0:
        a0, a1, chord = -0.5 * math.pi, 1.5 * math.pi, False
    else:
        a0 = math.asin(max(-1.0, min(0.999, rel)))
        a1, chord = math.pi - a0, True
    n = max(3, int(math.ceil(segments * (a1 - a0) / (2.0 * math.pi))))
    loop = [(cx + radius * math.cos(a0 + (a1 - a0) * k / n), cy + radius * math.sin(a0 + (a1 - a0) * k / n))
            for k in range(n + (1 if chord else 0))]
    if chord:
        (xl, yl), (xr, yr) = loop[-1], loop[0]
        loop += [(xl + (xr - xl) * k / chord_segments, yl + (yr - yl) * k / chord_segments)
                 for k in range(1, chord_segments)]
    mx = sum(p[0] for p in loop) / len(loop)
    my = sum(p[1] for p in loop) / len(loop)

    def uv(x, y):
        return (0.5 + (x - cx) / (2.0 * radius), 0.5 - (y - cy) / (2.0 * radius))
    centre = part.add_vertex((mx, my, cz), V.Z, uv(mx, my))
    ids = [part.add_vertex((x, y, cz), V.Z, uv(x, y)) for x, y in loop]
    for k in range(len(ids)):
        part.add_triangle(centre, ids[k], ids[(k + 1) % len(ids)])
    return part


# --- composites ---------------------------------------------------------------------------------

def post(name, material, base, height, radius, segments, bevel_segments=2):
    """Vertical rounded post standing on ``base``."""
    top = (base[0], base[1] + height, base[2])
    return rounded_cylinder(name, material, base, top, radius, min(0.35 * radius, 0.04), segments,
                            bevel_segments)


def rail(name, material, points, radius, segments, cap_segments=2):
    """Hand rail through ``points``: a chain of capsules merged into one part."""
    pieces = [capsule(name, material, points[i], points[i + 1], radius, segments, cap_segments)
              for i in range(len(points) - 1)]
    return merge(pieces, name=name)


def pedestal(prefix, base_material, cap_material, center, half_extents, bevel, bevel_segments,
             cap_thickness=0.12, cap_inset=0.06, plinth_height=0.0, plinth_grow=0.0):
    """Base block standing on the ground at ``center`` (x, z) plus a slightly inset cap plate.

    An optional plinth tier (``plinth_height`` tall) grows by ``plinth_grow`` outward in x (away
    from the origin) and both ways in z, giving a stepped, sturdier silhouette.
    """
    hx, hy, hz = half_extents
    x, z = center
    base = rounded_box(f'{prefix}.base', base_material, (hx, hy, hz), bevel, bevel_segments,
                       V.translation((x, hy, z)))
    if plinth_height > 0.0:
        ph = 0.5 * plinth_height
        out = math.copysign(0.5 * plinth_grow, x) if x else 0.0
        plinth = rounded_box(f'{prefix}.plinth', base_material, (hx + 0.5 * plinth_grow, ph, hz + plinth_grow),
                             min(bevel, 0.9 * ph), bevel_segments, V.translation((x + out, ph, z)))
        base = merge([base, plinth], name=f'{prefix}.base')
    ch = 0.5 * cap_thickness
    cap = rounded_box(f'{prefix}.cap', cap_material, (hx - cap_inset, ch, hz - cap_inset),
                      min(bevel, ch * 0.9), bevel_segments, V.translation((x, 2.0 * hy + ch * 0.6, z)))
    return base, cap
