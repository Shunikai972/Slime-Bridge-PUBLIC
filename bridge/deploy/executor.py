"""Journaled deployment executor: stage, check preconditions, apply with backups, verify, commit.

Transaction protocol (``DeployJournal``, SQLite, ``BEGIN IMMEDIATE``)::

    PLANNED -> STAGED -> APPLYING -> COMMITTED
                          \\-> ROLLING_BACK -> ROLLED_BACK   (any failure or crash before commit)
                                         \\-> FAILED          (a backup could not be restored)

Every file change is journaled *before* it is made (intent row with the before-hash and backup
reference) and marked done after. Recovery after a crash rolls back an APPLYING transaction by
restoring backups in reverse order. The inventory changes only in the COMMIT transaction, so a
reader never sees a half-applied deployment. Results never claim partial success: either the whole
plan is COMMITTED or the target is restored (ROLLED_BACK), or FAILED lists exactly what could not be
restored.

``TargetFS``: ``read(path) -> bytes | None``, ``write(path, data)`` (atomic), ``delete(path)`` and
optionally ``check(path) -> problem | None`` (a target the FS could not replace or delete, refused
before anything is applied). Paths are the plan's area-qualified relative paths plus the executor's
own ``.bridge/`` area. Rollback is idempotent: a step whose path already holds its before-state is
marked UNDONE without writing, and any per-step error lands the transaction in FAILED (never a
recovery loop).
"""

import hashlib
import json
import sqlite3
from pathlib import Path, PurePosixPath

from bridge.cache.fs import atomic_write, confined_root
from bridge.content.canonical import canonical_json, sha256_json
from bridge.core import diagnostics as D
from bridge.core.paths import safe_relative
from bridge.sim.faults import FaultPlan
from .planner import placed

TXN_STATES = ('PLANNED', 'STAGED', 'APPLYING', 'COMMITTED', 'ROLLING_BACK', 'ROLLED_BACK', 'FAILED')
_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS txns (txn_id INTEGER PRIMARY KEY AUTOINCREMENT, realm TEXT NOT NULL,'
    ' plan_id TEXT NOT NULL, plan TEXT NOT NULL, state TEXT NOT NULL, detail TEXT)',
    'CREATE TABLE IF NOT EXISTS steps (txn_id INTEGER NOT NULL, seq INTEGER NOT NULL, path TEXT NOT NULL,'
    ' action TEXT NOT NULL, before_sha TEXT, after_sha TEXT, backup TEXT, state TEXT NOT NULL,'
    ' PRIMARY KEY (txn_id, seq))',
    'CREATE TABLE IF NOT EXISTS inventory (realm TEXT PRIMARY KEY, doc TEXT NOT NULL, txn_id INTEGER)',
    'CREATE TABLE IF NOT EXISTS history (key TEXT NOT NULL, unit_id TEXT NOT NULL, unit TEXT NOT NULL,'
    ' txn_id INTEGER NOT NULL, PRIMARY KEY (key, unit_id))',
)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class DeployError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


class DeployJournal:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA synchronous=FULL')
        with self.tx() as db:
            for statement in _SCHEMA:
                db.execute(statement)
            if db.execute("SELECT 1 FROM meta WHERE key='schema_version'").fetchone() is None:
                db.execute("INSERT INTO meta VALUES ('schema_version', '1')")

    def close(self):
        self._db.close()

    def tx(self):
        db = self._db

        class Tx:
            def __enter__(self_inner):
                db.execute('BEGIN IMMEDIATE')
                return db

            def __exit__(self_inner, exc_type, *_):
                db.execute('ROLLBACK' if exc_type else 'COMMIT')
        return Tx()

    def inventory(self, realm, build=None):
        row = self._db.execute('SELECT doc FROM inventory WHERE realm=?', (realm,)).fetchone()
        if row is None:
            return {'schema': 'bridge-deploy-inventory', 'schema_version': 1, 'realm': realm, 'build': build,
                    'units': {}}
        return json.loads(row['doc'])

    def history(self):
        out = {}
        for row in self._db.execute('SELECT key, unit FROM history ORDER BY txn_id, key'):
            out.setdefault(row['key'], []).append(json.loads(row['unit']))
        return out

    def txn(self, txn_id):
        row = self._db.execute('SELECT * FROM txns WHERE txn_id=?', (txn_id,)).fetchone()
        return dict(row) if row else None

    def steps(self, txn_id):
        return [dict(r) for r in self._db.execute('SELECT * FROM steps WHERE txn_id=? ORDER BY seq', (txn_id,))]

    def open_txns(self):
        return [r['txn_id'] for r in self._db.execute(
            "SELECT txn_id FROM txns WHERE state IN ('PLANNED','STAGED','APPLYING','ROLLING_BACK') ORDER BY txn_id")]

    def set_state(self, txn_id, state, detail=None):
        with self.tx() as db:
            db.execute('UPDATE txns SET state=?, detail=? WHERE txn_id=?', (state, detail, txn_id))


class DeployExecutor:
    """Executes one checked plan against a TargetFS; ``artifacts(sha256) -> bytes | None`` supplies content."""

    def __init__(self, journal, fs, artifacts, *, faults=None):
        self.journal, self.fs, self.artifacts = journal, fs, artifacts
        self.faults = faults or FaultPlan()

    def _fail(self, txn_id, code, message):
        return {'txn_id': txn_id, 'state': self.journal.txn(txn_id)['state'],
                'diagnostics': [D.make(code, 'error', 'deploy', message)]}

    def apply(self, plan):
        if not plan.get('executable'):
            raise DeployError('PLAN_NOT_EXECUTABLE', 'plan has blockers')
        inventory = self.journal.inventory(plan['realm'])
        if sha256_json(inventory) != plan['base_inventory']:
            raise DeployError('STALE_PLAN', 'inventory changed since planning')
        if self.journal.open_txns():
            raise DeployError('TXN_IN_PROGRESS', 'recover the open transaction first')
        with self.journal.tx() as db:
            cur = db.execute('INSERT INTO txns(realm, plan_id, plan, state) VALUES (?,?,?,?)',
                             (plan['realm'], plan['plan_id'], canonical_json(plan), 'PLANNED'))
            txn_id = cur.lastrowid
        # 1. Stage every new file (verified by hash) without touching live paths.
        staged = {}
        local = {}
        for op in plan['operations']:
            for path, digest in op['preconditions']:
                local.setdefault(digest, path)
        for op in plan['operations']:
            for path, digest in op['put']:
                if digest in staged:
                    continue
                data = None
                if digest in local:
                    data = self.fs.read(local[digest])
                if data is None or _sha(data) != digest:
                    data = self.artifacts(digest)
                if data is None or _sha(data) != digest:
                    self.journal.set_state(txn_id, 'ROLLED_BACK', 'artifact unavailable or corrupt')
                    return self._fail(txn_id, 'ARTIFACT_UNAVAILABLE', f'no verified bytes for {digest[:12]}')
                stage_path = f'.bridge/staging/{txn_id}/{digest}'
                self.fs.write(stage_path, data)
                staged[digest] = stage_path
        self.journal.set_state(txn_id, 'STAGED')
        # 2. Preconditions: every file we will replace or delete is exactly what the inventory says.
        for op in plan['operations']:
            for path, digest in op['preconditions']:
                current = self.fs.read(path)
                if current is None or _sha(current) != digest:
                    self.journal.set_state(txn_id, 'ROLLED_BACK', f'drift at {path}')
                    self._cleanup(txn_id, staged)
                    return self._fail(txn_id, 'TARGET_DRIFT', f'{path} differs from the inventory')
        for op in plan['operations']:
            for path, _ in op['put']:
                if not any(path == p for p, _ in op['preconditions']) and self.fs.read(path) is not None:
                    self.journal.set_state(txn_id, 'ROLLED_BACK', f'untracked file at {path}')
                    self._cleanup(txn_id, staged)
                    return self._fail(txn_id, 'UNTRACKED_FILE', f'{path} exists but is not Bridge-owned')
        check = getattr(self.fs, 'check', None)
        for op in plan['operations'] if check else ():
            for path in [*op['delete'], *(p for p, _ in op['put'])]:
                problem = check(path)
                if problem:
                    self.journal.set_state(txn_id, 'ROLLED_BACK', f'unwritable target {path}')
                    self._cleanup(txn_id, staged)
                    return self._fail(txn_id, 'TARGET_UNWRITABLE', f'{path}: {problem}')
        # 3. Apply with journaled backups.
        self.journal.set_state(txn_id, 'APPLYING')
        seq = 0
        try:
            for op in plan['operations']:
                for path in op['delete']:
                    seq += 1
                    self._step(txn_id, seq, path, 'DELETE', None)
                for path, digest in op['put']:
                    seq += 1
                    self._step(txn_id, seq, path, 'PUT', digest, staged[digest])
            # 4. Postconditions over the whole final state.
            for key, entry in plan['final_units'].items():
                for path, digest in placed(entry['unit'], entry['state']).items():
                    data = self.fs.read(path)
                    if self.faults.check('deploy.postcondition', path=path) or data is None or _sha(data) != digest:
                        raise DeployError('POSTCONDITION_FAILED', f'{path} not as planned')
        except _Crash:
            return {'txn_id': txn_id, 'state': 'APPLYING', 'diagnostics': [
                D.make('SIMULATED_CRASH', 'warning', 'deploy', 'executor stopped mid-transaction')]}
        except Exception as exc:  # any failure before commit: restore everything
            state = self._rollback(txn_id)
            return {'txn_id': txn_id, 'state': state,
                    'diagnostics': [D.make(getattr(exc, 'code', 'APPLY_FAILED'), 'error', 'deploy', str(exc)[:200])]}
        # 5. Commit: inventory + history in one transaction.
        final_inventory = {'schema': 'bridge-deploy-inventory', 'schema_version': 1, 'realm': plan['realm'],
                           'build': plan['build'], 'units': plan['final_units']}
        with self.journal.tx() as db:
            db.execute('INSERT OR REPLACE INTO inventory VALUES (?,?,?)',
                       (plan['realm'], canonical_json(final_inventory), txn_id))
            for key, entry in plan['final_units'].items():
                db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?,?)',
                           (key, entry['unit']['unit_id'], canonical_json(entry['unit']), txn_id))
            db.execute("UPDATE txns SET state='COMMITTED' WHERE txn_id=?", (txn_id,))
        self._cleanup(txn_id, staged)
        for step in self.journal.steps(txn_id):  # a committed transaction never needs its backups again
            if step['backup'] is not None:
                self.fs.delete(step['backup'])
        return {'txn_id': txn_id, 'state': 'COMMITTED', 'diagnostics': []}

    def _step(self, txn_id, seq, path, action, digest, stage_path=None):
        if not safe_relative(path):
            raise DeployError('UNSAFE_PATH', path[:200])
        before = self.fs.read(path)
        backup = None
        if before is not None:
            backup = f'.bridge/backup/{txn_id}/{seq}'
            self.fs.write(backup, before)
        with self.journal.tx() as db:
            db.execute('INSERT INTO steps VALUES (?,?,?,?,?,?,?,?)',
                       (txn_id, seq, path, action, before and _sha(before), digest, backup, 'INTENT'))
        if self.faults.check('deploy.crash_before_write', path=path):
            raise _Crash()
        if self.faults.check('deploy.write', path=path):
            raise OSError(f'simulated write failure at {path}')
        if action == 'PUT':
            self.fs.write(path, self.fs.read(stage_path))
        else:
            self.fs.delete(path)
        with self.journal.tx() as db:
            db.execute("UPDATE steps SET state='DONE' WHERE txn_id=? AND seq=?", (txn_id, seq))
        if self.faults.check('deploy.crash_after_step', path=path):
            raise _Crash()

    def _rollback(self, txn_id):
        self.journal.set_state(txn_id, 'ROLLING_BACK')
        unrestored = []
        for step in reversed(self.journal.steps(txn_id)):
            if step['state'] == 'UNDONE':
                continue
            if self.faults.check('deploy.crash_during_rollback', path=step['path']):
                raise _Crash()  # process dies mid-rollback; recover() resumes from the UNDONE marks
            try:
                if self.faults.check('deploy.rollback_write', path=step['path']):
                    raise OSError('simulated rollback write failure')
                current = self.fs.read(step['path'])
                if (current and _sha(current)) == step['before_sha']:
                    pass  # already in its before-state (crash before the write, or a resumed rollback)
                elif step['backup'] is not None:
                    data = self.fs.read(step['backup'])
                    if data is None or _sha(data) != step['before_sha']:
                        raise OSError('backup missing or corrupt')
                    self.fs.write(step['path'], data)
                else:
                    self.fs.delete(step['path'])
                with self.journal.tx() as db:
                    db.execute("UPDATE steps SET state='UNDONE' WHERE txn_id=? AND seq=?", (txn_id, step['seq']))
            except _Crash:
                raise
            except Exception:  # noqa: BLE001 - one unrestorable step must not abort the others or loop recovery
                unrestored.append(step['path'])
        if unrestored:
            self.journal.set_state(txn_id, 'FAILED', canonical_json({'unrestored': sorted(set(unrestored))}))
            return 'FAILED'
        self.journal.set_state(txn_id, 'ROLLED_BACK')
        return 'ROLLED_BACK'

    def _cleanup(self, txn_id, staged):
        for stage_path in staged.values():
            self.fs.delete(stage_path)

    def recover(self):
        """Roll back every transaction left open by a crash; returns {txn_id: final state}."""
        return {txn_id: self._rollback(txn_id) for txn_id in self.journal.open_txns()}


class _Crash(Exception):
    """Simulated process death (fault injection only): leaves the journal exactly as a crash would."""


class MemoryFS:
    """Synthetic in-memory TargetFS for Cloud tests and demo mode."""

    def __init__(self, files=None):
        self.files = dict(files or {})

    def read(self, path):
        return self.files.get(path)

    def write(self, path, data):
        self.files[path] = bytes(data)

    def delete(self, path):
        self.files.pop(path, None)


class DirectoryFS:
    """TargetFS confined to one Bridge-owned directory (atomic, link-refusing). Never a game folder."""

    def __init__(self, root):
        self.root = confined_root(root)

    def _path(self, rel):
        """Confined path: plan paths must be safe; only the executor's own area may start with '.bridge'."""
        inner = rel[len('.bridge/'):] if rel.startswith('.bridge/') else None
        if not safe_relative(inner if inner is not None else rel):
            raise OSError(f'unsafe path {rel[:200]}')
        current = self.root
        for part in PurePosixPath(rel).parts:
            current = current / part
            if current.is_symlink() or current.is_junction():
                raise OSError(f'link inside the deployment root: {rel[:200]}')
        return current

    def read(self, rel):
        path = self._path(rel)
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()

    def write(self, rel, data):
        atomic_write(self.root, self._path(rel), data)

    def check(self, rel):
        """Why ``rel`` cannot be replaced or deleted safely (None when it can)."""
        try:
            path = self._path(rel)
        except OSError as exc:
            return str(exc)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return 'not a regular file'
        if path.is_file() and path.stat().st_nlink > 1:
            return 'hardlinked file'
        return None

    def delete(self, rel):
        path = self._path(rel)
        if path.is_symlink():
            raise OSError('refusing to delete through a link')
        if path.is_file():
            path.unlink()
