from bridge.content.sr2_asset_extract import albedo_uv_set

ATLAS = [[0.1, 0.1], [0.9, 0.2], [0.5, 0.9]]
GEOMETRY = {'vertices': [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 'uv2': ATLAS}
BODY = {'shader': 'SR/AMP/Slime/Body'}


def with_mask(material):
    return dict(material, textures=[{'property': '_StripeTextureBaked', 'file': 'mask.png'}])


def test_body_without_baked_markings_keeps_uv0_gradient():
    # SR1's body shader takes the top/middle/bottom gradient from UV0; the atlas set splits the colors left/middle/right.
    assert albedo_uv_set('body', GEOMETRY, [BODY]) == 'uv'
    assert albedo_uv_set('body', GEOMETRY, [dict(BODY, textures=[{'property': '_Noise', 'file': 'n.png'}])]) == 'uv'


def test_body_with_baked_markings_samples_the_atlas():
    assert albedo_uv_set('body', GEOMETRY, [with_mask(BODY)]) == 'uv2'


def test_face_and_missing_atlas_keep_uv0():
    assert albedo_uv_set('face', GEOMETRY, [with_mask(BODY)]) == 'uv'
    assert albedo_uv_set('body', dict(GEOMETRY, uv2=[]), [with_mask(BODY)]) == 'uv'
