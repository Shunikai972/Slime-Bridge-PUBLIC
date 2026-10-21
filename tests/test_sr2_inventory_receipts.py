"""Production-host gates fed by linked SR2 adapter wire JSON, not native save evidence."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.host.server import BridgeHost, _valid_command_result
from bridge.transfer.coordinator import _succeeded
from bridge.transfer.store import TransferStore
from transfer_binding_fixtures import PairFixture


class Sr2InventoryWireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get('SR2_INVENTORY_WIRE')
        if path is None:
            raise unittest.SkipTest('Run linked Sr2InventoryHarness to provide SR2_INVENTORY_WIRE')
        cls.rows = json.loads(Path(path).read_text())

    def test_success_wire_passes_unchanged_production_gate(self):
        rows = [r for r in self.rows if r['scenario'] == 'success']
        self.assertEqual(len(rows), 6)
        for row in rows:
            with self.subTest(kind=row['kind'], item=row['item']):
                self.assertTrue(_valid_command_result(row['wire']))
                delta = -3 if row['kind'] == 'remove' else 3
                self.assertTrue(_succeeded(row['wire'], delta))

    def test_old_boolean_only_success_is_rejected_and_remains_deliverable(self):
        row = next(r['wire'] for r in self.rows if r['scenario'] == 'success')
        old = {k: v for k, v in row.items() if k not in ('verified', 'save_file', 'sha256')}
        old.update(ok=True, applied=True, persisted=True, uncertain=False)
        self.assertFalse(_valid_command_result(old))
        self.assertFalse(_succeeded(old, 3))
        self.exchange(old, expected=400)

    def test_restarted_adapter_replay_uses_current_process_and_proof(self):
        row = next(r for r in self.rows if r.get('change') == 'none')
        self.assertNotEqual(row['wire']['pid'], row['historical_pid'])
        self.assertTrue(_valid_command_result(row['wire']))
        self.assertTrue(_succeeded(row['wire'], 3))
        self.exchange(row['wire'], expected=200)

    def test_stale_historical_pid_is_rejected_by_actual_host(self):
        row = next(r for r in self.rows if r.get('change') == 'none')
        current = row['wire']
        # Exercise the PID fence with a valid proof; isolate it from the old missing-proof bug.
        complete = {**current, 'ok': True, 'applied': True, 'persisted': True,
                    'verified': True, 'uncertain': False, 'save_file': 'fixture_save_1',
                    'sha256': 'a' * 64, 'pid': row['historical_pid']}
        self.exchange(complete, expected=409, session_pid=current['pid'])

    def test_failed_replay_is_reconciliation_not_success(self):
        rows = [r for r in self.rows if r['scenario'] == 'replay' and r['change'] != 'none']
        self.assertEqual(len(rows), 10)
        for row in rows:
            with self.subTest(change=row['change']):
                self.assertTrue(_valid_command_result(row['wire']))
                self.assertFalse(_succeeded(row['wire'], 3))
                self.assertFalse(row['wire']['ok'])
                self.assertTrue(row['wire']['uncertain'])

    def test_success_wire_actual_host_accepts(self):
        for row in (r for r in self.rows if r['scenario'] == 'success'):
            with self.subTest(kind=row['kind'], item=row['item']):
                self.exchange(row['wire'], expected=200)

    def exchange(self, wire, expected, session_pid=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        fixture = PairFixture(root)
        profile = fixture.linked(sender_save='fixture-sr1', receiver_save=wire['save_id'])
        fixture.load(profile)
        store = TransferStore(root / 'synthetic-ledger.db')
        self.addCleanup(store.close)
        host = BridgeHost(token='synthetic-only', transfer_store=store,
                          transfer_bindings=fixture.resolver, endpoint_provider=fixture.report,
                          endpoint_trust=fixture.trust, allow_lab_give=wire['kind'] == 'lab_give')
        wire = dict(wire)
        if wire['kind'] in ('remove', 'add'):
            sender, source, receiver, destination = (
                ('SR2', wire['save_id'], 'SR1', 'fixture-sr1') if wire['kind'] == 'remove' else
                ('SR1', 'fixture-sr1', 'SR2', wire['save_id']))
            binding = fixture.resolver.resolve(profile, sender, source, receiver, destination)
            tid = store.prepare_transfer(sender, source, receiver, destination, wire['item'], wire['count'],
                                         idempotency_key='synthetic-wire-action', binding=binding)
            store.lock(tid)
            if wire['kind'] == 'add':
                store.escrow(tid)
            # Only transport correlation changes; SR2 producer proof/PID/payload fields stay exact.
            wire['op_id'] = tid + ':' + wire['kind']
        host.__enter__()
        try:
            base = host.url
            def request(path, data=None):
                payload = None if data is None else json.dumps(data).encode()
                req = Request(base + path, payload, {'Authorization': 'Bearer synthetic-only',
                                                     'Content-Type': 'application/json'})
                try:
                    with urlopen(req, timeout=3) as response:
                        return response.status, json.loads(response.read())
                except HTTPError as exc:
                    return exc.code, json.loads(exc.read())
            pid = wire['pid'] if session_pid is None else session_pid
            self.assertEqual(request('/v1/heartbeat', {'protocol_version': 1,
                             'message_type': 'heartbeat', 'realm': 'SR2', 'request_id': 'synthetic',
                             'pid': pid, 'status': {'save_id': 'fixture_save', 'player_alive': True}})[0], 200)
            host.enqueue_command('SR2', wire['kind'], wire['item'], wire['count'],
                                 op_id=wire['op_id'], save_id=wire['save_id'])
            self.assertEqual(request('/v1/commands/next?realm=SR2')[1]['command']['op_id'], wire['op_id'])
            status, result = request('/v1/commands/result', wire)
            self.assertEqual(status, expected, result)
            if expected != 200:
                self.assertEqual(request('/v1/commands/next?realm=SR2')[1]['command']['op_id'], wire['op_id'])
        finally:
            host.__exit__(None, None, None)


if __name__ == '__main__':
    unittest.main()
