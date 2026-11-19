"""Exercise the production SR2 close handshake without touching a real game or save."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sr2_world_readiness import extract

ROOT = Path(__file__).resolve().parents[1]


class Sr2CloseHandshakeTests(unittest.TestCase):
    def test_prepared_close_waits_for_live_authorization_and_blocks_writes(self):
        dotnet = os.environ.get('INTRO_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        source = (ROOT / 'mods/sr2-bridge/Sr2Travel.cs').read_text(encoding='utf8')
        methods = ('private void PrepareClose(', 'private void CommitClose(', 'private bool TickClose()', 'private void AbortClose()')
        match = source.split('        private bool MatchesClose(', 1)[1].split(';', 1)[0]
        linked = [extract(source, marker) for marker in methods]
        linked.append('        private bool MatchesClose(' + match + ';')
        fixture = (ROOT / 'tests/Sr2CloseHandshakeHarness/Program.cs').read_text(encoding='utf8')
        with tempfile.TemporaryDirectory(prefix='sr2-close-handshake-') as temporary:
            work = Path(temporary)
            (work / 'Program.cs').write_text(fixture.replace('// PRODUCTION_METHODS', '\n'.join(linked)), encoding='utf8')
            (work / 'Harness.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '<TreatWarningsAsErrors>true</TreatWarningsAsErrors></PropertyGroup></Project>', encoding='utf8')
            (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'Harness.csproj'), '-c', 'Release'],
                                    env=dict(os.environ, DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false'),
                                    capture_output=True, text=True, errors='replace', timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('SR2 close handshake OK', result.stdout)
