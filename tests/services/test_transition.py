"""Warm transition machine, coordinator, simulator and UI projection."""

import tempfile
import unittest
from pathlib import Path

from bridge.sim.faults import Fault
from bridge.transition.coordinator import next_command
from bridge.transition.machine import FORWARD, ROLL_FORWARD_FROM, TransitionError, TransitionMachine, make_intent
from bridge.transition.simulator import SCENARIOS, TransitionSimulation
from bridge.transition.view import view_model


class Sim:
    def __init__(self, **kwargs):
        self._tmp = tempfile.TemporaryDirectory()
        self.sim = TransitionSimulation(Path(self._tmp.name), **kwargs)

    def __enter__(self):
        return self.sim

    def __exit__(self, *_):
        self.sim.close()
        self._tmp.cleanup()


ENDPOINT = {'SR1': {'realm': 'SR1', 'installation_id': 'i1', 'native_save_id': 's1', 'native_save_identity': 'x1'},
            'SR2': {'realm': 'SR2', 'installation_id': 'i2', 'native_save_id': 's2', 'native_save_identity': 'x2'}}
SRC = {'pid': 10, 'session_id': 'a'}
DST = {'pid': 20, 'session_id': 'b'}


def intent(**over):
    fields = dict(profile_id='p', source={'realm': 'SR1', 'session': SRC, 'endpoint': ENDPOINT['SR1']},
                  destination={'realm': 'SR2', 'session': DST, 'endpoint': ENDPOINT['SR2']})
    fields.update(over)
    return make_intent(**fields)


class MachineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.machine = TransitionMachine(Path(self._tmp.name) / 't.sqlite3', trust={'local.world-ready': 'SR2'})

    def tearDown(self):
        self.machine.close()
        self._tmp.cleanup()

    def test_world_ready_uses_the_durable_expected_build_not_the_payload(self):
        transition_intent = intent(expected_build='pinned-build')
        tid = self.machine.begin(transition_intent)
        self.machine.apply(tid, 'source_locked', session=SRC)
        self.machine.apply(tid, 'source_synced', session=SRC)
        self.machine.apply(tid, 'destination_started', session=DST)
        self.machine.apply(tid, 'correct_save_confirmed', session=DST, payload=ENDPOINT['SR2'])
        self.machine.close()
        self.machine = TransitionMachine(Path(self._tmp.name) / 't.sqlite3', trust={'local.world-ready': 'SR2'})
        self.assertEqual(self.machine.get(tid)['intent']['expected_build'], 'pinned-build')
        before = self.machine.get(tid)
        report = {'schema': 'bridge-world-ready', 'schema_version': 1, **ENDPOINT['SR2'], 'session': DST,
                  'build_fingerprint': 'forged-build', 'process_running': True, 'load_callback_returned': True,
                  'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True,
                  'observed_at': '2026-01-01T00:00:00Z',
                  'provider': {'provider_id': 'local.world-ready', 'provider_version': '1'},
                  'evidence': 'RUNTIME_OBSERVATION'}
        forged = self.machine.apply(tid, 'world_ready', session=DST,
                                    payload={'report': report, 'expected_build': 'forged-build'})
        self.assertEqual(forged['verdict'], 'REJECTED', forged)
        self.assertIn('WORLD_READY_WRONG_BUILD_FINGERPRINT', forged['code'])
        self.assertEqual(self.machine.get(tid), before)
        omitted = self.machine.apply(tid, 'world_ready', session=DST, payload={'report': report})
        self.assertEqual(omitted['verdict'], 'REJECTED', omitted)
        self.assertIn('WORLD_READY_WRONG_BUILD_FINGERPRINT', omitted['code'])
        self.assertEqual(self.machine.get(tid), before)
        report['build_fingerprint'] = 'pinned-build'
        accepted = self.machine.apply(tid, 'world_ready', session=DST,
                                      payload={'report': report, 'expected_build': 'forged-build'})
        self.assertEqual(accepted, {'verdict': 'ACCEPTED', 'stage': 'DESTINATION_WORLD_READY'})

    def test_intent_pins_the_destination_expected_build(self):
        transition_intent = intent(expected_build='pinned-build')
        self.assertEqual(transition_intent['expected_build'], 'pinned-build')
        tid = self.machine.begin(transition_intent)
        transition_intent['expected_build'] = 'changed-after-begin'
        self.machine.close()
        self.machine = TransitionMachine(Path(self._tmp.name) / 't.sqlite3', trust={'local.world-ready': 'SR2'})
        self.assertEqual(self.machine.get(tid)['intent']['expected_build'], 'pinned-build')

    def test_world_ready_without_a_pinned_build_fails_closed(self):
        tid = self.machine.begin(intent())
        self.machine.apply(tid, 'source_locked', session=SRC)
        self.machine.apply(tid, 'source_synced', session=SRC)
        self.machine.apply(tid, 'destination_started', session=DST)
        self.machine.apply(tid, 'correct_save_confirmed', session=DST, payload=ENDPOINT['SR2'])
        report = {'schema': 'bridge-world-ready', 'schema_version': 1, **ENDPOINT['SR2'], 'session': DST,
                  'build_fingerprint': 'b', 'process_running': True, 'load_callback_returned': True,
                  'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True,
                  'observed_at': '2026-01-01T00:00:00Z',
                  'provider': {'provider_id': 'local.world-ready', 'provider_version': '1'},
                  'evidence': 'RUNTIME_OBSERVATION'}
        before = self.machine.get(tid)
        result = self.machine.apply(tid, 'world_ready', session=DST, payload={'report': report, 'expected_build': 'b'})
        self.assertEqual(result['verdict'], 'REJECTED', result)
        self.assertIn('WORLD_READY_WRONG_BUILD_FINGERPRINT', result['code'])
        self.assertEqual(self.machine.get(tid), before)

    def test_intent_validation_and_single_active_transition(self):
        with self.assertRaises(TransitionError):
            intent(destination={'realm': 'SR1', 'session': None, 'endpoint': ENDPOINT['SR1']})
        with self.assertRaises(TransitionError):
            intent(source={'realm': 'SR1', 'session': None, 'endpoint': ENDPOINT['SR1']})
        tid = self.machine.begin(intent())
        with self.assertRaises(TransitionError):
            self.machine.begin(intent())
        self.machine.apply(tid, 'rollback')
        self.assertEqual(self.machine.get(tid)['stage'], 'ROLLED_BACK')
        self.machine.begin(intent())

    def test_bound_events_and_illegal_orders_are_rejected(self):
        tid = self.machine.begin(intent(expected_build='b'))
        self.assertEqual(self.machine.apply(tid, 'destination_started', session=DST)['code'], 'ILLEGAL_EVENT_FOR_STAGE')
        self.assertEqual(self.machine.apply(tid, 'source_locked', session=DST)['code'], 'WRONG_SESSION')
        self.assertEqual(self.machine.apply(tid, 'source_locked', session=SRC)['stage'], 'SOURCE_LOCKED')
        # destination cannot start before the source state is synced
        self.assertEqual(self.machine.apply(tid, 'destination_started', session=DST)['verdict'], 'REJECTED')
        self.machine.apply(tid, 'source_synced', session=SRC)
        self.assertEqual(self.machine.apply(tid, 'destination_started', session={'pid': 99, 'session_id': 'z'})['code'],
                         'WRONG_SESSION')
        self.assertEqual(self.machine.apply(tid, 'destination_started', session=DST)['stage'], 'DESTINATION_STARTING')
        wrong = dict(ENDPOINT['SR2'], native_save_id='other')
        self.assertEqual(self.machine.apply(tid, 'correct_save_confirmed', session=DST, payload=wrong)['code'],
                         'WRONG_SAVE')
        self.assertEqual(self.machine.apply(tid, 'destination_visible', session=DST)['code'], 'ILLEGAL_EVENT_FOR_STAGE')
        self.machine.apply(tid, 'correct_save_confirmed', session=DST, payload=ENDPOINT['SR2'])
        report = {'schema': 'bridge-world-ready', 'schema_version': 1, 'realm': 'SR2', 'session': DST,
                  'installation_id': 'i2', 'native_save_id': 's2', 'native_save_identity': 'x2',
                  'build_fingerprint': 'b', 'process_running': True, 'load_callback_returned': True,
                  'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True,
                  'observed_at': '2026-01-01T00:00:00Z', 'provider': {'provider_id': 'other', 'provider_version': '1'},
                  'evidence': 'RUNTIME_OBSERVATION'}
        untrusted = self.machine.apply(tid, 'world_ready', session=DST, payload={'report': report, 'expected_build': 'b'})
        self.assertIn('WORLD_READY_UNTRUSTED', untrusted['code'])
        synthetic = dict(report, provider={'provider_id': 'local.world-ready', 'provider_version': '1'},
                         evidence='SYNTHETIC')
        self.assertIn('WORLD_READY_NOT_RUNTIME', self.machine.apply(
            tid, 'world_ready', session=DST, payload={'report': synthetic, 'expected_build': 'b'})['code'])
        good = dict(synthetic, evidence='RUNTIME_OBSERVATION')
        generation = self.machine.get(tid)['generation']
        self.assertEqual(self.machine.apply(tid, 'world_ready', session=DST, generation=generation - 1,
                                            payload={'report': good, 'expected_build': 'b'})['code'], 'STALE_GENERATION')
        self.assertEqual(self.machine.apply(tid, 'world_ready', session=DST, payload={'report': good})['stage'],
                         'DESTINATION_WORLD_READY')
        self.assertEqual(self.machine.apply(tid, 'world_ready', session=DST,
                                            payload={'report': good, 'expected_build': 'b'})['verdict'], 'DUPLICATE')

    def test_rollback_compensations_follow_facts_and_terminal_is_final(self):
        tid = self.machine.begin(intent())
        self.machine.apply(tid, 'source_locked', session=SRC)
        self.machine.apply(tid, 'source_synced', session=SRC)
        self.machine.apply(tid, 'destination_started', session=DST)
        result = self.machine.apply(tid, 'rollback', payload={'reason': 'cancel'})
        self.assertEqual(result['stage'], 'ROLLING_BACK')
        self.assertEqual(self.machine.get(tid)['compensations'], ['UNLOCK_SOURCE', 'PARK_DESTINATION'])
        self.assertEqual(next_command(self.machine.get(tid))['command'], 'unlock')
        before = self.machine.get(tid)
        self.assertEqual(self.machine.apply(tid, 'compensation_done', payload={'step': 'PARK_DESTINATION'}),
                         {'verdict': 'REJECTED', 'stage': 'ROLLING_BACK', 'code': 'ILLEGAL_EVENT_FOR_STAGE'})
        self.assertEqual(self.machine.get(tid), before)
        self.assertEqual(self.machine.apply(tid, 'compensation_done', payload={'step': 'NOT_PENDING'})['verdict'],
                         'REJECTED')
        self.assertEqual(self.machine.get(tid), before)
        self.assertEqual(self.machine.apply(tid, 'compensation_done', payload={'step': 'UNLOCK_SOURCE'})['stage'],
                         'ROLLING_BACK')
        before = self.machine.get(tid)
        self.assertEqual(before['compensations'], ['PARK_DESTINATION'])
        self.assertFalse(before['facts']['source_locked'])
        self.assertEqual(self.machine.apply(tid, 'compensation_done', payload={'step': 'UNLOCK_SOURCE'})['verdict'],
                         'REJECTED')
        self.assertEqual(self.machine.get(tid), before)
        self.assertEqual(self.machine.apply(tid, 'compensation_done', payload={'step': 'PARK_DESTINATION'})['stage'],
                         'ROLLED_BACK')
        self.assertEqual(self.machine.apply(tid, 'commit')['code'], 'TRANSITION_FINISHED')
        self.assertEqual(self.machine.apply(tid, 'not_an_event')['code'], 'UNKNOWN_EVENT')

    def test_non_dict_payloads_are_logged_rejections_without_state_changes(self):
        tid = self.machine.begin(intent())
        for payload in ([], ['bad'], (), ('bad',), '', 'bad', 0, 1, False, True):
            with self.subTest(payload=payload):
                before = self.machine.get(tid)
                result = self.machine.apply(tid, 'source_synced', session=SRC, payload=payload)
                self.assertEqual(result, {'verdict': 'REJECTED', 'stage': 'PREPARING', 'code': 'MALFORMED_PAYLOAD'})
                self.assertEqual(self.machine.get(tid), before)
                logged = self.machine.events(tid)[-1]
                self.assertEqual((logged['event'], logged['verdict'], logged['details']),
                                 ('source_synced', 'REJECTED', {'code': 'MALFORMED_PAYLOAD'}))
        self.assertEqual(self.machine.apply(tid, 'source_synced', session=SRC, payload=None)['verdict'], 'ACCEPTED')
        self.assertEqual(self.machine.apply(tid, 'source_locked', session=SRC, payload={})['stage'], 'SOURCE_LOCKED')

    def test_synthetic_trust_refused_outside_simulation(self):
        with self.assertRaises(TransitionError):
            TransitionMachine(Path(self._tmp.name) / 'x.sqlite3', trust={'sim.world-ready.sr2': 'SR2'})


class SimulatorTests(unittest.TestCase):
    def test_scenarios(self):
        for name, (kwargs, expected) in SCENARIOS.items():
            with self.subTest(scenario=name), Sim(**kwargs) as sim:
                report = sim.run()
                self.assertEqual(report['stage'], expected, report)
                self.assertTrue(all(report['invariants'].values()), report)

    def test_stale_and_duplicate_world_ready_are_harmless(self):
        with Sim(**SCENARIOS['stale_world_ready'][0]) as sim:
            report = sim.run()
            self.assertTrue(any(code and code.startswith('WRONG_SESSION') for code in report['rejections']), report)
        with Sim(**SCENARIOS['duplicate_world_ready'][0]) as sim:
            self.assertIn(('world_ready', 'DUPLICATE'), sim.run()['events'])

    def test_bridge_restart_at_every_persisted_stage(self):
        for stage in FORWARD[1:-1]:
            for source, destination in (('SR1', 'SR2'), ('SR2', 'SR1')):
                with self.subTest(stage=stage, direction=source), \
                        Sim(source=source, destination=destination,
                            faults=(Fault(f'bridge.restart_at:{stage}'),)) as sim:
                    report = sim.run()
                    expected = 'COMMITTED' if stage in ROLL_FORWARD_FROM else 'ROLLED_BACK'
                    self.assertEqual(report['stage'], expected, report)
                    self.assertEqual(report['restarts'], 1)
                    self.assertTrue(all(report['invariants'].values()), report)

    def test_destination_crash_at_every_stage_returns_the_player(self):
        for stage in FORWARD[FORWARD.index('DESTINATION_STARTING'):FORWARD.index('COMMITTED')]:
            with self.subTest(stage=stage), Sim(faults=(Fault(f'destination.crash_at:{stage}'),)) as sim:
                report = sim.run()
                self.assertEqual(report['stage'], 'ROLLED_BACK', report)
                self.assertTrue(all(report['invariants'].values()), report)


class ViewTests(unittest.TestCase):
    def test_view_follows_state_not_time(self):
        modes, progress, cancellable = [], [], []

        def observe(entry, transition):
            view = view_model(transition)
            modes.append(view['mode'])
            progress.append(view['progress'])
            cancellable.append(view['can_cancel'])
        with Sim() as sim:
            sim.driver.run(sim.transition_id, on_step=observe)
        self.assertEqual(progress, sorted(progress))
        for mode in ('PREPARING', 'VERIFYING_SAVE', 'LOADING_WORLD', 'READY', 'SWITCHING', 'COMMITTED'):
            self.assertIn(mode, modes)
        self.assertTrue(cancellable[0])
        self.assertFalse(cancellable[-1])
        self.assertEqual(view_model(None)['mode'], 'IDLE')
        self.assertEqual(view_model(None, provider_available=False)['mode'], 'OFFLINE')
        with Sim(faults=(Fault('cancel_at:TRANSITION_READY'),)) as sim:
            sim.run()
            final = view_model(sim.current())
            self.assertEqual((final['mode'], final['can_cancel']), ('ROLLED_BACK', False))
            self.assertIn('back in Slime Rancher', final['title'])

if __name__ == '__main__':
    unittest.main()
