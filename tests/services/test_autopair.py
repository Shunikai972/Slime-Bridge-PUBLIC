"""Automatic pairing: launch either game, the partner save appears once and is reused afterwards."""

import tempfile
import unittest
from pathlib import Path

from bridge.counterpart.autopair import AutoPairing
from bridge.counterpart.simulator import SIM_BUILD, CounterpartSimulation
from bridge.sim.faults import Fault


class AutoPairingTests(unittest.TestCase):
    def world(self, source='SR1', target='SR2', faults=()):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        sim = CounterpartSimulation(Path(self._tmp.name), source=source, target=target, faults=faults)
        self.addCleanup(sim.close)
        auto = AutoPairing(sim.service, sim.orchestrator, sim.store,
                           installations={'SR1': 'sr1-install', 'SR2': 'sr2-install'})
        return sim, auto

    def settle(self, sim, auto, profile_id, target):
        """Deliver plugin replies and re-run the automatic flow, as the host loop would."""
        result = None
        for _ in range(6):
            sim.transport.deliver(sim.service)
            result = auto.resume(profile_id, target)
            if result['decision'] == 'USE_EXISTING_LINK':
                break
        return result

    def test_launching_sr1_creates_and_links_sr2_once_then_reuses_it(self):
        sim, auto = self.world()
        first = auto.on_save_identified('SR1', 'sr1-install', 'player-save-1', 'player-identity-1',
                                        build_fingerprint=SIM_BUILD)
        self.assertEqual([s['action'] for s in first['trace']][:2], ['REQUEST', 'DISPATCH'])
        done = self.settle(sim, auto, first['profile_id'], 'SR2')
        self.assertEqual((done['decision'], done['pair']), ('USE_EXISTING_LINK', 'LINKED'))
        again = auto.on_save_identified('SR1', 'sr1-install', 'player-save-1', 'player-identity-1',
                                        build_fingerprint=SIM_BUILD)
        self.assertEqual((again['profile_id'], again['decision']), (first['profile_id'], 'USE_EXISTING_LINK'))
        partner = sim.store.get(first['profile_id'])['sr2']
        back = auto.on_save_identified('SR2', partner['installation_id'], partner['native_save_id'],
                                       partner['native_save_identity'], build_fingerprint=SIM_BUILD)
        self.assertEqual((back['profile_id'], back['target_realm'], back['decision']),
                         (first['profile_id'], 'SR1', 'USE_EXISTING_LINK'))
        self.assertEqual(sim.transport_creates(sim.journal.intents_for(first['profile_id'])[-1]['creation_id']), 1)
        self.assertEqual(len(sim.realms['SR2'].native_writes), 1)

    def test_sr2_first_direction_and_lost_reply(self):
        sim, auto = self.world(source='SR2', target='SR1', faults=(Fault('result.drop'),))
        first = auto.on_save_identified('SR2', 'sr2-install', 'sr2-save', 'sr2-identity', build_fingerprint=SIM_BUILD)
        cid = sim.journal.intents_for(first['profile_id'])[-1]['creation_id']
        sim.transport.deliver(sim.service)   # the create reply is lost
        sim.service.timeout(cid)
        done = self.settle(sim, auto, first['profile_id'], 'SR1')
        self.assertEqual(done['pair'], 'LINKED')
        self.assertEqual(sim.transport_creates(cid), 1)

    def test_changed_identity_is_never_relinked(self):
        sim, auto = self.world()
        auto.on_save_identified('SR1', 'sr1-install', 'player-save-1', 'identity-a', build_fingerprint=SIM_BUILD)
        changed = auto.on_save_identified('SR1', 'sr1-install', 'player-save-1', 'identity-b',
                                          build_fingerprint=SIM_BUILD)
        self.assertEqual((changed['decision'], changed['reasons']), ('BLOCK_CONFLICT', ['SAVE_IDENTITY_CHANGED']))


if __name__ == '__main__':
    unittest.main()
