"""Intro harness orchestration contracts; synthetic boundaries are not C# gameplay results."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import test_sr2_background_intro as intro


def test_intro_runs_built_assembly_directly_with_bounded_durable_workload_budget(monkeypatch, tmp_path):
    work = tmp_path / 'captured'
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        # This test exercises orchestration only. The real 971-check harness runs separately.
        return subprocess.CompletedProcess(command, 0, 'synthetic runner boundary: intro failures=0\n')

    monkeypatch.setenv('INTRO_DOTNET', 'synthetic-dotnet')
    monkeypatch.setenv('INTRO_ARTIFACTS', str(work))
    monkeypatch.setenv('INTRO_MODE', 'all')
    monkeypatch.setattr(intro.subprocess, 'run', fake_run)
    intro.SR2BackgroundIntroTests('test_owned_background_native_intro').test_owned_background_native_intro()
    assert calls[1][0] == ['synthetic-dotnet', str(work / 'bin/Release/net10.0/Intro.dll'), 'all']
    assert calls[1][1]['timeout'] == 600
    assert json.loads((work / 'run-execution.json').read_text())['timed_out'] is False


def test_intro_runner_retains_partial_output_and_reraises_timeout(monkeypatch, tmp_path):
    assert callable(getattr(intro, 'run_intro', None)), 'Direct managed-runtime runner is not implemented'
    command = ['synthetic-dotnet', str(tmp_path / 'Intro.dll'), 'all']

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs['timeout'], output=b'owned child began\n')

    monkeypatch.setattr(intro.subprocess, 'run', timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        intro.run_intro(command, tmp_path, timeout=0.1)
    assert (tmp_path / 'run.log').read_text() == 'owned child began\n'
    result = json.loads((tmp_path / 'run-execution.json').read_text())
    assert result['timed_out'] is True
    assert result['exit_code'] is None
    assert result['command'] == command


def test_intro_runner_preserves_nonzero_exit_and_diagnostics(monkeypatch, tmp_path):
    command = ['synthetic-dotnet', str(tmp_path / 'Intro.dll'), 'all']
    monkeypatch.setattr(intro.subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(command, 23, 'synthetic intentional assertion failure\n'))
    result = intro.run_intro(command, tmp_path)
    assert result.returncode == 23
    assert 'intentional assertion failure' in (tmp_path / 'run.log').read_text()
    recorded = json.loads((tmp_path / 'run-execution.json').read_text())
    assert recorded['exit_code'] == 23
    assert recorded['timed_out'] is False


def test_intro_timeout_reaps_direct_managed_runtime_and_releases_its_files(tmp_path):
    assert callable(getattr(intro, 'run_intro', None)), 'Direct managed-runtime runner is not implemented'
    dotnet = os.environ.get('INTRO_DOTNET') or shutil.which('dotnet')
    assert dotnet, 'dotnet SDK required, like the production-linked intro harness'
    project = tmp_path / 'managed'
    project.mkdir()
    (project / 'Intro.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
        '<OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework>'
        '<NuGetAudit>false</NuGetAudit></PropertyGroup></Project>')
    (project / 'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>')
    (project / 'Program.cs').write_text('using System; using System.IO; using System.Threading; '
        'class Program { static void Main(string[] args) { '
        'using var held = new FileStream(args[0], FileMode.Create, FileAccess.Write, FileShare.None); '
        'Console.WriteLine("owned-managed-child-ready"); Console.Out.Flush(); '
        'Thread.Sleep(Timeout.Infinite); } }')
    build = subprocess.run([dotnet, 'build', str(project / 'Intro.csproj'), '-c', 'Release',
        '--disable-build-servers', '-p:UseSharedCompilation=false', '-v', 'q'],
        capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stdout + build.stderr
    assembly = project / 'bin/Release/net10.0/Intro.dll'
    lock = project / 'owned.lock'
    command = [dotnet, str(assembly), str(lock)]
    with pytest.raises(subprocess.TimeoutExpired):
        intro.run_intro(command, project, timeout=2)
    assert 'owned-managed-child-ready' in (project / 'run.log').read_text()
    # FileShare.None and the loaded DLL must both be released before temporary cleanup on Windows.
    assert lock.exists()
    lock.unlink()
    moved = assembly.with_name('Intro.released.dll')
    assembly.rename(moved)
    assert moved.is_file()
    assert json.loads((project / 'run-execution.json').read_text())['timed_out'] is True
