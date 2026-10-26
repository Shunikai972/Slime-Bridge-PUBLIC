"""Synthetic lifecycle fixtures for reload-oracle orchestration, not native game proof."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_crash_oracle import SyntheticGameHost
from tools.lab.durability_oracle import make_scenario


class DurabilityOracleTests(unittest.TestCase):
    def test_transfer_survives_restart_and_replays_receiver_receipt_with_current_pid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journals = {realm: root / (realm + '.journal') for realm in ('SR1', 'SR2')}
            for path in journals.values():
                path.write_text('', encoding='utf-8')
            with SyntheticGameHost(journals) as host:
                host.establish_sessions()
                beats = {realm: {'status': {'save_id': save}} for realm, save in host.saves.items()}
                procs = {realm: Mock(pid=host.pids[realm]) for realm in host.pids}
                def restart(_host, owned, saves, _journals, _timeout):
                    # Explicit fixture: counts remain in memory, so this proves orchestration ONLY.
                    _host.forget_volatile_state()
                    for realm in host.pids:
                        host.pids[realm] += 1000
                        owned[realm] = Mock(pid=host.pids[realm])
                    for receipt in host.saved_results.values():
                        receipt['pid'] = host.pids[receipt['realm']]
                    host.establish_sessions()
                    return {realm: {'pid': host.pids[realm], 'status': {'save_id': save}}
                            for realm, save in saves.items()}
                try:
                    with patch('tools.lab.durability_oracle.restart_owned_games', side_effect=restart) as reboot:
                        evidence = make_scenario('SR1', 'SR2', 'slime.pink', root, journals, 0, 0,
                                                 profile_id=host.profile_id,
                                                 authority=host.transfer_authority)(
                            host, beats, dict(host.pids), procs=procs)
                    self.assertTrue(evidence['passed'], evidence)
                    self.assertEqual(evidence['before_restart']['status'], 'ESCROWED')
                    self.assertEqual(evidence['after_restart']['status'], 'COMMITTED')
                    self.assertEqual(evidence['counts'], {'source_before': 1, 'destination_before': 0,
                                     'source_after': 0, 'destination_after': 1,
                                     'source_reloaded': 0, 'destination_reloaded': 1})
                    self.assertEqual(evidence['logical_retry']['transfer_id'], evidence['transfer_id'])
                    self.assertEqual(evidence['logical_retry']['status'], 'COMMITTED')
                    self.assertEqual(evidence['logical_retry']['steps'], [])
                    self.assertEqual(evidence['receiver_replay']['pid'], 1456)
                    self.assertTrue(evidence['same_native_receipt_except_transport'])
                    self.assertEqual(sum(op.endswith(':add') for op in host.mutations), 1)
                    self.assertEqual(sum(op.endswith(':remove') for op in host.mutations), 1)
                    reboot.assert_called_once()
                    saved = json.loads((root / 'durability-result.json').read_text(encoding='utf-8'))
                    self.assertEqual(saved['transfer_id'], evidence['transfer_id'])
                    self.assertTrue(saved['passed'])
                finally:
                    if host.transfer_store is not None:
                        host.transfer_store.close()


if __name__ == '__main__':
    unittest.main()
