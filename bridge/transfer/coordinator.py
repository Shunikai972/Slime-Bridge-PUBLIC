"""Drive one cross-game transfer through the durable ledger and the two plugins' native inventories.

The host never trusts its own counters: every step is a command executed by the game plugin on its
main thread, and the plugin's reported native before/after counts decide the ledger transition.

    query both -> PREPARED -> LOCKED -> sender remove (native) -> ESCROWED
               -> receiver add (native) -> COMMITTED
    sender remove refused (nothing removed)            -> ABORTED
    receiver add refused (nothing added)               -> sender re-add (native) -> REFUNDED
    any uncertain / missing result                      -> left in flight for reconciliation

Command op_ids are derived from the transfer id, so a retried step after a host restart reuses the
same op_id and the plugin's journal guarantees it is applied at most once.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol

from bridge.transfer.binding import TransferBinding, TransferBindingError, binding_for_row

from bridge.transfer.store import (STATUS_ESCROWED, STATUS_REFUNDED, STATUS_REFUND_PENDING,
                                   STATUS_LOCKED, STATUS_PREPARED, TransferRow,
                                   InvalidStateTransitionError, TransferStore)


class CommandChannel(Protocol):
    def bind_transfer_store(self, store: TransferStore) -> bool: ...
    def resolve_transfer_binding(self, profile_id, sender, sender_save, receiver, receiver_save) -> TransferBinding: ...
    def validate_transfer(self, row: TransferRow, *, realms=None) -> TransferBinding: ...
    def enqueue_command(self, realm: str, kind: str, item: str, count: int = 1,
                        op_id: str | None = None, save_id: str | None = None) -> str: ...

    def wait_result(self, op_id: str, timeout: float) -> dict | None: ...

    def cancel_command(self, realm: str, op_id: str) -> bool: ...


@dataclass
class TransferOutcome:
    transfer_id: str
    status: str
    steps: list = field(default_factory=list)
    error: Optional[str] = None


def run_command(channel: CommandChannel, realm: str, kind: str, item: str, count: int,
                op_id: str, save_id: str | None, timeout: float) -> dict | None:
    channel.enqueue_command(realm, kind, item, count, op_id=op_id, save_id=save_id)
    result = channel.wait_result(op_id, timeout)
    # Only a result from the addressed realm, for this exact op, about this item and save, counts.
    if result is not None and (result.get('realm') != realm or result.get('op_id') != op_id
                               or result.get('kind') != kind or result.get('item') != item
                               or type(result.get('count')) is not int or result['count'] != count
                               or result.get('save_id') != save_id):
        return None
    return result


def _bind_channel(store: TransferStore, channel: CommandChannel) -> None:
    bind = getattr(channel, 'bind_transfer_store', None)
    if not callable(bind) or bind(store) is not True:
        raise ValueError('transfer command channel must bind the same durable ledger')
    if not all(callable(getattr(channel, name, None)) for name in
               ('resolve_transfer_binding', 'validate_transfer')):
        raise TransferBindingError('transfer channel must attest profile/pair and live native endpoints')


def _validate_channel(channel, row, *, realms=None):
    recorded = binding_for_row(row)
    try:
        observed = channel.validate_transfer(row, realms=realms)
    except TransferBindingError:
        raise
    except Exception as exc:
        raise TransferBindingError('current transfer authority unavailable') from exc
    if not isinstance(observed, TransferBinding) or observed != recorded:
        raise TransferBindingError('channel authority differs from durable ownership')
    return recorded


def _get(store: TransferStore, tid: str) -> TransferRow:
    row = store.get(tid)
    if row is None:
        raise KeyError(tid)
    return row


def _refused(result: dict | None) -> bool:
    """A clean refusal: the plugin did not change the native count."""
    return (result is not None and result.get('ok') is False and result.get('applied') is False
            and result.get('uncertain') is False and type(result.get('before')) is int
            and type(result.get('after')) is int and result['before'] >= 0
            and result['before'] == result['after'])


def _succeeded(result: dict | None, expected_delta: int) -> bool:
    if (result is None or result.get('ok') is not True or result.get('applied') is not True
            or result.get('uncertain') is not False or result.get('persisted') is not True
            or result.get('verified') is not True):
        return False
    before, after = result.get('before'), result.get('after')
    digest, filename = result.get('sha256'), result.get('save_file')
    return (type(before) is int and type(after) is int and before >= 0 and after >= 0
            and after - before == expected_delta and isinstance(digest, str)
            and re.fullmatch(r'[0-9a-fA-F]{64}', digest) is not None
            and isinstance(filename, str) and 0 < len(filename) <= 128
            and filename not in ('.', '..') and '/' not in filename and '\\' not in filename)


def transfer(store: TransferStore, channel: CommandChannel, sender: str, sender_save: str,
             receiver: str, receiver_save: str, item: str, count: int = 1, timeout: float = 30.0,
             log: Callable[[str], None] = lambda _m: None, *, idempotency_key: str | None = None,
             bridge_profile_id: str | None = None) -> TransferOutcome:
    _bind_channel(store, channel)
    binding = channel.resolve_transfer_binding(bridge_profile_id, sender, sender_save, receiver, receiver_save)
    if not isinstance(binding, TransferBinding) or binding.bridge_profile_id != bridge_profile_id:
        raise TransferBindingError('channel did not resolve the requested profile/pair')
    binding.require_arguments(sender, sender_save, receiver, receiver_save)
    tid = store.prepare_transfer(sender, sender_save, receiver, receiver_save, item, count,
                                 idempotency_key=idempotency_key, binding=binding)
    outcome = TransferOutcome(tid, STATUS_PREPARED)
    return resume(store, channel, tid, timeout=timeout, log=log, outcome=outcome)


def resume(store: TransferStore, channel: CommandChannel, tid: str, timeout: float = 30.0,
           log: Callable[[str], None] = lambda _m: None, outcome: TransferOutcome | None = None) -> TransferOutcome:
    """Advance an in-flight transfer from whatever durable state it is in. Safe to call repeatedly."""
    _bind_channel(store, channel)
    with store.transfer_guard(tid):
        try:
            return _resume_guarded(store, channel, tid, timeout, log, outcome)
        except TransferBindingError as exc:
            row = _get(store, tid)
            outcome = outcome or TransferOutcome(tid, row.status)
            outcome.status, outcome.error = row.status, str(exc)
            return outcome


def _resume_guarded(store: TransferStore, channel: CommandChannel, tid: str, timeout: float,
                    log: Callable[[str], None], outcome: TransferOutcome | None) -> TransferOutcome:
    row = _get(store, tid)
    outcome = outcome or TransferOutcome(tid, row.status)
    _validate_channel(channel, row, realms=() if row.status in ('COMMITTED', 'ABORTED', 'REFUNDED')
                      else (row.sender_realm,) if row.status == STATUS_REFUND_PENDING else None)

    def step(name: str, result):
        outcome.steps.append({'step': name, 'result': result})
        log(f'{tid} {name}: {result}')
        _validate_channel(channel, _get(store, tid))

    if row.status == STATUS_PREPARED:
        # Re-verify ownership and capacity in the live games immediately before reserving.
        src = run_command(channel, row.sender_realm, 'query', row.canonical_item_id, row.count, f'{tid}:q-src',
                          row.sender_save_id, timeout)
        dst = run_command(channel, row.receiver_realm, 'query', row.canonical_item_id, row.count, f'{tid}:q-dst',
                          row.receiver_save_id, timeout)
        step('query_sender', src)
        step('query_receiver', dst)
        if src is None or dst is None:
            outcome.status, outcome.error = row.status, 'query timed out'
            return outcome
        if src.get('ok') is not True or type(src.get('before')) is not int:
            outcome.status, outcome.error = row.status, 'sender query uncertain'
            return outcome
        if src['before'] < row.count:
            store.abort(tid, 'sender does not own enough items')
            outcome.status = _get(store, tid).status
            outcome.error = 'sender does not own enough items'
            return outcome
        if dst.get('ok') is not True or type(dst.get('can_add')) is not bool:
            outcome.status, outcome.error = row.status, 'receiver query uncertain'
            return outcome
        if dst['can_add'] is not True:
            store.abort(tid, 'receiver cannot accept item')
            outcome.status = _get(store, tid).status
            outcome.error = 'receiver cannot accept item'
            return outcome
        store.lock(tid)
        row = _get(store, tid)

    if row.status == STATUS_LOCKED:
        removed = run_command(channel, row.sender_realm, 'remove', row.canonical_item_id, row.count,
                              f'{tid}:remove', row.sender_save_id, timeout)
        step('sender_remove', removed)
        if _succeeded(removed, -row.count):
            store.escrow(tid)
        elif _refused(removed):
            store.abort(tid, 'sender remove refused: ' + str((removed or {}).get('reason', '')))
            outcome.status = _get(store, tid).status
            outcome.error = 'sender remove refused'
            return outcome
        else:
            # Unknown native effect: do not escrow, do not abort. Reconcile manually / by count.
            outcome.status, outcome.error = row.status, 'sender remove uncertain'
            return outcome
        row = _get(store, tid)

    if row.status == STATUS_ESCROWED:
        added = run_command(channel, row.receiver_realm, 'add', row.canonical_item_id, row.count,
                            f'{tid}:add', row.receiver_save_id, timeout)
        step('receiver_add', added)
        if _succeeded(added, row.count):
            store.commit(tid)
        elif _refused(added):
            if not callable(getattr(channel, 'cancel_command', None)):
                outcome.status, outcome.error = row.status, 'channel cancellation unavailable'
                return outcome
            store.begin_refund(tid, 'receiver add refused: ' + str(added.get('reason', '')),
                               receiver_refusal=added)
            row = _get(store, tid)
        else:
            outcome.status, outcome.error = row.status, 'receiver add uncertain or timed out'
            return outcome
    if row.status == STATUS_REFUND_PENDING:
        _finish_refund(store, channel, row, timeout, outcome, log)
    outcome.status = _get(store, tid).status
    return outcome


def _finish_refund(store: TransferStore, channel: CommandChannel, row: TransferRow,
                   timeout: float, outcome: TransferOutcome, log: Callable[[str], None]) -> None:
    _validate_channel(channel, row, realms=(row.sender_realm,))
    cancel = getattr(channel, 'cancel_command', None)
    if not callable(cancel):
        outcome.error = 'channel cancellation unavailable'
        return
    cancel(row.receiver_realm, f'{row.transfer_id}:add')
    refund = run_command(channel, row.sender_realm, 'add', row.canonical_item_id, row.count,
                         f'{row.transfer_id}:refund', row.sender_save_id, timeout)
    outcome.steps.append({'step': 'sender_refund', 'result': refund})
    log(f'{row.transfer_id} sender_refund: {refund}')
    _validate_channel(channel, _get(store, row.transfer_id), realms=(row.sender_realm,))
    if _succeeded(refund, row.count):
        store.refund(row.transfer_id, row.abort_reason or 'receiver add refused')
    else:
        outcome.error = 'refund not confirmed'


def journal_op_state(path: Path | str, op_id: str) -> str:
    """Return absent/intent/done only for a complete valid journal; otherwise unknown."""
    from bridge.transfer.journal import read_operations
    records = read_operations(path)
    if records is None:
        return 'unknown'
    intents, done = records
    return 'done' if op_id in done else 'intent' if op_id in intents else 'absent'


def journal_op_result(path: Path | str, op_id: str) -> dict | None:
    from bridge.transfer.journal import read_operations
    records = read_operations(path)
    return None if records is None else records[1].get(op_id)


def refund_after_receiver_loss(store: TransferStore, channel: CommandChannel, tid: str, receiver_dead: bool,
                               receiver_journal_state: str, timeout: float = 30.0,
                               log: Callable[[str], None] = lambda _m: None) -> TransferOutcome:
    """Refund an ESCROWED transfer whose receiver died before adding.

    Fail-closed: only when the receiver process is verified dead AND its durable journal shows the
    add never started. Pending redelivery of the add is cancelled first so a restarted receiver can
    never apply it after the sender has been re-credited.
    """
    _bind_channel(store, channel)
    with store.transfer_guard(tid):
        try:
            return _refund_after_receiver_loss_guarded(store, channel, tid, receiver_dead,
                                                       receiver_journal_state, timeout, log)
        except TransferBindingError as exc:
            row = _get(store, tid)
            return TransferOutcome(tid, row.status, error=str(exc))


def _refund_after_receiver_loss_guarded(store: TransferStore, channel: CommandChannel, tid: str,
                                        receiver_dead: bool, receiver_journal_state: str, timeout: float,
                                        log: Callable[[str], None]) -> TransferOutcome:
    row = _get(store, tid)
    outcome = TransferOutcome(tid, row.status)
    _validate_channel(channel, row, realms=() if row.status == STATUS_REFUNDED else (row.sender_realm,))
    if row.status == STATUS_REFUNDED:
        return outcome
    if row.status == STATUS_REFUND_PENDING:
        _finish_refund(store, channel, row, timeout, outcome, log)
        outcome.status = _get(store, tid).status
        return outcome
    if row.status != STATUS_ESCROWED:
        outcome.error = 'not escrowed'
        return outcome
    if not receiver_dead or receiver_journal_state != 'absent':
        outcome.error = 'receiver may have applied the add; reconcile from its journal, do not refund'
        return outcome
    if not callable(getattr(channel, 'cancel_command', None)):
        outcome.error = 'channel cancellation unavailable'
        return outcome
    try:
        store.begin_refund(tid, 'receiver lost before add')
    except InvalidStateTransitionError:
        outcome.error = 'receiver delivery history is not proven safe; reconciliation required'
        return outcome
    _finish_refund(store, channel, _get(store, tid), timeout, outcome, log)
    outcome.status = _get(store, tid).status
    return outcome
