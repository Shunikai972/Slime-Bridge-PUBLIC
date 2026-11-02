"""The colour probe must report rendered pixels, not silently re-grade screenshots."""
from pathlib import Path

import numpy as np
from PIL import Image

from tools.largo_models.colour_check import game_pixels


def test_same_exposure_pair_preserves_exact_foreground_rgb(tmp_path: Path):
    shot = tmp_path / 'actor.png'
    background = tmp_path / 'actor-bg.png'
    rgb = np.full((20, 20, 3), 70, dtype=np.uint8)
    Image.fromarray(rgb).save(background)
    rgb[5:15, 5:15] = (0, 200, 0)
    Image.fromarray(rgb).save(shot)

    pixels = game_pixels(shot, tmp_path / 'missing-mask.png')

    assert pixels.shape == (100, 3)
    np.testing.assert_array_equal(pixels, np.tile((0, 200, 0), (100, 1)))


def test_magenta_mask_selects_unmodified_sr1_pixels(tmp_path: Path):
    shot = tmp_path / 'actor.png'
    mask = tmp_path / 'actor-mask.png'
    Image.fromarray(np.array([[(12, 34, 56), (140, 200, 20)]], dtype=np.uint8)).save(shot)
    Image.fromarray(np.array([[(255, 0, 255), (0, 0, 0)]], dtype=np.uint8)).save(mask)

    pixels = game_pixels(shot, mask)

    np.testing.assert_array_equal(pixels, [[140, 200, 20]])


def test_background_pair_excludes_unchanged_occluder_without_regrading(tmp_path: Path):
    shot = tmp_path / 'actor.png'
    mask = tmp_path / 'actor-mask.png'
    background = tmp_path / 'actor-bg.png'
    Image.fromarray(np.array([[(30, 40, 50), (180, 70, 30)]], dtype=np.uint8)).save(shot)
    Image.fromarray(np.array([[(30, 40, 50), (20, 20, 20)]], dtype=np.uint8)).save(background)
    Image.fromarray(np.zeros((1, 2, 3), dtype=np.uint8)).save(mask)

    pixels = game_pixels(shot, mask)

    np.testing.assert_array_equal(pixels, [[180, 70, 30]])
