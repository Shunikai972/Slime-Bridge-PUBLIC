import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.lab import save_guard


REG_EXPORT = '\r\n'.join(['Windows Registry Editor Version 5.00', '', '[HKEY_CURRENT_USER\\Software\\Monomi Park\\Slime Rancher]',
                          '"a"=dword:00000001', ''])


class SaveGuardTests(unittest.TestCase):
    @staticmethod
    def _export_registry(args, **_kwargs):
        from subprocess import CompletedProcess
        if args[0] == 'reg.exe' and args[1] == 'export':
            Path(args[3]).write_bytes('Windows Registry Editor Version 5.00\r\n'.encode('utf-16'))
        return CompletedProcess(args, 0)

    def _setup(self, base: Path):
        low = base / 'LocalLow'
        sr1 = low / 'Monomi Park' / 'Slime Rancher'
        sr1.mkdir(parents=True)
        (sr1 / 'game_1.sav').write_bytes(b'original-save')
        (sr1 / 'Player.log').write_bytes(b'log-before')
        return low, sr1

    def test_snapshot_compare_restore_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, sr1 = self._setup(base)
            with patch('tools.lab.save_guard.subprocess.run', side_effect=self._export_registry):
                snap = save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
            self.assertEqual((snap / 'SR1' / 'game_1.sav').read_bytes(), b'original-save')
            # Simulate a game run: modify the save, add an autosave, rewrite the log.
            (sr1 / 'game_1.sav').write_bytes(b'modified-by-run')
            (sr1 / 'game_2.sav').write_bytes(b'new-autosave')
            (sr1 / 'Player.log').write_bytes(b'log-after')
            report = save_guard.compare(snap, low=low)['SR1']
            self.assertEqual(report['modified'], ['Player.log', 'game_1.sav'])
            self.assertEqual(report['added'], ['game_2.sav'])
            actions = save_guard.restore_originals(snap, {'SR1': report}, low=low)
            self.assertIn(('restored', 'game_1.sav'), actions['SR1'])
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'original-save')
            self.assertFalse((sr1 / 'game_2.sav').exists())
            # Evidence of the post-run state was preserved before rollback.
            self.assertEqual((snap / 'post-run' / 'SR1' / 'game_1.sav').read_bytes(), b'modified-by-run')
            self.assertEqual((snap / 'post-run' / 'SR1' / 'game_2.sav').read_bytes(), b'new-autosave')
            # Logs are evidence only; they are not restored.
            self.assertEqual((sr1 / 'Player.log').read_bytes(), b'log-after')

    def test_corrupted_backup_is_never_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, sr1 = self._setup(base)
            with patch('tools.lab.save_guard.subprocess.run', side_effect=self._export_registry):
                snap = save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
            (snap / 'SR1' / 'game_1.sav').write_bytes(b'corrupt')
            (sr1 / 'game_1.sav').write_bytes(b'modified-by-run')
            report = save_guard.compare(snap, low=low)
            with self.assertRaisesRegex(RuntimeError, 'Backup corrupted'):
                save_guard.restore_originals(snap, report, low=low)
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'modified-by-run')

    def test_restore_replaces_verified_bytes_without_writing_through_existing_link(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, sr1 = self._setup(base)
            with patch('tools.lab.save_guard.subprocess.run', side_effect=self._export_registry):
                snap = save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
            external = base / 'another-name-for-live-save'
            os.link(sr1 / 'game_1.sav', external)
            (sr1 / 'game_1.sav').write_bytes(b'modified')
            report = save_guard.compare(snap, low=low)
            with patch('tools.lab.save_guard.os.replace', side_effect=OSError('interrupted')) as replace:
                # Every file is attempted; the failures are then reported together.
                with self.assertRaisesRegex(save_guard.RestoreIncomplete, 'interrupted'):
                    save_guard.restore_originals(snap, report, low=low)
                replace.assert_called()
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'modified')
            save_guard.restore_originals(snap, report, low=low)
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'original-save')
            self.assertEqual(external.read_bytes(), b'modified')

    def _two_realm_run(self, base: Path):
        """Snapshot SR1 (a, b) and SR2 (z), then simulate a run that modifies and adds saves."""
        low, sr1 = self._setup(base)
        (sr1 / 'b.sav').write_bytes(b'original-b')
        sr2 = low / 'MonomiPark' / 'SlimeRancher2'
        sr2.mkdir(parents=True)
        (sr2 / 'z.sav').write_bytes(b'original-z')
        with patch('tools.lab.save_guard.subprocess.run', side_effect=self._export_registry):
            snap = save_guard.snapshot(['SR1', 'SR2'], 'test', low=low, out_root=base / 'out')
        (sr1 / 'game_1.sav').write_bytes(b'run-a')
        (sr1 / 'b.sav').write_bytes(b'run-b')
        (sr1 / 'new.sav').write_bytes(b'run-new')
        (sr2 / 'z.sav').write_bytes(b'run-z')
        return low, sr1, sr2, snap, save_guard.compare(snap, low=low)

    def test_one_failed_restore_still_attempts_every_other_file_and_realm(self):
        real_restore = save_guard._atomic_restore

        def fail_b(backup, target, expected):
            if target.name == 'b.sav':
                raise OSError('disk refused b')
            return real_restore(backup, target, expected)
        with tempfile.TemporaryDirectory() as tmp:
            low, sr1, sr2, snap, report = self._two_realm_run(Path(tmp))
            with patch('tools.lab.save_guard._atomic_restore', side_effect=fail_b):
                with self.assertRaisesRegex(save_guard.RestoreIncomplete, 'disk refused b') as failed:
                    save_guard.restore_originals(snap, report, low=low)
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'original-save')
            self.assertFalse((sr1 / 'new.sav').exists(), 'added file after the failure was not handled')
            self.assertEqual((sr2 / 'z.sav').read_bytes(), b'original-z', 'later realm was skipped')
            self.assertIn(('restored', 'z.sav'), failed.exception.actions['SR2'])
            self.assertEqual(len(failed.exception.errors), 1)

    def test_retry_after_partial_restore_keeps_first_post_run_evidence(self):
        real_restore = save_guard._atomic_restore
        calls = {'b': 0}

        def fail_b_once(backup, target, expected):
            if target.name == 'b.sav' and calls['b'] == 0:
                calls['b'] += 1
                raise OSError('transient')
            return real_restore(backup, target, expected)
        with tempfile.TemporaryDirectory() as tmp:
            low, sr1, _sr2, snap, report = self._two_realm_run(Path(tmp))
            with patch('tools.lab.save_guard._atomic_restore', side_effect=fail_b_once):
                with self.assertRaises(save_guard.RestoreIncomplete):
                    save_guard.restore_originals(snap, report, low=low)
            # Retry with the ORIGINAL report: restored and already-moved files must be replay-safe.
            actions = save_guard.restore_originals(snap, report, low=low)
            evidence = snap / 'post-run' / 'SR1'
            self.assertEqual((evidence / 'game_1.sav').read_bytes(), b'run-a', 'retry overwrote run evidence')
            self.assertEqual((evidence / 'b.sav').read_bytes(), b'run-b')
            self.assertEqual((evidence / 'new.sav').read_bytes(), b'run-new')
            self.assertEqual((sr1 / 'b.sav').read_bytes(), b'original-b')
            self.assertIn(('already_moved_to_evidence', 'new.sav'), actions['SR1'])
            after = save_guard.compare(snap, low=low)
            self.assertTrue(all(not r['added'] and not r['removed'] and r['modified'] == ['Player.log'] or
                                not r['added'] and not r['removed'] and not r['modified']
                                for r in after.values()), after)

    def test_changed_live_file_is_captured_beside_existing_evidence_not_over_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            low, sr1, _sr2, snap, report = self._two_realm_run(Path(tmp))
            evidence = snap / 'post-run' / 'SR1'
            evidence.mkdir(parents=True)
            (evidence / 'game_1.sav').write_bytes(b'earlier-capture')
            save_guard.restore_originals(snap, report, low=low)
            self.assertEqual((evidence / 'game_1.sav').read_bytes(), b'earlier-capture')
            extra = [p for p in evidence.iterdir() if p.name.startswith('game_1.sav.') and p.suffix == '.capture']
            self.assertEqual([p.read_bytes() for p in extra], [b'run-a'])

    def test_safety_precondition_failure_aborts_restoration_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            low, sr1, _sr2, snap, report = self._two_realm_run(Path(tmp))
            checks = {'n': 0}

            def game_appears():
                checks['n'] += 1
                if checks['n'] > 1:
                    raise RuntimeError('Other game process(es) share these saves')
            with self.assertRaisesRegex(RuntimeError, 'Other game process') as raised:
                save_guard.restore_originals(snap, report, low=low, assert_safe=game_appears)
            self.assertNotIsInstance(raised.exception, save_guard.RestoreIncomplete)
            self.assertEqual((sr1 / 'game_1.sav').read_bytes(), b'run-a')

    @staticmethod
    def _reg(path: Path, body: str) -> Path:
        path.write_bytes(('Windows Registry Editor Version 5.00\r\n\r\n' + body).encode('utf-16'))
        return path

    def test_registry_records_keep_their_subkey(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            before = self._reg(base / 'before.reg', '[HKEY_CURRENT_USER\\Software\\A]\r\n"x"=dword:00000001\r\n\r\n'
                                                    '[HKEY_CURRENT_USER\\Software\\B]\r\n"x"=dword:00000002\r\n')
            swapped = self._reg(base / 'after.reg', '[HKEY_CURRENT_USER\\Software\\A]\r\n"x"=dword:00000002\r\n\r\n'
                                                    '[HKEY_CURRENT_USER\\Software\\B]\r\n"x"=dword:00000001\r\n')
            self.assertNotEqual(save_guard._reg_records(before), save_guard._reg_records(swapped))

    def test_registry_continuation_lines_form_one_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            split = self._reg(base / 'a.reg', '[HKEY_CURRENT_USER\\Software\\A]\r\n"h"=hex:01,02,\\\r\n  03,04\r\n')
            other = self._reg(base / 'b.reg', '[HKEY_CURRENT_USER\\Software\\A]\r\n"h"=hex:01,02,\\\r\n  03,05\r\n')
            records = save_guard._reg_records(split)
            self.assertIn('[HKEY_CURRENT_USER\\Software\\A]\t"h"=hex:01,02,03,04', records)
            self.assertNotEqual(records, save_guard._reg_records(other))
            truncated = self._reg(base / 'c.reg', '[HKEY_CURRENT_USER\\Software\\A]\r\n"h"=hex:01,02,\\\r\n')
            with self.assertRaisesRegex(ValueError, 'truncated'):
                save_guard._reg_records(truncated)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows junction test')
    def test_snapshot_rejects_directory_junction_in_save_tree(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, sr1 = self._setup(base)
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'source-save.sav').write_bytes(b'external')
            subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(sr1 / 'junction'), str(outside)],
                           check=True, capture_output=True)
            with self.assertRaisesRegex(ValueError, 'Reparse point'):
                save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
            self.assertEqual((outside / 'source-save.sav').read_bytes(), b'external')

    def test_snapshot_requires_successful_registry_export(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            low, _ = self._setup(Path(tmp))
            def fail_export(args, **_kwargs):
                return CompletedProcess(args, 0 if args[1] == 'query' else 1)   # the key exists, its export fails
            with patch('tools.lab.save_guard.subprocess.run', side_effect=fail_export):
                with self.assertRaisesRegex(RuntimeError, 'registry export'):
                    save_guard.snapshot(['SR1'], 'test', low=low, out_root=Path(tmp) / 'out')

    def test_absent_registry_key_before_the_run_is_recorded_and_the_new_key_removed_after(self):
        from subprocess import CompletedProcess
        state = {'exists': False, 'deleted': []}

        def fake(args, **_kwargs):
            if args[1] == 'query':
                return CompletedProcess(args, 0 if state['exists'] else 1)
            if args[1] == 'export':
                Path(args[3]).write_bytes(REG_EXPORT.encode('utf-16'))
                return CompletedProcess(args, 0)
            if args[1] == 'delete':
                state['deleted'].append(args[2])
                state['exists'] = False
                return CompletedProcess(args, 0)
            raise AssertionError(args)

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, _ = self._setup(base)
            with patch('tools.lab.save_guard.subprocess.run', side_effect=fake):
                snap = save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
                manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
                self.assertTrue(manifest['realms']['SR1']['registry_absent'])
                self.assertTrue((snap / 'SR1.reg.absent').is_file())
                self.assertFalse((snap / 'SR1.reg').exists())
                # nothing created by the run: nothing to restore, nothing deleted
                self.assertEqual(save_guard.registry_changes(snap)['SR1']['added_or_changed'], [])
                self.assertEqual(save_guard.restore_registry(snap, save_guard.registry_changes(snap)), {})
                # the game created its key: evidence is exported, then only that fixed key is deleted
                state['exists'] = True
                changes = save_guard.registry_changes(snap)
                self.assertTrue(changes['SR1']['absent_before'] and changes['SR1']['added_or_changed'])
                self.assertTrue((snap / 'post-run' / 'SR1.reg').is_file())
                self.assertEqual(save_guard.restore_registry(snap, changes), {'SR1': 'removed-new-key'})
                self.assertEqual(state['deleted'], [save_guard.REGISTRY_KEYS['SR1']])
                self.assertFalse(state['exists'])


    def test_manifest_records_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            low, _ = self._setup(base)
            with patch('tools.lab.save_guard.subprocess.run', side_effect=self._export_registry):
                snap = save_guard.snapshot(['SR1'], 'test', low=low, out_root=base / 'out')
            manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(len(manifest['realms']['SR1']['files']['game_1.sav']['sha256']), 64)


if __name__ == '__main__':
    unittest.main()
