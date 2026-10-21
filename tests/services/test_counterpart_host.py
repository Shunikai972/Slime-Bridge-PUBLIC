"""Generic counterpart service end-to-end over the REAL BridgeHost HTTP command channel (SR1 target)."""

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from bridge.counterpart.hosttransport import BridgeHostTransport
from bridge.counterpart.journal import CounterpartJournal
from bridge.counterpart.links import ProfileStoreLinks
from bridge.counterpart.realms import wire_adapter
from bridge.counterpart.service import CounterpartService
from bridge.host.server import BridgeHost
from bridge.profile.store import ProfileStore
from test_commands import TOKEN, _next, _result


class HostTransportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.host = BridgeHost(token=TOKEN).__enter__()
        self.store = ProfileStore(tmp / 'profiles.sqlite3')
        self.journal = CounterpartJournal(tmp / 'journal.sqlite3')
        self.transport = BridgeHostTransport(self.host)
        self.svc = CounterpartService(self.journal, ProfileStoreLinks(self.store),
                                      {'SR1': wire_adapter('SR1'), 'SR2': wire_adapter('SR2')}, self.transport, trust={})
        self.pid = self.store.find_or_create('SR2', 'sr2-install', 'native-sr2', 'sr2-identity')['bridge_profile_id']

    def tearDown(self):
        self.journal.close()
        self.store.__exit__(None, None, None)
        self.host.__exit__(None, None, None)
        self._tmp.cleanup()

    def plugin_reply(self, command, **fields):
        cid = command['creation_id']
        body = dict(realm='SR1', op_id=command['op_id'], kind=command['kind'], creation_id=cid,
                    save_id='sb1_' + cid, ok=True, applied=command['kind'] == 'create_save', verified=True,
                    persisted=True, uncertain=False, before=1, after=1, save_file=f'sb1_{cid}_0',
                    sha256=hashlib.sha256(cid.encode()).hexdigest())
        body.update(fields)
        return _result(self.host, **body)

    def test_sr2_source_gets_an_sr1_counterpart_through_bridgehost(self):
        self.assertIsNone(_next(self.host, 'SR1'))  # plugin heartbeat: SR1 session is live
        session = self.transport.live_session('SR1')
        self.assertEqual(session['pid'], os.getpid())
        cid = self.svc.request(self.pid, 'SR1', 'sr1-install')
        self.assertTrue(self.svc.dispatch(cid, session)['dispatched'])
        command = _next(self.host, 'SR1')
        self.assertEqual(command, {'op_id': f'{cid}:create', 'kind': 'create_save', 'creation_id': cid})
        self.assertEqual(self.plugin_reply(command), 200)
        self.assertEqual(self.transport.poll(self.svc), [{'verdict': 'ACCEPTED', 'phase': 'NATIVE_OBSERVED'}])
        observed = self.journal.get(cid)['observed']
        self.assertEqual((observed['native_save_id'], observed['native_filename']), ('sb1_' + cid, f'sb1_{cid}_0'))

    def test_sr2_target_is_fenced_by_the_host_and_nothing_is_issued(self):
        sr1_profile = self.store.find_or_create('SR1', 'sr1-install', 'native-sr1', 'sr1-identity')['bridge_profile_id']
        self.assertIsNone(_next(self.host, 'SR2'))
        session = self.transport.live_session('SR2')
        self.assertEqual(self.transport.health('SR2'), 'UNAVAILABLE')
        cid = self.svc.request(sr1_profile, 'SR2', 'sr2-install')
        self.assertFalse(self.svc.dispatch(cid, session)['dispatched'])
        self.assertEqual(self.journal.get(cid)['phase'], 'INTENT_PERSISTED')
        self.assertEqual(self.store.get(sr1_profile)['creation_issued'], 0)
        self.assertIsNone(_next(self.host, 'SR2'))
        decision = self.svc.decide(sr1_profile, 'SR2', provider_health=self.transport.health('SR2'), runtime_proven=True,
                                   slot_probe={'complete': True, 'free_slots': [0]})
        self.assertEqual(decision['decision'], 'BLOCK_PROVIDER_UNAVAILABLE')

    def test_transport_cannot_opt_in_a_realm_the_host_has_not_authorized(self):
        self.assertIsNone(_next(self.host, 'SR2'))
        transport = BridgeHostTransport(self.host, creation_realms=('SR1', 'SR2'))
        session = transport.live_session('SR2')
        self.assertEqual(transport.health('SR2'), 'UNAVAILABLE')
        self.assertFalse(transport.preflight('SR2', session))

    def test_explicit_sr2_host_opt_in_reaches_native_observed_not_runtime_proven(self):
        sr1_profile = self.store.find_or_create('SR1', 'sr1-install', 'native-sr1', 'sr1-identity')['bridge_profile_id']
        with BridgeHost(token=TOKEN, creation_realms=('SR1', 'SR2')) as host:
            transport = BridgeHostTransport(host, creation_realms=('SR1', 'SR2'))
            self.svc.transport = transport
            self.assertIsNone(_next(host, 'SR2'))
            session = transport.live_session('SR2')
            decision = self.svc.decide(sr1_profile, 'SR2', provider_health=transport.health('SR2'),
                                       runtime_proven=False, slot_probe={'complete': True, 'free_slots': [0]})
            self.assertEqual(decision['decision'], 'NEEDS_LOCAL_RUNTIME_PROOF')
            cid = self.svc.request(sr1_profile, 'SR2', 'sr2-install')
            self.assertTrue(self.svc.dispatch(cid, session)['dispatched'])
            command = _next(host, 'SR2')
            self.assertEqual(command['kind'], 'create_save')
            self.assertEqual(_result(host, realm='SR2', op_id=command['op_id'], kind=command['kind'], creation_id=cid,
                                     save_id='sb2_' + cid, ok=True, applied=True, verified=True, persisted=True,
                                     uncertain=False, before=1, after=1, save_file='sb2_' + cid + '_0',
                                     sha256=hashlib.sha256(cid.encode()).hexdigest()), 200)
            self.assertEqual(transport.poll(self.svc), [{'verdict': 'ACCEPTED', 'phase': 'NATIVE_OBSERVED'}])
            self.assertNotEqual(self.store.get(sr1_profile)['state'], 'LINKED')

    def test_host_restart_loses_the_reply_and_reconcile_recovers_it(self):
        _next(self.host, 'SR1')
        session = self.transport.live_session('SR1')
        cid = self.svc.request(self.pid, 'SR1', 'sr1-install')
        self.svc.dispatch(cid, session)
        command = _next(self.host, 'SR1')
        self.host.forget_volatile_state()  # BridgeHost process restart: mailboxes and results are gone
        self.transport.forget()
        self.assertEqual(self.svc.recover()['uncertain'], [cid])
        self.assertFalse(self.svc.dispatch(cid, session)['dispatched'])
        op = self.svc.reconcile(cid, session)['operation_id']
        reconcile = _next(self.host, 'SR1')
        self.assertEqual((reconcile['op_id'], reconcile['kind']), (op, 'reconcile_save'))
        self.assertEqual(self.plugin_reply(reconcile, applied=False), 200)
        self.assertEqual(self.transport.poll(self.svc), [{'verdict': 'ACCEPTED', 'phase': 'NATIVE_OBSERVED'}])
        self.assertEqual(command['op_id'], f'{cid}:create')

    def test_host_rejects_a_foreign_provenance_reply_before_the_service_sees_it(self):
        from urllib.error import HTTPError
        _next(self.host, 'SR1')
        session = self.transport.live_session('SR1')
        cid = self.svc.request(self.pid, 'SR1', 'sr1-install')
        self.svc.dispatch(cid, session)
        command = _next(self.host, 'SR1')
        with self.assertRaises(HTTPError) as ctx:
            self.plugin_reply(command, save_id='sb1_' + 'f' * 32)
        self.assertEqual(ctx.exception.code, 409)
        self.assertEqual(self.transport.poll(self.svc), [])
        self.assertEqual(self.journal.get(cid)['phase'], 'DISPATCHED')


if __name__ == '__main__':
    unittest.main()
