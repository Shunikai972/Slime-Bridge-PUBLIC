"""Durable counterpart-creation state machine (SQLite, realm-neutral).

One row per creation intent (``creation_id``), one row per issued native operation, one row per
consumed result digest, and an append-only event timeline. Every transition runs in a
``BEGIN IMMEDIATE`` transaction and is checked against ``TRANSITIONS``.

Phases::

    INTENT_PERSISTED -> DISPATCH_PENDING -> DISPATCHED -> NATIVE_OBSERVED -> READBACK_PENDING
        -> VERIFIED -> LINK_PENDING -> LINKED
    any dispatch phase -> UNCERTAIN -> RECONCILING -> (NATIVE_OBSERVED | UNCERTAIN | FAILED_ABSENT)
    refusal with no native effect -> FAILED;   identity/verification/link conflicts -> CONFLICT

Invariants enforced here:
- dispatch of the create operation happens at most once per creation (``dispatch_count``);
  anything after DISPATCH_PENDING is reconciled, never re-dispatched
- a result is accepted only for an operation this journal issued, from the session it was issued
  to, for the exact creation/operation/profile/realm/installation; replays are idempotent and a
  conflicting reply never downgrades or overrides an accepted outcome
- an ABSENT answer proves absence only from a *different process* than the dispatch (otherwise the
  original creation may still be in flight there, even across a host reconnect)
- one active intent per (profile, target realm); terminal phases are never left
"""

import json
import sqlite3
from pathlib import Path

from bridge.content.canonical import canonical_json, sha256_json
from bridge.core import diagnostics as D
from .legacy_quarantine import STATUS as QUARANTINE_STATUS, is_retired
from .model import endpoint_of, validate_request, validate_result

SCHEMA_VERSION = 1
PHASES = ('INTENT_PERSISTED', 'DISPATCH_PENDING', 'DISPATCHED', 'UNCERTAIN', 'RECONCILING', 'NATIVE_OBSERVED',
          'READBACK_PENDING', 'VERIFIED', 'LINK_PENDING', 'LINKED', 'FAILED', 'FAILED_ABSENT', 'CONFLICT')
TERMINAL = frozenset({'LINKED', 'FAILED', 'FAILED_ABSENT', 'CONFLICT'})
_RESULT_PHASES = ('DISPATCH_PENDING', 'DISPATCHED', 'UNCERTAIN', 'RECONCILING', 'NATIVE_OBSERVED', 'READBACK_PENDING')
TRANSITIONS = {
    ('INTENT_PERSISTED', 'begin_dispatch'): 'DISPATCH_PENDING',
    ('DISPATCH_PENDING', 'dispatched'): 'DISPATCHED',
    ('DISPATCH_PENDING', 'dispatch_uncertain'): 'UNCERTAIN',
    ('DISPATCHED', 'dispatch_uncertain'): 'UNCERTAIN',
    ('DISPATCH_PENDING', 'restart'): 'UNCERTAIN',
    ('DISPATCHED', 'restart'): 'UNCERTAIN',
    ('RECONCILING', 'restart'): 'UNCERTAIN',
    ('RECONCILING', 'reconcile_timeout'): 'UNCERTAIN',
    ('UNCERTAIN', 'begin_reconcile'): 'RECONCILING',
    ('RECONCILING', 'result_absent_same_session'): 'UNCERTAIN',
    ('RECONCILING', 'result_absent_new_session'): 'FAILED_ABSENT',
    ('NATIVE_OBSERVED', 'plan_verification'): 'READBACK_PENDING',
    ('READBACK_PENDING', 'verified'): 'VERIFIED',
    ('READBACK_PENDING', 'verification_failed'): 'CONFLICT',
    ('VERIFIED', 'begin_link'): 'LINK_PENDING',
    ('LINK_PENDING', 'linked'): 'LINKED',
    ('LINK_PENDING', 'link_conflict'): 'CONFLICT',
}
for _phase in ('DISPATCH_PENDING', 'DISPATCHED', 'UNCERTAIN', 'RECONCILING'):
    TRANSITIONS[(_phase, 'result_exists')] = 'NATIVE_OBSERVED'
    TRANSITIONS[(_phase, 'result_uncertain')] = 'UNCERTAIN'
for _phase in ('DISPATCH_PENDING', 'DISPATCHED', 'UNCERTAIN', 'RECONCILING'):
    TRANSITIONS[(_phase, 'result_refused')] = 'FAILED'
for _phase in set(PHASES) - TERMINAL:
    TRANSITIONS[(_phase, 'identity_conflict')] = 'CONFLICT'

ENDPOINT_VERDICTS = ('VERIFIED', 'REVERIFY_REQUIRED', 'FAILED')
ENDPOINT_CORE = ('realm', 'installation_id', 'native_save_id', 'native_save_identity')

_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS intents ('
    ' creation_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, profile_id TEXT NOT NULL,'
    ' target_realm TEXT NOT NULL, target_installation_id TEXT NOT NULL, request TEXT NOT NULL,'
    ' phase TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, dispatch_count INTEGER NOT NULL DEFAULT 0,'
    ' dispatch_session TEXT, observed TEXT, verification_plan TEXT, verification TEXT,'
    ' reconcile_rounds INTEGER NOT NULL DEFAULT 0)',
    'CREATE TABLE IF NOT EXISTS operations ('
    ' operation_id TEXT PRIMARY KEY, creation_id TEXT NOT NULL REFERENCES intents(creation_id),'
    ' kind TEXT NOT NULL, session TEXT NOT NULL, answer TEXT, answer_digest TEXT)',
    'CREATE TABLE IF NOT EXISTS consumed (digest TEXT PRIMARY KEY, creation_id TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS events ('
    ' seq INTEGER PRIMARY KEY AUTOINCREMENT, creation_id TEXT NOT NULL, event TEXT NOT NULL,'
    ' from_phase TEXT, to_phase TEXT, details TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS endpoint_meta ('
    ' profile_id TEXT NOT NULL, realm TEXT NOT NULL, origin TEXT NOT NULL, creation_id TEXT,'
    ' endpoint TEXT NOT NULL, build_fingerprint TEXT, verification_plan_id TEXT, verdict TEXT NOT NULL,'
    ' PRIMARY KEY (profile_id, realm))',
)


def _refuse_retired(creation_id):
    if is_retired(creation_id):
        raise JournalError('RETIRED_CREATION_ID', f'retired legacy creation id {creation_id} ({QUARANTINE_STATUS})')


def _refuse_retired_endpoint(db, profile_id, realm):
    # Existing metadata is not the sole provenance source; old durable intents survive adoption.
    for row in db.execute('SELECT creation_id FROM intents WHERE profile_id=? AND target_realm=? '
                          'UNION ALL SELECT creation_id FROM endpoint_meta WHERE profile_id=? AND realm=?',
                          (profile_id, realm, profile_id, realm)):
        _refuse_retired(row['creation_id'])


class JournalError(ValueError):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


class CounterpartJournal:
    def __init__(self, path, *, read_only=False):
        path = Path(path)
        if read_only:
            # Reuse the canonical snapshot readers without migrations or cross-thread handles.
            self._db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True,
                                       timeout=10, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        try:
            self._db.row_factory = sqlite3.Row
            self._db.execute('PRAGMA synchronous=FULL')
            self._migrate()
        except BaseException:
            self._db.close()
            raise

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _migrate(self):
        self._db.execute('BEGIN IMMEDIATE')
        try:
            for statement in _SCHEMA:
                self._db.execute(statement)
            row = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                self._db.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row[0]) > SCHEMA_VERSION:
                raise JournalError('SCHEMA_TOO_NEW', f'journal v{row[0]} > supported v{SCHEMA_VERSION}')
            self._db.execute('COMMIT')
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    # -- helpers ---------------------------------------------------------------------------

    def _tx(self):
        db = self._db

        class Tx:
            def __enter__(self_inner):
                db.execute('BEGIN IMMEDIATE')
                return db

            def __exit__(self_inner, exc_type, *_):
                db.execute('ROLLBACK' if exc_type else 'COMMIT')
        return Tx()

    def _row(self, db, creation_id):
        if not isinstance(creation_id, str):
            raise JournalError('NO_DURABLE_INTENT', 'creation id must be a string')
        row = db.execute('SELECT * FROM intents WHERE creation_id=?', (creation_id,)).fetchone()
        if row is None:
            raise JournalError('NO_DURABLE_INTENT', f'no intent for {creation_id}')
        return row

    def _move(self, db, row, event, **details):
        target = TRANSITIONS.get((row['phase'], event))
        if target is None:
            raise JournalError('ILLEGAL_TRANSITION', f"{row['phase']} --{event}--> ?")
        db.execute('UPDATE intents SET phase=?, generation=generation+1 WHERE creation_id=?', (target, row['creation_id']))
        db.execute('INSERT INTO events(creation_id, event, from_phase, to_phase, details) VALUES (?,?,?,?,?)',
                   (row['creation_id'], event, row['phase'], target, canonical_json(details)))
        return target

    def _note(self, db, creation_id, event, **details):
        db.execute('INSERT INTO events(creation_id, event, from_phase, to_phase, details) VALUES (?,?,?,?,?)',
                   (creation_id, event, None, None, canonical_json(details)))

    # -- intents -----------------------------------------------------------------------------

    def open_intent(self, request):
        """Persist a creation intent. Idempotent for the identical request; conflicting reuse is refused."""
        problems = validate_request(request)
        if problems:
            raise JournalError('INVALID_REQUEST', '; '.join(problems[:5]))
        _refuse_retired(request['creation_id'])
        with self._tx() as db:
            existing = db.execute('SELECT * FROM intents WHERE creation_id=? OR operation_id=?',
                                  (request['creation_id'], request['operation_id'])).fetchall()
            if existing:
                if len(existing) == 1 and json.loads(existing[0]['request']) == request:
                    return existing[0]['phase']
                raise JournalError('DUPLICATE_INTENT_CONFLICT', 'creation/operation id reused for another request')
            active = db.execute('SELECT creation_id, phase FROM intents WHERE profile_id=? AND target_realm=?',
                                (request['profile_id'], request['target_realm'])).fetchall()
            if any(r['phase'] not in TERMINAL or r['phase'] == 'LINKED' for r in active):
                raise JournalError('ACTIVE_INTENT_EXISTS', 'profile already has a counterpart intent for this realm')
            db.execute('INSERT INTO intents(creation_id, operation_id, profile_id, target_realm, target_installation_id,'
                       ' request, phase) VALUES (?,?,?,?,?,?,?)',
                       (request['creation_id'], request['operation_id'], request['profile_id'], request['target_realm'],
                        request['target_installation_id'], canonical_json(request), 'INTENT_PERSISTED'))
            self._note(db, request['creation_id'], 'intent_persisted', provenance_ref=request['provenance_ref'])
        return 'INTENT_PERSISTED'

    def get(self, creation_id):
        row = self._row(self._db, creation_id)
        out = dict(row)
        for key in ('request', 'observed', 'verification_plan', 'verification', 'dispatch_session'):
            out[key] = json.loads(row[key]) if row[key] else None
        return out

    def intents_for(self, profile_id):
        rows = self._db.execute('SELECT creation_id FROM intents WHERE profile_id=? ORDER BY creation_id',
                                (profile_id,)).fetchall()
        return [self.get(r['creation_id']) for r in rows]

    def pair_records(self, profile_id):
        """Read intents and endpoint metadata in one consistent, optionally read-only snapshot."""
        self._db.execute('BEGIN')
        try:
            records = self.intents_for(profile_id), self.endpoint_meta(profile_id)
            self._db.execute('COMMIT')
            return records
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    def timeline(self, creation_id):
        return [{'seq': r['seq'], 'event': r['event'], 'from': r['from_phase'], 'to': r['to_phase'],
                 'details': json.loads(r['details'])}
                for r in self._db.execute('SELECT * FROM events WHERE creation_id=? ORDER BY seq', (creation_id,))]

    # -- dispatch ----------------------------------------------------------------------------

    def begin_dispatch(self, creation_id, session):
        """Fence: True exactly once per creation. Any later call must reconcile instead."""
        with self._tx() as db:
            row = self._row(db, creation_id)
            if is_retired(creation_id):
                self._note(db, creation_id, 'dispatch_refused', phase=row['phase'], reason='RETIRED_CREATION_ID')
                return False
            if row['phase'] != 'INTENT_PERSISTED' or row['dispatch_count'] != 0:
                self._note(db, creation_id, 'dispatch_refused', phase=row['phase'])
                return False
            self._move(db, row, 'begin_dispatch', session=session)
            db.execute('UPDATE intents SET dispatch_count=1, dispatch_session=? WHERE creation_id=?',
                       (canonical_json(session), creation_id))
            db.execute('INSERT INTO operations(operation_id, creation_id, kind, session) VALUES (?,?,?,?)',
                       (row['operation_id'], creation_id, 'create', canonical_json(session)))
            return True

    def mark_dispatched(self, creation_id):
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] == 'DISPATCH_PENDING':
                self._move(db, row, 'dispatched')
            return self._row(db, creation_id)['phase']

    def mark_conflict(self, creation_id, reason):
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] not in TERMINAL:
                self._move(db, row, 'identity_conflict', reason=reason)
            return self._row(db, creation_id)['phase']

    def mark_uncertain(self, creation_id, reason):
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] in ('DISPATCH_PENDING', 'DISPATCHED'):
                self._move(db, row, 'dispatch_uncertain', reason=reason)
            elif row['phase'] == 'RECONCILING':
                self._move(db, row, 'reconcile_timeout', reason=reason)
            return self._row(db, creation_id)['phase']

    def begin_reconcile(self, creation_id, session):
        """Issue a reconcile operation id (never a create) bound to the given session."""
        _refuse_retired(creation_id)
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] != 'UNCERTAIN':
                raise JournalError('ILLEGAL_TRANSITION', f"reconcile from {row['phase']}")
            rounds = row['reconcile_rounds'] + 1
            op = f'{creation_id}:reconcile:{rounds}'
            db.execute('UPDATE intents SET reconcile_rounds=? WHERE creation_id=?', (rounds, creation_id))
            db.execute('INSERT INTO operations(operation_id, creation_id, kind, session) VALUES (?,?,?,?)',
                       (op, creation_id, 'reconcile', canonical_json(session)))
            self._move(db, row, 'begin_reconcile', operation_id=op, session=session)
            return op

    # -- results -----------------------------------------------------------------------------

    def accept_result(self, result, *, adapter, expected_source=None):
        """Bind a realm-neutral result to its exact outstanding operation. Returns (verdict, phase).

        verdict is ACCEPTED, DUPLICATE or raises JournalError with a rejection code.
        """
        problems = validate_result(result)
        if problems:
            raise JournalError('MALFORMED_RESULT', '; '.join(problems[:5]))
        _refuse_retired(result['creation_id'])
        digest = sha256_json({k: v for k, v in result.items() if k != 'diagnostics'})
        with self._tx() as db:
            row = db.execute('SELECT * FROM intents WHERE creation_id=?', (result['creation_id'],)).fetchone()
            if row is None:
                raise JournalError('NO_DURABLE_INTENT', 'result for an unknown creation')
            request = json.loads(row['request'])
            op = db.execute('SELECT * FROM operations WHERE operation_id=?', (result['operation_id'],)).fetchone()
            if op is None or op['creation_id'] != row['creation_id']:
                raise JournalError('STALE_OR_UNKNOWN_OPERATION', 'operation was not issued for this creation')
            checks = (('WRONG_PROFILE', result['profile_id'] == row['profile_id']),
                      ('WRONG_REALM', result['target_realm'] == row['target_realm'] == adapter.realm),
                      ('WRONG_INSTALLATION', result['installation_id'] == row['target_installation_id']),
                      ('WRONG_OPERATION_KIND', result['operation_kind'] == op['kind']),
                      ('WRONG_SESSION', canonical_json(result['session']) == op['session']),
                      ('SOURCE_ENDPOINT_MISMATCH', expected_source is None or expected_source == request['source']))
            for code, ok in checks:
                if not ok:
                    self._note(db, row['creation_id'], 'result_rejected', code=code)
                    raise JournalError(code, 'result does not bind to the outstanding intent')
            if result['native_save_id'] is not None and \
                    result['native_save_id'] != adapter.expected_identity(row['creation_id'])['native_save_id']:
                raise JournalError('PROVENANCE_MISMATCH', 'native save does not carry this creation provenance')
            if db.execute('SELECT 1 FROM consumed WHERE digest=?', (digest,)).fetchone():
                return 'DUPLICATE', row['phase']
            if op['answer'] is not None:
                previous = json.loads(op['answer'])
                if previous['outcome'] != 'UNCERTAIN':
                    self._note(db, row['creation_id'], 'conflicting_reply', operation_id=op['operation_id'],
                               previous=previous['outcome'], new=result['outcome'])
                    raise JournalError('CONFLICTING_REPLY', 'operation already answered definitively')
            observed = json.loads(row['observed']) if row['observed'] else None
            phase = self._apply_outcome(db, row, op, result, observed)
            db.execute('UPDATE operations SET answer=?, answer_digest=? WHERE operation_id=?',
                       (canonical_json(result), digest, op['operation_id']))
            db.execute('INSERT INTO consumed VALUES (?, ?)', (digest, row['creation_id']))
            self._note(db, row['creation_id'], 'result_accepted', operation_id=op['operation_id'],
                       outcome=result['outcome'])
            return 'ACCEPTED', phase

    def _apply_outcome(self, db, row, op, result, observed):
        """State effect of one bound result (raises JournalError for contradictions in terminal phases)."""
        phase, outcome = row['phase'], result['outcome']
        seen_save = observed is not None
        if outcome in ('CREATED', 'ALREADY_EXISTS'):
            identity = {**endpoint_of(result), 'native_filename': result['native_filename'],
                        'save_hash': result['save_hash'], 'slot': result['slot']}
            if seen_save:
                if any(observed[k] != identity[k] for k in ENDPOINT_CORE):
                    if phase in TERMINAL:
                        raise JournalError('CONFLICTING_IDENTITY', f'a different save appeared after {phase}')
                    return self._move(db, row, 'identity_conflict', observed=observed, new=identity)
                return phase  # same save reported again (e.g. reconcile after create)
            if phase in TERMINAL:
                raise JournalError('LATE_RESULT', f'a save appeared after {phase}; operator reconciliation required')
            db.execute('UPDATE intents SET observed=? WHERE creation_id=?', (canonical_json(identity), row['creation_id']))
            return self._move(db, row, 'result_exists', outcome=outcome)
        if outcome == 'UNCERTAIN' or result['native_effect'] == 'POSSIBLE':
            if phase in ('DISPATCH_PENDING', 'DISPATCHED', 'RECONCILING'):
                return self._move(db, row, 'result_uncertain', outcome=outcome)
            return phase  # already uncertain, observed or terminal: an uncertain reply changes nothing
        if phase in TERMINAL:
            raise JournalError('LATE_RESULT', f'{outcome} after {phase}')
        if seen_save:  # absence/refusal contradicts an observed save
            return self._move(db, row, 'identity_conflict', reason=f'{outcome} after an observed save')
        if outcome == 'ABSENT':
            # Same *process* as the dispatch: the original create may still complete there.
            dispatched = json.loads(row['dispatch_session']) if row['dispatch_session'] else None
            same = dispatched is not None and dispatched.get('pid') == json.loads(op['session']).get('pid')
            event = 'result_absent_same_session' if same else 'result_absent_new_session'
            if (phase, event) not in TRANSITIONS:
                return phase  # a late answer from an older round; the current round decides
            return self._move(db, row, event, operation_id=op['operation_id'])
        if op['kind'] == 'create':  # NO_CAPACITY / SLOT_CONFLICT / FAILED with no native effect
            earlier = db.execute("SELECT answer FROM operations WHERE creation_id=? AND answer IS NOT NULL",
                                 (row['creation_id'],)).fetchall()
            if any(json.loads(a['answer'])['native_effect'] != 'NONE' for a in earlier):
                # An earlier answer allowed a native effect: a later "nothing happened" cannot prove absence.
                # Only reconciliation from a different process (ABSENT) may close this creation.
                self._note(db, row['creation_id'], 'refusal_after_possible_effect',
                           operation_id=op['operation_id'], outcome=outcome)
                return phase
            return self._move(db, row, 'result_refused', outcome=outcome)
        return phase

    # -- verification and link -------------------------------------------------------------------

    def attach_verification_plan(self, creation_id, plan):
        _refuse_retired(creation_id)
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] == 'NATIVE_OBSERVED':
                self._move(db, row, 'plan_verification', plan_id=plan['plan_id'])
                db.execute('UPDATE intents SET verification_plan=? WHERE creation_id=?',
                           (canonical_json(plan), creation_id))
            elif row['phase'] != 'READBACK_PENDING':
                raise JournalError('ILLEGAL_TRANSITION', f"verification plan in {row['phase']}")
            return self._row(db, creation_id)['phase']

    def record_verification(self, creation_id, aggregate):
        _refuse_retired(creation_id)
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] != 'READBACK_PENDING':
                raise JournalError('ILLEGAL_TRANSITION', f"verification in {row['phase']}")
            plan = json.loads(row['verification_plan'])
            if aggregate.get('plan_id') != plan['plan_id']:
                raise JournalError('WRONG_PLAN', 'verification result for another plan')
            db.execute('UPDATE intents SET verification=? WHERE creation_id=?', (canonical_json(aggregate), creation_id))
            if aggregate['verdict'] == 'VERIFIED':
                return self._move(db, row, 'verified')
            if aggregate['verdict'] == 'FAILED':
                return self._move(db, row, 'verification_failed')
            return row['phase']

    def begin_link(self, creation_id):
        _refuse_retired(creation_id)
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] == 'VERIFIED':
                return self._move(db, row, 'begin_link')
            if row['phase'] != 'LINK_PENDING':
                raise JournalError('NOT_VERIFIED', f"link requires VERIFIED, not {row['phase']}")
            return row['phase']

    def finish_link(self, creation_id, *, linked, build_fingerprint=None, reason=None):
        _refuse_retired(creation_id)
        with self._tx() as db:
            row = self._row(db, creation_id)
            if row['phase'] != 'LINK_PENDING':
                raise JournalError('ILLEGAL_TRANSITION', f"finish_link in {row['phase']}")
            if not linked:
                return self._move(db, row, 'link_conflict', reason=reason)
            observed = json.loads(row['observed'])
            core = {k: observed[k] for k in ENDPOINT_CORE}
            plan = json.loads(row['verification_plan']) if row['verification_plan'] else {}
            db.execute('INSERT OR REPLACE INTO endpoint_meta VALUES (?,?,?,?,?,?,?,?)',
                       (row['profile_id'], row['target_realm'], 'COUNTERPART', creation_id, canonical_json(core),
                        build_fingerprint, plan.get('plan_id'), 'VERIFIED'))
            return self._move(db, row, 'linked')

    def endpoint_meta(self, profile_id):
        return {r['realm']: {'origin': r['origin'], 'creation_id': r['creation_id'], 'endpoint': json.loads(r['endpoint']),
                             'build_fingerprint': r['build_fingerprint'], 'verification_plan_id': r['verification_plan_id'],
                             'verdict': r['verdict']}
                for r in self._db.execute('SELECT * FROM endpoint_meta WHERE profile_id=?', (profile_id,))}

    def observe_live_endpoint(self, profile_id, endpoint, build_fingerprint):
        """Record that a live session identified a save under a build; returns the resulting verdict.

        A live identification is not independent evidence: it records a never-seen source save as
        VERIFIED, but never upgrades an existing verdict and never moves a recorded build. Seeing a
        VERIFIED endpoint under a different build downgrades it to REVERIFY_REQUIRED (only
        ``record_endpoint_verification`` with fresh evidence moves the build). Identity must not change.
        """
        core = {k: endpoint[k] for k in ENDPOINT_CORE}
        with self._tx() as db:
            _refuse_retired_endpoint(db, profile_id, core['realm'])
            row = db.execute('SELECT * FROM endpoint_meta WHERE profile_id=? AND realm=?',
                             (profile_id, core['realm'])).fetchone()
            if row is None:
                db.execute('INSERT INTO endpoint_meta VALUES (?,?,?,?,?,?,?,?)',
                           (profile_id, core['realm'], 'SOURCE', None, canonical_json(core), build_fingerprint, None,
                            'VERIFIED'))
                return 'VERIFIED'
            if json.loads(row['endpoint']) != core:
                raise JournalError('ENDPOINT_IDENTITY_CHANGED', 'a different save is recorded for this realm')
            if row['verdict'] == 'VERIFIED' and row['build_fingerprint'] != build_fingerprint:
                db.execute("UPDATE endpoint_meta SET verdict='REVERIFY_REQUIRED' WHERE profile_id=? AND realm=?",
                           (profile_id, core['realm']))
                self._note(db, row['creation_id'] or f"{profile_id}:{core['realm']}", 'endpoint_build_changed',
                           realm=core['realm'], recorded=row['build_fingerprint'], observed=build_fingerprint)
                return 'REVERIFY_REQUIRED'
            return row['verdict']

    def record_endpoint_verification(self, profile_id, realm, *, verdict, build_fingerprint=None, plan_id=None,
                                     endpoint=None):
        """Store a re-verification verdict; ``endpoint`` adopts a link made before this journal existed."""
        if verdict not in ENDPOINT_VERDICTS:
            raise JournalError('INVALID_VERDICT', verdict)
        with self._tx() as db:
            _refuse_retired_endpoint(db, profile_id, realm)
            row = db.execute('SELECT * FROM endpoint_meta WHERE profile_id=? AND realm=?', (profile_id, realm)).fetchone()
            if row is not None:
                _refuse_retired(row['creation_id'])
            if row is None and endpoint is not None:
                db.execute('INSERT INTO endpoint_meta VALUES (?,?,?,?,?,?,?,?)',
                           (profile_id, realm, 'LEGACY', None, canonical_json({k: endpoint[k] for k in ENDPOINT_CORE}),
                            None, None, 'REVERIFY_REQUIRED'))
                row = db.execute('SELECT * FROM endpoint_meta WHERE profile_id=? AND realm=?',
                                 (profile_id, realm)).fetchone()
            if row is None:
                raise JournalError('NO_ENDPOINT_RECORD', f'no endpoint record for {realm}')
            if endpoint is not None and json.loads(row['endpoint']) != {k: endpoint[k] for k in ENDPOINT_CORE}:
                raise JournalError('ENDPOINT_IDENTITY_CHANGED', 'verification is for a different save')
            # A failed or incomplete re-verification never moves the recorded build forward.
            build = build_fingerprint if verdict == 'VERIFIED' else row['build_fingerprint']
            db.execute('UPDATE endpoint_meta SET verdict=?, build_fingerprint=?, verification_plan_id=? '
                       'WHERE profile_id=? AND realm=?',
                       (verdict, build, plan_id or row['verification_plan_id'], profile_id, realm))
            self._note(db, row['creation_id'] or f'{profile_id}:{realm}', 'endpoint_verification', realm=realm,
                       verdict=verdict, build_fingerprint=build_fingerprint)
            return verdict

    def last_outcomes(self, profile_id):
        """creation id -> outcome of its most recent definitive (non-UNCERTAIN) answer."""
        out = {}
        rows = self._db.execute('SELECT o.creation_id, o.answer FROM operations o JOIN intents i '
                                'ON i.creation_id=o.creation_id WHERE i.profile_id=? AND o.answer IS NOT NULL '
                                'ORDER BY o.rowid', (profile_id,)).fetchall()
        for row in rows:
            outcome = json.loads(row['answer'])['outcome']
            if outcome != 'UNCERTAIN':
                out[row['creation_id']] = outcome
        return out

    # -- restart --------------------------------------------------------------------------------

    def recover(self):
        """After a host restart: in-flight dispatch/reconcile is uncertain (the host lost its queue)."""
        moved = []
        with self._tx() as db:
            for row in db.execute("SELECT * FROM intents WHERE phase IN ('DISPATCH_PENDING','DISPATCHED','RECONCILING')"
                                  " ORDER BY creation_id").fetchall():
                if is_retired(row['creation_id']):
                    continue  # preserve the unresolved historical row; never promote or redispatch it
                self._move(db, row, 'restart')
                moved.append(row['creation_id'])
        pending_links = [r['creation_id'] for r in self._db.execute(
            "SELECT creation_id FROM intents WHERE phase='LINK_PENDING' ORDER BY creation_id")
                         if not is_retired(r['creation_id'])]
        return {'uncertain': moved, 'link_pending': pending_links,
                'diagnostics': [D.make('COUNTERPART_RECOVERED_UNCERTAIN', 'warning', 'counterpart',
                                       'dispatch outcome unknown after restart; reconcile before anything else',
                                       subject=c) for c in moved]}
