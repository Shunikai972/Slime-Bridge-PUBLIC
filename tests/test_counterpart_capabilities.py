"""Synthetic native fixtures only; these tests never launch or create a native save."""
import copy
import unittest

from tools.lab import counterpart_capabilities as cap


def synthetic_report():
    return {
        'schema_version': 1, 'realm': 'SR2', 'status': 'DIAGNOSTIC_ONLY',
        'read_only': True, 'creation_authorized': False, 'native_write_calls': 0,
        'native_world_load_calls': 0, 'pid': 123, 'run_id': 'a' * 32,
        'menu_ready': True, 'storage_ready': True, 'slot_count': 3, 'raw_save_count': 2,
        'stock_definition_ids': ['Stock.Option'], 'model_definition_ids': ['Stock.Option'],
        'stock_default_keys_ready': True, 'stock_icon_reference_id': 'Stock.Icon',
        'stock_icon_matches_model': True, 'occupancy_complete': True,
        'least_free_slot': 1, 'detached_readback_complete': True, 'errors': [],
        'summaries': [
            {'raw_name': 'game_0', 'save_name': 'game_0', 'game_name': 'game',
             'slot_index': 0, 'invalid': False, 'icon_reference_id': 'Stock.Icon',
             'readback': {'status': 'READ_VERIFIED', 'game_name': 'game', 'slot_index': 0,
                          'sha256': 'b' * 64, 'byte_count': 1234, 'full_load': True,
                          'identity_matches': True, 'settings_complete': True,
                          'option_count': 1, 'icon_reference_id': 'Stock.Icon'}},
            {'raw_name': 'game_1', 'save_name': 'game_1', 'game_name': 'game',
             'slot_index': 0, 'invalid': False, 'icon_reference_id': 'Stock.Icon',
             'readback': {'status': 'READ_VERIFIED', 'game_name': 'game', 'slot_index': 0,
                          'sha256': 'c' * 64, 'byte_count': 1456, 'full_load': True,
                          'identity_matches': True, 'settings_complete': True,
                          'option_count': 1, 'icon_reference_id': 'Stock.Icon'}},
        ],
    }


class CapabilityReportTests(unittest.TestCase):
    def test_complete_detached_readback_is_diagnostic_not_creation_proof(self):
        report = synthetic_report()
        result = cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)
        self.assertTrue(result['diagnostic_ready'])
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY')
        self.assertEqual(result['least_free_slot'], 1)
        self.assertFalse(result['creation_authorized'])
        self.assertEqual(result['native_write_calls'], 0)
        self.assertEqual(report, synthetic_report())

    def test_malformed_or_conflicting_slot_metadata_is_refused(self):
        changes = [('slot_index', True), ('slot_index', -1), ('slot_index', 3),
                   ('invalid', True), ('save_name', 'wrong'), ('game_name', '')]
        for key, value in changes:
            report = synthetic_report()
            report['summaries'][1][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)
        report = synthetic_report()
        report['summaries'][1]['game_name'] = 'other_game'
        with self.assertRaises(ValueError):
            cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)

    def test_mismatched_or_missing_detached_readback_is_refused(self):
        for key, value in [('game_name', 'other'), ('slot_index', 2), ('full_load', False),
                           ('identity_matches', False), ('sha256', ''), ('settings_complete', False),
                           ('icon_reference_id', 'Other.Icon')]:
            report = synthetic_report()
            report['summaries'][0]['readback'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)
        report = synthetic_report()
        del report['summaries'][0]['readback']
        with self.assertRaises(ValueError):
            cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)

    def test_native_error_remains_diagnostic_but_not_ready(self):
        report = synthetic_report()
        report.update(errors=['NATIVE_ERROR: IOException'], occupancy_complete=False,
                      detached_readback_complete=False, least_free_slot=None)
        report['summaries'][0]['readback'] = {'status': 'NATIVE_ERROR'}
        result = cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)
        self.assertFalse(result['diagnostic_ready'])
        self.assertIsNone(result['least_free_slot'])
        self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY')

    def test_all_slots_full_cannot_be_ready_and_selects_nothing(self):
        report = synthetic_report()
        report.update(slot_count=1, least_free_slot=None)
        result = cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)
        self.assertFalse(result['diagnostic_ready'])
        self.assertIsNone(result['least_free_slot'])

    def test_report_from_other_process_or_run_is_refused(self):
        for pid, run_id in [(124, 'a' * 32), (123, 'd' * 32), (True, 'a' * 32)]:
            with self.subTest(pid=pid, run_id=run_id), self.assertRaises(ValueError):
                cap.validate_capability_report(synthetic_report(), expected_pid=pid, expected_run_id=run_id)

    def test_self_reported_free_slot_and_capabilities_are_crosschecked(self):
        for key, value in [('least_free_slot', 0), ('model_definition_ids', []),
                           ('native_write_calls', 1), ('creation_authorized', True),
                           ('raw_save_count', 1)]:
            report = synthetic_report()
            report[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                cap.validate_capability_report(report, expected_pid=123, expected_run_id='a' * 32)


class NativeProbeSourceTests(unittest.TestCase):
    def test_probe_contains_only_detached_read_interfaces(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        source = root / 'mods/sr2-bridge/CounterpartCapabilityProbe.cs'
        self.assertTrue(source.is_file(), 'read-only native probe is missing')
        text = source.read_text(encoding='utf-8')
        for forbidden in ('LoadNewGame(', 'CreateNew(', 'BeginLoad(', 'SaveAllNow(',
                          'SaveGameAndFlush(', 'StoreGameData(', 'ApplySelectedValue(', 'Initialize('):
            self.assertNotIn(forbidden, text)
        for required in ('GameBasedDefinitions', 'new GameSettingsModel(', 'SetGameIconForNewGame(',
                         'new GameV06', '_isLoadSummary = false', '.Load(stream, false)',
                         'stream.Position = 0', 'GameIconIndex.IndexTable', 'SHA256',
                         'SaveSlotSelection.SelectFree', 'DIAGNOSTIC_ONLY', 'FileMode.CreateNew',
                         '.Flush(true)', 'File.Move(', 'ReparsePoint'):
            self.assertIn(required, text)
        bridge = (root / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
        self.assertIn('CounterpartCapabilityProbe.Requested', bridge)


class ProtectedWrapperTests(unittest.TestCase):
    def test_authorization_is_required_before_any_artifact_or_oracle(self):
        from unittest.mock import patch
        with patch.object(cap.oracle, 'run') as launch:
            with self.assertRaises(PermissionError):
                cap.run_diagnostic(current_user_authorized=False)
            launch.assert_not_called()

    def test_output_escape_and_occupied_target_are_refused_without_writes(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'Lab/RuntimeArtifacts/capability'
            folder.mkdir(parents=True)
            outside = root / 'occupied-native-save'
            outside.write_bytes(b'SYNTHETIC OCCUPIED SLOT')
            with self.assertRaises(ValueError):
                cap.publish_json_exclusive(outside, {'x': 1}, repository_root=root)
            target = folder / 'occupied.json'
            target.write_bytes(b'SYNTHETIC EXISTING ARTIFACT')
            with self.assertRaises(FileExistsError):
                cap.publish_json_exclusive(target, {'x': 1}, repository_root=root)
            self.assertEqual(outside.read_bytes(), b'SYNTHETIC OCCUPIED SLOT')
            self.assertEqual(target.read_bytes(), b'SYNTHETIC EXISTING ARTIFACT')
            self.assertEqual(list(folder.iterdir()), [target])

    def test_native_hardlink_report_is_refused(self):
        import os
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'Lab/RuntimeArtifacts/capability'
            folder.mkdir(parents=True)
            source, link = folder / 'source.json', folder / 'hardlink.json'
            source.write_text('{}', encoding='utf-8')
            os.link(source, link)
            with self.assertRaises(ValueError):
                cap.read_native_report(link, repository_root=root)

    def test_protected_oracle_signature_no_world_load_and_cleanup_gating(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        for cleanup_ok in (True, False):
            with self.subTest(cleanup_ok=cleanup_ok), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                exe = root / 'Lab/Games/SR2/SlimeRancher2.exe'
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b'SYNTHETIC NOT AN EXECUTABLE')
                def fake_oracle(realms, timeout_s, keep_running, evidence_dir, **kwargs):
                    self.assertEqual(realms, ['SR2'])
                    self.assertFalse(keep_running)
                    self.assertEqual(kwargs['guard_realms'], ['SR2'])
                    self.assertTrue(kwargs['current_user'])
                    self.assertFalse(kwargs['require_status'])
                    self.assertFalse(kwargs['diag_saves'])
                    self.assertIsNone(kwargs['sr2_save_id'])
                    self.assertIsNone(kwargs['sr1_save_id'])
                    env = kwargs['extra_env']['SR2']
                    self.assertEqual(env['SLIMEBRIDGE_AUTOLOAD'], '')
                    self.assertEqual(env['SLIMEBRIDGE_LAB_GIVE'], '0')
                    self.assertEqual(env['SLIMEBRIDGE_LAB_CREATE'], '0')
                    report = synthetic_report()
                    report['run_id'] = env['SLIMEBRIDGE_LAB_COUNTERPART_RUN_ID']
                    cap.publish_json_exclusive(Path(env['SLIMEBRIDGE_LAB_COUNTERPART_OUTPUT']),
                                               report, repository_root=root)
                    scenario = kwargs['scenario'](None, {}, {'SR2': 123},
                                                  procs={'SR2': SimpleNamespace(pid=123, poll=lambda: None)})
                    return {'passed': True, 'scenario': scenario, 'sources_untouched': True,
                            'saves_match_backup_after_restore': cleanup_ok,
                            'registry_changes': {'SR2': {'removed_or_changed': [], 'added_or_changed': []}},
                            'registry_restore': {}}
                with patch.object(cap.oracle, 'REPO_ROOT', root), patch.object(cap.oracle, 'LAB', root / 'Lab/Games'), \
                     patch.object(cap.oracle, 'GAMES', {'SR2': exe}), patch.object(cap.oracle, 'run', side_effect=fake_oracle) as launch, \
                      patch.object(cap.process_guard, 'assert_no_other_game_processes'), patch.object(cap.process_guard, 'same_image', return_value=True):
                    result = cap.run_diagnostic(current_user_authorized=True, timeout_s=1)
                launch.assert_called_once()
                self.assertEqual(result['passed'], cleanup_ok)
                self.assertTrue(result['diagnostic']['diagnostic_ready'])
                self.assertEqual(result['status'], 'DIAGNOSTIC_ONLY')
                self.assertFalse(result['creation_authorized'])
                final = cap.read_native_report(Path(result['report_path']), repository_root=root)
                self.assertEqual(final['passed'], cleanup_ok)


if __name__ == '__main__':
    unittest.main()
