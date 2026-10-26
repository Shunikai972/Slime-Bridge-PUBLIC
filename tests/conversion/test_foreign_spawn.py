"""Unit coverage for the foreign-world natural spawning policy (bridge-foreign-spawn-policy).

Pins: NATIVE_ONLY defaults, fail-closed normalization, override semantics, and the runtime
truth that no policy value ever turns on foreign natural spawning while the integration stays
BLOCKED_ON_LOCAL_QUERY (Q034).
"""
import copy
import unittest

from bridge.conversion.foreign_spawn import (DEFAULT_MODE, EFFECTIVE_SCHEMA, MODES, POLICY_SCHEMA,
                                             POLICY_SCHEMA_VERSION, SPAWN_INTEGRATION_QUERY,
                                             default_policy, effective_policy, normalize_policy,
                                             validate_policy)

IMPORTED = ['slime.rad', 'slime.ringtail']


class ForeignSpawnPolicyTests(unittest.TestCase):
    def test_default_policy_is_native_only(self):
        policy = default_policy()
        self.assertEqual(policy['schema'], POLICY_SCHEMA)
        self.assertEqual(policy['schema_version'], POLICY_SCHEMA_VERSION)
        self.assertEqual(policy['mode'], 'NATIVE_ONLY')
        self.assertEqual(policy['overrides'], {})
        self.assertEqual(validate_policy(policy, imported_ids=IMPORTED), [])

    def test_effective_defaults_request_and_active_false(self):
        doc = effective_policy(default_policy(), imported_ids=IMPORTED)
        self.assertEqual(doc['schema'], EFFECTIVE_SCHEMA)
        self.assertEqual(doc['mode'], DEFAULT_MODE)
        self.assertEqual(doc['integration']['status'], 'BLOCKED_ON_LOCAL_QUERY')
        self.assertEqual(doc['integration']['local_query'], SPAWN_INTEGRATION_QUERY)
        for species_id in IMPORTED:
            entry = doc['species'][species_id]
            self.assertIs(entry['requested_natural_spawn_foreign'], False)
            self.assertEqual(entry['requested_source'], 'MODE')
            self.assertIs(entry['active_natural_spawn_foreign'], False)

    def test_all_imported_requests_only_imported_species_and_never_activates(self):
        policy = default_policy() | {'mode': 'ALL_IMPORTED'}
        doc = effective_policy(policy, imported_ids=IMPORTED)
        for species_id in IMPORTED:
            entry = doc['species'][species_id]
            self.assertIs(entry['requested_natural_spawn_foreign'], True)
            self.assertIs(entry['active_natural_spawn_foreign'], False)
        # Overrides may only name verified imported species; native ids are refused outright.
        self.assertTrue(any('not a verified imported species' in p for p in
                            validate_policy(policy | {'overrides': {'slime.pink': True}},
                                            imported_ids=IMPORTED)))

    def test_overrides_switch_both_ways(self):
        opt_in = default_policy() | {'overrides': {'slime.ringtail': True}}
        doc = effective_policy(opt_in, imported_ids=IMPORTED)
        self.assertIs(doc['species']['slime.ringtail']['requested_natural_spawn_foreign'], True)
        self.assertEqual(doc['species']['slime.ringtail']['requested_source'], 'OVERRIDE')
        self.assertIs(doc['species']['slime.rad']['requested_natural_spawn_foreign'], False)
        opt_out = default_policy() | {'mode': 'ALL_IMPORTED', 'overrides': {'slime.rad': False}}
        doc = effective_policy(opt_out, imported_ids=IMPORTED)
        self.assertIs(doc['species']['slime.rad']['requested_natural_spawn_foreign'], False)
        self.assertEqual(doc['species']['slime.rad']['requested_source'], 'OVERRIDE')
        for entry in doc['species'].values():
            self.assertIs(entry['active_natural_spawn_foreign'], False)

    def test_validate_refuses_malformed_documents(self):
        self.assertTrue(validate_policy(None, imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'mode': 'ON'}, imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'extra': 1}, imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'overrides': {'slime.bogus': True}},
                                        imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'overrides': {'slime.rad': 'yes'}},
                                        imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'overrides': {'RAD_SLIME': True}},
                                        imported_ids=IMPORTED))
        self.assertTrue(validate_policy(default_policy() | {'overrides': []}, imported_ids=IMPORTED))
        self.assertEqual(validate_policy(default_policy() | {'mode': 'ALL_IMPORTED'},
                                         imported_ids=IMPORTED), [])

    def test_normalize_fails_toward_native_only(self):
        policy, diagnostics = normalize_policy(None, imported_ids=IMPORTED)
        self.assertEqual(policy, default_policy())
        self.assertEqual(diagnostics, [])
        policy, diagnostics = normalize_policy('broken', imported_ids=IMPORTED)
        self.assertEqual(policy['mode'], 'NATIVE_ONLY')
        self.assertIn('FOREIGN_SPAWN_UNREADABLE', {d['code'] for d in diagnostics})
        doc = {'schema': POLICY_SCHEMA, 'schema_version': POLICY_SCHEMA_VERSION, 'mode': 'WHATEVER',
               'overrides': {'slime.rad': True, 'slime.pink': True, 'slime.bogus': False, 'x': 1}}
        policy, diagnostics = normalize_policy(doc, imported_ids=IMPORTED)
        self.assertEqual(policy['mode'], 'NATIVE_ONLY')
        self.assertEqual(policy['overrides'], {'slime.rad': True})
        codes = {d['code'] for d in diagnostics}
        self.assertIn('FOREIGN_SPAWN_MODE_INVALID', codes)
        self.assertIn('FOREIGN_SPAWN_OVERRIDE_UNKNOWN', codes)

    def test_effective_requires_a_valid_mode(self):
        with self.assertRaises(ValueError):
            effective_policy({'mode': 'MAYBE'}, imported_ids=IMPORTED)
        with self.assertRaises(ValueError):
            effective_policy({'mode': None}, imported_ids=IMPORTED)

    def test_mode_vocabulary_matches_the_documented_contract(self):
        self.assertEqual(MODES, ('NATIVE_ONLY', 'ALL_IMPORTED'))
        self.assertEqual(DEFAULT_MODE, 'NATIVE_ONLY')


if __name__ == '__main__':
    unittest.main()
