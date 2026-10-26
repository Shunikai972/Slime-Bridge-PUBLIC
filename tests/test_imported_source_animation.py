"""Production-linked clock/ownership tests plus an actual SR1 Unity API compilation gate.

The deterministic engine surrogate exercises authored curves at continuous times; it does
not certify Unity's renderer, crossfade blending or native Instantiate serialization.
"""
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOTNET = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
HARNESS = ROOT / 'tests/ImportedSourceAnimationHarness'


def run_dotnet(arguments):
    with tempfile.TemporaryDirectory(prefix='source-animation-', dir=os.environ.get('TMPDIR')) as temporary:
        environment = dict(os.environ)
        environment.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_CLI_TELEMETRY_OPTOUT='1',
                           DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_NOLOGO='1')
        return subprocess.run([str(DOTNET), *arguments, '--disable-build-servers',
                               '-p:BaseIntermediateOutputPath=' + str(Path(temporary) / 'obj') + os.sep,
                               '-p:MSBuildProjectExtensionsPath=' + str(Path(temporary) / 'obj') + os.sep,
                               '-p:UseSharedCompilation=false'], cwd=ROOT, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              encoding='utf-8', errors='replace', timeout=180)


def test_continuous_source_clock_and_resource_contract():
    result = run_dotnet(['run', '--project', str(HARNESS / 'ImportedSourceAnimationHarness.csproj'),
                         '-c', 'Release'])
    assert result.returncode == 0, result.stdout
    assert 'source animation checks=' in result.stdout
    assert 'failures=0; production-linked synthetic clock, not Unity runtime' in result.stdout


def test_actual_sr1_unity_animation_signatures():
    configured = os.environ.get('SR1_MANAGED_PATH')
    if configured:
        managed = Path(configured)
    else:
        common = subprocess.run(['git', 'rev-parse', '--git-common-dir'], cwd=ROOT,
                                text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
        gitdir = Path(common)
        if not gitdir.is_absolute():
            gitdir = ROOT / gitdir
        managed = gitdir.resolve().parent / 'Lab/Games/SR1/SlimeRancher_Data/Managed'
    if not (managed / 'UnityEngine.AnimationModule.dll').is_file():
        pytest.skip('local SR1 Unity assemblies absent; set SR1_MANAGED_PATH to enable native API gate')
    result = run_dotnet(['build', str(HARNESS / 'NativeCompile.csproj'), '-c', 'Release',
                         '-p:SR1ManagedPath=' + str(managed)])
    assert result.returncode == 0, result.stdout
