"""Launcher services for species parity and foreign spawn: settings v3, reports, plans.

Synthetic fixtures mirror the capture shapes; nothing here reads the LAB. Pins the v2->v3
additive settings migration, the fail-closed settings -> policy bridge, the validated parity
report and the read-only dev-spawn plan (which never executes anything natively).
"""
import copy
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from bridge.app import settings as ST
from bridge.app import species as SP
from bridge.conversion import foreign_spawn
from bridge.conversion.species_adapter import build_adapter
from bridge.conversion.species_parity import build_parity_matrix

H = 'a' * 64


def _base(ref, display, produces, numeric_id=None):
    return {'native_ref': ref, 'numeric_id': numeric_id, 'role': 'base', 'definition_hash': H,
            'display_name': display, 'produces': produces, 'quarantine': None, 'malformed': False}


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


def matrix_and_adapters():
    classification, snapshots = fixture()
    adapters = [build_adapter(classification, 'SR1', 'SR2', 'slime.rad', snapshots['SR1']),
                build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshots['SR2'])]
    matrix = build_parity_matrix(classification, snapshots, adapters)
    return classification, adapters, matrix


class SpeciesSettingsTests(unittest.TestCase):
    def test_v2_settings_migrate_additively_to_the_latest_version(self):
        old = {'schema': ST.SETTINGS_SCHEMA, 'schema_version': 2,
               'ui': {'language': 'fr'}, 'transitions': {'mode': 'COLD'}}
        settings, diags = ST.normalize(old)
        self.assertEqual(settings['schema_version'], ST.VERSION)
        self.assertEqual(settings['species'], ST.DEFAULTS['species'])
        self.assertEqual(settings['ui']['language'], 'fr')
        self.assertEqual(settings['transitions']['mode'], 'COLD')
        self.assertEqual([d['code'] for d in diags], ['SETTINGS_MIGRATED'] * (ST.VERSION - 2))
        self.assertEqual(settings['bridge'], ST.DEFAULTS['bridge'])
        chained, _ = ST.normalize({'schema': ST.SETTINGS_SCHEMA, 'schema_version': 1,
                                   'warm_mode': False, 'overlay': False})
        self.assertEqual(chained['schema_version'], ST.VERSION)
        self.assertEqual(chained['species'], ST.DEFAULTS['species'])

    def test_species_section_validates_and_updates(self):
        settings, diags = ST.normalize(ST.DEFAULTS)
        self.assertEqual(diags, [])
        changed = ST.update(settings, 'species', {'foreign_spawn': {
            'mode': 'ALL_IMPORTED', 'overrides': {'slime.ringtail': True}}})
        self.assertEqual(changed['species']['foreign_spawn']['mode'], 'ALL_IMPORTED')
        for bad in ({'foreign_spawn': {'mode': 'SOMETIMES', 'overrides': {}}},
                    {'foreign_spawn': {'mode': 'NATIVE_ONLY', 'overrides': {'ringtail': True}}},
                    {'foreign_spawn': {'mode': 'NATIVE_ONLY', 'overrides': {'slime.ringtail': 'yes'}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ST.update(settings, 'species', bad)

    def test_foreign_spawn_policy_bridge(self):
        imported = ['slime.rad', 'slime.ringtail']
        policy, diags = SP.foreign_spawn_policy({}, imported_ids=imported)
        self.assertEqual(policy, foreign_spawn.default_policy())
        self.assertEqual(diags, [])
        section = {'foreign_spawn': {'mode': 'ALL_IMPORTED',
                                     'overrides': {'slime.rad': False, 'slime.bogus': True}}}
        policy, diags = SP.foreign_spawn_policy(section, imported_ids=imported)
        self.assertEqual(policy['mode'], 'ALL_IMPORTED')
        self.assertEqual(policy['overrides'], {'slime.rad': False})
        self.assertIn('FOREIGN_SPAWN_OVERRIDE_UNKNOWN', {d['code'] for d in diags})
        self.assertTrue(all(d['subsystem'] == 'launcher' for d in diags))
        back = SP.species_section_from_policy(policy)
        self.assertEqual(back, {'foreign_spawn': {'mode': 'ALL_IMPORTED',
                                                  'overrides': {'slime.rad': False}}})
        effective = SP.foreign_spawn_effective(policy, imported_ids=imported)
        self.assertIs(effective['species']['slime.ringtail']['requested_natural_spawn_foreign'], True)
        self.assertIs(effective['species']['slime.ringtail']['active_natural_spawn_foreign'], False)

    def test_parity_report_validates_and_summarizes(self):
        _, _, matrix = matrix_and_adapters()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'parity.json'
            path.write_text(json.dumps(matrix), encoding='utf-8')
            report, diags = SP.parity_report(path)
            self.assertEqual(report['roster']['counts']['sr1_only'], 1)
            self.assertEqual(report['roster']['counts']['sr2_only'], 1)
            self.assertIn('Q034', report['blocked_queries'])
            rows = {row['canonical_id']: row for row in report['species']}
            self.assertEqual(rows['slime.pink']['import'], 'NOT_REQUIRED_NATIVE_BOTH_REALMS')
            self.assertEqual(rows['slime.rad']['native_realm'], 'SR1')
            self.assertEqual(rows['slime.rad']['target_realm'], 'SR2')
            self.assertIn('FOREIGN_NATURAL_SPAWN_NOT_IMPLEMENTED', rows['slime.rad']['blockers'])
            self.assertIs(rows['slime.rad']['foreign_world_spawn']['active'], False)
            codes = {d['code'] for d in diags}
            self.assertEqual(codes, {'SPECIES_PARITY_READY', 'SPECIES_RUNTIME_UNPROVEN'})
            tampered = copy.deepcopy(matrix)
            tampered['collisions'].append('x')
            path.write_text(json.dumps(tampered), encoding='utf-8')
            report, diags = SP.parity_report(path)
            self.assertIsNone(report)
            self.assertEqual([d['code'] for d in diags], ['SPECIES_PARITY_INVALID'])
            # SP-001 regression: a null presence must fail closed through diagnostics, not raise.
            nulled = copy.deepcopy(matrix)
            nulled['species'][0]['presence'] = None
            path.write_text(json.dumps(nulled), encoding='utf-8')
            report, diags = SP.parity_report(path)
            self.assertIsNone(report)
            self.assertEqual([d['code'] for d in diags], ['SPECIES_PARITY_INVALID'])
            report, diags = SP.parity_report(Path(tmp) / 'missing.json')
            self.assertIsNone(report)
            self.assertEqual([d['code'] for d in diags], ['SPECIES_PARITY_UNREADABLE'])

    def test_parity_report_survives_a_validator_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'parity.json'
            path.write_text(json.dumps({'x': 1}), encoding='utf-8')
            with mock.patch.object(SP.species_parity, 'validate_parity', side_effect=RuntimeError('boom')):
                report, diags = SP.parity_report(path)
            self.assertIsNone(report)
            self.assertEqual([d['code'] for d in diags], ['SPECIES_PARITY_VALIDATION_ERROR'])

    def test_dev_spawn_plan_is_read_only_and_fail_closed(self):
        classification, adapters, _ = matrix_and_adapters()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'classification.json').write_text(json.dumps(classification), encoding='utf-8')
            adapters_dir = root / 'adapters'
            adapters_dir.mkdir()
            for doc in adapters:
                (adapters_dir / f'{doc["adapter_id"]}.json').write_text(json.dumps(doc), encoding='utf-8')
            install = {'realm': 'SR1', 'root': 'D:/lab/SR1', 'lab': True, 'build_fingerprint': H}
            document, diags = SP.dev_spawn_plan({'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'},
                                                classification_path=root / 'classification.json',
                                                adapters_dir=adapters_dir, install=install)
            self.assertEqual(document['decision'], 'AUTHORIZED')
            self.assertEqual(document['execution']['state'], 'BLOCKED_ON_LOCAL_QUERY')
            self.assertEqual([d['code'] for d in diags], ['DEV_SPAWN_AUTHORIZED'])
            document, diags = SP.dev_spawn_plan({'canonical_id': 'slime.pink', 'target_realm': 'SR2'},
                                                classification_path=root / 'classification.json',
                                                adapters_dir=adapters_dir, install=install)
            self.assertEqual(document['decision'], 'REFUSED')
            self.assertEqual([d['code'] for d in diags], ['DEV_SPAWN_REFUSED'])
            empty = root / 'empty'
            empty.mkdir()
            document, diags = SP.dev_spawn_plan({'canonical_id': 'slime.rad', 'target_realm': 'SR2'},
                                                classification_path=root / 'classification.json',
                                                adapters_dir=empty, install=install)
            self.assertIsNone(document)
            self.assertEqual([d['code'] for d in diags], ['DEV_SPAWN_ADAPTERS_MISSING'])


if __name__ == '__main__':
    unittest.main()
