"""Read-only SR2 capability evidence. No counterpart creation or port proof."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time

from tools.lab import oracle, process_guard
from tools.lab.paths import refuse_reparse, validate_lab_destination

_NAME = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_REF = re.compile(r'[A-Za-z0-9_.:-]{1,256}\Z')


def _integer(value, lower, upper):
    return type(value) is int and lower <= value <= upper


def _identity(value, pattern=_NAME):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_capability_report(report: dict, *, expected_pid: int, expected_run_id: str) -> dict:
    """Cross-check full raw traversal and detached native reads, never reserve a slot."""
    if (not isinstance(report, dict) or report.get('schema_version') != 1 or
            type(report.get('schema_version')) is not int or report.get('realm') != 'SR2' or
            report.get('status') != 'DIAGNOSTIC_ONLY' or report.get('read_only') is not True or
            report.get('creation_authorized') is not False or
            not _integer(expected_pid, 1, 2**31 - 1) or type(report.get('pid')) is not int or
            report['pid'] != expected_pid or not _identity(expected_run_id, re.compile(r'[0-9a-f]{32}\Z')) or
            report.get('run_id') != expected_run_id or
            any(type(report.get(k)) is not int or report[k] != 0 for k in
                ('native_write_calls', 'native_world_load_calls'))):
        raise ValueError('not a read-only diagnostic from this owned process/run')
    flags = ('menu_ready', 'storage_ready', 'stock_default_keys_ready', 'stock_icon_matches_model',
             'occupancy_complete', 'detached_readback_complete')
    if any(type(report.get(k)) is not bool for k in flags):
        raise ValueError('missing or mistyped readiness flag')
    errors = report.get('errors')
    rows = report.get('summaries')
    if (not isinstance(errors, list) or any(not isinstance(x, str) or len(x) > 256 for x in errors) or
            not isinstance(rows, list) or len(rows) > 4096 or
            not _integer(report.get('slot_count'), 0, 100_000) or
            not _integer(report.get('raw_save_count'), 0, 4096)):
        raise ValueError('malformed diagnostic metadata')
    complete = report['occupancy_complete']
    if complete and (not report['slot_count'] or len(rows) != report['raw_save_count']):
        raise ValueError('incomplete raw traversal claimed complete')
    if report['detached_readback_complete'] and len(rows) != report['raw_save_count']:
        raise ValueError('incomplete detached traversal claimed complete')
    raw_names, by_game, by_slot = set(), {}, {}
    full_reads = True
    for row in rows:
        if (not isinstance(row, dict) or any(not _identity(row.get(k)) for k in
                ('raw_name', 'save_name', 'game_name')) or row['raw_name'] != row['save_name'] or
                row['raw_name'] in raw_names or type(row.get('invalid')) is not bool or
                not _integer(row.get('slot_index'), 0, report['slot_count'] - 1) or row['invalid']):
            raise ValueError('malformed or invalid raw native summary')
        raw_names.add(row['raw_name'])
        game, slot = row['game_name'], row['slot_index']
        if (game in by_game and by_game[game] != slot) or (slot in by_slot and by_slot[slot] != game):
            raise ValueError('conflicting native slot identities')
        by_game[game], by_slot[slot] = slot, game
        read = row.get('readback')
        if not isinstance(read, dict):
            raise ValueError('missing detached readback evidence')
        if read.get('status') == 'NATIVE_ERROR' and errors and not report['detached_readback_complete']:
            full_reads = False
            continue
        if (read.get('status') != 'READ_VERIFIED' or read.get('game_name') != game or
                type(read.get('slot_index')) is not int or read['slot_index'] != slot or
                any(read.get(k) is not True for k in ('full_load', 'identity_matches', 'settings_complete')) or
                not _identity(read.get('sha256'), re.compile(r'[0-9a-f]{64}\Z')) or
                not _integer(read.get('byte_count'), 1, 64 * 1024 * 1024) or
                not _integer(read.get('option_count'), 1, 4096) or
                not _identity(row.get('icon_reference_id'), _REF) or
                read.get('icon_reference_id') != row['icon_reference_id']):
            raise ValueError('incomplete or mismatched full detached readback proof')
    if report['detached_readback_complete'] and not full_reads:
        raise ValueError('contradictory detached readback completeness')
    candidate = next((n for n in range(report['slot_count']) if n not in by_slot), None) if complete else None
    observed = report.get('least_free_slot')
    if (observed is not None and type(observed) is not int) or observed != candidate:
        raise ValueError('least-free diagnostic contradicts complete native metadata')
    definitions, model_ids = report.get('stock_definition_ids'), report.get('model_definition_ids')
    if (not isinstance(definitions, list) or not isinstance(model_ids, list) or
            any(not _identity(x, _REF) for x in definitions + model_ids) or
            len(set(definitions)) != len(definitions) or len(set(model_ids)) != len(model_ids)):
        raise ValueError('malformed stock option definition identities')
    if not errors and (not definitions or set(definitions) != set(model_ids)):
        raise ValueError('stock definition/model coverage missing')
    settings = (bool(definitions) and set(definitions) == set(model_ids) and
                report['stock_default_keys_ready'] and report['stock_icon_matches_model'] and
                _identity(report.get('stock_icon_reference_id'), _REF))
    ready = (not errors and all(report[k] for k in ('menu_ready', 'storage_ready')) and
             settings and complete and report['detached_readback_complete'] and full_reads and
             bool(rows) and candidate is not None)
    return {**report, 'diagnostic_ready': bool(ready), 'least_free_slot': candidate,
            'status': 'DIAGNOSTIC_ONLY', 'creation_authorized': False}


def _artifact_path(path: Path, repository_root: Path) -> Path:
    path, repository_root = Path(path), Path(repository_root)
    if not path.is_absolute() or not repository_root.is_absolute():
        raise ValueError('absolute diagnostic artifact path required')
    refuse_reparse(repository_root)
    refuse_reparse(path)
    # Refuse traversal, UNC/device namespaces and Windows alternate data streams before resolution.
    if ('..' in path.parts or str(path).startswith(('\\\\', '//')) or
            any(':' in part for part in path.parts[1:]) or path.suffix != '.json'):
        raise ValueError('invalid diagnostic output path')
    root = repository_root.resolve() / 'Lab/RuntimeArtifacts'
    target = path.resolve()
    if root not in target.parents:
        raise ValueError('diagnostic output escapes ignored Lab/RuntimeArtifacts')
    return target


def _regular_single_link(path: Path) -> os.stat_result:
    refuse_reparse(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('diagnostic artifact must be a regular non-linked file')
    return info


def publish_json_exclusive(path: Path, value: dict, *, repository_root: Path) -> None:
    """Flush/fsync a new private temporary JSON, then publish without overwriting anything."""
    path = _artifact_path(path, repository_root)
    if os.path.lexists(path):
        raise FileExistsError('diagnostic output target is already occupied')
    if not path.parent.is_dir():
        raise ValueError('diagnostic output parent must already exist')
    encoded = json.dumps(value, indent=2, allow_nan=False).encode('utf-8')
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError('diagnostic report exceeds size bound')
    fd, name = tempfile.mkstemp(prefix='.capability-', suffix='.json', dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as file:
            _regular_single_link(temp)
            file.write(encoded)
            file.flush()
            os.fsync(file.fileno())
        _regular_single_link(temp)
        _artifact_path(path, repository_root)
        if os.name == 'nt':
            # Windows rename refuses an existing destination, unlike POSIX rename.
            os.rename(temp, path)
        else:
            os.link(temp, path, follow_symlinks=False)  # exclusive destination on POSIX
            temp.unlink()
        _regular_single_link(path)
    finally:
        if os.path.lexists(temp):
            _regular_single_link(temp)
            temp.unlink()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate diagnostic JSON key')
        result[key] = value
    return result


def read_native_report(path: Path, *, repository_root: Path) -> dict:
    """Read only one bounded regular report; no save-file or link traversal."""
    path = _artifact_path(path, repository_root)
    before = _regular_single_link(path)
    if before.st_size > 8 * 1024 * 1024:
        raise ValueError('native diagnostic report exceeds size bound')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags), 'rb') as file:
        opened = os.fstat(file.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_nlink) != (before.st_dev, before.st_ino, 1):
            raise ValueError('native diagnostic file changed before read')
        data = file.read(8 * 1024 * 1024 + 1)
    after = _regular_single_link(path)
    if len(data) > 8 * 1024 * 1024 or (after.st_dev, after.st_ino, after.st_size) != (before.st_dev, before.st_ino, before.st_size):
        raise ValueError('native diagnostic file changed during read')
    result = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('non-finite JSON value')))
    if not isinstance(result, dict):
        raise ValueError('native diagnostic report is not an object')
    return result


def _cleanup_verified(result: dict) -> bool:
    if (result.get('passed') is not True or result.get('sources_untouched') is not True or
            result.get('saves_match_backup_after_restore') is not True or
            any(result.get(k) for k in ('safety_error', 'registry_error', 'restoration_skipped',
                                       'registry_restore_skipped', 'save_restore_errors', 'stop_errors'))):
        return False
    changes, restored = result.get('registry_changes'), result.get('registry_restore')
    if not isinstance(changes, dict) or set(changes) != {'SR2'} or not isinstance(restored, dict):
        return False
    delta = changes['SR2']
    return (isinstance(delta, dict) and all(isinstance(delta.get(k), list) for k in
            ('removed_or_changed', 'added_or_changed')) and
            (not delta['removed_or_changed'] and not delta['added_or_changed'] or restored.get('SR2') == 'reimported') and
            all(value == 'reimported' for value in restored.values()))


def run_diagnostic(*, current_user_authorized: bool, timeout_s: float = 240) -> dict:
    """Explicitly authorized protected SR2 menu oracle. Do not call before review/approval."""
    if current_user_authorized is not True:
        raise PermissionError('--current-user-authorized is required before any artifact or game launch')
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 900:
        raise ValueError('diagnostic timeout must be finite and within (0, 900] seconds')
    repository_root = oracle.REPO_ROOT
    exe = validate_lab_destination(oracle.GAMES['SR2'], oracle.LAB)
    if not exe.is_file():
        raise RuntimeError('verified SR2 lab executable is missing')
    run_id = secrets.token_hex(16)
    root = repository_root / 'Lab/RuntimeArtifacts'
    work = root / ('counterpart-capability-' + run_id)
    native = _artifact_path(work / 'native-diagnostic.json', repository_root)
    final = _artifact_path(work / 'capability-result.json', repository_root)
    refuse_reparse(root)
    if not root.parent.is_dir():
        raise ValueError('verified Lab parent is missing')
    root.mkdir(exist_ok=True)
    refuse_reparse(root)
    work.mkdir(exist_ok=False)
    refuse_reparse(work)
    evidence = {'status': 'DIAGNOSTIC_ONLY', 'creation_authorized': False, 'passed': False,
                'cleanup_verified': False, 'diagnostic': None, 'report_path': str(final)}

    def scenario(host, beats, pids, procs=None):
        if set(pids) != {'SR2'} or procs is None or set(procs) != {'SR2'}:
            raise RuntimeError('diagnostic requires exactly its owned SR2 process')
        pid = pids['SR2']
        if not _integer(pid, 1, 2**31 - 1) or procs['SR2'].pid != pid:
            raise RuntimeError('owned diagnostic PID mismatch')
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            process_guard.assert_no_other_game_processes([pid])
            if not process_guard.same_image(pid, exe):
                raise RuntimeError('owned SR2 process image mismatch')
            if procs['SR2'].poll() is not None:
                raise RuntimeError('owned SR2 exited before diagnostic publication')
            if os.path.lexists(native):
                report = read_native_report(native, repository_root=repository_root)
                checked = validate_capability_report(report, expected_pid=pid, expected_run_id=run_id)
                evidence['diagnostic'] = checked
                return {'passed': checked['diagnostic_ready'], 'status': 'DIAGNOSTIC_ONLY',
                        'creation_authorized': False}
            time.sleep(0.25)
        raise TimeoutError('bounded native menu diagnostic was not published')

    try:
        result = oracle.run(['SR2'], timeout_s, False, work, require_status=False,
                            current_user=True, diag_saves=False, sr2_save_id=None, sr1_save_id=None,
                            guard_realms=['SR2'], scenario=scenario, extra_env={'SR2': {
                                'SLIMEBRIDGE_LAB_EXE': str(exe.resolve()),
                                'SLIMEBRIDGE_LAB_COUNTERPART_DIAGNOSTIC': '1',
                                'SLIMEBRIDGE_LAB_COUNTERPART_OUTPUT': str(native),
                                'SLIMEBRIDGE_LAB_COUNTERPART_RUN_ID': run_id,
                                'SLIMEBRIDGE_AUTOLOAD': '', 'SLIMEBRIDGE_LAB_SAVE_ID': '',
                                'SLIMEBRIDGE_LAB_GIVE': '0', 'SLIMEBRIDGE_LAB_CREATE': '0',
                                'SLIMEBRIDGE_LAB_DIAG_SAVES': '0', 'SLIMEBRIDGE_OP_JOURNAL': '',
                            }})
        evidence['cleanup_verified'] = _cleanup_verified(result)
        evidence['passed'] = (evidence['cleanup_verified'] and isinstance(evidence['diagnostic'], dict) and
                              evidence['diagnostic']['diagnostic_ready'] is True)
        evidence['oracle_scenario_status'] = result.get('scenario')
    except Exception as ex:
        # The protected oracle owns stop/backup restoration, including on exceptions.
        evidence['error_code'] = type(ex).__name__
    publish_json_exclusive(final, evidence, repository_root=repository_root)
    return evidence


def main():
    parser = argparse.ArgumentParser(description='Opt-in READ-ONLY SR2 menu capability diagnostic; no creation/port proof')
    parser.add_argument('--current-user-authorized', action='store_true', required=True,
                        help='Owner authorizes protected SR2-only oracle with verified backup/fingerprint/PID/restore')
    parser.add_argument('--timeout', type=float, default=240)
    args = parser.parse_args()
    result = run_diagnostic(current_user_authorized=args.current_user_authorized, timeout_s=args.timeout)
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
