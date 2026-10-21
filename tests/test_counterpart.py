import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from test_commands import _next, _result, TOKEN


class CounterpartCommandTests(unittest.TestCase):
    @staticmethod
    def poll_as(host, realm, pid):
        headers = {'Authorization': 'Bearer ' + host.realm_token(realm), 'Content-Type': 'application/json'}
        heartbeat = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': realm,
                     'request_id': 'creation-owned-session', 'pid': pid}
        with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(heartbeat).encode(), headers), timeout=3):
            pass
        with urlopen(Request(host.url + '/v1/commands/next?realm=' + realm, headers=headers), timeout=3) as response:
            return json.loads(response.read())['command']

    def test_foreign_live_pid_cannot_receive_a_creation_command(self):
        with BridgeHost(token=TOKEN) as host:
            pid = os.getpid()
            op = host.enqueue_creation_command('SR1', 'create_save', 'a' * 32, expected_pid=pid)
            self.assertIsNone(self.poll_as(host, 'SR1', pid + 1))
            self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(self.poll_as(host, 'SR1', pid)['op_id'], op)

    def test_sr2_creation_is_explicitly_opted_in_and_uses_its_marker(self):
        try:
            host = BridgeHost(token=TOKEN, creation_realms=('SR1', 'SR2'))
        except TypeError:
            self.fail('BridgeHost has no protected SR2 creation opt-in')
        with host:
            cid = 'b' * 32
            for kind in ('create_save', 'reconcile_save'):
                op = host.enqueue_creation_command('SR2', kind, cid, expected_pid=os.getpid())
                self.assertEqual(self.poll_as(host, 'SR2', os.getpid())['op_id'], op)
                self.assertEqual(_result(host, realm='SR2', op_id=op, kind=kind, creation_id=cid,
                                         save_id='sb2_' + cid, verified=True, ok=True,
                                         applied=kind == 'create_save', persisted=True, uncertain=False,
                                         before=1, after=1, save_file='sb2_' + cid + '_1234567890',
                                         sha256='a' * 64), 200)
                self.assertEqual(host.wait_result(op, 0.01)['save_id'], 'sb2_' + cid)
            self.assertIsNone(self.poll_as(host, 'SR2', os.getpid()))

    def test_incomplete_success_cannot_poison_creation_result_cache(self):
        cid = 'c' * 32
        with BridgeHost(token=TOKEN) as host:
            op = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            good = dict(realm='SR1', op_id=op, kind='create_save', creation_id=cid, save_id='sb1_' + cid,
                        ok=True, verified=True, applied=True, persisted=True, uncertain=False,
                        before=1, after=1, save_file='sb1_' + cid + '_0', sha256='a' * 64)
            for changed in (dict(verified=False), dict(verified=None), dict(persisted=False),
                            dict(uncertain=True), dict(save_file=None), dict(sha256=None), dict(sha256='A' * 64)):
                with self.subTest(changed=changed):
                    _next(host, 'SR1')
                    with self.assertRaises(HTTPError) as refused:
                        _result(host, **{**good, **changed})
                    self.assertIn(refused.exception.code, (400, 409))
                    self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(_result(host, **good), 200)
            self.assertEqual(host.wait_result(op, 0.01)['sha256'], good['sha256'])

    def test_creation_requires_delivery_and_the_current_session_epoch(self):
        cid = 'e' * 32
        with BridgeHost(token=TOKEN) as host:
            pid = os.getpid()
            op = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=pid)
            reply = dict(realm='SR1', op_id=op, kind='create_save', creation_id=cid, ok=False,
                         applied=False, verified=False, persisted=False, uncertain=True)
            with self.assertRaises(HTTPError) as early:
                _result(host, **reply)
            self.assertEqual(early.exception.code, 409)
            self.assertEqual(self.poll_as(host, 'SR1', pid)['op_id'], op)
            self.assertIsNone(self.poll_as(host, 'SR1', pid + 1))
            with self.assertRaises(HTTPError) as stale:
                _result(host, **reply)
            self.assertEqual(stale.exception.code, 409)
            self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(self.poll_as(host, 'SR1', pid)['op_id'], op)
            self.assertEqual(_result(host, **reply), 200)
            self.assertEqual(_result(host, **reply), 200)
            with self.assertRaises(HTTPError) as conflict:
                _result(host, **{**reply, 'uncertain': False})
            self.assertEqual(conflict.exception.code, 409)

    def test_sr1_read_only_reconciliation_and_absence_have_coherent_flags(self):
        with BridgeHost(token=TOKEN) as host:
            for cid, exists in (('1' * 32, True), ('2' * 32, False)):
                op = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=os.getpid())
                self.assertEqual(_next(host, 'SR1')['op_id'], op)
                proof = dict(realm='SR1', op_id=op, kind='reconcile_save', creation_id=cid, save_id='sb1_' + cid,
                             ok=True, verified=exists, applied=False, persisted=False, uncertain=False,
                             before=1 if exists else 0, after=1 if exists else 0, can_create=not exists)
                if exists:
                    proof.update(save_file='sb1_' + cid + '_0', sha256='a' * 64)
                self.assertEqual(_result(host, **proof), 200)

    def test_creation_command_has_native_provenance_and_redelivery(self):
        creation_id = '1234567890abcdef1234567890abcdef'
        with BridgeHost(token=TOKEN) as host:
            enqueue = getattr(host, 'enqueue_creation_command', None)
            if not callable(enqueue):
                self.fail('creation command transport is not implemented')
            op = enqueue('SR1', 'create_save', creation_id, op_id=creation_id + ':create',
                         expected_pid=os.getpid())
            command = _next(host, 'SR1')
            self.assertEqual(command, {'op_id': op, 'kind': 'create_save', 'creation_id': creation_id})
            self.assertEqual(command, _next(host, 'SR1'))
            self.assertIsNone(_next(host, 'SR2'))
            _result(host, realm='SR1', op_id=op, kind='create_save', creation_id=creation_id,
                    verified=False, applied=False, ok=False)
            self.assertIsNone(_next(host, 'SR1'))

    def test_creation_result_must_match_queued_identity_and_owned_process(self):
        creation_id = 'd' * 32
        with BridgeHost(token=TOKEN) as host:
            op = host.enqueue_creation_command('SR1', 'create_save', creation_id,
                                               expected_pid=os.getpid())
            good = dict(realm='SR1', op_id=op, kind='create_save', creation_id=creation_id,
                        save_id='sb1_' + creation_id, verified=False, ok=False, applied=False)
            for changed in [dict(kind='query'), dict(creation_id='e' * 32), dict(pid=os.getpid() + 1),
                            dict(save_id='other-save'), dict(verified=True, ok=True),
                            dict(kind=None), dict(creation_id=None)]:
                with self.subTest(changed=changed):
                    with self.assertRaises(HTTPError) as bad:
                        _result(host, **{**good, **changed})
                    self.assertEqual(bad.exception.code, 409)
                    self.assertEqual(_next(host, 'SR1')['op_id'], op)
                    self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(_result(host, **good), 200)

    def test_creation_operation_cannot_be_rebound_to_another_identity_or_pid(self):
        with BridgeHost(token=TOKEN) as host:
            op = host.enqueue_creation_command('SR1', 'create_save', 'a' * 32,
                                               expected_pid=os.getpid())
            for identity, pid in [('b' * 32, os.getpid()), ('a' * 32, os.getpid() + 1)]:
                with self.assertRaises(ValueError):
                    host.enqueue_creation_command('SR1', 'create_save', identity, op_id=op, expected_pid=pid)

    def test_sr2_creation_and_malformed_ids_are_refused(self):
        with BridgeHost(token=TOKEN) as host:
            enqueue = getattr(host, 'enqueue_creation_command', None)
            if not callable(enqueue):
                self.fail('creation command transport is not implemented')
            for realm, kind, identity in [('SR2', 'create_save', 'a' * 32),
                                           ('SR1', 'create_save', 'A' * 32),
                                           ('SR1', 'create_save', 'a' * 31),
                                           ('SR1', 'add', 'a' * 32)]:
                with self.subTest(realm=realm, kind=kind, identity=identity):
                    with self.assertRaises(ValueError):
                        enqueue(realm, kind, identity, expected_pid=os.getpid())


if __name__ == '__main__':
    unittest.main()
