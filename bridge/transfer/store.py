from __future__ import annotations

import sqlite3
import threading
import uuid
import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List

from bridge.transfer.binding import BINDING_COLUMNS, TransferBinding, TransferBindingError, binding_for_row

KNOWN_REALMS = ('SR1', 'SR2')

STATUS_PREPARED = 'PREPARED'
STATUS_LOCKED = 'LOCKED'
STATUS_ESCROWED = 'ESCROWED'
STATUS_COMMITTED = 'COMMITTED'
STATUS_ABORTED = 'ABORTED'
STATUS_REFUNDED = 'REFUNDED'
STATUS_REFUND_PENDING = 'REFUND_PENDING'

VALID_TRANSFER_STATUSES = (
    STATUS_PREPARED,
    STATUS_LOCKED,
    STATUS_ESCROWED,
    STATUS_COMMITTED,
    STATUS_ABORTED,
    STATUS_REFUNDED,
    STATUS_REFUND_PENDING,
)

TERMINAL_STATUSES = (STATUS_COMMITTED, STATUS_ABORTED, STATUS_REFUNDED)
IN_FLIGHT_STATUSES = (STATUS_PREPARED, STATUS_LOCKED, STATUS_ESCROWED, STATUS_REFUND_PENDING)

# Native adapters currently support these exact canonical inventory identities.
SUPPORTED_CANONICAL_ITEMS = ('slime.pink', 'plort.pink', 'slime.ringtail')


class TransferStoreError(Exception):
    """Base exception for transfer store errors."""


class InvalidStateTransitionError(TransferStoreError):
    """Raised when attempting an illegal state transition in the 2PC protocol."""


@dataclass(frozen=True)
class TransferRow:
    transfer_id: str
    sender_realm: str
    sender_save_id: str
    receiver_realm: str
    receiver_save_id: str
    canonical_item_id: str
    count: int
    status: str
    abort_reason: Optional[str]
    created_at_utc: str
    updated_at_utc: str
    idempotency_key: Optional[str] = None
    # Additive ownership snapshot. Historical rows stay explicitly unbound.
    bridge_profile_id: Optional[str] = None
    sender_installation_id: Optional[str] = None
    sender_native_save_identity: Optional[str] = None
    receiver_installation_id: Optional[str] = None
    receiver_native_save_identity: Optional[str] = None


class TransferStore:
    """SQLite-backed persistent store implementing a durable two-phase commit log for item transfers.

    Protocol States:
      PREPARED -> LOCKED -> ESCROWED -> COMMITTED
                                     +-> REFUNDED (sender refunded)
      PREPARED | LOCKED -------------> ABORTED (no items decremented)

    Guarantees:
    - Durable across restarts (WAL mode, fsync on commits).
    - Atomic transitions under BEGIN IMMEDIATE.
    - In-flight recovery queries to determine required recovery actions on startup.
    """
    _guard_lock = threading.Lock()
    _guards: dict[tuple[str, str], threading.Lock] = {}

    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = sqlite3.connect(
            str(self._path), timeout=30.0, check_same_thread=False
        )
        try:
            self._conn.execute('PRAGMA busy_timeout = 30000')
            self._conn.execute('PRAGMA journal_mode = WAL')
            self._conn.execute('PRAGMA synchronous = FULL')
            self._init_schema()
        except BaseException:
            self.close()
            raise

    @property
    def database_path(self) -> Path:
        return self._path.resolve()

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        with getattr(self, '_lock', threading.Lock()):
            conn = getattr(self, '_conn', None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                self._conn = None

    def __del__(self) -> None:
        self.close()

    def __enter__(self) -> TransferStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise TransferStoreError('Store is closed')
        return self._conn

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS transfers (
                        transfer_id TEXT PRIMARY KEY,
                        sender_realm TEXT NOT NULL,
                        sender_save_id TEXT NOT NULL,
                        receiver_realm TEXT NOT NULL,
                        receiver_save_id TEXT NOT NULL,
                        canonical_item_id TEXT NOT NULL,
                        count INTEGER NOT NULL,
                        status TEXT NOT NULL DEFAULT 'PREPARED',
                        abort_reason TEXT,
                        created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                        updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )
                ''')
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status)
                ''')
                columns = {row[1] for row in conn.execute('PRAGMA table_info(transfers)')}
                if 'idempotency_key' not in columns:
                    conn.execute('ALTER TABLE transfers ADD COLUMN idempotency_key TEXT')
                conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_idempotency ON transfers(idempotency_key)')
                for column in BINDING_COLUMNS:
                    if column not in columns:
                        # Never infer legacy ownership from a currently selected profile.
                        conn.execute(f'ALTER TABLE transfers ADD COLUMN {column} TEXT')
                conn.execute('''CREATE TABLE IF NOT EXISTS receiver_deliveries (
                    transfer_id TEXT PRIMARY KEY, dispatched INTEGER NOT NULL CHECK(dispatched IN (0, 1)),
                    refusal_json TEXT)''')
                # Old ledgers have no dispatch history: never interpret missing history as non-delivery.
                conn.execute('''INSERT OR IGNORE INTO receiver_deliveries (transfer_id, dispatched)
                    SELECT transfer_id, 1 FROM transfers''')

    @contextmanager
    def transfer_guard(self, transfer_id: str):
        """Serialize a complete external-effect decision across threads and processes."""
        key = (str(self._path.resolve()), transfer_id)
        with self._guard_lock:
            guard = self._guards.setdefault(key, threading.Lock())
        with guard:
            lock_dir = self._path.parent / (self._path.name + '.locks')
            lock_dir.mkdir(exist_ok=True)
            lock_path = lock_dir / (hashlib.sha256(transfer_id.encode()).hexdigest() + '.lock')
            with open(lock_path, 'a+b') as stream:
                stream.seek(0)
                if stream.read(1) == b'':
                    stream.seek(0)
                    stream.write(b'0')
                    stream.flush()
                if os.name == 'nt':
                    import msvcrt
                    while True:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                            break
                        except OSError:
                            time.sleep(0.05)
                    try:
                        yield
                    finally:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def prepare_transfer(
        self,
        sender_realm: str,
        sender_save_id: str,
        receiver_realm: str,
        receiver_save_id: str,
        canonical_item_id: str,
        count: int = 1,
        idempotency_key: str | None = None,
        *, binding: TransferBinding | None = None,
    ) -> str:
        if not isinstance(binding, TransferBinding):
            raise TransferBindingError('resolved profile/pair binding is required')
        binding.require_arguments(sender_realm, sender_save_id, receiver_realm, receiver_save_id)
        if sender_realm not in KNOWN_REALMS or receiver_realm not in KNOWN_REALMS:
            raise ValueError(f'Unknown realm: {sender_realm} or {receiver_realm}')
        if sender_realm == receiver_realm:
            raise ValueError('Sender and receiver realms must differ')
        if type(count) is not int or count < 1 or count > 30:
            raise ValueError('count must be an integer between 1 and 30 (max slot stack)')
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128 or not idempotency_key.strip():
            raise ValueError('idempotency_key must be a non-empty caller-owned string of at most 128 characters')
        if canonical_item_id not in SUPPORTED_CANONICAL_ITEMS:
            raise ValueError(f'Unsupported canonical item: {canonical_item_id}')
        for field_name, val in [('sender_save_id', sender_save_id), ('receiver_save_id', receiver_save_id)]:
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f'{field_name} must be a non-empty string')

        transfer_id = str(uuid.uuid4())
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                existing = conn.execute('''SELECT transfer_id, sender_realm, sender_save_id, receiver_realm,
                    receiver_save_id, canonical_item_id, count, bridge_profile_id,
                    sender_installation_id, sender_native_save_identity,
                    receiver_installation_id, receiver_native_save_identity
                    FROM transfers WHERE idempotency_key = ?''', (idempotency_key,)).fetchone()
                params = (sender_realm, sender_save_id, receiver_realm, receiver_save_id,
                          canonical_item_id, count, *binding.ledger_values)
                if existing:
                    if existing[1:] != params:
                        raise ValueError('idempotency_key already bound to different transfer parameters or ownership')
                    return existing[0]
                values = (transfer_id, *params, STATUS_PREPARED, idempotency_key, now, now)
                conn.execute(f'''INSERT INTO transfers (
                    transfer_id, sender_realm, sender_save_id, receiver_realm, receiver_save_id,
                    canonical_item_id, count, bridge_profile_id, sender_installation_id,
                    sender_native_save_identity, receiver_installation_id, receiver_native_save_identity,
                    status, idempotency_key, created_at_utc, updated_at_utc)
                    VALUES ({','.join('?' for _ in values)})''', values)
                conn.execute('INSERT INTO receiver_deliveries (transfer_id, dispatched) VALUES (?, 0)',
                             (transfer_id,))
        return transfer_id

    def get(self, transfer_id: str) -> Optional[TransferRow]:
        with self._lock:
            conn = self._require_conn()
            row = conn.execute('''
                SELECT transfer_id, sender_realm, sender_save_id, receiver_realm,
                       receiver_save_id, canonical_item_id, count, status,
                       abort_reason, created_at_utc, updated_at_utc, idempotency_key,
                       bridge_profile_id, sender_installation_id, sender_native_save_identity,
                       receiver_installation_id, receiver_native_save_identity
                FROM transfers WHERE transfer_id = ?
            ''', (transfer_id,)).fetchone()
            if not row:
                return None
            return TransferRow(*row)

    def lock(self, transfer_id: str) -> None:
        """Transition PREPARED -> LOCKED (sender has verified slot and capacity)."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if not row:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_LOCKED:
                    return  # idempotent
                if row.status != STATUS_PREPARED:
                    raise InvalidStateTransitionError(f'Cannot lock transfer in status {row.status}')
                conn.execute('''
                    UPDATE transfers
                    SET status = ?, updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE transfer_id = ?
                ''', (STATUS_LOCKED, transfer_id))

    def escrow(self, transfer_id: str) -> None:
        """Transition LOCKED -> ESCROWED (sender vac has successfully decremented item)."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if not row:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_ESCROWED:
                    return  # idempotent
                if row.status != STATUS_LOCKED:
                    raise InvalidStateTransitionError(f'Cannot escrow transfer in status {row.status}')
                conn.execute('''
                    UPDATE transfers
                    SET status = ?, updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE transfer_id = ?
                ''', (STATUS_ESCROWED, transfer_id))

    def commit(self, transfer_id: str) -> None:
        """Transition ESCROWED -> COMMITTED (receiver vac has successfully incremented item)."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if not row:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_COMMITTED:
                    return  # idempotent
                if row.status != STATUS_ESCROWED:
                    raise InvalidStateTransitionError(f'Cannot commit transfer in status {row.status}')
                conn.execute('''
                    UPDATE transfers
                    SET status = ?, updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE transfer_id = ?
                ''', (STATUS_COMMITTED, transfer_id))

    def abort(self, transfer_id: str, reason: str = '') -> None:
        """Transition PREPARED|LOCKED -> ABORTED (no items decremented or sender uncommitted)."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if not row:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_ABORTED:
                    return  # idempotent
                if row.status not in (STATUS_PREPARED, STATUS_LOCKED):
                    raise InvalidStateTransitionError(f'Cannot abort transfer in status {row.status}; must refund if escrowed')
                conn.execute('''
                    UPDATE transfers
                    SET status = ?, abort_reason = ?, updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE transfer_id = ?
                ''', (STATUS_ABORTED, reason, transfer_id))

    def refund(self, transfer_id: str, reason: str = '') -> None:
        """Transition ESCROWED -> REFUNDED (receiver failed/disconnected; sender re-incremented item)."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if not row:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_REFUNDED:
                    return  # idempotent
                if row.status != STATUS_REFUND_PENDING:
                    raise InvalidStateTransitionError(f'Cannot refund transfer in status {row.status}; refund decision required')
                conn.execute('''
                    UPDATE transfers
                    SET status = ?, abort_reason = ?, updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    WHERE transfer_id = ?
                ''', (STATUS_REFUNDED, reason, transfer_id))

    def mark_receiver_dispatched(self, transfer_id: str, realm: str, command: dict) -> bool:
        """Commit delivery history before exposing an add to the plugin; races use the same DB lock."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if (row is None or realm != row.receiver_realm or command.get('kind') != 'add'
                        or command.get('op_id') != transfer_id + ':add'
                        or command.get('item') != row.canonical_item_id
                        or command.get('count') != row.count or command.get('save_id') != row.receiver_save_id):
                    raise TransferStoreError('receiver delivery does not match ledger')
                if row.status != STATUS_ESCROWED:
                    return False
                changed = conn.execute('UPDATE receiver_deliveries SET dispatched = 1 WHERE transfer_id = ?',
                                       (transfer_id,)).rowcount
                if changed != 1:
                    raise TransferStoreError('receiver dispatch history unavailable')
                return True

    def begin_refund(self, transfer_id: str, reason: str, *, receiver_refusal: dict | None = None) -> None:
        """Durably fence receiver delivery before any sender refund is issued."""
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = self._get_unlocked(conn, transfer_id)
                if row is None:
                    raise TransferStoreError(f'Transfer {transfer_id} not found')
                if row.status == STATUS_REFUND_PENDING:
                    return
                if row.status != STATUS_ESCROWED:
                    raise InvalidStateTransitionError(f'Cannot begin refund in status {row.status}')
                evidence = None
                if receiver_refusal is not None:
                    refusal = receiver_refusal
                    if (refusal.get('realm') != row.receiver_realm
                            or refusal.get('op_id') != transfer_id + ':add' or refusal.get('kind') != 'add'
                            or refusal.get('item') != row.canonical_item_id
                            or type(refusal.get('count')) is not int or refusal['count'] != row.count
                            or refusal.get('save_id') != row.receiver_save_id
                            or refusal.get('ok') is not False or refusal.get('applied') is not False
                            or refusal.get('uncertain') is not False or type(refusal.get('before')) is not int
                            or type(refusal.get('after')) is not int or refusal['before'] < 0
                            or refusal['before'] != refusal['after']):
                        raise InvalidStateTransitionError('receiver refusal does not prove no native effect')
                    evidence = json.dumps(refusal, sort_keys=True, allow_nan=False)
                delivery = conn.execute('SELECT dispatched FROM receiver_deliveries WHERE transfer_id = ?',
                                        (transfer_id,)).fetchone()
                if delivery != (0,) and evidence is None:
                    raise InvalidStateTransitionError('receiver add may have been delivered; reconciliation required')
                if evidence is not None:
                    conn.execute('UPDATE receiver_deliveries SET refusal_json = ? WHERE transfer_id = ?',
                                 (evidence, transfer_id))
                conn.execute('''UPDATE transfers SET status = ?, abort_reason = ?,
                    updated_at_utc = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE transfer_id = ?''',
                    (STATUS_REFUND_PENDING, reason, transfer_id))

    def list_in_flight(self) -> List[TransferRow]:
        """Return all transfers in non-terminal states (PREPARED, LOCKED, ESCROWED) for crash recovery."""
        with self._lock:
            conn = self._require_conn()
            rows = conn.execute(f'''
                SELECT transfer_id, sender_realm, sender_save_id, receiver_realm,
                       receiver_save_id, canonical_item_id, count, status,
                       abort_reason, created_at_utc, updated_at_utc, idempotency_key,
                       bridge_profile_id, sender_installation_id, sender_native_save_identity,
                       receiver_installation_id, receiver_native_save_identity
                FROM transfers
                WHERE status IN ({','.join('?' for _ in IN_FLIGHT_STATUSES)})
                ORDER BY created_at_utc ASC
            ''', IN_FLIGHT_STATUSES).fetchall()
            return [TransferRow(*r) for r in rows]

    @staticmethod
    def _get_unlocked(conn: sqlite3.Connection, transfer_id: str) -> Optional[TransferRow]:
        row = conn.execute('''
            SELECT transfer_id, sender_realm, sender_save_id, receiver_realm,
                   receiver_save_id, canonical_item_id, count, status,
                   abort_reason, created_at_utc, updated_at_utc, idempotency_key,
                   bridge_profile_id, sender_installation_id, sender_native_save_identity,
                   receiver_installation_id, receiver_native_save_identity
            FROM transfers WHERE transfer_id = ?
        ''', (transfer_id,)).fetchone()
        if not row:
            return None
        result = TransferRow(*row)
        binding_for_row(result)  # Mutating paths cannot infer ownership for a legacy row.
        return result
