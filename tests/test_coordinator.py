from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bridge.transfer.coordinator import journal_op_state, refund_after_receiver_loss, resume, transfer
from bridge.transfer.binding import TransferBindingError, binding_for_row
from transfer_binding_fixtures import synthetic_binding
from bridge.transfer.store import (STATUS_ABORTED, STATUS_COMMITTED, STATUS_ESCROWED, STATUS_REFUNDED,
                                   TransferStore)


class FakeGames:
    """Two fake plugins with native-like counts and an at-most-once op journal per realm."""

    def __init__(self, counts, capacity=30, refuse_add=(), drop=()):
        self.counts = {realm: dict(items) for realm, items in counts.items()}
        self.capacity = capacity
        self.refuse_add = set(refuse_add)
        self.drop = set(drop)          # op suffixes whose result never arrives (crash)
        self.journal: dict[str, dict] = {}
        self.applied: list[str] = []
        self._pending: dict[str, tuple] = {}

    def bind_transfer_store(self, store):
        # Unit-only fake transport; production BridgeHost binds its polling path to this ledger.
        self.transfer_store = store
        return True

    def resolve_transfer_binding(self, profile_id, sender, sender_save, receiver, receiver_save):
        # Explicit structural authority for these unit-only fake plugins, not native evidence.
        if profile_id != 'unit-only-profile':
            raise TransferBindingError('synthetic profile required')
        return synthetic_binding(sender, sender_save, receiver, receiver_save)

    def validate_transfer(self, row, *, realms=None):
        recorded = binding_for_row(row)
        expected = synthetic_binding(row.sender_realm, row.sender_save_id, row.receiver_realm, row.receiver_save_id)
        if recorded != expected:
            raise TransferBindingError('synthetic channel does not own the transfer')
        return recorded

    def enqueue_command(self, realm, kind, item, count=1, op_id=None, save_id=None):
        self._pending[op_id] = (realm, kind, item, count, save_id)
        return op_id

    def wait_result(self, op_id, timeout):
        realm, kind, item, count, save_id = self._pending.pop(op_id)
        if op_id in self.journal:
            return dict(self.journal[op_id])
        before = self.counts[realm].get(item, 0)
        result = {'realm': realm, 'op_id': op_id, 'kind': kind, 'item': item, 'count': count,
                  'save_id': save_id, 'ok': True, 'applied': False, 'uncertain': False,
                    'before': before, 'after': before, 'can_add': before + count <= self.capacity}
        if kind == 'remove':
            if before < count:
                result.update(ok=False, reason='insufficient')
            else:
                self.counts[realm][item] = before - count
                result.update(applied=True, after=before - count)
        elif kind == 'add':
            if realm in self.refuse_add or before + count > self.capacity:
                result.update(ok=False, reason='full')
            else:
                self.counts[realm][item] = before + count
                result.update(applied=True, after=before + count)
        if kind != 'query':
            if result['applied']:
                # Explicit fake-plugin fixture: simulated native disk readback proof.
                result.update(persisted=True, verified=True, sha256='a' * 64, save_file='fake-native.sav')
            self.journal[op_id] = result
            if result['applied']:
                self.applied.append(op_id)
        if any(op_id.endswith(suffix) for suffix in self.drop):
            self.drop = {s for s in self.drop if not op_id.endswith(s)}
            return None
        return dict(result)

    def cancel_command(self, realm, op_id):
        self._pending.pop(op_id, None)
        return True


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / 'transfers.db'
        self.store = TransferStore(self.db)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_happy_path_moves_exactly_one(self):
        games = FakeGames({'SR1': {'slime.pink': 3}, 'SR2': {'slime.pink': 0}})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-1')
        self.assertEqual(out.status, STATUS_COMMITTED)
        self.assertEqual(games.counts['SR1']['slime.pink'], 2)
        self.assertEqual(games.counts['SR2']['slime.pink'], 1)

    def test_lost_logical_response_retry_uses_same_row_without_second_transfer(self):
        games = FakeGames({'SR1': {'slime.pink': 3}, 'SR2': {'slime.pink': 0}})
        args = (self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1)
        first = transfer(*args, bridge_profile_id='unit-only-profile', idempotency_key='one-user-action')
        self.store.close()
        self.store = TransferStore(self.db)
        second = transfer(self.store, games, *args[2:], bridge_profile_id='unit-only-profile', idempotency_key='one-user-action')
        self.assertEqual((first.transfer_id, first.status), (second.transfer_id, second.status))
        self.assertEqual(games.counts['SR1']['slime.pink'], 2)
        self.assertEqual(games.counts['SR2']['slime.pink'], 1)

    def test_sender_without_item_aborts_and_changes_nothing(self):
        games = FakeGames({'SR1': {'slime.pink': 0}, 'SR2': {'slime.pink': 0}})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-2')
        self.assertEqual(out.status, STATUS_ABORTED)
        self.assertEqual(games.applied, [])

    def test_receiver_refusal_refunds_sender(self):
        games = FakeGames({'SR1': {'plort.pink': 1}, 'SR2': {'plort.pink': 0}}, refuse_add={'SR2'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'plort.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-3')
        self.assertEqual(out.status, STATUS_REFUNDED)
        self.assertEqual(games.counts['SR1']['plort.pink'], 1)
        self.assertEqual(games.counts['SR2']['plort.pink'], 0)

    def test_lost_receiver_ack_then_host_restart_never_duplicates(self):
        games = FakeGames({'SR1': {'slime.pink': 2}, 'SR2': {'slime.pink': 5}}, drop={':add'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-4')
        self.assertEqual(out.status, STATUS_ESCROWED)
        self.store.close()
        self.store = TransferStore(self.db)  # host restart
        (row,) = self.store.list_in_flight()
        out = resume(self.store, games, row.transfer_id)
        self.assertEqual(out.status, STATUS_COMMITTED)
        self.assertEqual(games.counts['SR1']['slime.pink'], 1)
        self.assertEqual(games.counts['SR2']['slime.pink'], 6)
        self.assertEqual(sum(1 for op in games.applied if op.endswith(':add')), 1)

    def test_lost_sender_ack_then_restart_removes_once(self):
        games = FakeGames({'SR1': {'slime.pink': 2}, 'SR2': {'slime.pink': 0}}, drop={':remove'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-5')
        self.assertNotEqual(out.status, STATUS_COMMITTED)
        out = resume(self.store, games, out.transfer_id)
        self.assertEqual(out.status, STATUS_COMMITTED)
        self.assertEqual(games.counts['SR1']['slime.pink'], 1)
        self.assertEqual(games.counts['SR2']['slime.pink'], 1)

    def test_refund_after_receiver_loss_is_fail_closed(self):
        games = FakeGames({'SR1': {'slime.pink': 1}, 'SR2': {'slime.pink': 0}}, drop={':add'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-6')
        self.assertEqual(out.status, STATUS_ESCROWED)
        refused = refund_after_receiver_loss(self.store, games, out.transfer_id, receiver_dead=False,
                                             receiver_journal_state='absent')
        self.assertEqual(refused.status, STATUS_ESCROWED)
        refused = refund_after_receiver_loss(self.store, games, out.transfer_id, receiver_dead=True,
                                             receiver_journal_state='intent')
        self.assertEqual(refused.status, STATUS_ESCROWED)
        self.assertEqual(games.counts['SR1']['slime.pink'], 0)

    def test_refund_after_receiver_loss_recredits_sender_once(self):
        games = FakeGames({'SR1': {'slime.pink': 1}, 'SR2': {'slime.pink': 0}}, drop={':add'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 1, bridge_profile_id='unit-only-profile', idempotency_key='fixture-7')
        games.counts['SR2']['slime.pink'] = 0  # receiver lost (fake applied the add; model it never did)
        games.journal.pop(out.transfer_id + ':add', None)
        for _ in range(2):
            done = refund_after_receiver_loss(self.store, games, out.transfer_id, receiver_dead=True,
                                              receiver_journal_state='absent')
        self.assertEqual(done.status, STATUS_REFUNDED)
        self.assertEqual(games.counts['SR1']['slime.pink'], 1)

    def test_result_for_other_realm_or_item_is_ignored(self):
        from bridge.transfer.coordinator import run_command

        class Liar:
            def enqueue_command(self, *a, **k): return k['op_id']
            def wait_result(self, op_id, timeout):
                return {'realm': 'SR2', 'op_id': op_id, 'ok': True, 'applied': True, 'before': 1, 'after': 0}

        self.assertIsNone(run_command(Liar(), 'SR1', 'remove', 'slime.pink', 1, 'x:remove', None, 1))

    def test_journal_op_state(self):
        import json
        path = Path(self._tmp.name) / 'ops.journal'
        self.assertEqual(journal_op_state(path, 'x:add'), 'unknown')
        path.write_text('I\tx:add\n', encoding='utf-8')
        self.assertEqual(journal_op_state(path, 'x:add'), 'intent')
        receipt = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR2',
                   'pid': 123, 'op_id': 'x:add', 'kind': 'add', 'item': 'slime.pink',
                   'save_id': 'unit-save', 'count': 1, 'ok': False, 'applied': False}
        path.write_text('I\tx:add\nD\tx:add\t' + json.dumps(receipt) + '\n', encoding='utf-8')
        self.assertEqual(journal_op_state(path, 'x:add'), 'done')
        self.assertEqual(journal_op_state(path, 'y:add'), 'absent')

    def test_missing_or_false_native_proof_never_advances_sender_receiver_or_refund(self):
        for suffix, expected in [(':remove', 'LOCKED'), (':add', 'ESCROWED'),
                                 (':refund', 'REFUND_PENDING')]:
            for change in ({'persisted': False}, {'verified': False}, {'sha256': 'bad'},
                           {'save_file': ''}, {'before': True}):
                with self.subTest(suffix=suffix, change=change):
                    games = FakeGames({'SR1': {'slime.pink': 1}, 'SR2': {'slime.pink': 0}},
                                      refuse_add={'SR2'} if suffix == ':refund' else ())
                    original = games.wait_result
                    def altered(op_id, timeout):
                        result = original(op_id, timeout)
                        if result is not None and op_id.endswith(suffix):
                            result.update(change)
                        return result
                    games.wait_result = altered
                    out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink',
                                   bridge_profile_id='unit-only-profile', idempotency_key=f'proof-{suffix}-{change}')
                    self.assertEqual(out.status, expected)

    def test_query_count_is_transfer_quantity_and_result_fields_are_required(self):
        from bridge.transfer.coordinator import run_command
        games = FakeGames({'SR1': {'slime.pink': 2}, 'SR2': {'slime.pink': 0}})
        requested = []
        original = games.enqueue_command
        def record(*args, **kwargs):
            requested.append(args)
            return original(*args, **kwargs)
        games.enqueue_command = record
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink', 2,
                       bridge_profile_id='unit-only-profile', idempotency_key='quantity')
        self.assertEqual(out.status, STATUS_COMMITTED)
        self.assertEqual([call[3] for call in requested[:2]], [2, 2])
        for field in ('realm', 'op_id', 'kind', 'item', 'count', 'save_id'):
            class Incomplete:
                def enqueue_command(self, *args, **kwargs): return kwargs['op_id']
                def wait_result(self, op_id, timeout):
                    value = {'realm': 'SR1', 'op_id': op_id, 'kind': 'query', 'item': 'slime.pink',
                             'count': 2, 'save_id': 'a', 'ok': True}
                    value.pop(field)
                    return value
            with self.subTest(field=field):
                self.assertIsNone(run_command(Incomplete(), 'SR1', 'query', 'slime.pink', 2,
                                              'query-required', 'a', 1))

    def test_refund_decision_survives_restart_and_fences_receiver(self):
        games = FakeGames({'SR1': {'slime.pink': 1}, 'SR2': {'slime.pink': 0}}, drop={':add'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink',
                       bridge_profile_id='unit-only-profile', idempotency_key='refund-crash')
        self.assertEqual(out.status, STATUS_ESCROWED)
        games.journal.pop(out.transfer_id + ':add', None)
        games.counts['SR2']['slime.pink'] = 0
        self.store.begin_refund(out.transfer_id, 'verified receiver loss')
        self.store.close()
        self.store = TransferStore(self.db)
        done = resume(self.store, games, out.transfer_id)
        self.assertEqual(done.status, STATUS_REFUNDED)
        self.assertFalse(any(op.endswith(':add') for op in games.applied[2:]))

    def test_resume_and_refund_serialize_across_stores(self):
        import threading
        other = TransferStore(self.db)
        games = FakeGames({'SR1': {'slime.pink': 1}, 'SR2': {'slime.pink': 0}}, drop={':add'})
        out = transfer(self.store, games, 'SR1', 'a', 'SR2', 'b', 'slime.pink',
                       bridge_profile_id='unit-only-profile', idempotency_key='gated-race')
        games.journal.pop(out.transfer_id + ':add', None)
        games.counts['SR2']['slime.pink'] = 0
        entered, release = threading.Event(), threading.Event()
        original = games.cancel_command
        def gated(realm, op):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(realm, op)
        games.cancel_command = gated
        refund_result = []
        resume_result = []
        try:
            a = threading.Thread(target=lambda: refund_result.append(refund_after_receiver_loss(
                self.store, games, out.transfer_id, True, 'absent')))
            b = threading.Thread(target=lambda: resume_result.append(resume(other, games, out.transfer_id)))
            a.start()
            self.assertTrue(entered.wait(3))
            b.start()
            release.set()
            a.join(3)
            b.join(3)
            self.assertEqual(refund_result[0].status, STATUS_REFUNDED)
            self.assertEqual(resume_result[0].status, STATUS_REFUNDED)
            self.assertEqual(games.counts['SR1']['slime.pink'], 1)
            self.assertEqual(games.counts['SR2']['slime.pink'], 0)
        finally:
            release.set()
            other.close()


if __name__ == '__main__':
    unittest.main()
