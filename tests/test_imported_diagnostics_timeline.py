"""LAB diagnostics timeline: bounded periodic machine-state sampling for long proofs.

DiagnosticsTimeline parses SLIMEBRIDGE_DIAG_TIMELINE_SECONDS (strict invariant integer in
[5, 900]) and formats the compact single-line JSONL record appended by LabImportedOps once per
interval during a LAB rest-pose session: one "utc/elapsed_seconds/entries" object per line, one
entry per tracked imported root carrying its ImportedPetrification state. Read-only
instrumentation: it never mutates an actor and refuses unsafe diag file tokens. The CPU harness
links the production file and checks the parser bounds, the token guard and the JSONL line
shape against real System.Text.Json parsing — no Unity runtime.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TIMELINE = ROOT / 'mods/sr1-bridge/DiagnosticsTimeline.cs'
OPS = ROOT / 'mods/sr1-bridge/LabImportedOps.cs'
DOTNET = Path(os.environ.get('DOTNET_ROOT', str(Path.home() / '.dotnet'))) / 'dotnet.exe'


def test_timeline_source_contracts():
    source = TIMELINE.read_text(encoding='utf-8-sig')
    assert 'SLIMEBRIDGE_DIAG_TIMELINE_SECONDS' in source
    assert 'internal const int MinSeconds = 5;' in source
    assert 'internal const int MaxSeconds = 900;' in source
    assert 'internal const int MaxWrites = 256;' in source
    assert 'NumberStyles.None' in source and 'CultureInfo.InvariantCulture' in source
    assert 'IsSafeFileToken' in source and 'value.Contains("..")' in source
    assert '"elapsed_seconds"' in source and '"petrification"' in source and '"instance"' in source
    # Pure helper: no Unity API and no actor/material mutation of any kind.
    assert 'using UnityEngine' not in source
    assert not re.search(r'\b(Destroy|SetPropertyBlock|SetColor|SetFloat|MaybeAddToSpecificSlot)\b', source)


def test_lab_imported_ops_timeline_wiring():
    source = OPS.read_text(encoding='utf-8-sig')
    assert 'DiagnosticsTimeline.SecondsVariable' in source
    assert 'DiagnosticsTimeline.TryParseSeconds' in source
    assert re.search(r'return Refuse\(timelineProblem\)', source)
    assert 'private void TickTimeline()' in source
    assert 'ImportedSpawner.TrackedImportRoots' in source
    assert 'GetComponent<ImportedPetrification>()' in source
    assert 'BridgeEntry.LabRestPoseRequested' in source
    assert 'SLIMEBRIDGE_DIAG' in source
    assert '_timelineNext = ' in source and '+ _timelineSeconds' in source
    assert 'File.AppendAllText' in source and '.timeline.jsonl' in source
    assert 'DiagnosticsTimeline.IsSafeFileToken' in source
    assert 'DiagnosticsTimeline.MaxWrites' in source
    assert 'read_error' in source


def test_timeline_checks_deadline_and_stop_budget_before_lab_io():
    source = OPS.read_text(encoding='utf-8-sig')
    sampler = source[source.index('private void TickTimeline()'):source.index('private void WriteDiagnostics()')]
    authority = sampler.index('BridgeEntry.LabRestPoseRequested')
    assert sampler.index('_timelineWrites >= DiagnosticsTimeline.MaxWrites') < authority
    assert sampler.index('if (now < _timelineNext) return;') < authority, \
        'LAB authorization reads the marker file; it must not run before a sample is due'
    assert sampler.index('_timelineNext = now + _timelineSeconds;') < authority


def test_diagnostics_timeline_harness():
    scratch = Path(os.environ.get('TMPDIR', tempfile.gettempdir()))
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='diag-timeline-harness-', dir=scratch) as temporary:
        environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                           DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                           DOTNET_CLI_TELEMETRY_OPTOUT='1')
        result = subprocess.run(
            [str(DOTNET), 'run', '--project',
             str(ROOT / 'tests/ImportedDiagnosticsTimelineHarness/ImportedDiagnosticsTimelineHarness.csproj'),
             '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', timeout=240)
        assert result.returncode == 0, result.stdout
        assert 'diagnostics timeline checks=22 failures=0' in result.stdout
        assert 'no Unity runtime exercised' in result.stdout
