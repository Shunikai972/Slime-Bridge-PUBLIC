"""Synthetic execution of the actual SR2 OnUpdate body; no Unity/game/runtime proof."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def on_update_body(text, signature='public override void OnUpdate()'):
    marker = text.index(signature)
    start = text.index('{', marker)
    tokens = re.finditer(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\r\n]*|/\*[\s\S]*?\*/|[{}]', text[start:])
    depth = 0
    for token in tokens:
        if token.group() == '{':
            depth += 1
        elif token.group() == '}':
            depth -= 1
            if depth == 0:
                return text[start:start + token.end()]
    raise AssertionError('unterminated production OnUpdate')


PREFIX = r'''
using System;
using System.Collections.Generic;
using System.Threading;
class SyntheticMod { public virtual void OnUpdate() { } public virtual void OnSceneWasUnloaded(int buildIndex, string sceneName) { } }
class NativeRegistryCollector {
    public static bool Requested;
    public void Tick(Action<string> log) { }
}
class CounterpartCapabilityProbe {
    public static bool Requested;
    public void Tick() { }
}
class LabFocusDiagnostics {
    public static bool Requested;
    public bool Started = false, Stopped;
    public void Stop(string reason) { Stopped = true; }
}
class Sr2SpeciesRegistrar {
    public static bool Requested;
    public bool Done = true;
    public void Tick(Action<string> log) { }
}
class Sr2SanctuaryProbe { public void Tick(Action<string> log) { } }
class TravelStub { public void Unload() { } public void Tick() { } public void Gui() { } }
class Sr2LabImportedOps {
    public static bool Requested;
    public bool Done = true;
    public void Tick(Action<string> log) { }
}
class Sr2MeshDump {
    public static bool Requested;
    public static void Tick(bool labExecutable) { }
}
class SyntheticInventory {
    readonly Flow owner;
    public SyntheticInventory(Flow owner) { this.owner = owner; }
    public void Tick() { owner.Events.Add("inventory"); }
    public void CancelBackgroundIntro() { }
}
class SyntheticLogger { public void Msg(string message) { } }
class SyntheticStorage {
    readonly Flow owner;
    public SyntheticStorage(Flow owner) { this.owner = owner; }
    public void Tick() { owner.Events.Add("storage-binding"); }
}
class GameStatus { }
class StampedStatus {
    public StampedStatus(GameStatus status, DateTime sampled) { }
}
class Flow : SyntheticMod {
    public bool LabCreationAllowed, AutoloadContinue, ThrowBoot;
    public List<string> Events = new();
    public NativeRegistryCollector? _registryCollector = new();
    public CounterpartCapabilityProbe? _capabilityProbe = new();
    public LabFocusDiagnostics? _focusDiagnostics = new();
    public Sr2SpeciesRegistrar? _speciesRegistrar = new();
    public Sr2LabImportedOps? _speciesOps = new();
    public Sr2SanctuaryProbe? _worldProbe = null;
    public TravelStub? _travel = null;
    public TravelStub? _returnPortal = null;
    public bool _focusDiagnosticAllowed = true;
    public int _focusEpoch = 0;
    void TickFocusDiagnostics() { Events.Add("focus-snapshot"); }
    string? _pendingLog = null, _pendingReason = null;
    StampedStatus? _status = null;
    CancellationTokenSource? _stop = null;
    DateTime _nextSnapshot = DateTime.MinValue;
    SyntheticLogger LoggerInstance = new();
    SyntheticInventory _inventory;
    SyntheticStorage? _storageBinding;
    public Flow() { _inventory = new(this); _storageBinding = new(this); }
    void MaybeSkipStartScreen() {
        Events.Add("menu");
        if (ThrowBoot) throw new InvalidOperationException("synthetic boot failure");
    }
    void MaybeAutoload() {
        Events.Add("autoload-check");
        if (AutoloadContinue) { MaybeSkipStartScreen(); Events.Add("world-autoload"); }
    }
    void LogSaveServicesOnce() { }
    void LogDiagnosticsOnce() { }
    GameStatus? SnapshotOnMainThread() => null;
    static bool OracleExecutableMatches() => true;
    public override void OnUpdate()
'''
SUFFIX = r'''
}
class Program {
    static void Require(bool condition, string reason) {
        if (!condition) throw new Exception(reason);
    }
    static Flow Fresh(bool creation = false, bool autoload = false) {
        NativeRegistryCollector.Requested = false;
        CounterpartCapabilityProbe.Requested = false;
        LabFocusDiagnostics.Requested = false;
        Sr2SpeciesRegistrar.Requested = false;
        Sr2LabImportedOps.Requested = false;
        return new Flow { LabCreationAllowed = creation, AutoloadContinue = autoload };
    }
    static void Main() {
        var normal = Fresh(); normal.OnUpdate();
        Require(!normal.Events.Contains("menu") && normal.Events.Contains("inventory"), "normal launch must not bootstrap");
        var creation = Fresh(true); creation.OnUpdate();
        Require(creation.Events.IndexOf("menu") >= 0 && creation.Events.IndexOf("menu") < creation.Events.IndexOf("inventory"), "creation bootstrap must precede inventory");
        Require(!creation.Events.Contains("world-autoload"), "creation menu bootstrap must not load an existing save");
        var continued = Fresh(true, true); continued.OnUpdate();
        Require(continued.Events.FindAll(e => e == "menu").Count == 1 && continued.Events.Contains("world-autoload"), "continue autoload path remains unchanged");
        var diagnostic = Fresh(true); CounterpartCapabilityProbe.Requested = true; diagnostic.OnUpdate();
        Require(diagnostic.Events.Contains("menu") && !diagnostic.Events.Contains("inventory") && !diagnostic.Events.Contains("world-autoload"), "diagnostic remains command-exclusive");
        var registry = Fresh(true); NativeRegistryCollector.Requested = true; registry.OnUpdate();
        Require(registry.Events.Contains("menu") && !registry.Events.Contains("inventory") && !registry.Events.Contains("world-autoload"), "registry capture remains command-exclusive");
        var refusedDiagnostic = Fresh(true); CounterpartCapabilityProbe.Requested = true; refusedDiagnostic._capabilityProbe = null; refusedDiagnostic.OnUpdate();
        Require(refusedDiagnostic.Events.Count == 0, "refused diagnostic cannot fall into creation path");
        var refusedRegistry = Fresh(true); NativeRegistryCollector.Requested = true; refusedRegistry._registryCollector = null; refusedRegistry.OnUpdate();
        Require(refusedRegistry.Events.Count == 0, "refused registry capture cannot fall into creation path");
        var species = Fresh(true); Sr2SpeciesRegistrar.Requested = true; species._speciesRegistrar!.Done = false; species.OnUpdate();
        Require(species.Events.Contains("menu") && !species.Events.Contains("inventory") && !species.Events.Contains("world-autoload"),
                "species registration remains exclusive until done");
        var speciesDone = Fresh(true, true); Sr2SpeciesRegistrar.Requested = true; speciesDone._speciesRegistrar!.Done = true; speciesDone.OnUpdate();
        Require(speciesDone.Events.Contains("world-autoload"), "completed registration falls through to the continue autoload path");
        var refusedSpecies = Fresh(true); Sr2SpeciesRegistrar.Requested = true; refusedSpecies._speciesRegistrar = null; refusedSpecies.OnUpdate();
        Require(refusedSpecies.Events.Count == 0, "refused species registration cannot fall into creation path");
        var speciesOps = Fresh(true); Sr2LabImportedOps.Requested = true; speciesOps._speciesOps!.Done = false; speciesOps.OnUpdate();
        Require(speciesOps.Events.Contains("autoload-check") && !speciesOps.Events.Contains("inventory"),
                "species ops remain exclusive while running");
        var speciesOpsDone = Fresh(true); Sr2LabImportedOps.Requested = true; speciesOpsDone._speciesOps!.Done = true; speciesOpsDone.OnUpdate();
        Require(speciesOpsDone.Events.Contains("inventory"), "completed species ops fall through to the normal tail");
        var refusedSpeciesOps = Fresh(true); Sr2LabImportedOps.Requested = true; refusedSpeciesOps._speciesOps = null; refusedSpeciesOps.OnUpdate();
        Require(refusedSpeciesOps.Events.Count == 0, "refused species ops cannot fall into creation path");
        var bootFailure = Fresh(true); bootFailure.ThrowBoot = true; bootFailure.OnUpdate();
        Require(bootFailure.Events.Contains("menu") && bootFailure.Events.Contains("inventory") && !bootFailure.Events.Contains("world-autoload"), "boot exception is contained; downstream readiness remains authoritative");
        Require(creation.Events.IndexOf("storage-binding") >= 0 &&
                creation.Events.IndexOf("storage-binding") < creation.Events.IndexOf("inventory"),
                "storage binding must be re-read before every inventory/creation tick");
        Require(!diagnostic.Events.Contains("storage-binding") && !registry.Events.Contains("storage-binding"),
                "diagnostic modes cannot observe/authorize the creation storage binding");
        var focus = Fresh(true, true); LabFocusDiagnostics.Requested = true; focus.OnUpdate();
        Require(focus.Events.Contains("focus-snapshot") && focus.Events.Contains("world-autoload") &&
                !focus.Events.Contains("inventory") && !focus.Events.Contains("storage-binding"),
                "focus diagnostic must only load existing save and read focus, even if creation is requested");
        var refusedFocus = Fresh(true, true); LabFocusDiagnostics.Requested = true;
        refusedFocus._focusDiagnostics = null; refusedFocus._focusDiagnosticAllowed = false; refusedFocus.OnUpdate();
        Require(refusedFocus.Events.Count == 0, "refused focus diagnostic cannot load or enter creation path");
        var missingContinue = Fresh(true); LabFocusDiagnostics.Requested = true; missingContinue.OnUpdate();
        Require(missingContinue.Events.Count == 0, "focus diagnostic requires explicit existing-save continue mode");
        Require(focus.Events.FindAll(e => e == "menu").Count == continued.Events.FindAll(e => e == "menu").Count,
                "focus diagnostic reuses the unchanged Continue boot-prompt path (not the intro skipper)");
        var unloaded = Fresh(true, true); LabFocusDiagnostics.Requested = true;
        unloaded.OnSceneWasUnloaded(1, "synthetic");
        Require(unloaded._focusDiagnostics!.Stopped, "unstarted diagnostic must stop on unload");
        Console.WriteLine("21/21 extracted-production-OnUpdate flow checks passed (synthetic; no game or native creation)");
    }
}
'''


class SR2CreationBootstrapTests(unittest.TestCase):
    def test_actual_on_update_creation_menu_and_exclusive_diagnostics(self):
        dotnet = shutil.which('dotnet')
        if not dotnet:
            self.skipTest('dotnet SDK unavailable')
        bridge = (ROOT / 'mods/sr2-bridge/BridgeMod.cs').read_text(encoding='utf-8')
        body = on_update_body(bridge)
        body += '\npublic override void OnSceneWasUnloaded(int buildIndex, string sceneName)\n' + on_update_body(bridge, 'public override void OnSceneWasUnloaded(')
        with tempfile.TemporaryDirectory(prefix='sr2-creation-boot-flow-') as temporary:
            work = Path(temporary)
            (work / 'Program.cs').write_text(PREFIX + body + SUFFIX, encoding='utf-8')
            (work / 'flow.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>'
                '<Nullable>enable</Nullable><UseAppHost>false</UseAppHost><NuGetAudit>false</NuGetAudit>'
                '</PropertyGroup></Project>', encoding='utf-8')
            (work / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding='utf-8')
            env = dict(os.environ, DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1',
                       DOTNET_GENERATE_ASPNET_CERTIFICATE='false', DOTNET_CLI_TELEMETRY_OPTOUT='1')
            result = subprocess.run([dotnet, 'run', '--project', str(work / 'flow.csproj'), '-c', 'Release'],
                cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
                text=True, encoding='utf-8', errors='replace')
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('21/21 extracted-production-OnUpdate flow checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
