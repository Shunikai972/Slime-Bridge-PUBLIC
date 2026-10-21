"""Whole-source BridgeDiagnostics contract, not Unity/game runtime proof."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DOTNET_ROOT = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / ".dotnet")))
SCRATCH = Path(os.environ.get("TMPDIR", str(Path(tempfile.gettempdir()) / "slimebridge-scratch")))


def test_native_inventory_pose_and_driver_diagnostic_contract():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gameplay-diagnostics-", dir=SCRATCH) as temporary:
        env = dict(os.environ)
        env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary,
                   DOTNET_CLI_HOME=temporary, DOTNET_ROOT=str(DOTNET_ROOT),
                   DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
                   DOTNET_NOLOGO="1")
        junction = Path(temporary) / "diagnostic-junction"
        target = Path(temporary) / "diagnostic-junction-target"
        target.mkdir()
        try:
            created = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                                     cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            assert created.returncode == 0, created.stdout
            assert junction.is_junction() and junction.resolve() == target.resolve()
            env.update(DIAGNOSTICS_JUNCTION_ROOT=str(junction), DIAGNOSTICS_JUNCTION_TARGET=str(target))
            result = subprocess.run(
                [str(DOTNET_ROOT / "dotnet.exe"), "run", "--project",
                 str(Path(__file__).with_name("DiagnosticsHarness.csproj")), "-c", "Release",
                 "--disable-build-servers", "-p:UseSharedCompilation=false",
                 "-p:BaseIntermediateOutputPath=" + str(Path(temporary) / "obj") + os.sep,
                 "-p:MSBuildProjectExtensionsPath=" + str(Path(temporary) / "obj") + os.sep,
                 "-p:OutputPath=" + str(Path(temporary) / "bin") + os.sep],
                cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        finally:
            if junction.is_junction():
                junction.rmdir()  # Remove link before recursive sandbox cleanup; never traverse target.
        evidence = os.environ.get("SLIMEBRIDGE_PRIVATE_EVIDENCE_DIR")
        if evidence:
            private = Path(evidence)
            private.mkdir(parents=True, exist_ok=True)
            (private / "diagnostics-harness-final.log").write_text(result.stdout, encoding="utf-8")
        assert result.returncode == 0, result.stdout
        assert "failures=0; linked BridgeDiagnostics, synthetic native read-only boundary" in result.stdout


def test_readonly_driver_snapshot_contract():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="driver-diagnostics-", dir=SCRATCH) as temporary:
        env = dict(os.environ)
        env.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary, DOTNET_CLI_HOME=temporary,
                   DOTNET_ROOT=str(DOTNET_ROOT), DOTNET_CLI_TELEMETRY_OPTOUT="1",
                   DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1", DOTNET_NOLOGO="1")
        result = subprocess.run(
            [str(DOTNET_ROOT / "dotnet.exe"), "run", "--project",
             str(Path(__file__).with_name("GameplayDiagnosticsHarness.csproj")), "-c", "Release",
             "--disable-build-servers", "-p:UseSharedCompilation=false",
             "-p:BaseIntermediateOutputPath=" + str(Path(temporary) / "obj") + os.sep,
             "-p:MSBuildProjectExtensionsPath=" + str(Path(temporary) / "obj") + os.sep,
             "-p:OutputPath=" + str(Path(temporary) / "bin") + os.sep],
            cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        evidence = os.environ.get("SLIMEBRIDGE_PRIVATE_EVIDENCE_DIR")
        if evidence:
            private = Path(evidence)
            private.mkdir(parents=True, exist_ok=True)
            (private / "driver-diagnostics-harness-final.log").write_text(result.stdout, encoding="utf-8")
        assert result.returncode == 0, result.stdout
        assert "failures=0; production-linked read-only driver snapshot" in result.stdout
