"""Registry policy regressions with inert API/command providers only."""
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.lab import save_guard


def cleanup_receipt():
    return {'passed': True, 'sources_untouched': True, 'saves_match_backup_after_restore': True,
            'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
            'registry_changes': {'SR2': {'removed_or_changed': [], 'added_or_changed': ['created'],
                                        'absent_before': True}},
            'registry_restore': {'SR2': 'removed-new-key'}}


def test_removed_new_key_is_valid_capability_cleanup():
    from tools.lab import counterpart_capabilities as cap
    assert cap._cleanup_verified(cleanup_receipt()) is True


@pytest.mark.parametrize('absent,state', [(False, 'removed-new-key'), (True, 'reimported')])
def test_capability_restore_receipt_must_match_prior_key_presence(absent, state):
    from tools.lab import counterpart_capabilities as cap
    result = cleanup_receipt()
    result['registry_changes']['SR2']['absent_before'] = absent
    result['registry_restore']['SR2'] = state
    assert cap._cleanup_verified(result) is False


def test_removed_new_key_unblocks_a_finalized_creation(monkeypatch, tmp_path):
    from tools.lab import creation_oracle as creation
    from bridge.counterpart.legacy_quarantine import LegacyQuarantine
    monkeypatch.setattr(creation, 'legacy_quarantine', lambda: LegacyQuarantine({'folders': [], 'retired_creation_ids': []}))
    folder = tmp_path / 'creation-synthetic'
    folder.mkdir()
    result = cleanup_receipt()
    result['registry_changes']['SR1'] = {'removed_or_changed': [], 'added_or_changed': []}
    path = folder / 'oracle-result.json'
    path.write_text(json.dumps(result), encoding='utf-8')
    before = path.read_bytes()
    creation.require_finalized_prior_creations(tmp_path)
    assert path.read_bytes() == before
    result['registry_changes']['SR2']['absent_before'] = False
    path.write_text(json.dumps(result), encoding='utf-8')
    with pytest.raises(RuntimeError, match='Unfinalized'):
        creation.require_finalized_prior_creations(tmp_path)


@pytest.mark.parametrize('error', [PermissionError('denied'), OSError('provider failed')])
def test_failed_registry_query_is_not_confirmed_absence(monkeypatch, tmp_path, error):
    key = save_guard.REGISTRY_KEYS['SR1']
    opened = MagicMock(side_effect=error)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=opened, HKEY_CURRENT_USER=object()))
    calls = []
    def failed_query(args, **kwargs):
        calls.append(args)
        assert args == ['reg.exe', 'query', key]
        return CompletedProcess(args, 1)
    monkeypatch.setattr(save_guard.subprocess, 'run', failed_query)
    with pytest.raises(RuntimeError, match='registry'):
        save_guard.snapshot(['SR1'], 'synthetic', low=tmp_path / 'low', out_root=tmp_path / 'out')
    assert len(calls) == 1
    assert not list((tmp_path / 'out').rglob('*.reg.absent'))
    assert not list((tmp_path / 'out').rglob('manifest.json'))


def test_native_key_not_found_confirms_absence(monkeypatch):
    opened = MagicMock(side_effect=FileNotFoundError('synthetic missing key'))
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=opened, HKEY_CURRENT_USER='synthetic-hive'))
    monkeypatch.setattr(save_guard.subprocess, 'run', lambda args, **kw: CompletedProcess(args, 1))
    assert save_guard._registry_key_exists(save_guard.REGISTRY_KEYS['SR2']) is False
    opened.assert_called_once_with('synthetic-hive', r'Software\MonomiPark\SlimeRancher2')


def test_query_failure_but_successful_native_open_is_present(monkeypatch):
    handle = MagicMock()
    opened = MagicMock(return_value=handle)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=opened, HKEY_CURRENT_USER='synthetic-hive'))
    monkeypatch.setattr(save_guard.subprocess, 'run', lambda args, **kw: CompletedProcess(args, 1))
    assert save_guard._registry_key_exists(save_guard.REGISTRY_KEYS['SR1']) is True
    handle.__enter__.assert_called_once()
    handle.__exit__.assert_called_once()


@pytest.mark.parametrize('state,registry_error,expected', [
    ('removed-new-key', False, 0), ('still differs', False, 1), (None, True, 1),
])
def test_dev_restore_includes_registry_verdict(monkeypatch, tmp_path, state, registry_error, expected):
    from tools.lab import oracle, species_dev_session as dev
    monkeypatch.setattr(dev, 'assert_no_other_game_processes', lambda: None)
    monkeypatch.setattr(oracle, 'assert_no_other_game_processes', lambda: None)
    monkeypatch.setattr(oracle, 'source_fingerprints', lambda realms: {})
    monkeypatch.setattr(save_guard, 'compare', lambda path: {'SR1': {'modified': [], 'removed': [], 'added': []}})
    saves = MagicMock(return_value={})
    monkeypatch.setattr(save_guard, 'restore_originals', saves)
    changes = MagicMock(return_value={'SR1': {'absent_before': True, 'removed_or_changed': [], 'added_or_changed': ['created']}})
    if registry_error:
        changes.side_effect = RuntimeError('synthetic export denied')
    restored = MagicMock(return_value={'SR1': state})
    monkeypatch.setattr(save_guard, 'registry_changes', changes)
    monkeypatch.setattr(save_guard, 'restore_registry', restored)
    assert dev.restore_backup(tmp_path) == expected
    changes.assert_called_once_with(tmp_path)
    saves.assert_called_once()
    if registry_error:
        restored.assert_not_called()
    else:
        restored.assert_called_once()
    evidence = list(tmp_path.rglob('restore-result.json'))
    assert len(evidence) == 1
    assert json.loads(evidence[0].read_text())['passed'] is (expected == 0)


@pytest.mark.parametrize('fault', ['post-delete-query', 'import', 'verification-export'])
@pytest.mark.parametrize('entrypoint', ['api', 'cli'])
def test_registry_fault_keeps_failure_but_restores_independent_realm(monkeypatch, tmp_path, fault, entrypoint):
    from tools.lab import oracle, process_guard, species_dev_session as dev
    before = {'SR1': None if fault == 'post-delete-query' else 'before', 'SR2': 'before'}
    current = dict(before)
    phase = {'restoring': False}
    calls = []
    def export_text(realm, value):
        key = save_guard.REGISTRY_KEYS[realm].replace('HKCU', 'HKEY_CURRENT_USER', 1)
        return ('Windows Registry Editor Version 5.00\n\n[' + key + ']\n"fixture"="' + value + '"\n').encode('utf-16')
    def native_open(hive, key):
        assert hive == 'synthetic-hive'
        if phase['restoring'] and fault == 'post-delete-query':
            raise PermissionError('synthetic unknown presence after delete')
        raise FileNotFoundError('synthetic absent key')
    def registry(args, **kwargs):
        calls.append(args)
        assert args[0] == 'reg.exe'
        verb = args[1]
        realm = (Path(args[2]).stem if verb == 'import' else
                 next(realm for realm, key in save_guard.REGISTRY_KEYS.items() if key == args[2]))
        if verb == 'query':
            return CompletedProcess(args, 0 if current[realm] is not None else 1)
        if verb == 'export':
            if phase['restoring'] and realm == 'SR1' and fault == 'verification-export':
                raise OSError('synthetic independent verification export failure')
            Path(args[3]).write_bytes(export_text(realm, current[realm]))
        elif verb == 'delete':
            assert realm == 'SR1' and before[realm] is None
            current[realm] = None
        elif verb == 'import':
            if realm == 'SR1' and fault == 'import':
                raise OSError('synthetic independent import failure')
            assert Path(args[2]).read_bytes() == export_text(realm, before[realm])
            current[realm] = before[realm]
        else:
            raise AssertionError(args)
        return CompletedProcess(args, 0)
    monkeypatch.setattr(save_guard.subprocess, 'run', registry)
    monkeypatch.setitem(sys.modules, 'winreg', SimpleNamespace(OpenKey=native_open, HKEY_CURRENT_USER='synthetic-hive'))
    low = tmp_path / 'synthetic-low'
    for realm, rel in save_guard.SAVE_DIRS.items():
        (low / rel).mkdir(parents=True)
        (low / rel / 'fixture.sav').write_bytes(b'original')
    snap = save_guard.snapshot(['SR1', 'SR2'], 'synthetic-two-realms', low=low, out_root=tmp_path / 'backups')
    monkeypatch.setattr(save_guard, 'local_low', lambda: low)
    for rel in save_guard.SAVE_DIRS.values():
        (low / rel / 'fixture.sav').write_bytes(b'changed')
    current.update(SR1='changed', SR2='changed')
    changes = save_guard.registry_changes(snap)
    phase['restoring'] = True
    if entrypoint == 'api':
        try:
            restored = save_guard.restore_registry(snap, changes)
        except RuntimeError as ex:
            restored = {'SR1': str(ex)}
        assert any(args[1] == 'import' and Path(args[2]).stem == 'SR2' for args in calls)
        assert restored['SR2'] == 'reimported'
        assert not save_guard.registry_restoration_verified(changes, restored, ('SR1', 'SR2'))
    else:
        # Re-export after the mutation, but inject only the post-import verification fault.
        original_registry = registry
        def cli_registry(args, **kwargs):
            if args[1] == 'export' and not Path(args[3]).stem.endswith('after-restore'):
                phase['restoring'] = False
                try:
                    return original_registry(args, **kwargs)
                finally:
                    phase['restoring'] = True
            return original_registry(args, **kwargs)
        monkeypatch.setattr(save_guard.subprocess, 'run', cli_registry)
        monkeypatch.setattr(dev, 'assert_no_other_game_processes', lambda: None)
        monkeypatch.setattr(oracle, 'assert_no_other_game_processes', lambda: None)
        monkeypatch.setattr(process_guard, 'assert_no_other_game_processes', lambda: None)
        monkeypatch.setattr(oracle, 'source_fingerprints', lambda realms: {})
        assert dev.main(['--restore-backup', str(snap)]) == 1
        result = json.loads(next(snap.glob('restore-*/restore-result.json')).read_text())
        assert result['passed'] is False
        assert result['registry_restore']['SR2'] == 'reimported'
        assert result['saves_match_backup_after_restore'] is True
        for rel in save_guard.SAVE_DIRS.values():
            assert (low / rel / 'fixture.sav').read_bytes() == b'original'
    assert current['SR2'] == before['SR2']
