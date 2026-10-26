"""Cross-registry reconciliation: native registries are truth; exclusivity needs complete evidence."""
import copy
import random
import unittest
from pathlib import Path

from bridge.content.canonical import sha256_text
from bridge.registry.content import ContentRegistry
from bridge.registry.reconcile import (aliases_from_content_registry, classify_species, load_aliases)
from bridge.registry.snapshot import normalize_snapshot, snapshot_from_scan

ROOT = Path(__file__).resolve().parents[2]


def entry(ref, kind='slime', role='base', produces=(), **extra):
    row = {'native_ref': ref, 'numeric_id': None, 'content_kind': kind,
           'role': role if kind == 'slime' else None, 'canonical_id': None, 'canonical_evidence': [],
           'display_name': None, 'definition_hash': sha256_text(ref), 'produces': list(produces),
           'base_slimes': [], 'quarantine': None}
    row.update(extra)
    return row


def snap(realm, entries, completeness='COMPLETE', evidence='SYNTHETIC'):
    return {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': realm,
            'build': {'fingerprint': f'fixture-{realm}', 'label': None}, 'data_evidence': evidence,
            'traversal': {'completeness': completeness, 'registry_identity': f'{realm}:fixture',
                          'expected_count': len(entries), 'observed_count': len(entries)},
            'entries': entries, 'diagnostics': []}


def by_id(result):
    return {row['canonical_id']: row for row in result['species']}


SR1 = [entry('PINK_SLIME', produces=['PINK_PLORT']), entry('PINK_PLORT', 'plort'),
       entry('AMBER_TEST_SLIME', produces=['AMBER_TEST_PLORT']), entry('AMBER_TEST_PLORT', 'plort')]
SR2 = [entry('SlimeDefinition.Pink', produces=['IdentifiableType.PinkPlort']),
       entry('IdentifiableType.PinkPlort', 'plort'),
       entry('SlimeDefinition.AzureTest', produces=['IdentifiableType.AzureTestPlort']),
       entry('IdentifiableType.AzureTestPlort', 'plort')]


class ReconcileTests(unittest.TestCase):
    def test_shared_pink_maps_to_one_canonical_and_is_never_converted(self):
        result = classify_species(snap('SR1', SR1), snap('SR2', SR2))
        rows = by_id(result)
        self.assertEqual(rows['slime.pink']['classification'], 'SHARED_NATIVE')
        self.assertEqual(rows['slime.pink']['native']['SR1']['native_ref'], 'PINK_SLIME')
        self.assertEqual(rows['slime.pink']['native']['SR2']['native_ref'], 'SlimeDefinition.Pink')
        self.assertEqual(rows['slime.amber_test']['classification'], 'SR1_ONLY')
        self.assertEqual(rows['slime.azure_test']['classification'], 'SR2_ONLY')
        self.assertEqual(sum(r['canonical_id'] == 'slime.pink' for r in result['species']), 1)
        self.assertTrue(result['synthetic'])
        self.assertEqual(rows['slime.pink']['identity_basis'], {'SR1': 'DERIVED', 'SR2': 'DERIVED'})
        plorts = {row['canonical_id']: row['classification'] for row in result['plorts']}
        self.assertEqual(plorts['plort.pink'], 'SHARED_NATIVE')

    def test_partial_other_registry_cannot_establish_exclusivity(self):
        result = classify_species(snap('SR1', SR1), snap('SR2', SR2, completeness='PARTIAL'))
        rows = by_id(result)
        self.assertEqual(rows['slime.amber_test']['classification'], 'BLOCKED_UNKNOWN')
        self.assertIn('SR2_TRAVERSAL_INCOMPLETE', rows['slime.amber_test']['reasons'])
        self.assertEqual(rows['slime.azure_test']['classification'], 'SR2_ONLY')
        self.assertEqual(rows['slime.pink']['classification'], 'SHARED_NATIVE')

    def test_self_reported_complete_with_wrong_counts_is_downgraded(self):
        bad = snap('SR2', SR2)
        bad['traversal']['expected_count'] = 99
        normalized = normalize_snapshot(bad)
        self.assertEqual(normalized['traversal']['completeness'], 'PARTIAL')
        self.assertIn('TRAVERSAL_COUNT_MISMATCH', {d['code'] for d in normalized['diagnostics']})
        rows = by_id(classify_species(snap('SR1', SR1), bad))
        self.assertEqual(rows['slime.amber_test']['classification'], 'BLOCKED_UNKNOWN')

    def test_canonical_collision_quarantines_only_that_species(self):
        sr2 = SR2 + [entry('SlimeDefinition.Azure_Test')]
        rows = by_id(classify_species(snap('SR1', SR1), snap('SR2', sr2)))
        self.assertEqual(rows['slime.azure_test']['classification'], 'QUARANTINED')
        self.assertIn('AMBIGUOUS_IDENTITY_SR2', rows['slime.azure_test']['reasons'])
        self.assertEqual(rows['slime.amber_test']['classification'], 'SR1_ONLY')
        self.assertEqual(rows['slime.pink']['classification'], 'SHARED_NATIVE')

    def test_malformed_entry_is_isolated_and_quarantined(self):
        sr2 = SR2 + [entry('SlimeDefinition.Broken', definition_hash='nope'),
                     {'native_ref': 'SlimeDefinition.Weird', 'content_kind': 'slime'}]
        result = classify_species(snap('SR1', SR1), snap('SR2', sr2))
        rows = by_id(result)
        self.assertEqual(rows['slime.broken']['classification'], 'QUARANTINED')
        self.assertEqual(rows['slime.weird']['classification'], 'QUARANTINED')
        self.assertIn('MALFORMED_ENTRY', rows['slime.weird']['reasons'])
        self.assertEqual(rows['slime.azure_test']['classification'], 'SR2_ONLY')
        self.assertEqual(rows['slime.amber_test']['classification'], 'SR1_ONLY')

    def test_unidentifiable_entry_blocks_the_other_realms_exclusivity_claims(self):
        sr2 = SR2 + [{'content_kind': 'slime', 'garbage': True}]
        rows = by_id(classify_species(snap('SR1', SR1), snap('SR2', sr2)))
        self.assertEqual(rows['slime.amber_test']['classification'], 'BLOCKED_UNKNOWN')
        self.assertIn('SR2_HAS_UNIDENTIFIED_ENTRIES', rows['slime.amber_test']['reasons'])
        self.assertEqual(rows['slime.azure_test']['classification'], 'SR2_ONLY')

    def test_unknown_role_counterpart_blocks_rather_than_guessing(self):
        sr2 = SR2 + [entry('SlimeDefinition.AmberTest', role=None)]
        rows = by_id(classify_species(snap('SR1', SR1), snap('SR2', sr2)))
        self.assertEqual(rows['slime.amber_test']['classification'], 'BLOCKED_UNKNOWN')
        self.assertIn('COUNTERPART_ROLE_UNKNOWN', rows['slime.amber_test']['reasons'])

    def test_quarantined_source_wins_even_when_counterpart_exists(self):
        sr1 = copy.deepcopy(SR1)
        sr1[0]['quarantine'] = 'known broken prefab'
        rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', SR2)))
        self.assertEqual(rows['slime.pink']['classification'], 'QUARANTINED')
        self.assertIn('SOURCE_QUARANTINED_SR1', rows['slime.pink']['reasons'])

    def test_cross_realm_plort_overlap_blocks_suspected_renamed_species(self):
        sr1 = SR1 + [entry('OLDNAME_SLIME', produces=['MOSS_PLORT']), entry('MOSS_PLORT', 'plort')]
        sr2 = SR2 + [entry('SlimeDefinition.NewName', produces=['IdentifiableType.MossPlort']),
                     entry('IdentifiableType.MossPlort', 'plort')]
        rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', sr2)))
        for cid in ('slime.oldname', 'slime.new_name'):
            self.assertEqual(rows[cid]['classification'], 'BLOCKED_UNKNOWN')
            self.assertIn('CROSS_REALM_PLORT_OVERLAP', rows[cid]['reasons'])
        aliases = load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
            {'realm': 'SR2', 'native_ref': 'SlimeDefinition.NewName', 'canonical_id': 'slime.oldname',
             'evidence': 'fixture: same diet, plort and pedia key'}]})
        rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', sr2), aliases=aliases))
        self.assertEqual(rows['slime.oldname']['classification'], 'SHARED_NATIVE')
        self.assertEqual(rows['slime.oldname']['identity_basis']['SR2'], 'ALIAS_MANIFEST')
        self.assertNotIn('slime.new_name', rows)

    def test_conflicting_alias_manifest_is_refused(self):
        with self.assertRaises(ValueError):
            load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
                {'realm': 'SR2', 'native_ref': 'X', 'canonical_id': 'slime.a', 'evidence': 'e'},
                {'realm': 'SR2', 'native_ref': 'X', 'canonical_id': 'slime.b', 'evidence': 'e'}]})
        with self.assertRaises(ValueError):
            load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
                {'realm': 'SR2', 'native_ref': 'X', 'canonical_id': 'slime.a', 'evidence': ''}]})

    def test_existing_transfer_registry_seeds_identity_bound_to_build(self):
        registry = ContentRegistry.load(ROOT / 'bridge/registry/pink.json')
        sr1 = [entry('Identifiable.Id.3', numeric_id=3, produces=['Identifiable.Id.6']),
               entry('Identifiable.Id.6', 'plort', numeric_id=6)]
        aliases = aliases_from_content_registry(registry, builds={'SR1': 'fixture-SR1'})
        rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', SR2), aliases=aliases))
        self.assertEqual(rows['slime.pink']['classification'], 'SHARED_NATIVE')
        self.assertEqual(rows['slime.pink']['identity_basis']['SR1'], 'ALIAS_MANIFEST')
        # Numeric ids are build-bound: an alias for another build or with no build never applies.
        for builds in ({'SR1': 'another-build'}, None):
            aliases = aliases_from_content_registry(registry, builds=builds)
            rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', SR2), aliases=aliases))
            self.assertNotIn('SR1', rows['slime.pink']['identity_basis'])
        with self.assertRaises(ValueError):
            load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
                {'realm': 'SR1', 'native_ref': 3, 'canonical_id': 'slime.pink', 'evidence': 'e'}]})

    def test_disagreeing_string_and_numeric_aliases_quarantine(self):
        aliases = load_aliases({'schema': 'bridge-identity-aliases', 'schema_version': 1, 'aliases': [
            {'realm': 'SR1', 'native_ref': 'PINK_SLIME', 'canonical_id': 'slime.pink', 'evidence': 'e'},
            {'realm': 'SR1', 'native_ref': 3, 'build': 'fixture-SR1', 'canonical_id': 'slime.rose',
             'evidence': 'e'}]})
        sr1 = copy.deepcopy(SR1)
        sr1[0]['numeric_id'] = 3
        rows = by_id(classify_species(snap('SR1', sr1), snap('SR2', SR2), aliases=aliases))
        self.assertEqual(rows['slime.pink']['classification'], 'QUARANTINED')
        self.assertIn('ALIAS_CONFLICT', rows['slime.pink']['reasons'])

    def test_provider_canonical_ids_are_format_checked(self):
        sr2 = SR2 + [entry('SlimeDefinition.Odd', canonical_id='slime.Odd-One')]
        rows = by_id(classify_species(snap('SR1', SR1), snap('SR2', sr2)))
        self.assertEqual(rows['slime.Odd-One']['classification'], 'QUARANTINED')
        self.assertIn('INVALID_CANONICAL_ID', rows['slime.Odd-One']['reasons'])
        self.assertEqual(rows['slime.azure_test']['classification'], 'SR2_ONLY')

    def test_order_independence(self):
        reference = classify_species(snap('SR1', SR1), snap('SR2', SR2))
        for seed in range(10):
            a, b = copy.deepcopy(SR1), copy.deepcopy(SR2)
            random.Random(seed).shuffle(a)
            random.Random(seed + 100).shuffle(b)
            self.assertEqual(classify_species(snap('SR1', a), snap('SR2', b)), reference)

    def test_snapshot_level_errors_raise(self):
        with self.assertRaises(ValueError):
            normalize_snapshot({**snap('SR1', SR1), 'schema_version': 2})
        with self.assertRaises(ValueError):
            normalize_snapshot({**snap('SR1', SR1), 'realm': 'SR3'})
        with self.assertRaises(ValueError):
            classify_species(snap('SR2', SR2), snap('SR2', SR2))

    def test_duplicate_native_ref_quarantines(self):
        sr2 = SR2 + [entry('SlimeDefinition.AzureTest')]
        rows = by_id(classify_species(snap('SR1', SR1), snap('SR2', sr2)))
        self.assertEqual(rows['slime.azure_test']['classification'], 'QUARANTINED')
        self.assertIn('DUPLICATE_NATIVE_REF_SR2', rows['slime.azure_test']['reasons'])

    def test_largos_are_not_classified_for_conversion_but_are_kept(self):
        sr2 = SR2 + [entry('SlimeDefinition.PinkAzure', role='largo')]
        result = classify_species(snap('SR1', SR1), snap('SR2', sr2))
        self.assertNotIn('slime.pink_azure', by_id(result))
        self.assertEqual([r['native_ref'] for r in result['non_base']['SR2']], ['SlimeDefinition.PinkAzure'])


class ScanAdapterTests(unittest.TestCase):
    def test_scan_proof_gate_controls_completeness(self):
        from test_largo_coverage import snapshot, definition
        scan = snapshot('SR1', [definition('pink', 'SR1')])
        converted = snapshot_from_scan(scan)
        self.assertEqual(converted['traversal']['completeness'], 'COMPLETE')
        self.assertEqual(converted['data_evidence'], 'RUNTIME_ANCHORED')
        row = converted['entries'][0]
        self.assertEqual((row['canonical_id'], row['role']), ('slime.pink', 'base'))
        self.assertEqual(normalize_snapshot(converted)['diagnostics'], [])
        scan['build_match'] = 'UNVERIFIED'
        downgraded = snapshot_from_scan(scan)
        self.assertEqual(downgraded['traversal']['completeness'], 'PARTIAL')
        self.assertEqual(downgraded['data_evidence'], 'STATIC')


if __name__ == '__main__':
    unittest.main()


class EvidenceTrustTests(unittest.TestCase):
    def test_self_declared_runtime_evidence_is_downgraded(self):
        claimed = snap('SR2', SR2, evidence='RUNTIME_ANCHORED')
        normalized = normalize_snapshot(claimed)
        self.assertEqual((normalized['data_evidence'], normalized['traversal']['completeness']),
                         ('STATIC', 'PARTIAL'))
        self.assertIn('RUNTIME_CLAIM_UNANCHORED', {d['code'] for d in normalized['diagnostics']})
        static_complete = normalize_snapshot(snap('SR2', SR2, evidence='STATIC'))
        self.assertEqual(static_complete['traversal']['completeness'], 'PARTIAL')
        rows = by_id(classify_species(snap('SR1', SR1), claimed))
        self.assertEqual(rows['slime.amber_test']['classification'], 'BLOCKED_UNKNOWN')

    def test_only_in_process_anchored_scans_keep_runtime_evidence(self):
        import json as _json
        from test_largo_coverage import snapshot, definition
        from bridge.registry.snapshot import AnchoredSnapshot
        anchored = snapshot_from_scan(snapshot('SR1', [definition('pink', 'SR1')]))
        self.assertIsInstance(anchored, AnchoredSnapshot)
        self.assertEqual(normalize_snapshot(anchored)['data_evidence'], 'RUNTIME_ANCHORED')
        self.assertEqual(normalize_snapshot(copy.deepcopy(anchored))['data_evidence'], 'RUNTIME_ANCHORED')
        round_trip = _json.loads(_json.dumps(anchored))
        self.assertEqual(normalize_snapshot(round_trip)['data_evidence'], 'STATIC')
        anchored['entries'].append(entry('SlimeDefinition.Injected'))
        self.assertEqual(normalize_snapshot(anchored)['data_evidence'], 'STATIC')
        with self.assertRaises(TypeError):
            AnchoredSnapshot(round_trip)
