"""Production-linked gameplay animation lifecycle and native-hook contract harness."""
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOTNET = Path(os.environ.get("DOTNET_ROOT", str(Path.home() / ".dotnet"))) / "dotnet.exe"
HARNESS = ROOT / "tests/ImportedGameplayAnimationHarness"
SCRATCH_FALLBACK = Path(str(Path(tempfile.gettempdir()) / "slimebridge-scratch/ringtail-gameplay-v1"))


def run_dotnet(arguments):
    scratch = Path(os.environ.get("TMPDIR") or SCRATCH_FALLBACK)
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gameplay-animation-", dir=scratch) as temporary:
        temp = Path(temporary)
        environment = dict(os.environ)
        environment.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
                           DOTNET_CLI_HOME=str(temp / "dotnet-cli-home"), DOTNET_ROOT=str(DOTNET.parent),
                           DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_SKIP_FIRST_TIME_EXPERIENCE="1",
                           DOTNET_NOLOGO="1")
        result = subprocess.run([str(DOTNET), *arguments, "--disable-build-servers",
                                 "-p:BaseIntermediateOutputPath=" + str(temp / "obj") + os.sep,
                                 "-p:MSBuildProjectExtensionsPath=" + str(temp / "obj") + os.sep,
                                 "-p:BaseOutputPath=" + str(temp / "bin") + os.sep,
                                 "-p:UseSharedCompilation=false"], cwd=ROOT, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
        evidence_dir = os.environ.get("SLIMEBRIDGE_PRIVATE_EVIDENCE_DIR")
        if evidence_dir:
            target = Path(evidence_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / ("harness-" + os.environ.get("SLIMEBRIDGE_EVIDENCE_LABEL", "run") + ".log")).write_text(
                result.stdout, encoding="utf-8")
        return result


def test_production_gameplay_driver_contract():
    result = run_dotnet(["run", "--project", str(HARNESS / "ImportedGameplayAnimationHarness.csproj"), "-c", "Release"])
    assert result.returncode == 0, result.stdout
    assert "gameplay animation checks=" in result.stdout
    assert "failures=0; production-linked harness" in result.stdout
