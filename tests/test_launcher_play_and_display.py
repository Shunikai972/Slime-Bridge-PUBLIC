"""Regression tests for Play vs Continue, foreground focus, display restore, env stripping, and mod contracts."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.launcher import runtime, windows
from tools.lab import screen_guard
from test_launcher_lifecycle_ordering import api_for

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_screen_guard_uses_real_sr2_registry_path():
    assert screen_guard.KEYS['SR1'] == r'Software\Monomi Park\Slime Rancher'
    assert screen_guard.KEYS['SR2'] == r'Software\MonomiPark\SlimeRancher2'


def test_keep_display_settings_ignores_default_keys_and_captures_real_resolution(monkeypatch):
    fake_snapshot = {
        'SR2': {
            'Screenmanager Resolution Width Default_h680557497': (1024, 4),
            'Screenmanager Resolution Height Default_h1380706816': (768, 4),
            'Screenmanager Fullscreen mode Default_h401710285': (1, 4),
            'Screenmanager Resolution Width_h182942802': (1920, 4),
            'Screenmanager Resolution Height_h2627697771': (1080, 4),
            'Screenmanager Fullscreen mode_h3630240806': (1, 4),
        }
    }
    monkeypatch.setattr(runtime.os, 'name', 'nt', raising=False)
    monkeypatch.setattr(screen_guard, 'snapshot', lambda realms: fake_snapshot)
    runtime._PENDING_DISPLAY.pop('SR2', None)
    try:
        kept = runtime._keep_display_settings('SR2')
        assert kept == {'fullscreen': True, 'fullscreen_mode': 1, 'width': 1920, 'height': 1080}
        assert 'SR2' in runtime._PENDING_DISPLAY
    finally:
        runtime._PENDING_DISPLAY.pop('SR2', None)


def test_keep_display_settings_supports_legacy_is_fullscreen_key(monkeypatch):
    fake_snapshot = {
        'SR1': {
            'Screenmanager Is Fullscreen mode_h3981298716': (1, 4),
            'Screenmanager Resolution Width_h182942802': (1920, 4),
            'Screenmanager Resolution Height_h2627697771': (1080, 4),
        }
    }
    monkeypatch.setattr(runtime.os, 'name', 'nt', raising=False)
    monkeypatch.setattr(screen_guard, 'snapshot', lambda realms: fake_snapshot)
    runtime._PENDING_DISPLAY.pop('SR1', None)
    try:
        kept = runtime._keep_display_settings('SR1')
        assert kept['fullscreen'] is True
        assert kept['width'] == 1920 and kept['height'] == 1080
    finally:
        runtime._PENDING_DISPLAY.pop('SR1', None)


def test_game_launcher_strips_leaked_travel_env_but_keeps_dev_mode(tmp_path, monkeypatch):
    exe = tmp_path / 'SlimeRancher.exe'
    exe.write_bytes(b'stub')
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured['cmd'] = list(cmd)
        captured['env'] = dict(kwargs['env'])
        child = mock.Mock(pid=4321)
        child.poll.return_value = None
        return child

    monkeypatch.setenv('SLIMEBRIDGE_TRAVEL_MODE', 'RESUME')
    monkeypatch.setenv('SLIMEBRIDGE_TRAVEL_SAVE', 'LeakedSave')
    monkeypatch.setenv('SLIMEBRIDGE_TRAVEL_NEW', 'LeakedNew')
    monkeypatch.setenv('SLIMEBRIDGE_BACKGROUND_START', '1')
    monkeypatch.setenv('SLIMEBRIDGE_FULLSCREEN_ON_ARRIVAL', '1')
    monkeypatch.setenv('SLIMEBRIDGE_RESTORE_WIDTH', '1280')
    monkeypatch.setenv('SLIMEBRIDGE_DEV', '1')

    launcher = runtime.GameLauncher(popen=fake_popen)
    launcher.launch({'game': 'SR1', 'root': str(tmp_path)}, extra_env={'SLIMEBRIDGE_URL': 'http://127.0.0.1:9'})
    for leaked in runtime._STRIP_LAUNCH_ENV:
        assert leaked not in captured['env'], f'{leaked} leaked into child env'
    assert captured['env'].get('SLIMEBRIDGE_DEV') == '1'
    assert captured['cmd'] == [str(exe)]


def test_game_launcher_background_uses_compact_window_and_cleans_pending_on_oserror(tmp_path, monkeypatch):
    exe = tmp_path / 'SlimeRancher2.exe'
    exe.write_bytes(b'stub')
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured['cmd'] = list(cmd)
        child = mock.Mock(pid=5432)
        child.poll.return_value = None
        return child

    launcher = runtime.GameLauncher(popen=fake_popen)
    launcher.launch({'game': 'SR2', 'root': str(tmp_path)}, extra_env={'SLIMEBRIDGE_TRAVEL_MODE': 'DESTINATION'})
    if os.name == 'nt':
        assert captured['cmd'][1:7] == ['-screen-fullscreen', '0', '-screen-width', '640', '-screen-height', '360']

    runtime._PENDING_DISPLAY['SR2'] = {'SR2': {}}
    def failing_popen(cmd, **kwargs):
        raise OSError('denied')

    bad_launcher = runtime.GameLauncher(popen=failing_popen)
    with pytest.raises(runtime.RuntimeErrorWithCode):
        bad_launcher.launch({'game': 'SR2', 'root': str(tmp_path)}, extra_env={'SLIMEBRIDGE_TRAVEL_MODE': 'DESTINATION'})
    assert 'SR2' not in runtime._PENDING_DISPLAY


def test_play_both_opens_front_menu_without_autoload_and_focuses_foreground(tmp_path):
    api, _ = api_for(tmp_path)
    for child in api._launcher._children.values():
        child.poll.return_value = 0
    api._audio = mock.Mock()
    api._host = mock.Mock()
    api._host.realm_env.return_value = {'SLIMEBRIDGE_URL': 'http://127.0.0.1:9999', 'SLIMEBRIDGE_TOKEN': 'tok'}
    api._host.state.return_value = {'state': 'RUNNING', 'url': 'http://127.0.0.1:9999'}
    api._ensure_travel_loop = mock.Mock()
    api._bring_to_front = mock.Mock()
    api._primary = lambda realm: {'game': realm, 'root': str(tmp_path / realm)}
    api._session.set('SR1', 'OldRanch')
    api._travel._links.remember('OldRanch', 'OldSR2')

    control = mock.Mock()
    control.allow_foreground.return_value = True
    api._window_control = lambda: control

    calls = []
    def fake_launch(install, extra_env=None):
        calls.append((install['game'], dict(extra_env or {})))
        return {'realm': install['game'], 'pid': 7777}

    api._launcher.launch = mock.Mock(side_effect=fake_launch)
    with mock.patch('apps.launcher.desktop.pack.is_lab_copy', return_value=False):
        result = api.launch('BOTH_SR1', confirmed=True, resume=False)

    assert result['ok']
    assert [c[0] for c in calls] == ['SR1'], 'Play BOTH_SR1 must launch only SR1 first (follow mode)'
    env = calls[0][1]
    assert 'SLIMEBRIDGE_TRAVEL_MODE' not in env
    assert 'SLIMEBRIDGE_TRAVEL_SAVE' not in env
    control.allow_foreground.assert_called_once_with(7777)
    api._bring_to_front.assert_called_once()


def test_win32_allow_foreground_checks_verification():
    allowed = []
    inst = object.__new__(windows.Win32Windows)
    inst._verify = lambda pid: pid == 123
    inst._user32 = SimpleNamespace(AllowSetForegroundWindow=lambda pid: allowed.append(pid) or True)
    assert inst.allow_foreground(123) is True
    assert inst.allow_foreground(999) is False
    assert allowed == [123]


def test_shutdown_restores_pending_display_settings_for_exited_games(tmp_path):
    api, _ = api_for(tmp_path)
    api._launcher._children['SR1'].poll.return_value = 0
    api._launcher._children['SR2'].poll.return_value = None  # still running
    restored = []
    with mock.patch('apps.launcher.runtime.restore_display_settings', side_effect=lambda r: restored.append(r)):
        api.shutdown()
    assert restored == ['SR1']


def test_sr1_and_sr2_critical_contracts():
    cloner = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'Sr2PrefabCloner.cs').read_text(encoding='utf-8')
    assert 'clone.SetActive(false)' not in cloner, 'Reference prefabs must stay activeSelf=true under inactive container'

    patch = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'Sr2PersistencePatch.cs').read_text(encoding='utf-8')
    assert 'SaveGameImpl' in patch and 'CreateLoadReferenceTranslation' in patch

    spawner = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'Sr2DevSpawner.cs').read_text(encoding='utf-8')
    assert 'InstantiationHelpers.InstantiateActor' in spawner
    assert 'new Il2CppSystem.Nullable<Il2CppMonomiPark.SlimeRancher.Player.AmmoSlot.AmmoMetadata>()' in spawner

    portal = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'Sr2ReturnPortal.cs').read_text(encoding='utf-8')
    place_block = portal.split('internal bool PlacePlayer()')[1].split('private void LabScript()')[0]
    assert 'Build()' in place_block, 'PlacePlayer must build the 3D portal before the overlay opens'

    sr1_carry = (REPO_ROOT / 'mods' / 'sr1-bridge' / 'Sr1Carry.cs').read_text(encoding='utf-8')
    sr2_carry = (REPO_ROOT / 'mods' / 'sr2-bridge' / 'Sr2Carry.cs').read_text(encoding='utf-8')
    assert 'ammo.Decrement(i, afterCount - beforeCount)' in sr1_carry
    assert 'ammo.Clear(i)' in sr2_carry and 'ammo.Decrement(i, slot.Count - beforeCount)' in sr2_carry

    dev_cmd = (REPO_ROOT / 'mods' / 'sr1-bridge' / 'DevSpawnCommand.cs').read_text(encoding='utf-8')
    assert 'for (var i = 0; i < count; i++)' in dev_cmd

    travel_agent = (REPO_ROOT / 'mods' / 'shared' / 'TravelAgent.cs').read_text(encoding='utf-8')
    assert 'http.Slow =' in travel_agent and 'InFastWindow' in travel_agent
