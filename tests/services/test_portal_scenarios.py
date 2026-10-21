"""Portal gameplay contracts and the runtime scenario framework."""

import copy
import unittest

from bridge.app.content import catalogue
from bridge.portal import contracts as P
from bridge.scenarios import catalog as CAT
from bridge.scenarios.model import evaluate_run, validate_scenario
from conversion.test_batch import run

GATE = P.DEFAULT_PORTALS['portal.sr1.sanctuary_gate']
RETURN = P.DEFAULT_PORTALS['portal.sr2.return_portal']


def facts(**over):
    base = {'unlocked': True, 'balance': 0, 'flags': set(), 'known_species': set(), 'counterpart': 'USE_EXISTING_LINK',
            'transition_stage': None, 'provider_available': True, 'cooldown_remaining': 0}
    base.update(over)
    return base


class PortalTests(unittest.TestCase):
    def test_defaults_capture_project_requirements(self):
        for portal in P.DEFAULT_PORTALS.values():
            self.assertEqual(P.validate_portal(portal), [])
        self.assertEqual((GATE['cost']['resource'], GATE['cost']['amount']), ('sr1.plort.gold', 10))
        self.assertEqual((RETURN['cost']['resource'], RETURN['cost']['amount']), ('sr2.currency.newbucks', 250000))
        cheaper = P.configure(RETURN, cost_amount=1000)
        self.assertEqual(cheaper['cost']['amount'], 1000)
        self.assertEqual(RETURN['cost']['amount'], 250000)
        with self.assertRaises(ValueError):
            P.configure(RETURN, target_realm='SR2')
        bad = copy.deepcopy(GATE)
        bad['cost']['resource'] = 'sr2.currency.newbucks'
        self.assertTrue(P.validate_portal(bad))

    def test_ui_states(self):
        cases = {
            'LOCKED': facts(unlocked=False, balance=3),
            'REQUIREMENTS_MET': facts(unlocked=False, balance=10),
            'READY': facts(),
            'CREATING_COUNTERPART': facts(counterpart='CREATE_COUNTERPART'),
            'COUNTERPART_MISSING': facts(counterpart='BLOCK_NO_CAPACITY'),
            'DESTINATION_UNAVAILABLE': facts(provider_available=False),
            'PREPARING_TRANSITION': facts(transition_stage='DESTINATION_LOADING'),
            'ACTIVATING': facts(transition_stage='SOURCE_HIDDEN'),
            'ROLLBACK': facts(transition_stage='ROLLING_BACK'),
            'FAILED': facts(transition_stage='FAILED'),
            'COOLDOWN': facts(cooldown_remaining=4),
        }
        for expected, given in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(P.evaluate(GATE, given)['state'], expected)
        unknown_balance = P.evaluate(GATE, facts(unlocked=False, balance=None))
        self.assertEqual(unknown_balance['state'], 'LOCKED')
        flagged = dict(GATE, conditions=[{'kind': 'PROGRESSION_FLAG', 'ref': 'met_the_ranch_tech', 'amount': None}])
        self.assertEqual(P.evaluate(flagged, facts())['state'], 'REQUIREMENTS_UNMET')
        self.assertEqual(P.evaluate(flagged, facts(flags={'met_the_ranch_tech'}))['state'], 'READY')

    def test_locked_portal_without_payable_unlock_stays_locked(self):
        portal = P.configure(GATE, cost=None)
        for balance in (None, 0, 10):
            with self.subTest(balance=balance):
                result = P.evaluate(portal, facts(unlocked=False, balance=balance))
                self.assertEqual(result['state'], 'LOCKED')
                self.assertFalse(result['needs_payment'])
                self.assertEqual(result['checklist'], [])
        self.assertEqual(P.evaluate(portal, facts(unlocked=True))['state'], 'READY')
        unlocked_portal = P.configure(portal, lock=dict(portal['lock'], starts_locked=False))
        self.assertEqual(P.evaluate(unlocked_portal, facts(unlocked=False))['state'], 'READY')

    def test_charge_is_only_planned(self):
        self.assertEqual(P.plan_charge(GATE, 'p', balance=None)['status'], 'BLOCKED_ON_LOCAL_QUERY')
        self.assertEqual(P.plan_charge(GATE, 'p', balance=4)['missing'], 6)
        plan = P.plan_charge(GATE, 'p', balance=12)
        self.assertEqual((plan['status'], plan['settlement'], plan['refund_on_rollback']), ('PLANNED', 'LOCAL_ESCROW', True))

    def test_sanctuary_keeps_impossible_species_decorative(self):
        roster = P.sanctuary_roster(catalogue({'x': run()}), GATE)
        self.assertIn('slime.pink', roster['playable'])
        self.assertIn('slime.azure', roster['playable'])
        self.assertIn('slime.hollow', roster['decorative'])
        hidden = P.sanctuary_roster(catalogue({'x': run()}), dict(GATE, sanctuary={'decorative_policy':
                                                                                    'HIDE_NON_CONVERTIBLE'}))
        self.assertEqual(hidden['decorative'], [])


def lab_report(doc, events, state=None, *, provider='local.lab', synthetic=False, artifacts=None):
    return {'schema': 'bridge-scenario-run', 'schema_version': 1, 'scenario_id': doc['id'],
            'provider': {'provider_id': provider, 'provider_version': '1'}, 'realms': doc['realms'],
            'build_fingerprints': {r: f'{r.lower()}-build' for r in doc['realms']},
            'session': {'pid': 10, 'session_id': 'lab'}, 'observed_events': events,
            'persisted_state': state if state is not None else dict(doc['expected_state']),
            'artifacts': artifacts if artifacts is not None else {a: 'a' * 64 for a in doc['proof_artifacts']},
            'started_at': '2026-10-04T10:00:00Z', 'finished_at': '2026-10-04T10:05:00Z', 'synthetic': synthetic}


class ScenarioTests(unittest.TestCase):
    def test_catalogue_is_valid_complete_and_exported(self):
        self.assertEqual(CAT.problems(), {})
        ids = {s['id'] for s in CAT.catalogue()}
        for required in ('counterpart.sr1_to_sr2', 'counterpart.sr2_to_sr1', 'counterpart.slots_full',
                         'counterpart.slot_race', 'counterpart.lost_ack', 'counterpart.bridge_restart',
                         'system.game_update', 'transition.fails_mid_switch', 'net.reconnect',
                         'content.package_hash_mismatch'):
            self.assertIn(required, ids)
        self.assertTrue(all(s['status'] == 'NOT_RUN' for s in CAT.catalogue()))
        self.assertEqual(CAT.EXPORT.read_text(encoding='utf-8'), CAT.export_text())
        broken = dict(CAT.catalogue()[0], forbidden_events=['counterpart.linked'])
        self.assertTrue(validate_scenario(broken))

    def test_run_evaluation_requires_trusted_runtime(self):
        doc = next(s for s in CAT.catalogue() if s['id'] == 'counterpart.sr1_to_sr2')
        trust = {'local.lab': 'BOTH'}
        good = evaluate_run(doc, lab_report(doc, ['net.connected'] + doc['expected_events']), trust=trust)
        self.assertEqual((good['verdict'], good['runtime'], len(good['receipts'])), ('PASSED', True, 2))
        self.assertEqual({r['realm'] for r in good['receipts']}, {'SR1', 'SR2'})
        untrusted = evaluate_run(doc, lab_report(doc, doc['expected_events'], provider='random'), trust=trust)
        self.assertEqual((untrusted['verdict'], untrusted['receipts']), ('PASSED_SYNTHETIC', []))
        synthetic = evaluate_run(doc, lab_report(doc, doc['expected_events'], synthetic=True), trust=trust)
        self.assertEqual(synthetic['verdict'], 'PASSED_SYNTHETIC')
        forbidden = evaluate_run(doc, lab_report(doc, doc['expected_events'] + ['counterpart.duplicate_created']),
                                 trust=trust)
        self.assertEqual(forbidden['verdict'], 'FAILED')
        order = evaluate_run(doc, lab_report(doc, list(reversed(doc['expected_events']))), trust=trust)
        self.assertIn('EVENTS_OUT_OF_ORDER', order['reasons'])
        missing = evaluate_run(doc, lab_report(doc, doc['expected_events'][:2], artifacts={}), trust=trust)
        self.assertEqual(missing['verdict'], 'INCOMPLETE')
        wrong_state = evaluate_run(doc, lab_report(doc, doc['expected_events'], state={'profile.state': 'CREATING'}),
                                   trust=trust)
        self.assertEqual(wrong_state['verdict'], 'FAILED')
        self.assertEqual(evaluate_run(doc, {'schema': 'x'}, trust=trust)['verdict'], 'INVALID')

    def test_synthetic_rehearsals_follow_the_scenarios_but_never_count_as_runtime(self):
        rehearsed = [s for s in CAT.catalogue() if s['rehearsal']]
        self.assertGreaterEqual(len(rehearsed), 10)
        for doc in rehearsed:
            with self.subTest(scenario=doc['id']):
                report = CAT.rehearse(doc['id'])
                result = evaluate_run(doc, report, trust={'sim.rehearsal': 'BOTH'})
                self.assertFalse(result['runtime'])
                self.assertNotIn(result['verdict'], ('PASSED', 'FAILED'), result)
                residual = [r for r in result['reasons'] if not r.startswith('MISSING_ARTIFACTS')]
                self.assertEqual(residual, [], result)  # simulator and catalogue agree on events/state
                self.assertFalse(set(doc['forbidden_events']) & set(report['observed_events']), report)


if __name__ == '__main__':
    unittest.main()
