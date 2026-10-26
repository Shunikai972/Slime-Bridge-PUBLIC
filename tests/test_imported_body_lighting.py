"""Source-linked CPU material boundary; not a Unity/GPU fidelity claim."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def test_source_body_lighting_material_boundary():
    assert (ROOT / 'mods/sr1-bridge/ImportedBodyLighting.cs').is_file(), 'source lighting is not consumed'
    dotnet = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
    with tempfile.TemporaryDirectory(prefix='body-lighting-', dir=os.environ.get('TMPDIR')) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run([str(dotnet), 'run', '--project',
                                 str(ROOT / 'tests/ImportedBodyLightingHarness/ImportedBodyLightingHarness.csproj'),
                                 '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=180)
        assert result.returncode == 0, result.stdout
        assert 'body lighting checks=' in result.stdout and 'failures=0' in result.stdout
        assert 'no Unity rendering exercised' in result.stdout


def test_body_lighting_is_validated_before_allocation_and_applied_to_final_body_clone():
    loader = (ROOT / 'mods/sr1-bridge/ImportedModelLoader.cs').read_text(encoding='utf-8-sig')
    spawner = (ROOT / 'mods/sr1-bridge/ImportedSpawner.cs').read_text(encoding='utf-8-sig')
    marker = (ROOT / 'mods/sr1-bridge/ImportedPartColors.cs').read_text(encoding='utf-8-sig')
    renderer_build = loader[loader.index('private static bool TryBuildRenderer('):loader.index('private static Transform EnsurePath(')]
    assert renderer_build.index('ImportedBodyLighting.TryRead(') < renderer_build.index('var mesh = BuildMesh(')
    assert 'partData.BodyLighting = bodyLighting;' in renderer_build
    assert 'public ImportedBodyLighting? BodyLighting' in marker
    assert 'if (!isFace && partData != null) partData.BodyLighting?.Apply(clone);' in spawner
    assert spawner.index('if (partData != null && partData.SourceFace) continue;') < spawner.index('partData.BodyLighting?.Apply(clone)')
    assert spawner.index('partData.BodyLighting?.Apply(clone)') < spawner.index('shared[i] = clone;')
    diagnostics = (ROOT / 'mods/sr1-bridge/BridgeDiagnostics.cs').read_text(encoding='utf-8-sig')
    assert 'source_body_lighting_matches' in diagnostics and 'partData.BodyLighting?.Matches(material)' in diagnostics
    assert 'partData.HasBodyLighting && BridgeEntry.LabRestPoseRequested' in diagnostics
