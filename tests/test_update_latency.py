"""Bounded updater IO and single-owner UI requests; synthetic archives only."""
import hashlib
import io
import json
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest
from apps.launcher import updater, desktop
from test_launcher_updater import _zip_bytes, _Resp


@pytest.fixture(autouse=True)
def windows_archive_fixture(monkeypatch):
    monkeypatch.setattr(updater, 'WINDOWS', True)


def test_archive_download_is_chunked_and_never_reread_whole_for_hash(tmp_path):
    data = _zip_bytes('9.9.9')
    name = updater.asset_name('9.9.9')
    release = {'tag_name':'v9.9.9','assets':[
        {'name':name,'browser_download_url':'https://fixture.invalid/archive','size':len(data)},
        {'name':'SHA256SUMS.txt','browser_download_url':'https://fixture.invalid/sums'}]}
    reads = []
    class Archive(_Resp):
        def read(self, n=-1):
            reads.append(n)
            assert 0 < n <= 1024*1024, 'Archive read must be bounded'
            return super().read(n)
    def opener(req, timeout=0):
        if req.full_url == updater.API:
            return _Resp(json.dumps(release).encode())
        if req.full_url.endswith('/sums'):
            return _Resp((hashlib.sha256(data).hexdigest()+'  '+name+'\n').encode())
        return Archive(data)
    progress = []
    with mock.patch.object(Path,'read_bytes',side_effect=AssertionError('Whole-file hash reread forbidden')):
        ready = updater.prepare(opener,cache=tmp_path,progress=progress.append)
    assert (Path(ready['folder'])/'VERSION').read_text() == '9.9.9'
    assert reads and progress
    assert any(p['phase']=='download' and p['done']==len(data) for p in progress)
    assert progress[-1]['phase']=='ready'


def test_source_install_is_refused_before_download(tmp_path):
    api = desktop.LauncherAPI(config_path=tmp_path/'config.json')
    with mock.patch.object(sys,'frozen',False,create=True), mock.patch.object(updater,'prepare') as prepare:
        assert api.update_apply()['error']=='SOURCE_INSTALL'
    prepare.assert_not_called()


def test_second_update_cannot_start_while_first_download_is_active(tmp_path):
    api = desktop.LauncherAPI(config_path=tmp_path/'config.json')
    entered, release = threading.Event(), threading.Event()
    results=[]
    def slow_prepare(*args,**kwargs):
        entered.set()
        assert release.wait(5)
        raise updater.UpdateError('OFFLINE')
    with mock.patch.object(sys,'frozen',True,create=True), mock.patch.object(updater,'prepare',side_effect=slow_prepare) as prepare:
        thread=threading.Thread(target=lambda:results.append(api.update_apply()))
        thread.start()
        try:
            assert entered.wait(2)
            second=api.update_apply()
        finally:
            release.set()
            thread.join(6)
        assert not thread.is_alive()
        assert second['error']=='UPDATE_BUSY'
        assert prepare.call_count==1
        assert results[0]['error']=='OFFLINE'


def test_game_started_during_download_blocks_install_and_preserves_ready_files(tmp_path):
    api=desktop.LauncherAPI(config_path=tmp_path/'config.json')
    running={'active':False}
    def prepare(*args,**kwargs):
        running['active']=True
        return {'folder':str(tmp_path),'version':'9.9.9'}
    with mock.patch.object(sys,'frozen',True,create=True), \
         mock.patch.object(api._launcher,'running',side_effect=lambda _:running['active']), \
         mock.patch.object(updater,'prepare',side_effect=prepare), mock.patch.object(updater,'restart_into') as restart, \
         mock.patch.object(desktop.threading,'Thread'):
        assert api.update_apply()['error']=='GAME_RUNNING'
    restart.assert_not_called()


@pytest.mark.parametrize('header,limit,error', [('999', 32, 'DOWNLOAD_TOO_LARGE'),
                                              ('999999', 1024*1024, 'DOWNLOAD_FAILED'),
                                              ('', 32, 'DOWNLOAD_TOO_LARGE')])
def test_stream_limits_and_truncated_bodies_fail_before_unpack(tmp_path, header, limit, error):
    data=_zip_bytes('9.9.9')
    name=updater.asset_name('9.9.9')
    class Archive(_Resp):
        headers={'Content-Length':header}
    def opener(request,timeout=0):
        if request.full_url==updater.API:
            return _Resp(json.dumps({'tag_name':'v9.9.9','assets':[
                {'name':name,'browser_download_url':'https://fixture.invalid/archive'},
                {'name':'SHA256SUMS.txt','browser_download_url':'https://fixture.invalid/sums'}]}).encode())
        if request.full_url.endswith('/sums'):
            return _Resp((hashlib.sha256(data).hexdigest()+'  '+name+'\n').encode())
        return Archive(data)
    with mock.patch.object(updater,'MAX_ARCHIVE',limit), pytest.raises(updater.UpdateError) as failure:
        updater.prepare(opener,cache=tmp_path)
    assert failure.value.code==error
    assert not list(tmp_path.rglob('new'))


def test_copy_helper_is_parallel_non_destructive_and_wont_start_after_copy_error(tmp_path):
    with mock.patch.object(sys,'frozen',True,create=True), \
         mock.patch.object(updater,'install_dir',return_value=tmp_path/'target'):
        helper=updater.restart_into(tmp_path/'prepared',pid=424242,popen=mock.Mock())
    script=Path(helper['helper']).read_text()
    assert '/E /IS /IT' in script
    assert '/IM' in script  # NTFS change-time-only files otherwise remain stale despite /IS /IT.
    assert 'tasklist.exe' in script and 'PING.EXE' in script
    assert '/MIR' not in script and '/PURGE' not in script
    assert script.index('if errorlevel 8 exit /b 8') < script.index('start ""')


def test_restart_can_be_acknowledged_without_a_fixed_presentation_sleep(tmp_path):
    api=desktop.LauncherAPI(config_path=tmp_path/'config.json')
    assert api.update_restart_ack()=={'ok':True}
    assert api._update_restart_ack.is_set()


def test_success_remains_latched_and_shutdown_waits_for_response_ack_or_bounded_fallback(tmp_path):
    api=desktop.LauncherAPI(config_path=tmp_path/'config.json')
    targets=[]
    api._update_restart_ack=mock.Mock()
    def thread(*,target,**kwargs):
        targets.append(target)
        return mock.Mock()
    with mock.patch.object(sys,'frozen',True,create=True), \
         mock.patch.object(updater,'prepare',return_value={'folder':str(tmp_path),'version':'9.9.9'}) as prepare, \
         mock.patch.object(updater,'restart_into',return_value={}), \
         mock.patch.object(desktop.threading,'Thread',side_effect=thread), \
         mock.patch.object(api,'shutdown') as shutdown, mock.patch.object(desktop.os,'_exit') as exit_:
        assert api.update_apply()['ok']
        assert api.update_apply()['error']=='UPDATE_BUSY'
        assert prepare.call_count==1
        shutdown.assert_not_called()
        api.update_restart_ack()
        targets[0]()
        api._update_restart_ack.wait.assert_called_once_with(timeout=.5)
        shutdown.assert_called_once_with()
        exit_.assert_called_once_with(0)
