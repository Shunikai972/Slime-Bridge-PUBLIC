"""Measure how close a largo's in-game colours are to its Blender reference (CIELAB).

    py -3.12 -m tools.largo_models.colour_check --game <actor shot.png> [--mask <...-mask.png>] --reference <reference.png>

In-game pixels are those of the close-up's mask (the slime drawn alone on magenta); reference pixels are the opaque ones of
tools/largo_models/reference_render.py. Reports the mean colour (L, chroma, hue), the gaps, and a 4-colour palette of each,
matched by weight; deltaE is CIE76 on the means and the weighted palette distance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def game_pixels(shot: Path, mask: Path) -> np.ndarray:
    """Return original shot RGB selected by a mask or a same-exposure background pair.

    SR2 captures must use the same settled camera and exposure. Fitting separate
    channel gains to the background silently changes the measured slime colours.
    """
    img = np.asarray(Image.open(shot).convert('RGB'), dtype=float).reshape(-1, 3)
    background = shot.with_name(shot.stem + '-bg.png')
    if not mask.is_file() and background.is_file():
        bg = np.asarray(Image.open(background).convert('RGB'), dtype=float).reshape(-1, 3)
        return img[np.abs(img - bg).sum(axis=1) > 40]
    m = np.asarray(Image.open(mask).convert('RGB'), dtype=float).reshape(-1, 3)
    keep = np.abs(m - np.array([255, 0, 255])).sum(axis=1) > 90
    if background.is_file():                     # pixels identical without the slime are an obstacle in front of it
        bg = np.asarray(Image.open(background).convert('RGB'), dtype=float).reshape(-1, 3)
        keep &= np.abs(img - bg).sum(axis=1) > 30
    return img[keep]


def reference_pixels(ref: Path) -> np.ndarray:
    a = np.asarray(Image.open(ref).convert('RGBA'), dtype=float).reshape(-1, 4)
    return a[a[:, 3] > 230][:, :3]


def kmeans(lab: np.ndarray, k: int = 4, iterations: int = 20, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    sample = lab[rng.choice(len(lab), min(len(lab), 20000), replace=False)]
    centres = sample[np.argsort(sample[:, 0])[np.linspace(0, len(sample) - 1, k).astype(int)]]
    for _ in range(iterations):
        labels = np.argmin(((sample[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
        centres = np.array([sample[labels == i].mean(0) if (labels == i).any() else centres[i] for i in range(k)])
    weights = np.bincount(labels, minlength=k) / len(labels)
    order = np.argsort(centres[:, 0])
    return centres[order], weights[order]


def describe(lab: np.ndarray) -> dict:
    mean = lab.mean(0)
    chroma = float(np.hypot(mean[1], mean[2]))
    pix_chroma = float(np.hypot(lab[:, 1], lab[:, 2]).mean())
    centres, weights = kmeans(lab)
    return {'L': round(float(mean[0]), 1), 'a': round(float(mean[1]), 1), 'b': round(float(mean[2]), 1), 'chroma': round(chroma, 1),
            'pixel_chroma': round(pix_chroma, 1), 'hue': round(float(np.degrees(np.arctan2(mean[2], mean[1])) % 360), 1),
            'palette': [[round(float(x), 1) for x in c] + [round(float(w), 2)] for c, w in zip(centres, weights)]}


def compare(game_lab: np.ndarray, ref_lab: np.ndarray) -> dict:
    g, r = describe(game_lab), describe(ref_lab)
    hue_gap = (g['hue'] - r['hue'] + 180) % 360 - 180
    de_mean = float(np.linalg.norm(np.array([g['L'], g['a'], g['b']]) - np.array([r['L'], r['a'], r['b']])))
    # palette distance: lightness-ordered clusters paired, weighted
    gp, rp = np.array(g['palette']), np.array(r['palette'])
    de_palette = float((np.linalg.norm(gp[:, :3] - rp[:, :3], axis=1) * (gp[:, 3] + rp[:, 3]) / 2).sum())
    return {'game': g, 'reference': r, 'dL': round(g['L'] - r['L'], 1), 'chroma_ratio': round(g['pixel_chroma'] / max(r['pixel_chroma'], 1e-6), 2),
            'hue_gap': round(hue_gap, 1), 'deltaE_mean': round(de_mean, 1), 'deltaE_palette': round(de_palette, 1)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--game', required=True)
    ap.add_argument('--mask')
    ap.add_argument('--reference', required=True)
    args = ap.parse_args(argv)
    shot = Path(args.game)
    mask = Path(args.mask) if args.mask else shot.with_name(shot.stem + '-mask.png')
    result = compare(srgb_to_lab(game_pixels(shot, mask)), srgb_to_lab(reference_pixels(Path(args.reference))))
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
