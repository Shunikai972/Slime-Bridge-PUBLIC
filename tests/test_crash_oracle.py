"""Exercise the real HTTP mailbox/oracle with explicitly synthetic game APIs only.

The fixture's authority mirrors what a real oracle run arms before a scenario is invoked:
it creates a linked synthetic profile/pair, adopts its endpoints in the counterpart
journal, binds the run-scoped durable ledger, trusts its own local endpoint provider and
enables the controller-only lab-grant. The scenario is handed the same armed runtime the
oracle publishes, so recovery behaviour is exercised against the real authority gates.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost
from bridge.profile.store import ProfileStore
from bridge.transfer.store import TransferStore
from tools.lab.crash_oracle import make_scenario
from transfer_binding_fixtures import PairFixture

ENDPOINT_FIELDS = ('installation_id', 'native_save_id', 'native_save_identity')


class SyntheticGameHost(BridgeHost):
    """No game launch or native persistence; HTTP results model a durable plugin journal."""

    def __init__(self, journals):
        self.journals = journals
        root = Path(next(iter(journals.values()))).parent
        self.pair = PairFixture(root)
        self.saves = {'SR1': 'synthetic-source', 'SR2': 'synthetic-destination'}
        self.profile_id = self.pair.linked(sender_save=self.saves['SR1'],
                                           receiver_save=self.saves['SR2'])
        self.pair.load(self.profile_id)
        self.store_path = root / 'transfers.db'
        store = TransferStore(self.store_path)
        self.armed_store = store
        with ProfileStore(self.pair.profiles_path, read_only=True) as profiles:
            profile = profiles.get(self.profile_id)
        endpoints = {realm: {'realm': realm, **{key: profile[realm.lower()][key]
                                               for key in ENDPOINT_FIELDS}}
                     for realm in ('SR1', 'SR2')}
        # Same shape oracle._run_games arms: the run-scoped ledger, the linked pair authority,
        # the trusted local endpoint provider and the controller-only lab grant.
        self.transfer_authority = {'profiles_path': str(self.pair.profiles_path),
                                   'journal_path': str(self.pair.journal_path),
                                   'store_path': str(self.store_path),
                                   'profile_id': self.profile_id,
                                   'trust': dict(self.pair.trust),
                                   'allow_lab_give': True}
        self.transfer_authority['_runtime'] = {'profile_id': self.profile_id,
                                               'endpoints': endpoints,
                                               'builds': dict(self.pair.builds),
                                               'store': store,
                                               'journal_path': Path(self.pair.journal_path),
                                               'store_path': Path(self.store_path)}
        super().__init__(transfer_store=store, transfer_bindings=self.pair.resolver,
                         endpoint_provider=self.pair.report, endpoint_trust=self.pair.trust,
                         allow_lab_give=True)
        self.counts = {'SR1': 0, 'SR2': 0}
        self.saved_results = {}
        self.mutations = []
        self.polled_add_states = []
        self.pids = {'SR1': 123, 'SR2': 456}

    def post(self, realm, endpoint, body):
        headers = {'Content-Type': 'application/json',
                   'Authorization': 'Bearer ' + self.realm_token(realm)}
        with urlopen(Request(self.url + endpoint, json.dumps(body).encode(), headers), timeout=3) as reply:
            return json.loads(reply.read())

    def __exit__(self, exc_type, exc, tb):
        """Close the model host AND the run-scoped ledger it armed, so Windows can delete it."""
        try:
            super().__exit__(exc_type, exc, tb)
        finally:
            self.armed_store.close()

    def establish_sessions(self):
        for realm in self.pids:
            self.post(realm, '/v1/heartbeat', {'protocol_version': 1, 'message_type': 'heartbeat',
                      'realm': realm, 'request_id': 'synthetic-session', 'pid': self.pids[realm]})

    def wait_result(self, op_id, timeout):
        existing = super().wait_result(op_id, 0)
        if existing is not None:
            return existing
        command = self._commands.get(op_id)
        if command is None:
            return None
        realm = self._op_realm[op_id]
        headers = {'Authorization': 'Bearer ' + self.realm_token(realm)}
        with urlopen(Request(self.url + '/v1/commands/next?realm=' + realm, headers=headers), timeout=3) as reply:
            delivery = json.loads(reply.read())['command']
        if delivery is None:
            return None
        if delivery['op_id'] != op_id:
            raise AssertionError('synthetic game saw an unexpected pending command')
        if delivery['kind'] == 'add' and op_id.endswith(':add'):
            self.polled_add_states.append(self.transfer_store.get(op_id[:-4]).status)
        if op_id in self.saved_results:
            result = dict(self.saved_results[op_id])
        else:
            before = self.counts[realm]
            kind, count = delivery['kind'], delivery['count']
            delta = -count if kind == 'remove' else count if kind in ('add', 'lab_give') else 0
            self.counts[realm] += delta
            result = {'protocol_version': 1, 'message_type': 'command_result', 'realm': realm,
                      'pid': self.pids[realm], 'op_id': op_id, 'kind': kind, 'item': delivery['item'],
                      'count': count, 'save_id': delivery['save_id'], 'ok': True, 'applied': kind != 'query',
                      'before': before, 'after': self.counts[realm], 'can_add': True,
                      'uncertain': False, 'persisted': kind != 'query'}
            if kind != 'query':
                result.update(verified=True, sha256='a' * 64, save_file='synthetic_0')
                self.saved_results[op_id] = dict(result)
                self.mutations.append(op_id)
                with self.journals[realm].open('a', encoding='utf-8', newline='\n') as journal:
                    journal.write('I\t' + op_id + '\nD\t' + op_id + '\t' + json.dumps(result) + '\n')
        self.post(realm, '/v1/commands/result', result)
        return super().wait_result(op_id, 0)


class SyntheticProcess:
    """Explicit process fixture: kill/wait never controls a real process."""

    pid = 456
    returncode = None

    def kill(self):
        self.returncode = -1

    def wait(self, timeout):
        return self.returncode

    def poll(self):
        return self.returncode


class CrashOracleTests(unittest.TestCase):
    def _fixture(self, root):
        journals = {realm: root / (realm + '.journal') for realm in ('SR1', 'SR2')}
        for path in journals.values():
            path.write_text('', encoding='utf-8')
        return journals

    def test_scenario_refuses_a_ledger_different_from_the_armed_store(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journals = self._fixture(root)
            with SyntheticGameHost(journals) as host:
                host.establish_sessions()
                beats = {realm: {'status': {'save_id': save}} for realm, save in host.saves.items()}
                evidence = make_scenario('SR1', 'SR2', 'slime.pink', root / 'other.db', journals, 0,
                                         profile_id=host.profile_id,
                                         authority=host.transfer_authority)(
                    host, beats, host.pids, procs={'SR2': SyntheticProcess()})
                self.assertEqual(evidence['error'],
                                 'the scenario ledger differs from the armed transfer store')
                self.assertFalse(evidence['passed'])
                self.assertEqual(host.counts, {'SR1': 0, 'SR2': 0})
                self.assertEqual(host.mutations, [])

    def test_lost_receiver_ack_is_replayed_before_commit_with_no_second_native_effect(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journals = self._fixture(root)
            with SyntheticGameHost(journals) as host:
                database = host.store_path
                host.establish_sessions()
                beats = {realm: {'status': {'save_id': save}} for realm, save in host.saves.items()}
                receiver = SyntheticProcess()
                try:
                    with patch('tools.lab.crash_oracle.oracle.same_image', return_value=True):
                        evidence = make_scenario('SR1', 'SR2', 'slime.pink', database, journals, 0,
                                                 profile_id=host.profile_id,
                                                 authority=host.transfer_authority)(
                            host, beats, host.pids, procs={'SR2': receiver})
                    self.assertTrue(evidence['B']['passed'], evidence['B'])
                    self.assertEqual(evidence['B']['status_before_replay'], 'ESCROWED')
                    self.assertEqual(evidence['B']['status_after_replay'], 'COMMITTED')
                    self.assertTrue(evidence['B']['same_native_receipt'])
                    self.assertTrue(evidence['passed'], evidence)
                    self.assertEqual(host.polled_add_states, ['ESCROWED', 'ESCROWED'])
                    self.assertEqual(sum(op.endswith(':add') for op in host.mutations), 1)
                    self.assertEqual(sum(op.endswith(':remove') for op in host.mutations), 2)
                    self.assertEqual(sum(op.endswith(':refund') for op in host.mutations), 1)
                    self.assertEqual(host.counts, {'SR1': 1, 'SR2': 1})
                finally:
                    if host.transfer_store is not None:
                        host.transfer_store.close()
            with TransferStore(database) as reopened:
                self.assertEqual(reopened.list_in_flight(), [])


if __name__ == '__main__':
    unittest.main()
