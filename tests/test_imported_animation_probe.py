"""Production-linked LAB source-clip validator; native Unity remains a separate gate."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_source_clip_native_sampling_boundary():
    dotnet = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
    with tempfile.TemporaryDirectory(prefix='animation-probe-', dir=os.environ.get('TMPDIR')) as temporary:
        environment = dict(os.environ)
        environment.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run([str(dotnet), 'run', '--project',
                                 str(ROOT / 'tests/ImportedAnimationProbeHarness/ImportedAnimationProbeHarness.csproj'),
                                 '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                cwd=ROOT, env=environment, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', timeout=180)
        assert result.returncode == 0, result.stdout
        assert 'animation probe checks=' in result.stdout
        assert 'no Unity playback exercised' in result.stdout


def test_source_pose_is_wired_after_model_construction():
    text = (ROOT / 'mods/sr1-bridge/ImportedModelLoader.cs').read_text(encoding='utf-8')
    call = 'ImportedAnimationProbe.TrySample(root, document, out problem)'
    assert text.count(call) == 1
    assert text.index(call) > text.index('if (rendererCount == 0 || enabledRendererCount == 0)')
    assert text.index(call) < text.index('model = new ImportedModel')
