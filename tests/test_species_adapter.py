"""Unit coverage for the cross-game species adapter builder (no games, no LAB).

Builds descriptors from synthetic classification/snapshot documents that mirror the real capture
shapes (SR2 ``definitions`` rows, SR1 ``entries`` rows), then pins schema rules, fail-closed
behavior, determinism and the VISUAL_ASSET_BLOCKED marker.
"""
import copy
import unittest

from bridge.conversion.species_adapter import (ADAPTER_SCHEMA, ADAPTER_SCHEMA_VERSION, VISUAL_STATUS,
                                               adapter_id, build_adapter, validate_adapter)
from bridge.conversion.targets import REGISTRATION_OPS

H = 'a' * 64


def species_row(canonical_id='slime.ringtail', classification='SR2_ONLY', source='SR2',
                native_ref='SlimeDefinition.Ringtail', plort='plort.ringtail', produces=None,
                role='base', present=None, quarantine=None, numeric_id=None):
    native = {'native_ref': native_ref, 'numeric_id': numeric_id, 'role': role, 'definition_hash': H,
              'display_name': canonical_id.split('.', 1)[1].capitalize(),
              'produces': [plort] if produces is None else produces,
              'quarantine': quarantine, 'malformed': False}
    target = 'SR1' if source == 'SR2' else 'SR2'
    return {'canonical_id': canonical_id, 'classification': classification, 'reasons': [],
            'present': present or {source: 1, target: 0},
            'native': {source: native, target: None},
            'identity_basis': {source: 'DERIVED'},
            'identity_evidence': {source: [f'slug-v1:{native_ref}']}}


def classification_doc():
    return {'schema': 'bridge-classification', 'schema_version': 1,
            'species': [species_row()],
            'plorts': [{'canonical_id': 'plort.ringtail', 'classification': 'SR2_ONLY',
                        'present': {'SR1': 0, 'SR2': 1},
                        'native': {'SR1': None, 'SR2': {'native_ref': 'IdentifiableType.RingtailPlort',
                                                        'numeric_id': None, 'definition_hash': 'b' * 64,
                                                        'role': 'plort', 'produces': [],
                                                        'quarantine': None, 'malformed': False}}}]}


def snapshot_doc(realm='SR2', key='definitions'):
    row = {'object_ref': f'{realm}:SlimeDefinition:SlimeDefinition.Ringtail',
           'reference_id': 'SlimeDefinition.Ringtail', 'name': 'Ringtail', 'is_largo': False,
           'can_largofy': True, 'base_slimes': [],
           'produces': ['IdentifiableType.RingtailPlort'],
           'prefab_refs': ['SR2:Prefab:slimeRingtail'], 'evidence': ['runtime registry traversal']}
    return {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': realm, key: [row]}


class SpeciesAdapterTests(unittest.TestCase):
    def test_adapter_has_all_sections_and_visual_marker(self):
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(validate_adapter(doc), [])
        self.assertEqual(doc['schema'], ADAPTER_SCHEMA)
        self.assertEqual(doc['schema_version'], ADAPTER_SCHEMA_VERSION)
        self.assertEqual(doc['status'], 'EXPERIMENTAL')
        self.assertEqual(doc['adapter_id'], 'adapter.sr2-to-sr1.slime.ringtail')
        self.assertEqual(doc['visual']['status'], VISUAL_STATUS)
        self.assertEqual(doc['visual']['source_asset_refs'], ['SR2:Prefab:slimeRingtail'])
        self.assertEqual(doc['plort']['canonical_id'], 'plort.ringtail')
        self.assertFalse(doc['plort']['target_native_present'])
        self.assertEqual(doc['inventory']['items'],
                         [{'kind': 'slime', 'canonical_id': 'slime.ringtail'},
                          {'kind': 'plort', 'canonical_id': 'plort.ringtail'}])
        self.assertEqual(doc['diet']['status'], 'BLOCKED_ON_LOCAL_QUERY')
        self.assertTrue(doc['diet']['local_query'])

    def test_registration_plan_covers_every_operation_with_local_queries(self):
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        plan = doc['target_registration']
        self.assertEqual([row['operation'] for row in plan], list(REGISTRATION_OPS))
        for row in plan:
            self.assertIsNone(row['api'])
            self.assertTrue(row['local_query'])
            self.assertIn(row['section'],
                          {'identity', 'diet', 'plort', 'economy', 'spawn', 'save', 'visual'})

    def test_non_exclusive_species_is_quarantined(self):
        classification = classification_doc()
        classification['species'][0]['classification'] = 'SHARED_NATIVE'
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        codes = {d['code'] for d in doc['diagnostics']}
        self.assertIn('ADAPTER_NOT_EXCLUSIVE', codes)
        self.assertTrue(validate_adapter(doc))  # classification must be source-only

    def test_target_native_plort_is_refused(self):
        classification = classification_doc()
        classification['plorts'][0]['present']['SR1'] = 1
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_TARGET_ALREADY_NATIVE', {d['code'] for d in doc['diagnostics']})

    def test_species_present_in_target_is_refused_despite_classification(self):
        # SA-001: classification claims SR2_ONLY but the row itself marks the target present.
        classification = classification_doc()
        classification['species'][0]['present']['SR1'] = 1
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_TARGET_PRESENT', {d['code'] for d in doc['diagnostics']})
        self.assertTrue(validate_adapter(doc))

    def test_ambiguous_uncaptured_source_produces_is_quarantined(self):
        # SA-002: with no plort rows, the definition must name exactly one produced ref.
        classification = classification_doc()
        classification['plorts'] = []
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = ['IdentifiableType.A', 'IdentifiableType.B']
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_SOURCE_AMBIGUOUS', {d['code'] for d in doc['diagnostics']})
        self.assertTrue(validate_adapter(doc))

    def test_uncaptured_target_presence_is_not_absence(self):
        # SA-006: a missing/null target entry proves nothing; absence must be captured.
        classification = classification_doc()
        del classification['species'][0]['present']['SR1']
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_TARGET_PRESENCE_UNCAPTURED', {d['code'] for d in doc['diagnostics']})
        self.assertIsNone(doc['identity']['target']['native_present'])
        self.assertTrue(validate_adapter(doc))

    def test_uncaptured_plort_crosswalk_mismatch_is_quarantined(self):
        # SA-005: the canonical id and the definition-produced ref must identify the same plort.
        classification = classification_doc()
        classification['plorts'] = []
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = ['IdentifiableType.OtherItem']
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_CROSSWALK_MISMATCH', {d['code'] for d in doc['diagnostics']})
        self.assertTrue(validate_adapter(doc))

    def test_captured_plort_row_must_match_the_definition_produces(self):
        # SA-009: a captured plort row alone is not a crosswalk; the source definition must
        # name the same produced ref.
        classification = classification_doc()
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = ['IdentifiableType.OtherPlort']
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_CROSSWALK_MISMATCH', {d['code'] for d in doc['diagnostics']})
        self.assertTrue(validate_adapter(doc))

    def test_validator_rejects_absence_claim_without_classified_evidence(self):
        # SA-007: a false target presence requires CLASSIFIED evidence, never the pending marker.
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        tampered = copy.deepcopy(doc)
        tampered['plort']['target_native_present'] = False
        tampered['plort']['presence_evidence'] = 'PENDING_CAPTURE'
        self.assertTrue(any('CLASSIFIED' in p for p in validate_adapter(tampered)))

    def test_validator_requires_exact_presence_types(self):
        # SA-008: only true/false/null are valid; false-like values are never proven absence.
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        for bad in ('', 0, 'false', 1):
            tampered = copy.deepcopy(doc)
            tampered['plort']['target_native_present'] = bad
            tampered['plort']['presence_evidence'] = 'CLASSIFIED'
            problems = validate_adapter(tampered)
            self.assertTrue(any('true, false or null required' in p for p in problems), bad)
        tampered = copy.deepcopy(doc)
        tampered['plort']['presence_evidence'] = 'MAYBE'
        self.assertTrue(any('CLASSIFIED or PENDING_CAPTURE' in p for p in validate_adapter(tampered)))

    def test_plort_cardinality_fails_closed_and_missing_rows_stay_pending(self):
        classification = classification_doc()
        classification['species'][0]['native']['SR2']['produces'] = ['plort.ringtail', 'plort.other']
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_CARDINALITY', {d['code'] for d in doc['diagnostics']})
        classification = classification_doc()
        classification['species'][0]['native']['SR2']['native_ref'] = 'SlimeDefinition.Missing'
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertIn('ADAPTER_SNAPSHOT_ROW_MISSING', {d['code'] for d in doc['diagnostics']})

    def test_no_plort_species_build_clean_none_mode(self):
        # A captured species that produces nothing must stay plort-less instead of inventing one.
        classification = classification_doc()
        classification['plorts'] = []
        classification['species'][0]['native']['SR2']['produces'] = []
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = []
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        self.assertEqual(doc['status'], 'EXPERIMENTAL')
        self.assertIn('ADAPTER_NO_PLORT_SPECIES', {d['code'] for d in doc['diagnostics']})
        self.assertEqual(doc['plort']['mode'], 'NONE')
        self.assertIsNone(doc['plort']['canonical_id'])
        self.assertIsNone(doc['plort']['source']['native_ref'])
        self.assertEqual(doc['plort']['presence_evidence'], 'NOT_APPLICABLE')
        self.assertEqual(doc['inventory']['items'],
                         [{'kind': 'slime', 'canonical_id': 'slime.ringtail'}])
        self.assertEqual(validate_adapter(doc), [])

    def test_no_plort_conflicts_fail_closed(self):
        # The definition names refs while the classification lists none: quarantine.
        classification = classification_doc()
        classification['species'][0]['native']['SR2']['produces'] = []
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_PRESENCE_MISMATCH', {d['code'] for d in doc['diagnostics']})
        self.assertTrue(validate_adapter(doc))
        # The plorts bucket carries a slug-matching row while everything else says "no plort".
        classification = classification_doc()
        classification['species'][0]['native']['SR2']['produces'] = []
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = []
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        self.assertEqual(doc['status'], 'QUARANTINED')
        self.assertIn('ADAPTER_PLORT_PRESENCE_MISMATCH', {d['code'] for d in doc['diagnostics']})

    def test_no_plort_validator_rejects_invented_identity(self):
        classification = classification_doc()
        classification['plorts'] = []
        classification['species'][0]['native']['SR2']['produces'] = []
        snapshot = snapshot_doc()
        snapshot['definitions'][0]['produces'] = []
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot)
        tampered = copy.deepcopy(doc)
        tampered['plort']['canonical_id'] = 'plort.ringtail'
        self.assertTrue(any('must not carry a plort id' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['plort']['presence_evidence'] = 'CLASSIFIED'
        self.assertTrue(any('NOT_APPLICABLE' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['plort']['production_relation'] = 'produces'
        self.assertTrue(validate_adapter(tampered))
        tampered = copy.deepcopy(doc)
        tampered['inventory']['items'].append({'kind': 'plort', 'canonical_id': 'plort.ringtail'})
        self.assertTrue(any('inventory' in p for p in validate_adapter(tampered)))

    def test_plort_mode_is_declared_and_inventory_mirrors_it(self):
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['plort']['mode'], 'PLORT')
        self.assertEqual(doc['inventory']['items'],
                         [{'kind': 'slime', 'canonical_id': 'slime.ringtail'},
                          {'kind': 'plort', 'canonical_id': 'plort.ringtail'}])
        tampered = copy.deepcopy(doc)
        tampered['inventory']['items'] = [{'kind': 'slime', 'canonical_id': 'slime.ringtail'}]
        self.assertTrue(any('inventory' in p for p in validate_adapter(tampered)))

    def test_uncaptured_plort_rows_stay_pending_capture(self):
        # The real LAB capture has an empty plorts bucket: identity comes from the definition's
        # produces relation, target presence stays unproven (warning, not a false absence claim).
        classification = classification_doc()
        classification['plorts'] = []
        doc = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(doc['status'], 'EXPERIMENTAL')
        self.assertIn('PLORT_ROW_PENDING_CAPTURE', {d['code'] for d in doc['diagnostics']})
        self.assertIsNone(doc['plort']['target_native_present'])
        self.assertEqual(doc['plort']['presence_evidence'], 'PENDING_CAPTURE')
        self.assertEqual(doc['plort']['source']['native_ref'], 'IdentifiableType.RingtailPlort')
        self.assertTrue(doc['plort']['local_query'])
        self.assertEqual(validate_adapter(doc), [])
        tampered = copy.deepcopy(doc)
        tampered['plort']['presence_evidence'] = 'CLASSIFIED'
        self.assertTrue(any('PENDING_CAPTURE' in p for p in validate_adapter(tampered)))

    def test_direction_and_inputs_are_validated(self):
        with self.assertRaises(ValueError):
            build_adapter(classification_doc(), 'SR1', 'SR1', 'slime.ringtail', snapshot_doc())
        with self.assertRaises(ValueError):
            build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc('SR1'))
        with self.assertRaises(ValueError):
            build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.unknown', snapshot_doc())

    def test_sr1_style_entries_snapshot_works(self):
        classification = {'schema': 'bridge-classification', 'schema_version': 1,
                          'species': [species_row(canonical_id='slime.rad', classification='SR1_ONLY',
                                                  source='SR1', native_ref='RAD_SLIME',
                                                  plort='plort.rad', numeric_id=1)],
                          'plorts': [{'canonical_id': 'plort.rad', 'classification': 'SR1_ONLY',
                                      'present': {'SR1': 1, 'SR2': 0},
                                      'native': {'SR1': {'native_ref': 'RAD_PLORT', 'numeric_id': 1,
                                                         'definition_hash': 'c' * 64, 'produces': [],
                                                         'role': 'plort', 'quarantine': None,
                                                         'malformed': False}, 'SR2': None}}]}
        snapshot = {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': 'SR1',
                    'entries': [{'native_ref': 'RAD_SLIME', 'numeric_id': 1, 'content_kind': 'slime',
                                 'role': 'base', 'canonical_id': 'slime.rad',
                                 'display_name': 'Rad', 'definition_hash': H,
                                 'produces': ['RAD_PLORT'], 'base_slimes': [], 'quarantine': None}]}
        doc = build_adapter(classification, 'SR1', 'SR2', 'slime.rad', snapshot)
        self.assertEqual(doc['status'], 'EXPERIMENTAL')
        self.assertEqual(validate_adapter(doc), [])
        self.assertEqual(doc['adapter_id'], 'adapter.sr1-to-sr2.slime.rad')
        self.assertEqual(doc['identity']['source']['numeric_id'], 1)

    def test_deterministic_and_input_sensitive_hashes(self):
        first = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        second = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(first, second)
        changed = snapshot_doc()
        changed['definitions'][0]['prefab_refs'] = ['SR2:Prefab:other']
        third = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', changed)
        self.assertNotEqual(first['hashes']['snapshot_definition'], third['hashes']['snapshot_definition'])
        self.assertEqual(first['hashes']['classification_row'], third['hashes']['classification_row'])

    def test_validator_rejects_payloads_floats_and_native_target_keys(self):
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        tampered = copy.deepcopy(doc)
        tampered['visual']['mesh'] = {'vertices': [1, 2, 3]}
        self.assertTrue(any('forbidden key' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['economy']['base_value'] = 1.5
        self.assertTrue(any('floats' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['identity']['target']['numeric_id'] = 7
        self.assertTrue(any('forbidden key' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['status'] = 'AVAILABLE'
        self.assertTrue(any('capped' in p for p in validate_adapter(tampered)))

    def test_validator_rejects_lookalike_source_sections(self):
        # SA-004: only the exact source section paths may carry native identifiers.
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        tampered = copy.deepcopy(doc)
        tampered['identity']['source_shadow'] = {'numeric_id': 7}
        problems = validate_adapter(tampered)
        self.assertTrue(any('unknown field' in p for p in problems))
        self.assertTrue(any('forbidden key' in p for p in problems))
        tampered = copy.deepcopy(doc)
        tampered['plort']['source_copy'] = {'native_id': 'x'}
        self.assertTrue(validate_adapter(tampered))

    def test_validator_requires_status_coherence(self):
        # SA-003: a quarantined descriptor must never validate clean, and EXPERIMENTAL
        # descriptors may not carry error diagnostics.
        doc = build_adapter(classification_doc(), 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        tampered = copy.deepcopy(doc)
        tampered['status'] = 'QUARANTINED'
        tampered['diagnostics'] = []
        problems = validate_adapter(tampered)
        self.assertTrue(any('quarantined' in p for p in problems))
        self.assertTrue(any('without an error diagnostic' in p for p in problems))
        classification = classification_doc()
        classification['species'][0]['classification'] = 'SHARED_NATIVE'
        quarantined = build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshot_doc())
        self.assertEqual(quarantined['status'], 'QUARANTINED')
        self.assertTrue(validate_adapter(quarantined))
        tampered = copy.deepcopy(doc)
        tampered['identity']['source']['role'] = 'largo'
        self.assertTrue(any('base-role' in p for p in validate_adapter(tampered)))
        tampered = copy.deepcopy(doc)
        tampered['identity']['target']['native_present'] = True
        self.assertTrue(any('absent from the target' in p for p in validate_adapter(tampered)))

    def test_adapter_id_is_direction_scoped(self):
        self.assertEqual(adapter_id('SR1', 'SR2', 'slime.rad'), 'adapter.sr1-to-sr2.slime.rad')


if __name__ == '__main__':
    unittest.main()
