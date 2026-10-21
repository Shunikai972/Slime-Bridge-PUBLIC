"""Concrete independent-review regressions on synthetic HTTP/wire fixtures only."""
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.counterpart.hosttransport import BridgeHostTransport
from bridge.counterpart.realms import wire_adapter
from bridge.host.server import BridgeHost, HEARTBEAT_STALE_AFTER
from test_commands import TOKEN, _next, _result
import test_counterpart as mailbox_tests


class CreationReviewTests(unittest.TestCase):
    def test_reconcile_is_deliverable_after_the_old_create_times_out(self):
        for changed_pid in (False, True):
            with self.subTest(changed_pid=changed_pid), BridgeHost(token=TOKEN) as host:
                cid, old_pid = 'a' * 32, os.getpid()
                create = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=old_pid)
                self.assertEqual(_next(host, 'SR1')['op_id'], create)
                new_pid = old_pid + 1 if changed_pid else old_pid
                reconcile = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=new_pid)
                command = mailbox_tests.CounterpartCommandTests.poll_as(host, 'SR1', new_pid)
                self.assertIsNotNone(command)
                self.assertEqual(command['op_id'], reconcile)
                self.assertIsNone(host.wait_result(create, 0.01))

    def test_ineligible_old_creation_does_not_block_other_live_commands(self):
        with BridgeHost(token=TOKEN) as host:
            pid = os.getpid()
            host.enqueue_creation_command('SR1', 'create_save', 'a' * 32, expected_pid=pid + 1)
            live = host.enqueue_creation_command('SR1', 'reconcile_save', 'b' * 32, expected_pid=pid)
            command = _next(host, 'SR1')
            self.assertIsNotNone(command)
            self.assertEqual(command['op_id'], live)

    def test_absence_requires_explicit_verified_false_before_it_can_be_cached(self):
        with BridgeHost(token=TOKEN) as host:
            cid = 'c' * 32
            op = host.enqueue_creation_command('SR1', 'reconcile_save', cid, expected_pid=os.getpid())
            _next(host, 'SR1')
            reply = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                     'op_id': op, 'kind': 'reconcile_save', 'creation_id': cid, 'save_id': 'sb1_' + cid,
                     'pid': os.getpid(), 'ok': True, 'applied': False, 'persisted': False,
                     'uncertain': False, 'before': 0, 'after': 0, 'can_create': True}
            headers = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
            with self.assertRaises(HTTPError) as refused:
                urlopen(Request(host.url + '/v1/commands/result', json.dumps(reply).encode(), headers), timeout=3)
            self.assertIn(refused.exception.code, (400, 409))
            self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(_result(host, **{**reply, 'verified': False}), 200)

    def test_missing_or_null_count_proof_is_structurally_rejected(self):
        with BridgeHost(token=TOKEN) as host:
            cid = 'd' * 32
            op = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            _next(host, 'SR1')
            reply = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                     'op_id': op, 'kind': 'create_save', 'creation_id': cid, 'save_id': 'sb1_' + cid,
                     'pid': os.getpid(), 'ok': True, 'applied': True, 'persisted': True, 'verified': True,
                     'uncertain': False, 'before': 1, 'after': 1, 'save_file': 'sb1_' + cid + '_0', 'sha256': 'a' * 64}
            headers = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
            for key in ('before', 'after'):
                for absent in (False, True):
                    with self.subTest(key=key, absent=absent):
                        malformed = dict(reply)
                        if absent:
                            malformed.pop(key)
                        else:
                            malformed[key] = None
                        with self.assertRaises(HTTPError) as refused:
                            urlopen(Request(host.url + '/v1/commands/result', json.dumps(malformed).encode(), headers), timeout=3)
                        self.assertIn(refused.exception.code, (400, 409))
                        self.assertIsNone(host.wait_result(op, 0.01))
            self.assertEqual(_result(host, **reply), 200)

    def test_same_pid_expiry_cannot_rebind_an_accepted_creation_result(self):
        with BridgeHost(token=TOKEN) as host:
            cid = 'f' * 32
            op = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            _next(host, 'SR1')
            reply = {'protocol_version': 1, 'message_type': 'command_result', 'realm': 'SR1',
                     'op_id': op, 'kind': 'create_save', 'creation_id': cid, 'save_id': 'sb1_' + cid,
                     'pid': os.getpid(), 'ok': True, 'applied': True, 'persisted': True, 'verified': True,
                     'uncertain': False, 'before': 1, 'after': 1, 'save_file': 'sb1_' + cid + '_0', 'sha256': 'a' * 64}
            self.assertEqual(_result(host, **reply), 200)
            self.assertEqual(_result(host, **reply), 200)
            accepted = host.wait_result(op, 0.01)
            with host._lock:
                host._heartbeats['SR1']['received_monotonic'] -= HEARTBEAT_STALE_AFTER + 1
            self.assertIsNone(_next(host, 'SR1'))  # same PID, new session epoch; no completed command redelivery
            headers = {'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'}
            with self.assertRaises(HTTPError) as refused:
                urlopen(Request(host.url + '/v1/commands/result', json.dumps(reply).encode(), headers), timeout=3)
            self.assertEqual(refused.exception.code, 409)
            self.assertEqual(json.loads(refused.exception.read())['error'], 'plugin_session_mismatch')
            self.assertEqual(host.wait_result(op, 0.01), accepted)

    def test_accepted_creation_receipts_outlive_the_plugin_heartbeat_in_both_realms(self):
        for realm in ('SR1', 'SR2'):
            for kind in ('create_save', 'reconcile_save'):
                with self.subTest(realm=realm, kind=kind), BridgeHost(
                        token=TOKEN, creation_realms=('SR1', 'SR2')) as host:
                    cid, pid = '7' * 32, os.getpid()
                    op = host.enqueue_creation_command(realm, kind, cid, expected_pid=pid)
                    self.assertEqual(_next(host, realm)['op_id'], op)
                    marker = ('sb1_' if realm == 'SR1' else 'sb2_') + cid
                    self.assertEqual(_result(host, realm=realm, op_id=op, kind=kind,
                                             creation_id=cid, save_id=marker, verified=True,
                                             applied=kind == 'create_save', persisted=True,
                                             before=1, after=1, save_file=marker + '_0',
                                             sha256='a' * 64), 200)
                    accepted = host.wait_result(op, 0)
                    self.assertIsNotNone(accepted)
                    with host._lock:
                        host._heartbeats[realm]['received_monotonic'] -= HEARTBEAT_STALE_AFTER + 1
                    self.assertNotIn(realm, host.heartbeats())
                    self.assertEqual(host.wait_result(op, 0), accepted)
                    # Retaining a completed receipt must not requeue CREATE or reconciliation.
                    self.assertEqual(host.enqueue_creation_command(realm, kind, cid,
                                                                   expected_pid=pid), op)
                    self.assertIsNone(mailbox_tests.CounterpartCommandTests.poll_as(host, realm, pid + 1))
                    self.assertEqual(host.wait_result(op, 0), accepted)
                    self.assertIsNone(_next(host, realm))
                    copied = host.wait_result(op, 0)
                    copied['sha256'] = 'b' * 64
                    self.assertEqual(host.wait_result(op, 0), accepted)
                    host.forget_volatile_state()
                    self.assertIsNone(host.wait_result(op, 0))

    def test_nested_creation_receipt_is_isolated_on_admission_and_every_read(self):
        from unittest import mock
        import bridge.host.server as server

        for realm in ('SR1', 'SR2'):
            for kind in ('create_save', 'reconcile_save'):
                with self.subTest(realm=realm, kind=kind), BridgeHost(
                        token=TOKEN, creation_realms=('SR1', 'SR2')) as host:
                    cid = '9' * 32
                    op = host.enqueue_creation_command(realm, kind, cid, expected_pid=os.getpid())
                    self.assertEqual(_next(host, realm)['op_id'], op)
                    marker = ('sb1_' if realm == 'SR1' else 'sb2_') + cid
                    extension = {'history': [{'value': 'original', 'markers': [1, 2]}]}
                    admitted = []
                    validate = server._valid_command_result

                    def observe_admission(data):
                        admitted.append(data)
                        return validate(data)

                    reply = dict(realm=realm, op_id=op, kind=kind, creation_id=cid,
                                 save_id=marker, verified=True, applied=kind == 'create_save',
                                 persisted=True, before=1, after=1, save_file=marker + '_0',
                                 sha256='a' * 64, extension=extension)
                    with mock.patch.object(server, '_valid_command_result', side_effect=observe_admission):
                        self.assertEqual(_result(host, **reply), 200)
                    first = host.wait_result(op, 0)
                    self.assertEqual(first['extension'], extension)
                    first['extension']['history'][0]['value'] = 'caller-mutated'
                    first['extension']['history'][0]['markers'].append(3)
                    self.assertEqual(host.wait_result(op, 0)['extension'], extension)
                    # Admission must also detach the stored snapshot from the handler's input.
                    self.assertEqual(len(admitted), 1)
                    admitted[0]['extension']['history'][0]['value'] = 'handler-mutated'
                    admitted[0]['extension']['history'].clear()
                    self.assertEqual(host.wait_result(op, 0)['extension'], extension)
                    self.assertEqual(_result(host, **reply), 200)  # immutable replay still matches
                    with host._lock:
                        host._heartbeats[realm]['received_monotonic'] -= HEARTBEAT_STALE_AFTER + 1
                    stale_copy = host.wait_result(op, 0)
                    self.assertEqual(stale_copy['extension'], extension)
                    stale_copy['extension']['history'].clear()
                    self.assertEqual(host.wait_result(op, 0)['extension'], extension)

    def test_inventory_receipt_still_requires_a_live_session_after_creation_completes(self):
        with BridgeHost(token=TOKEN) as host:
            cid = '8' * 32
            create = host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
            self.assertEqual(_next(host, 'SR1')['op_id'], create)
            self.assertEqual(_result(host, realm='SR1', op_id=create, kind='create_save',
                                     creation_id=cid, save_id='sb1_' + cid, verified=True,
                                     applied=True, persisted=True, before=1, after=1,
                                     save_file='sb1_' + cid + '_0', sha256='a' * 64), 200)
            query = host.enqueue_command('SR1', 'query', 'slime.pink', save_id='synthetic-save')
            self.assertEqual(_next(host, 'SR1')['op_id'], query)
            self.assertEqual(_result(host, realm='SR1', op_id=query, kind='query',
                                     save_id='synthetic-save', applied=False, persisted=False,
                                     verified=False, before=1, after=1, can_add=True), 200)
            self.assertIsNotNone(host.wait_result(query, 0))
            accepted = host.wait_result(create, 0)
            with host._lock:
                host._heartbeats['SR1']['received_monotonic'] -= HEARTBEAT_STALE_AFTER + 1
            self.assertIsNone(host.wait_result(query, 0))
            self.assertEqual(host.wait_result(create, 0), accepted)
            self.assertIsNone(_next(host, 'SR1'))
            self.assertIsNone(host.wait_result(query, 0))

    def test_transport_preflight_never_equates_boolean_and_integer_pids(self):
        with BridgeHost(token=TOKEN) as host:
            self.assertIsNone(mailbox_tests.CounterpartCommandTests.poll_as(host, 'SR1', 1))
            transport = BridgeHostTransport(host)
            session = transport.live_session('SR1')
            assert session is not None
            self.assertFalse(transport.preflight('SR1', {**session, 'pid': True}))
            self.assertTrue(transport.preflight('SR1', session))

    def test_absence_adapter_rejects_missing_or_contradictory_effect_flags(self):
        for realm in ('SR1', 'SR2'):
            adapter = wire_adapter(realm)
            cid, op = 'e' * 32, 'review-reconcile'
            session = {'pid': 1234, 'session_id': 'review-session'}
            request = {'creation_id': cid, 'profile_id': 'review-profile', 'target_realm': realm,
                       'target_installation_id': realm.lower() + '-unit'}
            wire = {'realm': realm, 'kind': 'reconcile_save', 'op_id': op, 'creation_id': cid,
                    'pid': 1234, 'ok': True, 'verified': False, 'uncertain': False, 'can_create': True,
                    'applied': False, 'persisted': False, 'before': 0, 'after': 0}
            self.assertEqual(adapter.interpret_result(request, 'reconcile', op, wire, session=session)['outcome'], 'ABSENT')
            for changed in ({'applied': True}, {'persisted': True}, {'before': 1, 'after': 1},
                            {'applied': None}, {'persisted': None}, {'before': None}):
                with self.subTest(realm=realm, changed=changed), self.assertRaises(ValueError):
                    adapter.interpret_result(request, 'reconcile', op, {**wire, **changed}, session=session)
            for missing in ('applied', 'persisted', 'before', 'after'):
                malformed = dict(wire)
                malformed.pop(missing)
                with self.subTest(realm=realm, missing=missing), self.assertRaises(ValueError):
                    adapter.interpret_result(request, 'reconcile', op, malformed, session=session)


if __name__ == '__main__':
    unittest.main()
