"""Second-review HTTP concurrency/late-result regressions; synthetic native receipts only."""
import json
import os
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.counterpart.realms import wire_adapter
from bridge.host import server
from test_commands import TOKEN, _next, _result


class CreationAtomicityTests(unittest.TestCase):
    @staticmethod
    def receipt(cid, op, *, realm='SR1', kind='create_save', absence=False):
        return {'protocol_version': 1, 'message_type': 'command_result', 'realm': realm,
                'op_id': op, 'kind': kind, 'creation_id': cid, 'save_id': 'sb1_' + cid,
                'pid': os.getpid(), 'ok': True, 'applied': not absence, 'persisted': not absence,
                'verified': not absence, 'uncertain': False, 'before': 0 if absence else 1,
                'after': 0 if absence else 1,
                **({'can_create': True} if absence else
                   {'save_file': 'sb1_' + cid + '_0', 'sha256': 'a' * 64})}

    def test_session_snapshot_and_result_acceptance_have_one_linearization_order(self):
        with server.BridgeHost(token=TOKEN) as host:
            cid, pid = '1' * 32, os.getpid()
            op = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=pid)
            self.assertEqual(_next(host, 'SR1')['op_id'], op)
            snapshot_taken = threading.Event()
            release_receipt = threading.Event()
            heartbeat_parsed = threading.Event()
            heartbeat_finished = threading.Event()
            outcomes, errors = {}, []
            original_session = host._live_session
            original_validator = server._valid_heartbeat

            def gated_session(realm):
                snapshot = original_session(realm)
                snapshot_taken.set()
                if not release_receipt.wait(4):
                    raise RuntimeError('synthetic receipt barrier timed out')
                return snapshot

            def observe_heartbeat(data):
                valid = original_validator(data)
                if data.get('request_id') == 'synthetic-session-rotation':
                    heartbeat_parsed.set()
                return valid

            def post_receipt():
                try:
                    outcomes['receipt'] = _result(host, **self.receipt(cid, op))
                except HTTPError as exc:
                    outcomes['receipt'] = exc.code
                except Exception as exc:
                    errors.append(exc)

            def rotate_session():
                try:
                    heartbeat = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': 'SR1',
                                 'request_id': 'synthetic-session-rotation', 'pid': pid + 1}
                    headers = {'Authorization': 'Bearer ' + host.realm_token('SR1'),
                               'Content-Type': 'application/json'}
                    with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(heartbeat).encode(), headers),
                                 timeout=4) as response:
                        outcomes['heartbeat'] = response.status
                except Exception as exc:
                    errors.append(exc)
                finally:
                    heartbeat_finished.set()

            receipt_thread = threading.Thread(target=post_receipt)
            heartbeat_thread = threading.Thread(target=rotate_session)
            host._live_session = gated_session
            try:
                with patch.object(server, '_valid_heartbeat', observe_heartbeat):
                    receipt_thread.start()
                    self.assertTrue(snapshot_taken.wait(3))
                    heartbeat_thread.start()
                    self.assertTrue(heartbeat_parsed.wait(3))
                    rotated_before_receipt_release = heartbeat_finished.wait(0.25)
                    release_receipt.set()
                    receipt_thread.join(4)
                    heartbeat_thread.join(4)
                    self.assertFalse(receipt_thread.is_alive())
                    self.assertFalse(heartbeat_thread.is_alive())
                    self.assertEqual(errors, [])
                    self.assertEqual(outcomes.get('heartbeat'), 200)
                    if rotated_before_receipt_release:
                        self.assertEqual(outcomes.get('receipt'), 409,
                                         'a completed newer heartbeat invalidates the old-session snapshot')
                        self.assertIsNone(host.wait_result(op, 0))
                    else:
                        self.assertEqual(outcomes.get('receipt'), 200)
                        accepted = host.wait_result(op, 0)
                        assert accepted is not None
                        self.assertEqual(accepted['pid'], pid)
                    current = original_session('SR1')
                    assert current is not None
                    self.assertEqual(current[0], pid + 1)
            finally:
                release_receipt.set()
                for thread in (receipt_thread, heartbeat_thread):
                    if thread.ident is not None:
                        thread.join(5)
                host._live_session = original_session

    def test_issued_create_completion_remains_eligible_after_reconcile_supersedes_delivery(self):
        with server.BridgeHost(token=TOKEN) as host:
            cid = '2' * 32
            create = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            self.assertEqual(_next(host, 'SR1')['op_id'], create)
            reconcile = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=os.getpid())
            reply = self.receipt(cid, create)
            self.assertEqual(_result(host, **reply), 200)
            self.assertEqual(_result(host, **reply), 200)
            accepted = host.wait_result(create, 0)
            assert accepted is not None
            self.assertEqual(accepted['sha256'], reply['sha256'])
            self.assertEqual(_next(host, 'SR1')['op_id'], reconcile)

    def test_undelivered_create_stays_ineligible_after_reconciliation_supersedes_it(self):
        with server.BridgeHost(token=TOKEN) as host:
            cid = '3' * 32
            create = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            reconcile = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=os.getpid())
            self.assertEqual(_next(host, 'SR1')['op_id'], reconcile)
            with self.assertRaises(HTTPError) as refused:
                _result(host, **self.receipt(cid, create))
            self.assertEqual(refused.exception.code, 409)
            self.assertIsNone(host.wait_result(create, 0))

    def test_absence_host_rejects_nonempty_filename_or_hash_without_poisoning_cache(self):
        with server.BridgeHost(token=TOKEN) as host:
            cid = '4' * 32
            op = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=os.getpid())
            self.assertEqual(_next(host, 'SR1')['op_id'], op)
            absent = self.receipt(cid, op, kind='reconcile_save', absence=True)
            for extra in ({'save_file': 'sb1_' + cid + '_0'}, {'sha256': 'a' * 64},
                          {'save_file': ' ', 'sha256': ''}, {'save_file': '', 'sha256': ' '}):
                with self.subTest(extra=extra):
                    with self.assertRaises(HTTPError) as refused:
                        _result(host, **{**absent, **extra})
                    self.assertEqual(refused.exception.code, 409)
                    self.assertIsNone(host.wait_result(op, 0))
            self.assertEqual(_result(host, **{**absent, 'save_file': '', 'sha256': None}), 200)

    def test_sr2_foreign_or_noncanonical_filename_cannot_poison_the_result_cache(self):
        with server.BridgeHost(token=TOKEN, creation_realms=('SR1', 'SR2')) as host:
            cid = '6' * 32
            op = host.enqueue_creation_command('SR2', 'create_save', cid, expected_pid=os.getpid())
            self.assertEqual(_next(host, 'SR2')['op_id'], op)
            marker = 'sb2_' + cid
            reply = {**self.receipt(cid, op, realm='SR2'), 'save_id': marker}
            filenames = ('foreign_0', 'sb1_' + cid + '_0', 'sb2_' + 'a' * 32 + '_0',
                         marker + '_0.sav', '../' + marker + '_0', marker + '_0:ads',
                         marker + '_00', marker + '_12345678901')
            for filename in filenames:
                with self.subTest(filename=filename):
                    with self.assertRaises(HTTPError) as refused:
                        _result(host, **{**reply, 'save_file': filename})
                    self.assertEqual(refused.exception.code, 409)
                    self.assertIsNone(host.wait_result(op, 0))
            self.assertEqual(_result(host, **{**reply, 'save_file': marker + '_1234567890'}), 200)

    def test_absence_adapter_never_discards_conflicting_native_readback(self):
        for realm in ('SR1', 'SR2'):
            adapter = wire_adapter(realm)
            cid, op = '5' * 32, 'synthetic-absence'
            session = {'pid': os.getpid(), 'session_id': 'synthetic-session'}
            request = {'creation_id': cid, 'profile_id': 'synthetic-profile', 'target_realm': realm,
                       'target_installation_id': 'synthetic-install'}
            absent = self.receipt(cid, op, realm=realm, kind='reconcile_save', absence=True)
            absent['save_id'] = ('sb1_' if realm == 'SR1' else 'sb2_') + cid
            for extra in ({'save_file': absent['save_id'] + '_0'}, {'sha256': 'a' * 64},
                          {'save_file': ' ', 'sha256': ''}, {'save_file': '', 'sha256': ' '}):
                with self.subTest(realm=realm, extra=extra), self.assertRaises(ValueError):
                    adapter.interpret_result(request, 'reconcile', op, {**absent, **extra}, session=session)
            result = adapter.interpret_result(request, 'reconcile', op,
                                              {**absent, 'save_file': '', 'sha256': None}, session=session)
            self.assertEqual(result['outcome'], 'ABSENT')


if __name__ == '__main__':
    unittest.main()
