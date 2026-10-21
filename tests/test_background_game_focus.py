"""Production-linked pure focus policy; not an actual Windows/Unity focus test."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BackgroundGameFocusTests(unittest.TestCase):
    def test_harness_invocation_disables_persistent_build_processes(self):
        from unittest import mock
        output = 'focus-policy checks=24 failures=0; OS focus/input not exercised'
        with mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run, \
                mock.patch.dict(os.environ, {'DOTNET_ROOT': 'C:/synthetic-dotnet'}):
            self.test_fail_closed_focus_policy()
        command = run.call_args.args[0]
        self.assertIn('--disable-build-servers', command)
        self.assertIn('-p:UseSharedCompilation=false', command)

    def test_fail_closed_focus_policy(self):
        dotnet = os.environ.get('DOTNET_ROOT')
        dotnet = str(Path(dotnet) / 'dotnet.exe') if dotnet else shutil.which('dotnet')
        if not dotnet:
            self.fail('dotnet SDK required')
        project = ROOT / 'tests/BackgroundGameFocusHarness/BackgroundGameFocusHarness.csproj'
        with tempfile.TemporaryDirectory(prefix='background-focus-') as temporary:
            environment = dict(os.environ)
            environment.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                               DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                               DOTNET_CLI_TELEMETRY_OPTOUT='1')
            result = subprocess.run([dotnet, 'run', '--project', str(project), '-c', 'Release',
                                     '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                    cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                    errors='replace', timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('focus-policy checks=24 failures=0', result.stdout)
            self.assertIn('OS focus/input not exercised', result.stdout)


if __name__ == '__main__':
    unittest.main()
