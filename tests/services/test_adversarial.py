"""Adversarial and fuzz tests across subsystems (deterministic seeds, no timing)."""

import copy
import random
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal, JournalError
from bridge.deploy.executor import DeployExecutor, DeployJournal, MemoryFS
from bridge.deploy.planner import plan_deploy
from bridge.multiplayer.auth import DevTokenAuth
from bridge.multiplayer.protocol import BODIES, message
from bridge.multiplayer.server import BridgeServer, ServerConfig
from bridge.sim.faults import Fault, FaultPlan
from bridge.transition.machine import EVENTS, TransitionMachine, make_intent
from bridge.verification.receipts import make_receipt
from services.test_counterpart_core import NOW, READBACK, SESSION_A, Harness, receipts_for, wire
from services.test_deploy import ARTIFACTS, unit


class ReceiptReplayTests(unittest.TestCase):
    def test_receipts_for_one_creation_never_verify_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            try:
                pid_a, cid_a = h.created('SR1', 'SR2')
                pid_b, cid_b = h.created('SR2', 'SR1')
                for realm, cid in (('SR2', cid_a), ('SR1', cid_b)):
                    h.svc.on_wire_result(realm, wire(realm, cid, f'{cid}:create'), session=SESSION_A)
                    h.svc.plan_verification(cid, build_fingerprint='build-1')
                stolen = receipts_for(h.svc, cid_a, realm='SR2')
                retargeted = [make_receipt(r['check_id'], 'counterpart', cid_b, r['status'], r['evidence_type'],
                                           realm='SR1', build_fingerprint='build-1', provider=READBACK['SR1'],
                                           observed_at=NOW, artifact_hashes=r['artifact_hashes'],
                                           details=r['details']) for r in stolen if r['evidence_type'] != 'NONE']
                result = h.svc.verify(cid_b, stolen + retargeted)
                self.assertEqual(result['phase'], 'READBACK_PENDING')
                reasons = {r['reason'] for r in result['rejected']}
                self.assertTrue({'WRONG_SUBJECT', 'UNBOUND_SAVE'} <= reasons, reasons)
            finally:
                h.close()

    def test_counterpart_journal_from_a_newer_bridge_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'journal.sqlite3'
            CounterpartJournal(path).close()
            db = sqlite3.connect(path)
            db.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
            db.commit()
            db.close()
            with self.assertRaises(JournalError) as ctx:
                CounterpartJournal(path)
            self.assertEqual(ctx.exception.code, 'SCHEMA_TOO_NEW')


class CounterpartFuzzTests(unittest.TestCase):
    def test_mutated_replies_never_link_or_duplicate(self):
        rng = random.Random(20261004)
        fields = ('realm', 'op_id', 'kind', 'creation_id', 'pid', 'ok', 'applied', 'verified', 'uncertain',
                  'persisted', 'save_id', 'save_file', 'sha256', 'can_create')
        values = (None, '', 'SR1', 'SR2', 0, 1, True, False, 'x' * 300, 'sb2_' + 'f' * 32, '../x', [], {}, 4100)
        with tempfile.TemporaryDirectory() as tmp:
            h = Harness(tmp)
            try:
                pid, cid = h.created('SR1', 'SR2')
                good = wire('SR2', cid, f'{cid}:create')
                for _ in range(400):
                    reply = dict(good)
                    for field in rng.sample(fields, rng.randint(1, 4)):
                        reply[field] = rng.choice(values)
                    verdict = h.svc.on_wire_result(rng.choice(('SR1', 'SR2')), reply, session=SESSION_A)
                    self.assertIn(verdict['verdict'], ('ACCEPTED', 'DUPLICATE', 'REJECTED'))
                intent = h.journal.get(cid)
                self.assertNotIn(intent['phase'], ('VERIFIED', 'LINK_PENDING', 'LINKED'))
                if intent['observed'] is not None:
                    self.assertEqual(intent['observed']['native_save_id'], 'sb2_' + cid)
                self.assertEqual(h.store.get(pid)['state'], 'CREATING')
                self.assertEqual([c['kind'] for _, c, _ in h.transport.sent], ['create_save'])
            finally:
                h.close()


class TransitionFuzzTests(unittest.TestCase):
    def test_random_event_floods_never_commit_without_readiness(self):
        endpoint = {'SR1': {'realm': 'SR1', 'installation_id': 'i1', 'native_save_id': 's1', 'native_save_identity': 'x1'},
                    'SR2': {'realm': 'SR2', 'installation_id': 'i2', 'native_save_id': 's2', 'native_save_identity': 'x2'}}
        sessions = [{'pid': 10, 'session_id': 'a'}, {'pid': 20, 'session_id': 'b'}, {'pid': 30, 'session_id': 'c'}, None]
        with tempfile.TemporaryDirectory() as tmp:
            machine = TransitionMachine(Path(tmp) / 't.sqlite3', trust={})
            try:
                for seed in range(25):
                    rng = random.Random(seed)
                    tid = machine.begin(make_intent(
                        profile_id=f'p{seed}', source={'realm': 'SR1', 'session': sessions[0], 'endpoint': endpoint['SR1']},
                        destination={'realm': 'SR2', 'session': sessions[1], 'endpoint': endpoint['SR2']}))
                    for _ in range(300):
                        event = rng.choice(EVENTS + ('bogus',))
                        payload = rng.choice(({}, dict(endpoint['SR2']), {'step': 'UNLOCK_SOURCE'},
                                              {'report': {'schema': 'bridge-world-ready'}, 'expected_build': 'b'}))
                        result = machine.apply(tid, event, session=rng.choice(sessions), payload=payload)
                        self.assertIn(result['verdict'], ('ACCEPTED', 'DUPLICATE', 'REJECTED'))
                    final = machine.get(tid)
                    # No trusted provider exists, so WORLD_READY can never be accepted and nothing may commit.
                    self.assertFalse(final['facts']['world_ready'])
                    self.assertNotEqual(final['stage'], 'COMMITTED')
                    self.assertFalse(final['facts']['destination_visible'] and not final['facts']['world_ready'])
                    if final['stage'] not in ('ROLLED_BACK', 'FAILED'):
                        machine.apply(tid, 'rollback')
            finally:
                machine.close()


class NetworkFuzzTests(unittest.TestCase):
    def test_garbage_never_crashes_or_authenticates(self):
        rng = random.Random(7)
        server = BridgeServer(ServerConfig(name='Fuzz', mode='HYBRID'),
                              DevTokenAuth({'token-alice-0123456789': 'alice'}, development_only=True), clock=lambda: 0)
        server.connect('c1')
        junk = (None, '', 'HELLO', -1, 2 ** 60, [], {}, {'nested': {'deep': [1, 2, 3]}}, 'x' * 5000, '‮', True)
        for _ in range(1500):
            msg = message(rng.choice(tuple(BODIES)), {}, seq=rng.randint(0, 5))
            for key in rng.sample(list(msg), rng.randint(0, 3)):
                msg[key] = rng.choice(junk)
            if isinstance(msg.get('body'), dict) and rng.random() < .5:
                msg['body'] = {k: rng.choice(junk) for k in ('versions', 'auth', 'text', 'channel', 'transition')}
            replies = server.receive('c1', msg)
            self.assertTrue(all(reply['type'] in ('REJECT', 'PONG') for _, reply in replies))
        self.assertEqual(server.sessions, {})


class DeployAdversarialTests(unittest.TestCase):
    def test_interrupted_rollback_resumes_and_restores_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeployJournal(Path(tmp) / 'd.sqlite3')
            fs = MemoryFS()
            try:
                v1 = unit('fuzz', (1, 0, 0))
                DeployExecutor(journal, fs, ARTIFACTS.get).apply(
                    plan_deploy(journal.inventory('SR1'), [{'key': v1['key'], 'state': 'ENABLED', 'unit': v1}]))
                before = {k: v for k, v in fs.files.items() if not k.startswith('.bridge/')}
                v2 = unit('fuzz', (2, 0, 0), files={'fuzz/mesh.bin': 'v2', 'fuzz/extra.bin': 'v2-extra'})
                crashing = DeployExecutor(journal, fs, ARTIFACTS.get, faults=FaultPlan([
                    Fault('deploy.postcondition'), Fault('deploy.crash_during_rollback', on_hit=2)]))
                with self.assertRaises(Exception):
                    crashing.apply(plan_deploy(journal.inventory('SR1'), [{'key': v2['key'], 'state': 'ENABLED',
                                                                           'unit': v2}], history=journal.history()))
                self.assertEqual(journal.txn(journal.open_txns()[0])['state'], 'ROLLING_BACK')
                self.assertEqual(set(DeployExecutor(journal, fs, ARTIFACTS.get).recover().values()), {'ROLLED_BACK'})
                self.assertEqual({k: v for k, v in fs.files.items() if not k.startswith('.bridge/')}, before)
            finally:
                journal.close()

    def test_random_desired_states_never_yield_an_executable_conflicting_plan(self):
        rng = random.Random(99)
        units = [unit(f'u{i}', provides=[f'slime.shared{i % 3}'], files={f'u{i}/a.bin': f'u{i}',
                                                                         f'common/{i % 2}.bin': str(i)},
                      requires=[f'generated:u{(i + 1) % 6}:SR1'] if i % 4 == 0 else [])
                 for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeployJournal(Path(tmp) / 'd.sqlite3')
            try:
                for _ in range(300):
                    desired = [{'key': u['key'], 'state': rng.choice(('ENABLED', 'DISABLED', 'REMOVED', 'QUARANTINED')),
                                'unit': u} for u in rng.sample(units, rng.randint(1, 6))]
                    plan = plan_deploy(journal.inventory('SR1'), copy.deepcopy(desired))
                    if not plan['executable']:
                        continue
                    enabled = {k: v['unit'] for k, v in plan['final_units'].items() if v['state'] == 'ENABLED'}
                    provided = [cid for u in enabled.values() for cid in u['provides']]
                    self.assertEqual(len(provided), len(set(provided)))
                    paths = [p.casefold() for k, v in plan['final_units'].items() for p in
                             {f"{v['state']}/{f['path']}" for f in v['unit']['files']}]
                    self.assertEqual(len(paths), len(set(paths)))
                    for u in enabled.values():
                        self.assertTrue(set(u['requires']) <= set(enabled))
            finally:
                journal.close()


if __name__ == '__main__':
    unittest.main()
