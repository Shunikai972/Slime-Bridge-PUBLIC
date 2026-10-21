from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from transfer_binding_fixtures import synthetic_binding

from bridge.transfer.store import (
    TransferStore,
    TransferStoreError,
    TransferRow,
    STATUS_PREPARED,
    STATUS_LOCKED,
    STATUS_ESCROWED,
    STATUS_COMMITTED,
    STATUS_ABORTED,
    STATUS_REFUNDED,
    VALID_TRANSFER_STATUSES,
)


class TransferStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / 'transfers.db'
        self.store = TransferStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_prepare_transfer_creates_prepared_row(self) -> None:
        tid = self.store.prepare_transfer(
            sender_realm='SR1',
            sender_save_id='save-sr1',
            receiver_realm='SR2',
            receiver_save_id='save-sr2',
            canonical_item_id='slime.pink',
            count=1,
                    binding=synthetic_binding('SR1', 'save-sr1', 'SR2', 'save-sr2'), idempotency_key='fixture-1',
        )
        self.assertTrue(tid)
        row = self.store.get(tid)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.transfer_id, tid)
        self.assertEqual(row.status, STATUS_PREPARED)
        self.assertEqual(row.sender_realm, 'SR1')
        self.assertEqual(row.sender_save_id, 'save-sr1')
        self.assertEqual(row.receiver_realm, 'SR2')
        self.assertEqual(row.receiver_save_id, 'save-sr2')
        self.assertEqual(row.canonical_item_id, 'slime.pink')
        self.assertEqual(row.count, 1)

    def test_idempotency_survives_reopen_and_rejects_conflicts(self):
        args = ('SR1', 's1', 'SR2', 's2', 'slime.pink', 2)
        first = self.store.prepare_transfer(*args, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='logical-action')
        self.store.close()
        self.store = TransferStore(self.db_path)
        self.assertEqual(self.store.prepare_transfer(*args, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='logical-action'), first)
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 3,
                                        binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='logical-action')

    def test_separate_stores_race_same_idempotency_key(self):
        import concurrent.futures
        other = TransferStore(self.db_path)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(store.prepare_transfer, 'SR1', 'a', 'SR2', 'b',
                                       'slime.pink', 1, 'one-action', binding=synthetic_binding()) for store in (self.store, other)]
                self.assertEqual(futures[0].result(), futures[1].result())
        finally:
            other.close()

    def test_prepare_rejects_invalid_inputs(self) -> None:
        for bad_count in (0, -1, 50):
            with self.assertRaises(ValueError):
                self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', bad_count, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-2')
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 's1', 'SR1', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-3')
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('UNKNOWN', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-4')
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'unknown.item', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-5')

    def test_lifecycle_success_path_prepared_locked_escrowed_committed(self) -> None:
        tid = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 2, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-6')
        self.store.lock(tid)
        row = self.store.get(tid)
        assert row is not None
        self.assertEqual(row.status, STATUS_LOCKED)

        self.store.escrow(tid)
        row = self.store.get(tid)
        assert row is not None
        self.assertEqual(row.status, STATUS_ESCROWED)

        self.store.commit(tid)
        row = self.store.get(tid)
        assert row is not None
        self.assertEqual(row.status, STATUS_COMMITTED)

        # Idempotent calls on terminal state return gracefully
        self.store.commit(tid)
        self.assertEqual(self.store.get(tid).status, STATUS_COMMITTED)

    def test_abort_from_prepared_or_locked(self) -> None:
        tid1 = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-7')
        self.store.abort(tid1, reason='receiver rejected')
        self.assertEqual(self.store.get(tid1).status, STATUS_ABORTED)

        tid2 = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-8')
        self.store.lock(tid2)
        self.store.abort(tid2, reason='lock timeout')
        self.assertEqual(self.store.get(tid2).status, STATUS_ABORTED)

        # Cannot abort from escrowed (must refund instead)
        tid3 = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-9')
        self.store.lock(tid3)
        self.store.escrow(tid3)
        with self.assertRaises(TransferStoreError):
            self.store.abort(tid3, reason='too late')

    def test_refund_from_escrowed(self) -> None:
        tid = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-10')
        self.store.lock(tid)
        self.store.escrow(tid)
        self.store.begin_refund(tid, 'receiver disconnected')
        self.store.refund(tid, reason='receiver disconnected')
        row = self.store.get(tid)
        assert row is not None
        self.assertEqual(row.status, STATUS_REFUNDED)
        # Idempotent
        self.store.refund(tid, reason='again')
        self.assertEqual(self.store.get(tid).status, STATUS_REFUNDED)

    def test_legacy_schema_migrates_additively(self):
        import sqlite3
        from contextlib import closing
        self.store.close()
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('DROP TABLE transfers')
            conn.execute('''CREATE TABLE transfers (
                transfer_id TEXT PRIMARY KEY, sender_realm TEXT NOT NULL, sender_save_id TEXT NOT NULL,
                receiver_realm TEXT NOT NULL, receiver_save_id TEXT NOT NULL,
                canonical_item_id TEXT NOT NULL, count INTEGER NOT NULL, status TEXT NOT NULL,
                abort_reason TEXT, created_at_utc TEXT NOT NULL, updated_at_utc TEXT NOT NULL)''')
            conn.execute("INSERT INTO transfers VALUES ('old', 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1,"
                         " 'ESCROWED', NULL, 'now', 'now')")
        self.store = TransferStore(self.db_path)
        self.assertEqual(self.store.get('old').status, STATUS_ESCROWED)
        self.assertIsNone(self.store.get('old').idempotency_key)
        self.assertTrue(self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink', 1,
                                                    binding=synthetic_binding(), idempotency_key='new-key'))

    def test_reopen_persists_transfers_and_recovers_in_flight(self) -> None:
        tid1 = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'slime.pink', 1, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-11')
        self.store.lock(tid1)
        self.store.escrow(tid1)

        tid2 = self.store.prepare_transfer('SR1', 's1', 'SR2', 's2', 'plort.pink', 3, binding=synthetic_binding('SR1', 's1', 'SR2', 's2'), idempotency_key='fixture-12')
        self.store.lock(tid2)
        self.store.escrow(tid2)
        self.store.commit(tid2)

        self.store.close()

        store2 = TransferStore(self.db_path)
        try:
            r1 = store2.get(tid1)
            assert r1 is not None
            self.assertEqual(r1.status, STATUS_ESCROWED)
            in_flight = store2.list_in_flight()
            self.assertEqual(len(in_flight), 1)
            self.assertEqual(in_flight[0].transfer_id, tid1)
        finally:
            store2.close()
