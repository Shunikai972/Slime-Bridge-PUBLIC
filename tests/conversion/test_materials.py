"""Semantic material conversion: native -> neutral -> target plan, never guessing native names."""
import copy
import unittest

from bridge.content.canonical import sha256_text
from bridge.conversion import materials as m

SOURCE = {'schema': 'bridge-material-profile', 'schema_version': 1, 'realm': 'SR2', 'synthetic': True,
          'shaders': {'Fixture/SlimeBody': {
              'family': 'slime_body',
              'render_state': {'alpha_mode': 'BLEND', 'cull': 'BACK', 'zwrite': False},
              'properties': {'_BodyTex': {'semantic': 'albedo.texture', 'required': True},
                             '_TopColor': {'semantic': 'gradient.top', 'required': False},
                             '_Mask': {'semantic': 'mask.texture', 'required': False, 'uv_set': 1},
                             '_Glow': {'semantic': 'emission.color', 'required': False}}}}}
TARGET = {'schema': 'bridge-target-material-profile', 'schema_version': 1, 'realm': 'SR1', 'synthetic': True,
          'templates': [
              {'id': 'opaque', 'shader': 'Fixture/Opaque', 'families': ['*'], 'alpha_modes': ['OPAQUE'],
               'uv_sets': [0], 'supports': {'albedo.texture': {'property': '_MainTex', 'required': True}}},
              {'id': 'slime', 'shader': 'Fixture/TargetSlime', 'families': ['slime_body'],
               'alpha_modes': ['BLEND', 'OPAQUE'], 'uv_sets': [0, 1],
               'supports': {'albedo.texture': {'property': '_Albedo', 'required': True},
                            'gradient.top': {'property': '_GradTop', 'required': False},
                            'mask.texture': {'property': '_MaskMap', 'required': False},
                            'translucency.strength': {'property': '_Translucency', 'required': True,
                                                      'default': 0.5}}}]}
TEX = 'SR2:Tex.Body'


def native(**override):
    desc = {'shader': 'Fixture/SlimeBody', 'properties': [
        {'name': '_BodyTex', 'type': 'texture', 'value': TEX},
        {'name': '_TopColor', 'type': 'color', 'value': [1.0, 0.5, 0.5, 1.0]},
        {'name': '_Mask', 'type': 'texture', 'value': 'SR2:Tex.Mask'},
        {'name': '_Glow', 'type': 'color', 'value': [0, 0, 0, 1]},
        {'name': '_Wobble', 'type': 'float', 'value': 0.2}]}
    desc.update(override)
    return desc


RESOLVED = {TEX: sha256_text('body'), 'SR2:Tex.Mask': sha256_text('mask')}


def codes(diags):
    return {d['code'] for d in diags}


class MaterialTests(unittest.TestCase):
    def test_neutralize_maps_known_and_preserves_unknown(self):
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, RESOLVED)
        self.assertEqual(sem['family'], 'slime_body')
        self.assertEqual(sem['channels']['albedo.texture']['texture'], TEX)
        self.assertEqual(sem['channels']['mask.texture']['uv_set'], 1)
        self.assertEqual(sem['render_state']['alpha_mode'], 'BLEND')
        self.assertEqual([p['name'] for p in sem['unknown_properties']], ['_Wobble'])
        self.assertIn('UNKNOWN_MATERIAL_PROPERTY', codes(sem['diagnostics']))
        self.assertEqual(sem['status'], 'DEGRADED')
        self.assertEqual(m.neutralize('SR2:Mat.Body', dict(reversed(list(native().items()))), SOURCE, RESOLVED),
                         sem)

    def test_unknown_shader_is_pending_adapter_not_guessed(self):
        sem = m.neutralize('SR2:Mat.X', native(shader='Mystery/Shader'), SOURCE, RESOLVED)
        self.assertEqual(sem['status'], 'PENDING_ADAPTER')
        self.assertEqual(sem['channels'], {})
        self.assertEqual(len(sem['unknown_properties']), 5)

    def test_missing_required_source_semantic_quarantines(self):
        desc = native(properties=[p for p in native()['properties'] if p['name'] != '_BodyTex'])
        self.assertEqual(m.neutralize('SR2:Mat.Body', desc, SOURCE, RESOLVED)['status'], 'QUARANTINED')
        unresolved = {'SR2:Tex.Mask': RESOLVED['SR2:Tex.Mask']}
        self.assertEqual(m.neutralize('SR2:Mat.Body', native(), SOURCE, unresolved)['status'], 'QUARANTINED')

    def test_missing_optional_texture_degrades(self):
        unresolved = {TEX: RESOLVED[TEX]}
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, unresolved)
        self.assertEqual(sem['status'], 'DEGRADED')
        self.assertNotIn('mask.texture', sem['channels'])
        self.assertIn('OPTIONAL_TEXTURE_UNRESOLVED', codes(sem['diagnostics']))

    def test_bad_value_types(self):
        desc = native()
        desc['properties'][1]['value'] = 'pink'
        sem = m.neutralize('SR2:Mat.Body', desc, SOURCE, RESOLVED)
        self.assertIn('INVALID_PROPERTY_VALUE', codes(sem['diagnostics']))
        self.assertNotIn('gradient.top', sem['channels'])
        desc['properties'][0]['type'] = 'float'
        self.assertEqual(m.neutralize('SR2:Mat.Body', desc, SOURCE, RESOLVED)['status'], 'QUARANTINED')

    def test_target_plan_picks_best_template_deterministically(self):
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, RESOLVED)
        plan = m.plan_target_material(sem, TARGET)
        self.assertEqual(plan['template'], 'slime')
        props = {a['property']: a for a in plan['assignments']}
        self.assertEqual(props['_Albedo']['texture'], TEX)
        self.assertEqual(props['_Translucency']['value'], 0.5)
        self.assertEqual(props['_Translucency']['semantic_source'], 'template-default')
        self.assertEqual(plan['dropped'], ['emission.color'])
        self.assertEqual(plan['status'], 'DEGRADED')
        shuffled = copy.deepcopy(TARGET)
        shuffled['templates'].reverse()
        self.assertEqual(m.plan_target_material(sem, shuffled)['template'], 'slime')

    def test_target_cannot_express_transparency_or_core(self):
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, RESOLVED)
        opaque_only = {**TARGET, 'templates': [TARGET['templates'][0]]}
        plan = m.plan_target_material(sem, opaque_only)
        self.assertEqual(plan['status'], 'PENDING_ADAPTER')
        self.assertIn('NO_COMPATIBLE_TEMPLATE', codes(plan['diagnostics']))
        no_albedo = copy.deepcopy(TARGET)
        del no_albedo['templates'][1]['supports']['albedo.texture']
        self.assertEqual(m.plan_target_material(sem, no_albedo)['status'], 'PENDING_ADAPTER')

    def test_uv_set_unsupported_degrades_optional_channel(self):
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, RESOLVED)
        target = copy.deepcopy(TARGET)
        target['templates'][1]['uv_sets'] = [0]
        plan = m.plan_target_material(sem, target)
        self.assertIn('mask.texture', plan['dropped'])
        self.assertIn('UV_SET_UNSUPPORTED', codes(plan['diagnostics']))

    def test_profile_entry_hashes_scope_invalidation(self):
        sem = m.neutralize('SR2:Mat.Body', native(), SOURCE, RESOLVED)
        other = copy.deepcopy(SOURCE)
        other['shaders']['Unrelated/Shader'] = {'family': 'x', 'properties': {}, 'render_state': {}}
        self.assertEqual(m.neutralize('SR2:Mat.Body', native(), other, RESOLVED)['profile_entry_hash'],
                         sem['profile_entry_hash'])
        changed = copy.deepcopy(SOURCE)
        changed['shaders']['Fixture/SlimeBody']['properties']['_Glow']['semantic'] = 'rim.color'
        self.assertNotEqual(m.neutralize('SR2:Mat.Body', native(), changed, RESOLVED)['profile_entry_hash'],
                            sem['profile_entry_hash'])

    def test_profiles_are_validated(self):
        with self.assertRaises(ValueError):
            m.validate_source_profile({**SOURCE, 'schema_version': 9})
        bad = copy.deepcopy(SOURCE)
        bad['shaders']['Fixture/SlimeBody']['properties']['_X'] = {'semantic': 'albedo.sparkle', 'required': False}
        with self.assertRaises(ValueError):
            m.validate_source_profile(bad)
        bad = copy.deepcopy(TARGET)
        bad['templates'].append(copy.deepcopy(bad['templates'][0]))
        with self.assertRaises(ValueError):
            m.validate_target_profile(bad)


if __name__ == '__main__':
    unittest.main()
