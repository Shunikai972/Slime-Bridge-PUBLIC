"""Execute linked production C# against inert Unity doubles. Not an audible-runtime assertion."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def compiled_audio_harness(tmp_path_factory):
    dotnet = os.environ.get('SANCTUARY_DOTNET', 'dotnet')
    output = tmp_path_factory.mktemp('travel-audio')
    project = ROOT / 'tests/TravelPresentationHarness/TravelPresentationHarness.csproj'
    result = subprocess.run([dotnet, 'build', str(project), '--disable-build-servers', '-o', str(output), '-v', 'q',
                             '-p:UseSharedCompilation=false'], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return dotnet, output / 'TravelPresentationHarness.dll'

@pytest.mark.parametrize('case', ['waiting', 'maintain', 'quality-fault', 'quality-writer', 'focus-edge'])
def test_actual_audio_policy(compiled_audio_harness, case):
    dotnet, assembly = compiled_audio_harness
    result = subprocess.run([dotnet, str(assembly), case], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS ' + case in result.stdout
