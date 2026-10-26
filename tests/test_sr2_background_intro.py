"""Production-linked NEWGAME intro fixture. Synthetic, never in-game proof."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def prepare(work):
    sources = ['mods/shared/BridgeCommands.cs', 'mods/shared/SaveSlotSelection.cs',
        'mods/sr2-bridge/CounterpartCreator.cs', 'mods/sr2-bridge/InventoryAgent.cs',
        'mods/sr2-bridge/NativeStorageBinding.cs', 'mods/sr2-bridge/NativeStockDefinitions.cs', 'tests/CounterpartCreatorHarness/Sr2CompatFixture.cs',
        'tests/CounterpartCreatorHarness/InventoryApiFixtures.cs']
    for rel in ['mods/sr2-bridge/BackgroundIntroSkipper.cs', 'mods/shared/BackgroundGameFocus.cs']:
        if (ROOT / rel).exists():
            sources.append(rel)
    fixture_path = ROOT / 'tests/CounterpartCreatorHarness/NativeApiFixtures.cs'
    native = fixture_path.read_text(encoding='utf-8')
    replacements = {
        'public sealed class GameObject { public bool activeInHierarchy = true; }':
            'public sealed class GameObject : Object { public bool activeInHierarchy = true; }',
        'public static class Time { public static float realtimeSinceStartup; }':
            'public static class Time { public static float realtimeSinceStartup; public static int frameCount; }',
        'public sealed class List<T> : System.Collections.Generic.List<T>, IEnumerable<T>\n    {':
            'public sealed class List<T> : System.Collections.Generic.List<T>, IEnumerable<T>\n    { public IntPtr Pointer = new Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase().Pointer;',
        'public sealed class SceneContext : Il2CppInterop.Runtime.InteropTypes.Il2CppObjectBase':
            'public sealed class SceneContext : UnityEngine.Object',
        'public static SceneContext? Instance; public PlayerState? PlayerState; public UnityEngine.GameObject? Player;':
            'public static SceneContext? Instance; public PlayerState? PlayerState; public UnityEngine.GameObject? Player; public Il2CppMonomiPark.SlimeRancher.Cutscene.CutsceneDirector? CutsceneDirector;',
        'public sealed class GameContext\n    {':
            'public sealed class GameContext : UnityEngine.Object\n    { public Il2CppMonomiPark.SlimeRancher.UI.UIDirector? UIDirector;',
    }
    for old, new in replacements.items():
        if native.count(old) != 1:
            raise AssertionError('strict fixture extension shape changed: ' + old)
        native = native.replace(old, new, 1)
    (work / 'NativeApiFixtures.cs').write_text(native, encoding='utf-8')
    for path in (ROOT / 'tests/SR2BackgroundIntroHarness').glob('*.cs'):
        shutil.copyfile(path, work / path.name)
    from test_sr2_world_readiness import extract
    bridge = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
    shutdown = extract(bridge, 'public override void OnDeinitializeMelon()')
    unload_marker = 'public override void OnSceneWasUnloaded('
    unload = extract(bridge, unload_marker) if unload_marker in bridge else 'public override void OnSceneWasUnloaded(int index, string name) { }'
    (work / 'LifecycleBridge.cs').write_text('using System; using System.Threading; namespace SlimeBridge.SR2 {'
        'internal sealed class LabFocusDiagnostics { internal void Stop(string reason) {} } internal class LifecycleBase { public virtual void OnDeinitializeMelon() {} public virtual void OnSceneWasUnloaded(int index, string name) {} }'
        'internal sealed class TravelStub { public void Unload() { } public void Tick() { } public void Gui() { } }\ninternal sealed class LifecycleBridge : LifecycleBase { TravelStub? _travel = null; InventoryAgent? _inventory; CancellationTokenSource? _stop = null; LabFocusDiagnostics? _focusDiagnostics = null; internal bool _focusDiagnosticAllowed = false; int _focusEpoch = 0;'
        'internal LifecycleBridge(InventoryAgent inventory) { _inventory = inventory; }\n' + shutdown + '\n' + unload + '\n}}', encoding='utf-8')
    (work / 'lifecycle-extraction.json').write_text(json.dumps({'source_sha256': hashlib.sha256((ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_bytes()).hexdigest(),
        'shutdown_verbatim': shutdown in bridge, 'unload_verbatim': unload in bridge,
        'unload_absent_baseline_noop': unload_marker not in bridge}, indent=2), encoding='utf-8')
    linked = ''.join('<Compile Include="' + (ROOT / rel).as_posix() + '" />' for rel in sources)
    (work / 'Intro.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable>'
        '<ImplicitUsings>enable</ImplicitUsings><TreatWarningsAsErrors>true</TreatWarningsAsErrors>'
        '<NuGetAudit>false</NuGetAudit></PropertyGroup><ItemGroup>' + linked + '</ItemGroup></Project>', encoding='utf-8')
    (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf-8')
    manifest = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in sources}
    manifest['tests/CounterpartCreatorHarness/NativeApiFixtures.cs'] = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    manifest['tests/test_sr2_background_intro.py'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for path in (ROOT / 'tests/SR2BackgroundIntroHarness').glob('*.cs'):
        manifest[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (work / 'sources.json').write_text(json.dumps({'linked_sources': manifest,
        'fixture_extension_only': list(replacements), 'native_fixture_original_unchanged': True,
        'synthetic_only': True, 'native_runtime_verified': False}, indent=2), encoding='utf-8')


class SR2BackgroundIntroTests(unittest.TestCase):
    def test_owned_background_native_intro(self):
        dotnet = os.environ.get('INTRO_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        with tempfile.TemporaryDirectory(prefix='sr2-intro-') as temporary:
            work = Path(os.environ.get('INTRO_ARTIFACTS') or temporary)
            work.mkdir(parents=True, exist_ok=True)
            prepare(work)
            build_command = [dotnet, 'build', str(work / 'Intro.csproj'), '-c', 'Release', '--no-incremental']
            build = subprocess.run(build_command, cwd=ROOT, env=dict(os.environ), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', timeout=180)
            (work / 'build.log').write_text(build.stdout, encoding='utf-8')
            self.assertEqual(build.returncode, 0, 'COMPILATION FAILURE, not behavioral RED: ' + build.stdout)
            command = [dotnet, 'run', '--project', str(work / 'Intro.csproj'), '-c', 'Release', '--no-build', '--',
                os.environ.get('INTRO_MODE', 'all')]
            run = subprocess.run(command, cwd=ROOT, env=dict(os.environ), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', timeout=180)
            (work / 'run.log').write_text(run.stdout, encoding='utf-8')
            (work / 'execution.json').write_text(json.dumps({'build_command': build_command,
                'build_exit_code': build.returncode, 'command': command, 'exit_code': run.returncode,
                'cwd': str(ROOT), 'explicit_env': {key: os.environ.get(key) for key in
                    ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_ROOT', 'DOTNET_CLI_HOME', 'DOTNET_SKIP_FIRST_TIME_EXPERIENCE',
                     'DOTNET_GENERATE_ASPNET_CERTIFICATE', 'DOTNET_CLI_TELEMETRY_OPTOUT', 'PYTHONPATH')},
                'synthetic_only': True, 'native_runtime_verified': False}, indent=2), encoding='utf-8')
            print(run.stdout, end='')
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertIn('intro failures=0', run.stdout)


if __name__ == '__main__':
    unittest.main()
