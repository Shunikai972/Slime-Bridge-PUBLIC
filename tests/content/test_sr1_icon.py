"""Synthetic unpacked full-texture Sprite metadata, no native art."""
import copy
import importlib

import pytest


def fixture():
    return {'m_Name': 'iconSlimeRad', 'm_Rect': dict(x=0, y=0, width=4, height=4),
        'm_Pivot': dict(x=.5, y=.5), 'm_Border': dict(x=0, y=0, z=0, w=0),
        'm_PixelsToUnits': 2, 'm_AtlasTags': [], 'm_SpriteAtlas': dict(m_FileID=0, m_PathID=0),
        'm_RD': {'texture': dict(m_FileID=0, m_PathID=10), 'alphaTexture': dict(m_FileID=0, m_PathID=0),
            'settingsRaw': 64, 'textureRect': dict(x=0, y=1, width=4, height=3),
            'textureRectOffset': dict(x=0, y=1), 'uvTransform': dict(x=2, y=2, z=2, w=2)}}


def helper():
    try:
        return importlib.import_module('bridge.content.sr1_icon').build_icon_descriptor
    except ModuleNotFoundError:
        pytest.fail('bounded SR1 Sprite metadata contract missing')


def test_descriptor_preserves_original_framing_and_mesh():
    tree = fixture()
    before = copy.deepcopy(tree)
    result = helper()(tree, (4, 4), [[-1, -1], [1, -1], [0, 1]], [0, 1, 2],
                      sprite_id=20, appearance_id=30)
    assert result['rect'] == [0, 0, 4, 4]
    assert result['source_texture_rect'] == [0, 1, 4, 3]
    assert result['pixels_per_unit'] == 2
    assert result['pixel_encoding'] == 'source-texture-rgba-unresampled'
    assert tree == before


@pytest.mark.parametrize('mutate', [
    lambda t: t['m_RD']['uvTransform'].update(y=3),
    lambda t: t['m_RD']['textureRect'].update(x=-1),
    lambda t: t['m_RD']['textureRectOffset'].update(y=0),
    lambda t: t['m_RD'].update(settingsRaw=65),
    lambda t: t['m_RD']['alphaTexture'].update(m_PathID=1),
    lambda t: t['m_SpriteAtlas'].update(m_PathID=1),
    lambda t: t.update(m_PixelsToUnits=0),
])
def test_ambiguous_sprite_metadata_refused(mutate):
    tree = fixture()
    mutate(tree)
    with pytest.raises(ValueError):
        helper()(tree, (4, 4), [[-1, -1], [1, -1], [0, 1]], [0, 1, 2],
                 sprite_id=20, appearance_id=30)
