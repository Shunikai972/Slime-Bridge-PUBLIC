"""Sloomber parts loader contract: static-row parent_bone, shader-family rebind skip, skeleton refusals.

The Sloomber mesh-only cache (Lab/AssetCache/sr2-to-sr1/slime.sloomber, scope
appearance-lod0-mesh-only-v1) adds a STATIC attachment row (the source FX cloud) whose parent_bone
is bone_slime and whose source shader is SR/FX/Smoke Sloomber. Three loader defects are closed
here: static rows used to ignore parent_bone and silently create missing containers (a wrong path
left the cloud visibly present but detached from its bone), the spawn native-material rebind
repainted every non-face renderer as a slime body (the cloud rendered as an opaque palette blob)
and the skeleton builder silently flattened a bone whose parent index was out of order to the
model root. Static contracts plus the CPU harness linking the production ImportedPartColors with
a serialized-field transfer model; no Unity rendering is exercised.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
LOADER = ROOT / 'mods/sr1-bridge/ImportedModelLoader.cs'
MARKER = ROOT / 'mods/sr1-bridge/ImportedPartColors.cs'
SPAWNER = ROOT / 'mods/sr1-bridge/ImportedSpawner.cs'
DIAGNOSTICS = ROOT / 'mods/sr1-bridge/BridgeDiagnostics.cs'
HARNESS = ROOT / 'tests/ImportedSloomberPartsHarness/Program.cs'
DOTNET = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
# Mirrors ImportedPartColors.NativeSlimeShaderFamily; pinned below against the production literal.
SLIME_SHADER_FAMILY = 'SR/AMP/Slime/'


def _asset_cache():
    for candidate in (ROOT / 'Lab/AssetCache/sr2-to-sr1',
                      ROOT.parents[1] / 'Lab/AssetCache/sr2-to-sr1'):
        if candidate.is_dir():
            return candidate
    pytest.skip('LAB sr2-to-sr1 asset cache is absent from this checkout')


def test_static_rows_consume_parent_bone_and_never_create_containers():
    loader = LOADER.read_text(encoding='utf-8-sig')
    assert 'string.Equals(groupName, "static_renderers", StringComparison.Ordinal),' in loader
    helper = loader[loader.index('private static bool TryResolveStaticParent('):
                    loader.index('private static void ApplyLocalTransform(')]
    assert 'renderer.TryGetValue("parent_bone", out var declaredParent)' in helper
    assert 'FindChildByName(root, parentBone)' in helper
    assert 'ReferenceEquals(declared, current)' in helper
    assert 'parts[parts.Length - 2]' in helper
    assert 'EnsurePath' not in helper and 'new GameObject' not in helper
    for refusal in ('static row has no parent container', 'static row parent_bone must be a string',
                    'static row parent_bone does not match its path', 'static row path is unresolved at ',
                    'static row parent bone is unresolved: ', 'static row parent_bone disagrees with its path'):
        assert refusal in helper
    renderer_build = loader[loader.index('private static bool TryBuildRenderer('):
                            loader.index('private static Transform EnsurePath(')]
    assert 'bool staticRow,' in renderer_build
    assert 'if (staticRow)' in renderer_build
    assert 'TryResolveStaticParent(root, renderer, path, out parent, out var parentProblem)' in renderer_build
    # The static branch is the strict one: EnsurePath (the creating resolver) serves skinned
    # rows only, and never the static rows that carry an authored bone parent.
    assert renderer_build.count('EnsurePath(') == 1
    assert renderer_build.index('if (staticRow)') < renderer_build.index('EnsurePath(')
    assert renderer_build.index('return false; }') < renderer_build.index('EnsurePath(')


def test_skeleton_parent_refusal_is_fail_closed():
    loader = LOADER.read_text(encoding='utf-8-sig')
    assert 'private static bool BuildSkeleton(Transform root, Dictionary<string, object> document, out string problem)' in loader
    assert 'parentIndex >= i || parentIndex < -1 || (parentIndex >= 0 && transforms[parentIndex] == null)' in loader
    assert 'parentIndex >= 0 && parentIndex < i && transforms[parentIndex] != null' not in loader
    assert '.SetParent(parentIndex >= 0 ? transforms[parentIndex] : root, false);' in loader
    call = loader[loader.index('if (!BuildSkeleton('):]
    assert 'if (!BuildSkeleton(root.transform, document, out var skeletonProblem))' in call[:200]
    assert 'UnityEngine.Object.Destroy(root);' in call[:400]
    assert 'return false;' in call[:400]


def test_marker_transports_role_and_shader_for_the_instantiate_clone():
    marker = MARKER.read_text(encoding='utf-8-sig')
    assert '[SerializeField] private string sourceRole = "";' in marker
    assert '[SerializeField] private string sourceShader = "";' in marker
    assert 'internal string SourceRole' in marker and 'internal string SourceShader' in marker
    assert 'internal bool SourceShaderKnown => !string.IsNullOrEmpty(sourceShader);' in marker
    assert 'internal const string NativeSlimeShaderFamily = "SR/AMP/Slime/";' in marker
    assert 'internal static bool IsNativeSlimeFamilyShader(string shaderName)' in marker
    assert 'shaderName.StartsWith(NativeSlimeShaderFamily, StringComparison.Ordinal)' in marker
    assert 'internal bool SkipNativePaletteRebind => SourceShaderKnown && !IsNativeSlimeFamilyShader(sourceShader);' in marker
    loader = LOADER.read_text(encoding='utf-8-sig')
    add = loader.index('var partData = gameObject.AddComponent<ImportedPartColors>();')
    assert loader.index('partData.SourceRole = ', add) < loader.index('if (partData.SourceFace)', add)
    assert loader.index('partData.SourceShader = ', add) < loader.index('if (partData.SourceFace)', add)
    assert loader.index('shaderRow.TryGetValue("shader", out var shaderValue)') > add


def test_rebind_skip_is_shader_family_scoped_and_preserves_existing_species():
    spawner = SPAWNER.read_text(encoding='utf-8-sig')
    face_skip = 'if (partData != null && partData.SourceFace) continue;'
    family_skip = 'if (partData != null && partData.SkipNativePaletteRebind) continue;'
    assert face_skip in spawner and family_skip in spawner
    # The skip stays inside the visual rebind only: the placeholder palette scope is untouched.
    assert spawner.count('SkipNativePaletteRebind') == 1
    rebind = spawner[spawner.index('private static void RebindNativeMaterials('):
                     spawner.index('private static void SetMaterialColor(')]
    assert rebind.index(face_skip) < rebind.index(family_skip)
    assert rebind.index(family_skip) < rebind.index('for (var i = 0; i < shared.Length; i++)')
    # Existing body/face parts keep the exact palette path (clone + three-way palette + stripe).
    assert 'var clone = new Material(template) { name = isFace ? "SlimeBridgeNativeFace(Clone)" : "SlimeBridgeNativeBody(Clone)" };' in rebind
    assert 'SetMaterialColor(clone, "_TopColor", partTop);' in rebind
    assert 'SetMaterialColor(clone, "_MiddleColor", partMiddle);' in rebind
    assert 'SetMaterialColor(clone, "_BottomColor", partBottom);' in rebind
    assert 'SetMaterialTexture(clone, "_StripeTexture", partData.Pattern);' in rebind
    assert 'if (!isFace && partData != null) partData.BodyLighting?.Apply(clone);' in rebind
    assert 'if (partData != null && !float.IsNaN(partData.Gloss)) SetMaterialFloat(clone, "_Gloss", partData.Gloss);' in rebind


def test_skip_condition_covers_the_cloud_row_and_not_ringtail_bodies():
    cache = _asset_cache()
    document = json.loads((cache / 'slime.sloomber/model.json').read_text(encoding='utf-8'))
    assert document['source_extraction_scope'] == 'appearance-lod0-mesh-only-v1'
    static_rows = document['static_renderers']
    assert len(static_rows) == 1
    cloud = static_rows[0]
    assert cloud['role'] == 'attachment' and cloud['parent_bone'] == 'bone_slime'
    assert cloud['materials'][0]['shader'] == 'SR/FX/Smoke Sloomber'
    assert not cloud['materials'][0]['shader'].startswith(SLIME_SHADER_FAMILY)
    kept = []
    foreign = []
    for canonical in ('slime.sloomber', 'slime.ringtail', 'slime.batty', 'slime.twin'):
        document = json.loads((cache / canonical / 'model.json').read_text(encoding='utf-8'))
        for row in (document.get('skinned_renderers') or []) + (document.get('static_renderers') or []):
            shader = (row.get('materials') or [{}])[0].get('shader') or ''
            if not shader:
                continue  # legacy rows declare no shader: the loader keeps the legacy rebind
            if shader.startswith(SLIME_SHADER_FAMILY):
                kept.append((canonical, row.get('role'), shader))
            else:
                # A non-slime source shader is only legitimate for face rows (the immutable
                # source-face path, unchanged) or attachment rows (the new shader-family skip);
                # a body row declaring one would change its current rebind.
                assert row.get('role') in ('face', 'attachment'), (canonical, row.get('path'), shader)
                foreign.append((canonical, row.get('role'), shader))
    assert all(shader == 'SR/AMP/Slime/Body' for _, role, shader in kept if role == 'body')
    assert 'SR/AMP/Slime/Body' in [shader for _, _, shader in kept]
    assert 'SR/AMP/Slime/TwinOrbitals' in [shader for _, _, shader in kept]
    # The only foreign-shader attachment is the Sloomber cloud: it is the row the skip must
    # cover, while every body row (including ringtail's) keeps the legacy rebind.
    assert [entry for entry in foreign if entry[1] == 'attachment'] == \
        [('slime.sloomber', 'attachment', 'SR/FX/Smoke Sloomber')]
    # The harness runs the production predicate on the same literals the cache declares, and the
    # family prefix mirrored above is pinned to the production constant.
    harness = HARNESS.read_text(encoding='utf-8-sig')
    assert 'SR/FX/Smoke Sloomber' in harness and 'SR/AMP/Slime/Body' in harness and 'SR/AMP/Slime/TwinOrbitals' in harness
    assert 'internal const string NativeSlimeShaderFamily = "SR/AMP/Slime/";' in MARKER.read_text(encoding='utf-8-sig')


def test_diagnostics_expose_part_provenance_at_rest_pose_read_only():
    diagnostics = DIAGNOSTICS.read_text(encoding='utf-8-sig')
    describe = diagnostics[diagnostics.index('private static string DescribeRenderer('):
                           diagnostics.index('private static bool IsFullyRebound(')]
    block = describe[describe.index(r'sb.Append(", \"source_face\": ")'):
                     describe.index(r'sb.Append(", \"materials\": [")')]
    assert 'BridgeEntry.LabRestPoseRequested' in block
    assert r'sb.Append(", \"source_role\": ").Append(RegistrySnapshotFormat.Quote(partData != null ? partData.SourceRole : ""));' in block
    assert r'sb.Append(", \"source_shader\": ").Append(RegistrySnapshotFormat.Quote(partData != null ? partData.SourceShader : ""));' in block
    assert r'sb.Append(", \"native_palette_rebind_skipped\": ").Append(partData != null && partData.SkipNativePaletteRebind ? "true" : "false");' in block
    # Read-only: the provenance block is emitted regardless of the source-face branch and never
    # writes to the marker or the materials.
    assert describe.index(r'sb.Append(", \"native_palette_rebind_skipped\": ') < describe.index('if (sourceFace)')
    assert 'partData.SourceRole = ' not in block and 'SetColor' not in block
    assert 'SetFloat' not in block and 'SetTexture' not in block


def test_rebind_evidence_counts_skipped_parts_separately():
    diagnostics = DIAGNOSTICS.read_text(encoding='utf-8-sig')
    proof = diagnostics[diagnostics.index('private static void AppendImportedVisualProof('):
                        diagnostics.index('private static string SourceAnimationJson(')]
    assert 'var rebindSkippedParts = 0;' in proof
    assert 'if (partData != null && partData.SkipNativePaletteRebind)' in proof
    assert 'rebindSkippedParts++;' in proof
    assert 'RebindEvidenceJson(bodyRenderers, reboundBodies, rebindSkippedParts)' in proof
    evidence = diagnostics[diagnostics.index('private static string RebindEvidenceJson('):
                           diagnostics.index('private static string SourceFaceEvidence(')]
    assert 'private static string RebindEvidenceJson(int bodyRenderers, int reboundBodies, int rebindSkippedParts)' in evidence
    assert 'if (!BridgeEntry.LabRestPoseRequested) return "";' in evidence
    assert r'\"rebind_skipped_parts\": ' in evidence


def test_sloomber_parts_harness():
    scratch = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='sloomber-parts-', dir=scratch) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run(
            [str(DOTNET), 'run', '--project',
             str(ROOT / 'tests/ImportedSloomberPartsHarness/ImportedSloomberPartsHarness.csproj'),
             '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=240)
        assert result.returncode == 0, result.stdout
        assert 'sloomber parts checks=' in result.stdout and 'failures=0' in result.stdout
        assert 'no Unity rendering exercised' in result.stdout
