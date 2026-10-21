"""Tiny numpy triangle rasteriser used for (a) baking per-texel attributes in UV space and (b) offline
preview renders. Pure CPU, no GPU/OpenGL dependency, deterministic."""

import numpy as np


def rasterize_uv(uv, tris, attrs, size, flip_v=False, wrap=True):
    """Rasterise triangles in UV space (texel centres) and interpolate ``attrs`` (N,K) barycentrically.
    Returns (image (H,W,K) float32, coverage (H,W) bool). Texel row 0 = v=0 unless flip_v. With ``wrap`` a triangle
    whose UVs leave [0,1] is drawn at every integer UV shift that lands in the texture (the games sample these
    textures with Repeat), so tiled UV layouts are baked instead of silently dropped."""
    h, w = size
    out = np.zeros((h, w, attrs.shape[1]), dtype=np.float32)
    cov = np.zeros((h, w), dtype=bool)
    pts = np.stack([uv[:, 0] * w, (1.0 - uv[:, 1] if flip_v else uv[:, 1]) * h], axis=1)
    jobs = []
    for a, b, c in tris:
        pa, pb, pc = pts[a], pts[b], pts[c]
        if wrap:
            xs_ = (pa[0], pb[0], pc[0])
            ys_ = (pa[1], pb[1], pc[1])
            for kx in range(int(np.floor(min(xs_) / w)), int(np.floor(max(xs_) / w)) + 1):
                for ky in range(int(np.floor(min(ys_) / h)), int(np.floor(max(ys_) / h)) + 1):
                    off = np.array([kx * w, ky * h], dtype=np.float64)
                    jobs.append((a, b, c, pa - off, pb - off, pc - off))
        else:
            jobs.append((a, b, c, pa, pb, pc))
    for a, b, c, pa, pb, pc in jobs:
        den = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(den) < 1.0:
            # Sliver in UV space (area < half a texel): meshes mapped by one coordinate only (e.g. a tail addressed by v)
            # have no UV area, yet the shader samples that texel line. Rasterise the three edges instead.
            for (i0, p0), (i1, p1) in (((a, pa), (b, pb)), ((b, pb), (c, pc)), ((c, pc), (a, pa))):
                n = int(np.ceil(max(abs(p1[0] - p0[0]), abs(p1[1] - p0[1])) * 2)) + 1
                for k in range(n + 1):
                    t = k / max(n, 1)
                    x = p0[0] + (p1[0] - p0[0]) * t
                    y = p0[1] + (p1[1] - p0[1]) * t
                    xi, yi = int(np.floor(x)), int(np.floor(y))
                    if 0 <= xi < w and 0 <= yi < h:
                        out[yi, xi] = attrs[i0] * (1 - t) + attrs[i1] * t
                        cov[yi, xi] = True
            if abs(den) < 1e-12:
                continue
        x0, x1 = int(max(0, np.floor(min(pa[0], pb[0], pc[0])))), int(min(w - 1, np.ceil(max(pa[0], pb[0], pc[0]))))
        y0, y1 = int(max(0, np.floor(min(pa[1], pb[1], pc[1])))), int(min(h - 1, np.ceil(max(pa[1], pb[1], pc[1]))))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l1 = ((pb[1] - pc[1]) * (xs - pc[0]) + (pc[0] - pb[0]) * (ys - pc[1])) / den
        l2 = ((pc[1] - pa[1]) * (xs - pc[0]) + (pa[0] - pc[0]) * (ys - pc[1])) / den
        l3 = 1.0 - l1 - l2
        eps = -1e-4
        m = (l1 >= eps) & (l2 >= eps) & (l3 >= eps)
        if not m.any():
            continue
        val = l1[..., None] * attrs[a] + l2[..., None] * attrs[b] + l3[..., None] * attrs[c]
        sl = (slice(y0, y1 + 1), slice(x0, x1 + 1))
        out[sl][m] = val[m]
        cov[sl] |= m
    return out, cov


def dilate(img, cov, iterations=4):
    """Bleed covered texels into the neighbouring uncovered ones (prevents seams when sampled with filtering)."""
    img = img.copy()
    cov = cov.copy()
    for _ in range(iterations):
        grow = np.zeros_like(cov)
        acc = np.zeros_like(img)
        cnt = np.zeros(cov.shape, dtype=np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh_c = np.roll(cov, (dy, dx), axis=(0, 1))
            sh_i = np.roll(img, (dy, dx), axis=(0, 1))
            take = sh_c & ~cov
            acc[take] += sh_i[take]
            cnt[take] += 1
            grow |= take
        sel = grow & (cnt > 0)
        img[sel] = acc[sel] / cnt[sel][:, None]
        cov |= grow
    return img, cov
