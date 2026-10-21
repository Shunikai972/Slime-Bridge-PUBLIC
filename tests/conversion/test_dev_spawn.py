"""Unit coverage for the LAB-only dev-spawn authorization (bridge-dev-spawn).

Pins: authorization gates, the refusal vocabulary (unknown / unregistered / native collisions /
non-LAB / fingerprint), deterministic receipts, the check sequence staying complete for refusals,
and the runtime truth that authorization never claims native execution (BLOCKED_ON_LOCAL_QUERY Q033).
"""
import copy
import unittest

from bridge.conversion.dev_spawn import (CHECK_NAMES, DEV_SPAWN_QUERY, DEV_SPAWN_SCHEMA, MODE,
                                         PURPOSE, plan_dev_spawn, validate_dev_spawn)
from bridge.conversion.foreign_spawn import default_policy
from bridge.conversion.species_adapter import build_adapter

H = 'a' * 64


def _base(ref, display, produces, numeric_id=None, role='base', quarantine=None):
    return {'native_ref': ref, 'numeric_id': numeric_id, 'role': role, 'definition_hash': H,
            'display_name': display, 'produces': produces, 'quarantine': quarantine,
            'malformed': False}


def fixture():
    pink = {'canonical_id': 'slime.pink', 'classification': 'SHARED_NATIVE', 'reasons': [],
            'present': {'SR1': 1, 'SR2': 1},
            'native': {'SR1': _base('PINK_SLIME', 'Pink', ['plort.pink'], numeric_id=4),
                       'SR2': _base('SlimeDefinition.Pink', 'Pink', ['plort.pink'])},
            'identity_basis': {'SR1': 'DERIVED', 'SR2': 'DERIVED'},
            'identity_evidence': {'SR1': ['slug-v1:PINK_SLIME'], 'SR2': ['slug-v1:SlimeDefinition.Pink']}}
    rad = {'canonical_id': 'slime.rad', 'classification': 'SR1_ONLY', 'reasons': [],
           'present': {'SR1': 1, 'SR2': 0},
           'native': {'SR1': _base('RAD_SLIME', 'Rad', ['plort.rad'], numeric_id=1), 'SR2': None},
           'identity_basis': {'SR1': 'DERIVED'}, 'identity_evidence': {'SR1': ['slug-v1:RAD_SLIME']}}
    ringtail = {'canonical_id': 'slime.ringtail', 'classification': 'SR2_ONLY', 'reasons': [],
                'present': {'SR1': 0, 'SR2': 1},
                'native': {'SR1': None,
                           'SR2': _base('SlimeDefinition.Ringtail', 'Ringtail', ['plort.ringtail'])},
                'identity_basis': {'SR2': 'DERIVED'},
                'identity_evidence': {'SR2': ['slug-v1:SlimeDefinition.Ringtail']}}
    plorts = [
        {'canonical_id': 'plort.pink', 'classification': 'SHARED_NATIVE',
         'present': {'SR1': 1, 'SR2': 1},
         'native': {'SR1': _base('PINK_PLORT', 'Pink Plort', []),
                    'SR2': _base('IdentifiableType.PinkPlort', 'Pink Plort', [])}},
        {'canonical_id': 'plort.rad', 'classification': 'SR1_ONLY', 'present': {'SR1': 1, 'SR2': 0},
         'native': {'SR1': _base('RAD_PLORT', 'Rad Plort', []), 'SR2': None}},
        {'canonical_id': 'plort.ringtail', 'classification': 'SR2_ONLY', 'present': {'SR1': 0, 'SR2': 1},
         'native': {'SR1': None, 'SR2': _base('IdentifiableType.RingtailPlort', 'Ringtail Plort', [])}},
    ]
    classification = {'schema': 'bridge-classification', 'schema_version': 1,
                      'species': [pink, rad, ringtail], 'plorts': plorts,
                      'builds': {'SR1': H, 'SR2': 'b' * 64},
                      'data_evidence': {'SR1': 'RUNTIME_ANCHORED', 'SR2': 'RUNTIME_ANCHORED'},
                      'traversal': {'SR1': 'COMPLETE', 'SR2': 'COMPLETE'}}
    snapshots = {
        'SR1': {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': 'SR1',
                'entries': [{'native_ref': 'PINK_SLIME', 'display_name': 'Pink', 'role': 'base',
                             'definition_hash': H, 'numeric_id': 4, 'produces': ['PINK_PLORT']},
                            {'native_ref': 'RAD_SLIME', 'display_name': 'Rad', 'role': 'base',
                             'definition_hash': H, 'numeric_id': 1, 'produces': ['RAD_PLORT']}]},
        'SR2': {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': 'SR2',
                'definitions': [{'native_ref': 'SlimeDefinition.Pink', 'display_name': 'Pink',
                                 'role': 'base', 'definition_hash': H,
                                 'produces': ['IdentifiableType.PinkPlort']},
                                {'native_ref': 'SlimeDefinition.Ringtail', 'display_name': 'Ringtail',
                                 'role': 'base', 'definition_hash': H,
                                 'produces': ['IdentifiableType.RingtailPlort']}]}}
    return classification, snapshots


def build_inputs():
    classification, snapshots = fixture()
    adapters = [build_adapter(classification, 'SR1', 'SR2', 'slime.rad', snapshots['SR1']),
                build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshots['SR2'])]
    return classification, adapters


def install(realm='SR1', lab=True, fingerprint=H, root='D:/lab/'):
    return {'realm': realm, 'root': root, 'lab': lab, 'build_fingerprint': fingerprint}


class DevSpawnTests(unittest.TestCase):
    def test_authorized_request_keeps_native_execution_blocked(self):
        classification, adapters = build_inputs()
        doc = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                             classification, adapters, install('SR1'))
        self.assertEqual(validate_dev_spawn(doc), [])
        self.assertEqual(doc['schema'], DEV_SPAWN_SCHEMA)
        self.assertEqual(doc['mode'], MODE)
        self.assertEqual(doc['purpose'], PURPOSE)
        self.assertEqual(doc['decision'], 'AUTHORIZED')
        self.assertEqual(doc['refusals'], [])
        self.assertEqual([entry['check'] for entry in doc['checks']], list(CHECK_NAMES))
        self.assertTrue(all(entry['passed'] for entry in doc['checks']))
        self.assertEqual(doc['execution']['state'], 'BLOCKED_ON_LOCAL_QUERY')
        self.assertEqual(doc['execution']['local_query'], DEV_SPAWN_QUERY)
        receipt = doc['receipt']
        self.assertIs(receipt['test_only'], True)
        self.assertIs(receipt['one_shot'], True)
        self.assertIs(receipt['spawn_tables_modified'], False)
        self.assertEqual(doc['policy']['mode'], 'NATIVE_ONLY')
        self.assertIs(doc['policy']['active_natural_spawn_foreign'], False)

    def test_every_refusal_stays_a_valid_document(self):
        classification, adapters = build_inputs()
        cases = [
            ({'canonical_id': 'slime.bogus', 'target_realm': 'SR1'}, install('SR1'), 'DEV_SPAWN_UNKNOWN_SPECIES'),
            ({'canonical_id': 'slime.pink', 'target_realm': 'SR2'}, install('SR2', fingerprint='b' * 64),
             'DEV_SPAWN_NATIVE_COLLISION'),
            ({'canonical_id': 'slime.rad', 'target_realm': 'SR1'}, install('SR1'), 'DEV_SPAWN_NATIVE_REALM'),
            ({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'}, install('SR1', lab=False),
             'DEV_SPAWN_NOT_LAB_INSTALL'),
            ({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'}, install('SR1', fingerprint='c' * 64),
             'DEV_SPAWN_BUILD_MISMATCH'),
            ({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'}, install('SR2', fingerprint='b' * 64),
             'DEV_SPAWN_INSTALL_REALM_MISMATCH'),
            ({'canonical_id': None, 'target_realm': 'SR3'}, install('SR1'), 'DEV_SPAWN_INVALID_REQUEST'),
        ]
        for request, install_facts, code in cases:
            with self.subTest(code=code):
                doc = plan_dev_spawn(request, classification, adapters, install_facts)
                self.assertEqual(validate_dev_spawn(doc), [])
                self.assertEqual(doc['decision'], 'REFUSED')
                self.assertIn(code, {entry['code'] for entry in doc['refusals']})
                self.assertEqual(doc['execution'], {'state': 'NOT_ATTEMPTED'})

    def test_unregistered_species_is_refused(self):
        classification, adapters = build_inputs()
        adapters = [doc for doc in adapters if doc['adapter_id'] != 'adapter.sr2-to-sr1.slime.ringtail']
        doc = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                             classification, adapters, install('SR1'))
        self.assertEqual(doc['decision'], 'REFUSED')
        self.assertIn('DEV_SPAWN_UNREGISTERED_SPECIES', {entry['code'] for entry in doc['refusals']})

    def test_policy_document_is_recorded_but_never_activates(self):
        classification, adapters = build_inputs()
        policy = default_policy() | {'mode': 'ALL_IMPORTED'}
        doc = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                             classification, adapters, install('SR1'), policy)
        self.assertEqual(doc['decision'], 'AUTHORIZED')
        self.assertIs(doc['policy']['requested_natural_spawn_foreign'], True)
        self.assertIs(doc['policy']['active_natural_spawn_foreign'], False)
        self.assertEqual(doc['diagnostics'], [])
        invalid = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                                 classification, adapters, install('SR1'),
                                 default_policy() | {'mode': 'Always'})
        self.assertEqual(invalid['decision'], 'AUTHORIZED')
        self.assertIn('FOREIGN_SPAWN_MODE_INVALID', {d['code'] for d in invalid['diagnostics']})
        self.assertEqual(validate_dev_spawn(invalid), [])

    def test_decisions_are_deterministic(self):
        classification, adapters = build_inputs()
        first = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                               classification, adapters, install('SR1'))
        second = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                                classification, adapters, install('SR1'))
        self.assertEqual(first, second)

    def test_validator_catches_incoherent_decisions(self):
        classification, adapters = build_inputs()
        doc = plan_dev_spawn({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                             classification, adapters, install('SR1'))
        tampered = copy.deepcopy(doc)
        tampered['execution'] = {'state': 'RAN'}
        self.assertTrue(validate_dev_spawn(tampered))
        tampered = copy.deepcopy(doc)
        tampered['receipt']['spawn_tables_modified'] = True
        self.assertTrue(validate_dev_spawn(tampered))
        tampered = copy.deepcopy(doc)
        tampered['decision'] = 'REFUSED'
        self.assertTrue(validate_dev_spawn(tampered))
        tampered = copy.deepcopy(doc)
        tampered['checks'] = tampered['checks'][:-1]
        self.assertTrue(validate_dev_spawn(tampered))


if __name__ == '__main__':
    unittest.main()
