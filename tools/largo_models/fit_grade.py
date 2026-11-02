"""Fit each largo's colour correction from in-game measurements and store it in grade_overrides.json.

    py -3.12 -m tools.largo_models.fit_grade <measurement lines file>

Each line of the file names a largo id and the LAB run whose close-up measured it (as the calibration loop prints them).
From the CIELAB comparison with its reference: a value factor from the luminance ratio (texture values are sRGB, so
^(1/2.2)) and a saturation factor from the mean pixel chroma ratio, both damped by half and clamped, multiplied into the
stored ones. tools/largo_models/postpass.py applies base grade x these factors.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tools.largo_models import colour_check, postpass
from tools.largo_models.build import describe

ROOT = Path(__file__).resolve().parents[2]


def luminance(L: float) -> float:
    return ((L + 16) / 116) ** 3


def fit(lines_file: Path, damping: float = 0.5) -> dict:
    overrides = json.loads(postpass.OVERRIDES.read_text(encoding='utf-8')) if postpass.OVERRIDES.is_file() else {}
    report = {}
    for line in lines_file.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^(\w+) .* (sanctuary-[\w-]+)', line)
        if not m:
            continue
        mid, run = m.groups()
        shots = [s for s in sorted((ROOT / 'Lab' / 'RuntimeArtifacts' / run / 'shots').glob('actor-c-*.png')) if not s.stem.endswith(('-mask', '-bg'))]
        if not shots:
            continue
        shot = shots[0]
        r = colour_check.compare(
            colour_check.srgb_to_lab(colour_check.game_pixels(shot, shot.with_name(shot.stem + '-mask.png'))),
            colour_check.srgb_to_lab(colour_check.reference_pixels(ROOT / 'Lab' / 'LargoModels' / 'reference' / f'{mid}.png')))
        g, f = r['game'], r['reference']
        if r['deltaE_mean'] > 30:                          # a fire burst, smoke or a stray actor in the shot: not a colour error
            report[mid] = {'deltaE': r['deltaE_mean'], 'skipped': 'outlier'}
            continue
        value = (luminance(f['L']) / max(luminance(g['L']), 1e-6)) ** (1 / 2.2)
        saturation = f['pixel_chroma'] / max(g['pixel_chroma'], 1e-6)
        value = min(1.35, max(0.75, 1 + (value - 1) * damping))
        saturation = min(1.4, max(0.7, 1 + (saturation - 1) * damping))
        canonical = describe(mid)['canonical']
        old = overrides.get(canonical, {'saturation': 1.0, 'value': 1.0})
        overrides[canonical] = {'saturation': round(old['saturation'] * saturation, 4), 'value': round(old['value'] * value, 4)}
        report[mid] = {'deltaE': r['deltaE_mean'], 'dL': r['dL'], 'chroma_ratio': r['chroma_ratio'], **overrides[canonical]}
    postpass.OVERRIDES.write_text(json.dumps(dict(sorted(overrides.items())), indent=1), encoding='utf-8')
    return report


if __name__ == '__main__':
    for mid, row in fit(Path(sys.argv[1])).items():
        print(mid, row)
