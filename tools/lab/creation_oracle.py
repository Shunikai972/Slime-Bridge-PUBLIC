"""Create/link a counterpart from one loaded source in the protected, two-realm LAB.

Defaults remain SR2 -> SR1. Both save roots and original/Lab fingerprints are guarded before
launch; the later target child is immediately registered for cleanup. New native test saves
are preserved as post-run evidence, not left in the owner's profile. Synthetic orchestration
tests do not establish native runtime proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from bridge.counterpart.legacy_quarantine import LegacyQuarantine
from bridge.counterpart.policy import decide as decide_counterpart
from bridge.profile import native_proof
from bridge.profile.store import ProfileStore
from bridge.transfer.coordinator import run_command
from tools.lab import oracle, save_guard
from tools.lab.native_storage_binding import assert_same_binding, read_binding_log
from tools.lab.paths import refuse_reparse, validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes, process_image_path


def installation_id(realm: str) -> str:
    path = str(oracle.GAMES[realm].resolve()).casefold()
    return realm.lower() + '-lab-' + hashlib.sha256(path.encode()).hexdigest()


def _live_query(host, realm: str, save_id: str, pid: int, op_id: str, timeout: float) -> dict:
    result = run_command(host, realm, 'query', 'slime.pink', 0, op_id, save_id, timeout)
    expected = {'realm': realm, 'op_id': op_id, 'kind': 'query', 'item': 'slime.pink',
                'save_id': save_id, 'pid': pid}
    if (result is None or result.get('ok') is not True or
            any(result.get(k) != v for k, v in expected.items()) or
            type(result.get('before')) is not int or result['before'] < 0):
        raise RuntimeError(f'{realm} native loaded-world query did not match its process and save')
    return result


def _launch_target(host, work: Path, procs: dict, realm: str) -> tuple[subprocess.Popen, Path]:
    assert_no_other_game_processes(proc.pid for proc in procs.values())
    exe = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
    if realm in procs:
        raise RuntimeError('counterpart target already has an owned process')
    env = {k: v for k, v in os.environ.items() if not k.startswith('SLIMEBRIDGE_')}
    env.update(SLIMEBRIDGE_URL=host.url, SLIMEBRIDGE_TOKEN=host.realm_token(realm),
               SLIMEBRIDGE_LAB_EXE=str(exe.resolve()), SLIMEBRIDGE_LAB_CREATE='1',
               SLIMEBRIDGE_OP_JOURNAL=str(work / (realm + '-ops.journal')))
    proc = subprocess.Popen([str(exe), *oracle.WINDOW_ARGS], cwd=str(exe.parent), env=env)
    # Register before any fallible check: cleanup owns the original process handle.
    procs[realm] = proc
    image = process_image_path(proc.pid)
    if image is None or os.path.normcase(str(image)) != os.path.normcase(str(exe.resolve())):
        raise RuntimeError(f'automatic counterpart child is not the verified {realm} LAB executable')
    print(f'[creation] auto-launched {realm} pid={proc.pid} image={image}', flush=True)
    return proc, exe


def _require_owned_alive(procs: dict, realm: str) -> None:
    proc = procs[realm]
    if proc.poll() is not None or not oracle.same_image(proc.pid, oracle.GAMES[realm]):
        raise RuntimeError(f'{realm} is no longer the live owned LAB process')


def publish_creation_evidence(work: Path, evidence: dict | None, *, overall_passed: bool | None = None,
                              oracle_status: dict | None = None, cleanup_completed: bool = False) -> None:
    """Native success is not overall success until current-account cleanup has returned."""
    if evidence is None:
        pending = work / 'creation-result.json'
        if pending.is_file():
            pending_evidence = json.loads(pending.read_text(encoding='utf-8'))
            if not isinstance(pending_evidence, dict):
                raise ValueError('creation evidence must be an object')
            evidence = pending_evidence
            native_passed = evidence.get('native_sequence_passed') is True
        else:
            evidence, native_passed = {}, False
    else:
        native_passed = evidence.get('passed') is True
    saved = {**evidence, 'native_sequence_passed': native_passed,
             'passed': native_passed and overall_passed is True and cleanup_completed is True,
             'cleanup_completed': cleanup_completed is True,
             'validation_scope': 'complete_oracle' if cleanup_completed is True else 'awaiting_cleanup'}
    if oracle_status is not None:
        saved['oracle_scenario_status'] = oracle_status
    (work / 'creation-result.json').write_text(json.dumps(saved, indent=2, default=str), encoding='utf-8')


def finalize_creation_evidence(work: Path, oracle_result: dict) -> None:
    """Publish overall status after cleanup without replacing the scenario's own evidence.

    When the scenario raises, the generic oracle stores only a {'passed', 'error'} stub; the
    scenario's own finally block already wrote the full evidence (creation id, native receipt).
    """
    scenario = oracle_result.get('scenario')
    status = ({k: scenario[k] for k in ('passed', 'error') if k in scenario}
              if isinstance(scenario, dict) else None)
    overall_passed = (oracle_result.get('passed') is True and
                      oracle_result.get('cleanup_completed') is True and
                      not any(oracle_result.get(key) for key in
                              ('stop_errors', 'safety_error', 'registry_error', 'cleanup_error',
                               'restoration_skipped', 'registry_restore_skipped', 'save_restore_errors')))
    publish_creation_evidence(work, None, overall_passed=overall_passed, oracle_status=status,
                              cleanup_completed=oracle_result.get('cleanup_completed') is True)


def legacy_quarantine() -> LegacyQuarantine:
    """Reviewed exact pins of the 13 individually audited historical creation folders."""
    return LegacyQuarantine()


def require_finalized_prior_creations(root: Path) -> None:
    """Never allocate a fresh creation ID while an interrupted lab invocation is unresolved.

    Completed lab runs intentionally restore their new saves, so their per-run profile database
    is evidence, not a live production profile. An interrupted run needs explicit recovery using
    its existing database/journal; rerunning this fresh-creation oracle is not recovery.
    """
    quarantine = legacy_quarantine()
    # Every pinned folder must be present and byte-identical; a partial set means move/rename/delete.
    quarantine.verify_root(root, require_all=True)
    for prior in sorted(root.glob('creation-*')):
        if quarantine.is_pinned(prior.name):
            # Exact audited evidence: UNRESOLVED_LEGACY_INTENT_QUARANTINED, never success or cleanup proof.
            # Its receipt is not rewritten; its creation ids are retired and can never be dispatched again.
            quarantine.verify_folder(prior)
            continue
        refuse_reparse(prior)
        if not prior.is_dir():
            continue
        receipt = prior / 'oracle-result.json'
        refuse_reparse(receipt)
        try:
            finalized = json.loads(receipt.read_text(encoding='utf-8'))
        except (OSError, ValueError) as ex:
            raise RuntimeError(f'Unfinished creation oracle requires existing-intent recovery: {prior}') from ex
        if not isinstance(finalized, dict):
            raise RuntimeError(f'Invalid creation evidence requires existing-intent recovery: {prior}')
        if not {'cleanup_completed', 'lab_fingerprints_unchanged'} <= finalized.keys():
            # Do not rewrite or silently promote historical receipts lacking these attestations.
            raise RuntimeError(f'Legacy or incomplete creation evidence requires existing-intent recovery: {prior}')
        restored = finalized.get('registry_restore', {})
        changes = finalized.get('registry_changes', {})
        registry_finalized = (set(restored) == {'SR1', 'SR2'} and
                              all(value == 'reimported' for value in restored.values()))
        if set(changes) == {'SR1', 'SR2'}:
            registry_finalized = all(
                isinstance(diff, dict) and
                all(isinstance(diff.get(key), list) for key in ('removed_or_changed', 'added_or_changed')) and
                (not diff['removed_or_changed'] and not diff['added_or_changed'] or
                 restored.get(realm) == 'reimported')
                for realm, diff in changes.items())
        if (finalized.get('sources_untouched') is not True or
                finalized.get('saves_match_backup_after_restore') is not True or
                finalized.get('lab_fingerprints_unchanged') is not True or
                finalized.get('cleanup_completed') is not True or
                not registry_finalized or any(value != 'reimported' for value in restored.values()) or
                any(finalized.get(key) for key in ('safety_error', 'registry_error', 'cleanup_error', 'restoration_skipped',
                                                  'registry_restore_skipped', 'save_restore_errors',
                                                  'stop_errors'))):
            raise RuntimeError(f'Unfinalized creation oracle requires existing-intent recovery: {prior}')


def make_scenario(work: Path, command_timeout: float, world_timeout: float, *,
                  source_realm: str = 'SR2', target_realm: str = 'SR1'):
    if (source_realm not in ('SR1', 'SR2') or target_realm not in ('SR1', 'SR2') or
            source_realm == target_realm):
        raise ValueError('Creation requires distinct supported source and target realms')
    evidence = {'passed': False, 'initially_launched_realms': [source_realm],
                'source_realm': source_realm, 'target_realm': target_realm}

    def execute(host, beats, pids, procs=None):
        if procs is None or set(procs) != {source_realm}:
            raise RuntimeError(f'creation oracle must initially own exactly the {source_realm} source game')
        _require_owned_alive(procs, source_realm)
        if (pids.get(source_realm) != procs[source_realm].pid or
                (beats.get(source_realm) or {}).get('pid') != procs[source_realm].pid):
            raise RuntimeError('source heartbeat is not from the owned process')
        source_id = beats[source_realm]['status']['save_id']
        if type(source_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', source_id) is None:
            raise RuntimeError('source loaded native identity is missing or invalid')
        stamp = secrets.token_hex(8)
        evidence['source_native_query'] = _live_query(host, source_realm, source_id, pids[source_realm],
                                                     stamp + ':source-query', command_timeout)
        source_identity = source_realm.lower() + '-native:' + source_id
        db_path = work / 'profiles.sqlite'
        with ProfileStore(db_path) as store:
            profile = store.find_or_create(source_realm, installation_id(source_realm), source_id, source_identity)
            profile_id = profile['bridge_profile_id']
            pending = store.reserve_counterpart(profile_id, target_realm)
            creation_id = pending['creation_id']
            evidence.update(bridge_profile_id=profile_id, creation_id=creation_id, profile_db=str(db_path))
            proc, exe = _launch_target(host, work, procs, target_realm)
            evidence['target_launch'] = {'pid': proc.pid, 'executable': str(exe.resolve())}
            root = Path.home() / 'AppData' / 'LocalLow' / save_guard.SAVE_DIRS[target_realm]
            guarded_save_root = root
            latest_binding = None

            def refresh_storage_binding():
                nonlocal root, latest_binding
                if target_realm != 'SR2':
                    return
                _require_owned_alive(procs, target_realm)
                observed = read_binding_log(
                    exe.parent / 'MelonLoader' / 'Latest.log', token=host.realm_token(target_realm),
                    pid=proc.pid, executable=exe.resolve(),
                    journal=(work / (target_realm + '-ops.journal')).resolve(),
                    guarded_root=guarded_save_root)
                if latest_binding is not None:
                    assert_same_binding(latest_binding, observed)
                if evidence.get('native_storage_binding') is None:
                    evidence['native_storage_binding'] = observed
                latest_binding = observed
                evidence['native_storage_binding_last_read'] = observed
                root = Path(observed['native_root'])
                _require_owned_alive(procs, target_realm)

            def reconcile(tag, evidence_key):
                _require_owned_alive(procs, target_realm)
                assert_no_other_game_processes(p.pid for p in procs.values())
                refresh_storage_binding()
                query_op = creation_id + ':reconcile:' + tag
                host.enqueue_creation_command(target_realm, 'reconcile_save', creation_id,
                                              op_id=query_op, expected_pid=proc.pid)
                result = host.wait_result(query_op, command_timeout)
                evidence[evidence_key] = result  # retain even a failed/uncertain readback
                refresh_storage_binding()
                checked = native_proof.verify_creation(result, creation_id, query_op, proc.pid, root,
                                                       realm=target_realm, expected_kind='reconcile_save')
                refresh_storage_binding()
                return result, checked

            # Menu readiness queries are read-only. The native CREATE is durably claimed once.
            deadline = time.monotonic() + world_timeout
            attempt = 0
            ready = None
            while time.monotonic() < deadline:
                _require_owned_alive(procs, target_realm)
                assert_no_other_game_processes(p.pid for p in procs.values())
                op = creation_id + ':ready:' + str(attempt)
                host.enqueue_creation_command(target_realm, 'reconcile_save', creation_id,
                                              op_id=op, expected_pid=proc.pid)
                ready = host.wait_result(op, min(command_timeout, 5.0))
                evidence['menu_ready'] = ready
                if ready is None:
                    host.cancel_command(target_realm, op)  # NEVER cancel a CREATE to retry it
                elif (ready.get('pid') == proc.pid and ready.get('realm') == target_realm and
                      ready.get('kind') == 'reconcile_save' and ready.get('op_id') == op and
                      ready.get('creation_id') == creation_id and ready.get('can_create') is True and
                      ready.get('ok') is True):
                    break
                attempt += 1
            else:
                raise RuntimeError(f'{target_realm} native menu/storage never became ready')
            if (ready or {}).get('verified') is True:
                raise RuntimeError('fresh creation id unexpectedly already exists; refusing new dispatch')
            _require_owned_alive(procs, target_realm)
            # Bind the exact initialized native provider root BEFORE durable claim/CREATE.
            # This is a read-only signed diagnostic, never a filesystem search/fallback.
            refresh_storage_binding()
            if not store.claim_creation(profile_id, creation_id):
                raise RuntimeError('creation already issued; native reconciliation is required')
            evidence['creation_claimed'] = True
            op = host.enqueue_creation_command(target_realm, 'create_save', creation_id, expected_pid=proc.pid)
            evidence['creation_op_id'] = op
            receipt = host.wait_result(op, world_timeout)
            evidence['native_creation_result'] = receipt
            # Missing or explicitly uncertain receipts may NEVER trigger another CREATE, even
            # a transport replay. Only reconcile this existing intent and keep all diagnostics.
            uncertain = receipt is None or receipt.get('uncertain') is True
            evidence['creation_uncertain'] = uncertain
            if uncertain:
                # Retire delivery of this uncertain CREATE, retaining its durable intent and
                # receipt context. A late result may still arrive; only read-only recovery follows.
                host.cancel_command(target_realm, op)
                _, proof = reconcile('timeout', 'native_timeout_reconciliation')
                evidence['plugin_journal_replay_not_attempted'] = 'uncertain initial creation; reconciliation only'
            else:
                refresh_storage_binding()
                proof = native_proof.verify_creation(receipt, creation_id, op, proc.pid, root, realm=target_realm)
            refresh_storage_binding()
            evidence['independent_disk_readback'] = proof
            linked = store.confirm_counterpart(profile_id, creation_id, installation_id(target_realm),
                                               proof['save_id'], proof['identity'])
            evidence['profile_after_link'] = linked
            if linked['state'] != 'LINKED':
                raise RuntimeError('verified native counterpart was not linked')

        with ProfileStore(db_path) as reopened:
            durable = reopened.get(profile_id)
            repeated = reopened.confirm_counterpart(profile_id, creation_id, installation_id(target_realm),
                                                      proof['save_id'], proof['identity'])
            if durable != repeated or durable['state'] != 'LINKED':
                raise RuntimeError('profile linkage was not durable/idempotent')
            evidence['profile_after_db_reopen'] = durable

        before_replay, before_proof = reconcile('before', 'native_reconcile_before_replay')
        if before_proof != proof:
            raise RuntimeError('native creation identity/file/fingerprint changed before replay')
        # A known-success receipt is not permission to redeliver an issued CREATE.
        # Replay the durable decision and perform a fresh read-only native reconciliation.
        _require_owned_alive(procs, target_realm)
        host.forget_volatile_state()
        decision = decide_counterpart(profile=durable, intents=[], target_realm=target_realm,
                                      provider_health='HEALTHY', slot_status='UNKNOWN', runtime_proven=False,
                                      endpoint_compatibility='VALID')
        # VALID is this exact linked endpoint's matching fresh native readback above, not
        # a claim that the build/general creator has completed protected runtime validation.
        evidence['replay_decision'] = decision
        evidence['plugin_journal_replay_not_attempted'] = 'single-dispatch policy; read-only decision/reconciliation only'
        if decision.get('decision') != 'USE_EXISTING_LINK':
            raise RuntimeError('replayed durable decision would not use the existing verified counterpart')
        after_replay, after_proof = reconcile('after', 'native_reconcile_after_replay')
        evidence['independent_replay_readback'] = after_proof
        if (after_replay.get('applied') is not False or after_replay.get('uncertain') is not False or
                after_proof != before_proof or any(type(after_replay.get(k)) is not int or
                                             after_replay[k] != before_replay[k] for k in ('before', 'after'))):
            raise RuntimeError('read-only reconciliation changed the native identity/file/hash/save-revision count or applied again')

        deadline = time.monotonic() + world_timeout
        while time.monotonic() < deadline:
            assert_no_other_game_processes(p.pid for p in procs.values())
            current = host.heartbeats()
            if all(oracle.heartbeat_matches_launched(current.get(realm), procs[realm], oracle.GAMES[realm]) and
                   oracle.loaded_status_ok(current.get(realm)) and
                   current[realm]['status']['save_id'] == expected
                   for realm, expected in [(target_realm, proof['save_id']), (source_realm, source_id)]):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('both linked worlds did not report live WORLD_READY together')
        refresh_storage_binding()
        evidence['worlds_ready'] = current
        _require_owned_alive(procs, target_realm)
        evidence['target_native_query'] = _live_query(host, target_realm, proof['save_id'], proc.pid,
                                                     creation_id + ':target-ready', command_timeout)
        refresh_storage_binding()
        _require_owned_alive(procs, source_realm)
        evidence['source_still_ready_query'] = _live_query(host, source_realm, source_id, pids[source_realm],
                                                          creation_id + ':source-still-ready', command_timeout)
        _require_owned_alive(procs, source_realm)
        refresh_storage_binding()
        evidence['passed'] = True
        return evidence

    def scenario(host, beats, pids, procs=None):
        try:
            return execute(host, beats, pids, procs)
        except Exception as ex:
            evidence['error'] = f'{type(ex).__name__}: {ex}'
            raise
        finally:
            publish_creation_evidence(work, evidence)
    return scenario


def main():
    parser = argparse.ArgumentParser(description='Protected native source -> counterpart creation + linkage oracle')
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    parser.add_argument('--source-realm', choices=('SR1', 'SR2'), default='SR2')
    parser.add_argument('--target-realm', choices=('SR1', 'SR2'), default='SR1')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--command-timeout', type=float, default=30)
    parser.add_argument('--sr1-save-id')
    parser.add_argument('--sr2-save-id')
    args = parser.parse_args()
    if args.source_realm == args.target_realm:
        parser.error('source and target realms must differ')
    for realm, save_id in (('SR1', args.sr1_save_id), ('SR2', args.sr2_save_id)):
        if save_id is not None and (realm != args.source_realm or
                                   re.fullmatch(r'[A-Za-z0-9_-]{1,128}', save_id) is None):
            parser.error(f'--{realm.lower()}-save-id must be a valid existing source save ID')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    root = oracle.REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    require_finalized_prior_creations(root)
    work = root / ('creation-' + stamp)
    refuse_reparse(work)
    work.mkdir(parents=True, exist_ok=False)
    scenario = make_scenario(work, args.command_timeout, args.timeout,
                             source_realm=args.source_realm, target_realm=args.target_realm)
    try:
        result = oracle.run([args.source_realm], args.timeout, False, work, require_status=True, current_user=True,
                            sr1_save_id=args.sr1_save_id, sr2_save_id=args.sr2_save_id,
                            guard_realms=['SR1', 'SR2'], creation_realms=(args.target_realm,),
                            extra_env={args.source_realm: {'SLIMEBRIDGE_OP_JOURNAL':
                                                          str(work / (args.source_realm + '-ops.journal'))}},
                            scenario=scenario)
    except Exception as ex:
        # Detailed generic/scenario evidence remains on disk. This invocation can never pass
        # or allocate another intent when launch, dispatch or cleanup raised.
        result = {'passed': False, 'error': f'{type(ex).__name__}: {ex}'}
        (work / 'oracle-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        finalize_creation_evidence(work, result)
        raise
    (work / 'oracle-result.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
    finalize_creation_evidence(work, result)
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
