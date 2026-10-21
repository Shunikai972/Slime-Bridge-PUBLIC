from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from bridge.transfer.store import TransferStore
from transfer_host_fixture import SyntheticTransferHost

TOKEN = 'local-test-token'
AUTH = {'Authorization': 'Bearer ' + TOKEN}
JSON = {**AUTH, 'Content-Type': 'application/json'}


def _next(host, realm):
    heartbeat = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': realm,
                 'request_id': 'test-session', 'pid': os.getpid()}
    headers = {'Authorization': 'Bearer ' + host.realm_token(realm), 'Content-Type': 'application/json'}
    with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(heartbeat).encode(), headers), timeout=3):
        pass
    with urlopen(Request(f'{host.url}/v1/commands/next?realm={realm}', headers=AUTH), timeout=3) as r:
        return json.loads(r.read())['command']


def _result(host, **fields):
    count = fields.get('count', 1)
    body = {'protocol_version': 1, 'message_type': 'command_result', 'pid': os.getpid(),
            'ok': True, 'applied': True, 'before': count if type(count) is int else 1, 'after': 0,
            'uncertain': False, 'persisted': True, 'verified': True,
            'sha256': 'a' * 64, 'save_file': 'unit_save_0',
            'kind': 'remove', 'item': 'slime.pink', 'count': 1, 'save_id': 'unit-save'}
    if 'creation_id' in fields or fields.get('kind') in ('create_save', 'reconcile_save'):
        # Creation fixtures have their own proof contract; never inherit inventory proof defaults.
        body.update(save_id='', before=-1, after=-1, persisted=False, verified=False)
        body.pop('sha256')
        body.pop('save_file')
    body.update(fields)
    with urlopen(Request(host.url + '/v1/commands/result', json.dumps(body).encode(), JSON), timeout=3) as r:
        return r.status


class CommandMailboxTests(unittest.TestCase):
    def test_command_is_redelivered_until_result_then_cleared(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='mailbox-redelivery')
                store.lock(tid)
                host = fixture.host
                op = fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 1, 'a')
                self.assertEqual(_next(host, 'SR1')['op_id'], op)
                self.assertEqual(_next(host, 'SR1')['op_id'], op)  # at-least-once delivery
                self.assertIsNone(_next(host, 'SR2'))
                self.assertEqual(_result(host, realm='SR1', op_id=op, save_id='a'), 200)
                self.assertIsNone(_next(host, 'SR1'))
                self.assertEqual(host.wait_result(op, 1)['after'], 0)

    def test_first_result_wins_and_reenqueue_after_result_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='mailbox-first-result', item='plort.pink')
                store.lock(tid)
                store.escrow(tid)
                host = fixture.host
                op = fixture.enqueue(tid, 'add', 'SR2', 'add', 'plort.pink', 1, 'b')
                _next(host, 'SR2')
                _result(host, realm='SR2', op_id=op, kind='add', item='plort.pink',
                        save_id='b', before=0, after=1)
                with self.assertRaises(HTTPError) as replay:
                    _result(host, realm='SR2', op_id=op, kind='add', item='plort.pink',
                            save_id='b', before=1, after=2)
                self.assertEqual(replay.exception.code, 409)
                self.assertEqual(host.wait_result(op, 1)['after'], 1)
                host.enqueue_command('SR2', 'add', 'plort.pink', 1, op_id=op, save_id='b')
                self.assertIsNone(_next(host, 'SR2'))

    def test_validation(self):
        with BridgeHost(token=TOKEN) as host:
            with self.assertRaises(ValueError):
                host.enqueue_command('SR3', 'add', 'slime.pink', save_id='unit-save')
            with self.assertRaises(ValueError):
                host.enqueue_command('SR1', 'add', 'slime.gold', save_id='unit-save')
            with self.assertRaises(ValueError):
                host.enqueue_command('SR1', 'add', 'slime.pink', op_id='bad id!', save_id='unit-save')
            with self.assertRaises(HTTPError) as bad:
                _result(host, realm='SR1', op_id='x', ok='yes')
            self.assertEqual(bad.exception.code, 400)
            with self.assertRaises(HTTPError) as unauth:
                urlopen(Request(host.url + '/v1/commands/next?realm=SR1'), timeout=3)
            self.assertEqual(unauth.exception.code, 401)
            self.assertIsNone(host.wait_result('never', 0.05))

    def test_unbound_mutations_fail_closed_queries_remain_read_only_and_lab_give_is_opt_in(self):
        with BridgeHost(token=TOKEN) as host:
            for kind in ('remove', 'add'):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    host.enqueue_command('SR1', kind, 'slime.pink', 1,
                                         op_id='unbound:' + kind, save_id='arbitrary-save')
            query = host.enqueue_command('SR1', 'query', 'slime.pink', 1,
                                         op_id='read-only-query', save_id='arbitrary-save')
            self.assertEqual(_next(host, 'SR1')['op_id'], query)
            with self.assertRaises(ValueError):
                host.enqueue_command('SR1', 'lab_give', 'slime.pink', 1,
                                     op_id='lab-give-default-deny', save_id='arbitrary-save')

        with BridgeHost(token=TOKEN, allow_lab_give=True) as host:
            op = host.enqueue_command('SR1', 'lab_give', 'slime.pink', 1,
                                      op_id='explicit-lab-give', save_id='arbitrary-save')
            self.assertEqual(op, 'explicit-lab-give')

    def test_result_from_wrong_realm_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='mailbox-wrong-realm')
                store.lock(tid)
                host = fixture.host
                op = fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 1, 'a')
                _next(host, 'SR1')
                with self.assertRaises(HTTPError) as bad:
                    _result(host, realm='SR2', op_id=op)
                self.assertEqual(bad.exception.code, 409)
                self.assertIsNone(host.wait_result(op, 0.05))
                self.assertEqual(_next(host, 'SR1')['op_id'], op)
                with self.assertRaises(ValueError):
                    host.enqueue_command('SR2', 'remove', 'slime.pink', 1, op_id=op, save_id='a')

    def test_early_incomplete_and_cross_realm_results_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='mailbox-incomplete-result', count=2)
                store.lock(tid)
                host = fixture.host
                op = fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 2, 'a')
                for token in (host.realm_token('SR2'),):
                    with self.assertRaises(HTTPError) as wrong:
                        urlopen(Request(host.url + '/v1/commands/next?realm=SR1',
                                        headers={'Authorization': 'Bearer ' + token}), timeout=3)
                    self.assertEqual(wrong.exception.code, 401)
                with self.assertRaises(HTTPError) as early:
                    _result(host, realm='SR1', op_id=op, count=2, save_id='a')
                self.assertEqual(early.exception.code, 409)
                _next(host, 'SR1')
                for changed in ({'item': None}, {'kind': None}, {'count': None}, {'save_id': None},
                                {'count': 1}, {'pid': os.getpid() + 1}):
                    with self.subTest(changed=changed):
                        with self.assertRaises(HTTPError) as bad:
                            _result(host, **{'realm': 'SR1', 'op_id': op, 'count': 2,
                                             'save_id': 'a', **changed})
                        self.assertIn(bad.exception.code, (400, 409))
                        self.assertIsNone(host.wait_result(op, 0.01))
                self.assertEqual(_result(host, realm='SR1', op_id=op, count=2, save_id='a'), 200)
                with self.assertRaises(ValueError):
                    host.enqueue_command('SR1', 'add', 'slime.pink', 2, op_id=op, save_id='a')

    def test_result_requires_current_polled_plugin_session(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='mailbox-session')
                store.lock(tid)
                host = fixture.host
                op = fixture.enqueue(tid, 'remove', 'SR1', 'remove', 'slime.pink', 1, 'a')
                _next(host, 'SR1')
                changed_pid = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': 'SR1',
                               'request_id': 'new-process', 'pid': os.getpid() + 1}
                headers = {'Authorization': 'Bearer ' + host.realm_token('SR1'),
                           'Content-Type': 'application/json'}
                with urlopen(Request(host.url + '/v1/heartbeat', json.dumps(changed_pid).encode(), headers), timeout=3):
                    pass
                with self.assertRaises(HTTPError) as stale:
                    _result(host, realm='SR1', op_id=op)
                self.assertEqual(stale.exception.code, 409)
                self.assertIsNone(host.wait_result(op, 0.01))

    def test_refund_fence_blocks_pending_and_reenqueued_receiver_add(self):
        with tempfile.TemporaryDirectory() as tmp, TransferStore(Path(tmp) / 'ledger.db') as store:
            with SyntheticTransferHost(tmp, store, token=TOKEN) as fixture:
                tid = fixture.prepare(key='fenced-add')
                store.lock(tid)
                store.escrow(tid)
                host = fixture.host
                fixture.enqueue(tid, 'add', 'SR2', 'add', 'slime.pink', 1, 'b')
                store.begin_refund(tid, 'verified receiver loss')
                self.assertIsNone(_next(host, 'SR2'))
                with self.assertRaises(ValueError):
                    host.enqueue_command('SR2', 'add', 'slime.pink', 1, op_id=tid + ':add', save_id='b')
                with self.assertRaises(HTTPError) as admin:
                    urlopen(Request(host.url + '/v1/transfer/' + tid,
                                    headers={'Authorization': 'Bearer ' + host.realm_token('SR2')}), timeout=3)
                self.assertEqual(admin.exception.code, 401)


if __name__ == '__main__':
    unittest.main()
