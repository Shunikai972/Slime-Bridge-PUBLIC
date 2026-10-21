import unittest

import numpy as np

from sbconv.materials import bake
from sbconv.materials.translate import Translator, is_infra, convert_hdr, family


def mat(shader, floats=None, colors=None, kw=None, textures=None, name='m'):
    return {'ref': f'f#{name}', 'name': name, 'shader': shader, 'keywords': kw or [], 'floats': floats or {}, 'colors': colors or {},
            'textures': textures or {}, 'render_queue': -1}


def tpl(shaders):
    ex = {'ref': 'f#t', 'name': 'tmpl', 'keywords': [], 'floats': {}, 'colors': {}, 'texture_props': []}
    return {'game': 'x', 'normal_map_formats': {}, 'shaders': {
        k: {'count': 1, 'floats': v.get('floats', []), 'colors': v.get('colors', []), 'textures': v.get('textures', []),
            'keywords': v.get('keywords', []), 'examples': [ex]} for k, v in shaders.items()}}


class Rules(unittest.TestCase):
    def test_infrastructure_props_are_not_semantic(self):
        for n in ('_StencilRef', '_TessValue', '_DiffusionProfile', '_ZTestGBuffer', '_SrcBlend', '_DoubleSidedEnable', '_RenderQueueType', '_Label_Boom'):
            self.assertTrue(is_infra(n), n)
        for n in ('_Gloss', '_TopColor', '_RimPower', '_StripeUV1', '_EyeGlare'):
            self.assertFalse(is_infra(n), n)

    def test_family_classification(self):
        self.assertEqual(family(mat('SR/AMP/Slime/Face/Eyes Glow')), 'face')
        self.assertEqual(family(mat('SR/AMP/Slime/Body')), 'body')
        self.assertEqual(family(mat('SR/AMP/Slime/Body/Rad Stripe')), 'body')
        self.assertEqual(family(mat('SR/Null Render')), 'null')
        self.assertEqual(family(mat('SR/AMP/FX/RadAura')), 'fx')

    def test_hdr_conversion_round_trip_and_identity(self):
        c = [2.0, 1.0, 0.25, 1.0]
        self.assertEqual(convert_hdr(c, 'SR1', 'SR1'), c)
        down = convert_hdr(c, 'SR2', 'SR1')
        back = convert_hdr(down, 'SR1', 'SR2')
        self.assertTrue(np.allclose(back[:3], c[:3], atol=1e-6))
        self.assertEqual(down[3], 1.0)
        self.assertGreater(down[2], c[2])          # linear 0.25 re-encoded for a gamma project is brighter


class FaceTranslation(unittest.TestCase):
    def setUp(self):
        self.tpl = tpl({'SR/AMP/Slime/Face/Eyes Default': {'floats': ['_EyeGlare', '_Cutoff'], 'colors': ['_EyeRed', '_Color'], 'textures': ['_FaceAtlas']},
                        'SR/AMP/Slime/Face/Mouth Default': {'floats': ['_Cutoff'], 'colors': ['_MouthTop'], 'textures': ['_FaceAtlas']}})
        self.tex = {'f#t1': {'format': 'RGBA32', 'srgb': True, 'width': 64, 'height': 64}}

    def tr(self, src, tgt):
        return Translator(src, tgt, self.tpl, self.tex, None, None)

    def test_same_shader_direct_copy_drops_infrastructure_only(self):
        m = mat('SR/AMP/Slime/Face/Eyes Default', floats={'_EyeGlare': 0.2, '_Cutoff': 0.5, '_StencilRef': 4.0, '_TessValue': 16.0},
                colors={'_EyeRed': [0.1, 0.0, 0.0, 1.0], '_DiffusionProfile_Asset': [0, 0, 0, 0]},
                textures={'_FaceAtlas': {'texture': 'f#t1', 'file': 'textures/a.png', 'scale': [1, 1], 'offset': [0, 0]}})
        r = self.tr('SR2', 'SR1').face(m)
        self.assertEqual(r['fidelity']['level'], 'exact')
        self.assertEqual(r['target']['floats'], {'_EyeGlare': 0.2, '_Cutoff': 0.5})
        self.assertEqual(r['target']['textures']['_FaceAtlas']['file'], 'textures/a.png')
        self.assertEqual(r['dropped']['infrastructure'], 3)

    def test_unknown_semantic_property_lowers_fidelity_and_is_reported(self):
        m = mat('SR/AMP/Slime/Face/Eyes Default', floats={'_Cutoff': 0.5, '_Mystery': 1.0})
        r = self.tr('SR2', 'SR1').face(m)
        self.assertEqual(r['fidelity']['level'], 'equivalent')
        self.assertIn('_Mystery', r['dropped']['floats'])

    def test_sleep_mouth_falls_back_with_note(self):
        r = self.tr('SR2', 'SR1').face(mat('SR/AMP/Slime/Face/Mouth Sleep'))
        self.assertEqual(r['target']['shader'], 'SR/AMP/Slime/Face/Mouth Default')
        self.assertEqual(r['fidelity']['level'], 'approximate')


class BodyTranslation(unittest.TestCase):
    def test_sr1_shader_selection_prefers_exact_then_degrades_with_notes(self):
        t = Translator('SR2', 'SR1', tpl({}), {}, None, None)
        self.assertEqual(t._sr1_shader_for({'stripes'}), ('SR/AMP/Slime/Body/Stripe', []))
        self.assertEqual(t._sr1_shader_for(set()), ('SR/AMP/Slime/Body/Default', []))
        sh, lost = t._sr1_shader_for({'stripes', 'normals', 'boom'})
        self.assertTrue(lost or sh.endswith('Normal Stripe'))

    def test_sr1_to_sr2_enables_uber_features(self):
        t = Translator('SR1', 'SR2', tpl({'SR/AMP/Slime/Body': {'floats': ['_EnableStripes', '_Gloss'], 'colors': ['_TopColor', '_MiddleColor', '_BottomColor'],
                                                                  'textures': ['_StripeTexture']}}), {}, None, None)
        m = mat('SR/AMP/Slime/Body/Stripe', floats={'_Gloss': 1.2, '_Outline': 0.005},
                colors={'_TopColor': [1, 0, 0, 1], '_MiddleColor': [0, 1, 0, 1], '_BottomColor': [0, 0, 1, 1]})
        r = t.body(m)
        self.assertEqual(r['target']['shader'], 'SR/AMP/Slime/Body')
        self.assertEqual(r['target']['floats'].get('_EnableStripes'), 1.0)
        self.assertIn('_ENABLESTRIPES_ON', r['target']['keywords'])
        self.assertEqual(r['target']['colors']['_TopColor'], [1, 0, 0, 1])


class SpecialShaders(unittest.TestCase):
    def test_amp_prefix_alias_between_games(self):
        t = Translator('SR1', 'SR2', tpl({'SR/AMP/Slime/Mosaic': {'floats': ['_Gloss'], 'colors': ['_Color'], 'textures': ['_PrimaryTex']}}), {}, None, None)
        m = mat('SR/Slime/Mosaic', floats={'_Gloss': 0.5})
        self.assertEqual(family(m), 'special')
        r = t.translate(m)
        self.assertEqual((r['plan'], r['target']['shader']), ('special_same_shader', 'SR/AMP/Slime/Mosaic'))
        self.assertTrue(any('is named' in n for n in r['fidelity']['notes']))

    def test_missing_target_shader_is_unsupported_not_silent(self):
        t = Translator('SR1', 'SR2', tpl({}), {}, None, None)
        r = t.translate(mat('SR/Slime/Face Tarr Interior'))
        self.assertEqual(r['fidelity']['level'], 'unsupported')


class Bake(unittest.TestCase):
    def quad(self):
        uv = np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], dtype=np.float32)
        return {'positions': np.zeros((4, 3), np.float32), 'uv0': np.array([[0.5, 0.0], [0.5, 0.0], [0.5, 1.0], [0.5, 1.0]], np.float32), 'uv1': uv,
                'tris': [np.array([[0, 1, 2], [0, 2, 3]])]}

    def material(self, contribution):
        return mat('SR/AMP/Slime/Body', floats={'_ColorMaskUV1': 1.0, '_LightingUVContribution': contribution, '_LightingUVHorizontalAdjust': 0.0},
                   kw=['_BODYCOLORING_MULTI'],
                   colors={'_TopColor': [1, 1, 1, 1], '_MiddleColor': [0.5, 0.5, 0.5, 1], '_BottomColor': [0, 0, 0, 1],
                           '_RedTopColor': [1, 0, 0, 1], '_RedMiddleColor': [1, 0, 0, 1], '_RedBottomColor': [1, 0, 0, 1],
                           '_GreenTopColor': [0, 1, 0, 1], '_GreenMiddleColor': [0, 1, 0, 1], '_GreenBottomColor': [0, 1, 0, 1],
                           '_BlueTopColor': [0, 0, 1, 1], '_BlueMiddleColor': [0, 0, 1, 1], '_BlueBottomColor': [0, 0, 1, 1]})

    def test_zero_contribution_is_flat_middle_colour(self):
        mask = np.zeros((64, 64, 4), np.float32)
        rgba, rep = bake.bake_multi(self.material(0.0), [self.quad()], mask)
        px = rgba[32, 32, :3]
        self.assertTrue(np.all(np.abs(px.astype(int) - 128) <= 1), px)
        self.assertGreater(rep['coverage'], 0.5)

    def test_mask_channels_select_regional_palettes(self):
        mask = np.zeros((64, 64, 4), np.float32)
        mask[:, :32, 0] = 1.0
        mask[:, 32:, 2] = 1.0
        rgba, _ = bake.bake_multi(self.material(0.0), [self.quad()], mask)
        left, right = rgba[32, 16, :3], rgba[32, 48, :3]
        self.assertTrue(left[0] > 250 and left[1] < 5 and left[2] < 5, left)
        self.assertTrue(right[2] > 250 and right[0] < 5, right)

    def test_vertical_gradient_when_lighting_uv_contributes(self):
        mask = np.zeros((64, 64, 4), np.float32)
        rgba, _ = bake.bake_multi(self.material(1.0), [self.quad()], mask)
        self.assertNotEqual(int(rgba[10, 32, :3].astype(int).sum()), int(rgba[54, 32, :3].astype(int).sum()))

    def test_bake_is_deterministic(self):
        mask = np.random.RandomState(1).rand(64, 64, 4).astype(np.float32)
        a, _ = bake.bake_multi(self.material(0.5), [self.quad()], mask)
        b, _ = bake.bake_multi(self.material(0.5), [self.quad()], mask)
        self.assertTrue(np.array_equal(a, b))


if __name__ == '__main__':
    unittest.main()


class EdgeCases(unittest.TestCase):
    def test_material_without_shader_is_reported_not_crashing(self):
        t = Translator('SR2', 'SR1', tpl({}), {}, None, None)
        r = t.translate(mat(None))
        self.assertEqual((r['fidelity']['level'], r['plan']), ('unsupported', 'unsupported'))
        self.assertIn('no shader reference', r['fidelity']['notes'][0])

    def test_documented_fallback_is_used_with_a_note(self):
        t = Translator('SR1', 'SR2', tpl({'SR/Slime/Face Tarr': {'floats': ['_Cutoff'], 'colors': [], 'textures': ['_FaceTexture']}}), {}, None, None)
        r = t.translate(mat('SR/Slime/Face Tarr Interior', floats={'_Cutoff': 0.5}))
        self.assertEqual(r['target']['shader'], 'SR/Slime/Face Tarr')
        self.assertTrue(any('fallback' in n for n in r['fidelity']['notes']))


class WrapBake(unittest.TestCase):
    def test_tiled_uv_layout_is_baked_instead_of_dropped(self):
        from sbconv.materials.raster import rasterize_uv
        uv = np.array([[-1.0, 0.1], [-0.2, 0.1], [-0.2, 0.9], [-1.0, 0.9]], dtype=np.float32)    # entirely outside [0,1]
        tris = np.array([[0, 1, 2], [0, 2, 3]])
        attrs = np.ones((4, 1), np.float32)
        _, cov_nowrap = rasterize_uv(uv, tris, attrs, (32, 32), wrap=False)
        _, cov_wrap = rasterize_uv(uv, tris, attrs, (32, 32), wrap=True)
        self.assertEqual(int(cov_nowrap.sum()), 0)
        self.assertGreater(int(cov_wrap.sum()), 100)


class MissingSecondUv(unittest.TestCase):
    def test_mesh_without_uv1_bakes_with_uv0(self):
        uv0 = np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]], np.float32)
        me = {'positions': np.zeros((4, 3), np.float32), 'uv0': uv0, 'uv1': None, 'tris': [np.array([[0, 1, 2], [0, 2, 3]])]}
        m = mat('SR/AMP/Slime/Body', floats={'_ColorMaskUV1': 1.0, '_LightingUVContribution': 0.0}, kw=['_BODYCOLORING_MULTI'],
                colors={'_MiddleColor': [0.5, 0.5, 0.5, 1]})
        rgba, rep = bake.bake_multi(m, [me], np.zeros((32, 32, 4), np.float32))
        self.assertEqual(rep['mask_uv'], 'uv1')
        self.assertGreater(rep['coverage'], 0.3)


class SliverUv(unittest.TestCase):
    def test_collapsed_uv_strip_is_baked_from_its_edges(self):
        from sbconv.materials.raster import rasterize_uv
        uv = np.array([[1.00001, 0.1], [0.99998, 0.1], [1.00002, 0.9], [0.99999, 0.9]], dtype=np.float32)   # one-coordinate mapping
        tris = np.array([[0, 1, 2], [2, 3, 0]])
        attrs = np.array([[0.1], [0.1], [0.9], [0.9]], np.float32)
        img, cov = rasterize_uv(uv, tris, attrs, (64, 64), flip_v=False)
        self.assertGreater(int(cov.sum()), 40)
        self.assertLess(float(img[cov].min()), 0.2)
        self.assertGreater(float(img[cov].max()), 0.8)
