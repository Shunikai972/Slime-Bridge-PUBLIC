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


@pytest.mark.parametrize('target', ['SR2', 'BOTH'])
def test_stop_cannot_overtake_an_accepted_online_request(tmp_path, target):
    api, _ = api_for(tmp_path)
    api._travel.online.host('SR2')
    api._launcher.request_stop = mock.Mock(return_value={'realm': 'SR2', 'stopping': True})
    assert api.game_stop(target) == {'ok': False, 'error': 'ONLINE_REQUEST_PENDING'}
    api._launcher.request_stop.assert_not_called()
    api._travel.online.request.update(done=True, cancelled=False)
    assert api.game_stop(target) == {'ok': False, 'error': 'ONLINE_REQUEST_PENDING'}
    api._launcher.request_stop.assert_not_called()
    api._travel.online.request['cancelled'] = True
    assert api.game_stop(target)['ok']


def test_launcher_cannot_join_sr2_on_the_players_save(tmp_path):
    api, _ = api_for(tmp_path)
    api._host.start = mock.Mock()
    api._ensure_travel_loop = mock.Mock()
    api._start_for_online = mock.Mock()
    api._travel.online.join = mock.Mock()
    assert api.mp_join('Rk7mP2xQa') == {'ok': False, 'error': 'SR2_JOIN_IN_GAME'}
    api._host.start.assert_not_called()
    api._ensure_travel_loop.assert_not_called()
    api._start_for_online.assert_not_called()
    api._travel.online.join.assert_not_called()


@pytest.mark.parametrize('op', ['host', 'join'])
def test_sr1_online_does_not_start_the_linked_sr2_save(tmp_path, op):
    api, _ = api_for(tmp_path)
    api._primary = lambda realm: {'game': realm, 'root': str(tmp_path / realm)}
    api._launcher.running = lambda realm: False
    api._host = mock.Mock()
    api._host.realm_env.return_value = {}
    api._ensure_travel_loop = mock.Mock()
    api._keep_in_front = mock.Mock()
    api._session.set('SR1', 'sr1-save')
    api._travel._links.remember('sr1-save', 'sr2-save')
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy', return_value=False), mock.patch.object(api._launcher, 'launch') as launch:
        result = api.mp_host('SR1') if op == 'host' else api.mp_join('K7QX2PA')
    assert result['ok']
    assert [c.args[0]['game'] for c in launch.call_args_list] == ['SR1']
    assert launch.call_args.kwargs['extra_env']['SLIMEBRIDGE_TRAVEL_SAVE'] == 'sr1-save'


def test_invalid_online_name_does_not_launch_games(tmp_path):
    api, _ = api_for(tmp_path)
    api._start_for_online = mock.Mock()
    assert api.mp_host('BOTH', 'invalid name') == {'ok': False, 'error': 'BAD_NAME'}
    assert api.mp_join('K7QX2PA', 'invalid name') == {'ok': False, 'error': 'BAD_NAME'}
    api._start_for_online.assert_not_called()


@pytest.mark.parametrize('realm', ['SR1', 'SR2'])
def test_online_cold_solo_does_not_hide_the_only_game(tmp_path, realm):
    api, _ = api_for(tmp_path)
    for child in api._launcher._children.values():
        child.poll.return_value = 0
    api._primary = mock.Mock(return_value={'game': realm})
    api._launch = mock.Mock(return_value={'ok': True})
    assert api._start_for_online((realm,))['ok']
    assert not api._launch.call_args.kwargs.get('background')


def test_online_does_not_reuse_a_dead_childs_heartbeat(tmp_path):
    api, _ = api_for(tmp_path)
    code = 'Rk7mP2xQa'
    beat = {'pid': 999, 'status': {'player_alive': True, 'save_id': 'old'},
            'online': {'mod': True, 'signed_in': True, 'hosting': True, 'code': code}}
    api._host.state = lambda: {'heartbeats': {'SR2': beat}}
    assert api._travel.online.state()['code'] == ''
    assert not api._travel._world_ready('SR2')
    beat['pid'] = 222
    assert api._travel.online.state()['code'] == code
    assert api._travel._world_ready('SR2')


def test_play_both_refuses_to_replace_a_session_during_verified_close(tmp_path):
    api, _ = api_for(tmp_path)
    api._travel._close['SR2'] = {'to': 'SR2'}
    api._launcher.launch = mock.Mock()
    result = api.launch('BOTH_SR2', confirmed=True)
    assert not result['ok'] and result['errors'] == [{'realm': 'SR2', 'error': 'GAME_CLOSING'}]
    api._launcher.launch.assert_not_called()


def test_both_online_preflights_missing_game_before_launch(tmp_path):
    api, _ = api_for(tmp_path)
    api._primary = lambda realm: None if realm == 'SR1' else {'game': realm, 'root': str(tmp_path / realm)}
    api._launcher.running = lambda realm: False
    api._host = mock.Mock()
    api._host.realm_env.return_value = {}
    api._ensure_travel_loop = mock.Mock()
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy', return_value=False), mock.patch.object(api._launcher, 'launch') as launch:
        result = api.mp_host('BOTH')
    assert (result['ok'], result['error'], result['realm']) == (False, 'GAME_NOT_FOUND', 'SR1')
    assert api._travel.online.request is None
    launch.assert_not_called()


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
    control.focus_startup.assert_not_called()


def test_startup_waits_for_unity_window_not_loader_console(tmp_path):
    api, _ = api_for(tmp_path)
    control = api._window_control()
    control._checked.return_value = [100]
    control._game_windows.side_effect = [windows.WindowError('console only'), [200], [200]]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if seconds == 2.0 and sleeps.count(2.0) == 2:
            control.minimize.assert_not_called()
            control.focus_startup.assert_not_called()

    with mock.patch('apps.launcher.desktop.time.sleep', side_effect=sleep), \
            mock.patch('apps.launcher.desktop.threading.Thread') as thread:
        api._keep_in_front('SR1', api._audio_startup_epoch)
        thread.call_args.kwargs['target']()
    assert control.minimize.call_count == 2
    assert sleeps.count(2.0) >= 2


def test_startup_uses_non_waiting_foreground_request(tmp_path):
    api, _ = api_for(tmp_path)
    control = api._window_control()
    control._game_windows.return_value = [200]
    control.focus.side_effect = AssertionError('synchronous focus under the travel lock')
    with mock.patch('apps.launcher.desktop.time.sleep'), \
            mock.patch('apps.launcher.desktop.threading.Thread') as thread:
        api._keep_in_front('SR1', api._audio_startup_epoch)
        thread.call_args.kwargs['target']()
    assert control.focus_startup.call_args_list == [mock.call(111), mock.call(111)]


def test_startup_window_worker_stands_down_after_stop(tmp_path):
    api, exe = api_for(tmp_path)
    control = api._window_control()
    control._game_windows.return_value = [200]
    with mock.patch('apps.launcher.desktop.time.sleep'), \
            mock.patch('apps.launcher.desktop.threading.Thread') as thread, \
            mock.patch('tools.lab.process_guard.process_image_path', return_value=exe):
        api._keep_in_front('SR1', api._audio_startup_epoch)
        assert api.game_stop('SR2')['ok']
        thread.call_args.kwargs['target']()
    assert api.game_process_state()['SR2'] == {'running': True, 'closing': True}
    control.minimize.assert_not_called()
    control.focus_startup.assert_not_called()


def test_crossing_completed_between_startup_passes_keeps_destination_in_front(tmp_path):
    api, _ = api_for(tmp_path)
    control = api._window_control()
    control._checked.return_value = control._game_windows.return_value = [200]
    coord = api._travel
    coord._install_for = lambda realm: {'game': realm}
    coord._send = mock.Mock(return_value=True)
    coord._settings = lambda: {'travel_carry': 'NONE'}
    coord._heartbeat = lambda realm: {'pid': 111 if realm == 'SR1' else 222,
                                     'status': {'player_alive': True, 'save_id': realm + '-save'}}
    coord._windows = control
    coord._saw = lambda _row, _realm, event: event in ('departure_ready', 'arrived', 'held')
    row = {'id': 'crossing', 'portal_id': 'portal.sr1.sanctuary_gate', 'from': 'SR1', 'to': 'SR2',
           'state': 'PREPARING', 'since': coord._clock(), 'history': [], 'deadline': 60,
           'launched': False, 'dest_save': 'SR2-save'}

    def sleep(seconds):
        if seconds != 6.0 or row['state'] == 'COMPLETE':
            return
        with coord._lock:
            coord.transitions.append(row)
            coord._start_destination(row)
            for _ in range(3):
                coord._advance(row)
        assert row['state'] == 'COMPLETE'

    with mock.patch('apps.launcher.desktop.time.sleep', side_effect=sleep), \
            mock.patch('apps.launcher.desktop.threading.Thread') as thread:
        api._keep_in_front('SR1', api._audio_startup_epoch)
        thread.call_args.kwargs['target']()
    assert row['state'] == 'COMPLETE'
    assert control.minimize.call_args_list == [mock.call(222)]   # the game left behind is not minimized (no desktop flash)
    assert control.focus_startup.call_args_list == [mock.call(111)]
    assert control.focus.call_args_list == [mock.call(222)]
    assert api._audio_startup_deadline == 0


@pytest.mark.parametrize('last,partner',[('SR1','SR2'),('SR2','SR1')])
def test_continue_starts_both_games_on_the_linked_pair_and_keeps_the_session(tmp_path,last,partner):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._host=mock.Mock()
    api._host.realm_env.return_value={}
    api._ensure_travel_loop=mock.Mock()
    api._keep_in_front=mock.Mock()
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)}
    for child in api._launcher._children.values():
        child.poll.return_value=0                                   # nothing runs yet
    api._travel._links.remember('ranch-sr1','ranch-sr2')
    api._session.set(last,'ranch-'+last.lower())
    started=[]
    def start(installation,extra_env=None):
        started.append((installation['game'],dict(extra_env or {})))
        return {'realm':installation['game'],'pid':1}
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False),mock.patch.object(api._launcher,'launch',side_effect=start):
        result=api.launch(last,confirmed=True,resume=True)
    assert result['ok'] and result['front']==last
    envs=dict(started)
    assert [g for g,_ in started]==[last,partner], 'the game the player was in first, its partner behind'
    assert (envs[last]['SLIMEBRIDGE_TRAVEL_MODE'],envs[last]['SLIMEBRIDGE_TRAVEL_SAVE'])==('RESUME','ranch-'+last.lower())
    assert (envs[partner]['SLIMEBRIDGE_TRAVEL_MODE'],envs[partner]['SLIMEBRIDGE_TRAVEL_SAVE'])==('DESTINATION','ranch-'+partner.lower())
    assert api._session.get()['realm']==last and api._session.get()['save']=='ranch-'+last.lower(), 'Continue still points at the front game'
    assert (api._travel._front,api._travel._parked)==(last,partner)


def test_continue_without_a_partner_starts_only_that_game(tmp_path):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._host=mock.Mock()
    api._host.realm_env.return_value={}
    api._ensure_travel_loop=mock.Mock()
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)}
    for child in api._launcher._children.values():
        child.poll.return_value=0
    api._session.set('SR1','solo-ranch')
    started=[]
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False),mock.patch.object(api._launcher,'launch',side_effect=lambda i,extra_env=None:started.append(i['game']) or {'pid':1}):
        assert api.launch('SR1',confirmed=True,resume=True)['ok']
    assert started==['SR1']


def _launch_fixture(tmp_path, running=(), beats=None, plugin_state='missing'):
    api,exe=api_for(tmp_path)
    api._audio=mock.Mock()
    api._host=mock.Mock()
    api._host.realm_env.return_value={}
    api._ensure_travel_loop=mock.Mock()
    api._keep_in_front=mock.Mock()
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)}
    for realm,child in api._launcher._children.items():
        child.poll.return_value=None if realm in running else 0
    api._travel._heartbeat=lambda realm:(beats or {}).get(realm,{})
    started=[]
    def start(installation,extra_env=None):
        started.append((installation['game'],dict(extra_env or {})))
        api._launcher._children[installation['game']].poll.return_value=None
        return {'realm':installation['game'],'pid':1}
    patches=[mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False),
             mock.patch('apps.launcher.desktop.plugins.status',return_value={'state':plugin_state}),
             mock.patch('apps.launcher.desktop.pack.prepare',return_value={'ok':True,'steps':[],'warnings':[]}),
             mock.patch.object(api._launcher,'launch',side_effect=start)]
    return api,started,patches


@pytest.mark.parametrize('front,back',[('SR1','SR2'),('SR2','SR1')])
def test_play_both_opens_the_session_pair_and_the_back_game_waits_on_its_save(tmp_path,front,back):
    api,started,patches=_launch_fixture(tmp_path)
    api._travel._links.remember('ranch-sr1','ranch-sr2')
    api._session.set('SR1','ranch-sr1')
    with patches[0],patches[1],patches[2],patches[3]:
        assert api.launch('BOTH_'+front,confirmed=True)['ok']
    envs=dict(started)
    assert [g for g,_ in started]==[front,back]
    assert (envs[front]['SLIMEBRIDGE_TRAVEL_MODE'],envs[front]['SLIMEBRIDGE_TRAVEL_SAVE'])==('RESUME','ranch-'+front.lower())
    assert (envs[back]['SLIMEBRIDGE_TRAVEL_MODE'],envs[back]['SLIMEBRIDGE_TRAVEL_SAVE'])==('DESTINATION','ranch-'+back.lower())
    assert api._travel._waiting[back]['save']=='ranch-'+back.lower()
    assert (api._travel._front,api._travel._parked)==(front,back)


def test_online_starts_the_missing_game_behind_the_one_being_played(tmp_path):
    beats={'SR1':{'pid':111,'status':{'player_alive':True,'save_id':'ranch-sr1'}}}
    api,started,patches=_launch_fixture(tmp_path,running=('SR1',),beats=beats)
    api._travel._links.remember('ranch-sr1','ranch-sr2')
    with patches[0],patches[1],patches[2],patches[3]:
        assert api._start_for_online(('SR1','SR2'))['ok']
    (game,env),=started
    assert (game,env['SLIMEBRIDGE_TRAVEL_MODE'],env['SLIMEBRIDGE_TRAVEL_SAVE'])==('SR2','DESTINATION','ranch-sr2')
    assert (api._travel._front,api._travel._parked)==('SR1','SR2')


def test_without_a_partner_the_started_game_makes_a_new_save_for_the_ranch(tmp_path):
    beats={'SR1':{'pid':111,'status':{'player_alive':True,'save_id':'solo'}}}
    api,started,patches=_launch_fixture(tmp_path,running=('SR1',),beats=beats)
    with patches[0],patches[1],patches[2],patches[3]:
        assert api._start_for_online(('SR1','SR2'))['ok']
    (game,env),=started
    assert (game,env['SLIMEBRIDGE_TRAVEL_MODE'],env['SLIMEBRIDGE_TRAVEL_NEW'])==('SR2','DESTINATION','SlimeBridge solo')
    assert api._travel._waiting['SR2']['save'] is None


@pytest.mark.parametrize('state,armed',[('current',True),('outdated',True),('missing',False)])
def test_play_arms_the_extracted_content_where_the_plugin_is_installed(tmp_path,state,armed):
    api,started,patches=_launch_fixture(tmp_path,plugin_state=state)
    with patches[0],patches[1],patches[2] as prepare,patches[3]:
        assert api.launch('SR1',confirmed=True)['ok']
    assert prepare.called is armed


def test_the_typed_online_name_reaches_slime_rancher(tmp_path):
    api,started,patches=_launch_fixture(tmp_path)
    api._remember_online_name('Noam_42')
    assert api.mp_host('SR1', 'bad name') == {'ok': False, 'error': 'BAD_NAME'}
    with patches[0],patches[1],patches[2],patches[3]:
        assert api.launch('BOTH_SR1',confirmed=True)['ok']
    envs=dict(started)
    assert envs['SR1']['SLIMEBRIDGE_ONLINE_NAME']=='Noam_42' and 'SLIMEBRIDGE_ONLINE_NAME' not in envs['SR2']


def test_starting_the_other_game_in_front_brings_the_vac_of_the_game_played_last(tmp_path):
    api,started,patches=_launch_fixture(tmp_path)
    api._travel._links.remember('ranch-sr1','ranch-sr2')
    api._session.set('SR1','ranch-sr1')                          # SR1 was played last
    with patches[0],patches[1],patches[2],patches[3]:
        assert api.launch('BOTH_SR2',confirmed=True)['ok']
    assert api._travel._pending_sync['from']=='SR1' and api._travel._pending_sync['to']=='SR2'


def test_starting_the_game_played_last_in_front_needs_no_sync(tmp_path):
    api,started,patches=_launch_fixture(tmp_path)
    api._travel._links.remember('ranch-sr1','ranch-sr2')
    api._session.set('SR1','ranch-sr1')
    with patches[0],patches[1],patches[2],patches[3]:
        assert api.launch('BOTH_SR1',confirmed=True)['ok']
    assert api._travel._pending_sync is None
