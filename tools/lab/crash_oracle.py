"""Phase-95 crash/restart oracle in the real LAB games (run only after the happy path passes).

One run, sender -> receiver, one canonical item, sender seeded with 2 controlled native items:

  C  host restart mid-transfer: the sender removes (native, journaled) but the host "crashes" before
     recording ESCROWED (result held only in memory). The host's volatile state is dropped, the
     ledger is reopened from disk, and the transfer is resumed with the SAME op ids. The sender
     plugin must replay its journaled result instead of removing again. Expect N-1 / M+1.
  B  lost destination ACK: while ESCROWED, the receiver adds once but its result is lost before
     commit. After another host-state drop the SAME add op is replayed, then COMMITTED. Native
     counts and the durable receipt must not change. Terminal mutation fences remain enforced.
  A  receiver crash after sender removal, before receiver addition: the receiver process is killed
     before the add is ever enqueued. The transfer stays ESCROWED; the refund is allowed only after
     the receiver is verified dead and its durable journal shows the add never started. Expect the
     sender count restored and ledger REFUNDED.

Saves are hashed/backed up before launch and restored after (oracle current-user mode).
"""
from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal
from bridge.transfer.coordinator import (journal_op_state, refund_after_receiver_loss, resume, run_command,
                                         transfer)
from bridge.transfer.store import STATUS_COMMITTED, STATUS_ESCROWED, STATUS_LOCKED, STATUS_REFUNDED, TransferStore
from tools.lab import oracle
from tools.lab.endpoint_attestation import PROVIDER_PREFIX, validate_run_write_paths
from tools.lab.transfer_oracle import read_linked_profile


class FaultChannel:
    """Wraps the BridgeHost command channel to inject host-side faults deterministically."""

    def __init__(self, host):
        self.host = host
        self.drop_result: set[str] = set()   # op suffix -> result is received but "lost" by a host crash
        self.before_enqueue: dict = {}        # op suffix -> callback(realm, op_id) run instead of enqueuing
        self.no_wait: set[str] = set()

    def bind_transfer_store(self, store):
        return self.host.bind_transfer_store(store)

    # The coordinator requires its channel to attest the profile/pair and the live native
    # endpoints; delegate those reads to the wrapped host instead of re-implementing authority.
    def resolve_transfer_binding(self, *args, **kwargs):
        return self.host.resolve_transfer_binding(*args, **kwargs)

    def validate_transfer(self, *args, **kwargs):
        return self.host.validate_transfer(*args, **kwargs)

    def enqueue_command(self, realm, kind, item, count=1, op_id=None, save_id=None):
        suffix = (op_id or '').rsplit(':', 1)[-1]
        hook = self.before_enqueue.pop(suffix, None)
        if hook is not None:
            hook(realm, op_id)
            self.no_wait.add(op_id)
            return op_id
        return self.host.enqueue_command(realm, kind, item, count, op_id=op_id, save_id=save_id)

    def cancel_command(self, realm, op_id):
        return self.host.cancel_command(realm, op_id)

    def wait_result(self, op_id, timeout):
        if op_id in self.no_wait:
            return None
        result = self.host.wait_result(op_id, timeout)
        suffix = op_id.rsplit(':', 1)[-1]
        if suffix in self.drop_result:
            self.drop_result.discard(suffix)
            return None
        return result


def make_scenario(sender, receiver, item, db_path, journals, timeout, *, profile_id=None, authority=None):
    def scenario(host, beats, pids, procs=None):
        ev: dict = {'sender': sender, 'receiver': receiver, 'item': item, 'profile_id': profile_id,
                    'passed': False}
        runtime = authority.get('_runtime') if isinstance(authority, dict) else None
        if (not isinstance(runtime, dict) or not isinstance(profile_id, str) or not profile_id
                or runtime.get('profile_id') != profile_id):
            ev['error'] = 'the bound transfer runtime was not armed by the oracle'
            return ev
        # The scenario must drive the exact durable ledger the host was armed with; a different
        # path would bypass the run's authority and must fail closed rather than be used.
        try:
            armed_store = Path(runtime['store_path']).resolve()
        except (KeyError, TypeError, ValueError):
            ev['error'] = 'the armed transfer runtime has no valid ledger path'
            return ev
        if armed_store != Path(db_path).resolve():
            ev['error'] = 'the scenario ledger differs from the armed transfer store'
            return ev
        saves = {r: beats[r]['status']['save_id'] for r in (sender, receiver)}
        for realm in (sender, receiver):
            if saves[realm] != runtime['endpoints'][realm]['native_save_id']:
                ev['error'] = f'{realm} is not loaded with the linked save'
                return ev
        try:
            validate_run_write_paths(runtime)
            with CounterpartJournal(runtime['journal_path']) as pair_journal:
                adoption = {realm: pair_journal.observe_live_endpoint(
                    profile_id,
                    {'realm': realm, **{key: runtime['endpoints'][realm][key] for key in
                                        ('installation_id', 'native_save_id', 'native_save_identity')}},
                    runtime['builds'][realm]) for realm in (sender, receiver)}
        except Exception as ex:
            ev['error'] = f'journal endpoint adoption failed: {type(ex).__name__}: {ex}'
            return ev
        ev['adoption'] = adoption
        if any(verdict != 'VERIFIED' for verdict in adoption.values()):
            ev['error'] = 'a linked endpoint did not adopt as VERIFIED under this build'
            return ev
        stamp = secrets.token_hex(6)

        def query(realm, tag):
            r = run_command(host, realm, 'query', item, 0, f'crash-{stamp}-{tag}', saves[realm], timeout)
            return r.get('before') if r and r.get('ok') else None

        seed = run_command(host, sender, 'lab_give', item, 2, f'crash-{stamp}-seed', saves[sender], timeout)
        ev['seed'] = seed
        if not (seed and seed.get('ok') and seed.get('after') == seed.get('before', -10) + 2):
            ev['error'] = 'controlled seed failed'
            return ev
        n0, m0 = query(sender, 'n0'), query(receiver, 'm0')
        ev['start'] = {'source': n0, 'destination': m0}
        if n0 is None or m0 is None or n0 < 2:
            ev['error'] = 'pre-test query failed'
            return ev
        channel = FaultChannel(host)

        # C: host restart after the sender removed but before ESCROWED was recorded.
        channel.drop_result.add('remove')
        store = TransferStore(db_path)
        first = transfer(store, channel, sender, saves[sender], receiver, saves[receiver], item, 1,
                         timeout=timeout, idempotency_key='crash-oracle-' + stamp + '-first',
                         bridge_profile_id=profile_id)
        store.close()
        host.forget_volatile_state()
        n_mid = query(sender, 'c-mid')
        store = TransferStore(db_path)
        in_flight = [r.transfer_id for r in store.list_in_flight()]
        # Receive the add proof but lose it before the coordinator can commit. This is the
        # legitimate redelivery window; COMMITTED must never authorize a fresh native add.
        channel.drop_result.add('add')
        resumed = resume(store, channel, first.transfer_id, timeout=timeout)
        original_add = host.wait_result(f'{first.transfer_id}:add', 0)
        n1, m1 = query(sender, 'n1'), query(receiver, 'm1')
        ev['C'] = {'first_status': first.status, 'first_error': first.error, 'sender_after_crash': n_mid,
                   'in_flight_after_restart': in_flight, 'resumed_status': resumed.status,
                   'resumed_steps': resumed.steps, 'source': n1, 'destination': m1,
                   'sender_remove_journal': journal_op_state(journals[sender], f'{first.transfer_id}:remove')}
        ev['C']['passed'] = (first.status == STATUS_LOCKED and n_mid == n0 - 1 and in_flight == [first.transfer_id]
                             and resumed.status == STATUS_ESCROWED and n1 == n0 - 1 and m1 == m0 + 1
                             and ev['C']['sender_remove_journal'] == 'done')

        # B: recover the lost receiver ACK before commit, without a second native add.
        before_replay = store.get(first.transfer_id).status
        store.close()
        host.forget_volatile_state()
        store = TransferStore(db_path)
        replayed = resume(store, channel, first.transfer_id, timeout=timeout)
        replay = next((step['result'] for step in replayed.steps if step['step'] == 'receiver_add'), None)
        n2, m2 = query(sender, 'n2'), query(receiver, 'm2')
        def native_receipt(result):
            return {key: value for key, value in (result or {}).items() if key != 'received_unix'}
        same_receipt = bool(original_add and replay and native_receipt(original_add) == native_receipt(replay))
        ev['B'] = {'status_before_replay': before_replay, 'status_after_replay': replayed.status,
                   'original_result': original_add, 'replayed_result': replay,
                   'same_native_receipt': same_receipt, 'source': n2, 'destination': m2}
        ev['B']['passed'] = bool(before_replay == STATUS_ESCROWED and replayed.status == STATUS_COMMITTED
                                 and same_receipt and replay.get('applied') is True
                                 and replay.get('before') == m0 and replay.get('after') == m0 + 1
                                 and n2 == n1 and m2 == m1)

        # A: receiver killed after the sender removed, before the receiver add was ever enqueued.
        proc = (procs or {}).get(receiver)
        killed = {}

        def kill_receiver(realm, op_id):
            if proc is None or not oracle.same_image(proc.pid, oracle.GAMES[receiver]):
                raise RuntimeError('refusing to kill: receiver PID is not the lab executable')
            proc.kill()
            proc.wait(timeout=30)
            killed.update(pid=proc.pid, returncode=proc.returncode)

        channel.before_enqueue['add'] = kill_receiver
        second = transfer(store, channel, sender, saves[sender], receiver, saves[receiver], item, 1,
                          timeout=timeout, idempotency_key='crash-oracle-' + stamp + '-second',
                          bridge_profile_id=profile_id)
        n_esc = query(sender, 'a-escrow')
        state = journal_op_state(journals[receiver], f'{second.transfer_id}:add')
        refunded = refund_after_receiver_loss(store, host, second.transfer_id,
                                              receiver_dead=proc is not None and proc.poll() is not None,
                                              receiver_journal_state=state, timeout=timeout)
        n3 = query(sender, 'n3')
        row = store.get(second.transfer_id)
        store.close()
        ev['A'] = {'killed': killed, 'status_after_crash': second.status, 'sender_while_escrowed': n_esc,
                   'receiver_add_journal_state': state, 'refund_status': refunded.status,
                   'refund_error': refunded.error, 'refund_steps': refunded.steps, 'source': n3,
                   'ledger_status': row.status if row else None}
        ev['A']['passed'] = (second.status == STATUS_ESCROWED and n_esc == n1 - 1 and state == 'absent'
                             and refunded.status == STATUS_REFUNDED and n3 == n1)
        ev['passed'] = ev['C']['passed'] and ev['B']['passed'] and ev['A']['passed']
        print('[crash-oracle] C=%s B=%s A=%s' % (ev['C']['passed'], ev['B']['passed'], ev['A']['passed']), flush=True)
        return ev
    return scenario


def main():
    parser = argparse.ArgumentParser(description='Crash/restart transfer oracle (LAB copies only)')
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
    run_dir = oracle.REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'transfer' / ('crash-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    run_dir.mkdir(parents=True, exist_ok=False)
    journals = {r: run_dir / f'{r}-ops.journal' for r in ('SR1', 'SR2')}
    authority = {'profiles_path': str(profiles_path),
                 'journal_path': str(run_dir / 'counterpart-journal.sqlite'),
                 'store_path': str(run_dir / 'transfers.db'),
                 'profile_id': args.profile_id,
                 'trust': {f'{PROVIDER_PREFIX}.{realm}': realm for realm in ('SR1', 'SR2')},
                 'allow_lab_give': True}
    extra_env = {r: {'SLIMEBRIDGE_OP_JOURNAL': str(journals[r]),
                     'SLIMEBRIDGE_LAB_GIVE': '1' if r == args.sender else '0'} for r in ('SR1', 'SR2')}
    scenario = make_scenario(args.sender, receiver, args.item, run_dir / 'transfers.db', journals,
                             args.command_timeout, profile_id=args.profile_id, authority=authority)
    result = oracle.run(['SR1', 'SR2'], args.timeout, False, run_dir, require_status=True, current_user=True,
                        sr1_save_id=endpoints['SR1']['native_save_id'],
                        sr2_save_id=endpoints['SR2']['native_save_id'],
                        scenario=scenario, extra_env=extra_env, transfer_authority=authority)
    print(json.dumps({'passed': result['passed'], 'scenario': result.get('scenario'),
                      'saves_match_backup_after_restore': result.get('saves_match_backup_after_restore'),
                      'sources_untouched': result.get('sources_untouched'), 'evidence': str(run_dir)},
                     indent=2, default=str))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
