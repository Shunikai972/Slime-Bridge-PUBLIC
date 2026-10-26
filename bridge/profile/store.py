"""Bridge-owned native save linkage; never modify a proprietary save file here.

A durable CREATING reservation is written before invoking any native CREATE_SAVE command.
On restart, the same creation ID must be reconciled with LIST_SAVES before retrying;
this store alone does not prove that an external game command is exactly-once.
"""

import sqlite3
import uuid
from pathlib import Path

from bridge.counterpart.legacy_quarantine import is_retired as creation_id_is_retired, require_not_retired


class ProfileStore:
    def __init__(self, path: Path, *, read_only=False):
        path = Path(path)
        if read_only:
            # No directory creation or schema migration on an authority read.
            self._db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
            self._db.row_factory = sqlite3.Row
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10)
        try:
            self._initialize()
        except BaseException:
            # sqlite3's connection context manager does not close the handle.
            # An initialization error must not leave the DB locked on Windows.
            self._db.close()
            raise

    def _initialize(self) -> None:
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys=ON')
        self._db.execute('PRAGMA synchronous=FULL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS profiles (
                bridge_profile_id TEXT PRIMARY KEY,
                state TEXT NOT NULL CHECK(state IN ('UNLINKED','CREATING','LINKED')),
                creation_id TEXT UNIQUE,
                creation_issued INTEGER NOT NULL DEFAULT 0,
                creation_target_realm TEXT CHECK(creation_target_realm IN ('SR1','SR2')),
                last_realm TEXT CHECK(last_realm IN ('SR1','SR2')),
                sr1_last_safe_position TEXT,
                sr2_last_safe_position TEXT,
                sr1_portal_unlocked INTEGER NOT NULL DEFAULT 0,
                sr2_portal_unlocked INTEGER NOT NULL DEFAULT 0,
                currency_mode TEXT NOT NULL DEFAULT 'separate',
                transition_mode TEXT NOT NULL DEFAULT 'WARM',
                content_manifest_version INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS endpoints (
                bridge_profile_id TEXT NOT NULL REFERENCES profiles(bridge_profile_id),
                realm TEXT NOT NULL CHECK(realm IN ('SR1','SR2')),
                installation_id TEXT NOT NULL,
                native_save_id TEXT NOT NULL,
                native_save_identity TEXT NOT NULL,
                PRIMARY KEY (bridge_profile_id, realm),
                UNIQUE (realm, installation_id, native_save_id)
            );
        ''')
        # Acquire the writer lock before reading the old schema. Otherwise two
        # processes can both observe a missing column and race on ALTER TABLE.
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in self._db.execute('PRAGMA table_info(profiles)')}
            if 'creation_issued' not in columns:
                self._db.execute('ALTER TABLE profiles ADD COLUMN creation_issued INTEGER NOT NULL DEFAULT 0')
            if 'creation_target_realm' not in columns:
                self._db.execute("ALTER TABLE profiles ADD COLUMN creation_target_realm TEXT "
                                 "CHECK(creation_target_realm IN ('SR1','SR2'))")

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self._db.close()

    @staticmethod
    def _identity(realm, installation_id, save_id, identity):
        if realm not in ('SR1', 'SR2') or any(
            not isinstance(v, str) or not v.strip() or len(v) > 128
            for v in (installation_id, save_id, identity)
        ):
            raise ValueError('Invalid native save identity')

    def get(self, profile_id: str) -> dict:
        # A single read transaction prevents a concurrent confirm from producing a
        # CREATING profile with two endpoints in the same returned snapshot.
        with self._db:
            self._db.execute('BEGIN')
            row = self._db.execute('SELECT * FROM profiles WHERE bridge_profile_id=?', (profile_id,)).fetchone()
            if row is None:
                raise KeyError(profile_id)
            profile = dict(row)
            for realm in ('SR1', 'SR2'):
                endpoint = self._db.execute('SELECT installation_id, native_save_id, native_save_identity '
                                            'FROM endpoints WHERE bridge_profile_id=? AND realm=?',
                                            (profile_id, realm)).fetchone()
                profile[realm.lower()] = dict(endpoint) if endpoint else None
        return profile

    def find_or_create(self, realm: str, installation_id: str, save_id: str, identity: str) -> dict:
        self._identity(realm, installation_id, save_id, identity)
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT bridge_profile_id, native_save_identity FROM endpoints '
                                   'WHERE realm=? AND installation_id=? AND native_save_id=?',
                                   (realm, installation_id, save_id)).fetchone()
            if row:
                if row[1] != identity:
                    raise ValueError('Native save identity changed; refusing automatic relink')
                profile_id = row[0]
            else:
                profile_id = uuid.uuid4().hex
                self._db.execute('INSERT INTO profiles(bridge_profile_id, state, last_realm) VALUES (?, ?, ?)',
                                 (profile_id, 'UNLINKED', realm))
                self._db.execute('INSERT INTO endpoints VALUES (?, ?, ?, ?, ?)',
                                 (profile_id, realm, installation_id, save_id, identity))
        return self.get(profile_id)

    def reserve_counterpart(self, profile_id: str, realm: str) -> dict:
        if realm not in ('SR1', 'SR2'):
            raise ValueError('Invalid counterpart realm')
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT state, creation_id, creation_target_realm FROM profiles WHERE bridge_profile_id=?',
                                   (profile_id,)).fetchone()
            if row is None:
                raise KeyError(profile_id)
            source = self._db.execute('SELECT realm FROM endpoints WHERE bridge_profile_id=?',
                                      (profile_id,)).fetchall()
            if len(source) != 1 or source[0][0] == realm or row['state'] == 'LINKED':
                raise ValueError('Counterpart already linked or realm matches the source')
            if row['creation_target_realm'] is not None and row['creation_target_realm'] != realm:
                raise ValueError('Counterpart creation already reserved for another realm')
            creation_id = row['creation_id'] or uuid.uuid4().hex
            while not row['creation_id'] and creation_id_is_retired(creation_id):
                creation_id = uuid.uuid4().hex
            require_not_retired(creation_id)  # a stale reservation can never resurrect a retired id
            if row['state'] == 'UNLINKED' or row['creation_target_realm'] is None:
                self._db.execute('UPDATE profiles SET state=?, creation_id=?, creation_target_realm=? '
                                 'WHERE bridge_profile_id=?', ('CREATING', creation_id, realm, profile_id))
        return {'state': 'CREATING', 'creation_id': creation_id}

    def claim_creation(self, profile_id: str, creation_id: str) -> bool:
        """Durably mark one native CREATE attempt; uncertain replies require reconciliation.

        True means the caller may issue the command once. A later False means
        inspect native saves for this creation ID before any retry, not that the
        native save necessarily exists. A crash between this write and the game
        call is deliberately left indeterminate rather than risking duplicates.
        """
        if not isinstance(creation_id, str) or not creation_id:
            raise ValueError('Creation ID required')
        require_not_retired(creation_id)
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT state, creation_id, creation_issued FROM profiles '
                                   'WHERE bridge_profile_id=?', (profile_id,)).fetchone()
            if row is None:
                raise KeyError(profile_id)
            if row['state'] != 'CREATING' or row['creation_id'] != creation_id:
                raise ValueError('No matching pending creation')
            if row['creation_issued']:
                return False
            self._db.execute('UPDATE profiles SET creation_issued=1 WHERE bridge_profile_id=?', (profile_id,))
            return True

    def confirm_counterpart(self, profile_id: str, creation_id: str, realm_install: str,
                            save_id: str, identity: str) -> dict:
        require_not_retired(creation_id)
        if not isinstance(creation_id, str) or not creation_id:
            raise ValueError('Creation ID required')
        with self._db:
            self._db.execute('BEGIN IMMEDIATE')
            row = self._db.execute('SELECT state, creation_id, creation_issued, creation_target_realm FROM profiles '
                                   'WHERE bridge_profile_id=?', (profile_id,)).fetchone()
            if row is None:
                raise KeyError(profile_id)
            if row['creation_id'] != creation_id or row['state'] not in ('CREATING', 'LINKED'):
                raise ValueError('No matching pending creation')
            if row['creation_issued'] != 1:
                raise ValueError('Cannot confirm an unissued creation; dispatch not recorded')
            realm = row['creation_target_realm']
            if realm is None:
                # Older pending intents had no recorded target. Reconstruct only
                # while a single source endpoint makes it unambiguous; never guess
                # the target of a previously linked legacy profile.
                if row['state'] != 'CREATING':
                    raise ValueError('Linked creation target is unknown; manual reconciliation required')
                sources = self._db.execute('SELECT realm FROM endpoints WHERE bridge_profile_id=?',
                                           (profile_id,)).fetchall()
                if len(sources) != 1:
                    raise ValueError('Ambiguous counterpart creation')
                realm = 'SR2' if sources[0][0] == 'SR1' else 'SR1'
                self._db.execute('UPDATE profiles SET creation_target_realm=? WHERE bridge_profile_id=?',
                                 (realm, profile_id))
            self._identity(realm, realm_install, save_id, identity)
            existing = self._db.execute('SELECT installation_id, native_save_id, native_save_identity '
                                        'FROM endpoints WHERE bridge_profile_id=? AND realm=?',
                                        (profile_id, realm)).fetchone()
            target = (realm_install, save_id, identity)
            if existing is not None:
                if tuple(existing) != target or row[0] != 'LINKED':
                    raise ValueError('Counterpart differs from committed identity')
            else:
                # A destination may heartbeat and be discovered between CREATE_SAVE and this
                # acknowledgement. Reconcile only an untouched, single-endpoint placeholder;
                # never merge a profile that has progressed or contains user settings.
                owner = self._db.execute('SELECT p.* FROM endpoints e JOIN profiles p '
                                         'ON p.bridge_profile_id=e.bridge_profile_id '
                                         'WHERE e.realm=? AND e.installation_id=? AND e.native_save_id=?',
                                         (realm, realm_install, save_id)).fetchone()
                if owner is not None:
                    # A matching save ID and fingerprint do not prove the save belongs
                    # to this creation intent. Never silently absorb another profile.
                    raise ValueError('Native save already linked elsewhere; creation provenance required')
                try:
                    self._db.execute('INSERT INTO endpoints VALUES (?, ?, ?, ?, ?)',
                                     (profile_id, realm, *target))
                except sqlite3.IntegrityError as ex:
                    raise ValueError('Native save already linked elsewhere') from ex
                self._db.execute('UPDATE profiles SET state=? WHERE bridge_profile_id=?',
                                 ('LINKED', profile_id))
        return self.get(profile_id)
