"""Run the real C# adapters against inert native multiplayer APIs (no games or sockets)."""
from pathlib import Path
import os
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OnlineModAdapterTests(unittest.TestCase):
    def test_online_adapters_wait_for_native_authority(self):
        dotnet = shutil.which('dotnet')
        if not dotnet:
            self.skipTest('dotnet SDK required')
        project = ROOT / 'tests/OnlineModHarness/OnlineModHarness.csproj'
        env = dict(os.environ, DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false')
        build = subprocess.run([dotnet, 'build', str(project), '-c', 'Release', '-v:q'],
                               env=env, capture_output=True, text=True, errors='replace', timeout=120)
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        dll = project.parent / 'bin/Release/net10.0/OnlineModHarness.dll'
        for scenario in ('startup', 'explicit', 'saved', 'missing', 'sr2'):
            with self.subTest(scenario=scenario):
                run = subprocess.run([dotnet, str(dll), scenario], env=env, capture_output=True,
                                     text=True, errors='replace', timeout=30)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertIn('online adapter OK: ' + scenario, run.stdout)
