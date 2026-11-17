"""Owned, graceful game close only: never a process-name kill or a console close."""
from pathlib import Path
from unittest import mock
from apps.launcher.desktop import LauncherAPI
from apps.launcher.runtime import GameLauncher, RuntimeErrorWithCode


def running(tmp_path, realm='SR1'):
    exe=tmp_path/('SlimeRancher.exe' if realm=='SR1' else 'SlimeRancher2.exe')
    exe.write_bytes(b'fixture')
    child=mock.Mock(pid=11 if realm=='SR1' else 22)
    child.poll.return_value=None
    launcher=GameLauncher(popen=lambda *a,**k:child)
    launcher.launch({'game':realm,'root':str(tmp_path)})
    return launcher,child,exe


def test_stop_posts_close_to_verified_owned_child_without_killing_and_is_idempotent(tmp_path):
    launcher,child,exe=running(tmp_path)
    windows=mock.Mock()
    windows.request_close.return_value=True
    first=launcher.request_stop('SR1',windows,lambda pid:exe)
    again=launcher.request_stop('SR1',windows,lambda pid:exe)
    assert first['stopping'] and again['stopping']
    windows.request_close.assert_called_once_with(11)
    child.terminate.assert_not_called()
    child.kill.assert_not_called()
    assert launcher.running('SR1')


def test_stop_rejects_reused_or_wrong_image_before_window_request(tmp_path):
    launcher,child,exe=running(tmp_path)
    windows=mock.Mock()
    try:
        launcher.request_stop('SR1',windows,lambda pid:tmp_path/'foreign.exe')
        assert False,'expected ownership rejection'
    except RuntimeErrorWithCode as exc:
        assert exc.code=='PROCESS_NOT_OWNED'
    windows.request_close.assert_not_called()


def test_failed_close_is_reported_not_claimed_stopped(tmp_path):
    launcher,child,exe=running(tmp_path)
    windows=mock.Mock()
    windows.request_close.return_value=False
    try:
        launcher.request_stop('SR1',windows,lambda pid:exe)
        assert False,'expected failed close'
    except RuntimeErrorWithCode as exc:
        assert exc.code=='CLOSE_NOT_CONFIRMED'
    assert launcher.running('SR1')
    child.kill.assert_not_called()


def test_backend_blocks_close_during_inventory_transfer(tmp_path):
    api=LauncherAPI(config_path=tmp_path/'settings.json',known_paths=(),lab_games=tmp_path/'games')
    api._travel.transitions=[{'state':'SWITCHING'}]
    api._launcher=mock.Mock()
    assert api.game_stop('BOTH')=={'ok':False,'error':'TRAVEL_ACTIVE'}
    api._launcher.request_stop.assert_not_called()


def test_both_stop_is_scoped_and_reports_each_result(tmp_path):
    api=LauncherAPI(config_path=tmp_path/'settings.json',known_paths=(),lab_games=tmp_path/'games')
    api._launcher=mock.Mock()
    api._launcher.request_stop.side_effect=lambda realm,*args:{'realm':realm,'stopping':realm=='SR1','already_stopped':realm=='SR2'}
    api._window_control=lambda:mock.sentinel.windows
    result=api.game_stop('BOTH')
    assert result['ok']
    assert [r['realm'] for r in result['games']]==['SR1','SR2']
    assert api.game_stop('foreign')['error']=='BAD_TARGET'


def test_same_pid_new_child_still_receives_close_after_relaunch(tmp_path):
    launcher,child,exe=running(tmp_path)
    windows=mock.Mock()
    windows.request_close.return_value=True
    launcher.request_stop('SR1',windows,lambda pid:exe)
    child.poll.return_value=0
    replacement=mock.Mock(pid=child.pid)
    replacement.poll.return_value=None
    launcher._popen=lambda *args,**kwargs:replacement
    launcher.launch({'game':'SR1','root':str(tmp_path)})
    launcher.request_stop('SR1',windows,lambda pid:exe)
    assert windows.request_close.call_count==2
