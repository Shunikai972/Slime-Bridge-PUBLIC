"""Spawn palette scope: the MaterialPropertyBlock palette pass must never tint the imported visual.

The spawn palette loop (ImportedSpawner.Spawn) paints placeholder renderers with the source
species palette through renderer-wide MaterialPropertyBlocks. The appearance guard attaches the
imported visual DURING Object.Instantiate (Harmony postfix on the applicator Awake), so before
this contract the loop also repainted the visual's SlimeBridgeNativeBody(Clone) renderers with
the spec palette (ringtail-reapply-color-v1, fait principal H-A) while the validated material
colors (214/189/148) are the ones the native rethrow and load paths render. The spawn must keep
the visual out of the palette scope, and ApplyImportedVisualToRoot must fail-safe clean legacy
palette blocks off the visual subtree so pre-fix/rebuilt instances heal too.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPAWNER = ROOT / 'mods/sr1-bridge/ImportedSpawner.cs'
DIAGNOSTICS = ROOT / 'mods/sr1-bridge/BridgeDiagnostics.cs'


def test_palette_pass_skips_the_marked_visual_subtree():
    spawner = SPAWNER.read_text(encoding='utf-8-sig')
    loop = spawner[spawner.index('if (registrar.TryGetPalette('):
                    spawner.index('details.Add("\\"palette_applied\\": false")')]
    assert 'var visual = FindImportedVisual(instance);' in loop
    assert 'IsInsideVisualSubtree(renderer, instance, visual)' in loop
    # Skipped renderers are excluded before the palette scope counts anything: the counters stay
    # honest (visited/supported/verified cover the placeholder scope only) and the exclusions are
    # reported explicitly.
    assert loop.index('IsInsideVisualSubtree(renderer, instance, visual)') < loop.index('visited++')
    assert 'palette_visual_skipped' in loop
    predicate = spawner[spawner.index('private static bool IsInsideVisualSubtree('):
                        spawner.index('/// <summary>Bounded suppression walk')]
    assert 'ImportedVisualMarker' in predicate and 'current.parent' in predicate


def test_visual_palette_cleanup_exists_and_is_wired_into_the_apply():
    spawner = SPAWNER.read_text(encoding='utf-8-sig')
    apply = spawner[spawner.index('internal static bool ApplyImportedVisualToRoot('):
                    spawner.index('private static void RebindNativeMaterials(')]
    assert apply.index('RebindNativeMaterials(instance, visual, canonical);') < \
        apply.index('ClearVisualPaletteBlocks(visual);')
    assert 'private static void ClearVisualPaletteBlocks(GameObject visual)' in spawner
    assert 'if (renderer == null || !renderer.HasPropertyBlock()) continue;' in spawner
    assert 'block.Clear();' in spawner and 'renderer.SetPropertyBlock(block);' in spawner
    assert 'catch (Exception)' in spawner
    # The per-material slot storage (native _HeldInVac) is never read or written by the sweep.
    assert 'materialIndex' not in spawner[spawner.index('private static void ClearVisualPaletteBlocks('):
                                          spawner.index('internal static string ResolveImportedCanonical(')]


def test_guards_unregressed_and_registrar_negative_cache_not_pinned():
    spawner = SPAWNER.read_text(encoding='utf-8-sig')
    resolve = spawner[spawner.index('internal static string ResolveImportedCanonical('):
                      spawner.index('internal static void GuardApplicator(')]
    assert 'TryGetValue(applicator, out var cached)) return cached;' in resolve
    assert 'registrarArmed = registrar != null;' in resolve
    assert 'canonical.Length > 0 || registrarArmed' in resolve
    guard = spawner[spawner.index('internal static void GuardApplicator('):]
    assert 'if (string.IsNullOrEmpty(canonical)) return;' in guard
    diagnostics = DIAGNOSTICS.read_text(encoding='utf-8-sig')
    # P4 LAB read-back: per-renderer color block values, material palette values, rebind evidence,
    # all rest-pose gated and read-only.
    assert r'\"palette\": {' in diagnostics
    assert r'\"color_block\"' in diagnostics
    assert 'visual_rebind_ok' in diagnostics
    assert diagnostics.count('BridgeEntry.LabRestPoseRequested') >= 6


def test_palette_scope_harness():
    dotnet = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'
    with tempfile.TemporaryDirectory(prefix='palette-scope-', dir=os.environ.get('TMPDIR')) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run([str(dotnet), 'run', '--project',
                                 str(ROOT / 'tests/ImportedPaletteScopeHarness/ImportedPaletteScopeHarness.csproj'),
                                 '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
                                cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', timeout=240)
        assert result.returncode == 0, result.stdout
        assert 'palette scope checks=' in result.stdout and 'failures=0' in result.stdout
        assert 'no Unity rendering exercised' in result.stdout
