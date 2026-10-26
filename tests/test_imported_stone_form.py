"""Ringtail stone-form cache loader: fail-closed validation and the inactive stone visual.

ImportedStoneForm reads <repo>/Lab/AssetCache/sr2-to-sr1/<canonical>/stone-form.json behind the
LabSpecPathGuard containment and the extraction receipt (schema bridge-stone-form v1): every
consumed file must be listed with a matching SHA-256, meshes/vertices/triangles are bounded,
every TRS must be finite with a unit quaternion and file names are traversal-safe. Any doubt
refuses the whole entry: an actor whose cache does not validate simply never petrifies and never
gets a partial visual. The CPU harness links the production file and drives valid / absent /
hash-mismatch / traversal / malformed / singular-TRS fixtures against minimal UnityEngine
doubles — no Unity runtime, no GPU.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STONE_FORM = ROOT / 'mods/sr1-bridge/ImportedStoneForm.cs'
DOTNET = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'


def test_loader_reads_the_guarded_cache_and_pins_the_schema():
    source = STONE_FORM.read_text(encoding='utf-8-sig')
    assert 'LabSpecPathGuard.FindRepoRoot()' in source
    assert 'Path.Combine(repoRoot, "Lab", "AssetCache", "sr2-to-sr1")' in source
    assert 'LabSpecPathGuard.IsAllowedDirectory(cacheRoot, directory' in source
    assert 'LabSpecPathGuard.IsAllowedFile(directory, documentPath' in source
    assert 'LabSpecPathGuard.IsAllowedFile(directory, receiptPath' in source
    assert 'internal const string Schema = "bridge-stone-form";' in source
    assert 'internal const int SchemaVersion = 1;' in source
    assert 'internal const string StoneFormFileName = "stone-form.json";' in source
    assert 'internal const string ReceiptFileName = "stone-form-receipt.json";' in source
    # The receipt is the trust anchor: every listed file is verified, and stone-form.json itself
    # must be covered by it.
    assert 'string.Equals(Sha256(path), hash, StringComparison.OrdinalIgnoreCase)' in source
    assert 'stone form receipt does not cover ' in source
    assert 'source_read_only' in source
    assert 'is not marked read-only' in source


def test_loader_bounds_are_mirrored_from_the_extraction_module():
    source = STONE_FORM.read_text(encoding='utf-8-sig')
    assert 'internal const int MaxMeshes = 3;' in source
    assert 'internal const int MaxVerticesPerMesh = 2048;' in source
    assert 'internal const int MaxTrianglesPerMesh = 4096;' in source
    assert 'exceeds the mesh budget' in source
    assert 'over the triangle budget' in source
    assert 'bridge/content/sr2_stone_form.py' in source  # provenance comment for the bounds


def test_loader_refuses_traversal_and_degenerate_transforms():
    source = STONE_FORM.read_text(encoding='utf-8-sig')
    assert 'IsSafeFileName' in source
    assert 'references an unsafe file name' in source
    assert 'references an unsafe file' in source
    assert 'value.Contains("..") || value.Contains("/") || value.Contains("\\\\")' in source or \
        '|| value.Contains("\\\\")' in source
    assert 'quaternion is not unit length' in source and 'QuaternionUnitTolerance' in source
    assert 'scale is not a finite non-zero vector' in source
    assert 'position is not a finite vector' in source


def test_visual_build_is_inactive_and_never_partial():
    source = STONE_FORM.read_text(encoding='utf-8-sig')
    assert 'internal const string StoneVisualName = "StoneVisual";' in source
    assert 'internal const string StandardShaderName = "Standard";' in source
    assert 'material.mainTexture = albedo;' in source
    # Bounded material adaptation (docs section 2.5): the source normal map lands on Standard's
    # _BumpMap with the _NORMALMAP keyword and a unit bump scale; the legacy _Normal slot is no
    # longer bound, and the load paths differ: albedo sRGB (linear:false), normal linear (linear:true).
    assert 'private static Texture2D? LoadTexture(string path, bool linear)' in source
    assert 'new Texture2D(2, 2, TextureFormat.RGBA32, false, linear)' in source
    assert 'LoadTexture(Path.Combine(directory, albedoFile), false)' in source
    assert 'LoadTexture(Path.Combine(directory, normalFile), true)' in source
    assert 'material.SetTexture("_BumpMap", normal);' in source
    assert 'material.EnableKeyword("_NORMALMAP");' in source
    assert 'material.SetFloat("_BumpScale", 1f);' in source
    assert 'material.HasProperty("_BumpMap")' in source
    assert 'material.SetTexture("_Normal", normal);' not in source
    # Bounded source render queue: an integral queue inside [0, 5000] is applied, absent => 2000.
    assert 'internal const int MaxRenderQueue = 5000;' in source
    assert 'internal const int StandardDefaultRenderQueue = 2000;' in source
    assert 'materialRow.TryGetValue("render_queue", out var renderQueueValue)' in source
    assert 'material.renderQueue = renderQueue;' in source
    assert 'material.color = Color.white;' in source
    assert 'AddComponent<ImportedStoneFormMarker>()' in source
    assert 'visual.SetActive(false);' in source
    assert 'template.SetActive(false);' in source
    assert 'finally' in source and 'if (!built) DestroyBuilt(' in source
    # Faces, palettes and lighting are out of scope: the loader never writes those systems, and
    # the only float it writes is the bounded bump scale.
    assert 'MaterialPropertyBlock' not in source
    assert 'ImportedPartColors' not in source
    assert '_TopColor' not in source
    assert source.count('SetFloat(') == 1
    assert 'SetColor(' not in source


def test_stone_form_harness():
    scratch = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='stone-form-harness-', dir=scratch) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run(
            [str(DOTNET), 'run', '--project',
             str(ROOT / 'tests/ImportedStoneFormHarness/ImportedStoneFormHarness.csproj'),
             '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=240)
        assert result.returncode == 0, result.stdout
        assert 'stone form checks=' in result.stdout and 'failures=0' in result.stdout
        assert 'no Unity runtime exercised' in result.stdout
