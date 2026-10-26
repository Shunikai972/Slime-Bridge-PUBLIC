"""Software preview of a runtime package (rest pose, 4 views). It shades with the *recipe* colours/textures
(baked ``_Diffuse``, or the Top/Middle/Bottom palette along the lighting UV), simple lambert + ambient.
It is an inspection aid, not a renderer of the games' shaders: faces and effects are not drawn."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .ir.store import unpack_mesh
from .materials.bake import lighting_z, palette_color

SKIP_PARTS = ('SlimeFace',)


def _material_color(base, recipe, uv0, uv1_present):
    t = recipe['target']
    tex = t.get('textures', {})
    if '_Diffuse' in tex:
        img = np.asarray(Image.open(base / tex['_Diffuse']['file']).convert('RGB'), dtype=np.float32) / 255.0
        h, w = img.shape[:2]
        x = np.clip((uv0[:, 0] * w).astype(int) % w, 0, w - 1)
        y = np.clip(((1.0 - uv0[:, 1]) * h).astype(int), 0, h - 1)
        return img[y, x]
    c = t.get('colors', {})
    f = t.get('floats', {})
    top = np.array(c.get('_TopColor', [0.8, 0.8, 0.8])[:3], np.float32)
    mid = np.array(c.get('_MiddleColor', [0.6, 0.6, 0.6])[:3], np.float32)
    bot = np.array(c.get('_BottomColor', [0.4, 0.4, 0.4])[:3], np.float32)
    z = lighting_z(uv0[:, 0], uv0[:, 1], f.get('_LightingUVContribution', 1.0), f.get('_LightingUVHorizontalAdjust', 0.0))
    return palette_color(z[None, :], bot, mid, top)[0]


def _draw(zbuf, img, pts, depth, colors, normals, tris, light):
    h, w = zbuf.shape
    for a, b, c in tris:
        pa, pb, pc = pts[a], pts[b], pts[c]
        den = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(den) < 1e-9:
            continue
        x0, x1 = int(max(0, np.floor(min(pa[0], pb[0], pc[0])))), int(min(w - 1, np.ceil(max(pa[0], pb[0], pc[0]))))
        y0, y1 = int(max(0, np.floor(min(pa[1], pb[1], pc[1])))), int(min(h - 1, np.ceil(max(pa[1], pb[1], pc[1]))))
        if x1 < x0 or y1 < y0:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        l1 = ((pb[1] - pc[1]) * (xs - pc[0]) + (pc[0] - pb[0]) * (ys - pc[1])) / den
        l2 = ((pc[1] - pa[1]) * (xs - pc[0]) + (pa[0] - pc[0]) * (ys - pc[1])) / den
        l3 = 1 - l1 - l2
        m = (l1 >= 0) & (l2 >= 0) & (l3 >= 0)
        if not m.any():
            continue
        z = l1 * depth[a] + l2 * depth[b] + l3 * depth[c]
        sl = (slice(y0, y1 + 1), slice(x0, x1 + 1))
        ok = m & (z < zbuf[sl])
        if not ok.any():
            continue
        n = l1[..., None] * normals[a] + l2[..., None] * normals[b] + l3[..., None] * normals[c]
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
        shade = 0.45 + 0.55 * np.clip((n * light).sum(-1), 0, 1)
        col = (l1[..., None] * colors[a] + l2[..., None] * colors[b] + l3[..., None] * colors[c]) * shade[..., None]
        zbuf[sl][ok] = z[ok]
        img[sl][ok] = col[ok]


def render_package(pkg_dir, out_png, size=384):
    base = Path(pkg_dir)
    parts = json.loads((base / 'parts.json').read_text(encoding='utf-8'))
    mats = json.loads((base / 'materials.json').read_text(encoding='utf-8'))['recipes']
    items = []
    for part in parts:
        if part['element'] in SKIP_PARTS:
            continue
        lod = next((l for l in part['lods'] if l['lod'] == 0), None)
        for r in (lod['renderers'] if lod else []):
            if not r.get('mesh_file'):
                continue
            hdr, arrs = unpack_mesh((base / r['mesh_file']).read_bytes())
            slot_mats = r['material_slots']
            for si, sm in enumerate(hdr['submeshes']):
                rid = slot_mats[min(si, len(slot_mats) - 1)] if slot_mats else None
                rec = mats.get(rid) if rid else None
                if rec is None or rec['family'] not in ('body', 'special'):
                    continue
                uv0 = arrs.get('uv0', np.zeros((len(arrs['positions']), 2), np.float32)).astype(np.float32)
                col = _material_color(base, rec, uv0, 'uv1' in arrs)
                nrm = arrs['normals'].astype(np.float32) if 'normals' in arrs else np.tile([0, 1, 0], (len(arrs['positions']), 1)).astype(np.float32)
                items.append((arrs['positions'].astype(np.float32), nrm, col.astype(np.float32), arrs[sm['index_array']].reshape(-1, 3)))
    if not items:
        raise ValueError('nothing renderable (no body/special material found)')
    allp = np.concatenate([p for p, *_ in items])
    c = (allp.min(0) + allp.max(0)) / 2
    radius = float(np.linalg.norm(allp.max(0) - allp.min(0))) / 2 or 1.0
    views = []
    for yaw in (0, 90, 180, 270):
        a = np.deg2rad(yaw)
        R = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]], np.float32)
        img = np.full((size, size, 3), 0.18, np.float32)
        zbuf = np.full((size, size), np.inf, np.float32)
        for p, n, col, tris in items:
            q = (p - c) @ R.T
            nn = n @ R.T
            pts = np.stack([(q[:, 0] / radius * 0.45 + 0.5) * size, (0.5 - q[:, 1] / radius * 0.45) * size], axis=1)
            _draw(zbuf, img, pts, -q[:, 2], col, nn * np.array([1, 1, -1], np.float32), tris[:, [0, 2, 1]], np.array([0, 0.3, -0.95], np.float32))
        views.append(img)
    sheet = np.concatenate(views, axis=1)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(sheet, 0, 1) * 255).astype(np.uint8)).save(out)
    return {'file': str(out), 'views': 4, 'size': [size * 4, size]}
