"""Exercise the production SR2 portal's retry and fallback decisions without a Unity world."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sr2_world_readiness import extract

ROOT = Path(__file__).resolve().parents[1]


class ReturnPortalVisualTests(unittest.TestCase):
    def test_missing_native_parts_are_retried_without_publishing_an_empty_root(self):
        dotnet = os.environ.get('INTRO_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        source = (ROOT / 'mods/sr2-bridge/Sr2ReturnPortal.cs').read_text(encoding='utf8')
        with tempfile.TemporaryDirectory(prefix='sr2-return-marker-') as temporary:
            work = Path(temporary)
            fixture = (ROOT / 'tests/Sr2ReturnPortalVisualHarness/Program.cs').read_text(encoding='utf8')
            methods = ('internal void Gui()', 'private void Interact()',
                       'private void RefreshVisual(float now, bool focused)')
            (work / 'Program.cs').write_text(fixture.replace('// PRODUCTION_METHOD', '\n'.join(
                extract(source, signature) for signature in methods)), encoding='utf8')
            (work / 'Harness.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '<TreatWarningsAsErrors>true</TreatWarningsAsErrors></PropertyGroup></Project>', encoding='utf8')
            (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'Harness.csproj'), '-c', 'Release'],
                                    capture_output=True, text=True, errors='replace', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('portal visual retry OK', result.stdout)
