"""Ringtail solar petrification machine: wiring, state machine and freeze/restore contracts.

ImportedPetrification mirrors the native DestroyOutsideHoursOfDay idiom (CaveTrigger.Listener,
HashSet<GameObject> of protecting volumes, null purge) but is bounded to the v1 adaptation in
docs/RINGTAIL_STONE_FORM.md: day is TimeDirector.CurrHour() in [6, 18), each exposure episode
draws a uniform [1, 10]-minute target (RingtailMetadata 1.0/10.0), shelter/darkness revert and
reset the accumulator, and an uncertain clock never petrifies. The machine is attached only for
species whose cache declares a stone form — ImportedSpawner calls EnsureAttached behind
ImportedStoneForm.TryGet at the same place as the gameplay-animation attach — and the machine is
read-only-exposed per instance by the rest-pose-gated BridgeDiagnostics payload. The CPU harness
links the production files against minimal UnityEngine doubles (no Unity runtime, no GPU).
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PETRIFICATION = ROOT / 'mods/sr1-bridge/ImportedPetrification.cs'
STONE_FORM = ROOT / 'mods/sr1-bridge/ImportedStoneForm.cs'
SPAWNER = ROOT / 'mods/sr1-bridge/ImportedSpawner.cs'
DIAGNOSTICS = ROOT / 'mods/sr1-bridge/BridgeDiagnostics.cs'
DOTNET = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'


def test_machine_constants_and_day_window_match_the_frozen_design():
    source = PETRIFICATION.read_text(encoding='utf-8-sig')
    assert 'internal const float MinMinutesToTransform = 1.0f;' in source
    assert 'internal const float MaxMinutesToTransform = 10.0f;' in source
    assert 'internal const float DaylightStartHour = 6.0f;' in source
    assert 'internal const float DaylightEndHour = 18.0f;' in source
    assert 'RingtailMetadata' in source and 'docs/RINGTAIL_STONE_FORM.md' in source
    assert 'hour >= DaylightStartHour && hour < DaylightEndHour' in source
    assert 'SceneContext.Instance' in source and 'CurrHour()' in source
    assert 'UnityEngine.Random.Range(MinMinutesToTransform, MaxMinutesToTransform)' in source
    assert 'director.WorldTime()' in source
    assert '_exposureMinutes += gameMinutes;' in source
    assert '_exposureMinutes += deltaSeconds / 60f;' not in source
    step = source[source.index('internal void Step(float deltaSeconds)'):source.index('public void OnCaveEnter(')]
    assert step.index('if (!_exposureEpisode)') < step.index('_exposureMinutes += gameMinutes')
    assert step.index('_exposureMinutes += gameMinutes') < step.index('Petrify()')
    # Uncertainty fail-opens to the living state and never petrifies.
    assert '_daylightKnown = director != null' in step
    assert step.index('if (!_daylightKnown || !_daylight || protectedNow)') < step.index('Petrify()')


def test_machine_follows_the_native_cave_listener_idiom():
    source = PETRIFICATION.read_text(encoding='utf-8-sig')
    assert 'CaveTrigger.Listener' in source
    assert 'public void OnCaveEnter(GameObject caveObject, bool affectLighting, AmbianceDirector.Zone caveZone)' in source
    assert 'public void OnCaveExit(GameObject caveObject, bool affectLighting, AmbianceDirector.Zone caveZone)' in source
    assert 'HashSet<GameObject> _protectingVolumes' in source
    assert '_protectingVolumes.RemoveWhere(IsDestroyedVolume)' in source
    assert 'if (caveObject != null) _protectingVolumes.Add(caveObject);' in source
    assert 'if (caveObject != null) _protectingVolumes.Remove(caveObject);' in source


def test_freeze_restore_is_exact_and_captured_once():
    source = PETRIFICATION.read_text(encoding='utf-8-sig')
    for component in ('SlimeRandomMove', 'SlimeEat', 'GotoConsumable', 'FleeThreats', 'SlimeFeral'):
        assert 'GetComponent<' + component + '>()' in source
    capture = source[source.index('private void CaptureOriginalEnabled()'):source.index('private void AddFrozen(')]
    assert 'if (_captured) return;' in capture and '_captured = true;' in capture
    assert 'CapturedEnabled = component.enabled' in source
    assert 'entry.Component.enabled = entry.CapturedEnabled;' in source
    assert 'private void OnDestroy()' in source
    assert 'RestoreFrozen();' in source[source.index('private void OnDestroy()'):source.index('private void PurgeProtectingVolumes()')]


def test_visual_swap_is_bounded_and_never_touches_face_palette_lighting():
    source = PETRIFICATION.read_text(encoding='utf-8-sig')
    assert 'ImportedVisualMarker' in source
    assert 'SetSubtreeRenderersEnabled(live, !hidden)' in source
    assert '_stoneVisual.SetActive(true)' in source
    assert '_stoneVisual.SetActive(false)' in source
    assert 'ImportedStoneForm.AttachTo(gameObject, _stoneForm' in source
    assert 'MaterialPropertyBlock' not in source
    # Metadata is explicitly excluded from the animation hold; mentioning its type is
    # not a material write. Keep the write prohibitions and verify the exclusion order.
    capture = source[source.index('private void CaptureOriginalEnabled()'):source.index('private void AddFrozen(')]
    skip = 'behaviour is ImportedPartColors || behaviour is SourceFaceResourceOwner) continue;'
    assert skip in capture and capture.index(skip) < capture.index('AddFrozen(behaviour);')
    assert 'GetComponent<ImportedPartColors>()' not in source
    assert 'ImportedBodyLighting' not in source
    assert '_TopColor' not in source
    assert 'SetFloat(' not in source and 'SetColor(' not in source


def test_spawner_attaches_petrification_behind_the_stone_form_cache():
    spawner = SPAWNER.read_text(encoding='utf-8-sig')
    apply = spawner[spawner.index('internal static bool ApplyImportedVisualToRoot('):
                    spawner.index('private static void RebindNativeMaterials(')]
    assert 'ImportedStoneForm.TryGet(canonical, out var stoneForm, out _)' in apply
    assert 'ImportedPetrification.EnsureAttached(instance, canonical, stoneForm, out var petrificationProblem)' in apply
    assert apply.index('ImportedGameplayAnimation.EnsureAttached') < apply.index('ImportedPetrification.EnsureAttached')
    assert apply.index('ImportedPetrification.EnsureAttached') < apply.index('RegisterImportRoot(instance);')
    assert apply.index('ImportedStoneForm.TryGet') < apply.index('ImportedPetrification.EnsureAttached')
    assert '"\\"petrification_attached\\": "' in spawner
    # The loader stays attach-free: only the petrification machine owns EnsureAttached.
    assert 'EnsureAttached' not in STONE_FORM.read_text(encoding='utf-8-sig')


def test_diagnostics_expose_petrification_only_at_rest_pose():
    diagnostics = DIAGNOSTICS.read_text(encoding='utf-8-sig')
    assert '(BridgeEntry.LabRestPoseRequested ? ", \\"petrification\\": " + PetrificationJson(root) : "")' in diagnostics
    assert 'private static string PetrificationJson(GameObject actor)' in diagnostics
    assert 'actor.GetComponent<ImportedPetrification>()' in diagnostics
    assert 'machine.ToJson()' in diagnostics
    assert diagnostics.index('BridgeEntry.LabRestPoseRequested ? ", \\"petrification\\": "') < \
        diagnostics.index('private static string PetrificationJson(GameObject actor)')


def test_petrification_harness():
    scratch = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='petrification-harness-', dir=scratch) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run(
            [str(DOTNET), 'run', '--project',
             str(ROOT / 'tests/ImportedPetrificationHarness/ImportedPetrificationHarness.csproj'),
             '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=240)
        assert result.returncode == 0, result.stdout
        assert 'petrification checks=99 failures=0' in result.stdout
        assert 'no Unity runtime exercised' in result.stdout
