"""SDK-service lifetime regressions for a synthetic focus harness, not native-game proof."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_lab_focus_diagnostics as focus


class FocusSdkLifetimeTests(unittest.TestCase):
    def exercise(self, provider):
        with tempfile.TemporaryDirectory(prefix='sdk-service-parent-', dir=os.environ.get('TMPDIR')) as outer:
            environment = {name: outer for name in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME')}
            with patch.dict(os.environ, environment), patch.object(focus.subprocess, 'run', side_effect=provider):
                focus.LabFocusDiagnosticsTests('test_production_linked_bounded_sampler').test_production_linked_bounded_sampler()

    def test_sdk_services_inherit_parent_lifetime_directories(self):
        def provider(command, **kwargs):
            for name in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME'):
                self.assertEqual(kwargs['env'][name], os.environ[name], name)
            self.assertIn('--disable-build-servers', command)
            self.assertIn('-p:UseSharedCompilation=false', command)
            return subprocess.CompletedProcess(command, 0,
                'focus diagnostic checks=67 failures=0; no Unity/User32/input/CREATE exercised')
        self.exercise(provider)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing-violation regression')
    def test_service_log_handle_outlives_command_without_fixture_cleanup_failure(self):
        handles = []
        def provider(command, **kwargs):
            # A retained real Windows file handle models the SDK installer's longer-lived log.
            # This replaces only the SDK boundary; it is not a claimed C# harness execution.
            path = Path(kwargs['env']['TEMP']) / 'synthetic-workload-installer.log'
            handles.append(path.open('xb'))
            self.assertTrue(path.exists())
            return subprocess.CompletedProcess(command, 0,
                'focus diagnostic checks=67 failures=0; no Unity/User32/input/CREATE exercised')
        # Retain the service handle across the focus caller's complete lifetime, but release
        # it before the enclosing parent-owned directory is removed.
        with tempfile.TemporaryDirectory(prefix='sdk-lock-parent-', dir=os.environ.get('TMPDIR')) as outer:
            try:
                with patch.dict(os.environ, {name: outer for name in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME')}), \
                     patch.object(focus.subprocess, 'run', side_effect=provider):
                    focus.LabFocusDiagnosticsTests('test_production_linked_bounded_sampler').test_production_linked_bounded_sampler()
                self.assertTrue(handles and not handles[0].closed)
            finally:
                for handle in handles:
                    handle.close()

    def test_nonzero_sdk_result_still_fails(self):
        def provider(command, **kwargs):
            return subprocess.CompletedProcess(command, 9, 'synthetic compiler failure')
        with self.assertRaisesRegex(AssertionError, 'synthetic compiler failure'):
            self.exercise(provider)


if __name__ == '__main__':
    unittest.main()
