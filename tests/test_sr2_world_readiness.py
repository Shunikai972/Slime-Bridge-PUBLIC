"""Production-linked synthetic query + verbatim heartbeat body, not runtime proof."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def extract(text, marker):
    start = text.rfind('\n', 0, text.index(marker)) + 1
    opening = text.index('{', start)
    depth = 0
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|//[^\r\n]*|/\*[\s\S]*?\*/|[{}]', text[opening:])
    for token in tokens:
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
            if depth == 0:
                return text[start:opening + token.end()]
    raise AssertionError(marker)


def prepare(work):
    bridge = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_bytes().decode('utf-8')
    pieces = [extract(bridge, marker) for marker in (
        'private sealed class GameStatus', 'private static GameStatus? Omit(string reason)',
        'private static GameStatus? SnapshotOnMainThread()', 'public override void OnDeinitializeMelon()')]
    for marker in ('internal const string PinkSlimeRef', 'internal const string PinkPlortRef',
                   'private static readonly DateTime ProcessStartUtc', 'internal static string Since()',
                   'private static string _snapshotReason', 'private static string? _pendingReason'):
        index = bridge.index(marker)
        pieces.append(bridge[bridge.rfind('\n', 0, index) + 1:bridge.index('\n', index)].rstrip('\r'))
    (work / 'Snapshot.cs').write_text('using System; using System.Diagnostics;\n'
        'namespace SlimeBridge.SR2 { internal sealed class LabFocusDiagnostics { internal void Stop(string reason) {} } internal class MelonStub { public virtual void OnDeinitializeMelon() {} } internal class TravelStub { public void Unload() { } public void Tick() { } public void Gui() { } } internal class BridgeMod : MelonStub { TravelStub? _travel = null; System.Threading.CancellationTokenSource? _stop = null; InventoryAgent? _inventory = null; LabFocusDiagnostics? _focusDiagnostics = null; internal bool _focusDiagnosticAllowed = false; int _focusEpoch = 0;\n' + '\n'.join(pieces) + '\n}}', encoding='utf-8')
    (work / 'extraction.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256((ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_bytes()).hexdigest(),
        'pieces': [{'verbatim': piece in bridge, 'sha256': hashlib.sha256(piece.encode()).hexdigest()} for piece in pieces]}, indent=2), encoding='utf-8')
    fixture = (ROOT / 'tests/CounterpartCreatorHarness/InventoryApiFixtures.cs').read_text(encoding='utf-8')
    fixture = re.sub(r'namespace SlimeBridge.SR2\s*\{.*?\}\s*namespace Il2Cpp', 'namespace Il2Cpp', fixture, count=1, flags=re.S)
    old = 'public sealed class LookupDirector\n    { public bool TryFindIdentifiableTypeByReferenceId(string id, out IdentifiableType? type) { type = null; return false; } }'
    new = '''public sealed class LookupDirector {
        public readonly Dictionary<string, IdentifiableType> Items = new(StringComparer.Ordinal);
        public int Calls;
        public System.Action? OnLookup;
        public bool TryFindIdentifiableTypeByReferenceId(string id, out IdentifiableType? type)
        { Calls++; OnLookup?.Invoke(); return Items.TryGetValue(id, out type); }
    }'''
    if old not in fixture:
        raise AssertionError('lookup fixture shape changed')
    (work / 'InventoryDoubles.cs').write_text(fixture.replace(old, new), encoding='utf-8')
    sources = ['mods/shared/BridgeCommands.cs', 'mods/shared/SaveSlotSelection.cs',
        'mods/sr2-bridge/CounterpartCreator.cs', 'mods/sr2-bridge/InventoryAgent.cs',
        'mods/sr2-bridge/NativeStorageBinding.cs', 'mods/sr2-bridge/NativeStockDefinitions.cs', 'tests/CounterpartCreatorHarness/Sr2CompatFixture.cs',
        'tests/CounterpartCreatorHarness/NativeApiFixtures.cs', 'tests/CounterpartCreatorHarness/ReadinessChecks.cs',
        'tests/CounterpartCreatorHarness/IntroSkipFixture.cs']
    linked = ''.join('<Compile Include="' + (ROOT / rel).as_posix() + '" />' for rel in sources)
    (work / 'Readiness.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
        '<ImplicitUsings>enable</ImplicitUsings><TreatWarningsAsErrors>true</TreatWarningsAsErrors>'
        '<DefineConstants>READINESS_SNAPSHOT</DefineConstants><NuGetAudit>false</NuGetAudit>'
        '</PropertyGroup><ItemGroup>' + linked + '</ItemGroup></Project>', encoding='utf-8')
    (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf-8')
    (work / 'sources.json').write_text(json.dumps({rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in sources}, indent=2), encoding='utf-8')


class SR2WorldReadinessTests(unittest.TestCase):
    def test_actual_creator_query_and_snapshot(self):
        dotnet = os.environ.get('READINESS_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        evidence = os.environ.get('READINESS_ARTIFACTS')
        with tempfile.TemporaryDirectory(prefix='sr2-readiness-') as temporary:
            work = Path(evidence or temporary)
            work.mkdir(parents=True, exist_ok=True)
            prepare(work)
            mode = os.environ.get('READINESS_MODE', 'all')
            build_command = [dotnet, 'build', str(work / 'Readiness.csproj'), '-c', 'Release', '--no-incremental']
            build = subprocess.run(build_command, cwd=ROOT, env=dict(os.environ), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', timeout=180)
            (work / 'build.log').write_text(build.stdout, encoding='utf-8')
            self.assertEqual(build.returncode, 0, 'COMPILATION FAILURE, not behavioral RED: ' + build.stdout)
            command = [dotnet, 'run', '--project', str(work / 'Readiness.csproj'), '-c', 'Release', '--no-build', '--', mode]
            result = subprocess.run(command, cwd=ROOT, env=dict(os.environ), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', timeout=180)
            public_env = {key: os.environ.get(key) for key in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME',
                'DOTNET_ROOT', 'DOTNET_GENERATE_ASPNET_CERTIFICATE', 'DOTNET_SKIP_FIRST_TIME_EXPERIENCE',
                'DOTNET_CLI_TELEMETRY_OPTOUT', 'PYTHONPATH')}
            (work / 'execution.json').write_text(json.dumps({'command': command, 'exit_code': result.returncode,
                'build_command': build_command, 'build_exit_code': build.returncode, 'cwd': str(ROOT), 'explicit_env': public_env,
                'synthetic_only': True, 'native_runtime_verified': False}, indent=2), encoding='utf-8')
            (work / 'run.log').write_text(result.stdout, encoding='utf-8')
            print(result.stdout, end='')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('readiness failures=0', result.stdout)


if __name__ == '__main__':
    unittest.main()
