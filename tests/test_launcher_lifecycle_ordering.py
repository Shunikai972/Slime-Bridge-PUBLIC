"""Production lifecycle ordering with inert windows/children, no games or COM calls."""
from types import SimpleNamespace
from unittest import mock
import pytest
from apps.launcher.desktop import LauncherAPI
from apps.launcher import windows


def api_for(tmp_path):
    with mock.patch.object(LauncherAPI, '_window_control', return_value=windows.NullWindows()):
        api=LauncherAPI(config_path=tmp_path/'config.json', known_paths=(), lab_games=tmp_path/'games')
    exe=tmp_path/'owned-fixture.exe'
    exe.write_bytes(b'fixture')
    for realm,pid in [('SR1',111),('SR2',222)]:
        child=mock.Mock(pid=pid)
        child.poll.return_value=None
        api._launcher._children[realm]=child
        api._launcher._executables[realm]=exe.resolve()
    control=mock.Mock()
    control.request_close.return_value=True
    api._window_control=lambda:control
    return api,exe


@pytest.mark.parametrize('closing',['SR1','SR2'])
def test_already_queued_portal_cannot_begin_after_stop_intent(tmp_path,closing):
    api,exe=api_for(tmp_path)
    request={'request_id':'queued-before-stop','portal_id':'portal.sr1.sanctuary_gate','realm':'SR1','save_id':'ranch'}
    coord=api._travel
    coord._settings=lambda:{'travel_save_mode':'CONTINUE'}
    coord._install_for=lambda realm:{'game':realm}
    coord._select_audio_front=mock.Mock()
    coord._host.state=lambda:{'state':'RUNNING'}
    coord._note_session=lambda:None
    def call(method,path,body=None):
        if path=='/v1/portal/requests':return {'requests':[request]}
        return {'ok':True,'events':[]}
    coord._call=mock.Mock(side_effect=call)
    with mock.patch('tools.lab.process_guard.process_image_path',return_value=exe):
        assert api.game_stop(closing)['ok']
    rows=coord.poll()
    assert rows[-1]['state']=='FAILED' and rows[-1]['code']=='GAME_CLOSING'
    assert not any(c.args[2].get('message',{}).get('kind')=='prepare_departure'
                   for c in coord._call.call_args_list if len(c.args)>2 and isinstance(c.args[2],dict))
    coord._select_audio_front.assert_not_called()


def test_closing_state_clears_after_real_exit_and_after_new_child(tmp_path):
    api,exe=api_for(tmp_path)
    with mock.patch('tools.lab.process_guard.process_image_path',return_value=exe):api.game_stop('SR2')
    assert api._launcher.closing('SR2')
    api._launcher._children['SR2'].poll.return_value=0
    assert not api._launcher.closing('SR2')
    assert 'SR2' not in api._launcher._closing


def test_one_audio_watcher_survives_no_games_then_relaunch_and_late_session(tmp_path):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._sync_game_audio=mock.Mock()
    api._launcher.running=mock.Mock(return_value=False)
    threads=[]
    class InertThread:
        def __init__(self,target,**kwargs):self.target=target;self.alive=False;threads.append(self)
        def start(self):self.alive=True
        def is_alive(self):return self.alive
        def run(self):
            try:self.target()
            finally:self.alive=False
    ticks=[]
    def wait(_delay):
        ticks.append(len(ticks)+1)
        if len(ticks)==2:
            assert not api._audio.restore.called, 'idle must not retire a watcher that can be reused'
            api._launcher.running.return_value=True
            api._game_started('SR2',222)
        return len(ticks)>=4
    api._audio_stop=mock.Mock()
    api._audio_stop.wait.side_effect=wait
    with mock.patch('apps.launcher.desktop.threading.Thread',InertThread):
        api._game_started('SR1',111)
        threads[0].run()
    assert len(ticks)==4 and len(threads)==1
    api._sync_game_audio.assert_any_call(follow_foreground=True)
    api._audio.restore.assert_called_once()  # only on actual stop/shutdown, after all ticks


def test_pending_close_checked_again_before_any_departure_command(tmp_path):
    api,exe=api_for(tmp_path)
    coord=api._travel
    coord._install_for=lambda realm:{'game':realm}
    coord._send=mock.Mock()
    transition={'id':'fixture','from':'SR1','to':'SR2','history':[],'portal_id':'portal.sr1.sanctuary_gate'}
    with mock.patch('tools.lab.process_guard.process_image_path',return_value=exe):api.game_stop('SR2')
    coord._start_destination(transition)
    assert transition['state']=='FAILED' and transition['code']=='GAME_CLOSING'
    assert all(c.args[1]!='prepare_departure' for c in coord._send.call_args_list)


def test_retained_watcher_mutes_session_appearing_only_after_relaunch_gap(tmp_path):
    from contextlib import contextmanager
    from apps.launcher.audio import GameAudio
    api,exe=api_for(tmp_path)
    sessions=[]
    @contextmanager
    def enumerate_sessions():
        yield list(sessions)
    api._launcher._children.clear()
    api._audio=GameAudio(lambda pid:pid==222 and api._launcher.running('SR2'), sessions=enumerate_sessions)
    api._audio_front='SR1'
    created=[]
    class InertThread:
        def __init__(self,target,**kwargs):self.target=target;self.alive=False;created.append(self)
        def start(self):self.alive=True
        def is_alive(self):return self.alive
    observations=[]
    state=SimpleNamespace(mute=False)
    volume=mock.Mock()
    volume.GetMute.side_effect=lambda:state.mute
    volume.SetMute.side_effect=lambda value,_:setattr(state,'mute',value)
    session=SimpleNamespace(ProcessId=222,InstanceIdentifier='late-relaunch-session',SimpleAudioVolume=volume)
    ticks=[]
    def wait(_delay):
        ticks.append(1)
        if len(ticks)==2:
            child=mock.Mock(pid=222)
            child.poll.return_value=None
            api._launcher._children['SR2']=child
            api._game_started('SR2',222)
        if len(ticks)==3:sessions.append(session)
        if len(ticks)==4:observations.append(state.mute)
        return len(ticks)>=4
    api._audio_stop=mock.Mock()
    api._audio_stop.wait.side_effect=wait
    with mock.patch('apps.launcher.desktop.threading.Thread',InertThread):
        api._game_started('SR1',111)
        created[0].target()
    assert len(created)==1 and observations==[True]
    assert state.mute is False, 'finally restores the original mute only when the watcher really stops'


@pytest.mark.parametrize('front,back',[('SR1','SR2'),('SR2','SR1')])
def test_background_boot_focus_cannot_override_selected_audio_before_window_arrangement(tmp_path,front,back):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._host=mock.Mock()
    api._host.realm_env.return_value={}
    api._ensure_travel_loop=mock.Mock()
    api._keep_in_front=mock.Mock()
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)}
    foreground=api._launcher._children[back].pid
    api._travel._windows=mock.Mock()
    api._travel._windows.foreground_pid.return_value=foreground
    def start(installation,extra_env=None):
        # Unity's newly created background window temporarily becomes foreground before parking.
        if installation['game']==back:api._sync_game_audio(follow_foreground=True)
        return {'realm':installation['game'],'pid':api._launcher._children[installation['game']].pid}
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False),mock.patch.object(api._launcher,'launch',side_effect=start):
        result=api.launch('BOTH_'+front,confirmed=True)
    assert result['ok']
    assert api._audio_front==front, 'background window creation is not an intentional manual return'
    assert mock.call(foreground,True) in api._audio.set_muted.call_args_list


def test_startup_window_arrangement_releases_pin_for_later_manual_return(tmp_path):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._audio_front='SR1'
    api._audio_startup_deadline=float('inf')
    control=mock.Mock()
    control._checked.return_value=[1]
    control.foreground_pid.return_value=222
    api._window_control=lambda:control
    api._travel._windows=control
    class InlineThread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):self.target()
    with mock.patch('apps.launcher.desktop.threading.Thread',InlineThread),mock.patch('apps.launcher.desktop.time.sleep'):
        api._keep_in_front('SR1', api._audio_startup_epoch)
    assert api._audio_startup_deadline==0
    api._sync_game_audio(follow_foreground=True)
    assert api._audio_front=='SR2'


def test_older_window_arrangement_cannot_release_newer_startup_pin(tmp_path):
    api,exe=api_for(tmp_path)
    api._audio_startup_deadline=123.0
    control=mock.Mock()
    control._checked.return_value=[1]
    def supersede(_pid):
        api._audio_startup_epoch+=1
        api._audio_startup_deadline=456.0
    control.minimize.side_effect=supersede
    api._window_control=lambda:control
    class InlineThread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):self.target()
    with mock.patch('apps.launcher.desktop.threading.Thread',InlineThread),mock.patch('apps.launcher.desktop.time.sleep'):
        api._keep_in_front('SR1', api._audio_startup_epoch)
    assert api._audio_startup_deadline==456.0
    control.focus.assert_not_called()
