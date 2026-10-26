"""Oriented boxes (OBBs) for collision proxies and trigger volumes.

A box is a plain JSON-ready dict::

    {'name': 'deck', 'shape': 'box', 'center': [x, y, z], 'half_extents': [hx, hy, hz],
     'rotation': [qx, qy, qz, qw]}

``rotation`` maps the box's local axes to the model frame (glTF quaternion order).
"""

import itertools

from . import vec as V


def make_box(name, center, half_extents, axes=None, digits=6):
    q = V.quat_from_axes(*axes) if axes is not None else (0.0, 0.0, 0.0, 1.0)
    return {'name': name, 'shape': 'box', 'center': list(V.rounded(center, digits)),
            'half_extents': list(V.rounded(half_extents, digits)), 'rotation': list(V.rounded(q, digits))}


def axes(box):
    return V.axes_from_quat(tuple(box['rotation']))


def corners(box):
    c, h = box['center'], box['half_extents']
    ax = axes(box)
    out = []
    for sx, sy, sz in itertools.product((-1.0, 1.0), repeat=3):
        p = tuple(c)
        for s, a, e in zip((sx, sy, sz), ax, h):
            p = V.madd(p, a, s * e)
        out.append(p)
    return out


def aabb(boxes):
    pts = [p for b in boxes for p in corners(b)]
    return (tuple(min(p[k] for p in pts) for k in range(3)), tuple(max(p[k] for p in pts) for k in range(3)))


def contains_point(box, point, tolerance=0.0):
    d = V.sub(point, tuple(box['center']))
    return all(abs(V.dot(d, a)) <= e + tolerance for a, e in zip(axes(box), box['half_extents']))


def overlap(a, b, epsilon=1e-6):
    """Separating-axis test: True when the two boxes overlap by more than ``epsilon``."""
    aa, ba = axes(a), axes(b)
    ha, hb = a['half_extents'], b['half_extents']
    d = V.sub(tuple(b['center']), tuple(a['center']))
    candidates = list(aa) + list(ba) + [V.cross(x, y) for x in aa for y in ba]
    for axis in candidates:
        n = V.length(axis)
        if n < 1e-9:
            continue
        axis = V.scale(axis, 1.0 / n)
        ra = sum(e * abs(V.dot(u, axis)) for u, e in zip(aa, ha))
        rb = sum(e * abs(V.dot(u, axis)) for u, e in zip(ba, hb))
        if abs(V.dot(d, axis)) >= ra + rb - epsilon:
            return False
    return True


def shrunk(box, amount):
    out = dict(box)
    out['half_extents'] = [max(0.0, e - amount) for e in box['half_extents']]
    return out
