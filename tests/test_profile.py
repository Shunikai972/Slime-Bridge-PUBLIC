"""Persistent BridgeProfile linkage. Native game saves remain untouched."""
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.profile.store import ProfileStore


class ProfileStoreTests(unittest.TestCase):
    def test_constructor_closes_database_on_initialization_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MagicMock()
            db.execute.side_effect = RuntimeError('database setup failed')
            with patch('bridge.profile.store.sqlite3.connect', return_value=db):
                with self.assertRaisesRegex(RuntimeError, 'database setup failed'):
                    ProfileStore(Path(tmp) / 'profiles.sqlite3')
            db.close.assert_called_once()

    def test_concurrent_open_migrates_old_schema_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old-profiles.sqlite3'
            db = sqlite3.connect(path)
            try:
                db.execute('CREATE TABLE profiles (bridge_profile_id TEXT PRIMARY KEY, state TEXT, '
                           'creation_id TEXT, last_realm TEXT)')
                db.commit()
            finally:
                db.close()
            barrier = threading.Barrier(6)
            original_connect = sqlite3.connect

            class SlowSchemaRead(sqlite3.Connection):
                def execute(self, sql, parameters=()):
                    if sql == 'PRAGMA table_info(profiles)':
                        rows = list(super().execute(sql, parameters))
                        # Old code reads the schema outside its write transaction:
                        # deliberately widen the window for two openers to see it.
                        time.sleep(0.05)
                        return rows
                    return super().execute(sql, parameters)

            def open_store(_):
                barrier.wait(timeout=10)
                with ProfileStore(path) as store:
                    return {row['name'] for row in store._db.execute('PRAGMA table_info(profiles)')}

            with patch('bridge.profile.store.sqlite3.connect',
                       side_effect=lambda *a, **kw: original_connect(*a, factory=SlowSchemaRead, **kw)):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    schemas = list(pool.map(open_store, range(6)))
            for columns in schemas:
                self.assertIn('creation_issued', columns)
                self.assertIn('creation_target_realm', columns)

    def test_creation_intent_survives_restart_without_duplicate_save_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bridge-profiles.sqlite3'
            with ProfileStore(path) as store:
                profile = store.find_or_create('SR1', 'installation-1', 'sr1-save', 'source-fingerprint')
                intent = store.reserve_counterpart(profile['bridge_profile_id'], 'SR2')
                self.assertEqual(intent['state'], 'CREATING')
                self.assertTrue(intent['creation_id'])
            with ProfileStore(path) as reopened:
                same = reopened.find_or_create('SR1', 'installation-1', 'sr1-save', 'source-fingerprint')
                self.assertEqual(profile['bridge_profile_id'], same['bridge_profile_id'])
                again = reopened.reserve_counterpart(profile['bridge_profile_id'], 'SR2')
                self.assertEqual(intent, again)
                self.assertEqual(reopened.get(profile['bridge_profile_id'])['state'], 'CREATING')

    def test_confirm_counterpart_is_idempotent_and_rejects_conflicting_identity(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            profile = store.find_or_create('SR1', 'installation-1', 'sr1-save', 'source-fingerprint')
            intent = store.reserve_counterpart(profile['bridge_profile_id'], 'SR2')
            self.assertTrue(store.claim_creation(profile['bridge_profile_id'], intent['creation_id']))
            linked = store.confirm_counterpart(profile['bridge_profile_id'], intent['creation_id'],
                                                  'installation-2', 'sr2-save', 'target-fingerprint')
            self.assertEqual(linked['state'], 'LINKED')
            self.assertEqual(linked['sr2']['native_save_id'], 'sr2-save')
            self.assertEqual(linked, store.confirm_counterpart(profile['bridge_profile_id'], intent['creation_id'],
                                                                'installation-2', 'sr2-save', 'target-fingerprint'))
            with self.assertRaises(ValueError):
                store.confirm_counterpart(profile['bridge_profile_id'], intent['creation_id'],
                                          'installation-2', 'different-save', 'target-fingerprint')

    def test_sr2_to_sr1_confirmation_remains_idempotent_after_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'profiles.sqlite3'
            with ProfileStore(db) as store:
                profile = store.find_or_create('SR2', 'i2', 'sr2-source', 'fp2')
                profile_id = profile['bridge_profile_id']
                creation_id = store.reserve_counterpart(profile_id, 'SR1')['creation_id']
                self.assertTrue(store.claim_creation(profile_id, creation_id))
                linked = store.confirm_counterpart(profile_id, creation_id, 'i1', 'sr1-target', 'fp1')
                self.assertEqual(linked['sr1']['native_save_id'], 'sr1-target')
            with ProfileStore(db) as store:
                self.assertEqual(linked, store.confirm_counterpart(profile_id, creation_id,
                                                                   'i1', 'sr1-target', 'fp1'))
                with self.assertRaises(ValueError):
                    store.confirm_counterpart(profile_id, creation_id, 'i2', 'sr2-source', 'fp2')

    def test_legacy_linked_profile_without_target_does_not_guess_counterpart(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            profile = store.find_or_create('SR2', 'i2', 'source', 'fp2')
            token = store.reserve_counterpart(profile['bridge_profile_id'], 'SR1')['creation_id']
            self.assertTrue(store.claim_creation(profile['bridge_profile_id'], token))
            store.confirm_counterpart(profile['bridge_profile_id'], token, 'i1', 'target', 'fp1')
            store._db.execute('UPDATE profiles SET creation_target_realm=NULL WHERE bridge_profile_id=?',
                              (profile['bridge_profile_id'],))
            store._db.commit()
            with self.assertRaisesRegex(ValueError, 'manual reconciliation'):
                store.confirm_counterpart(profile['bridge_profile_id'], token, 'i2', 'source', 'fp2')

    def test_rejects_cross_linked_save_and_changed_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            first = store.find_or_create('SR1', 'i1', 'save1', 'fingerprint1')
            second = store.find_or_create('SR1', 'i1', 'save2', 'fingerprint2')
            with self.assertRaises(ValueError):
                store.find_or_create('SR1', 'i1', 'save1', 'changed')
            intent = store.reserve_counterpart(first['bridge_profile_id'], 'SR2')
            self.assertTrue(store.claim_creation(first['bridge_profile_id'], intent['creation_id']))
            store.confirm_counterpart(first['bridge_profile_id'], intent['creation_id'], 'i2', 'other', 'fp')
            other_intent = store.reserve_counterpart(second['bridge_profile_id'], 'SR2')
            self.assertTrue(store.claim_creation(second['bridge_profile_id'], other_intent['creation_id']))
            with self.assertRaises(ValueError):
                store.confirm_counterpart(second['bridge_profile_id'], other_intent['creation_id'], 'i2', 'other', 'fp')
            self.assertEqual(store.get(second['bridge_profile_id'])['state'], 'CREATING')

    def test_creation_dispatch_is_once_even_after_crash_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'profiles.sqlite3'
            with ProfileStore(path) as store:
                profile = store.find_or_create('SR1', 'i1', 'save1', 'fp1')
                profile_id = profile['bridge_profile_id']
                intent = store.reserve_counterpart(profile_id, 'SR2')
                self.assertTrue(store.claim_creation(profile_id, intent['creation_id']))
            with ProfileStore(path) as store:
                self.assertFalse(store.claim_creation(profile_id, intent['creation_id']))
                self.assertEqual(store.reserve_counterpart(profile_id, 'SR2'), intent)
                with self.assertRaises(ValueError):
                    store.claim_creation(profile_id, 'wrong-token')

    def test_unproven_destination_discovered_before_confirmation_is_not_absorbed(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            source = store.find_or_create('SR1', 'i1', 'save1', 'fp1')
            intent = store.reserve_counterpart(source['bridge_profile_id'], 'SR2')
            self.assertTrue(store.claim_creation(source['bridge_profile_id'], intent['creation_id']))
            orphan = store.find_or_create('SR2', 'i2', 'save2', 'fp2')
            with self.assertRaisesRegex(ValueError, 'provenance|linked elsewhere'):
                store.confirm_counterpart(source['bridge_profile_id'], intent['creation_id'], 'i2', 'save2', 'fp2')
            self.assertEqual(store.get(orphan['bridge_profile_id'])['state'], 'UNLINKED')
            self.assertEqual(store.get(source['bridge_profile_id'])['state'], 'CREATING')

    def test_unissued_intent_cannot_claim_an_unregistered_save(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            profile = store.find_or_create('SR1', 'i1', 'source', 'fp1')
            token = store.reserve_counterpart(profile['bridge_profile_id'], 'SR2')['creation_id']
            with self.assertRaisesRegex(ValueError, 'issued|dispatch|provenance'):
                store.confirm_counterpart(profile['bridge_profile_id'], token, 'i2', 'unrelated', 'fp2')
            self.assertEqual(store.get(profile['bridge_profile_id'])['state'], 'CREATING')

    def test_rejects_ambiguous_or_empty_identity(self):
        with tempfile.TemporaryDirectory() as tmp, ProfileStore(Path(tmp) / 'profiles.sqlite3') as store:
            for realm, install, save, fingerprint in [('SR3', 'i', 's', 'f'), ('SR1', 'i', '', 'f'),
                                                      ('SR1', 'i', 's', ''), ('SR1', '', 's', 'f')]:
                with self.assertRaises(ValueError):
                    store.find_or_create(realm, install, save, fingerprint)


if __name__ == '__main__':
    unittest.main()
