"""Unit coverage for the real live-endpoint attestation provider (no games, no LAB).

Every fail-closed branch of ``tools.lab.endpoint_attestation`` is exercised with fakes: an owned
child process, a heartbeat surface and an image probe. The healthy report is additionally run
through the reviewed ``evaluate_world_ready`` contract with its own trust map, and through the
same per-field host checks (``process_running`` / ``load_callback_returned`` / fresh
``observed_at``) that ``BridgeHost._validate_live_binding`` applies.
"""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from bridge.counterpart.world_ready import evaluate_world_ready
from tools.lab.endpoint_attestation import (LabRuntimeHandles, LiveEndpointAttestation,
                                            canonical_build_fingerprints, lab_installation_id)

REALMS = ('SR1', 'SR2')


class FakeProc:
    def __init__(self, pid, running=True):
        self.pid = pid
        self._running = running

    def poll(self):
        return None if self._running else 1


class FakeHost:
    def __init__(self, beats):
        self._beats = beats

    def heartbeats(self):
        return self._beats


def endpoint(realm):
    return {'realm': realm, 'installation_id': f'{realm.lower()}-lab-test',
            'native_save_id': f'{realm.lower()}-save-1',
            'native_save_identity': f'{realm.lower()}-native:save-1'}


def healthy():
    endpoints = {realm: endpoint(realm) for realm in REALMS}
    builds = {realm: f'lab-files-sha256:{realm.lower()}' for realm in REALMS}
    handles = LabRuntimeHandles()
    handles.procs = {'SR1': FakeProc(101), 'SR2': FakeProc(202)}
    handles.host = FakeHost({'SR1': {'pid': 101, 'status': {'player_alive': True, 'save_id': 'sr1-save-1'}},
                             'SR2': {'pid': 202, 'status': {'player_alive': True, 'save_id': 'sr2-save-1'}}})
    provider = LiveEndpointAttestation(
        handles, endpoints, builds,
        installation_ids=lambda realm: f'{realm.lower()}-lab-test',
        game_paths={realm: Path('C:/fake') / realm for realm in REALMS},
        image_probe=lambda pid, path: True)
    return provider, handles, endpoints, builds


class LiveEndpointAttestationTests(unittest.TestCase):
    def test_healthy_report_satisfies_the_reviewed_contract(self):
        provider, handles, endpoints, builds = healthy()
        report = provider('SR1', (101, 7))
        self.assertIsNotNone(report)
        self.assertEqual(report['schema'], 'bridge-world-ready')
        self.assertEqual(report['evidence'], 'RUNTIME_OBSERVATION')
        self.assertEqual(report['provider']['provider_id'], 'slimebridge.lab-live.SR1')
        self.assertTrue(report['process_running'] and report['load_callback_returned'])
        ready, diags = evaluate_world_ready(
            report,
            expected={**endpoints['SR1'], 'build_fingerprint': builds['SR1'],
                      'session': {'pid': 101, 'session_id': '7'}},
            trust={'slimebridge.lab-live.SR1': 'SR1'})
        self.assertTrue(ready, diags)
        # The host additionally requires a fresh observed_at within its own staleness bound.
        observed = datetime.fromisoformat(report['observed_at'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - observed).total_seconds()
        self.assertLessEqual(0, age)
        self.assertLess(age, 20)

    def test_unarmed_handles_are_not_attested(self):
        provider, _, _, _ = healthy()
        provider.handles = LabRuntimeHandles()
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_exited_child_is_not_attested(self):
        provider, handles, _, _ = healthy()
        handles.procs['SR1'] = FakeProc(101, running=False)
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_session_pid_must_be_the_owned_child(self):
        provider, _, _, _ = healthy()
        self.assertIsNone(provider('SR1', (999, 7)))
        self.assertIsNone(provider('SR1', (0, 7)))
        self.assertIsNone(provider('SR1', 'not-a-session'))

    def test_image_mismatch_is_not_attested(self):
        provider, _, _, _ = healthy()
        provider.image_probe = lambda pid, path: False
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_missing_or_stale_heartbeat_is_not_attested(self):
        provider, handles, _, _ = healthy()
        handles.host = FakeHost({})
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_heartbeat_pid_must_match_the_session(self):
        provider, handles, _, _ = healthy()
        handles.host = FakeHost({'SR1': {'pid': 404, 'status': {'player_alive': True, 'save_id': 'sr1-save-1'}}})
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_loaded_save_must_equal_the_linked_endpoint(self):
        provider, handles, _, _ = healthy()
        handles.host = FakeHost({'SR1': {'pid': 101, 'status': {'player_alive': True, 'save_id': 'another-save'}}})
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_not_player_alive_is_not_attested(self):
        provider, handles, _, _ = healthy()
        handles.host = FakeHost({'SR1': {'pid': 101, 'status': {'player_alive': False, 'save_id': 'sr1-save-1'}}})
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_installation_mismatch_is_not_attested(self):
        provider, _, _, _ = healthy()
        provider.installation_ids = lambda realm: 'someone-elses-installation'
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_missing_endpoint_or_build_is_not_attested(self):
        provider, _, endpoints, builds = healthy()
        provider.endpoints.pop('SR1')
        self.assertIsNone(provider('SR1', (101, 7)))
        provider, _, endpoints, builds = healthy()
        provider.builds.pop('SR2')
        self.assertIsNone(provider('SR2', (202, 7)))
        provider, _, endpoints, builds = healthy()
        incomplete = {**endpoint('SR1')}
        incomplete['native_save_identity'] = ''
        provider.endpoints['SR1'] = incomplete
        self.assertIsNone(provider('SR1', (101, 7)))

    def test_unknown_realm_is_not_attested(self):
        provider, _, _, _ = healthy()
        self.assertIsNone(provider('SR3', (101, 7)))

    def test_report_is_untrusted_for_the_other_realm(self):
        provider, _, endpoints, builds = healthy()
        report = provider('SR2', (202, 7))
        ready, diags = evaluate_world_ready(
            report,
            expected={**endpoints['SR2'], 'build_fingerprint': builds['SR2'],
                      'session': {'pid': 202, 'session_id': '7'}},
            trust={'slimebridge.lab-live.SR2': 'SR1'})
        self.assertFalse(ready)
        self.assertTrue(any(d['code'] == 'WORLD_READY_UNTRUSTED' for d in diags))

    def test_trust_map_names_every_realm(self):
        provider, _, _, _ = healthy()
        self.assertEqual(provider.trust(),
                         {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'})

    def test_constructor_rejects_bad_wiring(self):
        with self.assertRaises(ValueError):
            LiveEndpointAttestation('not-handles', {}, {})
        with self.assertRaises(ValueError):
            LiveEndpointAttestation(LabRuntimeHandles(), {}, {}, installation_ids='nope')
        with self.assertRaises(ValueError):
            LiveEndpointAttestation(LabRuntimeHandles(), {}, {}, image_probe='nope')

    def test_canonical_build_fingerprints_are_deterministic(self):
        fake = {'SR1': {'a.exe': 'aa', 'b.dll': 'bb'}, 'SR2': {'c.exe': 'cc'}}
        with mock.patch('tools.lab.oracle.lab_fingerprints', return_value=fake):
            first = canonical_build_fingerprints(['SR1', 'SR2'])
            second = canonical_build_fingerprints(['SR2', 'SR1'])
        self.assertEqual(first, second)
        self.assertTrue(all(value.startswith('lab-files-sha256:') for value in first.values()))
        changed = {'SR1': {'a.exe': 'aa', 'b.dll': 'CHANGED'}, 'SR2': {'c.exe': 'cc'}}
        with mock.patch('tools.lab.oracle.lab_fingerprints', return_value=changed):
            third = canonical_build_fingerprints(['SR1', 'SR2'])
        self.assertNotEqual(first['SR1'], third['SR1'])
        self.assertEqual(first['SR2'], third['SR2'])

    def test_lab_installation_id_matches_the_creation_oracle_formula(self):
        from tools.lab.creation_oracle import installation_id as creation_formula
        for realm in REALMS:
            self.assertEqual(lab_installation_id(realm), creation_formula(realm))


if __name__ == '__main__':
    unittest.main()
