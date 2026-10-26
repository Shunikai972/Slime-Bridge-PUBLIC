import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.lab.deploy import deploy_plugin
from tools.lab.process_guard import process_image_path
from tools.lab.oracle import heartbeat_matches_launched


class ProcessGuardTests(unittest.TestCase):
    def test_reports_exact_image_of_a_child_process(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        try:
            image = process_image_path(child.pid)
            self.assertIsNotNone(image)
            self.assertEqual(os.path.normcase(str(image)), os.path.normcase(str(Path(sys.executable).resolve())))
        finally:
            child.kill()
            child.wait()

    def test_unknown_pid_returns_none(self):
        self.assertIsNone(process_image_path(0xFFFFFFF0))


    def test_heartbeat_is_from_the_live_process_launched_by_oracle(self):
        from unittest.mock import Mock, patch
        child = Mock(pid=1234)
        child.poll.return_value = None
        expected = Path('C:/lab/SlimeRancher.exe')
        with patch('tools.lab.oracle.same_image', return_value=True):
            self.assertTrue(heartbeat_matches_launched({'pid': 1234}, child, expected))
            self.assertFalse(heartbeat_matches_launched({'pid': 5678}, child, expected))
            child.poll.return_value = 0
            self.assertFalse(heartbeat_matches_launched({'pid': 1234}, child, expected))
        with patch('tools.lab.oracle.same_image', return_value=False):
            child.poll.return_value = None
            self.assertFalse(heartbeat_matches_launched({'pid': 1234}, child, expected))

    def test_process_only_oracle_does_not_inherit_autoload_flags(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {}
            host.__enter__.return_value = host
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch.dict('os.environ', {'SLIMEBRIDGE_AUTOLOAD': 'continue', 'SLIMEBRIDGE_LAB_EXE': str(exe),
                                           'SLIMEBRIDGE_LAB_DIAG_SAVES': '1', 'SLIMEBRIDGE_LAB_CREATE': '1'}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child) as launched, \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True):
                run(['SR1'], 0, False, Path(tmp) / 'e')
            env = launched.call_args.kwargs['env']
            self.assertNotIn('SLIMEBRIDGE_AUTOLOAD', env)
            self.assertNotIn('SLIMEBRIDGE_LAB_EXE', env)
            self.assertNotIn('SLIMEBRIDGE_LAB_DIAG_SAVES', env)
            self.assertFalse('SLIMEBRIDGE_LAB_CREATE' in env, 'inherited native creation flag leaked')

    def test_counterpart_oracle_guards_both_realms_before_source_launch(self):
        import json
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lab = root / 'Lab' / 'Games'
            games = {}
            for realm, name in [('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe')]:
                exe = lab / realm / name
                exe.parent.mkdir(parents=True)
                exe.touch()
                (exe.parent / 'slimebridge-lab.json').write_text(json.dumps({'source': str(root / 'original' / realm)}))
                games[realm] = exe
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', games), \
                 patch('tools.lab.oracle.assert_no_other_game_processes'), \
                 patch('tools.lab.oracle.source_fingerprints', return_value={}) as fingerprints, \
                 patch('tools.lab.oracle.save_guard.snapshot', return_value=root / 'snapshot') as backup, \
                 patch('tools.lab.oracle._finalize_current_user') as restore, \
                 patch('tools.lab.oracle._run_games', return_value={'passed': False}):
                run(['SR2'], 0, False, root / 'evidence', current_user=True, guard_realms=['SR1', 'SR2'])
            backup.assert_called_once_with(['SR1', 'SR2'], 'oracle-pre')
            fingerprints.assert_called_once_with(['SR1', 'SR2'])
            self.assertEqual(restore.call_args.args[2], ['SR1', 'SR2'])

    def test_save_diagnostic_only_reaches_requested_sr2_lab(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR2' / 'SlimeRancher2.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {}
            host.__enter__.return_value = host
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR2': exe}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child) as launched, \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True):
                with self.assertRaisesRegex(ValueError, 'require'):
                    run(['SR2'], 0, False, Path(tmp) / 'no-diag', diag_saves=True)
                run(['SR2'], 0, False, Path(tmp) / 'diag', require_status=True, diag_saves=True)
            env = launched.call_args.kwargs['env']
            self.assertEqual(env['SLIMEBRIDGE_LAB_DIAG_SAVES'], '1')
            self.assertEqual(env['SLIMEBRIDGE_LAB_EXE'], str(exe.resolve()))

    def test_targeted_sr2_load_is_passed_only_to_lab_and_must_match_heartbeat(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR2' / 'SlimeRancher2.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            beat = {'pid': 1234, 'first_received_unix': 1.0,
                    'status': {'player_alive': True, 'save_id': 'wrong-save',
                               'pink_slime_count': 0, 'pink_plort_count': 0}}
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {'SR2': beat}
            host.__enter__.return_value = host
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR2': exe}), \
                 patch.dict('os.environ', {'SLIMEBRIDGE_LAB_SAVE_ID': 'inherited'}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child) as launched, \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True):
                result = run(['SR2'], 0, False, Path(tmp) / 'target', require_status=True,
                             sr2_save_id='requested-save')
                env = launched.call_args.kwargs['env']
                self.assertEqual(env['SLIMEBRIDGE_LAB_SAVE_ID'], 'requested-save')
                self.assertFalse(result['passed'])
                self.assertFalse(result['realms']['SR2']['requested_save_loaded'])
                beat['status']['save_id'] = 'requested-save'
                matched = run(['SR2'], 0, False, Path(tmp) / 'matched', require_status=True,
                              sr2_save_id='requested-save')
                self.assertTrue(matched['passed'])
                self.assertTrue(matched['realms']['SR2']['requested_save_loaded'])
                run(['SR2'], 0, False, Path(tmp) / 'ordinary', require_status=True)
                self.assertNotIn('SLIMEBRIDGE_LAB_SAVE_ID', launched.call_args.kwargs['env'])
                for invalid in ('', '../escape', 'bad\\name', 'a' * 129):
                    with self.assertRaises(ValueError):
                        run(['SR2'], 0, False, Path(tmp) / 'invalid', require_status=True,
                            sr2_save_id=invalid)
                with self.assertRaises(ValueError):
                    run(['SR2'], 0, False, Path(tmp) / 'no-status', sr2_save_id='requested-save')
                with self.assertRaises(ValueError):
                    run(['SR1'], 0, False, Path(tmp) / 'no-sr2', require_status=True,
                        sr2_save_id='requested-save')

    def test_targeted_sr1_load_matches_requested_group_and_rejects_bad_ids(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            beat = {'pid': 1234, 'first_received_unix': 1.0,
                    'status': {'player_alive': True, 'save_id': 'wrong-save',
                               'pink_slime_count': 0, 'pink_plort_count': 0}}
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {'SR1': beat}
            host.__enter__.return_value = host
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch.dict('os.environ', {'SLIMEBRIDGE_LAB_SAVE_ID': 'inherited'}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child) as launched, \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True):
                wrong = run(['SR1'], 0, False, Path(tmp) / 'wrong', require_status=True,
                            sr1_save_id='requested-save')
                self.assertFalse(wrong['passed'])
                self.assertFalse(wrong['realms']['SR1']['requested_save_loaded'])
                self.assertEqual(launched.call_args.kwargs['env']['SLIMEBRIDGE_LAB_SAVE_ID'], 'requested-save')
                beat['status']['save_id'] = 'requested-save'
                matched = run(['SR1'], 0, False, Path(tmp) / 'matched', require_status=True,
                              sr1_save_id='requested-save')
                self.assertTrue(matched['passed'])
                run(['SR1'], 0, False, Path(tmp) / 'ordinary', require_status=True)
                self.assertNotIn('SLIMEBRIDGE_LAB_SAVE_ID', launched.call_args.kwargs['env'])
                for invalid in ('', '../escape', 'bad\\name', 'a' * 129):
                    with self.assertRaises(ValueError):
                        run(['SR1'], 0, False, Path(tmp) / 'invalid', require_status=True,
                            sr1_save_id=invalid)
                with self.assertRaises(ValueError):
                    run(['SR1'], 0, False, Path(tmp) / 'no-status', sr1_save_id='requested-save')
                with self.assertRaises(ValueError):
                    run(['SR2'], 0, False, Path(tmp) / 'no-sr1', require_status=True,
                        sr1_save_id='requested-save')

    def test_cleanup_refuses_controls_when_image_query_fails(self):
        from unittest.mock import Mock, MagicMock, patch
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {}
            host.__enter__ = Mock(return_value=host)
            host.__exit__ = Mock(return_value=None)
            with patch('tools.lab.oracle.LAB', lab), \
                 patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child), \
                 patch('tools.lab.oracle.process_image_path', return_value=None), \
                 patch('tools.lab.oracle.same_image', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'could not stop'):
                    run(['SR1'], 0, False, Path(tmp) / 'evidence')
            child.terminate.assert_not_called()
            child.kill.assert_not_called()
            child.wait.assert_not_called()

    def test_status_less_heartbeat_is_not_phase94_proof(self):
        from tools.lab.oracle import loaded_status_ok
        self.assertFalse(loaded_status_ok({'pid': 1}))
        self.assertFalse(loaded_status_ok(None))
        self.assertFalse(loaded_status_ok({'status': {'player_alive': False, 'save_id': 'g'}}))
        # Phase 94 requires both Pink counts, not just player/save identity.
        self.assertFalse(loaded_status_ok({'status': {'player_alive': True, 'save_id': 'g'}}))
        self.assertTrue(loaded_status_ok({'status': {'player_alive': True, 'save_id': 'g',
                                                     'pink_slime_count': 0, 'pink_plort_count': 2}}))

    def test_require_status_fails_run_with_process_only_heartbeat(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            child.poll.return_value = None
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {'SR1': {'pid': 1234, 'first_received_unix': 0}}
            host.__enter__ = Mock(return_value=host)
            host.__exit__ = Mock(return_value=None)
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child), \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True):
                self.assertTrue(run(['SR1'], 0, False, Path(tmp) / 'e')['passed'])
                self.assertFalse(run(['SR1'], 0, False, Path(tmp) / 'e', require_status=True)['passed'])

    def test_keep_running_keeps_host_until_games_exit(self):
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            child = Mock(pid=1234)
            polls = iter([None] * 6 + [0] * 50)
            child.poll.side_effect = lambda: next(polls)
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {'SR1': {'pid': 1234, 'first_received_unix': 0}}
            host.__enter__ = Mock(return_value=host)
            host.__exit__ = Mock(return_value=None)
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', return_value=child), \
                 patch('tools.lab.oracle.process_image_path', return_value=exe), \
                 patch('tools.lab.oracle.same_image', return_value=True), \
                 patch('tools.lab.oracle.time.sleep') as sleep:
                run(['SR1'], 0, True, Path(tmp) / 'e')
            # The host context only closed after the game's poll() reported exit.
            self.assertTrue(sleep.called)
            host.__exit__.assert_called_once()
            child.terminate.assert_not_called()

    def test_failed_post_run_registry_export_still_restores_native_saves(self):
        from tools.lab.oracle import _finalize_current_user
        report = {'SR1': {'modified': ['game.sav'], 'added': [], 'removed': []}}
        after = {'SR1': {'modified': [], 'added': [], 'removed': []}}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('tools.lab.oracle.save_guard.compare', side_effect=[report, after]), \
             patch('tools.lab.oracle.save_guard.registry_changes', side_effect=RuntimeError('registry export failed')), \
             patch('tools.lab.oracle.save_guard.restore_originals', return_value={'SR1': [('restored', 'game.sav')]}) as restore, \
             patch('tools.lab.oracle.assert_no_other_game_processes'), \
             patch('tools.lab.oracle.source_fingerprints', return_value={'SR1': {'actual': {}, 'expected': {}}}), \
             patch('tools.lab.oracle._write_evidence'):
            result = {'passed': True}
            _finalize_current_user(result, Path(tmp) / 'snapshot', ['SR1'], Path(tmp))
            restore.assert_called_once()
            self.assertFalse(result['passed'])
            self.assertIn('registry export failed', result['registry_error'])

    def test_failed_save_restore_still_restores_registry_and_records_errors(self):
        from tools.lab import save_guard
        from tools.lab.oracle import _finalize_current_user
        report = {'SR1': {'modified': ['game.sav'], 'added': [], 'removed': []}}
        after = {'SR1': {'modified': ['game.sav'], 'added': [], 'removed': []}}
        changes = {'SR1': {'removed_or_changed': ['x'], 'added_or_changed': ['y']}}
        incomplete = save_guard.RestoreIncomplete({'SR1': []}, ['SR1/game.sav: OSError: disk refused'])
        with tempfile.TemporaryDirectory() as tmp, \
             patch('tools.lab.oracle.save_guard.compare', side_effect=[report, after]), \
             patch('tools.lab.oracle.save_guard.registry_changes', return_value=changes), \
             patch('tools.lab.oracle.save_guard.restore_originals', side_effect=incomplete), \
             patch('tools.lab.oracle.save_guard.restore_registry', return_value={'SR1': 'reimported'}) as registry, \
             patch('tools.lab.oracle.assert_no_other_game_processes'), \
             patch('tools.lab.oracle.source_fingerprints', return_value={'SR1': {'actual': {}, 'expected': {}}}), \
             patch('tools.lab.oracle._write_evidence'):
            result = {'passed': True}
            _finalize_current_user(result, Path(tmp) / 'snapshot', ['SR1'], Path(tmp))
            registry.assert_called_once()
            self.assertFalse(result['passed'])
            self.assertEqual(result['save_restore_errors'], ['SR1/game.sav: OSError: disk refused'])
            self.assertEqual(result['registry_restore'], {'SR1': 'reimported'})

    def test_safety_failure_during_save_restore_never_touches_registry(self):
        from tools.lab.oracle import _finalize_current_user
        report = {'SR1': {'modified': ['game.sav'], 'added': [], 'removed': []}}
        changes = {'SR1': {'removed_or_changed': ['x'], 'added_or_changed': []}}
        with tempfile.TemporaryDirectory() as tmp, \
             patch('tools.lab.oracle.save_guard.compare', return_value=report), \
             patch('tools.lab.oracle.save_guard.registry_changes', return_value=changes), \
             patch('tools.lab.oracle.save_guard.restore_originals',
                   side_effect=RuntimeError('Other game process(es) share these saves')), \
             patch('tools.lab.oracle.save_guard.restore_registry') as registry, \
             patch('tools.lab.oracle.assert_no_other_game_processes'), \
             patch('tools.lab.oracle._write_evidence'):
            result = {'passed': True}
            with self.assertRaisesRegex(RuntimeError, 'Other game process'):
                _finalize_current_user(result, Path(tmp) / 'snapshot', ['SR1'], Path(tmp))
            registry.assert_not_called()
            self.assertFalse(result['passed'])

    def test_one_failed_child_stop_still_stops_every_other_owned_game(self):
        import json
        from unittest.mock import Mock, MagicMock
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            games = {}
            for realm, name in [('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe')]:
                games[realm] = lab / realm / name
                games[realm].parent.mkdir(parents=True)
                games[realm].touch()
            first, second = Mock(pid=1111), Mock(pid=2222)
            first.poll.return_value = None
            first.terminate.side_effect = OSError('access denied')
            first.kill.side_effect = OSError('access denied')
            second.poll.return_value = None
            host = MagicMock(url='http://127.0.0.1:10000', token='test')
            host.heartbeats.return_value = {}
            host.__enter__.return_value = host
            evidence = Path(tmp) / 'e'
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', games), \
                 patch('tools.lab.oracle.require_isolated_profile'), \
                 patch('tools.lab.oracle.BridgeHost', return_value=host), \
                 patch('tools.lab.oracle.subprocess.Popen', side_effect=[first, second]), \
                 patch('tools.lab.oracle.process_image_path',
                       side_effect=lambda pid: games['SR1' if pid == 1111 else 'SR2'].resolve()), \
                 patch('tools.lab.oracle.same_image', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'could not stop'):
                    run(['SR1', 'SR2'], 0, False, evidence)
            second.terminate.assert_called_once()
            second.wait.assert_called()
            saved = [json.loads(p.read_text()) for p in evidence.glob('oracle-*.json')]
            self.assertTrue(saved and all(s['passed'] is False for s in saved))
            self.assertIn('SR1', saved[-1]['stop_errors'])

    def test_current_user_refuses_another_game_before_launch(self):
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp, patch('tools.lab.oracle.subprocess.Popen') as popen, \
             patch('tools.lab.oracle.assert_no_other_game_processes',
                   side_effect=RuntimeError('Other game process')):
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}):
                with self.assertRaisesRegex(RuntimeError, 'Other game process'):
                    run(['SR1'], 1, False, Path(tmp) / 'evidence', current_user=True)
            popen.assert_not_called()

    def test_game_process_enumeration_excludes_only_owned_pids(self):
        from subprocess import CompletedProcess
        from tools.lab.process_guard import other_game_processes
        rows = b'"SlimeRancher.exe","123","Console","1","100 K"\r\n' \
               b'"SlimeRancher2.exe","456","Console","1","100 K"\r\n' \
               b'"python.exe","999","Console","1","100 K"\r\n'
        with patch('tools.lab.process_guard.subprocess.run', return_value=CompletedProcess([], 0, rows, b'')):
            self.assertEqual(other_game_processes([123]), {456: 'SlimeRancher2.exe'})
        with patch('tools.lab.process_guard.subprocess.run', return_value=CompletedProcess([], 1, b'', b'')):
            with self.assertRaisesRegex(RuntimeError, 'enumerate'):
                other_game_processes()

    def test_oracle_rejects_duplicate_realm_before_launch(self):
        from unittest.mock import patch
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp, patch('tools.lab.oracle.subprocess.Popen') as popen:
            with self.assertRaises(ValueError):
                run(['SR1', 'SR1'], 1, False, Path(tmp) / 'evidence')
            popen.assert_not_called()

    def test_oracle_rejects_junctioned_lab_before_launch(self):
        from unittest.mock import patch
        from tools.lab.oracle import run
        if sys.platform != 'win32':
            self.skipTest('Windows junction test')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'original-game'
            source.mkdir()
            lab = base / 'Lab' / 'Games'
            lab.parent.mkdir()
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(lab), str(source)], check=True, capture_output=True)
            try:
                (source / 'SR1').mkdir()
                exe = source / 'SR1' / 'SlimeRancher.exe'
                exe.touch()
                with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': lab / 'SR1' / exe.name}), \
                     patch('tools.lab.oracle.subprocess.Popen') as popen:
                    with self.assertRaises(ValueError):
                        run(['SR1'], 1, False, base / 'evidence')
                    popen.assert_not_called()
            finally:
                lab.rmdir()

    def test_oracle_refuses_unisolated_profile_before_launch(self):
        from unittest.mock import patch
        from tools.lab.oracle import run
        with tempfile.TemporaryDirectory() as tmp, patch('tools.lab.oracle.subprocess.Popen') as popen:
            # Self-contained: a clean checkout has no ignored Lab/Games executable.
            lab = Path(tmp) / 'Lab' / 'Games'
            exe = lab / 'SR1' / 'SlimeRancher.exe'
            exe.parent.mkdir(parents=True)
            exe.touch()
            with patch('tools.lab.oracle.LAB', lab), patch.dict('tools.lab.oracle.GAMES', {'SR1': exe}), \
                 patch('tools.lab.oracle.require_isolated_profile', side_effect=RuntimeError('shared profile'), create=True):
                with self.assertRaisesRegex(RuntimeError, 'shared profile'):
                    run(['SR1'], 1, False, Path(tmp) / 'evidence')
            popen.assert_not_called()

class DeployTests(unittest.TestCase):
    def test_deploy_only_inside_lab_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            lab = Path(tmp) / 'Lab' / 'Games'
            target = lab / 'SR1' / 'SRML' / 'Mods' / 'SlimeBridge'
            build = Path(tmp) / 'build'
            build.mkdir()
            (build / 'SlimeBridge.SR1.dll').write_bytes(b'dll')
            (build / 'modinfo.json').write_text('{}')
            copied = deploy_plugin([build / 'SlimeBridge.SR1.dll', build / 'modinfo.json'], target, lab_root=lab)
            self.assertEqual(sorted(p.name for p in copied), ['SlimeBridge.SR1.dll', 'modinfo.json'])
            self.assertEqual((target / 'SlimeBridge.SR1.dll').read_bytes(), b'dll')
            with self.assertRaises(ValueError):
                deploy_plugin([build / 'modinfo.json'], Path(tmp) / 'elsewhere', lab_root=lab)


    def test_deploy_refuses_junctioned_lab_root(self):
        if sys.platform != 'win32':
            self.skipTest('Windows junction test')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            original = base / 'original-game'
            original.mkdir()
            lab_root = base / 'Lab' / 'Games'
            lab_root.parent.mkdir()
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(lab_root), str(original)],
                           check=True, capture_output=True)
            try:
                source = base / 'plugin.dll'
                source.write_bytes(b'plugin')
                with self.assertRaises(ValueError):
                    deploy_plugin([source], lab_root / 'SR1' / 'Mods', lab_root=lab_root)
                self.assertFalse((original / 'SR1').exists())
            finally:
                lab_root.rmdir()

    def test_deploy_refuses_reparse_destination_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lab_root = base / 'Lab' / 'Games'
            destination = lab_root / 'SR1' / 'Mods'
            destination.mkdir(parents=True)
            source = base / 'plugin.dll'
            source.write_bytes(b'new-plugin')
            sentinel = base / 'sentinel.dll'
            sentinel.write_bytes(b'original-content')
            with patch.object(Path, 'is_symlink', autospec=True) as mock_sym:
                def fake_is_symlink(p):
                    return getattr(p, 'name', '') == 'plugin.dll'
                mock_sym.side_effect = fake_is_symlink
                with self.assertRaises(ValueError):
                    deploy_plugin([source], destination, lab_root=lab_root)
            self.assertEqual(sentinel.read_bytes(), b'original-content')

    def test_deploy_never_writes_through_existing_hard_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lab_root = base / 'Lab' / 'Games'
            destination = lab_root / 'SR1' / 'Mods'
            destination.mkdir(parents=True)
            original = base / 'original-game.dll'
            original.write_bytes(b'original-content')
            os.link(original, destination / 'plugin.dll')
            source = base / 'build' / 'plugin.dll'
            source.parent.mkdir()
            source.write_bytes(b'new-plugin')
            deploy_plugin([source], destination, lab_root=lab_root)
            self.assertEqual(original.read_bytes(), b'original-content')
            self.assertEqual((destination / 'plugin.dll').read_bytes(), b'new-plugin')
            self.assertEqual([p.name for p in destination.iterdir()], ['plugin.dll'])

    @unittest.skipUnless(sys.platform == 'win32', 'Windows symlink test')
    def test_deploy_refuses_real_symlink_destination_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            destination = base / 'Lab' / 'Games' / 'SR1' / 'Mods'
            destination.mkdir(parents=True)
            original = base / 'original-game.dll'
            original.write_bytes(b'original-content')
            try:
                os.symlink(original, destination / 'plugin.dll')
            except OSError:
                self.skipTest('Symlink creation requires Developer Mode or elevation')
            source = base / 'plugin.dll'
            source.write_bytes(b'new-plugin')
            with self.assertRaises(ValueError):
                deploy_plugin([source], destination, lab_root=base / 'Lab' / 'Games')
            self.assertEqual(original.read_bytes(), b'original-content')


if __name__ == '__main__':
    unittest.main()
