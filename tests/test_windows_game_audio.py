"""Windows-session policy using inert sessions, not the master endpoint or real games."""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import mock


def session(pid, key, muted=False):
    volume = mock.Mock()
    volume.GetMute.return_value = muted
    def set_mute(value, _):
        volume.GetMute.return_value = value
    volume.SetMute.side_effect = set_mute
    return SimpleNamespace(ProcessId=pid, InstanceIdentifier=key, SimpleAudioVolume=volume)


def policy(rows, owned):
    from apps.launcher.audio import GameAudio
    @contextmanager
    def sessions():
        yield list(rows)
    return GameAudio(lambda pid: pid in owned, sessions=sessions)


def test_mutes_only_verified_game_session_and_preserves_existing_user_mute():
    rows = [session(11, 'game'), session(22, 'other'), session(0, 'system')]
    audio = policy(rows, {11})
    audio.set_muted(11, True)
    audio.sync()
    rows[0].SimpleAudioVolume.SetMute.assert_called_once_with(True, None)
    rows[1].SimpleAudioVolume.SetMute.assert_not_called()
    rows[2].SimpleAudioVolume.SetMute.assert_not_called()
    audio.set_muted(11, False)
    audio.sync()
    assert rows[0].SimpleAudioVolume.GetMute() is False
    rows[0].SimpleAudioVolume.GetMute.return_value = True
    audio.set_muted(11, True)
    audio.sync()
    audio.restore()
    assert rows[0].SimpleAudioVolume.GetMute() is True


def test_session_appears_after_launch_before_first_heartbeat_and_new_sessions_get_muted():
    rows = []
    audio = policy(rows, {11})
    audio.set_muted(11, True)
    audio.sync()
    rows.append(session(11, 'late'))
    audio.sync()
    assert rows[0].SimpleAudioVolume.GetMute() is True
    rows.append(session(11, 'new-output'))
    audio.sync()
    assert all(row.SimpleAudioVolume.GetMute() for row in rows)
    audio.restore()
    assert all(not row.SimpleAudioVolume.GetMute() for row in rows)


def test_pid_reuse_or_foreign_image_is_not_touched_even_during_restore():
    rows = [session(11, 'game')]
    owned = {11}
    audio = policy(rows, owned)
    audio.set_muted(11, True)
    audio.sync()
    owned.clear()
    rows[0].SimpleAudioVolume.SetMute.reset_mock()
    audio.restore()
    rows[0].SimpleAudioVolume.SetMute.assert_not_called()


def test_one_disappearing_session_does_not_skip_other_owned_sessions():
    rows = [session(11, 'gone'), session(11, 'live')]
    rows[0].SimpleAudioVolume.GetMute.side_effect = OSError('session disappeared')
    audio = policy(rows, {11})
    audio.set_muted(11, True)
    result = audio.sync()
    assert rows[1].SimpleAudioVolume.GetMute() is True
    assert result['errors'] and result['muted'] == 1


def test_restore_failure_keeps_original_for_retry():
    row = session(11, 'game')
    audio = policy([row], {11})
    audio.set_muted(11, True)
    audio.sync()
    row.SimpleAudioVolume.SetMute.side_effect = OSError('busy')
    assert audio.restore()['errors']
    row.SimpleAudioVolume.SetMute.side_effect = lambda value, _: setattr(row.SimpleAudioVolume.GetMute, 'return_value', value)
    assert not audio.restore()['errors']
    assert row.SimpleAudioVolume.GetMute() is False
