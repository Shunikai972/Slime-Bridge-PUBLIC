"""Synthetic proof-boundary regressions; no host, game, profile or registry access."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from tools.lab import oracle

ROOT = Path(__file__).resolve().parents[1]


def source_function(name, symbol):
    tree = ast.parse((ROOT / 'tools/lab' / name).read_text(encoding='utf-8-sig'))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == symbol)


def load(fn, env):
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<exact-proof-function>', 'exec'), env)
    return env[fn.name]


def test_return_drive_does_not_accept_retained_outbound_transition(tmp_path):
    old = {'id': 'outbound', 'state': 'COMPLETE', 'from': 'SR1', 'to': 'SR2', 'history': []}
    timeline = [{'label': 'out', 'id': 'outbound', 'state': 'COMPLETE'}]
    clock = iter([0.0, 0.0, 2.0])
    drive = load(source_function('travel_oracle.py', 'drive'), {
        'time': NS(monotonic=lambda: next(clock), time=lambda: 0.0, sleep=lambda _: None),
        'grab': lambda *a: 'synthetic', 'beats': lambda *a: {}})
    assert drive(None, NS(poll=lambda: [old]), tmp_path, 'back', 1.0, timeline) is None


def test_implant_realm_must_be_in_guarded_scope_before_any_side_effect(tmp_path):
    # Preflight must reject before even checking game paths, backup providers or source files.
    with pytest.raises(ValueError, match='guard'):
        oracle.run(['SR1'], 0, False, tmp_path, current_user=True, creation_realms=(),
                   guard_realms=['SR1'], implant=[{'source': str(tmp_path / 'never-read'), 'realm': 'SR2',
                                                  'rel': 'synthetic.sav', 'sha256': '0' * 64}])


@pytest.mark.parametrize('initial', [b'started a new native game\n', 'ancien é🙂 started a new native game\n'.encode()])
def test_fresh_log_never_returns_unchanged_previous_success(tmp_path, initial):
    log = tmp_path / 'synthetic.log'
    log.write_bytes(initial)
    run = source_function('sr2_newgame_probe.py', 'run')
    fresh = next(n for n in ast.walk(run) if isinstance(n, ast.FunctionDef) and n.name == 'fresh')
    fn = load(fresh, {'log': log, 'start': len(initial), 'started_at': log.stat().st_mtime + 1,
                      'initial_log': initial, 'started_at_ns': log.stat().st_mtime_ns + 1})
    assert fn() == ''
    with log.open('ab') as stream:
        stream.write(b'new appended line\n')
    assert fn() == 'new appended line\n'


def test_oracle_sanitizes_inherited_mutation_modes_but_keeps_explicit_run_env(tmp_path, monkeypatch):
    exe = tmp_path / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'synthetic inert marker')
    host = mock.MagicMock(url='http://127.0.0.1:1')
    host.__enter__.return_value = host
    host.realm_token.return_value = 'synthetic-realm-credential'
    child = mock.Mock(pid=123)
    child.poll.return_value = None
    modes = ['SLIMEBRIDGE_TRAVEL_MODE', 'SLIMEBRIDGE_TRAVEL_NEW', 'SLIMEBRIDGE_SANCTUARY_NEWGAME',
             'SLIMEBRIDGE_SANCTUARY_OPS', 'SLIMEBRIDGE_LAB_REGISTRY_OUT', 'SLIMEBRIDGE_DEV_OPS',
             'SLIMEBRIDGE_IMPORT_OPS']
    for key in modes:
        monkeypatch.setenv(key, 'inherited-must-not-reach-game')
    monkeypatch.setenv('SYNTHETIC_ORDINARY_ENV', 'preserved')
    monkeypatch.setattr(oracle, 'LAB', exe.parents[1])
    monkeypatch.setattr(oracle, 'GAMES', {'SR1': exe})
    monkeypatch.setattr(oracle, 'require_isolated_profile', lambda *a: None)
    monkeypatch.setattr(oracle, 'BridgeHost', lambda **kw: host)
    monkeypatch.setattr(oracle, 'process_image_path', lambda _: exe)
    monkeypatch.setattr(oracle, '_stop_owned', lambda _: {})
    with mock.patch.object(oracle.subprocess, 'Popen', return_value=child) as launch:
        oracle.run(['SR1'], 0, False, tmp_path / 'evidence',
                   extra_env={'SR1': {'SLIMEBRIDGE_LAB_FOCUS_DIAGNOSTICS': '1'}})
    env = launch.call_args.kwargs['env']
    assert all(key not in env for key in modes)
    assert env['SLIMEBRIDGE_LAB_FOCUS_DIAGNOSTICS'] == '1'
    assert env['SLIMEBRIDGE_TOKEN'] == 'synthetic-realm-credential'
    assert env['SYNTHETIC_ORDINARY_ENV'] == 'preserved'
