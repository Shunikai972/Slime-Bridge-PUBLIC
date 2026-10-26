"""Unit coverage for the species parity roster, matrix and fail-closed validators.

Synthetic classification/snapshot documents mirror the real capture shapes (SR2 ``definitions``
rows, SR1 ``entries`` rows). Nothing here reads the LAB: the tests pin roster derivation,
exclusion vocabulary, identity-collision checks, matrix assembly/validation, the NATIVE_ONLY
default and the no-imported-duplicate rule for shared species.
"""
import copy
import random
import unittest

from bridge.conversion.foreign_spawn import default_policy
from bridge.conversion.species_adapter import build_adapter, validate_adapter
from bridge.conversion.species_parity import (BEHAVIOR_QUERY, PARITY_SCHEMA, PARITY_SCHEMA_VERSION,
                                              build_parity_matrix, derive_roster,
                                              target_identity_collisions, validate_parity)

H = 'a' * 64


def _base(ref, display, produces, numeric_id=None, role='base', quarantine=None):
    return {'native_ref': ref, 'numeric_id': numeric_id, 'role': role, 'definition_hash': H,
            'display_name': display, 'produces': produces, 'quarantine': quarantine,
            'malformed': False}


def _exclusive(canonical_id, classification, source, native, plort, present=None):
    target = 'SR1' if source == 'SR2' else 'SR2'
    return {'canonical_id': canonical_id, 'classification': classification, 'reasons': [],
            'present': present or {source: 1, target: 0},
            'native': {source: native, target: None},
            'identity_basis': {source: 'DERIVED'},
            'identity_evidence': {source: [f'slug-v1:{native["native_ref"]}'] if native else []}}


def fixture():
    pink = {'canonical_id': 'slime.pink', 'classification': 'SHARED_NATIVE', 'reasons': [],
            'present': {'SR1': 1, 'SR2': 1},
            'native': {'SR1': _base('PINK_SLIME', 'Pink', ['plort.pink'], numeric_id=4),
                       'SR2': _base('SlimeDefinition.Pink', 'Pink', ['plort.pink'])},
            'identity_basis': {'SR1': 'DERIVED', 'SR2': 'DERIVED'},
            'identity_evidence': {'SR1': ['slug-v1:PINK_SLIME'], 'SR2': ['slug-v1:SlimeDefinition.Pink']}}
    rad = _exclusive('slime.rad', 'SR1_ONLY', 'SR1',
                     _base('RAD_SLIME', 'Rad', ['plort.rad'], numeric_id=1), 'plort.rad')
    ringtail = _exclusive('slime.ringtail', 'SR2_ONLY', 'SR2',
                          _base('SlimeDefinition.Ringtail', 'Ringtail', ['plort.ringtail']),
                          'plort.ringtail')
    largo = _exclusive('slime.pink_rock_largo', 'SR2_ONLY', 'SR2',
                       _base('SlimeDefinition.PinkRockLargo', 'Pink Rock Largo',
                             ['IdentifiableType.PinkPlort'], role='largo'), 'plort.pink')
    fog = _exclusive('slime.fog', 'SR1_ONLY', 'SR1', _base('FOG_SLIME', 'Fog', []), None,
                     present={'SR1': 1})
    rust = _exclusive('slime.rust', 'SR1_ONLY', 'SR1',
                      _base('RUST_SLIME', 'Rust', [], quarantine={'reason': 'quarantined'}), None)
    dusk = _exclusive('slime.dusk', 'SR1_ONLY', 'SR1', None, None)
    dusk['present'] = {'SR1': 0, 'SR2': 1}
    dusk['native'] = {'SR1': None, 'SR2': _base('SlimeDefinition.Dusk', 'Dusk', [])}
    mist = _exclusive('slime.mist', 'SR1_ONLY', 'SR1', _base('MIST_SLIME', 'Mist', []), None)
    mist['native']['SR2'] = _base('SlimeDefinition.Mist', 'Mist', [])
    echo_a = _exclusive('slime.echo', 'SR1_ONLY', 'SR1', _base('ECHO_SLIME', 'Echo', []), None)
    echo_b = _exclusive('slime.echo', 'SR1_ONLY', 'SR1', _base('ECHO_ALT', 'Echo', []), None)
    species = [pink, rad, ringtail, largo, fog, rust, dusk, mist, echo_a, echo_b]
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
                      'species': species, 'plorts': plorts,
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
                'definitions': [{'native_ref': 'SlimeDefinition.Pink', 'reference_id': 'SlimeDefinition.Pink',
                                 'display_name': 'Pink', 'role': 'base', 'definition_hash': H,
                                 'produces': ['IdentifiableType.PinkPlort']},
                                {'native_ref': 'SlimeDefinition.Ringtail',
                                 'reference_id': 'SlimeDefinition.Ringtail', 'display_name': 'Ringtail',
                                 'role': 'base', 'definition_hash': H,
                                 'produces': ['IdentifiableType.RingtailPlort']}]}}
    return classification, snapshots


def adapters_for(classification, snapshots, policy=None):
    docs = [build_adapter(classification, 'SR1', 'SR2', 'slime.rad', snapshots['SR1']),
            build_adapter(classification, 'SR2', 'SR1', 'slime.ringtail', snapshots['SR2'])]
    return {doc['adapter_id']: doc for doc in docs}


class RosterTests(unittest.TestCase):
    def test_roster_derives_exact_buckets(self):
        roster = derive_roster(fixture()[0])
        self.assertEqual(roster['shared_native'], ['slime.pink'])
        self.assertEqual(roster['sr1_only'], ['slime.rad'])
        self.assertEqual(roster['sr2_only'], ['slime.ringtail'])
        self.assertEqual(roster['names']['slime.ringtail'], 'Ringtail')
        self.assertEqual(roster['counts'], {'shared_native': 1, 'sr1_only': 1, 'sr2_only': 1,
                                            'excluded': 6, 'rows_scanned': 10})

    def test_roster_excludes_every_broken_row_with_reasons(self):
        excluded = {entry['canonical_id']: entry['reasons'] for entry in derive_roster(fixture()[0])['excluded']}
        self.assertEqual(excluded['slime.pink_rock_largo'], ['NATIVE_ROLE_NOT_BASE'])
        self.assertEqual(excluded['slime.fog'], ['PRESENCE_UNCAPTURED'])
        self.assertEqual(excluded['slime.rust'], ['NATIVE_QUARANTINED'])
        self.assertEqual(excluded['slime.dusk'], ['PRESENCE_MISMATCH'])
        self.assertEqual(excluded['slime.mist'], ['NATIVE_FOR_ABSENT_REALM'])
        self.assertEqual(excluded['slime.echo'], ['DUPLICATE_CANONICAL_ID'])
        self.assertNotIn('slime.echo', derive_roster(fixture()[0])['sr1_only'])

    def test_roster_is_deterministic_under_row_shuffle(self):
        classification, _ = fixture()
        shuffled = copy.deepcopy(classification)
        random.Random(7).shuffle(shuffled['species'])
        first, second = derive_roster(classification), derive_roster(shuffled)
        self.assertEqual(first['sr1_only'], second['sr1_only'])
        self.assertEqual(first['sr2_only'], second['sr2_only'])
        self.assertEqual(first['shared_native'], second['shared_native'])
        self.assertEqual([e['canonical_id'] for e in first['excluded']],
                         [e['canonical_id'] for e in second['excluded']])

    def test_identity_collisions_are_detected(self):
        classification, _ = fixture()
        roster = derive_roster(classification)
        self.assertEqual(target_identity_collisions(classification, roster), [])
        collided = copy.deepcopy(classification)
        next(row for row in collided['species'] if row['canonical_id'] == 'slime.rad')['native']['SR1']['produces'] = ['plort.pink']
        problems = target_identity_collisions(collided, roster)
        self.assertTrue(any('plort.pink' in p and 'collides' in p for p in problems))


class ParityMatrixTests(unittest.TestCase):
    def test_matrix_builds_and_validates_for_the_roster(self):
        classification, snapshots = fixture()
        adapters = adapters_for(classification, snapshots)
        matrix = build_parity_matrix(classification, snapshots, adapters)
        self.assertEqual(matrix['schema'], PARITY_SCHEMA)
        self.assertEqual(matrix['schema_version'], PARITY_SCHEMA_VERSION)
        self.assertEqual(validate_parity(matrix), [])
        self.assertEqual(matrix['roster']['counts']['sr1_only'], 1)
        rows = {row['canonical_id']: row for row in matrix['species']}
        self.assertEqual(len(rows), 3)
        rad = rows['slime.rad']
        self.assertEqual(rad['classification'], 'SR1_ONLY')
        self.assertEqual(rad['required_adapter_direction'], 'SR1->SR2')
        self.assertEqual(rad['adapter']['adapter_id'], 'adapter.sr1-to-sr2.slime.rad')
        self.assertEqual(rad['plort']['canonical_id'], 'plort.rad')
        self.assertEqual(rad['plort']['target_status'], 'CLASSIFIED_ABSENT')
        self.assertEqual(rad['states']['behavior']['local_query'], BEHAVIOR_QUERY['SR1'])
        self.assertIs(rad['foreign_world_spawn']['active'], False)
        pink = rows['slime.pink']
        self.assertIsNone(pink['adapter'])
        self.assertIsNone(pink['required_adapter_direction'])
        self.assertEqual(pink['states'], {'import': {'state': 'NOT_REQUIRED_NATIVE_BOTH_REALMS'}})
        self.assertEqual(pink['plort']['target_status'], 'NATIVE_BOTH_REALMS')
        self.assertEqual(pink['foreign_world_spawn'], {'applicable': False, 'reason': 'NATIVE_BOTH_REALMS'})

    def test_matrix_never_activates_foreign_spawn_even_under_all_imported(self):
        classification, snapshots = fixture()
        adapters = adapters_for(classification, snapshots)
        policy = default_policy() | {'mode': 'ALL_IMPORTED'}
        matrix = build_parity_matrix(classification, snapshots, adapters, policy=policy)
        self.assertEqual(validate_parity(matrix), [])
        for row in matrix['species']:
            if row['classification'] != 'SHARED_NATIVE':
                self.assertIs(row['foreign_world_spawn']['requested'], True)
                self.assertIs(row['foreign_world_spawn']['active'], False)
                self.assertEqual(row['foreign_world_spawn']['integration'], 'BLOCKED_ON_LOCAL_QUERY')

    def test_missing_adapter_is_a_validation_problem(self):
        classification, snapshots = fixture()
        adapters = adapters_for(classification, snapshots)
        del adapters['adapter.sr1-to-sr2.slime.rad']
        matrix = build_parity_matrix(classification, snapshots, adapters)
        rows = {row['canonical_id']: row for row in matrix['species']}
        self.assertIsNone(rows['slime.rad']['adapter'])
        problems = validate_parity(matrix)
        self.assertTrue(any('slime.rad' in p and 'adapter' in p for p in problems))

    def test_quarantined_adapter_fails_the_matrix(self):
        classification, snapshots = fixture()
        classification['plorts'] = []
        snapshots['SR1']['entries'][1]['produces'] = ['IdentifiableType.OtherPlort']  # rad row
        broken = build_adapter(classification, 'SR1', 'SR2', 'slime.rad', snapshots['SR1'])
        self.assertEqual(broken['status'], 'QUARANTINED')
        adapters = {'adapter.sr1-to-sr2.slime.rad': broken}
        adapters['adapter.sr2-to-sr1.slime.ringtail'] = build_adapter(
            classification, 'SR2', 'SR1', 'slime.ringtail', snapshots['SR2'])
        matrix = build_parity_matrix(classification, snapshots, adapters)
        problems = validate_parity(matrix)
        self.assertTrue(any('not EXPERIMENTAL-clean' in p for p in problems))

    def test_shared_species_never_gain_an_imported_adapter(self):
        classification, snapshots = fixture()
        adapters = adapters_for(classification, snapshots)
        stray = build_adapter(classification, 'SR1', 'SR2', 'slime.pink', snapshots['SR1'])
        self.assertEqual(stray['status'], 'QUARANTINED')
        adapters[stray['adapter_id']] = stray
        matrix = build_parity_matrix(classification, snapshots, adapters)
        problems = validate_parity(matrix)
        self.assertTrue(any('outside the exclusive roster' in p for p in problems))

    def test_matrix_validator_catches_tampering(self):
        classification, snapshots = fixture()
        matrix = build_parity_matrix(classification, snapshots, adapters_for(classification, snapshots))
        self.assertEqual(validate_parity(matrix), [])
        for mutate, needle in (
                (lambda m: m['collisions'].append('x'), 'collisions'),
                (lambda m: m['policy'].__setitem__('mode', 'SOMETIMES'), 'mode'),
                (lambda m: m['blocked_queries'].append('Q999'), 'blocked_queries'),
        ):
            tampered = copy.deepcopy(matrix)
            mutate(tampered)
            self.assertTrue(any(needle in p for p in validate_parity(tampered)))
        tampered = copy.deepcopy(matrix)
        row = next(r for r in tampered['species'] if r['classification'] == 'SR1_ONLY')
        row['foreign_world_spawn']['active'] = True
        self.assertTrue(any('Q034' in p for p in validate_parity(tampered)))
        tampered = copy.deepcopy(matrix)
        row = next(r for r in tampered['species'] if r['classification'] == 'SR1_ONLY')
        row['plort']['target_status'] = 'PENDING_CAPTURE'
        self.assertTrue(validate_parity(tampered))
        tampered = copy.deepcopy(matrix)
        row = next(r for r in tampered['species'] if r['classification'] == 'SHARED_NATIVE')
        row['canonical_id'] = 'slime.pink_largo'
        self.assertTrue(any('largo' in p for p in validate_parity(tampered)))

    def test_no_plort_claim_must_match_the_adapter_descriptor(self):
        # Reviewer repro PARITY-001: flip a clean PLORT row to NONE without touching its
        # descriptor; the matrix must refuse the contradiction.
        classification, snapshots = fixture()
        matrix = build_parity_matrix(classification, snapshots, adapters_for(classification, snapshots))
        self.assertEqual(validate_parity(matrix), [])
        tampered = copy.deepcopy(matrix)
        row = next(r for r in tampered['species'] if r['classification'] == 'SR1_ONLY')
        row['plort'] = {'mode': 'NONE', 'canonical_id': None, 'source': None,
                        'target_status': 'NONE', 'presence_evidence': None, 'local_query': None}
        row['states']['plort'] = {'state': 'NONE', 'local_query': None}
        problems = validate_parity(tampered)
        self.assertTrue(any('contradicts its adapter descriptor' in p for p in problems), problems)
        # And a plort state that silently diverges from the mapping is refused too.
        tampered = copy.deepcopy(matrix)
        row = next(r for r in tampered['species'] if r['classification'] == 'SR1_ONLY')
        row['states']['plort'] = {'state': 'NONE', 'local_query': None}
        problems = validate_parity(tampered)
        self.assertTrue(any('must mirror the plort mapping' in p for p in problems), problems)
        # Reviewer repro PARITY-002: the row-local descriptor must agree with both the mapping and
        # the indexed descriptor; flipping only it (or to an unknown value) must be refused.
        for value in ('NONE', 'SOMETIMES'):
            tampered = copy.deepcopy(matrix)
            row = next(r for r in tampered['species'] if r['classification'] == 'SR1_ONLY')
            row['adapter']['plort_mode'] = value
            problems = validate_parity(tampered)
            self.assertTrue(any('row adapter descriptor' in p for p in problems), (value, problems))

    def test_matrix_validator_is_total_on_null_fields(self):
        # SP-001 regression: malformed JSON values must yield problems, never an exception.
        classification, snapshots = fixture()
        matrix = build_parity_matrix(classification, snapshots, adapters_for(classification, snapshots))
        self.assertEqual(validate_parity(matrix), [])
        tampered = copy.deepcopy(matrix)
        tampered['species'][0]['presence'] = None
        problems = validate_parity(tampered)
        self.assertTrue(any('presence must map both realms to booleans' in p for p in problems))
        tampered = copy.deepcopy(matrix)
        tampered['species'][1]['states']['runtime_registration'] = None
        problems = validate_parity(tampered)
        self.assertTrue(any('runtime state entries must be objects' in p for p in problems))
        tampered = copy.deepcopy(matrix)
        tampered['species'][1]['native'] = None
        tampered['species'][1]['record_hashes'] = None
        problems = validate_parity(tampered)
        self.assertTrue(any('needs native_ref and definition_hash' in p for p in problems))

    def test_matrix_regeneration_is_deterministic(self):
        classification, snapshots = fixture()
        adapters = adapters_for(classification, snapshots)
        first = build_parity_matrix(classification, snapshots, adapters)
        second = build_parity_matrix(classification, snapshots, adapters)
        self.assertEqual(first, second)
        self.assertEqual(validate_adapter(adapters['adapter.sr1-to-sr2.slime.rad']), [])


if __name__ == '__main__':
    unittest.main()
