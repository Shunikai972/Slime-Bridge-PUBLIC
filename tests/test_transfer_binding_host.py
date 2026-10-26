"""Real HTTP/mailbox tests with explicit synthetic profile and endpoint providers.

These are protocol/security tests, not native game evidence.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from bridge.transfer.store import TransferStore
from transfer_binding_fixtures import PairFixture


class TransferBindingHostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fixture = PairFixture(self.root)
        self.profile = self.fixture.linked('1')
        self.other = self.fixture.linked('2')
        self.fixture.load(self.profile)
        self.store = TransferStore(self.root / 'ledger.sqlite3')
        self.addCleanup(self.store.close)

    def host(self):
        self.assertIn('transfer_bindings', inspect.signature(BridgeHost).parameters,
                      'production host has no profile/pair binding boundary')
        host = BridgeHost(transfer_store=self.store, transfer_bindings=self.fixture.resolver,
                          endpoint_provider=self.fixture.report, endpoint_trust=self.fixture.trust)
        host.__enter__()
        self.addCleanup(host.__exit__, None, None, None)
        for realm, pid in (('SR1', 2001), ('SR2', 2002)):
            self.assertEqual(self.request(host, '/v1/heartbeat', {
                'protocol_version': 1, 'message_type': 'heartbeat', 'realm': realm,
                'request_id': 'synthetic-' + realm, 'pid': pid,
                'status': {'save_id': 'a' if realm == 'SR1' else 'b', 'player_alive': True}})[0], 200)
        return host

    def request(self, host, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(host.url + path, data, {'Content-Type': 'application/json',
                                               'Authorization': 'Bearer ' + host.token})
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def body(self):
        return {'bridge_profile_id': self.profile, 'sender_realm': 'SR1', 'sender_save_id': 'a',
                'receiver_realm': 'SR2', 'receiver_save_id': 'b',
                'canonical_item_id': 'slime.pink', 'count': 1, 'idempotency_key': 'one-action'}

    def prepared(self, phase='PREPARED'):
        binding = self.fixture.resolver.resolve(self.profile, 'SR1', 'a', 'SR2', 'b')
        tid = self.store.prepare_transfer('SR1', 'a', 'SR2', 'b', 'slime.pink',
                                          idempotency_key='setup-action', binding=binding)
        if phase in ('LOCKED', 'ESCROWED', 'REFUND_PENDING'):
            self.store.lock(tid)
        if phase in ('ESCROWED', 'REFUND_PENDING'):
            self.store.escrow(tid)
        if phase == 'REFUND_PENDING':
            self.store.begin_refund(tid, 'synthetic no-delivery setup')
        return tid

    def receipt(self, tid):
        # Deliberately fake wire evidence. No disk proof/runtime claim is made by this fixture.
        return {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                'op_id': tid + ':remove', 'kind': 'remove', 'item': 'slime.pink', 'count': 1,
                'pid': 2001, 'save_id': 'a', 'ok': True, 'applied': True,
                'uncertain': False, 'persisted': True, 'verified': True,
                'before': 3, 'after': 2, 'save_file': 'a_1.sav', 'sha256': 'a' * 64, 'reason': ''}

    def test_no_authority_provider_fails_closed_instead_of_accepting_admin_bearer(self):
        with BridgeHost(transfer_store=self.store) as host:
            status, _ = self.request(host, '/v1/transfer/prepare', self.body())
            self.assertEqual(status, 409)
            self.assertEqual(self.store.list_in_flight(), [])

    def test_missing_or_unknown_profile_is_rejected_without_row_or_command(self):
        host = self.host()
        for pid in (None, '', 'unknown-profile'):
            with self.subTest(pid=pid):
                status, _ = self.request(host, '/v1/transfer/prepare', {**self.body(), 'bridge_profile_id': pid})
                self.assertEqual(status, 409)
        self.assertEqual(self.store.list_in_flight(), [])
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR1')[1]['command'], None)

    def test_same_save_id_foreign_receiver_installation_cannot_prepare(self):
        host = self.host()
        self.fixture.load(self.other, 'SR2')
        status, _ = self.request(host, '/v1/transfer/prepare', self.body())
        self.assertEqual(status, 409)
        self.assertEqual(self.store.list_in_flight(), [])
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR1')[1]['command'], None)
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR2')[1]['command'], None)

    def test_prepare_snapshots_authority_and_returns_same_id_for_same_action(self):
        host = self.host()
        status, result = self.request(host, '/v1/transfer/prepare', self.body())
        self.assertEqual(status, 200, result)
        row = self.store.get(result['transfer_id'])
        self.assertEqual(row.bridge_profile_id, self.profile)
        self.assertEqual(row.receiver_installation_id, 'synthetic-install-SR2-1')
        status, repeated = self.request(host, '/v1/transfer/prepare', self.body())
        self.assertEqual((status, repeated['transfer_id']), (200, row.transfer_id))

    def test_untrusted_or_nonruntime_endpoint_report_does_not_authorize_mutation(self):
        host = self.host()
        original = self.fixture.report
        for changes in ({'evidence': 'SYNTHETIC'}, {'process_running': False},
                        {'load_callback_returned': False}, {'scene_settled': False},
                        {'provider': {'provider_id': 'untrusted', 'provider_version': '1'}},
                        {'observed_at': '2000-01-01T00:00:00Z'}):
            host.endpoint_provider = lambda realm, session, changes=changes: {**original(realm, session), **changes}
            with self.subTest(changes=changes):
                status, _ = self.request(host, '/v1/transfer/prepare', self.body())
                self.assertEqual(status, 409)
        self.assertEqual(self.store.list_in_flight(), [])

    def test_http_finalization_without_native_receipt_cannot_advance_state(self):
        host = self.host()
        tid = self.prepared('LOCKED')
        for action in ('escrow', 'commit', 'refund', 'abort'):
            with self.subTest(action=action):
                status, _ = self.request(host, '/v1/transfer/' + action, {'transfer_id': tid})
                self.assertEqual(status, 409)
                self.assertEqual(self.store.get(tid).status, 'LOCKED')

    def test_arbitrary_remove_add_operation_cannot_bypass_durable_binding(self):
        host = self.host()
        for kind in ('remove', 'add'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                host.enqueue_command('SR1', kind, 'slime.pink', 1, op_id='arbitrary-' + kind, save_id='a')
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR1')[1]['command'], None)

    def test_wrong_realm_save_item_or_count_does_not_match_bound_mutation(self):
        host = self.host()
        tid = self.prepared('LOCKED')
        for realm, item, count, save in (('SR2', 'slime.pink', 1, 'b'),
                                         ('SR1', 'plort.pink', 1, 'a'),
                                         ('SR1', 'slime.pink', 2, 'a'),
                                         ('SR1', 'slime.pink', 1, 'other')):
            with self.subTest(args=(realm, item, count, save)), self.assertRaises(ValueError):
                host.enqueue_command(realm, 'remove', item, count, op_id=tid + ':remove', save_id=save)

    def test_endpoint_change_between_enqueue_and_poll_preserves_pending_command_without_delivery(self):
        host = self.host()
        tid = self.prepared('LOCKED')
        op = host.enqueue_command('SR1', 'remove', 'slime.pink', 1, op_id=tid + ':remove', save_id='a')
        self.fixture.load(self.other, 'SR1')
        self.assertIsNone(self.request(host, '/v1/commands/next?realm=SR1')[1]['command'])
        self.assertNotIn(op, host._issued)
        self.assertEqual(self.store.get(tid).status, 'LOCKED')
        self.fixture.load(self.profile, 'SR1')
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR1')[1]['command']['op_id'], op)

    def test_endpoint_change_between_delivery_and_result_cannot_poison_receipt_cache(self):
        host = self.host()
        tid = self.prepared('LOCKED')
        op = host.enqueue_command('SR1', 'remove', 'slime.pink', 1, op_id=tid + ':remove', save_id='a')
        self.assertEqual(self.request(host, '/v1/commands/next?realm=SR1')[1]['command']['op_id'], op)
        self.fixture.load(self.other, 'SR1')
        self.assertEqual(self.request(host, '/v1/commands/result', self.receipt(tid))[0], 409)
        self.assertNotIn(op, host._results)
        self.fixture.load(self.profile, 'SR1')
        self.assertEqual(self.request(host, '/v1/commands/result', self.receipt(tid))[0], 200)

    def test_native_receipt_and_current_pair_are_required_for_http_escrow(self):
        host = self.host()
        tid = self.prepared('LOCKED')
        host.enqueue_command('SR1', 'remove', 'slime.pink', 1, op_id=tid + ':remove', save_id='a')
        self.request(host, '/v1/commands/next?realm=SR1')
        self.assertEqual(self.request(host, '/v1/commands/result', self.receipt(tid))[0], 200)
        self.fixture.load(self.other, 'SR1')
        self.assertEqual(self.request(host, '/v1/transfer/escrow', {'transfer_id': tid})[0], 409)
        self.assertEqual(self.store.get(tid).status, 'LOCKED')
        self.fixture.load(self.profile, 'SR1')
        self.assertEqual(self.request(host, '/v1/transfer/escrow', {'transfer_id': tid})[0], 200)
        self.assertEqual(self.store.get(tid).status, 'ESCROWED')

    def test_legacy_unbound_http_finalization_is_blocked_and_keeps_row(self):
        host = self.host()
        with closing(sqlite3.connect(self.store.database_path)) as db, db:
            db.execute("INSERT INTO transfers (transfer_id,sender_realm,sender_save_id,receiver_realm,receiver_save_id,"
                       "canonical_item_id,count,status,created_at_utc,updated_at_utc)"
                       " VALUES ('legacy','SR1','a','SR2','b','slime.pink',1,'ESCROWED','now','now')")
        for action in ('commit', 'refund'):
            self.assertEqual(self.request(host, '/v1/transfer/' + action, {'transfer_id': 'legacy'})[0], 409)
        self.assertEqual(self.store.get('legacy').status, 'ESCROWED')
        self.assertIsNone(self.store.get('legacy').bridge_profile_id)


if __name__ == '__main__':
    unittest.main()
