"""Bake the SR2 body shader's colour model into a UV-space RGBA albedo texture.

Documented model (see docs/MATERIALS.md; assumptions are recorded in every recipe):

* Mesh UV0 carries the *lighting UV*; the vertical gradient coordinate follows the shader's published
  lighting-UV control:  t = saturate((|(u-0.5, v)| - 0.25) * 4/3);  q = v + H * (smoothstep(t) - v);
  z = 0.5 + C * (q - 0.5)     with C = _LightingUVContribution and H = _LightingUVHorizontalAdjust.
* Palette gradient: z in [0,0.5] blends Bottom->Middle, z in [0.5,1] blends Middle->Top.
* _BODYCOLORING_MULTI: the _ColorMask RGB channels weight the Red/Green/Blue palettes; the remaining weight
  (1 - sum) keeps the base Top/Middle/Bottom palette.
* The mask is sampled with UV1 when _ColorMaskUV1 > 0.5, otherwise UV0 (same switch the shader exposes).

Colours stay in authored (sRGB) space: the bake output is an sRGB texture.
"""

import numpy as np

from .raster import rasterize_uv, dilate

BAKER_VERSION = 2


def lighting_z(u, v, contribution, horizontal):
    t = np.clip((np.hypot(u - 0.5, v) - 0.25) * (4.0 / 3.0), 0.0, 1.0)
    q = v + horizontal * (t * t * (3.0 - 2.0 * t) - v)
    return np.clip(0.5 + contribution * (q - 0.5), 0.0, 1.0)


def palette_color(z, bottom, middle, top):
    z = z[..., None]
    lo = bottom[None, None, :] * (1 - z * 2) + middle[None, None, :] * (z * 2)
    hi = middle[None, None, :] * (1 - (z - 0.5) * 2) + top[None, None, :] * ((z - 0.5) * 2)
    return np.where(z < 0.5, lo, hi)


def _rgb(mat, name, default=(1, 1, 1)):
    c = mat['colors'].get(name)
    return np.array(c[:3] if c else default, dtype=np.float32)


def sample_nearest(img, uv):
    h, w = img.shape[:2]
    x = np.clip((uv[..., 0] * w).astype(int) % w, 0, w - 1)
    y = np.clip(((1.0 - uv[..., 1]) * h).astype(int), 0, h - 1)  # PNG rows are top-down
    return img[y, x]


def bake_multi(mat, meshes, mask_img, size=None, pad=6):
    """meshes: list of dicts with positions, uv0, uv1 (optional) and index arrays (list of (M,3) int).
    mask_img: (H,W,4) float32 in 0..1 (top-down, as decoded). Returns (rgba uint8 (H,W,4), report dict)."""
    f = mat['floats']
    use_uv1 = f.get('_ColorMaskUV1', 0.0) > 0.5
    C = f.get('_LightingUVContribution', 1.0)
    H = f.get('_LightingUVHorizontalAdjust', 0.0)
    h, w = size or mask_img.shape[:2]
    base = (_rgb(mat, '_BottomColor'), _rgb(mat, '_MiddleColor'), _rgb(mat, '_TopColor'))
    pal = {ch: (_rgb(mat, f'_{ch}BottomColor'), _rgb(mat, f'_{ch}MiddleColor'), _rgb(mat, f'_{ch}TopColor')) for ch in ('Red', 'Green', 'Blue')}
    out = np.zeros((h, w, 3), dtype=np.float32)
    cov = np.zeros((h, w), dtype=bool)
    used_uv = 'uv1' if use_uv1 else 'uv0'

    def _mask_uv(me):
        uv = me.get(used_uv)
        return me['uv0'] if uv is None else uv

    for me in meshes:
        uv_mask = _mask_uv(me)
        uv0 = me['uv0']
        tris = np.concatenate(me['tris'], axis=0) if me['tris'] else np.zeros((0, 3), int)
        img, c = rasterize_uv(uv_mask, tris, uv0[:, :2].astype(np.float32), (h, w), flip_v=True)
        z = lighting_z(img[..., 0], img[..., 1], C, H)
        m = mask_img if mask_img.shape[:2] == (h, w) else np.asarray(__import__('PIL.Image', fromlist=['Image']).fromarray((mask_img * 255).astype(np.uint8)).resize((w, h)), dtype=np.float32) / 255.0
        wts = m[..., :3]
        s = np.clip(wts.sum(axis=2, keepdims=True), 0.0, 1.0)
        if s.max() > 1.0:
            wts = wts / np.maximum(wts.sum(axis=2, keepdims=True), 1.0)
        colour = (1 - s) * palette_color(z, *base)
        for k, ch in enumerate(('Red', 'Green', 'Blue')):
            colour = colour + wts[..., k:k + 1] * palette_color(z, *pal[ch])
        new = c & ~cov
        out[new] = colour[new]
        cov |= c
    out, cov2 = dilate(out, cov, pad)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(out * 255.0 + 0.5, 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    tiled = any(float(np.min(_mask_uv(me))) < 0 or float(np.max(_mask_uv(me))) > 1 for me in meshes)
    return rgba, {'baker_version': BAKER_VERSION, 'model': 'multi', 'wrap_tiled': bool(tiled), 'mask_uv': used_uv, 'lighting_uv': {'contribution': C, 'horizontal': H},
                  'coverage': float(cov.mean()), 'padded_coverage': float(cov2.mean()), 'size': [w, h]}
