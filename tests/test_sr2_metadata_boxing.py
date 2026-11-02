"""Compile full production Sr2Compat against inert doubles of native boxing shapes observed in LAB013."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def metadata_harness(tmp_path_factory):
    dotnet = os.environ.get('SANCTUARY_DOTNET', 'dotnet')
    output = tmp_path_factory.mktemp('sr2-metadata')
    proc = subprocess.run([dotnet,'build',str(ROOT/'tests/Sr2MetadataHarness/Sr2MetadataHarness.csproj'),
        '--disable-build-servers','-p:UseSharedCompilation=false','-v','q','-o',str(output)],capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
    return dotnet,output/'Sr2MetadataHarness.dll'

@pytest.mark.parametrize('case',['boxed-false','boxed-true','empty','genuine-nullable','getter-empty','unexpected-box',
    'assignable-but-not-exact','uninitialized-nullable-type','null-wrapper','zero-type-and-object'])
def test_current_game_boxing(metadata_harness,case):
    dotnet,assembly=metadata_harness
    proc=subprocess.run([dotnet,str(assembly),case],capture_output=True,text=True,timeout=20)
    assert proc.returncode==0,proc.stdout+proc.stderr
    assert 'PASS '+case in proc.stdout
