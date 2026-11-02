"""Production-linked travel intro regression. Managed contracts, not game proof."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sr2_world_readiness import extract

ROOT = Path(__file__).resolve().parents[1]


class TravelIntroGateTests(unittest.TestCase):
    def test_native_intro_does_not_require_gameplay_authority_to_start_gameplay(self):
        dotnet = os.environ.get('INTRO_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        source = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf8')
        method = extract(source, 'private void MaybeEndNewGameIntro()')
        with tempfile.TemporaryDirectory(prefix='sr2-travel-intro-') as temporary:
            work = Path(temporary)
            fixture = (ROOT / 'tests/Sr2TravelIntroHarness/Program.cs').read_text(encoding='utf8')
            self.assertEqual(fixture.count('// PRODUCTION_METHOD'), 1)
            (work / 'Program.cs').write_text(fixture.replace('// PRODUCTION_METHOD', method), encoding='utf8')
            (work / 'Intro.csproj').write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
                '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
                '<ImplicitUsings>enable</ImplicitUsings><TreatWarningsAsErrors>true</TreatWarningsAsErrors>'
                '<NoWarn>CS0414;CS0649;CS0169</NoWarn><NuGetAudit>false</NuGetAudit>'
                '</PropertyGroup></Project>', encoding='utf8')
            (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf8')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'Intro.csproj'), '-c', 'Release'],
                                    capture_output=True, text=True, encoding='utf8', errors='replace', timeout=120)
            print('linked BridgeMod SHA256', hashlib.sha256(source.encode('utf8')).hexdigest())
            print(result.stdout)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('RESULT 9 passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
