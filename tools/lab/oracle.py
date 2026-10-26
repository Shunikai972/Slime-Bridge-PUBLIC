"""First real oracle: both lab games load their SlimeBridge plugin and heartbeat ONE BridgeHost.

Safety: launches only executables inside Lab/Games, verifies every heartbeat PID resolves to the
exact lab executable, and cleans up only process handles this script started (never by image name).
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from bridge.host.server import BridgeHost
from tools.lab.process_guard import process_image_path, same_image, assert_no_other_game_processes
from tools.lab.paths import lexical_path, refuse_reparse, validate_lab_destination
from tools.lab.lab_identity import require_isolated_profile
from tools.lab import copy_game, save_guard

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB = REPO_ROOT / 'Lab' / 'Games'
GAMES = {
    'SR1': LAB / 'SR1' / 'SlimeRancher.exe',
    'SR2': LAB / 'SR2' / 'SlimeRancher2.exe',
}
WINDOW_ARGS = ['-screen-fullscreen', '0', '-screen-width', '1280', '-screen-height', '720']


def heartbeat_matches_launched(beat: dict | None, proc: subprocess.Popen, expected: Path) -> bool:
    """Do not accept another lab instance or an already-exited process as oracle proof."""
    return (bool(beat) and proc.poll() is None and beat.get('pid') == proc.pid
            and same_image(proc.pid, expected))


def loaded_status_ok(beat: dict | None) -> bool:
    """Phase-94 proof needs an in-game status, not just a process heartbeat."""
    status = (beat or {}).get('status')
    return (isinstance(status, dict) and status.get('player_alive') is True and bool(status.get('save_id'))
            and isinstance(status.get('pink_slime_count'), int) and isinstance(status.get('pink_plort_count'), int))


def source_fingerprints(realms: list[str], lab_root: Path | None = None) -> dict:
    """Re-hash the ORIGINAL installs' key files recorded in each lab manifest (read-only)."""
    lab_root = lab_root or LAB
    result = {}
    for realm in realms:
        manifest = json.loads((lab_root / realm / 'slimebridge-lab.json').read_text(encoding='utf-8'))
        source = Path(manifest['source'])
        result[realm] = {'source': str(source), 'expected': manifest['sha256'],
                         'actual': copy_game._fingerprints(source, list(manifest['sha256']))}
    return result


def lab_fingerprints(realms: list[str]) -> dict:
    """Hash only the manifest's key files in the actual LAB, never game-generated saves."""
    result = {}
    for realm in realms:
        manifest_path = validate_lab_destination(LAB / realm / 'slimebridge-lab.json', LAB)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        names = list(manifest['sha256'])
        if not names or any(not isinstance(name, str) or not name for name in names):
            raise RuntimeError(f'{realm} lab manifest has no valid fingerprint targets')
        base = GAMES[realm].parent
        for name in names:
            target = validate_lab_destination(base / name, LAB)
            if not target.is_file() or base.resolve() not in target.resolve().parents:
                raise RuntimeError(f'{realm} lab fingerprint target is missing or escapes its root')
        result[realm] = copy_game._fingerprints(base, names)
    return result


def _implant_rel_ok(rel: str) -> bool:
    """A save-relative path only: non-empty, no drive, no root, no parent traversal.

    ``:`` is rejected everywhere: on NTFS it opens an alternate data stream on an existing
    file, which the default-stream hash/restore inventory would neither see nor remove.
    """
    if not isinstance(rel, str) or ':' in rel:
        return False
    path = Path(rel)
    return bool(path.parts) and not path.drive and not path.root and not path.is_absolute() \
        and '..' not in path.parts


def _implant_saves(specs: list[dict]) -> list[dict]:
    """Copy pinned Bridge-owned test saves into the account between snapshot and launch.

    Only runs inside current-user mode, after the backup snapshot: the restore pass then sees
    each implant as a post-snapshot addition, preserves its post-run bytes under the snapshot
    and moves it out of the live tree, so the account returns to its true pre-run state.
    A target that already exists is never overwritten, and the target path is re-validated as a
    lexically-contained save-relative path so it can never escape the realm save root that the
    restore pass scans.
    """
    low = save_guard.local_low()
    done = []
    for spec in specs:
        source = Path(spec['source'])
        if not source.is_absolute() or not source.is_file():
            raise RuntimeError(f'implant source missing: {source}')
        rel = spec['rel']
        if not _implant_rel_ok(rel):
            raise RuntimeError(f'implant rel is not a save-relative path: {rel!r}')
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != spec['sha256']:
            raise RuntimeError(f'implant source does not match its pinned digest: {source}')
        root = low / save_guard.SAVE_DIRS[spec['realm']]
        target = root / rel
        if lexical_path(root) not in lexical_path(target).parents:
            raise RuntimeError(f'implant target escapes the {spec["realm"]} save root: {target}')
        refuse_reparse(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise RuntimeError(f'implant target already exists: {target}')
        with target.open('xb') as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if hashlib.sha256(target.read_bytes()).hexdigest() != spec['sha256']:
            raise RuntimeError(f'implanted save failed readback verification: {target}')
        print(f'[oracle] implanted {spec["realm"]} save {spec["rel"]} sha256={spec["sha256"]}', flush=True)
        done.append({'realm': spec['realm'], 'target': str(target), 'rel': spec['rel'],
                     'sha256': spec['sha256'], 'bytes': len(data)})
    return done


def run(realms: list[str], timeout_s: float, keep_running: bool, evidence_dir: Path,
        require_status: bool = False, current_user: bool = False, diag_saves: bool = False,
        sr2_save_id: str | None = None, sr1_save_id: str | None = None,
        scenario=None, extra_env: dict | None = None, guard_realms: list[str] | None = None,
        creation_realms: tuple[str, ...] = ('SR1',), transfer_authority: dict | None = None,
        implant: list[dict] | None = None) -> dict:
    """SR2 native creation is explicit opt-in, after both profile roots are guarded.

    current_user=True is the owner-authorized backup/compare/restore mode. Otherwise the
    separate Windows profile gate must pass. Scenario success is provisional until cleanup.

    ``transfer_authority`` arms the bound transfer path: the host resolves every native command
    against the durable ledger and a linked profile whose live endpoints must be attested by the
    real provider. Once the owned children are launched, the same dict is filled with
    ``_runtime`` (profile id, endpoints, builds, provider handles and the shared ledger store)
    for the scenario closure.

    ``implant`` copies pinned Bridge-owned test saves into the current-user account between the
    backup snapshot and launch; the restore pass captures them as additions and moves them back
    out, so the account returns to its true pre-run state.
    """
    if not realms or len(realms) != len(set(realms)) or any(realm not in GAMES for realm in realms):
        raise ValueError('Each supported realm must be requested exactly once')
    protected_realms = list(realms if guard_realms is None else guard_realms)
    if (len(protected_realms) != len(set(protected_realms)) or not set(realms) <= set(protected_realms) or
            any(realm not in GAMES for realm in protected_realms)):
        raise ValueError('Guarded realms must include every launched supported realm')
    if (not isinstance(creation_realms, (tuple, list)) or len(creation_realms) != len(set(creation_realms)) or
            any(realm not in ('SR1', 'SR2') for realm in creation_realms)):
        raise ValueError('Creation realms must be distinct supported realms')
    creation_realms = tuple(creation_realms)
    if transfer_authority is not None:
        if not isinstance(transfer_authority, dict):
            raise ValueError('transfer authority must be a mapping')
        for key in ('profiles_path', 'journal_path', 'store_path', 'profile_id', 'trust'):
            if key not in transfer_authority:
                raise ValueError(f'transfer authority is missing {key!r}')
        if (not isinstance(transfer_authority['profile_id'], str) or
                re.fullmatch(r'[0-9a-f]{32}', transfer_authority['profile_id']) is None):
            raise ValueError('transfer authority profile id must be a 32-hex profile id')
        trust = transfer_authority['trust']
        if (not isinstance(trust, dict) or not trust or
                any(not isinstance(key, str) or not key or value not in ('SR1', 'SR2')
                    for key, value in trust.items())):
            raise ValueError('transfer authority trust must map provider ids to realms')
        if type(transfer_authority.get('allow_lab_give', False)) is not bool:
            raise ValueError('transfer authority allow_lab_give must be boolean')
        # Everything below must fail BEFORE the backup snapshot, implant or any db/journal file
        # creation: malformed parameters never get to touch shared state.
        try:
            profiles_path = Path(transfer_authority['profiles_path'])
        except TypeError as exc:
            raise ValueError('transfer authority profiles path is invalid') from exc
        if not profiles_path.is_file():
            raise ValueError('transfer authority profiles database does not exist')
        base = lexical_path(Path(evidence_dir))
        for key in ('journal_path', 'store_path'):
            try:
                candidate = lexical_path(Path(transfer_authority[key]))
            except TypeError as exc:
                raise ValueError(f'transfer authority {key} is invalid') from exc
            if base not in candidate.parents:
                raise ValueError(f'transfer authority {key} must stay inside the run evidence directory')
        from tools.lab.endpoint_attestation import read_linked_pair, validate_run_write_paths
        validate_run_write_paths(transfer_authority)
        transfer_authority['_endpoints'] = read_linked_pair(profiles_path,
                                                            transfer_authority['profile_id'])['endpoints']
    if implant is not None:
        if not current_user:
            raise ValueError('save implants require current-user authorization')
        if not isinstance(implant, list) or not implant:
            raise ValueError('implant must be a non-empty list of pinned save specifications')
        for spec in implant:
            if (not isinstance(spec, dict) or set(spec) != {'source', 'realm', 'rel', 'sha256'} or
                    spec.get('realm') not in ('SR1', 'SR2') or
                    any(not isinstance(spec.get(key), str) or not spec[key]
                        for key in ('source', 'rel', 'sha256')) or
                    re.fullmatch(r'[0-9a-f]{64}', spec['sha256']) is None or
                    not _implant_rel_ok(spec['rel'])):
                raise ValueError('invalid implant specification')
    if 'SR2' in creation_realms and set(protected_realms) != {'SR1', 'SR2'}:
        raise ValueError('SR2 creation requires both source and target roots protected')
    protected_creation = ('SR2' in creation_realms or
                          (scenario is not None and set(protected_realms) == {'SR1', 'SR2'}))
    if protected_creation and keep_running:
        raise ValueError('Protected creation must stop both games before cleanup')
    if diag_saves and (not require_status or 'SR2' not in realms):
        raise ValueError('Save diagnostics require a loaded-save SR2 oracle')
    for realm, save_id in (('SR1', sr1_save_id), ('SR2', sr2_save_id)):
        if save_id is not None and (not require_status or realm not in realms or
                                    type(save_id) is not str or
                                    re.fullmatch(r'[A-Za-z0-9_-]{1,128}', save_id) is None):
            raise ValueError(f'Targeted {realm} load requires --require-status and a valid native save group ID')
    for realm in protected_realms:
        exe = validate_lab_destination(GAMES[realm], LAB)
        if not exe.is_file():
            raise RuntimeError(f'{realm} lab executable missing: {exe}')
    evidence_dir.mkdir(parents=True, exist_ok=True)
    result = {'started_utc': datetime.now(timezone.utc).isoformat(), 'realms': {}, 'passed': False,
              'creation_realms': list(creation_realms), 'guarded_realms': protected_realms,
              'cleanup_completed': False,
              'profile_mode': 'current-user-authorized' if current_user else 'isolated'}
    snap = None
    if current_user or protected_creation:
        assert_no_other_game_processes()
        for realm in protected_realms:
            manifest = json.loads((LAB / realm / 'slimebridge-lab.json').read_text(encoding='utf-8'))
            source = Path(manifest['source']).resolve()
            exe = GAMES[realm].resolve()
            if exe == source or source in exe.parents:
                raise RuntimeError(f'{realm} lab executable resolves inside its original install')
        result['sources_before'] = source_fingerprints(protected_realms)
        if any(v['actual'] != v['expected'] for v in result['sources_before'].values()):
            raise RuntimeError('Original install fingerprints differ from lab manifest; refusing to launch')
    if protected_creation:
        result['lab_fingerprints_before'] = lab_fingerprints(protected_realms)
    if current_user:
        if keep_running:
            raise ValueError('Cannot keep games running while restoring shared current-user saves')
        snap = save_guard.snapshot(protected_realms, 'oracle-pre')
        result['save_snapshot'] = str(snap)
        print(f'[oracle] current-user mode: saves backed up to {snap}', flush=True)
    else:
        require_isolated_profile(protected_realms)
    try:
        if current_user:
            # This check is INSIDE the restore-finally, including a race during backup/export.
            assert_no_other_game_processes()
        if implant:
            result['implanted_saves'] = _implant_saves(implant)
        return _run_games(realms, timeout_s, keep_running, evidence_dir, require_status, current_user, result,
                          diag_saves, sr2_save_id, sr1_save_id, scenario, extra_env,
                          creation_realms=creation_realms, transfer_authority=transfer_authority)
    except BaseException as ex:
        result['passed'] = False
        result['error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        try:
            if snap is not None:
                _finalize_current_user(result, snap, protected_realms, evidence_dir)
            if protected_creation:
                after = lab_fingerprints(protected_realms)
                result['lab_fingerprints_after'] = after
                result['lab_fingerprints_unchanged'] = after == result['lab_fingerprints_before']
                if not result['lab_fingerprints_unchanged']:
                    result['passed'] = False
                if not current_user:
                    result['sources_after'] = source_fingerprints(protected_realms)
                    result['sources_untouched'] = result['sources_after'] == result['sources_before']
                    if not result['sources_untouched']:
                        result['passed'] = False
            result['cleanup_completed'] = not any(result.get(key) for key in
                ('stop_errors', 'safety_error', 'registry_error', 'restoration_skipped',
                 'registry_restore_skipped', 'save_restore_errors'))
        except BaseException as ex:
            result['passed'] = False
            result['cleanup_error'] = f'{type(ex).__name__}: {ex}'
            raise
        finally:
            _write_evidence(result, evidence_dir)


def _finalize_current_user(result: dict, snap: Path, realms: list[str], evidence_dir: Path) -> None:
    """Preserve evidence and restore files even if registry inspection fails."""
    try:
        report = save_guard.compare(snap)
        result['save_changes'] = report
        changes = None
        try:
            changes = save_guard.registry_changes(snap)
            result['registry_changes'] = changes
        except Exception as ex:
            # HKCU is not safe to modify without post-run evidence, but independent
            # native save files still need their verified rollback.
            result['registry_error'] = f'{type(ex).__name__}: {ex}'
            result['passed'] = False
        try:
            assert_no_other_game_processes()
        except RuntimeError as ex:
            result['restoration_skipped'] = str(ex)
            result['passed'] = False
        else:
            try:
                result['save_restore'] = save_guard.restore_originals(snap, report)
            except save_guard.RestoreIncomplete as ex:
                # Individual file failures: every other file was attempted. Games are verified
                # stopped, so the independent HKCU rollback still runs below.
                result['save_restore'] = ex.actions
                result['save_restore_errors'] = ex.errors
                result['passed'] = False
            if changes is not None:
                try:
                    assert_no_other_game_processes()
                except RuntimeError as ex:
                    result['registry_restore_skipped'] = str(ex)
                    result['passed'] = False
                else:
                    result['registry_restore'] = save_guard.restore_registry(snap, changes)
        result['sources_after'] = source_fingerprints(realms)
        result['sources_untouched'] = all(v['actual'] == v['expected'] for v in result['sources_after'].values())
        after = save_guard.compare(snap)
        result['saves_match_backup_after_restore'] = all(
            not [f for f in r['modified'] + r['removed'] + r['added'] if not f.lower().endswith('.log')]
            for r in after.values())
        if (not result['sources_untouched'] or not result['saves_match_backup_after_restore']
                or result.get('restoration_skipped') or result.get('registry_restore_skipped')
                or result.get('save_restore_errors') or result.get('stop_errors')
                or any(state not in ('reimported', 'removed-new-key') for state in result.get('registry_restore', {}).values())):
            result['passed'] = False
    except Exception as ex:
        result['passed'] = False
        result['safety_error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        _write_evidence(result, evidence_dir)


def _run_games(realms, timeout_s, keep_running, evidence_dir, require_status, current_user, result,
               diag_saves=False, sr2_save_id=None, sr1_save_id=None, scenario=None, extra_env=None,
               creation_realms=('SR1',), transfer_authority=None) -> dict:
    requested_saves: dict[str, str | None] = {'SR1': sr1_save_id, 'SR2': sr2_save_id}
    started: dict[str, subprocess.Popen] = {}
    host_kwargs: dict = {}
    transfer_runtime = None
    if transfer_authority is not None:
        from bridge.transfer.binding import TransferBindingResolver
        from bridge.transfer.store import TransferStore
        from tools.lab import endpoint_attestation
        profiles_path = Path(transfer_authority['profiles_path'])
        profile_id = transfer_authority['profile_id']
        # Preflight refused links/hardlinks before any side effect; re-validate the write paths
        # here right before the store/journal files are actually created (defense against a link
        # added in between), so nothing can redirect the databases outside the evidence dir.
        try:
            endpoint_attestation.validate_run_write_paths(transfer_authority)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        # run() preflights and caches the read-only endpoints; recompute when called directly.
        endpoints = transfer_authority.get('_endpoints')
        if endpoints is None:
            endpoints = endpoint_attestation.read_linked_pair(profiles_path, profile_id)['endpoints']
        builds = endpoint_attestation.canonical_build_fingerprints(['SR1', 'SR2'])
        handles = endpoint_attestation.LabRuntimeHandles()
        provider = endpoint_attestation.LiveEndpointAttestation(handles, endpoints, builds)
        resolver = TransferBindingResolver(profiles_path, Path(transfer_authority['journal_path']),
                                           current_builds=lambda: dict(builds))
        store = TransferStore(Path(transfer_authority['store_path']))
        host_kwargs = dict(transfer_store=store, transfer_bindings=resolver,
                           endpoint_provider=provider,
                           endpoint_trust=dict(transfer_authority['trust']),
                           allow_lab_give=bool(transfer_authority.get('allow_lab_give')))
        transfer_runtime = {'profile_id': profile_id, 'endpoints': endpoints, 'builds': builds,
                            'handles': handles, 'store': store, 'provider': provider,
                            'journal_path': Path(transfer_authority['journal_path']),
                            'store_path': Path(transfer_authority['store_path'])}
        transfer_authority['_runtime'] = transfer_runtime
    with BridgeHost(creation_realms=creation_realms, **host_kwargs) as host:
        env = dict(os.environ, SLIMEBRIDGE_URL=host.url)
        # Never inherit a previous oracle's lab autoload markers into an ordinary run.
        env.pop('SLIMEBRIDGE_AUTOLOAD', None)
        env.pop('SLIMEBRIDGE_LAB_EXE', None)
        env.pop('SLIMEBRIDGE_LAB_DIAG_SAVES', None)
        env.pop('SLIMEBRIDGE_LAB_SAVE_ID', None)
        env.pop('SLIMEBRIDGE_LAB_GIVE', None)
        env.pop('SLIMEBRIDGE_LAB_CREATE', None)
        env.pop('SLIMEBRIDGE_OP_JOURNAL', None)
        if require_status:
            env['SLIMEBRIDGE_AUTOLOAD'] = 'continue'
        try:
            for realm in realms:
                if current_user:
                    assert_no_other_game_processes(proc.pid for proc in started.values())
                exe = validate_lab_destination(GAMES[realm], LAB)
                if not exe.is_file():
                    raise RuntimeError(f'{realm} lab executable missing: {exe}')
                launch_env = dict(env)
                if require_status:
                    # A mere inherited AUTOLOAD flag must not load a normal source install.
                    launch_env['SLIMEBRIDGE_LAB_EXE'] = str(exe.resolve())
                if diag_saves and realm == 'SR2':
                    launch_env['SLIMEBRIDGE_LAB_DIAG_SAVES'] = '1'
                requested_save_id = requested_saves[realm]
                if requested_save_id is not None:
                    launch_env['SLIMEBRIDGE_LAB_SAVE_ID'] = requested_save_id
                for key, value in ((extra_env or {}).get(realm) or {}).items():
                    if not key.startswith('SLIMEBRIDGE_'):
                        raise ValueError('extra_env may only set SLIMEBRIDGE_* variables')
                    if key == 'SLIMEBRIDGE_TOKEN':
                        raise ValueError('extra_env cannot replace the realm credential')
                    launch_env[key] = str(value)
                launch_env['SLIMEBRIDGE_TOKEN'] = host.realm_token(realm)
                started[realm] = subprocess.Popen([str(exe), *WINDOW_ARGS], cwd=str(exe.parent), env=launch_env)
                launched_image = process_image_path(started[realm].pid)
                result['realms'][realm] = {'launched_pid': started[realm].pid, 'launched_image': str(launched_image),
                                           'launch_time': time.time()}
                print(f'[oracle] launched {realm} pid={started[realm].pid} image={launched_image}', flush=True)
            if transfer_runtime is not None:
                # Providers only attest live state; they fail closed until these handles exist.
                transfer_runtime['handles'].procs = started
                transfer_runtime['handles'].host = host
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                if current_user:
                    assert_no_other_game_processes(proc.pid for proc in started.values())
                beats = host.heartbeats()
                for realm, proc in started.items():
                    if proc.poll() is not None and realm not in beats:
                        result['realms'][realm]['exited_early'] = proc.returncode
                if all((r in beats and (not require_status or
                                         (loaded_status_ok(beats[r]) and
                                          (requested_saves[r] is None or
                                           beats[r]['status']['save_id'] == requested_saves[r]))))
                       or 'exited_early' in result['realms'][r] for r in realms):
                    break
                time.sleep(1)
            beats = host.heartbeats()
            ok = True
            for realm in realms:
                info = result['realms'][realm]
                beat = beats.get(realm)
                info['heartbeat'] = beat
                if beat:
                    info['seconds_to_first_heartbeat'] = round(beat['first_received_unix'] - info['launch_time'], 1)
                    pid = beat.get('pid')
                    info['heartbeat_pid_image'] = str(process_image_path(pid)) if pid else None
                    info['pid_matches_lab_exe'] = bool(pid) and same_image(pid, GAMES[realm])
                    info['pid_matches_launched'] = pid == started[realm].pid
                ok = ok and heartbeat_matches_launched(beat, started[realm], GAMES[realm])
                if require_status:
                    info['loaded_status_ok'] = loaded_status_ok(beat)
                    ok = ok and info['loaded_status_ok']
                requested_save_id = requested_saves[realm]
                if requested_save_id is not None:
                    info['requested_save_id'] = requested_save_id
                    status = beat.get('status') if beat else None
                    info['requested_save_loaded'] = bool(info.get('loaded_status_ok') and
                                                         isinstance(status, dict) and
                                                         status.get('save_id') == requested_save_id)
                    ok = ok and info['requested_save_loaded']
            result['require_status'] = require_status
            if scenario is not None:
                if ok:
                    try:
                        result['scenario'] = scenario(host, {r: beats[r] for r in realms},
                                                      {r: started[r].pid for r in realms}, procs=started)
                    except Exception as ex:
                        result['scenario'] = {'passed': False, 'error': f'{type(ex).__name__}: {ex}'}
                else:
                    result['scenario'] = {'passed': False, 'error': 'games did not reach loaded status'}
                ok = ok and result['scenario'].get('passed') is True
            result['passed'] = ok
            if keep_running:
                # Closing the context would strand live games without their BridgeHost. Keep
                # serving until every launched game exits (or the operator interrupts).
                _write_evidence(result, evidence_dir)
                print('[oracle] keep-running: BridgeHost stays up until the lab games exit', flush=True)
                try:
                    while any(proc.poll() is None for proc in started.values()):
                        time.sleep(1)
                except KeyboardInterrupt:
                    keep_running = False
        finally:
            if not keep_running:
                stop_errors = _stop_owned(started)
                if stop_errors:
                    result['stop_errors'] = stop_errors
                    result['passed'] = False
    result['finished_utc'] = datetime.now(timezone.utc).isoformat()
    _write_evidence(result, evidence_dir)
    if result.get('stop_errors'):
        # The caller's current-user restoration re-checks for live games and refuses on its own.
        raise RuntimeError(f"could not stop owned game(s): {result['stop_errors']}")
    return result


def _require_control_image(realm: str, proc: subprocess.Popen) -> None:
    image = process_image_path(proc.pid)
    if image is None or os.path.normcase(str(image)) != os.path.normcase(str(GAMES[realm].resolve())):
        raise RuntimeError('owned child PID no longer matches its verified LAB executable')


def _stop_owned(started: dict) -> dict[str, str]:
    """Attempt to stop EVERY owned child; one failure must not strand the others.

    Unknown or mismatched PID images fail closed even with a retained Popen handle.
    Independently verified siblings are still attempted after an individual failure.
    """
    errors = {}
    for realm, proc in started.items():
        try:
            if proc.poll() is None:
                # A retained handle does not replace the exact PID + LAB image prerequisite.
                _require_control_image(realm, proc)
                try:
                    proc.terminate()
                    proc.wait(timeout=20)
                except (OSError, subprocess.TimeoutExpired):
                    if proc.poll() is None:
                        _require_control_image(realm, proc)
                        proc.kill()
                    proc.wait(timeout=20)
                print(f'[oracle] stopped {realm} pid={proc.pid}', flush=True)
        except Exception as ex:
            errors[realm] = f'{type(ex).__name__}: {ex}'
            print(f'[oracle] FAILED to stop {realm} pid={proc.pid}: {errors[realm]}', flush=True)
    return errors


def _write_evidence(result: dict, evidence_dir: Path) -> None:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    saved = dict(result)
    if result.get('cleanup_completed') is not True:
        saved['passed'] = False
        saved['validation_scope'] = 'awaiting_cleanup'
    (evidence_dir / f'oracle-{stamp}.json').write_text(json.dumps(saved, indent=2, default=str), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description='SlimeBridge first oracle: both games -> one BridgeHost')
    parser.add_argument('--realm', action='append', choices=sorted(GAMES), help='Default: both')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--keep-running', action='store_true')
    parser.add_argument('--require-status', action='store_true',
                        help='Phase 94: pass only with a loaded-save status (player alive + save ID)')
    parser.add_argument('--diag-saves', action='store_true',
                        help='SR2 lab-only read-only menu save-service diagnostic; requires --require-status')
    parser.add_argument('--sr1-save-id',
                        help='Lab-only: load exactly this existing SR1 native save group, not Continue')
    parser.add_argument('--sr2-save-id',
                        help='Lab-only: load exactly this existing SR2 native save group, not Continue')
    parser.add_argument('--current-user-authorized', action='store_true',
                        help='Owner-authorized: run under this account with save backup/compare/restore')
    args = parser.parse_args()
    realms = args.realm or ['SR1', 'SR2']
    if args.current_user_authorized and args.keep_running:
        parser.error('--keep-running is not allowed in current-user mode (saves are restored after exit)')
    if args.diag_saves and (not args.require_status or 'SR2' not in realms):
        parser.error('--diag-saves requires --require-status and SR2')
    for realm, save_id in (('SR1', args.sr1_save_id), ('SR2', args.sr2_save_id)):
        if save_id is not None and (not args.require_status or realm not in realms or
                                    re.fullmatch(r'[A-Za-z0-9_-]{1,128}', save_id) is None):
            parser.error(f'--{realm.lower()}-save-id requires --require-status, {realm}, and a valid native save group ID')
    result = run(realms, args.timeout, args.keep_running, REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'oracle',
                 require_status=args.require_status, current_user=args.current_user_authorized,
                 diag_saves=args.diag_saves, sr2_save_id=args.sr2_save_id, sr1_save_id=args.sr1_save_id)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
