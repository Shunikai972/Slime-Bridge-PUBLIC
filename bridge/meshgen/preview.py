"""Tiny pure-Python software rasteriser for human inspection previews (PNG, stdlib only).

Perspective camera, z-buffer, Gouraud-shaded Lambert lighting with a camera-relative key light,
hemispheric ambient, a soft rim term, emissive materials, a translucent pass for the portal
energy, a projected ground shadow and a thin depth-edge outline. Supersampling smooths edges.

This is a look-dev aid only. It says nothing about how Blender, Unity or a game will render the
asset (NEEDS_LOCAL_VISUAL_PROOF).
"""

import math
import struct
import zlib

from . import mesh as M
from . import vec as V
from .palettes import material, srgb_to_linear

VIEWS = {
    # name: (azimuth degrees from +Z toward +X, elevation degrees, frame fill factor)
    'front': (0.0, 9.0, 1.0),
    'back': (180.0, 9.0, 1.0),
    'side': (90.0, 9.0, 1.0),
    'three_quarter': (38.0, 13.0, 1.05),
}

_SKY_TOP = (0.55, 0.76, 0.95)
_SKY_HORIZON = (0.88, 0.94, 0.98)
_GRASS = (0.52, 0.74, 0.40)
_GRASS_FAR = (0.70, 0.84, 0.62)
_CLEARING = (0.62, 0.80, 0.47)


def _linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1.0 / 2.4) - 0.055


class Camera:
    def __init__(self, eye, target, fov_deg, width, height, up=V.Y):
        self.eye = tuple(eye)
        self.f = V.normalize(V.sub(target, eye))
        self.r = V.normalize(V.cross(self.f, up))
        self.u = V.cross(self.r, self.f)
        self.width, self.height = width, height
        self.tan = math.tan(math.radians(fov_deg) * 0.5)
        self.focal = (height * 0.5) / self.tan

    def project(self, p):
        d = V.sub(p, self.eye)
        z = V.dot(d, self.f)
        if z < 0.05:
            return None
        return (self.width * 0.5 + V.dot(d, self.r) / z * self.focal,
                self.height * 0.5 - V.dot(d, self.u) / z * self.focal, 1.0 / z)

    def ray(self, px, py):
        x = (px - self.width * 0.5) / self.focal
        y = (self.height * 0.5 - py) / self.focal
        return V.normalize(V.add(self.f, V.add(V.scale(self.r, x), V.scale(self.u, y))))


def view_camera(view, box, width, height, fov_deg=36.0, fill=0.86):
    """Camera looking at the model's bounds from a named view; the projected bounding-box corners
    fill ``fill`` (scaled by the view's factor) of the frame."""
    az, el, factor = VIEWS[view]
    lo, hi = box
    target = V.lerp(lo, hi, 0.5)
    a, e = math.radians(az), math.radians(el)
    direction = (math.sin(a) * math.cos(e), math.sin(e), math.cos(a) * math.cos(e))
    corners = [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]

    def extent(dist):
        cam = Camera(V.madd(target, direction, dist), target, fov_deg, width, height)
        worst = 0.0
        for c in corners:
            s = cam.project(c)
            if s is None:
                return math.inf
            worst = max(worst, abs(s[0] - 0.5 * width) / (0.5 * width), abs(s[1] - 0.5 * height) / (0.5 * height))
        return worst
    near, far = 0.1, 10.0 * (V.length(V.sub(hi, lo)) + 1.0)
    for _ in range(40):  # smallest distance whose framing fits (extent shrinks with distance)
        mid = 0.5 * (near + far)
        near, far = (mid, far) if extent(mid) > fill * factor else (near, mid)
    return Camera(V.madd(target, direction, far), target, fov_deg, width, height)


class Canvas:
    def __init__(self, width, height):
        self.w, self.h = width, height
        n = width * height
        self.z = [0.0] * n
        self.r = [0.0] * n
        self.g = [0.0] * n
        self.b = [0.0] * n
        self.model = bytearray(n)


def _span(a, b, x0, x1):
    """Restrict [x0, x1] to where a + b*x >= 0."""
    if b > 0.0:
        return max(x0, -a / b), x1
    if b < 0.0:
        return x0, min(x1, -a / b)
    return (x0, x1) if a >= -1e-12 else (1.0, 0.0)


def _raster(cv, v0, v1, v2, alpha=None):
    """Fill one screen triangle; v = (x, y, inv_z, r, g, b). ``alpha`` blends without z-write."""
    x0, y0, z0, r0, g0, b0 = v0
    x1, y1, z1, r1, g1, b1 = v1
    x2, y2, z2, r2, g2, b2 = v2
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(area) < 1e-9:
        return
    inv = 1.0 / area
    w, h = cv.w, cv.h
    ymin = max(0, int(math.ceil(min(y0, y1, y2) - 0.5)))
    ymax = min(h - 1, int(math.floor(max(y0, y1, y2) - 0.5)))
    xlo = max(0.0, min(x0, x1, x2))  # continuous x; pixel centres sit at px + 0.5
    xhi = min(w - 0.5, max(x0, x1, x2))
    # w0(x, y) = ((x1-x)(y2-y) - (x2-x)(y1-y)) / area ; w1 likewise
    dw0 = (y1 - y2) * inv
    dw1 = (y2 - y0) * inv
    pad = 1e-3 if alpha is None else 0.0  # closes hairline cracks on shared edges (z-test hides overlap)
    zb, rb, gb, bb, mb = cv.z, cv.r, cv.g, cv.b, cv.model
    for py in range(ymin, ymax + 1):
        sy = py + 0.5
        a0 = ((x1) * (y2 - sy) - (x2) * (y1 - sy)) * inv
        a1 = ((x2) * (y0 - sy) - (x0) * (y2 - sy)) * inv
        # w0 = a0 + dw0 * sx ; w1 = a1 + dw1 * sx ; w2 = 1 - w0 - w1
        lo, hi = _span(a0, dw0, xlo, xhi)
        lo, hi = _span(a1, dw1, lo, hi)
        lo, hi = _span(1.0 - a0 - a1, -dw0 - dw1, lo, hi)
        px0 = int(math.ceil(lo - 0.5 - pad))
        px1 = int(math.floor(hi - 0.5 + pad))
        if px1 < px0:
            continue
        row = py * w
        sx = px0 + 0.5
        wa = a0 + dw0 * sx
        wb = a1 + dw1 * sx
        for px in range(px0, px1 + 1):
            wc = 1.0 - wa - wb
            iz = wa * z0 + wb * z1 + wc * z2
            idx = row + px
            if iz > zb[idx]:
                if alpha is None:
                    zb[idx] = iz
                    rb[idx] = wa * r0 + wb * r1 + wc * r2
                    gb[idx] = wa * g0 + wb * g1 + wc * g2
                    bb[idx] = wa * b0 + wb * b1 + wc * b2
                    mb[idx] = 1
                else:
                    k = 1.0 - alpha
                    rb[idx] = rb[idx] * k + (wa * r0 + wb * r1 + wc * r2) * alpha
                    gb[idx] = gb[idx] * k + (wa * g0 + wb * g1 + wc * g2) * alpha
                    bb[idx] = bb[idx] * k + (wa * b0 + wb * b1 + wc * b2) * alpha
            wa += dw0
            wb += dw1


def _background(cv, cam):
    w, h = cv.w, cv.h
    eye = cam.eye
    for py in range(h):
        for px in range(w):
            d = cam.ray(px + 0.5, py + 0.5)
            idx = py * w + px
            if d[1] < -1e-4:
                t = -eye[1] / d[1]
                hx, hz = eye[0] + d[0] * t, eye[2] + d[2] * t
                dist = math.hypot(hx, hz)
                k = max(0.0, min(1.0, (dist - 4.0) / 6.0))
                base = tuple(_CLEARING[c] * (1 - k) + _GRASS[c] * k for c in range(3))
                fog = max(0.0, min(1.0, (t - 12.0) / 40.0))
                col = tuple(base[c] * (1 - fog) + _GRASS_FAR[c] * fog for c in range(3))
                cv.z[idx] = 1.0 / max(1e-3, t * V.dot(d, cam.f))
            else:
                k = min(1.0, max(0.0, d[1]) * 2.5)
                col = tuple(_SKY_HORIZON[c] * (1 - k) + _SKY_TOP[c] * k for c in range(3))
            cv.r[idx], cv.g[idx], cv.b[idx] = col


def _shadow(cv, cam, tris, light):
    """Darken ground pixels covered by the model's projection along the key light."""
    if light[1] < 0.1:
        return
    mask = bytearray(cv.w * cv.h)
    shadow_cv = Canvas.__new__(Canvas)
    shadow_cv.w, shadow_cv.h = cv.w, cv.h
    shadow_cv.z = [0.0] * (cv.w * cv.h)
    shadow_cv.r = shadow_cv.g = shadow_cv.b = [0.0] * (cv.w * cv.h)
    shadow_cv.model = mask
    for a, b, c in tris:
        pts = []
        for p in (a, b, c):
            g = V.madd(p, light, -p[1] / light[1])
            s = cam.project(g)
            if s is None:
                break
            pts.append((s[0], s[1], 1.0, 0.0, 0.0, 0.0))
        if len(pts) == 3:
            _raster(shadow_cv, *pts)
    for idx in range(cv.w * cv.h):
        if mask[idx]:
            cv.r[idx] *= 0.68
            cv.g[idx] *= 0.7
            cv.b[idx] *= 0.74


def _shade(base_lin, emissive_lin, n, view_dir, light):
    ndl = max(0.0, V.dot(n, light))
    sky = 0.5 + 0.5 * n[1]
    ambient = 0.4 * sky + 0.22 * (1.0 - sky)
    rim = (1.0 - max(0.0, -V.dot(n, view_dir))) ** 3 * 0.18
    k = ambient + 0.72 * ndl + rim
    return tuple(_linear_to_srgb(base_lin[c] * k + emissive_lin[c]) for c in range(3))


def render(parts, palette_id, cam, supersample=2):
    """Render mesh parts to an RGB byte image ``(width, height, bytes)``."""
    ss = max(1, int(supersample))
    scam = Camera(cam.eye, V.add(cam.eye, cam.f), math.degrees(2 * math.atan(cam.tan)), cam.width * ss,
                  cam.height * ss)
    cv = Canvas(scam.width, scam.height)
    _background(cv, scam)
    light = V.normalize(V.add(V.add(V.scale(scam.f, -1.0), V.scale(scam.u, 0.95)), V.scale(scam.r, -0.6)))
    light = V.normalize((light[0], max(light[1], 0.5), light[2]))
    opaque, translucent, shadow_tris = [], [], []
    for part in parts:
        mat = material(palette_id, part.material)
        base = tuple(srgb_to_linear(c) for c in mat['base_srgb'])
        emis = tuple(srgb_to_linear(c) * 0.9 for c in mat['emissive_srgb'])
        is_translucent = mat['alpha'] < 1.0
        pos, nor, idx = part.positions, part.normals, part.indices
        proj = [scam.project(p) for p in pos]
        for t in range(0, len(idx), 3):
            ia, ib, ic = idx[t], idx[t + 1], idx[t + 2]
            if not is_translucent:
                shadow_tris.append((pos[ia], pos[ib], pos[ic]))
            sa, sb, sc = proj[ia], proj[ib], proj[ic]
            if sa is None or sb is None or sc is None:
                continue
            area = (sb[0] - sa[0]) * (sc[1] - sa[1]) - (sc[0] - sa[0]) * (sb[1] - sa[1])
            back = area > 0.0  # screen y points down: counter-clockwise world faces have negative area
            if back and not mat['double_sided']:
                continue
            verts = []
            for i, s in ((ia, sa), (ib, sb), (ic, sc)):
                n = V.scale(nor[i], -1.0) if back else nor[i]
                vd = V.normalize(V.sub(pos[i], scam.eye))
                col = _shade(base, emis, n, vd, light)
                if part.material == 'portal_energy' and part.name == 'portal.membrane':
                    rr = min(1.0, 2.0 * math.hypot(part.uvs[i][0] - 0.5, part.uvs[i][1] - 0.5))
                    col = tuple(min(1.0, c * (0.75 + 0.35 * rr) + 0.12 * rr) for c in col)
                verts.append((s[0], s[1], s[2]) + col)
            (translucent if is_translucent else opaque).append((verts, mat['alpha']))
    _shadow(cv, scam, shadow_tris, light)
    for verts, _a in opaque:
        _raster(cv, *verts)
    for verts, alpha in translucent:
        _raster(cv, *verts, alpha=alpha)
    _outline(cv)
    return _downsample(cv, ss)


def _outline(cv):
    """Darken model pixels at depth discontinuities (a soft cartoon outline)."""
    w, h, z, m = cv.w, cv.h, cv.z, cv.model
    edges = []
    for py in range(h - 1):
        row = py * w
        for px in range(w - 1):
            i = row + px
            za = z[i]
            for j in (i + 1, i + w):
                zb = z[j]
                if (m[i] or m[j]) and za > 0.0 and zb > 0.0:
                    da, db = 1.0 / za, 1.0 / zb
                    if abs(da - db) > 0.08 * min(da, db):
                        edges.append(i if da < db else j)
    for i in edges:
        cv.r[i] *= 0.35
        cv.g[i] *= 0.35
        cv.b[i] *= 0.4


def _downsample(cv, ss):
    w, h = cv.w // ss, cv.h // ss
    out = bytearray()
    k = 255.0 / (ss * ss)
    for y in range(h):
        for x in range(w):
            r = g = b = 0.0
            for dy in range(ss):
                base = (y * ss + dy) * cv.w + x * ss
                for dx in range(ss):
                    r += cv.r[base + dx]
                    g += cv.g[base + dx]
                    b += cv.b[base + dx]
            out += bytes((min(255, int(r * k + 0.5)), min(255, int(g * k + 0.5)), min(255, int(b * k + 0.5))))
    return w, h, bytes(out)


def png_bytes(width, height, rgb):
    """Minimal deterministic PNG (8-bit RGB, filter 0, zlib level 9)."""
    stride = width * 3
    raw = b''.join(b'\x00' + rgb[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def render_views(model, level=1, width=480, height=360, supersample=2, views=None):
    """``{view: png_bytes}`` for the requested views of one LOD (LOD1 by default for speed)."""
    level = min(level, len(model.lods) - 1)
    parts = model.lods[level]['parts']
    box = M.bounds(parts)
    out = {}
    for view in views or VIEWS:
        cam = view_camera(view, box, width, height)
        w, h, rgb = render(parts, model.params.palette, cam, supersample)
        out[view] = png_bytes(w, h, rgb)
    return out
