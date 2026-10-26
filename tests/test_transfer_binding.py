"""Synthetic profile/pair binding regressions; never native transfer proof."""
from __future__ import annotations

import concurrent.futures
import importlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal
from bridge.profile.store import ProfileStore
from bridge.transfer.store import TransferStore


class TransferBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profiles = self.root / 'profiles.sqlite3'
        self.journal = self.root / 'counterpart.sqlite3'
        self.ledger = self.root / 'ledger.sqlite3'
        self.builds = {'SR1': 'synthetic-build-1', 'SR2': 'synthetic-build-2'}
        with ProfileStore(self.profiles):
            pass
        with CounterpartJournal(self.journal):
            pass
        self.store = TransferStore(self.ledger)
        self.addCleanup(self.store.close)

    def module(self):
        # Before the feature exists, report a failing assertion rather than an import error.
        spec = importlib.util.find_spec('bridge.transfer.binding')
        self.assertIsNotNone(spec, 'the shared transfer binding boundary is missing')
        return importlib.import_module('bridge.transfer.binding')

    def linked(self, suffix='1', *, sender_save='a', receiver_save='b'):
        with ProfileStore(self.profiles) as profiles:
            profile = profiles.find_or_create('SR1', 'install-sr1-' + suffix,
                                               sender_save, 'native-sr1-' + suffix)
            pid = profile['bridge_profile_id']
            cid = profiles.reserve_counterpart(pid, 'SR2')['creation_id']
            self.assertTrue(profiles.claim_creation(pid, cid))
            profile = profiles.confirm_counterpart(pid, cid, 'install-sr2-' + suffix,
                                                    receiver_save, 'native-sr2-' + suffix)
        # Explicit synthetic setup: no real native CREATE or receipt is produced.
        with CounterpartJournal(self.journal) as journal:
            for realm in ('SR1', 'SR2'):
                journal.observe_live_endpoint(pid, {'realm': realm, **profile[realm.lower()]},
                                              self.builds[realm])
        return pid

    def resolver(self):
        return self.module().TransferBindingResolver(self.profiles, self.journal,
                                                     current_builds=lambda: dict(self.builds))

    def resolve(self, pid, sender='SR1', sender_save='a', receiver='SR2', receiver_save='b'):
        return self.resolver().resolve(pid, sender, sender_save, receiver, receiver_save)

    def prepare(self, binding, key='logical-action'):
        return self.store.prepare_transfer(binding.sender_realm, binding.sender_save_id,
                                           binding.receiver_realm, binding.receiver_save_id,
                                           'slime.pink', 1, idempotency_key=key, binding=binding)

    def test_missing_binding_refuses_before_inserting_any_row(self):
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                        idempotency_key='unbound-action')
        self.assertEqual(self.store.list_in_flight(), [])

    def test_missing_profile_refuses_before_ledger_write(self):
        resolver = self.resolver()
        for pid in (None, '', 'unknown-profile'):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b')
        self.assertEqual(self.store.list_in_flight(), [])

    def test_unlinked_and_creating_profiles_are_not_transfer_authority(self):
        resolver = self.resolver()
        with ProfileStore(self.profiles) as profiles:
            pid = profiles.find_or_create('SR1', 'only-sr1', 'a', 'only-native')['bridge_profile_id']
            with self.assertRaises(ValueError):
                resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b')
            profiles.reserve_counterpart(pid, 'SR2')
            with self.assertRaises(ValueError):
                resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b')
        self.assertEqual(self.store.list_in_flight(), [])

    def test_wrong_save_or_direction_cannot_be_bound_to_a_valid_profile(self):
        pid = self.linked()
        resolver = self.resolver()
        for args in (('SR1', 'other', 'SR2', 'b'), ('SR1', 'a', 'SR2', 'other'),
                     ('SR1', 'a', 'SR1', 'a'), ('SR3', 'a', 'SR2', 'b')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                resolver.resolve(pid, *args)
        self.assertEqual(self.store.list_in_flight(), [])

    def test_snapshot_records_profile_and_both_installation_and_native_identities(self):
        pid = self.linked()
        binding = self.resolve(pid)
        tid = self.prepare(binding)
        row = self.store.get(tid)
        self.assertEqual(row.bridge_profile_id, pid)
        self.assertEqual(row.sender_installation_id, 'install-sr1-1')
        self.assertEqual(row.sender_native_save_identity, 'native-sr1-1')
        self.assertEqual(row.receiver_installation_id, 'install-sr2-1')
        self.assertEqual(row.receiver_native_save_identity, 'native-sr2-1')
        self.assertEqual(self.module().binding_for_row(row), binding)

    def test_reverse_direction_uses_the_same_pair_without_swapping_identity(self):
        pid = self.linked()
        binding = self.resolve(pid, 'SR2', 'b', 'SR1', 'a')
        tid = self.prepare(binding)
        row = self.store.get(tid)
        self.assertEqual((row.sender_realm, row.sender_installation_id, row.sender_native_save_identity),
                         ('SR2', 'install-sr2-1', 'native-sr2-1'))
        self.assertEqual((row.receiver_realm, row.receiver_installation_id), ('SR1', 'install-sr1-1'))

    def test_same_save_ids_on_different_installations_do_not_share_an_idempotency_key(self):
        a, b = self.linked('1'), self.linked('2')
        first = self.prepare(self.resolve(a))
        with self.assertRaises(ValueError):
            self.prepare(self.resolve(b))
        other = self.prepare(self.resolve(b), 'other-action')
        self.assertNotEqual(first, other)
        self.assertNotEqual(self.store.get(first).bridge_profile_id,
                            self.store.get(other).bridge_profile_id)

    def test_snapshot_survives_reopen_and_same_action_retry_is_idempotent(self):
        pid = self.linked()
        original = self.resolve(pid)
        tid = self.prepare(original)
        self.store.lock(tid)
        self.store.close()
        self.store = TransferStore(self.ledger)
        self.addCleanup(self.store.close)
        self.assertEqual(self.prepare(self.resolve(pid)), tid)
        self.assertEqual(self.module().binding_for_row(self.store.get(tid)), original)
        self.assertEqual(self.store.get(tid).status, 'LOCKED')

    def test_bound_snapshot_must_match_the_legacy_save_arguments(self):
        binding = self.resolve(self.linked())
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 'other', 'SR2', 'b', 'slime.pink',
                                        idempotency_key='wrong-save', binding=binding)
        self.assertEqual(self.store.list_in_flight(), [])

    def test_compatibility_and_record_identity_guards_are_not_replaced_by_raw_linked(self):
        pid = self.linked()
        self.resolve(pid)
        self.builds['SR2'] = 'different-current-build'
        with self.assertRaises(ValueError):
            self.resolve(pid)
        self.builds['SR2'] = 'synthetic-build-2'
        with closing(sqlite3.connect(self.journal)) as db, db:
            db.execute("UPDATE endpoint_meta SET verdict='FAILED' WHERE profile_id=? AND realm='SR2'", (pid,))
        with self.assertRaises(ValueError):
            self.resolve(pid)
        self.assertEqual(self.store.list_in_flight(), [])

    def test_retired_provenance_is_rejected_by_the_existing_pair_policy(self):
        from bridge.counterpart.legacy_quarantine import RETIRED_CREATION_IDS
        pid = self.linked()
        retired = next(iter(RETIRED_CREATION_IDS))
        with closing(sqlite3.connect(self.profiles)) as db, db:
            db.execute('UPDATE profiles SET creation_id=? WHERE bridge_profile_id=?', (retired, pid))
        with self.assertRaises(ValueError):
            self.resolve(pid)

    def test_readonly_resolution_works_on_http_worker_threads_and_does_not_migrate(self):
        pid = self.linked()
        resolver = self.resolver()
        before = {p: p.read_bytes() for p in (self.profiles, self.journal)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            bindings = list(pool.map(lambda _: resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b'), range(12)))
        self.assertTrue(all(binding == bindings[0] for binding in bindings))
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_legacy_rows_are_retained_unbound_and_never_backfilled(self):
        module = self.module()
        self.store.close()
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.execute('DROP TABLE transfers')
            db.execute('''CREATE TABLE transfers (
                transfer_id TEXT PRIMARY KEY, sender_realm TEXT NOT NULL, sender_save_id TEXT NOT NULL,
                receiver_realm TEXT NOT NULL, receiver_save_id TEXT NOT NULL,
                canonical_item_id TEXT NOT NULL, count INTEGER NOT NULL, status TEXT NOT NULL,
                abort_reason TEXT, created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL)''')
            db.execute("INSERT INTO transfers VALUES ('legacy', 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1,"
                       " 'ESCROWED', NULL, 'now', 'now')")
        self.store = TransferStore(self.ledger)
        self.addCleanup(self.store.close)
        row = self.store.get('legacy')
        self.assertEqual(row.status, 'ESCROWED')
        self.assertIsNone(row.bridge_profile_id)
        with self.assertRaises(ValueError):
            module.binding_for_row(row)
        with self.assertRaises(ValueError):
            self.store.commit('legacy')
        self.assertEqual(self.store.get('legacy').status, 'ESCROWED')


if __name__ == '__main__':
    unittest.main()
