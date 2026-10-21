"""Fail-closed regressions: local synthetic fixtures, never real game proof."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from bridge.transfer.coordinator import journal_op_result, journal_op_state, transfer
from bridge.transfer.store import InvalidStateTransitionError, TransferStore
from transfer_binding_fixtures import synthetic_binding
from transfer_host_fixture import SyntheticTransferHost


class TransferSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = TransferStore(self.root / 'ledger.db')
        self.binding = synthetic_binding()
        self.addCleanup(self.store.close)

    def test_missing_key_is_refused_before_any_native_command(self):
        class NoCommands:
            def enqueue_command(self, *args, **kwargs):
                raise AssertionError('unkeyed request reached native command')
        with self.assertRaises(ValueError):
            self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink', binding=self.binding)
        with self.assertRaises(ValueError):
            transfer(self.store, NoCommands(), 'SR1', 'a', 'SR2', 'b', 'slime.pink')
        self.assertEqual(self.store.list_in_flight(), [])

    def test_http_prepare_requires_caller_key_and_retries_return_same_id(self):
        with SyntheticTransferHost(self.root, self.store) as fixture:
            host = fixture.host
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + host.token}
            body = {'bridge_profile_id': fixture.profile_id, 'sender_realm': 'SR1', 'sender_save_id': 'a', 'receiver_realm': 'SR2',
                    'receiver_save_id': 'b', 'canonical_item_id': 'slime.pink', 'count': 1}
            def prepare(payload):
                with urlopen(Request(host.url + '/v1/transfer/prepare', json.dumps(payload).encode(), headers), timeout=3) as response:
                    return json.loads(response.read())['transfer_id']
            with self.assertRaises(HTTPError) as missing:
                prepare(body)
            self.assertEqual(missing.exception.code, 400)
            body['idempotency_key'] = 'one-logical-controller-action'
            first = prepare(body)
            self.assertEqual(prepare(body), first)
            with self.assertRaises(HTTPError) as conflict:
                prepare({**body, 'count': 2})
            self.assertEqual(conflict.exception.code, 400)

    def test_dispatched_receiver_cannot_be_refunded_even_after_host_reopen(self):
        with SyntheticTransferHost(self.root, self.store) as fixture:
            tid = fixture.prepare(key='delivered')
            self.store.lock(tid)
            self.store.escrow(tid)
            op = fixture.enqueue(tid, 'add', 'SR2', 'add', 'slime.pink', 1, 'b')
            self.assertEqual(fixture.poll('SR2')['op_id'], op)
            with self.assertRaises(InvalidStateTransitionError):
                self.store.begin_refund(tid, 'caller says dead and absent')
        with TransferStore(self.root / 'ledger.db') as reopened:
            with self.assertRaises(InvalidStateTransitionError):
                reopened.begin_refund(tid, 'host lost volatile dispatch history')
            self.assertEqual(reopened.get(tid).status, 'ESCROWED')

    def test_delivery_and_refund_cas_have_exactly_one_winner_across_stores(self):
        import concurrent.futures
        import threading
        tid = self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                          idempotency_key='dispatch-race', binding=self.binding)
        self.store.lock(tid)
        self.store.escrow(tid)
        barrier = threading.Barrier(2)
        command = {'op_id': tid + ':add', 'kind': 'add', 'item': 'slime.pink',
                   'count': 1, 'save_id': 'b'}
        with TransferStore(self.root / 'ledger.db') as other:
            def deliver():
                barrier.wait(timeout=3)
                return self.store.mark_receiver_dispatched(tid, 'SR2', command)
            def refund():
                barrier.wait(timeout=3)
                try:
                    other.begin_refund(tid, 'not yet delivered')
                    return True
                except InvalidStateTransitionError:
                    return False
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                delivery, decision = pool.submit(deliver), pool.submit(refund)
                self.assertNotEqual(delivery.result(timeout=4), decision.result(timeout=4))

    def test_dispatched_refund_requires_bound_clean_refusal_and_records_it(self):
        tid = self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                          idempotency_key='refused', binding=self.binding)
        self.store.lock(tid)
        self.store.escrow(tid)
        command = {'op_id': tid + ':add', 'kind': 'add', 'item': 'slime.pink', 'count': 1, 'save_id': 'b'}
        self.assertTrue(self.store.mark_receiver_dispatched(tid, 'SR2', command))
        refusal = {**command, 'realm': 'SR2', 'ok': False, 'applied': False,
                   'uncertain': False, 'before': 0, 'after': 0}
        for bad in ({'save_id': 'wrong'}, {'count': True}, {'before': -1, 'after': -1},
                    {'applied': True}, {'uncertain': True}, {'op_id': 'other:add'}):
            with self.subTest(bad=bad), self.assertRaises(InvalidStateTransitionError):
                self.store.begin_refund(tid, 'bad proof', receiver_refusal={**refusal, **bad})
        self.store.begin_refund(tid, 'authenticated clean refusal', receiver_refusal=refusal)
        self.assertFalse(self.store.mark_receiver_dispatched(tid, 'SR2', command))
        with TransferStore(self.root / 'ledger.db') as reopened:
            self.assertEqual(reopened.get(tid).status, 'REFUND_PENDING')

    def test_unknown_legacy_delivery_history_is_not_a_refund_permission(self):
        import sqlite3
        from contextlib import closing
        tid = self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                          idempotency_key='legacy', binding=self.binding)
        self.store.lock(tid)
        self.store.escrow(tid)
        self.store.close()
        with closing(sqlite3.connect(self.root / 'ledger.db')) as db, db:
            db.execute('DROP TABLE receiver_deliveries')
        with TransferStore(self.root / 'ledger.db') as reopened:
            with self.assertRaises(InvalidStateTransitionError):
                reopened.begin_refund(tid, 'legacy no-history ledger')

    def test_unbound_channel_is_refused_before_creating_ledger_or_native_effect(self):
        class Unbound:
            def enqueue_command(self, *args, **kwargs):
                raise AssertionError('unsafe channel reached native command')
            def wait_result(self, *args, **kwargs):
                raise AssertionError('unsafe channel awaited native effect')
        with self.assertRaises(ValueError):
            transfer(self.store, Unbound(), 'SR1', 'a', 'SR2', 'b', 'slime.pink',
                     idempotency_key='unbound')
        self.assertEqual(self.store.list_in_flight(), [])

    def test_host_commands_match_durable_transfer_suffix_endpoint_payload_and_phase(self):
        with SyntheticTransferHost(self.root, self.store) as fixture:
            tid = fixture.prepare(key='command-provenance', count=2)
            with self.assertRaises(ValueError):
                fixture.host.enqueue_command('SR1', 'remove', 'slime.pink', 2,
                                             op_id=tid + ':remove', save_id='a')

            self.store.lock(tid)
            invalid_remove = (
                ('SR2', 'remove', 'slime.pink', 2, tid + ':remove', 'a'),
                ('SR1', 'remove', 'slime.pink', 2, tid + ':add', 'a'),
                ('SR1', 'remove', 'plort.pink', 2, tid + ':remove', 'a'),
                ('SR1', 'remove', 'slime.pink', 1, tid + ':remove', 'a'),
                ('SR1', 'remove', 'slime.pink', 2, tid + ':remove', 'wrong-save'),
            )
            for realm, kind, item, count, op_id, save_id in invalid_remove:
                with self.subTest(realm=realm, kind=kind, item=item, count=count,
                                  op_id=op_id, save_id=save_id), self.assertRaises(ValueError):
                    fixture.host.enqueue_command(realm, kind, item, count,
                                                 op_id=op_id, save_id=save_id)

            fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 2, 'a')
            self.store.escrow(tid)
            fixture.enqueue(tid, 'add', 'SR2', 'add', 'slime.pink', 2, 'b')
            self.store.begin_refund(tid, 'synthetic unpolled receiver add')

            for op_id, realm, save_id in ((tid + ':add', 'SR2', 'b'),
                                          (tid + ':refund', 'SR1', 'wrong-save')):
                with self.subTest(op_id=op_id, realm=realm, save_id=save_id), self.assertRaises(ValueError):
                    fixture.host.enqueue_command(realm, 'add', 'slime.pink', 2,
                                                 op_id=op_id, save_id=save_id)
            fixture.enqueue(tid, 'refund', 'SR1', 'add', 'slime.pink', 2, 'a')

    def test_rebinding_ledger_reopen_keeps_dispatch_fence_and_rejects_other_database(self):
        with BridgeHost() as host:
            self.assertTrue(host.bind_transfer_store(self.store))
            tid = self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                              idempotency_key='binding', binding=self.binding)
            self.store.lock(tid)
            self.store.escrow(tid)
            self.store.close()
            with TransferStore(self.root / 'ledger.db') as reopened:
                self.assertTrue(host.bind_transfer_store(reopened))
                self.assertIs(host.transfer_store, reopened)
                with TransferStore(self.root / 'other.db') as other:
                    with self.assertRaises(ValueError):
                        host.bind_transfer_store(other)

    def test_incomplete_native_success_is_rejected_without_poisoning_mailbox(self):
        with SyntheticTransferHost(self.root, self.store) as fixture:
            host = fixture.host
            tid = fixture.prepare(key='incomplete-result')
            self.store.lock(tid)
            headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + host.realm_token('SR1')}
            op = fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 1, 'a')
            fixture.poll('SR1')
            complete = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                        'pid': os.getpid(), 'op_id': op, 'kind': 'remove', 'item': 'slime.pink',
                        'count': 1, 'save_id': 'a', 'ok': True, 'applied': True, 'before': 1, 'after': 0,
                        'uncertain': False, 'persisted': True, 'verified': True,
                        'sha256': 'a' * 64, 'save_file': 'a_0'}
            for field in ('before', 'after', 'uncertain', 'persisted', 'verified', 'sha256', 'save_file'):
                incomplete = {key: value for key, value in complete.items() if key != field}
                with self.subTest(field=field), self.assertRaises(HTTPError) as refused:
                    urlopen(Request(host.url + '/v1/commands/result', json.dumps(incomplete).encode(), headers), timeout=3)
                self.assertEqual(refused.exception.code, 400)
                self.assertIsNone(host.wait_result(op, 0))
                with urlopen(Request(host.url + '/v1/commands/next?realm=SR1', headers=headers), timeout=3) as response:
                    self.assertEqual(json.loads(response.read())['command']['op_id'], op)
            with urlopen(Request(host.url + '/v1/commands/result', json.dumps(complete).encode(), headers), timeout=3) as response:
                self.assertEqual(response.status, 200)

    def test_duplicate_blank_and_out_of_order_journal_records_are_unknown(self):
        path = self.root / 'ambiguous.journal'
        receipt = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                   'pid': 123, 'op_id': 'one:add', 'kind': 'add', 'item': 'slime.pink', 'count': 1,
                   'save_id': 'a', 'ok': False, 'applied': False}
        done = 'D\tone:add\t' + json.dumps(receipt) + '\n'
        for value in ('\n\n', 'I\tone:add\nI\tone:add\n', done + done, done + 'I\tone:add\n'):
            with self.subTest(value=value):
                path.write_text(value, encoding='utf-8')
                self.assertEqual(journal_op_state(path, 'one:add'), 'unknown')
                self.assertEqual(journal_op_state(path, 'other:add'), 'unknown')
                self.assertIsNone(journal_op_result(path, 'one:add'))

    def test_missing_corrupt_or_conflicting_journal_is_unknown_not_absent(self):
        path = self.root / 'ops.jsonl'
        self.assertEqual(journal_op_state(path, 'one:add'), 'unknown')
        for raw in (b'\xff\n', b'I\tone:add', b'garbage\n', b'I\tbad id\n',
                    b'D\tone:add\t{}\n',
                    b'I\tone:add\nD\tone:add\t{"op_id":"wrong"}\n'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                self.assertEqual(journal_op_state(path, 'one:add'), 'unknown')
                self.assertIsNone(journal_op_result(path, 'one:add'))
        path.write_bytes(b'')
        self.assertEqual(journal_op_state(path, 'one:add'), 'absent')
        path.write_bytes(b'I\tone:add\n')
        self.assertEqual(journal_op_state(path, 'one:add'), 'intent')


if __name__ == '__main__':
    unittest.main()
