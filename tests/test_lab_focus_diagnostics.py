"""Bounded LAB focus telemetry, synthetic only; never an intro/runtime proof."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LabFocusDiagnosticsTests(unittest.TestCase):
    def test_production_linked_bounded_sampler(self):
        dotnet_root = os.environ.get('DOTNET_ROOT')
        dotnet = str(Path(dotnet_root) / 'dotnet.exe') if dotnet_root else shutil.which('dotnet')
        self.assertIsNotNone(dotnet, 'dotnet SDK required')
        project = ROOT / 'tests/LabFocusDiagnosticsHarness/LabFocusDiagnosticsHarness.csproj'
        with tempfile.TemporaryDirectory(prefix='lab-focus-diagnostics-', dir=os.environ.get('TMPDIR')) as temporary:
            environment = dict(os.environ)
            environment.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                               DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                               DOTNET_CLI_TELEMETRY_OPTOUT='1')
            # A shared Roslyn server retains analyzer DLL handles inside this owned temporary root.
            result = subprocess.run([dotnet, 'run', '--project', str(project), '-c', 'Release',
                                     '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                    cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                    errors='replace', timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('focus diagnostic checks=67 failures=0', result.stdout)
            self.assertIn('no Unity/User32/input/CREATE exercised', result.stdout)

    def test_diagnostic_cannot_authorize_or_mutate(self):
        text = (ROOT / 'mods/sr2-bridge/LabFocusDiagnostics.cs').read_text(encoding='utf-8')
        for forbidden in ('BackgroundGameFocus', 'CounterpartCreator', 'AutoSaveDirector',
                          'InventoryAgent', 'SetForegroundWindow', 'EndFrame(', 'BeginLoad(', 'BeginNewGame('):
            self.assertNotIn(forbidden, text)
        self.assertIn('SLIMEBRIDGE_LAB_FOCUS_DIAGNOSTICS', text)
        self.assertIn('Null means not read', text)
        bridge = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
        self.assertIn('if (!LabFocusDiagnostics.Requested && !CounterpartCapabilityProbe.Requested && !NativeRegistryCollector.Requested)', bridge)
        self.assertIn('_focusDiagnostics?.Stop("deinitialize")', bridge)
        self.assertIn('string.Equals(world!.Name, RequestedSaveGroup, StringComparison.Ordinal)', bridge)

    def test_intro_skipper_constructed_only_by_excluded_inventory_agent(self):
        owners = sorted(path.name for path in (ROOT / 'mods/sr2-bridge').glob('*.cs')
                        if 'new BackgroundIntroSkipper(' in path.read_text(encoding='utf-8'))
        self.assertEqual(owners, ['InventoryAgent.cs'])


if __name__ == '__main__':
    unittest.main()
