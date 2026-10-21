import unittest
import hashlib

from bridge.content.coverage import largo_coverage


def definition(name, realm, largo=False, parents=(), prefab=True, eligible=True):
    object_ref = realm + ':' + name
    prefab_ref = name + '-prefab'
    return {'canonical_id': None if largo else 'slime.' + name,
            'canonical_proven': not largo, 'canonical_evidence': ['native mapping'] if not largo else [],
            'native_id': name + '-id', 'reference_id': name + '-ref',
            'object_ref': object_ref, 'name': name,
            'is_largo': largo, 'can_largofy': False if largo else eligible,
            'base_slimes': list(parents), 'produced_plorts': [],
            'prefab_refs': [prefab_ref] if prefab else [],
            'dedicated_prefab_identity': ({'object_ref': prefab_ref,
                'definition_ref': object_ref, 'kind': 'prefab',
                'sha256': hashlib.sha256(prefab_ref.encode()).hexdigest(),
                'provenance': 'native lookup'} if prefab and largo else None),
            'evidence': ['registry', 'prefab']}


def snapshot(realm, definitions, complete=True):
    digest = hashlib.sha256(realm.encode()).hexdigest()
    return {'schema_version': 1, 'realm': realm, 'source_kind': 'runtime_registry',
            'completeness': 'COMPLETE' if complete else 'PARTIAL', 'definitions': definitions,
            'assets': [{'object_ref': d['dedicated_prefab_identity']['object_ref'],
                        'kind': 'prefab', 'sha256': d['dedicated_prefab_identity']['sha256']}
                       for d in definitions if d.get('dedicated_prefab_identity')],
            'errors': [], 'game_build': 'test', 'registry_traversed': complete,
            'build_match': 'VERIFIED',
            'build_proof': {'active_sha256': digest, 'scanned_sha256': digest,
                            'verified_active_sha256': digest,
                            'active_path': 'Lab/Games/' + realm, 'provenance': 'test fixture'},
            'traversal_proof': {'registry_identity': realm + ':native-registry',
                                'registry_sha256': digest, 'verified_registry_sha256': digest,
                                'verified_registry_identity': realm + ':native-registry',
                                'verified_object_refs': sorted(d['object_ref'] for d in definitions),
                                'expected_count': len(definitions), 'observed_count': len(definitions)}}


class LargoCoverageTests(unittest.TestCase):
    def test_missing_both_needs_two_complete_scans_and_proven_eligibility(self):
        a = definition('a', 'SR1')
        b = definition('b', 'SR1')
        x = definition('a', 'SR2')
        y = definition('b', 'SR2')
        rows = largo_coverage(snapshot('SR1', [a, b]), snapshot('SR2', [x, y]))['pairs']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['final_status'], 'MISSING_BOTH')
        self.assertIsNone(rows[0]['future_model_required'])
        partial = largo_coverage(snapshot('SR1', [a, b], False), snapshot('SR2', [x, y]))
        self.assertEqual(partial['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')

    def test_largo_requires_explicit_parents_and_prefab(self):
        a, b = definition('a', 'SR1'), definition('b', 'SR1')
        x, y = definition('a', 'SR2'), definition('b', 'SR2')
        named_only = definition('ab', 'SR1', largo=True)
        no_prefab = definition('ab', 'SR1', largo=True,
                               parents=[a['object_ref'], b['object_ref']], prefab=False)
        for largo in (named_only, no_prefab):
            row = largo_coverage(snapshot('SR1', [a, b, largo]), snapshot('SR2', [x, y]))['pairs'][0]
            self.assertEqual(row['final_status'], 'BLOCKED_UNKNOWN')
        row = largo_coverage(snapshot('SR1', [a, b, no_prefab]), snapshot('SR2', [x, y]))['pairs'][0]
        self.assertEqual(row['sr1_definition_id'], 'ab-id')
        proven = definition('ab', 'SR1', largo=True,
                            parents=[a['object_ref'], b['object_ref']])
        row = largo_coverage(snapshot('SR1', [a, b, proven]), snapshot('SR2', [x, y]))['pairs'][0]
        self.assertEqual(row['final_status'], 'NATIVE_SR1_ONLY')
        self.assertEqual(row['sr1_definition_id'], 'ab-id')

    def test_conflicting_eligibility_blocks_unknown(self):
        sr1 = [definition('a', 'SR1'), definition('b', 'SR1')]
        sr2 = [definition('a', 'SR2', eligible=False), definition('b', 'SR2')]
        row = largo_coverage(snapshot('SR1', sr1), snapshot('SR2', sr2))['pairs'][0]
        self.assertEqual(row['final_status'], 'BLOCKED_UNKNOWN')

    def test_runtime_native_id_parent_refs_resolve_without_name_guessing(self):
        a, b = definition('a', 'SR1'), definition('b', 'SR1')
        x, y = definition('a', 'SR2'), definition('b', 'SR2')
        largo = definition('unrelated_name', 'SR1', largo=True,
                           parents=[a['native_id'], b['native_id']])
        row = largo_coverage(snapshot('SR1', [a, b, largo]), snapshot('SR2', [x, y]))['pairs'][0]
        self.assertEqual(row['final_status'], 'NATIVE_SR1_ONLY')

    def test_unverified_build_and_traversal_count_block_absence(self):
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1')])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        one['build_match'] = 'UNVERIFIED'
        self.assertEqual(largo_coverage(one, two)['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')
        one['build_match'] = 'VERIFIED'
        one['traversal_proof']['expected_count'] = 3
        self.assertEqual(largo_coverage(one, two)['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')

    def test_arbitrary_prefab_reference_is_not_native(self):
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1')])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        largo = definition('ab', 'SR1', largo=True,
                           parents=[one['definitions'][0]['object_ref'], one['definitions'][1]['object_ref']])
        largo['dedicated_prefab_identity'] = None
        one['definitions'].append(largo)
        one['traversal_proof'].update(expected_count=3, observed_count=3)
        self.assertEqual(largo_coverage(one, two)['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')

    def test_unresolved_claimed_prefab_is_not_native(self):
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1')])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        row = definition('ab', 'SR1', largo=True,
            parents=[one['definitions'][0]['object_ref'], one['definitions'][1]['object_ref']])
        one['definitions'].append(row)
        one['traversal_proof'].update(expected_count=3, observed_count=3)
        one['assets'] = []
        self.assertEqual(largo_coverage(one, two)['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')

    def test_reported_prefab_is_the_verified_identity(self):
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1'),
            definition('ab', 'SR1', largo=True, parents=['SR1:a', 'SR1:b'])])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        one['definitions'][2]['prefab_refs'].insert(0, 'unrelated')
        pair = largo_coverage(one, two)['pairs'][0]
        self.assertEqual(pair['sr1_prefab_identity'],
                         one['definitions'][2]['dedicated_prefab_identity']['object_ref'])

    def test_canonical_collision_blocks_exclusive_and_largo(self):
        from bridge.content.coverage import species_coverage
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1')])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        duplicate = definition('duplicate', 'SR1')
        duplicate['canonical_id'] = 'slime.a'
        one['definitions'].append(duplicate)
        one['traversal_proof'].update(expected_count=3, observed_count=3)
        self.assertFalse(species_coverage(one, two)['exclusive_classification_complete'])
        self.assertEqual(largo_coverage(one, two)['pairs'][0]['final_status'], 'BLOCKED_UNKNOWN')

    def test_model_required_needs_two_verified_dedicated_inventories(self):
        one = snapshot('SR1', [definition('a', 'SR1'), definition('b', 'SR1')])
        two = snapshot('SR2', [definition('a', 'SR2'), definition('b', 'SR2')])
        self.assertIsNone(largo_coverage(one, two)['pairs'][0]['future_model_required'])
        for scan in (one, two):
            scan['asset_inventory'] = {'completeness': 'COMPLETE',
                'build_sha256': scan['build_proof']['active_sha256'],
                'inventory_identity': scan['realm'] + ':models',
                'inventory_sha256': scan['build_proof']['active_sha256'],
                'expected_count': 0, 'observed_count': 0,
                'dedicated_largo_model_ids': [], 'classification_provenance': 'native model traversal'}
        self.assertTrue(largo_coverage(one, two)['pairs'][0]['future_model_required'])
        two['asset_inventory']['completeness'] = 'PARTIAL'
        self.assertIsNone(largo_coverage(one, two)['pairs'][0]['future_model_required'])


if __name__ == '__main__':
    unittest.main()
