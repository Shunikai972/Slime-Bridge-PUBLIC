"""Early audio policy integration; synthetic children only, no Windows session/device writes."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import tempfile

from apps.launcher.desktop import LauncherAPI
from apps.launcher.runtime import GameLauncher
from apps.launcher import travel
from test_launcher_travel import CoordinatorTests, cross, plugin_reply


def test_game_launcher_notifies_after_owned_child_image_has_been_pinned(tmp_path):
    exe = tmp_path / 'SlimeRancher2.exe'
    exe.write_bytes(b'fixture')
    child = mock.Mock(pid=123)
    child.poll.return_value = None
    seen = []
    launcher = GameLauncher(popen=lambda *a, **k: child,
                            on_started=lambda realm, pid: seen.append((realm, pid, launcher.owns_image(pid, exe))))
    launcher.launch({'game': 'SR2', 'root': str(tmp_path)})
    assert seen == [('SR2', 123, True)]
    launcher.launch({'game': 'SR2', 'root': str(tmp_path)})
    assert len(seen) == 1


def test_portal_keeps_source_audio_until_focus_is_confirmed_then_hands_over_and_rolls_back():
    case = CoordinatorTests('test_warm_crossing_both_games_running')
    case.setUp()
    try:
        case.links.remember('sr1-save', 'sr2-save')
        case.proc.alive('SR1', 'sr1-save', 111)
        case.proc.alive('SR2', 'sr2-save', 222)
        case.launcher._running |= {'SR1', 'SR2'}
        selected = []
        case.coord._select_audio_front = selected.append
        cross(case.host)
        case.coord.poll()
        assert selected == ['SR1']
        plugin_reply(case.host, 'SR1', 'departure_ready')
        case.coord.poll()
        assert selected[-1] == 'SR2'
        case.coord._rollback(case.coord.transitions[-1], 'fixture-failure')
        assert selected[-1] == 'SR1'
    finally:
        case.doCleanups()


def test_launcher_front_policy_uses_live_children_not_plugin_heartbeats(tmp_path):
    api = LauncherAPI(config_path=tmp_path/'config.json', known_paths=(), lab_games=tmp_path/'games')
    api._audio = mock.Mock()
    api._launcher._children = {'SR1': mock.Mock(pid=11), 'SR2': mock.Mock(pid=22)}
    for child in api._launcher._children.values():
        child.poll.return_value = None
    api._select_audio_front('SR1')
    assert mock.call(11, False) in api._audio.set_muted.call_args_list
    assert mock.call(22, True) in api._audio.set_muted.call_args_list
    api._audio.sync.assert_called_once()


def test_audio_follows_manual_foreground_unity_window_but_not_during_transfer(tmp_path):
    api = LauncherAPI(config_path=tmp_path/'settings.json', known_paths=(), lab_games=tmp_path/'games')
    api._audio = mock.Mock()
    api._launcher._children = {'SR1': mock.Mock(pid=11), 'SR2': mock.Mock(pid=22)}
    for child in api._launcher._children.values():
        child.poll.return_value = None
    api._travel._windows = mock.Mock()
    api._travel._windows.foreground_pid.return_value = 22
    api._audio_front = 'SR1'
    api._sync_game_audio(follow_foreground=True)
    assert api._audio_front == 'SR2'
    api._audio_front = 'SR1'
    api._travel.transitions = [{'state':'PREPARING'}]
    api._sync_game_audio(follow_foreground=True)
    assert api._audio_front == 'SR1'
