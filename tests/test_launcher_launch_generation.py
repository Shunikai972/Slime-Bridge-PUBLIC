"""Overlapping launch completion order with actual methods and inert child/window providers."""
from unittest import mock
import pytest
from test_launcher_lifecycle_ordering import api_for


def ready(tmp_path):
    api,_=api_for(tmp_path)
    api._audio=mock.Mock()
    api._host=mock.Mock()
    api._host.realm_env.return_value={}
    api._ensure_travel_loop=mock.Mock()
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)}
    api._launcher.launch=mock.Mock(side_effect=lambda install,**kwargs:{'realm':install['game'],'pid':api._launcher._children[install['game']].pid})
    return api


@pytest.mark.parametrize('older_target',['SR1','BOTH_SR1'])
def test_superseded_launch_completion_cannot_clear_newer_audio_pin_or_focus_stale_game(tmp_path,older_target):
    api=ready(tmp_path)
    queued=[]
    class InertThread:
        def __init__(self,target,**kwargs):self.target=target
        def start(self):queued.append(self)
    entered=[]
    def host_start():
        if entered:return
        entered.append(True)
        assert api.launch('BOTH_SR2',confirmed=True,resume=True)['ok']
    api._host.start.side_effect=host_start
    control=mock.Mock()
    control._checked.return_value=[1]
    control.foreground_pid.return_value=111
    api._window_control=lambda:control
    api._travel._windows=control
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False),mock.patch('apps.launcher.desktop.threading.Thread',InertThread),mock.patch('apps.launcher.desktop.time.sleep'):
        api.launch(older_target,confirmed=True,resume=older_target=='BOTH_SR1')
        # The newer BOTH_SR2 arrangement is deliberately still pending.
        for stale in queued[1:]:stale.target()
        api._sync_game_audio(follow_foreground=True)
    assert api._audio_startup_deadline>0
    assert api._audio_front=='SR2'
    control.focus.assert_not_called()
    assert len(queued)==1, 'superseded launch must not enqueue its window work as the new generation'


def test_superseded_partial_completion_cannot_clear_newer_audio_pin(tmp_path):
    api=ready(tmp_path)
    api._keep_in_front=mock.Mock()
    nested=[False]
    def host_start():
        if nested[0]:return
        nested[0]=True
        assert api.launch('BOTH_SR2',confirmed=True,resume=True)['ok']
        api._primary=lambda realm:{'game':'SR1','root':str(tmp_path/'SR1')} if realm=='SR1' else None
    api._host.start.side_effect=host_start
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False):
        older=api.launch('BOTH_SR1',confirmed=True,resume=True)
    assert not older['ok']
    assert api._audio_startup_deadline>0 and api._audio_front=='SR2'
    assert api._keep_in_front.call_count==1


@pytest.mark.parametrize('target',['SR1','BOTH_SR1'])
def test_unconfirmed_launch_never_touches_a_current_generation(tmp_path,target):
    api=ready(tmp_path)
    api._audio_startup_epoch=7
    api._audio_startup_deadline=float('inf')
    assert api.launch(target)=={'ok':False,'needs_confirmation':True}
    assert api._audio_startup_epoch==7 and api._audio_startup_deadline==float('inf')


def test_current_partial_completion_releases_its_own_pin(tmp_path):
    api=ready(tmp_path)
    api._primary=lambda realm:{'game':realm,'root':str(tmp_path/realm)} if realm=='SR1' else None
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy',return_value=False):
        result=api.launch('BOTH_SR1',confirmed=True,resume=True)
    assert not result['ok'] and len(result['launched'])==1
    assert api._audio_startup_deadline==0
