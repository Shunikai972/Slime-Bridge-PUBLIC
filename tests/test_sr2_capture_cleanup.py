"""Exercise the actual capture finally with failure-injecting native-boundary doubles."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from test_sr2_world_readiness import extract

ROOT = Path(__file__).resolve().parents[1]


def test_capture_cleanup_attempts_every_restore_after_native_failures():
    source = (ROOT / 'mods/sr2-bridge/Sr2LabImportedOps.cs').read_text(encoding='utf8')
    method = extract(source, 'private bool CaptureActor(')
    start = method.rindex('\n            finally')
    cleanup = extract(method[start:], 'finally')
    fixture = (ROOT / 'tests/Sr2CaptureCleanupHarness/Program.cs').read_text(encoding='utf8')
    assert fixture.count('// PRODUCTION_FINALLY') == 1
    dotnet = os.environ.get('READINESS_DOTNET') or shutil.which('dotnet')
    assert dotnet, 'a .NET 10 SDK is required'
    with tempfile.TemporaryDirectory(prefix='sr2-capture-cleanup-', dir=os.environ.get('TMPDIR')) as tmp:
        work = Path(tmp)
        (work / 'Program.cs').write_text(fixture.replace('// PRODUCTION_FINALLY', cleanup), encoding='utf8')
        (work / 'Cleanup.csproj').write_text(
            '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
            '<TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
            '<ImplicitUsings>enable</ImplicitUsings><TreatWarningsAsErrors>true</TreatWarningsAsErrors>'
            '<NoWarn>CS0219</NoWarn><NuGetAudit>false</NuGetAudit></PropertyGroup></Project>', encoding='utf8')
        (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf8')
        result = subprocess.run([dotnet, 'run', '--project', str(work / 'Cleanup.csproj'), '-c', 'Release'],
                                capture_output=True, text=True, encoding='utf8', errors='replace', timeout=120)
        print('linked capture SHA256', hashlib.sha256(source.encode('utf8')).hexdigest())
        print(result.stdout)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'RESULT 28 cleanup scenarios passed' in result.stdout
