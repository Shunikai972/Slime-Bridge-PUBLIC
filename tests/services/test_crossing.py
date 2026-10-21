"""Cross-subsystem flow: linked pair -> portal -> warm transition -> multiplayer realm switch (synthetic)."""

import tempfile
import unittest
from pathlib import Path

from bridge.app.crossing import CrossingError, PortalCrossing
from bridge.counterpart.simulator import SIM_BUILD, CounterpartSimulation
from bridge.multiplayer.auth import DevTokenAuth
from bridge.multiplayer.server import BridgeServer, ServerConfig
from bridge.multiplayer.transport import Client, Network
from bridge.portal.contracts import DEFAULT_PORTALS, configure
from bridge.sim.faults import Fault, FaultPlan
from bridge.transition.coordinator import TransitionDriver
from bridge.transition.machine import TransitionMachine
from bridge.transition.simulator import SimAdapters, SimRealmProcess, provider_for

GATE = DEFAULT_PORTALS['portal.sr1.sanctuary_gate']
FACTS = {'unlocked': True, 'balance': 0, 'flags': set(), 'known_species': set(), 'counterpart': 'USE_EXISTING_LINK',
         'transition_stage': None, 'provider_available': True, 'cooldown_remaining': 0}


class CrossingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.pairs = CounterpartSimulation(root / 'pairs', source='SR1', target='SR2')
        self.pairs.run()
        self.pairs.service.observe_live(self.pairs.profile_id, 'SR1', build_fingerprint=SIM_BUILD)
        self.machine = TransitionMachine(root / 'transitions.sqlite3', allow_synthetic=True,
                                         trust={provider_for(r)['provider_id']: r for r in ('SR1', 'SR2')})
        self.server = BridgeServer(ServerConfig(name='Hub', mode='HYBRID'),
                                   DevTokenAuth({'token-rancher-0123456789': 'rancher'}, development_only=True),
                                   clock=lambda: 0)
        self.net = Network(self.server)
        self.player = self.net.join(Client('Rancher', 'token-rancher-0123456789', content={}, realm='SR1'))
        self.net.send(self.player, self.player.hello())
        self.crossing = PortalCrossing(machine=self.machine, counterparts=self.pairs.service,
                                       portals={**DEFAULT_PORTALS, 'pay.per.use': configure(GATE, portal_id='pay.per.use',
                                                                                            cost=dict(GATE['cost'],
                                                                                                      charge='ON_USE'))},
                                       announce=lambda phase, t: self.net.send(self.player, self.player.switch(t, phase)))

    def tearDown(self):
        self.machine.close()
        self.pairs.close()
        self._tmp.cleanup()

    def drive(self, transition_id, faults=()):
        """Run the transition with fake adapters bound to the pair's real endpoints."""
        intent = self.machine.get(transition_id)['intent']
        src = SimRealmProcess('SR1', intent['source']['endpoint'], pid=intent['source']['session']['pid'])
        src.session = dict(intent['source']['session'])
        dst = SimRealmProcess('SR2', intent['destination']['endpoint'], pid=8000, running=False)
        adapters = SimAdapters(src, dst, FaultPlan(faults))
        driver = TransitionDriver(self.machine, adapters)
        for _ in range(60):
            transition = self.machine.get(transition_id)
            if transition['stage'] in ('COMMITTED', 'ROLLED_BACK', 'FAILED'):
                break
            before = (transition['stage'], transition['compensations'])
            driver.step(transition_id)
            after = self.machine.get(transition_id)
            if (after['stage'], after['compensations']) == before:
                self.machine.apply(transition_id, 'rollback', payload={'reason': 'adapter silent'})
        return self.machine.get(transition_id)['stage']

    def test_successful_crossing_moves_the_player_to_the_partner_realm(self):
        started = self.crossing.begin(self.pairs.profile_id, 'portal.sr1.sanctuary_gate',
                                      source_session={'pid': 3100, 'session_id': 'sr1-live'}, facts=FACTS,
                                      current_builds={'SR1': SIM_BUILD, 'SR2': SIM_BUILD})
        intent = self.machine.get(started['transition_id'])['intent']
        linked = self.pairs.store.get(self.pairs.profile_id)['sr2']
        self.assertEqual(intent['destination']['endpoint']['native_save_id'], linked['native_save_id'])
        pair = self.pairs.service.pair(self.pairs.profile_id,
                                       current_builds={'SR1': SIM_BUILD, 'SR2': SIM_BUILD})
        self.assertEqual(intent['expected_build'],
                         pair['endpoints']['SR2']['recorded_build'])
        self.assertEqual(self.server.snapshot()['player_list'][0]['status'], 'IN_TRANSITION')
        self.assertEqual(self.drive(started['transition_id']), 'COMMITTED')
        self.assertEqual(self.crossing.finish(started['transition_id'])['outcome'], 'COMMITTED')
        self.assertEqual(self.server.snapshot()['realm_populations'], {'SR1': 0, 'SR2': 1})

    def test_failed_crossing_keeps_the_player_home_and_plans_the_refund(self):
        started = self.crossing.begin(self.pairs.profile_id, 'pay.per.use', balance=25,
                                      source_session={'pid': 3100, 'session_id': 'sr1-live'}, facts=FACTS,
                                      current_builds={'SR1': SIM_BUILD, 'SR2': SIM_BUILD})
        self.assertEqual(started['charge']['status'], 'PLANNED')
        self.assertEqual(self.drive(started['transition_id'], faults=(Fault('world_ready.never', times=99),)),
                         'ROLLED_BACK')
        restarted = PortalCrossing(machine=self.machine, counterparts=self.pairs.service, portals=DEFAULT_PORTALS,
                                   announce=self.crossing.announce)   # the crossing service restarted in between
        done = restarted.finish(started['transition_id'])
        self.assertEqual((done['outcome'], done['settle']['settlement_action']), ('ROLLED_BACK', 'RELEASE'))
        self.assertEqual(self.server.snapshot()['realm_populations'], {'SR1': 1, 'SR2': 0})
        self.assertEqual(self.server.snapshot()['player_list'][0]['status'], 'ONLINE')

    def test_guards(self):
        with self.assertRaises(CrossingError) as ctx:
            self.crossing.begin(self.pairs.profile_id, 'portal.sr1.sanctuary_gate', source_session={'pid': 1,
                                'session_id': 'x'}, facts=dict(FACTS, unlocked=False, balance=1))
        self.assertEqual(ctx.exception.code, 'PORTAL_NOT_READY')
        with self.assertRaises(CrossingError) as ctx:
            self.crossing.begin(self.pairs.profile_id, 'portal.sr1.sanctuary_gate', source_session={'pid': 1,
                                'session_id': 'x'}, facts=FACTS, current_builds={'SR1': SIM_BUILD, 'SR2': 'new-build'})
        self.assertEqual(ctx.exception.code, 'PAIR_NOT_LINKED')  # a game update requires re-verification first
        with self.assertRaises(CrossingError) as ctx:
            self.crossing.begin(self.pairs.profile_id, 'pay.per.use', balance=3, source_session={'pid': 1,
                                'session_id': 'x'}, facts=FACTS, current_builds={'SR1': SIM_BUILD, 'SR2': SIM_BUILD})
        self.assertEqual(ctx.exception.code, 'PORTAL_NOT_READY')  # cannot afford the per-use cost
        with self.assertRaises(CrossingError):
            self.crossing.begin(self.pairs.profile_id, 'portal.unknown', source_session={'pid': 1, 'session_id': 'x'},
                                facts=FACTS)


if __name__ == '__main__':
    unittest.main()
