"""Protected native transfer/reload oracle, including receiver replay in a NEW process.

A successful synthetic test proves only orchestration. Native proof requires this CLI to run
against the owner-authorized LAB, with save/registry backup and restoration by oracle.run.
The receiver ACK is lost BEFORE commit; terminal mutation fences are never bypassed.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal
from bridge.transfer.coordinator import resume, run_command, transfer
from bridge.transfer.store import STATUS_COMMITTED, STATUS_ESCROWED, TransferStore
from tools.lab import oracle
from tools.lab.crash_oracle import FaultChannel
from tools.lab.endpoint_attestation import PROVIDER_PREFIX, validate_run_write_paths
from tools.lab.paths import validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes, same_image
from tools.lab.transfer_oracle import read_linked_profile


def restart_owned_games(host, procs, saves, journals, timeout):
    """Restart only verified owned LAB processes inside the existing backup/cleanup boundary."""
    if set(procs) != set(saves) or set(journals) != set(saves):
        raise RuntimeError('reload requires the exact set of owned games, saves and journals')
    executables = {}
    for realm, proc in procs.items():
        executable = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
        if not executable.is_file() or proc.poll() is not None or not same_image(proc.pid, executable):
            raise RuntimeError(f'{realm} reload process is not the live owned LAB executable')
        executables[realm] = executable
    assert_no_other_game_processes(proc.pid for proc in procs.values())
    stop_errors = oracle._stop_owned(procs)
    if stop_errors:
        raise RuntimeError(f'cannot reload after an incomplete stop: {stop_errors}')
    assert_no_other_game_processes()
    host.forget_volatile_state()
    for realm, executable in executables.items():
        assert_no_other_game_processes(proc.pid for proc in procs.values() if proc.poll() is None)
        env = {key: value for key, value in os.environ.items() if not key.startswith('SLIMEBRIDGE_')}
        env.update(SLIMEBRIDGE_URL=host.url, SLIMEBRIDGE_TOKEN=host.realm_token(realm),
                   SLIMEBRIDGE_AUTOLOAD='continue', SLIMEBRIDGE_LAB_EXE=str(executable.resolve()),
                   SLIMEBRIDGE_LAB_SAVE_ID=saves[realm], SLIMEBRIDGE_OP_JOURNAL=str(journals[realm]))
        # Register each new process immediately so outer oracle cleanup owns even a failed launch.
        procs[realm] = subprocess.Popen([str(executable), *oracle.WINDOW_ARGS],
                                       cwd=str(executable.parent), env=env)
        if not same_image(procs[realm].pid, executable):
            raise RuntimeError(f'{realm} new process image differs from LAB')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert_no_other_game_processes(proc.pid for proc in procs.values())
        beats = host.heartbeats()
        if all(oracle.heartbeat_matches_launched(beats.get(realm), procs[realm], executables[realm])
               and oracle.loaded_status_ok(beats.get(realm))
               and beats[realm]['status']['save_id'] == saves[realm] for realm in saves):
            return {realm: beats[realm] for realm in saves}
        if any(proc.poll() is not None for proc in procs.values()):
            raise RuntimeError('a restarted LAB process exited before its exact save loaded')
        time.sleep(0.5)
    raise RuntimeError('restarted LAB games did not load their exact saves within the deadline')


def make_scenario(sender, receiver, item, work, journals, command_timeout, load_timeout, *,
                  profile_id=None, authority=None):
    def scenario(host, beats, pids, procs=None):
        evidence = {'passed': False, 'sender': sender, 'receiver': receiver, 'item': item,
                    'profile_id': profile_id}
        store = None
        try:
            if procs is None:
                raise RuntimeError('durability oracle requires owned process handles')
            runtime = authority.get('_runtime') if isinstance(authority, dict) else None
            if (not isinstance(runtime, dict) or not isinstance(profile_id, str) or not profile_id
                    or runtime.get('profile_id') != profile_id):
                raise RuntimeError('the bound transfer runtime was not armed by the oracle')
            # The recovered transfer must resume from the exact durable ledger the host was
            # armed with; a different path would bypass the run's authority.
            try:
                armed_store = Path(runtime['store_path']).resolve()
            except (KeyError, TypeError, ValueError) as ex:
                raise RuntimeError('the armed transfer runtime has no valid ledger path') from ex
            if armed_store != (work / 'transfers.db').resolve():
                raise RuntimeError('the scenario ledger differs from the armed transfer store')
            saves = {realm: beats[realm]['status']['save_id'] for realm in (sender, receiver)}
            for realm in (sender, receiver):
                if saves[realm] != runtime['endpoints'][realm]['native_save_id']:
                    raise RuntimeError(f'{realm} is not loaded with the linked save')
            evidence['saves'], evidence['original_pids'] = saves, dict(pids)
            try:
                validate_run_write_paths(runtime)
                with CounterpartJournal(runtime['journal_path']) as pair_journal:
                    adoption = {realm: pair_journal.observe_live_endpoint(
                        profile_id,
                        {'realm': realm, **{key: runtime['endpoints'][realm][key] for key in
                                            ('installation_id', 'native_save_id', 'native_save_identity')}},
                        runtime['builds'][realm]) for realm in (sender, receiver)}
            except Exception as ex:
                raise RuntimeError(f'journal endpoint adoption failed: {type(ex).__name__}: {ex}') from ex
            evidence['adoption'] = adoption
            if any(verdict != 'VERIFIED' for verdict in adoption.values()):
                raise RuntimeError('a linked endpoint did not adopt as VERIFIED under this build')
            stamp = secrets.token_hex(6)
            caller_key = 'durability-oracle-' + stamp
            evidence['idempotency_key'] = caller_key
            def query(realm, tag):
                result = run_command(host, realm, 'query', item, 0, 'durable-' + stamp + '-' + tag,
                                     saves[realm], command_timeout)
                if not result or result.get('ok') is not True or result.get('pid') != pids[realm]:
                    raise RuntimeError(f'{realm} exact-session native count query failed: {tag}')
                return result['before']
            seed = run_command(host, sender, 'lab_give', item, 1, 'durable-' + stamp + '-seed',
                               saves[sender], command_timeout)
            evidence['seed'] = seed
            if not (seed and seed.get('ok') is True and seed.get('applied') is True
                    and seed.get('persisted') is True and seed.get('verified') is True
                    and seed.get('after') == seed.get('before', -10) + 1):
                raise RuntimeError('native seed was not persisted and verified')
            n0, m0 = query(sender, 'n0'), query(receiver, 'm0')
            if n0 < 1:
                raise RuntimeError('refusing a zero-count transfer proof')
            store = TransferStore(work / 'transfers.db')
            channel = FaultChannel(host)
            channel.drop_result.add('add')
            first = transfer(store, channel, sender, saves[sender], receiver, saves[receiver], item, 1,
                             timeout=command_timeout, idempotency_key=caller_key,
                             bridge_profile_id=profile_id)
            evidence['transfer_id'] = first.transfer_id
            evidence['before_restart'] = {'status': first.status, 'error': first.error, 'steps': first.steps}
            original = host.wait_result(first.transfer_id + ':add', 0)
            evidence['receiver_original'] = original
            n1, m1 = query(sender, 'n1'), query(receiver, 'm1')
            if not (first.status == STATUS_ESCROWED and original and original.get('applied') is True
                    and original.get('persisted') is True and original.get('verified') is True
                    and n1 == n0 - 1 and m1 == m0 + 1):
                raise RuntimeError('lost-ACK transfer did not reach verified native ESCROWED state')
            store.close()
            reloaded = restart_owned_games(host, procs, saves, journals, load_timeout)
            pids.update({realm: proc.pid for realm, proc in procs.items()})
            evidence['reloaded_pids'] = dict(pids)
            evidence['reloaded_save_ids'] = {realm: beat['status']['save_id'] for realm, beat in reloaded.items()}
            nr, mr = query(sender, 'reloaded-n'), query(receiver, 'reloaded-m')
            evidence['counts'] = {'source_before': n0, 'destination_before': m0,
                                  'source_after': n1, 'destination_after': m1,
                                  'source_reloaded': nr, 'destination_reloaded': mr}
            store = TransferStore(work / 'transfers.db')
            recovered = resume(store, host, first.transfer_id, timeout=command_timeout)
            evidence['after_restart'] = {'status': recovered.status, 'error': recovered.error,
                                         'steps': recovered.steps}
            replay = next((step['result'] for step in recovered.steps if step['step'] == 'receiver_add'), None)
            evidence['receiver_replay'] = replay
            def native_receipt(result):
                return {key: value for key, value in (result or {}).items()
                        if key not in ('received_unix', 'pid')}
            same_receipt = bool(original and replay and native_receipt(original) == native_receipt(replay))
            evidence['same_native_receipt_except_transport'] = same_receipt
            retry = transfer(store, host, sender, saves[sender], receiver, saves[receiver], item, 1,
                             timeout=command_timeout, idempotency_key=caller_key,
                             bridge_profile_id=profile_id)
            evidence['logical_retry'] = {'transfer_id': retry.transfer_id, 'status': retry.status,
                                         'steps': retry.steps}
            nf, mf = query(sender, 'final-n'), query(receiver, 'final-m')
            evidence['final_counts'] = {'source': nf, 'destination': mf}
            evidence['passed'] = bool(nr == n1 and mr == m1 and nf == n1 and mf == m1
                                      and recovered.status == STATUS_COMMITTED and same_receipt
                                      and replay.get('pid') == pids[receiver]
                                      and original.get('pid') != pids[receiver]
                                      and retry.transfer_id == first.transfer_id
                                      and retry.status == STATUS_COMMITTED and retry.steps == [])
            return evidence
        except Exception as error:
            evidence['error'] = f'{type(error).__name__}: {error}'
            raise
        finally:
            if store is not None:
                store.close()
            (work / 'durability-result.json').write_text(json.dumps(evidence, indent=2, default=str), encoding='utf-8')
    return scenario


def main():
    parser = argparse.ArgumentParser(description='Protected native transfer -> process restart -> exact-save reload proof')
    parser.add_argument('--profile-db', required=True, help='profiles.sqlite written by a creation run')
    parser.add_argument('--profile-id', required=True, help='linked bridge_profile_id to transfer between')
    parser.add_argument('--sender', choices=('SR1', 'SR2'), default='SR1')
    parser.add_argument('--item', choices=('slime.pink', 'plort.pink'), default='slime.pink')
    parser.add_argument('--sr1-save-id', help='must equal the linked SR1 endpoint save if given')
    parser.add_argument('--sr2-save-id', help='must equal the linked SR2 endpoint save if given')
    parser.add_argument('--timeout', type=float, default=420)
    parser.add_argument('--command-timeout', type=float, default=45)
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    args = parser.parse_args()
    profiles_path = Path(args.profile_db).resolve()
    profile = read_linked_profile(profiles_path, args.profile_id)
    endpoints = {}
    for realm in ('SR1', 'SR2'):
        endpoint = profile[realm.lower()]
        endpoints[realm] = {'realm': realm, **{key: endpoint[key] for key in
                                               ('installation_id', 'native_save_id', 'native_save_identity')}}
    for realm, override in (('SR1', args.sr1_save_id), ('SR2', args.sr2_save_id)):
        if override is not None and override != endpoints[realm]['native_save_id']:
            parser.error(f'--{realm.lower()}-save-id differs from the linked profile endpoint')
    receiver = 'SR2' if args.sender == 'SR1' else 'SR1'
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    work = oracle.REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'durability' / stamp
    work.mkdir(parents=True, exist_ok=False)
    journals = {realm: work / (realm + '-ops.journal') for realm in ('SR1', 'SR2')}
    env = {realm: {'SLIMEBRIDGE_OP_JOURNAL': str(path),
                   'SLIMEBRIDGE_LAB_GIVE': '1' if realm == args.sender else '0'}
           for realm, path in journals.items()}
    authority = {'profiles_path': str(profiles_path),
                 'journal_path': str(work / 'counterpart-journal.sqlite'),
                 'store_path': str(work / 'transfers.db'),
                 'profile_id': args.profile_id,
                 'trust': {f'{PROVIDER_PREFIX}.{realm}': realm for realm in ('SR1', 'SR2')},
                 'allow_lab_give': True}
    result = oracle.run(['SR1', 'SR2'], args.timeout, False, work, require_status=True, current_user=True,
                        sr1_save_id=endpoints['SR1']['native_save_id'],
                        sr2_save_id=endpoints['SR2']['native_save_id'],
                        scenario=make_scenario(args.sender, receiver, args.item, work, journals,
                                               args.command_timeout, args.timeout,
                                               profile_id=args.profile_id, authority=authority),
                        extra_env=env, transfer_authority=authority)
    detail_path = work / 'durability-result.json'
    detail = json.loads(detail_path.read_text(encoding='utf-8')) if detail_path.is_file() else {}
    detail.update(native_scenario_passed=detail.get('passed') is True, passed=result['passed'],
                  oracle_scenario=result.get('scenario'),
                  saves_match_backup_after_restore=result.get('saves_match_backup_after_restore'),
                  sources_untouched=result.get('sources_untouched'))
    detail_path.write_text(json.dumps(detail, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'evidence': str(work)}, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
