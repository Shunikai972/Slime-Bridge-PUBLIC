"""Execute production focus integration callbacks with synthetic world/read boundaries."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_sr2_creation_bootstrap import on_update_body

ROOT = Path(__file__).resolve().parents[1]
PREFIX = r'''
using System;
using System.Threading;
namespace UnityEngine { static class Time { public static float realtimeSinceStartup => 1; } }
class SyntheticMod {
    public virtual void OnSceneWasUnloaded(int buildIndex, string sceneName) { }
    public virtual void OnDeinitializeMelon() { }
}
class SyntheticInventory { public void Shutdown() { } public void CancelBackgroundIntro() { } }
class SyntheticLogger { public void Msg(string message) { } }
class World { public string Name = "target"; }
static class CounterpartCreator {
    public static World? Current;
    public static Action? DuringRead;
    public static bool TryReadWorld(out World? world) {
        world = Current;
        var callback = DuringRead; DuringRead = null; callback?.Invoke();
        return world != null;
    }
    public static void ClearWorldReadiness() { Current = null; }
}
class LabFocusDiagnostics {
    public static bool Requested = true;
    public static int Created;
    public bool Started, Stopped;
    public int Samples;
    public static LabFocusDiagnostics? TryCreate(bool lab, bool continued, string? save, Action<string> log) {
        if (!Requested || !lab || !continued || save != "target") return null;
        Created++; return new();
    }
    public void Tick(double now, bool exact) {
        if (Stopped) return;
        if (!exact) { if (Started) Stop("world-scope-lost"); return; }
        Started = true; Samples++;
    }
    public void Stop(string reason) { Stopped = true; }
}
class TravelStub { public void Unload() { } }
class Flow : SyntheticMod {
    public LabFocusDiagnostics? _focusDiagnostics;
    public bool _focusDiagnosticAllowed = true;
    public int _focusEpoch = 0;
    bool AutoloadContinue = true;
    string RequestedSaveGroup = "target";
    bool OracleExecutableMatches() => true;
    SyntheticLogger LoggerInstance = new();
    SyntheticInventory? _inventory = null;
    TravelStub? _travel = null;
    CancellationTokenSource? _stop = null;
    public void ReadFocus()
'''
SUFFIX = r'''
}
class Program {
    static void Require(bool condition, string reason) { if (!condition) throw new Exception(reason); }
    static Flow Fresh() {
        CounterpartCreator.Current = null; CounterpartCreator.DuringRead = null;
        LabFocusDiagnostics.Created = 0; return new();
    }
    static void Main() {
        var flow = Fresh(); flow.ReadFocus();
        Require(flow._focusDiagnostics == null && LabFocusDiagnostics.Created == 0,
            "pending explicit world load must not construct a scene-bound sampler");
        flow.OnSceneWasUnloaded(0, "boot-menu");
        CounterpartCreator.Current = new World { Name = "other" }; flow.ReadFocus();
        Require(flow._focusDiagnostics == null, "wrong loaded world must not construct sampler");
        CounterpartCreator.Current = new World(); flow.ReadFocus();
        Require(LabFocusDiagnostics.Created == 1 && flow._focusDiagnostics?.Samples == 1,
            "first exact world after boot/menu unload must construct and sample");
        var captured = flow._focusDiagnostics!;
        flow.OnSceneWasUnloaded(1, "world"); flow.ReadFocus();
        Require(captured.Stopped && captured.Samples == 1 && LabFocusDiagnostics.Created == 1,
            "unload must prevent restart even if detached world read still matches");
        var unstarted = Fresh(); unstarted._focusDiagnostics = new();
        unstarted.OnSceneWasUnloaded(1, "world"); unstarted.ReadFocus();
        Require(unstarted._focusDiagnostics.Stopped && !unstarted._focusDiagnostics.Started,
            "unstarted published instance must also be cancelled");
        var cancelled = Fresh(); CounterpartCreator.Current = new World();
        CounterpartCreator.DuringRead = () => cancelled.OnSceneWasUnloaded(1, "world");
        cancelled.ReadFocus();
        Require(cancelled._focusDiagnostics == null && LabFocusDiagnostics.Created == 0,
            "scene change during native world read must not publish a new instance");
        var deinitialized = Fresh(); deinitialized.OnDeinitializeMelon();
        CounterpartCreator.Current = new World(); deinitialized.ReadFocus();
        Require(deinitialized._focusDiagnostics == null && LabFocusDiagnostics.Created == 0,
            "deinitialize before first sample must revoke pending diagnostic eligibility");
        var scope = Fresh(); CounterpartCreator.Current = new World(); scope.ReadFocus();
        CounterpartCreator.Current = null; scope.ReadFocus();
        Require(scope._focusDiagnostics!.Stopped, "loss of exact world must stop the active sampler");
        Console.WriteLine("8/8 production focus lifecycle checks passed (synthetic; no game/User32/input/CREATE)");
    }
}
'''


class LabFocusLifecycleTests(unittest.TestCase):
    def test_exact_world_construction_and_cancellation(self):
        text = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
        body = on_update_body(text, 'private void TickFocusDiagnostics()')
        body += '\npublic override void OnSceneWasUnloaded(int buildIndex, string sceneName)\n'
        body += on_update_body(text, 'public override void OnSceneWasUnloaded(')
        body += '\npublic override void OnDeinitializeMelon()\n'
        body += on_update_body(text, 'public override void OnDeinitializeMelon()')
        dotnet_root = os.environ.get('DOTNET_ROOT')
        dotnet = str(Path(dotnet_root) / 'dotnet.exe') if dotnet_root else shutil.which('dotnet')
        self.assertIsNotNone(dotnet, 'dotnet SDK required')
        with tempfile.TemporaryDirectory(prefix='focus-lifecycle-', dir=os.environ.get('TMPDIR')) as temporary:
            work = Path(temporary)
            (work / 'Program.cs').write_text(PREFIX + body + SUFFIX, encoding='utf-8')
            (work / 'lifecycle.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>'
                '<Nullable>enable</Nullable><UseAppHost>false</UseAppHost><NuGetAudit>false</NuGetAudit>'
                '</PropertyGroup></Project>', encoding='utf-8')
            (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf-8')
            environment = dict(os.environ, TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                DOTNET_CLI_HOME=temporary, DOTNET_GENERATE_ASPNET_CERTIFICATE='0',
                DOTNET_CLI_TELEMETRY_OPTOUT='1')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'lifecycle.csproj'),
                '-c', 'Release', '--disable-build-servers', '-p:UseSharedCompilation=false'],
                cwd=work, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=90, text=True, encoding='utf-8', errors='replace')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('8/8 production focus lifecycle checks passed', result.stdout)

    def test_initialization_only_records_eligibility(self):
        text = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
        body = on_update_body(text, 'public override void OnInitializeMelon()')
        self.assertNotIn('LabFocusDiagnostics.TryCreate(', body)
        self.assertIn('LabFocusDiagnostics.Eligible(', body)


if __name__ == '__main__':
    unittest.main()
