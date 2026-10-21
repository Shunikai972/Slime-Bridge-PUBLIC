import unittest

from bridge.app import settings as ST
from bridge.sanctuary import layout as L
from tests.sanctuary.fixtures import copy_kit


class BridgeSettingsTests(unittest.TestCase):
    def test_defaults_follow_the_project_brief(self):
        bridge = ST.DEFAULTS['bridge']
        self.assertEqual(bridge['sr1_bridge_access_mode'], 'ENDGAME')
        self.assertEqual(bridge['sr1_gold_plort_gate_cost'], 10)
        self.assertEqual(bridge['sr2_portal_cost'], 250000)
        self.assertTrue(bridge['sr2_portal_gate_enabled'])
        self.assertEqual(bridge['currency_mode'], 'SEPARATE')
        self.assertEqual(bridge['transition_mode'], 'AUTO')
        self.assertFalse(bridge['dynamic_statue_requirements'])

    def test_updates_validate_and_reach_the_layout(self):
        settings, _ = ST.normalize(ST.DEFAULTS)
        updated = ST.update(settings, 'bridge', {'sr1_bridge_access_mode': 'ALWAYS', 'sr1_gold_plort_gate_cost': 25})
        layout = L.build_layout(copy_kit(), L.config_from_settings(updated))
        self.assertFalse(layout['gate']['enabled'])
        self.assertEqual(layout['gate']['cost'], 25)

    def test_bad_values_are_refused(self):
        settings, _ = ST.normalize(ST.DEFAULTS)
        for bad in ({'sr1_gold_plort_gate_cost': 0}, {'sr1_bridge_access_mode': 'NEVER'}, {'sr2_portal_cost': -1},
                    {'currency_mode': 'BARTER'}, {'transition_mode': 'INSTANT'}, {'cross_realm_chat': 'yes'}):
            with self.assertRaises(ValueError, msg=str(bad)):
                ST.update(settings, 'bridge', bad)

    def test_a_v3_document_gains_the_bridge_section_without_losing_anything(self):
        old = {'schema': ST.SETTINGS_SCHEMA, 'schema_version': 3, 'ui': {'language': 'fr'}}
        settings, diags = ST.normalize(old)
        self.assertEqual(settings['ui']['language'], 'fr')
        self.assertEqual(settings['bridge'], ST.DEFAULTS['bridge'])
        self.assertEqual(settings['schema_version'], ST.VERSION)
        self.assertEqual([d['code'] for d in diags], ['SETTINGS_MIGRATED'])


if __name__ == '__main__':
    unittest.main()
