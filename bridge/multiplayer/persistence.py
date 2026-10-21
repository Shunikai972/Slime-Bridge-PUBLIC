"""Server persistence (SQLite) with ordered, transactional, tested migrations.

Only durable, meaningful data is stored: server configuration, player identity <-> Bridge profile
associations, published content manifests, protocol/schema metadata and in-flight realm-switch
intents. Presence, chat and sessions are ephemeral and never persisted.

``MIGRATIONS`` is append-only: each entry is (version, description, statements). ``migrate`` applies
every pending migration in its own ``BEGIN IMMEDIATE`` transaction and refuses a database written by
a newer server (it never downgrades or guesses).
"""

import json
import sqlite3
from pathlib import Path

from bridge.content.canonical import canonical_json

MIGRATIONS = (
    (1, 'initial schema', (
        'CREATE TABLE server_config (id INTEGER PRIMARY KEY CHECK (id = 1), doc TEXT NOT NULL)',
        'CREATE TABLE identities (player_id TEXT PRIMARY KEY, bridge_profile_id TEXT NOT NULL,'
        ' display_name TEXT NOT NULL)',
        'CREATE TABLE content_manifests (realm TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, packages TEXT NOT NULL)',
    )),
    (2, 'protocol metadata', (
        'CREATE TABLE protocol_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
        "INSERT INTO protocol_meta VALUES ('protocol', 'bridge-net'), ('protocol_versions', '[1]')",
    )),
    (3, 'durable realm-switch intents', (
        'CREATE TABLE transition_intents (player_id TEXT NOT NULL, transition_id TEXT NOT NULL, doc TEXT NOT NULL,'
        ' PRIMARY KEY (player_id, transition_id))',
    )),
    (4, 'identity first/last association ordering', (
        'ALTER TABLE identities ADD COLUMN associations INTEGER NOT NULL DEFAULT 1',
    )),
    (5, 'at most one in-flight realm switch per player', (
        'CREATE TABLE transition_intents_v5 (player_id TEXT PRIMARY KEY, transition_id TEXT NOT NULL,'
        ' doc TEXT NOT NULL)',
        'INSERT INTO transition_intents_v5 SELECT player_id, transition_id, doc FROM transition_intents'
        ' WHERE rowid IN (SELECT MAX(rowid) FROM transition_intents GROUP BY player_id)',
        'DROP TABLE transition_intents',
        'ALTER TABLE transition_intents_v5 RENAME TO transition_intents',
    )),
)
LATEST = MIGRATIONS[-1][0]


class MigrationError(RuntimeError):
    pass


def current_version(db):
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not exists:
        return 0
    row = db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()
    return row[0] or 0


def migrate(db, *, target=LATEST, migrations=MIGRATIONS):
    """Apply pending migrations up to ``target``; returns the list of applied versions."""
    versions = [m[0] for m in migrations]
    if versions != sorted(versions) or len(set(versions)) != len(versions) or versions[0] != 1 or \
            versions != list(range(1, len(versions) + 1)):
        raise MigrationError('migrations must be numbered 1..n without gaps')
    db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, description TEXT NOT NULL)')
    have = current_version(db)
    if have > versions[-1]:
        raise MigrationError(f'database schema v{have} is newer than this server (v{versions[-1]})')
    applied = []
    for version, description, statements in migrations:
        if version <= have or version > target:
            continue
        db.execute('BEGIN IMMEDIATE')
        try:
            for statement in statements:
                db.execute(statement)
            db.execute('INSERT INTO schema_migrations VALUES (?, ?)', (version, description))
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
        applied.append(version)
    return applied


class ServerStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            self._db.execute('PRAGMA synchronous=FULL')
            self.applied = migrate(self._db)
        except BaseException:
            self._db.close()
            raise

    def close(self):
        self._db.close()

    def _write(self, sql, args=()):
        self._db.execute('BEGIN IMMEDIATE')
        try:
            self._db.execute(sql, args)
            self._db.execute('COMMIT')
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    def version(self):
        return current_version(self._db)

    def save_config(self, doc):
        self._write('INSERT OR REPLACE INTO server_config VALUES (1, ?)', (canonical_json(doc),))

    def load_config(self):
        row = self._db.execute('SELECT doc FROM server_config WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def associate(self, player_id, bridge_profile_id, display_name):
        self._write('INSERT INTO identities(player_id, bridge_profile_id, display_name) VALUES (?,?,?) '
                    'ON CONFLICT(player_id) DO UPDATE SET bridge_profile_id=excluded.bridge_profile_id, '
                    'display_name=excluded.display_name, associations=associations+1',
                    (player_id, bridge_profile_id, display_name))

    def identity(self, player_id):
        row = self._db.execute('SELECT bridge_profile_id, display_name, associations FROM identities WHERE player_id=?',
                               (player_id,)).fetchone()
        return {'bridge_profile_id': row[0], 'display_name': row[1], 'associations': row[2]} if row else None

    def publish_manifest(self, realm, manifest_hash, packages):
        self._write('INSERT OR REPLACE INTO content_manifests VALUES (?,?,?)',
                    (realm, manifest_hash, canonical_json(sorted(packages))))

    def manifests(self):
        return {r: {'manifest_hash': h, 'packages': json.loads(p)}
                for r, h, p in self._db.execute('SELECT * FROM content_manifests ORDER BY realm')}

    def record_transition(self, player_id, transition):
        """The player's single in-flight realm switch (a newer one replaces it; the table stays bounded)."""
        self._write('INSERT OR REPLACE INTO transition_intents VALUES (?,?,?)',
                    (player_id, transition['transition_id'], canonical_json(transition)))

    def clear_transition(self, player_id, transition_id=None):
        if transition_id is None:
            self._write('DELETE FROM transition_intents WHERE player_id=?', (player_id,))
        else:
            self._write('DELETE FROM transition_intents WHERE player_id=? AND transition_id=?',
                        (player_id, transition_id))

    def pending_transition(self, player_id):
        row = self._db.execute('SELECT doc FROM transition_intents WHERE player_id=?', (player_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def pending_transitions(self):
        return [{'player_id': p, **json.loads(d)}
                for p, d in self._db.execute('SELECT player_id, doc FROM transition_intents ORDER BY player_id')]

    def reconcile_transitions(self, valid):
        """Startup: drop stored switches that ``valid(doc)`` rejects (unreadable or no longer well-formed)."""
        dropped = []
        for player_id, doc in self._db.execute('SELECT player_id, doc FROM transition_intents').fetchall():
            try:
                ok = valid(json.loads(doc))
            except ValueError:
                ok = False
            if not ok:
                dropped.append(player_id)
        for player_id in dropped:
            self.clear_transition(player_id)
        return dropped
