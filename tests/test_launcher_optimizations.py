"""Regression tests for launcher & plugin refactoring/optimizations."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from apps.launcher import console, desktop, fsutil, integrity, windows

REPO_ROOT = Path(__file__).resolve().parents[1]


class FsutilTests(unittest.TestCase):
    def test_sha256_cache_hits_and_invalidates_on_write(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            target = Path(tmp) / 'sample.bin'
            target.write_bytes(b'first-payload')
            fsutil.clear_sha256_cache()
            expected1 = hashlib.sha256(b'first-payload').hexdigest()
            self.assertEqual(fsutil.sha256_file(target), expected1)
            with mock.patch.object(Path, 'open', side_effect=AssertionError('expected cache hit')):
                self.assertEqual(fsutil.sha256_file(target), expected1)
            target.write_bytes(b'second-different-payload')
            expected2 = hashlib.sha256(b'second-different-payload').hexdigest()
            self.assertEqual(fsutil.sha256_file(target), expected2)
            self.assertNotEqual(expected1, expected2)

    def test_is_reparse_safe_on_regular_and_missing(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            p = Path(tmp) / 'plain.txt'
            p.write_text('ok', encoding='utf-8')
            self.assertFalse(fsutil.is_reparse(p))
            self.assertFalse(fsutil.is_reparse(Path(tmp) / 'does-not-exist'))


class IntegrityOptimizationTests(unittest.TestCase):
    def test_count_bundles_early_exits_at_limit(self):
        entries = [SimpleNamespace(name=f'b{i}.bundle') for i in range(500)]
        consumed = 0

        class FakeScandir:
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                return False

            def __iter__(self):
                nonlocal consumed
                for entry in entries:
                    consumed += 1
                    yield entry

        with mock.patch('apps.launcher.integrity.os.scandir', return_value=FakeScandir()):
            found = integrity._count_bundles(Path('fake'), integrity.MIN_SR2_BUNDLES)
        self.assertEqual(found, integrity.MIN_SR2_BUNDLES)
        self.assertEqual(consumed, integrity.MIN_SR2_BUNDLES)

    def test_check_calls_deepest_path_once_when_too_long(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            root = Path(tmp)
            for item in integrity.REQUIRED['SR2']:
                rel = item[0] if isinstance(item, tuple) else item
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b'x')
            bundles = root / integrity.SR2_BUNDLES
            bundles.mkdir(parents=True, exist_ok=True)
            for i in range(integrity.MIN_SR2_BUNDLES):
                (bundles / f'b{i}.bundle').write_bytes(b'x')
            with mock.patch.object(integrity, 'long_paths_enabled', return_value=False), \
                    mock.patch.object(integrity, 'deepest_path', return_value=integrity.MAX_PATH + 15) as deepest:
                state = integrity.check('SR2', root)
            self.assertTrue(state['too_long'])
            self.assertEqual(deepest.call_count, 1)


class DesktopAndConsoleOptimizationTests(unittest.TestCase):
    def test_get_state_scans_installations_once(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            base = Path(tmp)
            api = desktop.LauncherAPI(config_path=base / 'cfg' / 'installations.json', known_paths=(), lab_games=base / 'lab')
            with mock.patch.object(api, '_installations', wraps=api._installations) as spy:
                state = api.get_state()
            self.assertEqual(spy.call_count, 1)
            self.assertIn('SR1', state['games'])
            self.assertIn('SR2', state['games'])

    def test_console_poll_avoids_integrity_and_plugin_hashing(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            base = Path(tmp)
            api = desktop.LauncherAPI(config_path=base / 'cfg' / 'installations.json', known_paths=(), lab_games=base / 'lab')
            with mock.patch('apps.launcher.integrity.check', side_effect=AssertionError('console_poll must not scan integrity')), \
                    mock.patch('apps.launcher.plugins.status', side_effect=AssertionError('console_poll must not hash plugins')):
                result = api.console_poll()
            self.assertTrue(result['ok'])

    def test_console_bounds_history_seen(self):
        c = console.Console()
        steps = [{'at': i, 'text': f'step-{i}'} for i in range(console.MAX_HISTORY_SEEN + 100)]
        c.poll(None, [{'id': 't1', 'from': 'SR1', 'to': 'SR2', 'history': steps}])
        self.assertLessEqual(len(c._history_seen), console.MAX_HISTORY_SEEN)
        self.assertEqual(len(c._history_seen), len(c._history_order))


class WindowOptimizationTests(unittest.TestCase):
    def test_foreground_pid_is_o1_without_enum_windows(self):
        instance = object.__new__(windows.Win32Windows)
        instance._verify = lambda pid: pid == 123
        instance._ct, instance._wt = ctypes, wintypes

        def owner(hwnd, output):
            output._obj.value = 123 if hwnd == 88 else 999
            return 1

        def class_name(hwnd, buf, _length):
            buf.value = 'UnityWndClass' if hwnd == 88 else 'ConsoleWindowClass'
            return len(buf.value)

        instance._user32 = SimpleNamespace(
            GetForegroundWindow=lambda: 88,
            GetWindowThreadProcessId=owner,
            GetWindow=lambda _hwnd, _cmd: 0,
            IsWindowVisible=lambda _hwnd: True,
            IsIconic=lambda _hwnd: False,
            GetClassNameW=class_name,
            EnumWindows=lambda *_a: (_ for _ in ()).throw(AssertionError('foreground_pid must not call EnumWindows')),
        )
        self.assertEqual(instance.foreground_pid(), 123)
        instance._user32.GetForegroundWindow = lambda: 99
        self.assertIsNone(instance.foreground_pid())


class RuntimeOptimizationContractTests(unittest.TestCase):
    def test_csharp_and_js_optimization_contracts(self):
        sanct = (REPO_ROOT / 'mods' / 'sr1-bridge' / 'SanctuaryBuilder.cs').read_text(encoding='utf-8')
        self.assertIn('internal static class CachedMainCamera', sanct)
        self.assertIn('CachedMainCamera.Get()', sanct)
        mini = (REPO_ROOT / 'mods' / 'sr1-bridge' / 'SanctuaryMiniVisual.cs').read_text(encoding='utf-8')
        self.assertIn('CachedMainCamera.Get()', mini)
        self.assertNotIn('Camera.main', mini)

        spawner = (REPO_ROOT / 'mods' / 'sr1-bridge' / 'ImportedSpawner.cs').read_text(encoding='utf-8')
        self.assertIn('ResolveRegionSetId(', spawner)
        self.assertIn('scene.RegionRegistry', spawner)

        sr2_mod = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'BridgeMod.cs').read_text(encoding='utf-8')
        self.assertIn('private Action<string> LogMsg', sr2_mod)
        self.assertNotIn('_worldProbe?.Tick(text =>', sr2_mod)

        app_js = (REPO_ROOT / 'apps' / 'launcher' / 'ui' / 'js' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('let pollInFlight = false', app_js)
        self.assertIn('let crossingsInFlight = false', app_js)
        self.assertIn('let consoleInFlight = false', app_js)
        self.assertIn("insertAdjacentHTML('beforeend'", app_js)


if __name__ == '__main__':
    unittest.main()
