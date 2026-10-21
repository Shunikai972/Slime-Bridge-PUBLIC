"""Bind an SR1/SR2 creation receipt to its exact on-disk native blob before linkage.

Callers remain responsible for trusting and binding the plugin's verified metadata/readback.
This reader independently matches bytes and requests an OS flush: it does not parse native
save formats, prove SR2 runtime behavior, or guarantee hardware power-loss safety. Lexical
path/handle checks detect changes observed during verification, not all adversarial races.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path


def same_native_result(original: dict, replay: dict | None) -> bool:
    """Compare all plugin proof fields, excluding only the host's per-delivery receipt timestamp."""
    return isinstance(replay, dict) and (
        {k: v for k, v in original.items() if k != 'received_unix'} ==
        {k: v for k, v in replay.items() if k != 'received_unix'})


_CREATION_REALMS = {'SR1': ('sb1_', r'_(0|[1-9][0-9]{0,5})'),
                    'SR2': ('sb2_', r'_(0|[1-9][0-9]{0,9})')}


def creation_save_id(creation_id: str, *, realm: str) -> str:
    """Exact Bridge marker for a known realm; never infer a realm from caller input."""
    if not isinstance(realm, str) or realm not in _CREATION_REALMS:
        raise ValueError('unknown native creation realm')
    if not isinstance(creation_id, str) or re.fullmatch(r'[0-9a-f]{32}', creation_id) is None:
        raise ValueError('invalid creation id')
    return _CREATION_REALMS[realm][0] + creation_id


def creation_filename_ok(creation_id: str, filename: str, *, realm: str) -> bool:
    """Validate a storage basename, not a path or a file extension.

    SR2's ten-digit bound is the local CounterpartCreator.Verify static contract, not
    a claim that any save format or game-side marker hook has been runtime-proven.
    """
    save_id = creation_save_id(creation_id, realm=realm)
    return isinstance(filename, str) and re.fullmatch(
        re.escape(save_id) + _CREATION_REALMS[realm][1], filename) is not None


def _checked_path_stats(target: Path) -> dict:
    """lstat lexical ancestors before accessing children; resolution would hide reparse points."""
    states = {}
    for component in reversed((target, *target.parents)):
        state = component.lstat()
        if stat.S_ISLNK(state.st_mode) or getattr(state, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('reparse point in native creation readback path')
        if component != target and not stat.S_ISDIR(state.st_mode):
            raise ValueError('non-directory ancestor in native creation readback path')
        states[component] = state
    if not stat.S_ISREG(states[target].st_mode):
        raise ValueError('verified native creation path is not a regular file')
    if states[target].st_nlink != 1:
        raise ValueError('native creation filename is hardlinked to another path')
    return states


def _file_state(state: os.stat_result) -> tuple:
    # Access times may change merely by reading; do not mistake them for mutations.
    # Windows lstat/fstat can expose different deprecated ctime semantics. Bind
    # their common birthtime instead; compare handle change-time separately below.
    timestamp = getattr(state, 'st_birthtime_ns', state.st_ctime_ns)
    return (state.st_dev, state.st_ino, state.st_mode, state.st_nlink,
            state.st_size, state.st_mtime_ns, timestamp)


def _assert_stable_path(target: Path, before: dict) -> None:
    after = _checked_path_stats(target)
    if (any((state.st_dev, state.st_ino) != (after[path].st_dev, after[path].st_ino)
            for path, state in before.items()) or _file_state(before[target]) != _file_state(after[target])):
        raise ValueError('native creation file or its bound path changed during disk readback')


def verify_creation(result: dict, creation_id: str, op_id: str, pid: int, save_root: Path,
                    *, realm: str, expected_kind: str = 'create_save') -> dict:
    save_id = creation_save_id(creation_id, realm=realm)
    if type(pid) is not int or pid <= 0:
        raise ValueError('invalid native creation process id')
    if not isinstance(op_id, str) or re.fullmatch(r'[A-Za-z0-9:_-]{1,128}', op_id) is None:
        raise ValueError('invalid native creation operation id')
    if expected_kind not in ('create_save', 'reconcile_save'):
        raise ValueError('invalid native creation command kind')
    expected = {'realm': realm, 'kind': expected_kind, 'op_id': op_id, 'creation_id': creation_id,
                'save_id': save_id, 'pid': pid}
    if (not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()) or
            type(result.get('pid')) is not int or result.get('ok') is not True or result.get('verified') is not True):
        raise ValueError('native creation receipt is not verified for this command, save and process')
    filename, digest = result.get('save_file'), result.get('sha256')
    if (not isinstance(filename, str) or not creation_filename_ok(creation_id, filename, realm=realm) or
            not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
        raise ValueError('invalid native creation filename or fingerprint')
    try:
        root = Path(save_root)
    except TypeError as exc:
        raise ValueError('invalid native creation save root path') from exc
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('native creation save root path must be absolute without parent traversal')
    target = root / (filename + '.sav')
    before = _checked_path_stats(target)
    with target.open('r+b', buffering=0) as native:
        opened = os.fstat(native.fileno())
        if _file_state(opened) != _file_state(before[target]):
            raise ValueError('native creation file changed between path check and open')
        actual = hashlib.sha256(native.read()).hexdigest()
        if actual != digest:
            raise ValueError('native file fingerprint does not match full game-side readback')
        _assert_stable_path(target, before)
        # Flush only the proven Bridge-owned file, without changing any save bytes.
        os.fsync(native.fileno())
        native.seek(0)
        second_hash = hashlib.sha256(native.read()).hexdigest()
        after = os.fstat(native.fileno())
        if (second_hash != digest or _file_state(after) != _file_state(before[target]) or
                after.st_ctime_ns != opened.st_ctime_ns):
            raise ValueError('native creation file changed during persistence verification')
    # A still-readable descriptor is not proof that the named file still exists.
    _assert_stable_path(target, before)
    return {'save_id': save_id, 'save_file': str(target), 'sha256': digest,
            'identity': realm.lower() + '-creation:' + creation_id, 'os_flush_completed': True}


def verify_sr1_creation(result: dict, creation_id: str, op_id: str, pid: int, save_root: Path,
                        *, expected_kind: str = 'create_save') -> dict:
    """Compatibility entry point for existing SR1 callers."""
    return verify_creation(result, creation_id, op_id, pid, save_root, realm='SR1', expected_kind=expected_kind)


def verify_sr2_creation(result: dict, creation_id: str, op_id: str, pid: int, save_root: Path,
                        *, expected_kind: str = 'create_save') -> dict:
    """SR2 counterpart of the SR1 entry point; this does not parse native data."""
    return verify_creation(result, creation_id, op_id, pid, save_root, realm='SR2', expected_kind=expected_kind)
