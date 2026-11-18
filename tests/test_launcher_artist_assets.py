"""Artist asset mapping and GIF transparency; local image files only."""
from pathlib import Path
from PIL import Image

UI = Path(__file__).resolve().parents[1]/'apps/launcher/ui'


def test_victor_animation_has_transparent_corners_on_every_frame():
    image = Image.open(UI/'img/need-games.gif')
    assert image.n_frames == 2
    durations = []
    for index in range(image.n_frames):
        image.seek(index)
        rgba = image.convert('RGBA')
        durations.append(image.info.get('duration'))
        assert all(rgba.getpixel(corner)[3] == 0 for corner in [(0,0),(319,0),(0,262),(319,262)])
        assert rgba.getpixel((160,130))[3] == 255, 'Do not erase the actual character'
    assert durations == [800,800]


def test_artist_largo_mapping_covers_user_files_and_one_honest_placeholder():
    import json
    manifest = json.loads((UI/'img/largos/artist-icons.json').read_text())
    expected = {'quantum_angler', 'quantum_batty', 'quantum_cotton', 'quantum_flutter', 'quantum_ringtail',
                'quantum_slumber', 'quantum_twin', 'rad_angler', 'rad_batty', 'rad_cotton',
                'rad_flutter', 'rad_hyper', 'rad_ringtail', 'rad_slumber', 'rad_twin'}
    assert set(manifest['icons']) == expected
    assert manifest['placeholder'] == 'unknown.png'
    for filename in list(manifest['icons'].values()) + [manifest['placeholder']]:
        with Image.open(UI/'img/largos'/filename) as image:
            assert image.mode == 'RGBA'
            assert image.getpixel((0,0))[3] == 0
