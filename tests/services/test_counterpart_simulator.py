"""Counterpart simulator: both directions, every failure family, zero duplicates and zero overwrites."""

import tempfile
import unittest
from pathlib import Path

from bridge.counterpart.realms import wire_adapter
from bridge.counterpart.service import CounterpartService
from bridge.counterpart.simulator import SCENARIOS, CounterpartSimulation, _f
from bridge.sim.faults import Fault, FaultPlan


class Sim:
    def __init__(self, **kwargs):
        self._tmp = tempfile.TemporaryDirectory()
        self.sim = CounterpartSimulation(Path(self._tmp.name), **kwargs)

    def __enter__(self):
        return self.sim

    def __exit__(self, *_):
        self.sim.close()
        self._tmp.cleanup()


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_holds_the_invariants(self):
        for name, (kwargs, phase, store_state) in SCENARIOS.items():
            with self.subTest(scenario=name), Sim(**kwargs) as sim:
                report = sim.run()
                self.assertEqual(report['phase'], phase, report)
                self.assertEqual(report['store_state'], store_state, report)
                self.assertEqual(report['invariants'], {'zero_duplicate': True, 'zero_overwrite': True,
                                                        'link_sound': True}, report)
                self.assertLessEqual(report['create_commands'], 1)
                if store_state != 'LINKED':
                    self.assertIsNone(report['linked'])

    def test_both_directions_link_the_realm_specific_save(self):
        for source, target in (('SR1', 'SR2'), ('SR2', 'SR1')):
            with self.subTest(target=target), Sim(source=source, target=target) as sim:
                report = sim.run()
                marker = wire_adapter(target).expected_identity(report['creation_id'])['native_save_id']
                self.assertEqual(report['linked']['native_save_id'], marker)
                self.assertEqual(report['decisions'][:2], ['CREATE_COUNTERPART', 'CREATE_COUNTERPART'])
                self.assertEqual(report['decisions'][-1], 'USE_EXISTING_LINK')

    def test_slot_race_aborts_with_zero_destructive_continuation(self):
        with Sim(**SCENARIOS['slot_race'][0]) as sim:
            report = sim.run()
            self.assertEqual(report['native_writes'], [])
            self.assertEqual(sim.realms['SR1'].saves[0]['save_id'], 'player-made-save')
            self.assertEqual(sim.service.decide(sim.profile_id, 'SR1', provider_health='HEALTHY', runtime_proven=True,
                                                slot_probe=sim.realms['SR1'].probe())['decision'], 'BLOCK_CONFLICT')

    def test_unproven_absence_waits_for_another_process(self):
        with Sim(**SCENARIOS['send_failed_before_delivery'][0]) as sim:
            report = sim.run()
            self.assertEqual(report['phase'], 'UNCERTAIN')
            self.assertEqual(report['create_commands'], 1)
            self.assertEqual(report['native_writes'], [])
            sim.realms['SR2'].crash()
            sim.realms['SR2'].start()  # the game is restarted: absence is now provable
            report = sim.run()
            self.assertEqual(report['phase'], 'FAILED_ABSENT')
            self.assertEqual(report['create_commands'], 1)

    def test_duplicate_dispatch_and_replayed_result_change_nothing(self):
        with Sim(source='SR1', target='SR2') as sim:
            report = sim.run()
            cid = report['creation_id']
            self.assertFalse(sim.service.dispatch(cid, sim.realms['SR2'].session)['dispatched'])
            self.assertEqual(sim.transport_creates(cid), 1)
        with Sim(**SCENARIOS['duplicate_result'][0]) as sim:
            report = sim.run()
            self.assertIn({'verdict': 'DUPLICATE', 'phase': 'NATIVE_OBSERVED'}, report['verdicts'])

    def test_wrong_replies_are_rejected_then_reconciled(self):
        expected = {'wrong_creation_reply': 'NO_DURABLE_INTENT', 'wrong_operation_reply': 'UNBINDABLE_REPLY',
                    'wrong_realm_reply': 'UNBINDABLE_REPLY', 'wrong_process_reply': 'UNBINDABLE_REPLY',
                    'wrong_target_save': 'UNBINDABLE_REPLY'}
        for name, code in expected.items():
            with self.subTest(scenario=name), Sim(**SCENARIOS[name][0]) as sim:
                report = sim.run()
                self.assertEqual(report['verdicts'][0]['code'], code)
                self.assertIn('RECONCILE', report['actions'])

    def test_host_crash_at_every_round_recovers_without_duplicates(self):
        for direction in (('SR1', 'SR2'), ('SR2', 'SR1')):
            for round_no in range(1, 6):
                with self.subTest(direction=direction, crash_on_round=round_no), \
                        Sim(source=direction[0], target=direction[1], faults=(Fault('host.crash', on_hit=round_no),)) as sim:
                    report = sim.run(max_rounds=12)
                    self.assertEqual((report['phase'], report['store_state']), ('LINKED', 'LINKED'), report)
                    self.assertTrue(all(report['invariants'].values()), report)
                    self.assertLessEqual(report['create_commands'], 1)

    def test_combined_faults_never_duplicate(self):
        combos = [(_f('transport.send_after'), _f('host.crash')),
                  (_f('result.drop', times=3), _f('SR2.plugin.crash_after_create')),
                  (_f('result.delay'), _f('result.duplicate', on_hit=1, times=5)),
                  (_f('SR2.plugin.crash_after_create'), _f('host.crash', on_hit=2), _f('link.persist_crash'))]
        for faults in combos:
            with self.subTest(faults=[f.point for f in faults]), Sim(source='SR1', target='SR2', faults=faults) as sim:
                report = sim.run(max_rounds=14)
                self.assertTrue(all(report['invariants'].values()), report)
                self.assertLessEqual(report['create_commands'], 1)
                self.assertEqual(report['phase'], 'LINKED', report)

    def test_synthetic_trust_is_refused_outside_a_simulation(self):
        with self.assertRaises(ValueError):
            CounterpartService(None, None, {'SR1': wire_adapter('SR1')}, None, trust={'sim.readback.sr1': 'SR1'})


class FaultPlanTests(unittest.TestCase):
    def test_faults_fire_deterministically_on_exact_hits(self):
        plan = FaultPlan([Fault('x', on_hit=2, times=2)])
        self.assertEqual([bool(plan.check('x', n=i)) for i in range(5)], [False, True, True, False, False])
        self.assertEqual(plan.fired_points(), ['x', 'x'])
        self.assertEqual(plan.fired[0]['n'], 1)
        with self.assertRaises(ValueError):
            Fault('x', on_hit=0)


if __name__ == '__main__':
    unittest.main()
