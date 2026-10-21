"""Durable warm-transition state machine (SQLite, ``BEGIN IMMEDIATE``, explicit transition table).

Stages (in order)::

    IDLE -> PREPARING -> SOURCE_LOCKED -> DESTINATION_STARTING -> DESTINATION_LOADING
         -> DESTINATION_WORLD_READY -> TRANSITION_READY -> VISUAL_TRANSITION_STARTED -> SOURCE_HIDDEN
         -> DESTINATION_VISIBLE -> INPUT_SWITCHING -> PLAYER_CONTROL_READY
         -> BACKGROUND_REALM_THROTTLED -> COMMITTED
    any stage before COMMITTED --rollback--> ROLLING_BACK --(all compensations done)--> ROLLED_BACK
    a compensation that cannot be done -> FAILED (operator/recovery center)

Facts recorded alongside the stage (source locked/synced, destination session, confirmed save,
validated WORLD_READY, source hidden, destination visible, input owner, audio faded) decide the
compensations a rollback must perform, in a fixed safe order (input back to the source, source
shown before the destination is hidden, audio restored, source unlocked, destination parked or
stopped). Events are bound: destination events must name the destination session, WORLD_READY
must validate against the confirmed save and session, duplicates are idempotent and anything for
another transition, an older generation or the wrong session is rejected.

Restart recovery: stages before PLAYER_CONTROL_READY roll back (the player is returned to the
source); PLAYER_CONTROL_READY and BACKGROUND_REALM_THROTTLED roll forward to COMMITTED because
the destination already owns the player.
"""

import json
import sqlite3
import uuid
from pathlib import Path

from bridge.content.canonical import canonical_json
from bridge.counterpart.world_ready import evaluate_world_ready

STAGES = ('IDLE', 'PREPARING', 'SOURCE_LOCKED', 'DESTINATION_STARTING', 'DESTINATION_LOADING',
          'DESTINATION_WORLD_READY', 'TRANSITION_READY', 'VISUAL_TRANSITION_STARTED', 'SOURCE_HIDDEN',
          'DESTINATION_VISIBLE', 'INPUT_SWITCHING', 'PLAYER_CONTROL_READY', 'BACKGROUND_REALM_THROTTLED',
          'COMMITTED', 'ROLLING_BACK', 'ROLLED_BACK', 'FAILED')
TERMINAL = frozenset({'COMMITTED', 'ROLLED_BACK', 'FAILED'})
FORWARD = STAGES[:STAGES.index('COMMITTED') + 1]
ROLL_FORWARD_FROM = ('PLAYER_CONTROL_READY', 'BACKGROUND_REALM_THROTTLED')
EVENTS = ('source_locked', 'source_synced', 'destination_started', 'destination_heartbeat', 'correct_save_confirmed',
          'world_ready', 'overlay_ready', 'audio_fade_requested', 'visual_started', 'source_hidden',
          'destination_visible', 'input_switch_requested', 'input_transferred', 'player_ready',
          'background_throttled', 'commit', 'rollback', 'compensation_done', 'compensation_failed',
          'destination_crashed', 'source_crashed')
COMPENSATIONS = ('RETURN_INPUT_TO_SOURCE', 'SHOW_SOURCE', 'HIDE_DESTINATION', 'RESTORE_SOURCE_AUDIO', 'UNLOCK_SOURCE',
                 'PARK_DESTINATION', 'STOP_DESTINATION')

# (stage, event) -> next stage, for the forward path. Guards in ``_guard`` add the fact checks.
FORWARD_TRANSITIONS = {
    ('PREPARING', 'source_locked'): 'SOURCE_LOCKED',
    ('SOURCE_LOCKED', 'destination_started'): 'DESTINATION_STARTING',
    ('DESTINATION_STARTING', 'correct_save_confirmed'): 'DESTINATION_LOADING',
    ('DESTINATION_LOADING', 'world_ready'): 'DESTINATION_WORLD_READY',
    ('DESTINATION_WORLD_READY', 'overlay_ready'): 'TRANSITION_READY',
    ('TRANSITION_READY', 'visual_started'): 'VISUAL_TRANSITION_STARTED',
    ('VISUAL_TRANSITION_STARTED', 'source_hidden'): 'SOURCE_HIDDEN',
    ('SOURCE_HIDDEN', 'destination_visible'): 'DESTINATION_VISIBLE',
    ('DESTINATION_VISIBLE', 'input_switch_requested'): 'INPUT_SWITCHING',
    ('INPUT_SWITCHING', 'player_ready'): 'PLAYER_CONTROL_READY',
    ('PLAYER_CONTROL_READY', 'background_throttled'): 'BACKGROUND_REALM_THROTTLED',
    ('BACKGROUND_REALM_THROTTLED', 'commit'): 'COMMITTED',
}
# Facts an event records whatever stage it arrives in (if the stage allows it at all).
FACT_EVENTS = {'source_synced': ('source_synced', True), 'audio_fade_requested': ('audio_faded', True),
               'input_transferred': ('input_owner', 'DESTINATION'), 'destination_heartbeat': None}

_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS transitions (transition_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL,'
    ' intent TEXT NOT NULL, stage TEXT NOT NULL, furthest TEXT NOT NULL, facts TEXT NOT NULL,'
    ' compensations TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0, outcome TEXT)',
    'CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, transition_id TEXT NOT NULL,'
    ' event TEXT NOT NULL, from_stage TEXT, to_stage TEXT, verdict TEXT NOT NULL, details TEXT NOT NULL)',
)


class TransitionError(ValueError):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


def make_intent(*, profile_id, source, destination, expected_build=None, portal_id=None, mode='WARM',
                transfer_ids=(), charge=None):
    """``source``/``destination``: {'realm', 'session', 'endpoint'} (destination session may be None).

    ``expected_build`` pins the destination build; an unbound intent cannot accept WORLD_READY.
    """
    if source['realm'] == destination['realm'] or mode not in ('WARM', 'COLD'):
        raise TransitionError('INVALID_INTENT', 'distinct realms and WARM/COLD mode required')
    for side in (source, destination):
        endpoint = side.get('endpoint') or {}
        if endpoint.get('realm') != side['realm'] or not endpoint.get('native_save_id'):
            raise TransitionError('INVALID_INTENT', 'each side needs its linked endpoint for that realm')
    if source.get('session') is None:
        raise TransitionError('INVALID_INTENT', 'the source must be a live session')
    return {'schema': 'bridge-transition-intent', 'schema_version': 1, 'profile_id': profile_id,
            'portal_id': portal_id, 'mode': mode, 'source': dict(source), 'destination': dict(destination),
            'expected_build': expected_build, 'transfer_ids': sorted(transfer_ids), 'charge': charge}


class TransitionMachine:
    def __init__(self, path, *, trust, allow_synthetic=False):
        if not allow_synthetic and any(str(provider).startswith('sim.') for provider in trust):
            raise TransitionError('SYNTHETIC_TRUST', 'simulator providers are only trusted inside a simulation')
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA synchronous=FULL')
        self.trust = dict(trust)
        with self._tx() as db:
            for statement in _SCHEMA:
                db.execute(statement)
            if db.execute("SELECT 1 FROM meta WHERE key='schema_version'").fetchone() is None:
                db.execute("INSERT INTO meta VALUES ('schema_version', '1')")

    def close(self):
        self._db.close()

    def _tx(self):
        db = self._db

        class Tx:
            def __enter__(self_inner):
                db.execute('BEGIN IMMEDIATE')
                return db

            def __exit__(self_inner, exc_type, *_):
                db.execute('ROLLBACK' if exc_type else 'COMMIT')
        return Tx()

    # -- reads -----------------------------------------------------------------------------------

    def get(self, transition_id):
        row = self._db.execute('SELECT * FROM transitions WHERE transition_id=?', (transition_id,)).fetchone()
        if row is None:
            raise TransitionError('UNKNOWN_TRANSITION', transition_id)
        out = dict(row)
        for key in ('intent', 'facts', 'compensations', 'outcome'):
            out[key] = json.loads(row[key]) if row[key] else None
        return out

    def active(self, profile_id):
        row = self._db.execute("SELECT transition_id FROM transitions WHERE profile_id=? AND stage NOT IN "
                               "('COMMITTED','ROLLED_BACK','FAILED')", (profile_id,)).fetchone()
        return self.get(row['transition_id']) if row else None

    def events(self, transition_id):
        return [{'seq': r['seq'], 'event': r['event'], 'from': r['from_stage'], 'to': r['to_stage'],
                 'verdict': r['verdict'], 'details': json.loads(r['details'])}
                for r in self._db.execute('SELECT * FROM events WHERE transition_id=? ORDER BY seq', (transition_id,))]

    # -- writes ----------------------------------------------------------------------------------

    def begin(self, intent):
        with self._tx() as db:
            if db.execute("SELECT 1 FROM transitions WHERE profile_id=? AND stage NOT IN "
                          "('COMMITTED','ROLLED_BACK','FAILED')", (intent['profile_id'],)).fetchone():
                raise TransitionError('TRANSITION_ACTIVE', 'one transition per profile at a time')
            transition_id = uuid.uuid4().hex
            facts = {'source_locked': False, 'source_synced': False, 'destination_session':
                     intent['destination'].get('session'), 'save_confirmed': False, 'world_ready': False,
                     'overlay_ready': False, 'source_hidden': False, 'destination_visible': False,
                     'input_owner': 'SOURCE', 'audio_faded': False, 'destination_started': False}
            db.execute('INSERT INTO transitions VALUES (?,?,?,?,?,?,?,?,?)',
                       (transition_id, intent['profile_id'], canonical_json(intent), 'PREPARING', 'PREPARING',
                        canonical_json(facts), '[]', 1, None))
            self._log(db, transition_id, 'begin', 'IDLE', 'PREPARING', 'ACCEPTED', {})
        return transition_id

    def _log(self, db, transition_id, event, src, dst, verdict, details):
        db.execute('INSERT INTO events(transition_id, event, from_stage, to_stage, verdict, details) VALUES (?,?,?,?,?,?)',
                   (transition_id, event, src, dst, verdict, canonical_json(details)))

    def apply(self, transition_id, event, *, generation=None, session=None, payload=None):
        """Apply one event. Returns {'verdict': ACCEPTED|DUPLICATE|REJECTED, 'stage', 'code'?}; never raises
        for a bad event (rejections are logged), only for an unknown transition."""
        payload = {} if payload is None else payload
        with self._tx() as db:
            row = db.execute('SELECT * FROM transitions WHERE transition_id=?', (transition_id,)).fetchone()
            if row is None:
                raise TransitionError('UNKNOWN_TRANSITION', transition_id)
            stage, facts = row['stage'], json.loads(row['facts'])
            intent = json.loads(row['intent'])
            comps = json.loads(row['compensations'])

            def reject(code):
                self._log(db, transition_id, event, stage, None, 'REJECTED', {'code': code})
                return {'verdict': 'REJECTED', 'stage': stage, 'code': code}

            if not isinstance(payload, dict):
                return reject('MALFORMED_PAYLOAD')
            if event not in EVENTS:
                return reject('UNKNOWN_EVENT')
            if generation is not None and generation != row['generation']:
                return reject('STALE_GENERATION')
            if stage in TERMINAL:
                return reject('TRANSITION_FINISHED') if event != 'commit' or stage != 'COMMITTED' else \
                    {'verdict': 'DUPLICATE', 'stage': stage}
            code = self._bind(event, stage, facts, intent, session, payload)
            if code == 'DUPLICATE':
                self._log(db, transition_id, event, stage, stage, 'DUPLICATE', {})
                return {'verdict': 'DUPLICATE', 'stage': stage}
            if code:
                return reject(code)
            new_stage, new_comps = self._next(event, stage, facts, intent, comps, payload, session)
            if new_stage is None:
                return reject('ILLEGAL_EVENT_FOR_STAGE')
            furthest = row['furthest']
            if new_stage in FORWARD and FORWARD.index(new_stage) > FORWARD.index(furthest):
                furthest = new_stage
            db.execute('UPDATE transitions SET stage=?, furthest=?, facts=?, compensations=?, generation=generation+1,'
                       ' outcome=? WHERE transition_id=?',
                       (new_stage, furthest, canonical_json(facts), canonical_json(new_comps),
                        canonical_json({'reason': payload.get('reason')}) if new_stage in TERMINAL else None,
                        transition_id))
            self._log(db, transition_id, event, stage, new_stage, 'ACCEPTED',
                      {k: payload[k] for k in ('reason', 'step') if k in payload})
            return {'verdict': 'ACCEPTED', 'stage': new_stage}

    def _bind(self, event, stage, facts, intent, session, payload):
        """Rejection code, 'DUPLICATE', or None. Mutates nothing."""
        destination = intent['destination']
        if event in ('destination_started', 'destination_heartbeat', 'correct_save_confirmed', 'world_ready',
                     'destination_crashed', 'destination_visible', 'input_transferred', 'player_ready',
                     'background_throttled'):
            expected = facts['destination_session']
            if session is None:
                return 'SESSION_REQUIRED'
            if event == 'destination_started':
                if expected is not None and expected != session:
                    return 'WRONG_SESSION'
            elif expected is None or session != expected:
                return 'WRONG_SESSION'
        if event in ('source_locked', 'source_synced', 'source_hidden', 'source_crashed') and \
                session is not None and session != intent['source']['session']:
            return 'WRONG_SESSION'
        if event == 'correct_save_confirmed':
            if any(payload.get(k) != destination['endpoint'].get(k)
                   for k in ('realm', 'installation_id', 'native_save_id', 'native_save_identity')):
                return 'WRONG_SAVE'
            if facts['save_confirmed']:
                return 'DUPLICATE'
        if event == 'world_ready':
            if facts['world_ready']:
                return 'DUPLICATE'
            if not facts['save_confirmed']:
                return 'SAVE_NOT_CONFIRMED'
            expected = {**{k: destination['endpoint'][k] for k in ('realm', 'installation_id', 'native_save_id',
                                                                  'native_save_identity')},
                        'build_fingerprint': intent.get('expected_build'), 'session': facts['destination_session']}
            ready, diags = evaluate_world_ready(payload.get('report'), expected=expected, trust=self.trust)
            if not ready:
                return 'WORLD_READY_REJECTED:' + ','.join(sorted({d['code'] for d in diags}))[:200]
        return None

    def _next(self, event, stage, facts, intent, comps, payload, session):
        """(new stage, compensations) or (None, None) if the event is illegal in this stage."""
        if stage == 'ROLLING_BACK':
            if event == 'compensation_done':
                step = payload.get('step')
                if not comps or step != comps[0]:
                    return None, None
                remaining = comps[1:]
                self._undo_fact(step, facts)
                return ('ROLLED_BACK' if not remaining else 'ROLLING_BACK'), remaining
            if event == 'compensation_failed':
                return 'FAILED', comps
            if event in ('rollback', 'destination_crashed', 'source_crashed', 'destination_heartbeat'):
                return 'ROLLING_BACK', comps
            return None, None
        if event == 'source_crashed' and stage in ROLL_FORWARD_FROM:
            return 'COMMITTED', comps  # the destination already owns the player
        if event in ('rollback', 'destination_crashed', 'source_crashed'):
            if event == 'destination_crashed':
                facts['destination_started'] = False
                facts['destination_visible'] = False
            steps = compensations_for(facts, intent['mode'], source_alive=event != 'source_crashed')
            return ('ROLLING_BACK' if steps else 'ROLLED_BACK'), steps
        if event in FACT_EVENTS:
            if FACT_EVENTS[event] is not None:
                key, value = FACT_EVENTS[event]
                if event == 'input_transferred' and stage != 'INPUT_SWITCHING':
                    return None, None
                facts[key] = value
            return stage, comps
        target = FORWARD_TRANSITIONS.get((stage, event))
        if target is None:
            return None, None
        if event == 'source_locked':
            facts['source_locked'] = True
        elif event == 'destination_started':
            if not facts['source_synced']:
                return None, None
            facts['destination_started'] = True
            facts['destination_session'] = facts['destination_session'] or session
        elif event == 'correct_save_confirmed':
            facts['save_confirmed'] = True
        elif event == 'world_ready':
            facts['world_ready'] = True
        elif event == 'overlay_ready':
            facts['overlay_ready'] = True
        elif event == 'source_hidden':
            facts['source_hidden'] = True
        elif event == 'destination_visible':
            facts['destination_visible'] = True
        elif event == 'player_ready':
            if facts['input_owner'] != 'DESTINATION':
                return None, None
        return target, comps

    @staticmethod
    def _undo_fact(step, facts):
        undo = {'RETURN_INPUT_TO_SOURCE': ('input_owner', 'SOURCE'), 'SHOW_SOURCE': ('source_hidden', False),
                'HIDE_DESTINATION': ('destination_visible', False), 'RESTORE_SOURCE_AUDIO': ('audio_faded', False),
                'UNLOCK_SOURCE': ('source_locked', False), 'PARK_DESTINATION': ('destination_visible', False),
                'STOP_DESTINATION': ('destination_started', False)}
        key, value = undo[step]
        facts[key] = value

    def recover(self):
        """After a Bridge restart: roll forward where the destination owns the player, else roll back."""
        out = {}
        for row in self._db.execute("SELECT transition_id, stage FROM transitions WHERE stage NOT IN "
                                    "('COMMITTED','ROLLED_BACK','FAILED') ORDER BY transition_id").fetchall():
            tid, stage = row['transition_id'], row['stage']
            if stage in ROLL_FORWARD_FROM:
                with self._tx() as db:
                    db.execute("UPDATE transitions SET stage='COMMITTED', generation=generation+1,"
                               " outcome=? WHERE transition_id=?",
                               (canonical_json({'reason': 'recovered: destination already owned the player'}), tid))
                    self._log(db, tid, 'recover_commit', stage, 'COMMITTED', 'ACCEPTED', {})
                out[tid] = 'COMMITTED'
            elif stage == 'ROLLING_BACK':
                out[tid] = 'ROLLING_BACK'
            else:
                out[tid] = self.apply(tid, 'rollback', payload={'reason': 'bridge restarted mid-transition'})['stage']
        return out


def compensations_for(facts, mode, *, source_alive=True):
    """Ordered compensations that undo exactly what the facts say was done."""
    steps = []
    if facts['input_owner'] == 'DESTINATION':
        steps.append('RETURN_INPUT_TO_SOURCE')
    if facts['source_hidden'] and source_alive:
        steps.append('SHOW_SOURCE')
    if facts['destination_visible']:
        steps.append('HIDE_DESTINATION')
    if facts['audio_faded'] and source_alive:
        steps.append('RESTORE_SOURCE_AUDIO')
    if facts['source_locked'] and source_alive:
        steps.append('UNLOCK_SOURCE')
    if facts['destination_started']:
        steps.append('PARK_DESTINATION' if mode == 'WARM' else 'STOP_DESTINATION')
    return steps
