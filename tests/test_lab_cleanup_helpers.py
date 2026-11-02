"""Pure cleanup helper and synthetic LAB plugin rollback contracts."""
import ast
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import pytest

from tools.lab import oracle, paths
from apps.launcher import plugins
from portability import directory_link

ROOT = Path(__file__).resolve().parents[1]


def helpers(tmp_path):
    tree = ast.parse((ROOT / 'tools/lab/oracle.py').read_text(encoding='utf-8-sig'))
    names = {'cleanup_steps', 'snapshot_plugins', 'restore_plugins'}
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in found} == names
    env = {'Path': Path, 'hashlib': hashlib, 'LAB': tmp_path / 'Lab' / 'Games',
           'validate_lab_destination': paths.validate_lab_destination,
           'assert_no_other_game_processes': lambda: None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=found, type_ignores=[])), '<exact-cleanup-helpers>', 'exec'), env)
    return NS(**env)


def roots(tmp_path):
    result = {r: tmp_path / 'Lab' / 'Games' / r for r in ('SR1', 'SR2')}
    for realm, root in result.items():
        folder = plugins.destination(realm, root)
        folder.mkdir(parents=True)
        (folder / plugins.FILES[realm][0]).write_bytes(b'')
    return result


def test_plugin_rollback_preserves_empty_bytes_and_removes_added_files(tmp_path):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    snapshot = h.snapshot_plugins(installations)
    for realm, root in installations.items():
        for name in plugins.FILES[realm]:
            (plugins.destination(realm, root) / name).write_bytes(b'synthetic replacement')
    h.restore_plugins(snapshot)
    for realm, root in installations.items():
        folder = plugins.destination(realm, root)
        assert (folder / plugins.FILES[realm][0]).read_bytes() == b''
        assert all(not (folder / name).exists() for name in plugins.FILES[realm][1:])


def test_plugin_rollback_failure_attempts_independent_realm(tmp_path):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    snapshot = h.snapshot_plugins(installations)
    for realm, root in installations.items():
        (plugins.destination(realm, root) / plugins.FILES[realm][0]).write_bytes(b'changed')
    real_write = Path.write_bytes

    def write(path, data):
        if path.name == plugins.FILES['SR1'][0]:
            raise OSError('synthetic first plugin write failure')
        return real_write(path, data)

    with mock.patch.object(Path, 'write_bytes', write):
        with pytest.raises(RuntimeError, match='plugin'):
            h.restore_plugins(snapshot)
    assert (plugins.destination('SR2', installations['SR2']) / plugins.FILES['SR2'][0]).read_bytes() == b''


def test_cleanup_preserves_finalizer_failure_and_runs_display_restore(tmp_path):
    h = helpers(tmp_path)
    events = []
    result = {'passed': True}

    def finalize():
        result.update(passed=False, registry_error='synthetic unavailable registry', saves_match_backup_after_restore=True)

    h.cleanup_steps(result, [('finalizer', finalize), ('display', lambda: events.append('display'))])
    assert events == ['display']
    assert result['passed'] is False and result['cleanup_completed'] is False


def test_cleanup_defers_keyboard_interrupt_until_after_independent_steps(tmp_path):
    h = helpers(tmp_path)
    events = []
    result = {'passed': True}

    def interrupted():
        raise KeyboardInterrupt('synthetic interrupt')

    with pytest.raises(KeyboardInterrupt):
        h.cleanup_steps(result, [('first', interrupted), ('second', lambda: events.append('second'))])
    assert events == ['second']
    assert result['passed'] is False and result['cleanup_completed'] is False


@pytest.mark.parametrize('name,realm', [('sanctuary_oracle.py', 'SR1'), ('sr2_probe_oracle.py', 'SR2')])
def test_legacy_plugin_restore_refuses_live_games_before_writes(tmp_path, name, realm):
    folder = tmp_path / 'synthetic-plugin'
    folder.mkdir()
    target = folder / plugins.FILES[realm][0]
    target.write_bytes(b'unchanged plugin while game is live')
    source = ast.parse((ROOT / 'tools/lab' / name).read_text(encoding='utf-8-sig'))
    fn = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'restore_plugin')

    def blocked():
        raise RuntimeError('synthetic live game')

    env = {'PLUGIN_DEST': folder, 'PLUGIN_FILES': plugins.FILES[realm],
           '_sha': lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
           'assert_no_other_game_processes': blocked}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<exact-plugin-restore>', 'exec'), env)
    with pytest.raises(RuntimeError, match='synthetic live game'):
        env['restore_plugin']({target.name: b'do not write'})
    assert target.read_bytes() == b'unchanged plugin while game is live'


@pytest.mark.parametrize('matching', [False, True])
def test_stop_owned_checks_exact_custom_lab_executable(tmp_path, matching):
    lab = tmp_path / 'Lab' / 'Games'
    exe = lab / 'custom-copy' / 'SlimeRancher.exe'
    child = mock.Mock(pid=123)
    child.poll.return_value = None
    image = exe if matching else lab / 'another-copy' / 'SlimeRancher.exe'
    with mock.patch.object(oracle, 'LAB', lab), mock.patch.object(oracle, 'process_image_path', return_value=image):
        errors = oracle._stop_owned({'SR1': child}, {'SR1': exe})
    assert bool(errors) is (not matching)
    assert child.terminate.call_count == int(matching)


def test_plugin_rollback_refuses_hardlink_without_changing_external_bytes(tmp_path):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    saved = h.snapshot_plugins(installations)
    external = tmp_path / 'external-sentinel.bin'
    external.write_bytes(b'external bytes must remain untouched')
    target = plugins.destination('SR1', installations['SR1']) / plugins.FILES['SR1'][0]
    target.unlink()
    os.link(external, target)
    assert target.stat().st_nlink == 2
    sibling = plugins.destination('SR2', installations['SR2']) / plugins.FILES['SR2'][0]
    sibling.write_bytes(b'synthetic replaced SR2 plugin')
    with pytest.raises(RuntimeError, match='plugin'):
        h.restore_plugins(saved)
    assert external.read_bytes() == b'external bytes must remain untouched'
    assert sibling.read_bytes() == b''


def test_snapshot_refuses_hardlinked_plugin_before_reading_it(tmp_path):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    target = plugins.destination('SR1', installations['SR1']) / plugins.FILES['SR1'][0]
    external = tmp_path / 'external-sentinel.bin'
    external.write_bytes(b'external bytes must not be snapshotted')
    target.unlink()
    os.link(external, target)
    with pytest.raises(ValueError, match='regular|link'):
        h.snapshot_plugins(installations)
    assert external.read_bytes() == b'external bytes must not be snapshotted'


def test_rejected_junction_does_not_prevent_safe_sibling_rollback(tmp_path):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    saved = h.snapshot_plugins(installations)
    external = tmp_path / 'external-plugins'
    external.mkdir()
    sentinel = external / plugins.FILES['SR1'][0]
    sentinel.write_bytes(b'outside junction target must stay unchanged')
    folder = plugins.destination('SR1', installations['SR1'])
    (folder / plugins.FILES['SR1'][0]).unlink()
    folder.rmdir()
    sibling = plugins.destination('SR2', installations['SR2']) / plugins.FILES['SR2'][0]
    sibling.write_bytes(b'synthetic replaced SR2 plugin')
    with directory_link(folder, external):
        with pytest.raises((ValueError, RuntimeError)):
            h.restore_plugins(saved)
    assert sentinel.read_bytes() == b'outside junction target must stay unchanged'
    assert sibling.read_bytes() == b''


@pytest.mark.parametrize('name,realm', [('sanctuary_oracle.py', 'SR1'), ('sr2_probe_oracle.py', 'SR2')])
def test_default_plugin_providers_reuse_hardlink_safe_rollback(tmp_path, name, realm):
    h = helpers(tmp_path)
    installations = roots(tmp_path)
    source = ast.parse((ROOT / 'tools/lab' / name).read_text(encoding='utf-8-sig'))
    functions = [n for n in source.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('snapshot_plugin', 'restore_plugin')]
    folder = plugins.destination(realm, installations[realm])
    env = {'PLUGIN_DEST': folder, 'PLUGIN_FILES': plugins.FILES[realm], 'oracle': h,
           '_sha': lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
           'assert_no_other_game_processes': lambda: None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[])), '<exact-default-plugin-provider>', 'exec'), env)
    saved = env['snapshot_plugin']()
    external = tmp_path / 'external-sentinel.bin'
    external.write_bytes(b'default provider must not change external bytes')
    target = folder / plugins.FILES[realm][0]
    target.unlink()
    os.link(external, target)
    with pytest.raises(RuntimeError, match='plugin'):
        env['restore_plugin'](saved)
    assert external.read_bytes() == b'default provider must not change external bytes'
