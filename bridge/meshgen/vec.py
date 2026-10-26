"""Small 3D vector / affine helpers on plain tuples (stdlib only, deterministic).

Matrices are 3x4 row-major tuples ``((r00, r01, r02, tx), (r10, ...), (r20, ...))``: a linear 3x3
part plus a translation column. Quaternions are ``(x, y, z, w)`` like glTF.
"""

import math

X = (1.0, 0.0, 0.0)
Y = (0.0, 1.0, 0.0)
Z = (0.0, 0.0, 1.0)
IDENTITY = ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def madd(a, b, s):
    """a + b * s"""
    return (a[0] + b[0] * s, a[1] + b[1] * s, a[2] + b[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(dot(a, a))


def normalize(a, fallback=Y):
    n = length(a)
    if not n > 1e-12 or not math.isfinite(n):
        return fallback
    return (a[0] / n, a[1] / n, a[2] / n)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def any_perpendicular(v):
    """A unit vector perpendicular to ``v`` chosen deterministically."""
    helper = X if abs(v[0]) < 0.9 else Y
    return normalize(cross(helper, v))


# --- affine 3x4 ---------------------------------------------------------------------------------

def from_axes(ax, ay, az, origin=(0.0, 0.0, 0.0)):
    """Matrix whose columns are the given local axes (local x/y/z expressed in parent space)."""
    return ((ax[0], ay[0], az[0], origin[0]), (ax[1], ay[1], az[1], origin[1]),
            (ax[2], ay[2], az[2], origin[2]))


def translation(t):
    return ((1.0, 0.0, 0.0, t[0]), (0.0, 1.0, 0.0, t[1]), (0.0, 0.0, 1.0, t[2]))


def rotation_axis(axis, angle):
    x, y, z = normalize(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return ((t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0),
            (t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0),
            (t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0))


def scaling(s):
    sx, sy, sz = (s, s, s) if isinstance(s, (int, float)) else s
    return ((sx, 0.0, 0.0, 0.0), (0.0, sy, 0.0, 0.0), (0.0, 0.0, sz, 0.0))


def compose(a, b):
    """Matrix applying ``b`` first, then ``a``."""
    rows = []
    for i in range(3):
        r = a[i]
        rows.append(tuple(r[0] * b[0][j] + r[1] * b[1][j] + r[2] * b[2][j] for j in range(3)) +
                    (r[0] * b[0][3] + r[1] * b[1][3] + r[2] * b[2][3] + r[3],))
    return tuple(rows)


def apply_point(m, p):
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
            m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
            m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])


def apply_vector(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def determinant(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
            m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
            m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def normal_matrix(m):
    """Inverse-transpose of the linear part (as a 3x4 with zero translation)."""
    det = determinant(m)
    if abs(det) < 1e-15:
        raise ValueError('singular transform')
    c = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            a, b = [k for k in range(3) if k != i], [k for k in range(3) if k != j]
            minor = m[a[0]][b[0]] * m[a[1]][b[1]] - m[a[0]][b[1]] * m[a[1]][b[0]]
            c[i][j] = ((-1) ** (i + j)) * minor / det
    # cofactor / det == inverse transpose
    return tuple(tuple(c[i]) + (0.0,) for i in range(3))


# --- quaternions --------------------------------------------------------------------------------

def quat_from_axes(ax, ay, az):
    """Unit quaternion (x, y, z, w) for the orthonormal right-handed basis given as columns."""
    m00, m01, m02 = ax[0], ay[0], az[0]
    m10, m11, m12 = ax[1], ay[1], az[1]
    m20, m21, m22 = ax[2], ay[2], az[2]
    trace = m00 + m11 + m22
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    n = math.sqrt(sum(c * c for c in q))
    q = tuple(c / n for c in q)
    if q[3] < 0.0:  # canonical hemisphere for deterministic output
        q = tuple(-c for c in q)
    return q


def axes_from_quat(q):
    x, y, z, w = q
    ax = (1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w))
    ay = (2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w))
    az = (2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y))
    return ax, ay, az


def frame_from_y(direction):
    """Right-handed axes (ax, ay, az) whose +Y is ``direction``."""
    ay = normalize(direction)
    az = any_perpendicular(ay)
    ax = cross(ay, az)
    return ax, ay, az


def rounded(values, digits=6):
    """Round floats for deterministic serialization (avoids -0.0)."""
    return tuple(round(v, digits) + 0.0 for v in values)
