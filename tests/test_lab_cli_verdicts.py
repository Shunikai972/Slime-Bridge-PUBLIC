"""Actual probe functions, inert process/registry/profile providers only."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.lab import launcher_travel_probe, oracle, screen_guard, sr1_ops_probe


@pytest.mark.parametrize('probe,args', [
    (sr1_ops_probe, ['--sr1', 'synthetic', '--ops', '["vac"]']),
    (launcher_travel_probe, ['--sr1', 'synthetic', '--sr2', 'synthetic']),
])
@pytest.mark.parametrize('passed,cleanup,expected', [(False, True, 1), (True, False, 1), (True, True, 0), (1, True, 1)])
def test_cli_propagates_only_literal_final_success(monkeypatch, probe, args, passed, cleanup, expected):
    monkeypatch.setattr(probe, 'run', lambda *a: {'passed': passed, 'cleanup_completed': cleanup})
    assert probe.main([*args, '--current-user-authorized']) == expected
    tree = ast.parse(Path(probe.__file__).read_text(encoding='utf-8'))
    assert isinstance(tree.body[-1].body[0], ast.Raise)


@pytest.mark.parametrize('code,ops,log,expected', [
    (7, ['vac'], '', False),
    (None, ['vac'], '', False),
    (None, ['vac'], '[SlimeBridge] dev op vac -> vac: unavailable', False),
    (None, ['vac'], '[SlimeBridge] dev op vac -> usage: sanctuary', False),
    (None, ['give:PINK_PLORT:2'], '[SlimeBridge] dev op give:PINK_PLORT:2 -> nothing fitted or unknown id PINK_PLORT', False),
    (None, ['give:PINK_PLORT:2'], '[SlimeBridge] dev op give:PINK_PLORT:2 -> gave PINK_PLORT x1', False),
    (None, ['vac', 'vac'], '[SlimeBridge] dev op vac -> vac: 0:PINK_PLORT x2', False),
    (None, ['vac', 'give:PINK_PLORT:2'], '[SlimeBridge] dev op give:PINK_PLORT:2 -> gave PINK_PLORT x2\n[SlimeBridge] dev op vac -> vac: 0:PINK_PLORT x2', False),
    (None, ['wait:25'], '', False),
    (None, ['wait:25', 'give:PINK_PLORT:2', 'vac'], '[SlimeBridge] dev op give:PINK_PLORT:2 -> gave PINK_PLORT x2\n[SlimeBridge] dev op vac -> vac: 0:PINK_PLORT x2', True),
    (7, ['vac'], '[SlimeBridge] dev op vac -> vac: 0:PINK_PLORT x2', False),
])
def test_sr1_ops_requires_owned_live_child_and_ordered_success_readback(monkeypatch, tmp_path, code, ops, log, expected):
    probe = sr1_ops_probe
    exe = tmp_path / 'Lab/Games/SR1/SlimeRancher.exe'
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b'synthetic not executable')
    player_log = tmp_path / 'synthetic-player.log'
    child = SimpleNamespace(pid=123, poll=lambda: code)
    def spawn(*a, **kw):
        player_log.write_text(log, encoding='utf-8')
        return child
    monkeypatch.setattr(probe, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(probe, 'validate_lab_destination', lambda *a: exe)
    monkeypatch.setattr(probe, 'assert_no_other_game_processes', lambda: None)
    monkeypatch.setattr(probe.sanctuary_oracle, 'SR1_LOG', player_log)
    monkeypatch.setattr(probe.save_guard, 'snapshot', lambda *a: tmp_path / 'synthetic-backup')
    monkeypatch.setattr(screen_guard, 'snapshot', lambda *a: {})
    monkeypatch.setattr(screen_guard, 'restore', lambda *a: {})
    monkeypatch.setattr(oracle, 'snapshot_plugins', lambda *a: {})
    monkeypatch.setattr(oracle, 'restore_plugins', lambda *a: {})
    stop = Mock(return_value={})
    monkeypatch.setattr(oracle, '_stop_owned', stop)
    monkeypatch.setattr(oracle, '_finalize_current_user', lambda result, *a: result.update(saves_match_backup_after_restore=True, sources_untouched=True))
    monkeypatch.setattr(probe.plugins, 'install', lambda *a: {})
    monkeypatch.setattr(probe.subprocess, 'Popen', spawn)
    times = iter([0.0, 2.0])
    monkeypatch.setattr(probe, 'time', SimpleNamespace(monotonic=lambda: next(times), sleep=lambda _: None))
    result = probe.run(exe.parent, ops, 1, [])
    assert result['passed'] is expected
    assert result['cleanup_completed'] is True
    stop.assert_called_once_with({'SR1': child}, {'SR1': exe})
    saved = list((tmp_path / 'Lab/RuntimeArtifacts').glob('*/sr1-ops.json'))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())['passed'] is expected
