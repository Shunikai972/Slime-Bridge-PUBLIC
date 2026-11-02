from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.launcher import travel, windows


@pytest.mark.parametrize('realm', ['SR1', 'SR2'])
def test_cold_portal_prepares_destination_before_starting_it(tmp_path, realm):
    events = []
    prepared = {'value': False}
    host = SimpleNamespace(state=lambda: {'state': 'STOPPED'}, realm_env=lambda r: {})
    def launch(installation, extra_env=None):
        assert prepared['value'], 'Portal launch bypassed destination plugin preparation'
        events.append('launch')
    launcher = SimpleNamespace(running=lambda r: False, launch=launch)
    coord = travel.TravelCoordinator(host, launcher, lambda r: {'game': r, 'root': str(tmp_path / r)},
                                     lambda: True, windows.NullWindows(), travel.TravelLinks(tmp_path / 'links.json'))
    def prepare(installation):
        assert installation['game'] == realm
        prepared['value'] = True
        events.append('prepare')
        return {'ok': True}
    coord._prepare_destination = prepare
    coord._send = lambda *a, **kw: True
    source = 'SR2' if realm == 'SR1' else 'SR1'
    row = coord._begin({'portal_id': 'portal.sr2.return_portal' if realm == 'SR1' else 'portal.sr1.sanctuary_gate',
                        'realm': source, 'request_id': 'fixture', 'save_id': 'fixture-save'})
    assert row['state'] == 'PREPARING', row
    assert events == ['prepare', 'launch']


@pytest.mark.parametrize('problem', ['failed-result', 'exception'])
def test_cold_portal_never_launches_after_failed_preparation(tmp_path, problem):
    launched = []
    host = SimpleNamespace(state=lambda: {'state': 'STOPPED'}, realm_env=lambda r: {})
    launcher = SimpleNamespace(running=lambda r: False, launch=lambda *a, **kw: launched.append(True))
    coord = travel.TravelCoordinator(host, launcher, lambda r: {'game': r, 'root': str(tmp_path / r)},
                                     lambda: True, windows.NullWindows(), travel.TravelLinks(tmp_path / 'links.json'))
    def prepare(installation):
        if problem == 'exception':
            raise OSError('synthetic plugin copy failure')
        return {'ok': False, 'error': 'PLUGIN_NOT_READY'}
    coord._prepare_destination = prepare
    coord._send = lambda *a, **kw: True
    row = coord._begin({'portal_id': 'portal.sr1.sanctuary_gate', 'realm': 'SR1',
                        'request_id': 'fixture', 'save_id': 'fixture-save'})
    assert row['state'] == 'FAILED'
    assert row['code'] == 'DESTINATION_PREPARATION_FAILED'
    assert launched == []


def test_warm_portal_does_not_reinstall_or_relaunch_running_destination(tmp_path):
    host = SimpleNamespace(state=lambda: {'state': 'STOPPED'})
    def forbidden(*args, **kwargs):
        raise AssertionError('Warm destination must not be modified or relaunched')
    launcher = SimpleNamespace(running=lambda r: True, launch=forbidden)
    coord = travel.TravelCoordinator(host, launcher, lambda r: {'game': r}, lambda: True,
                                     windows.NullWindows(), travel.TravelLinks(tmp_path / 'links.json'),
                                     settings=lambda: {'travel_save_mode': 'CONTINUE'})
    coord._prepare_destination = forbidden
    coord._send = lambda *a, **kw: True
    row = coord._begin({'portal_id': 'portal.sr1.sanctuary_gate', 'realm': 'SR1',
                        'request_id': 'fixture', 'save_id': 'fixture-save'})
    assert row['state'] == 'PREPARING'
    assert row['launched'] is False


@pytest.mark.parametrize('plugin_state', ['missing', 'outdated', 'current'])
def test_real_installs_are_not_silently_modified_for_portal(monkeypatch, plugin_state):
    from apps.launcher import desktop
    api = object.__new__(desktop.LauncherAPI)
    api._launcher = SimpleNamespace(running=lambda r: False)
    monkeypatch.setattr(desktop.pack, 'is_lab_copy', lambda *a: False)
    monkeypatch.setattr(desktop.pack, 'prepare', lambda *a, **kw: pytest.fail('Original install write attempted'))
    monkeypatch.setattr(desktop.plugins, 'status', lambda *a: {'state': plugin_state, 'loader': True})
    result = api._prepare_travel_destination({'game': 'SR2', 'root': 'synthetic-original'})
    assert result['ok'] is (plugin_state == 'current')
