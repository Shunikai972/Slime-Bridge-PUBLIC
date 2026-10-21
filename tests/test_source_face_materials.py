"""Production-linked immutable layered source face material boundary."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_layered_source_face_materials():
    dotnet = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
    with tempfile.TemporaryDirectory(prefix='source-face-', dir=os.environ.get('TMPDIR')) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run([str(dotnet), 'run', '--project',
                                 str(ROOT / 'tests/SourceFaceMaterialsHarness/SourceFaceMaterialsHarness.csproj'),
                                 '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=180)
        assert result.returncode == 0, result.stdout
        assert 'source face checks=38 failures=0' in result.stdout
        assert 'no Unity rendering exercised' in result.stdout


def test_source_face_cannot_enter_lossy_appearance_paths():
    loader = (ROOT / 'mods/sr1-bridge/ImportedModelLoader.cs').read_text(encoding='utf-8-sig')
    spawner = (ROOT / 'mods/sr1-bridge/ImportedSpawner.cs').read_text(encoding='utf-8-sig')
    marker = (ROOT / 'mods/sr1-bridge/ImportedPartColors.cs').read_text(encoding='utf-8-sig')
    assert 'public bool SourceFace;' in marker
    assert 'SourceFaceMaterials.TryBuild' in loader
    assert loader.index('if (sourceFace)') < loader.index('var submeshCount =')
    assert 'if (partData.SourceFace)' in loader
    assert 'if (partData != null && partData.SourceFace) continue;' in spawner
