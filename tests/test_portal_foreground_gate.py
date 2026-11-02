"""Coordinator window failures must not authorize arrival; external boundaries are inert."""
from types import SimpleNamespace

import pytest

from apps.launcher import travel


def scenario(tmp_path, source='SR1', **changes):
    destination = 'SR2' if source == 'SR1' else 'SR1'
    calls = []
    backend = SimpleNamespace(requires_focus_confirmation=True, restore=lambda pid: True,
        minimize=lambda pid: True, focus=lambda pid: False)
    host = SimpleNamespace(state=lambda: {'state': 'STOPPED'})
    coordinator = travel.TravelCoordinator(host, SimpleNamespace(running=lambda realm: True),
        lambda realm: {'game': realm}, lambda: True, backend, travel.TravelLinks(tmp_path/'links.json'), clock=lambda: 10.0)
    coordinator._pid = lambda realm: 111 if realm == source else 222
    coordinator._world_ready = lambda realm: True
    coordinator._saw = lambda *args: True
    coordinator._latest_carry = lambda *args: {}
    coordinator._send = lambda *args, **kwargs: calls.append((args, kwargs))
    row = dict(id='synthetic', portal_id='portal.sr1.sanctuary_gate' if source == 'SR1' else 'portal.sr2.return_portal',
        state='PREPARING', since=0.0, deadline=45.0, launched=False,
        history=[], **{'from': source, 'to': destination})
    row.update(changes)
    # The source has not failed; its departure_ready is available.
    coordinator._saw = lambda transition, realm, event: event == 'departure_ready'
    return coordinator, backend, row, calls


@pytest.mark.parametrize('source', ['SR1', 'SR2'])
def test_no_arrival_or_inventory_offer_before_confirmed_destination_focus(tmp_path, source):
    coordinator, backend, row, calls = scenario(tmp_path, source)
    coordinator._advance(row)
    assert row['state'] == 'PREPARING'
    assert calls == []
    backend.focus = lambda pid: True
    coordinator._advance(row)
    assert row['state'] == 'SWITCHING'
    assert [args[:2] for args, kwargs in calls] == [(row['to'], 'arrive')]


def test_native_focus_exception_is_not_silently_accepted(tmp_path):
    coordinator, backend, row, calls = scenario(tmp_path)
    def fail(pid):
        raise OSError('synthetic foreground refusal')
    backend.focus = fail
    coordinator._advance(row)
    assert row['state'] == 'PREPARING'
    assert calls == []


def test_cold_game_with_no_window_yet_is_retried_not_marked_parked(tmp_path):
    coordinator, backend, row, calls = scenario(tmp_path, launched=True)
    coordinator._world_ready = lambda realm: False
    backend.minimize = lambda pid: False
    backend.focus = lambda pid: True
    coordinator._advance(row)
    assert not row.get('parked')
    backend.minimize = lambda pid: True
    coordinator._advance(row)
    assert row['parked'] is True


def test_failed_focus_times_out_without_issuing_destination_arrival(tmp_path):
    coordinator, backend, row, calls = scenario(tmp_path, since=-100.0)
    coordinator._advance(row)
    assert row['state'] == 'ROLLED_BACK'
    assert [args[:2] for args, kwargs in calls] == [('SR1', 'resume')]


def test_launch_pid_can_park_destination_before_its_first_plugin_heartbeat(tmp_path):
    coordinator, backend, row, calls = scenario(tmp_path, launched=True, destination_pid=222)
    coordinator._pid = lambda realm: 111 if realm == 'SR1' else None
    coordinator._world_ready = lambda realm: False
    parked = []
    backend.minimize = lambda pid: parked.append(pid) or True
    coordinator._advance(row)
    assert parked == [222]
    assert row['parked'] is True


def test_cold_destination_new_windows_are_parked_after_an_earlier_console_was_parked(tmp_path):
    coordinator, backend, row, calls = scenario(tmp_path, launched=True, parked=True)
    coordinator._world_ready = lambda realm: False
    parked = []
    backend.minimize = lambda pid: parked.append(pid) or True
    coordinator._advance(row)
    assert parked == [222], 'A later Unity window must not escape the earlier console-only parking'
