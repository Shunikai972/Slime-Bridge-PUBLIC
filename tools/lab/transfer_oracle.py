"""Real bound transfer oracle: move held items between the two linked LAB endpoints.

Runs on top of tools.lab.oracle in current-user mode (saves hashed + backed up before launch and
restored after, with post-run evidence preserved). The pair comes from a linked profile DB
written by the creation flow; this run adopts its endpoints in a fresh counterpart journal under
the run's evidence directory, and the host authorizes every native remove/add/query against the
durable ledger, the profile/pair authority and live endpoint attestation:

  1. wait until both games report the linked saves loaded;
  2. adopt both endpoints as VERIFIED in the run-scoped journal under this run's canonical LAB
     build (a never-seen live save is recorded once; later runs re-validate, never silently);
  3. per step: query both natives, optionally seed a sender that owns none via one controlled
     lab_give, drive the durable ledger + native remove/add through bridge.transfer.coordinator
     with the resolved bridge_profile_id, re-query and require exact deltas and COMMITTED;
  4. replay the exact idempotency key (same transfer id, no duplicate commands, counts stable);
  5. attempt refused wrong-profile and wrong-save mutations (no native change, no ledger rows);
  6. resume every committed row through a second store instance opened on the same DB.

The proof is the plugins' native before/after counts read on the Unity main thread, not the ledger.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal
from bridge.transfer.coordinator import resume as resume_transfer
from bridge.transfer.coordinator import run_command, transfer
from bridge.transfer.store import STATUS_COMMITTED, TransferStore
from tools.lab import oracle
from tools.lab.endpoint_attestation import PROVIDER_PREFIX


def read_linked_profile(profiles_path: Path, profile_id: str) -> dict:
    from tools.lab.endpoint_attestation import read_linked_pair
    try:
        return read_linked_pair(profiles_path, profile_id)['profile']
    except ValueError as exc:
        raise SystemExit(str(exc))


def make_scenario(profile_id: str, endpoints: dict, steps: list, auto_seed: bool,
                  command_timeout: float, authority: dict):
    def scenario(host, beats, pids, procs=None):
        from tools.lab.endpoint_attestation import validate_run_write_paths
        evidence: dict = {'profile_id': profile_id, 'steps': [], 'passed': False}
        runtime = authority.get('_runtime') if isinstance(authority, dict) else None
        if not isinstance(runtime, dict) or runtime.get('profile_id') != profile_id:
            evidence['error'] = 'the bound transfer runtime was not armed by the oracle'
            return evidence
        saves = {}
        for realm in ('SR1', 'SR2'):
            saves[realm] = ((beats.get(realm) or {}).get('status') or {}).get('save_id')
        evidence['saves'] = saves
        for realm in ('SR1', 'SR2'):
            if saves[realm] != endpoints[realm]['native_save_id']:
                evidence['error'] = f'{realm} is not loaded with the linked save'
                return evidence
        store = runtime.get('store')
        if store is None:
            evidence['error'] = 'the oracle did not arm the durable ledger store'
            return evidence
        # The journal is opened hundreds of seconds after the arm-time check: re-validate the
        # run-scoped write targets (and their SQLite sidecars) immediately before the open, so a
        # link inserted while the games were loading is still refused.
        try:
            validate_run_write_paths(runtime)
        except ValueError as ex:
            evidence['error'] = f'journal open refused: {ex}'
            return evidence
        # Adopt the linked endpoints only after both live saves matched the pair. A fresh journal
        # records a never-seen live save as VERIFIED under this run's canonical build; a journal
        # that already holds different bytes refuses loudly.
        adoption = {}
        try:
            with CounterpartJournal(runtime['journal_path']) as journal:
                for realm in ('SR1', 'SR2'):
                    adoption[realm] = journal.observe_live_endpoint(
                        profile_id,
                        {'realm': realm, **{key: endpoints[realm][key] for key in
                                            ('installation_id', 'native_save_id', 'native_save_identity')}},
                        runtime['builds'][realm])
        except Exception as ex:
            evidence['error'] = f'journal endpoint adoption failed: {type(ex).__name__}: {ex}'
            return evidence
        evidence['journal_adoption'] = adoption
        if any(verdict != 'VERIFIED' for verdict in adoption.values()):
            evidence['error'] = 'a linked endpoint did not adopt as VERIFIED under this build'
            return evidence

        stamp = secrets.token_hex(4)

        def cmd(realm, kind, item, count, tag):
            return run_command(host, realm, kind, item, count, f'inv-oracle-{stamp}-{tag}',
                               saves[realm], command_timeout)

        def count_of(result, key):
            return result.get(key) if isinstance(result, dict) and type(result.get(key)) is int else None

        attempted = 0
        for index, step in enumerate(steps):
            sender, item = step['sender'], step['item']
            receiver = 'SR2' if sender == 'SR1' else 'SR1'
            tag = f'{index}-{item.split(".")[0]}'
            entry: dict = {'sender': sender, 'receiver': receiver, 'item': item}
            evidence['steps'].append(entry)

            def fail(message):
                entry['error'] = message
                entry['passed'] = False
                return False

            src_before = cmd(sender, 'query', item, 0, f'{tag}-src-before')
            if src_before is None or src_before.get('ok') is not True or type(src_before.get('before')) is not int:
                fail('native pre-transfer source query failed')
                break
            if src_before['before'] < 1:
                if not auto_seed:
                    fail('sender owns no item; refusing a zero-count transfer test (no --auto-seed)')
                    break
                seeded = cmd(sender, 'lab_give', item, 1, f'{tag}-seed')
                entry['seed'] = seeded
                before_give, after_give = count_of(seeded, 'before'), count_of(seeded, 'after')
                if not (isinstance(seeded, dict) and seeded.get('ok') is True and
                        seeded.get('applied') is True and before_give is not None and
                        after_give == before_give + 1):
                    fail('controlled seed did not change the native inventory')
                    break
                src_before = cmd(sender, 'query', item, 0, f'{tag}-src-before2')
                if src_before is None or src_before.get('ok') is not True or type(src_before.get('before')) is not int:
                    fail('native source query after seeding failed')
                    break
            dst_before = cmd(receiver, 'query', item, 0, f'{tag}-dst-before')
            entry['source_before'], entry['destination_before'] = src_before, dst_before
            if dst_before is None or dst_before.get('ok') is not True or type(dst_before.get('before')) is not int:
                fail('native pre-transfer destination query failed')
                break

            key = f'inventory-oracle-{stamp}-{tag}'
            attempted += 1
            outcome = transfer(store, host, sender, saves[sender], receiver, saves[receiver], item, 1,
                               timeout=command_timeout, idempotency_key=key, bridge_profile_id=profile_id)
            row = store.get(outcome.transfer_id)
            entry['transfer'] = {'transfer_id': outcome.transfer_id, 'status': outcome.status,
                                 'error': outcome.error}
            entry['ledger_status'] = row.status if row else None
            src_after = cmd(sender, 'query', item, 0, f'{tag}-src-after')
            dst_after = cmd(receiver, 'query', item, 0, f'{tag}-dst-after')
            entry['source_after'], entry['destination_after'] = src_after, dst_after
            n, m = src_before['before'], dst_before['before']
            sa, da = count_of(src_after, 'before'), count_of(dst_after, 'before')
            entry['counts'] = {'source_before': n, 'source_after': sa,
                               'destination_before': m, 'destination_after': da}
            ok = (entry['ledger_status'] == STATUS_COMMITTED and sa == n - 1 and da == m + 1 and
                  (src_after or {}).get('pid') == pids[sender] and
                  (dst_after or {}).get('pid') == pids[receiver])
            if not ok:
                entry['passed'] = False
                entry['error'] = 'native counts or ledger state do not match the committed transfer'
                break

            # Replay: the same idempotency key must reuse the committed transfer without a single
            # new native command; both native counts must stay exactly where the commit left them.
            replay = transfer(store, host, sender, saves[sender], receiver, saves[receiver], item, 1,
                              timeout=command_timeout, idempotency_key=key, bridge_profile_id=profile_id)
            src_replay = cmd(sender, 'query', item, 0, f'{tag}-src-replay')
            dst_replay = cmd(receiver, 'query', item, 0, f'{tag}-dst-replay')
            entry['replay'] = {'transfer_id': replay.transfer_id,
                               'same_transfer': replay.transfer_id == outcome.transfer_id,
                               'source_after': count_of(src_replay, 'before'),
                               'destination_after': count_of(dst_replay, 'before')}
            ok = (replay.transfer_id == outcome.transfer_id and
                  count_of(src_replay, 'before') == sa and count_of(dst_replay, 'before') == da)
            entry['passed'] = bool(ok)
            if not ok:
                entry['error'] = 'replayed idempotency key did not resolve to the committed transfer'
                break

        # Refusals: a wrong profile id and a wrong save argument must both be rejected before any
        # native command is dispatched or any ledger row is prepared.
        last = steps[-1]
        sender, item = last['sender'], last['item']
        receiver = 'SR2' if sender == 'SR1' else 'SR1'
        refusals = {}
        try:
            transfer(store, host, sender, saves[sender], receiver, saves[receiver], item, 1,
                     timeout=command_timeout, idempotency_key=f'inventory-oracle-{stamp}-wrong-profile',
                     bridge_profile_id='0' * 32)
            refusals['wrong_profile'] = 'NOT REFUSED'
        except Exception as ex:
            refusals['wrong_profile'] = f'{type(ex).__name__}: {ex}'
        try:
            transfer(store, host, sender, saves[sender] + '-not-the-pair', receiver, saves[receiver],
                     item, 1, timeout=command_timeout,
                     idempotency_key=f'inventory-oracle-{stamp}-wrong-save', bridge_profile_id=profile_id)
            refusals['wrong_save'] = 'NOT REFUSED'
        except Exception as ex:
            refusals['wrong_save'] = f'{type(ex).__name__}: {ex}'
        evidence['refusals'] = refusals
        refusals_ok = all(value.startswith('TransferBindingError') for value in refusals.values())

        # No new ledger row may exist beyond the attempted transfers; refusals never prepare one.
        # Re-check the write targets before this read-only open as well: an insertion cannot
        # change what it reads, but a redirected path must be refused, not followed.
        try:
            validate_run_write_paths(runtime)
            uri = runtime['store_path'].resolve().as_uri() + '?mode=ro'
            with sqlite3.connect(uri, uri=True) as conn:
                rows = conn.execute('SELECT COUNT(*) FROM transfers WHERE idempotency_key LIKE ?',
                                    (f'inventory-oracle-{stamp}%',)).fetchone()[0]
            rows_ok = rows == attempted
            evidence['run_rows'] = {'attempted_transfers': attempted, 'ledger_rows': rows}
        except ValueError as ex:
            evidence['run_rows'] = {'attempted_transfers': attempted, 'ledger_rows': None,
                                    'refused': str(ex)}
            rows_ok = False

        # A second store instance opened on the same DB must see every committed row and be able
        # to resume it without dispatching anything. This is another writable open: re-validate
        # the targets (and SQLite sidecars) immediately before it, not only once at the start.
        reopen = {'rows': {}}
        try:
            validate_run_write_paths(runtime)
        except ValueError as ex:
            evidence['reopen'] = {'refused': str(ex), 'rows': {}}
            reopen_ok = False
        else:
            reopened = TransferStore(runtime['store_path'])
            try:
                for entry in evidence['steps']:
                    tid = (entry.get('transfer') or {}).get('transfer_id')
                    if not tid:
                        continue
                    row = reopened.get(tid)
                    outcome = resume_transfer(reopened, host, tid, timeout=command_timeout)
                    reopen['rows'][tid] = {'status': row.status if row else None, 'resume': outcome.status}
            finally:
                reopened.close()
            evidence['reopen'] = reopen
            reopen_ok = (all(value['status'] == STATUS_COMMITTED and value['resume'] == STATUS_COMMITTED
                             for value in reopen['rows'].values()) and len(reopen['rows']) == attempted)

        evidence['passed'] = bool(evidence['steps'] and all(s.get('passed') for s in evidence['steps'])
                                  and len(evidence['steps']) == len(steps) and refusals_ok and
                                  rows_ok and reopen_ok)
        print('[transfer-oracle] ' + json.dumps({'adoption': adoption, 'refusals': refusals,
                                                 'rows': evidence['run_rows'],
                                                 'passed': evidence['passed']}), flush=True)
        return evidence
    return scenario


def main():
    parser = argparse.ArgumentParser(description='Real bound SR1<->SR2 transfer oracle (LAB copies only)')
    parser.add_argument('--profile-db', required=True, help='profiles.sqlite written by a creation run')
    parser.add_argument('--profile-id', required=True, help='linked bridge_profile_id to transfer between')
    parser.add_argument('--sr1-save-id', help='must equal the linked SR1 endpoint save if given')
    parser.add_argument('--sr2-save-id', help='must equal the linked SR2 endpoint save if given')
    parser.add_argument('--slime-from', choices=('SR1', 'SR2'), default='SR1',
                        help='realm that sends one shared slime (default SR1 -> SR2)')
    parser.add_argument('--plort-from', choices=('SR1', 'SR2'), default='SR2',
                        help='realm that sends one plort (default SR2 -> SR1)')
    parser.add_argument('--auto-seed', action='store_true',
                        help='lab_give one item to a sender that owns none (lab opt-in)')
    parser.add_argument('--implant-json', help='JSON list of pinned save implants for this run')
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
    steps = [{'sender': args.slime_from, 'item': 'slime.pink'},
             {'sender': args.plort_from, 'item': 'plort.pink'}]
    implant = None
    if args.implant_json:
        implant = json.loads(Path(args.implant_json).read_text(encoding='utf-8'))
        if not isinstance(implant, list) or not implant:
            parser.error('--implant-json must contain a non-empty JSON list')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    run_dir = oracle.REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'transfer' / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    authority = {'profiles_path': str(profiles_path),
                 'journal_path': str(run_dir / 'counterpart-journal.sqlite'),
                 'store_path': str(run_dir / 'transfers.db'),
                 'profile_id': args.profile_id,
                 'trust': {f'{PROVIDER_PREFIX}.{realm}': realm for realm in ('SR1', 'SR2')},
                 'allow_lab_give': bool(args.auto_seed)}
    extra_env = {realm: {'SLIMEBRIDGE_OP_JOURNAL': str(run_dir / f'{realm}-ops.journal'),
                         'SLIMEBRIDGE_LAB_GIVE': '1' if args.auto_seed else '0'}
                 for realm in ('SR1', 'SR2')}
    scenario = make_scenario(args.profile_id, endpoints, steps, args.auto_seed, args.command_timeout,
                             authority)
    result = oracle.run(['SR1', 'SR2'], args.timeout, False, run_dir, require_status=True, current_user=True,
                        sr1_save_id=endpoints['SR1']['native_save_id'],
                        sr2_save_id=endpoints['SR2']['native_save_id'],
                        scenario=scenario, extra_env=extra_env, transfer_authority=authority,
                        implant=implant)
    print(json.dumps({'passed': result['passed'], 'scenario': result.get('scenario'),
                      'saves_match_backup_after_restore': result.get('saves_match_backup_after_restore'),
                      'sources_untouched': result.get('sources_untouched'), 'evidence': str(run_dir)},
                     indent=2, default=str))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
