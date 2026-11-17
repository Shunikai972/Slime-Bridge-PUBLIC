"""Production-linked scheduling regressions and cold-start concurrency; never touches a game."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import mock

from apps.launcher import desktop
from test_launcher_travel import CoordinatorTests, cross

ROOT = Path(__file__).resolve().parents[1]


def test_departure_is_dispatched_before_slow_cold_preparation():
    case = CoordinatorTests('test_warm_crossing_both_games_running')
    case.setUp()
    try:
        case.links.remember('sr1-save', 'sr2-save')
        case.proc.alive('SR1', 'sr1-save', 111)
        case.launcher._running.add('SR1')
        seen = []
        def prepare(installation):
            message = case.host.travel_peek('SR1')
            seen.append(message['kind'] if message else None)
            return {'ok': True}
        case.coord._prepare_destination = prepare
        cross(case.host)
        case.coord.poll()
        assert seen == ['prepare_departure']
        assert len(case.launcher.started) == 1
    finally:
        case.doCleanups()


def test_launcher_poll_does_not_add_three_hundred_ms_between_every_step():
    assert 0 < desktop.TRAVEL_TICK <= .05


def test_portal_animations_have_a_short_explicit_budget():
    for realm, filename in [('sr1-bridge', 'Sr1Travel.cs'), ('sr2-bridge', 'Sr2Travel.cs')]:
        source = (ROOT/'mods'/realm/filename).read_text()
        assert '0.18f' in source.split('case "prepare_departure":',1)[1].split('case "hold":',1)[0]
        assert '_overlay.Open(0.22f' in source


def test_production_transport_and_agent_scheduling_contracts():
    executable = 'dotnet.exe' if os.name == 'nt' else 'dotnet'
    sdk_root = os.environ.get('DOTNET_ROOT')
    sdk = Path(sdk_root)/executable if sdk_root else Path(shutil.which('dotnet') or Path.home()/'.dotnet'/executable)
    assert sdk.is_file()
    scratch = ROOT/'Lab/T'
    scratch.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='travel-latency-',dir=scratch) as temp:
        temp = Path(temp)
        env = dict(os.environ, DOTNET_ROOT=str(sdk.parent), DOTNET_ADD_GLOBAL_TOOLS_TO_PATH='false',
                   DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_NOLOGO='1')
        # SDK service directories remain inherited; only ordinary temporary files are short-lived.
        for key in ('TEMP','TMP','TMPDIR'):
            env[key] = str(temp)
        project = ROOT/'tests/TravelLatencyHarness/TravelLatencyHarness.csproj'
        output = temp/'out'
        build = subprocess.run([str(sdk),'build',str(project),'-o',str(output),'-v','q','--disable-build-servers',
                                '-p:UseSharedCompilation=false'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
        assert build.returncode == 0, build.stdout+build.stderr
        run = subprocess.run([str(sdk),str(output/'TravelLatencyHarness.dll')],cwd=ROOT,env=env,
                             capture_output=True,text=True,timeout=15)
        assert run.returncode == 0, run.stdout+run.stderr
        assert 'CHECKS=10 FAILURES=0' in run.stdout
