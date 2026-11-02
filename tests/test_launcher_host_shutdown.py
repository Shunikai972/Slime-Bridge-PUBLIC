"""Graceful shutdown must authenticate as administrator and finish the owned Python process."""
import json
import subprocess
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

import pytest

from apps.launcher import runtime
from bridge.host.server import BridgeHost


def post(host, token, payload=None):
    request = urllib.request.Request(host.url+'/v1/shutdown', data=json.dumps({} if payload is None else payload).encode(),
                                     headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
    try:
        with urllib.request.urlopen(request,timeout=3) as response:
            return response.status,json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code,json.loads(error.read())


def test_shutdown_is_admin_only_and_signals_without_stopping_the_handler_thread():
    with BridgeHost() as host:
        for token in ('wrong',host.realm_token('SR1'),host.realm_token('SR2')):
            assert post(host,token)[0]==401
        assert post(host,host.token)[0]==200
        assert host.wait_for_shutdown(1)


def test_shutdown_rejects_nonempty_or_nonobject_payloads():
    with BridgeHost() as host:
        for payload in ([1],{'force':True},True):
            assert post(host,host.token,payload)[0]==400
        assert not host.wait_for_shutdown(0)


@pytest.mark.parametrize('iteration', range(5))
def test_real_owned_host_exits_cleanly_before_cleanup(tmp_path, iteration):
    host=runtime.HostProcess(tmp_path/'host')
    try:
        assert host.start()['state']=='RUNNING'
        child=host._child
        assert host.stop()['stopped']
        assert child.returncode==0, 'Host was forcibly killed instead of completing its own cleanup'
        assert not host.token_file.exists()
        assert not host.status_file.exists()
        assert not list(host.root.glob('.slimebridge-status-*.tmp'))
    finally:
        host.stop()


@pytest.mark.parametrize('url', ['https://example.invalid', 'http://localhost:1', 'http://127.0.0.1@other.invalid:1',
                               'http://127.0.0.1:1/elsewhere', 'http://127.0.0.1:70000', 'http://127.0.0.1:1?x=y'])
def test_shutdown_never_sends_the_admin_credential_outside_its_literal_loopback_endpoint(url):
    with patch.object(runtime.http.client, 'HTTPConnection') as connect:
        assert not runtime.HostProcess._request_shutdown({'url':url}, 'fixture-token')
    connect.assert_not_called()


def test_shutdown_does_not_follow_a_redirect_or_accept_an_invalid_reply():
    for status, body in ((302,b'{"ok":true}'),(200,b'[]'),(200,b'not json')):
        connection = Mock()
        response = connection.getresponse.return_value
        response.status = status
        response.read.return_value = body
        with patch.object(runtime.http.client, 'HTTPConnection', return_value=connection) as connect:
            assert not runtime.HostProcess._request_shutdown({'url':'http://127.0.0.1:1'}, 'fixture-token')
        assert connect.call_count==1
        assert connection.request.call_count==1
        connection.close.assert_called_once()


def test_stop_reaps_the_owned_child_after_a_forced_kill(tmp_path):
    host = runtime.HostProcess(tmp_path/'host')
    child = Mock()
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired('fixture',5),0]
    host._child = child
    assert host.stop()['stopped']
    child.terminate.assert_called_once()
    child.kill.assert_called_once()
    assert child.wait.call_count==2
