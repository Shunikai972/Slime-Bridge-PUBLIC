"""Transport-agnostic Bridge server core: sessions, presence, chat, realm membership, resume.

``receive(conn, msg)`` returns the outbound ``(conn, message)`` pairs; the caller (an in-memory
transport in Cloud, a socket server locally) delivers them. Time comes from an injected clock, so
grace periods and rate limits are deterministic in tests.

Rules enforced here:
- unauthenticated connections may only HELLO, RESUME, PING or BYE
- protocol version negotiation; authentication through the configured ``AuthProvider`` only
- one live session per player: a new HELLO replaces the old session (the old connection gets BYE)
- RESUME moves the session to the new connection and replays unacknowledged server messages;
  token rotation is retry-safe until the first authenticated client message confirms the reconnect
- client sequence numbers strictly increase; an exact duplicate (same message id) is re-acked with
  no effect, an older sequence is rejected as stale
- envelope session/player/realm must match the session (wrong-realm messages are rejected)
- idempotency keys make chat and realm switches retry-safe; a reused key with a different body is
  rejected
- realm membership respects the server mode and per-realm content compatibility: an incompatible
  client may stay on the server (lobby or its compatible realm) but cannot enter that realm
"""

import hmac
import uuid

from bridge.content.canonical import sha256_json
from bridge.core import schema as S
from .protocol import _TRANSITION, CAPABILITIES, MODES, REALMS, message, mode_realms, negotiate, validate

OUTBOX_LIMIT = 256
SEEN_LIMIT = 512
IDEMPOTENCY_LIMIT = 256


class ServerConfig:
    def __init__(self, *, name, mode, max_players=16, required_content=None, required_packages=None,
                 capabilities=CAPABILITIES, grace_seconds=60, chat_per_window=5, chat_window_seconds=10, motd=None):
        if mode not in MODES or max_players < 1 or grace_seconds < 0:
            raise ValueError('invalid server configuration')
        hosted = mode_realms(mode)
        self.name, self.mode, self.max_players = name, mode, max_players
        self.required_content = {r: h for r, h in (required_content or {}).items() if r in hosted}
        self.required_packages = {r: sorted(p) for r, p in (required_packages or {}).items() if r in hosted}
        self.capabilities = tuple(c for c in CAPABILITIES if c in capabilities)
        self.grace_seconds, self.motd = grace_seconds, motd
        self.chat_per_window, self.chat_window_seconds = chat_per_window, chat_window_seconds

    def to_dict(self):
        return {'name': self.name, 'mode': self.mode, 'max_players': self.max_players,
                'required_content': dict(sorted(self.required_content.items())),
                'required_packages': dict(sorted(self.required_packages.items())),
                'capabilities': list(self.capabilities), 'grace_seconds': self.grace_seconds,
                'chat_per_window': self.chat_per_window, 'chat_window_seconds': self.chat_window_seconds,
                'motd': self.motd}

    @classmethod
    def from_dict(cls, data):
        return cls(**data)


class Session:
    def __init__(self, player_id, display_name, conn, *, version, capabilities, content, profile):
        self.session_id, self.resume_token = uuid.uuid4().hex, uuid.uuid4().hex
        self.previous_resume_token = None
        self.player_id, self.display_name, self.conn = player_id, display_name, conn
        self.version, self.capabilities, self.content, self.profile = version, capabilities, content, profile
        self.realm, self.status = None, 'LOBBY'
        self.client_seq, self.server_seq = 0, 0
        self.seen, self.idempotency, self.outbox = {}, {}, []
        self.disconnected_at, self.chat_times, self.transition = None, [], None


class BridgeServer:
    def __init__(self, config, auth, *, clock, store=None):
        self.config, self.auth, self.clock, self.store = config, auth, clock, store
        self.sessions = {}        # session_id -> Session
        self.by_player = {}       # player_id -> session_id
        self.by_conn = {}         # conn -> session_id
        self.connections = set()
        if store is not None:
            store.save_config(config.to_dict())
            # A stored switch that no longer validates could never be resumed or cleared by its client.
            store.reconcile_transitions(lambda doc: isinstance(doc, dict) and not S.check(_TRANSITION, doc))

    # -- helpers ---------------------------------------------------------------------------------

    def _send(self, session, type_, body, out):
        session.server_seq += 1
        msg = message(type_, body, seq=session.server_seq, session_id=session.session_id,
                      player_id=session.player_id, realm=session.realm)
        session.outbox.append(msg)
        del session.outbox[:-OUTBOX_LIMIT]
        if session.conn is not None:
            out.append((session.conn, msg))
        return msg

    @staticmethod
    def _reject(conn, code, detail='', ref=None, session=None):
        return (conn, message('REJECT', {'code': code, 'detail': detail[:200], 'ref': ref},
                              session_id=session and session.session_id))

    def _realms_for(self, content):
        out = {}
        for realm in mode_realms(self.config.mode):
            required = self.config.required_content.get(realm)
            if required is None or content.get(realm) == required:
                out[realm] = {'allowed': True, 'reason': None}
            else:
                out[realm] = {'allowed': False, 'reason': 'CONTENT_INCOMPATIBLE' if realm in content
                              else 'CONTENT_UNDECLARED'}
        return out

    def _player_list(self):
        return {'players': [{'player_id': s.player_id, 'display_name': s.display_name, 'realm': s.realm,
                             'status': s.status}
                            for s in sorted(self.sessions.values(), key=lambda s: s.player_id)]}

    def _broadcast_presence(self, out, *, exclude=None):
        body = self._player_list()
        for session in self.sessions.values():
            if session.session_id != exclude:
                self._send(session, 'PLAYER_LIST', body, out)

    def _end(self, session, reason, out):
        if self.store is not None and reason != 'replaced':
            # BYE or grace expiry ends the player's presence, and with it any in-flight realm switch. A
            # replacing session (same player reconnecting) inherits the switch instead.
            self.store.clear_transition(session.player_id)
        if session.conn is not None:
            self._send(session, 'BYE', {'reason': reason}, out)
            self.by_conn.pop(session.conn, None)
        self.sessions.pop(session.session_id, None)
        if self.by_player.get(session.player_id) == session.session_id:
            self.by_player.pop(session.player_id)

    # -- connection lifecycle --------------------------------------------------------------------

    def connect(self, conn):
        self.connections.add(conn)

    def disconnect(self, conn):
        out = []
        self.connections.discard(conn)
        session_id = self.by_conn.pop(conn, None)
        session = self.sessions.get(session_id)
        if session is not None and session.conn == conn:
            session.conn, session.status, session.disconnected_at = None, 'AWAY', self.clock()
            self._broadcast_presence(out, exclude=session.session_id)
        return out

    def tick(self):
        """Expire sessions whose grace period ended (deterministic: uses the injected clock)."""
        out, now = [], self.clock()
        for session in list(self.sessions.values()):
            if session.conn is None and session.disconnected_at is not None and \
                    now - session.disconnected_at >= self.config.grace_seconds:
                self._end(session, 'expired', out)
                self._broadcast_presence(out)
        return out

    def announce(self, text, level='INFO'):
        out = []
        for session in self.sessions.values():
            self._send(session, 'SERVER_MESSAGE', {'text': text, 'level': level}, out)
        return out

    # -- inbound ---------------------------------------------------------------------------------

    def receive(self, conn, msg):
        if conn not in self.connections:
            return []
        problems = validate(msg, direction='client')
        if problems:
            return [self._reject(conn, 'MALFORMED', problems[0])]
        session = self.sessions.get(self.by_conn.get(conn))
        kind = msg['type']
        if session is None:
            if kind == 'HELLO':
                return self._hello(conn, msg)
            if kind == 'RESUME':
                return self._resume(conn, msg)
            if kind == 'PING':
                return [(conn, message('PONG', {'nonce': msg['body']['nonce']}))]
            if kind == 'BYE':
                return []
            return [self._reject(conn, 'NOT_AUTHENTICATED', kind, ref=msg['message_id'])]
        if kind in ('HELLO', 'RESUME'):
            return [self._reject(conn, 'WRONG_SESSION', 'already authenticated', ref=msg['message_id'], session=session)]
        if msg['session_id'] != session.session_id or msg['player_id'] != session.player_id:
            return [self._reject(conn, 'WRONG_SESSION', 'envelope does not match the session', ref=msg['message_id'],
                                 session=session)]
        # A well-formed message on the resumed connection with its session/player envelope confirms
        # the reconnect, even if it is a duplicate or its application request is subsequently rejected.
        session.previous_resume_token = None
        out = []
        if msg['message_id'] in session.seen:
            self._send(session, 'ACK', {'seq': session.seen[msg['message_id']], 'message_id': msg['message_id']}, out)
            return out
        if msg['seq'] <= session.client_seq:
            return [self._reject(conn, 'STALE_SEQUENCE', f"seq {msg['seq']} <= {session.client_seq}",
                                 ref=msg['message_id'], session=session)]
        if msg['realm'] is not None and msg['realm'] != session.realm:
            return [self._reject(conn, 'WRONG_REALM', 'message names a realm the player is not in',
                                 ref=msg['message_id'], session=session)]
        key = msg['idempotency_key']
        digest = sha256_json({'type': kind, 'body': msg['body']})
        if key is not None and key in session.idempotency:
            if session.idempotency[key] != digest:
                return [self._reject(conn, 'DUPLICATE_KEY_CONFLICT', key, ref=msg['message_id'], session=session)]
            self._accept(session, msg)
            self._send(session, 'ACK', {'seq': msg['seq'], 'message_id': msg['message_id']}, out)
            return out
        handler = getattr(self, '_on_' + kind.lower())
        rejection = handler(session, msg, out)
        if rejection is not None:
            return out + [self._reject(conn, rejection[0], rejection[1], ref=msg['message_id'], session=session)]
        self._accept(session, msg)
        if key is not None:
            session.idempotency[key] = digest
            while len(session.idempotency) > IDEMPOTENCY_LIMIT:
                session.idempotency.pop(next(iter(session.idempotency)))
        if kind not in ('ACK', 'BYE', 'PING'):
            self._send(session, 'ACK', {'seq': msg['seq'], 'message_id': msg['message_id']}, out)
        return out

    def _accept(self, session, msg):
        session.client_seq = msg['seq']
        session.seen[msg['message_id']] = msg['seq']
        while len(session.seen) > SEEN_LIMIT:
            session.seen.pop(next(iter(session.seen)))

    def _hello(self, conn, msg):
        body = msg['body']
        version = negotiate(body['versions'])
        if version is None:
            return [self._reject(conn, 'PROTOCOL_UNSUPPORTED', 'no common protocol version')]
        auth = self.auth.authenticate(body['auth']['provider'], body['auth']['credential'])
        if auth is None:
            return [self._reject(conn, 'AUTH_FAILED', 'authentication refused')]
        out = []
        previous = self.sessions.get(self.by_player.get(auth.player_id))
        live = sum(1 for s in self.sessions.values() if s is not previous)
        if live >= self.config.max_players:
            return [self._reject(conn, 'SERVER_FULL', f'{live} players')]
        if previous is not None:
            self._end(previous, 'replaced', out)
        capabilities = [c for c in body['capabilities'] if c in self.config.capabilities]
        session = Session(auth.player_id, body['display_name'].strip(), conn, version=version,
                          capabilities=capabilities, content=dict(body['content']), profile=body['profile'])
        realms = self._realms_for(session.content)
        wanted = body['realm']
        if wanted is not None and realms.get(wanted, {}).get('allowed'):
            session.realm, session.status = wanted, 'ONLINE'
        self.sessions[session.session_id] = session
        self.by_player[auth.player_id] = session.session_id
        self.by_conn[conn] = session.session_id
        session.client_seq = msg['seq']
        session.seen[msg['message_id']] = msg['seq']
        if self.store is not None and body['profile'] is not None:
            self.store.associate(auth.player_id, body['profile']['bridge_profile_id'], session.display_name)
        pending = self._pending_transition(auth.player_id)
        if pending is not None:
            # A realm switch was in flight when the server (or the old session) went away: resume it so the
            # client can COMMIT or ABORT the same transition id instead of starting a second one.
            session.transition, session.status = pending, 'IN_TRANSITION'
            if session.realm is None and pending['from_realm'] in realms and realms[pending['from_realm']]['allowed']:
                session.realm = pending['from_realm']
        self._send(session, 'WELCOME', {'version': version, 'session_id': session.session_id,
                                        'resume_token': session.resume_token, 'mode': self.config.mode,
                                        'capabilities': capabilities, 'realms': realms,
                                        'player_id': auth.player_id, 'pending_transition': pending}, out)
        for realm in mode_realms(self.config.mode):
            if realm in self.config.required_content:
                self._send(session, 'CONTENT_MANIFEST', {
                    'realm': realm, 'manifest_hash': self.config.required_content[realm],
                    'required_packages': self.config.required_packages.get(realm, [])}, out)
        if self.config.motd:
            self._send(session, 'SERVER_MESSAGE', {'text': self.config.motd, 'level': 'INFO'}, out)
        self._broadcast_presence(out)
        return out

    def _pending_transition(self, player_id):
        if self.store is None:
            return None
        pending = self.store.pending_transition(player_id)
        if pending is None:
            return None
        return {k: pending[k] for k in ('transition_id', 'from_realm', 'to_realm', 'portal_id')}

    @classmethod
    def from_store(cls, store, auth, *, clock):
        """Restart a server from its persisted configuration (sessions/presence are never persisted)."""
        config = store.load_config()
        if config is None:
            raise ValueError('no persisted server configuration')
        return cls(ServerConfig.from_dict(config), auth, clock=clock, store=store)

    def _resume(self, conn, msg):
        body = msg['body']
        session = self.sessions.get(body['session_id'])
        if session is None or not (hmac.compare_digest(session.resume_token, body['resume_token']) or
                                   (session.previous_resume_token is not None and
                                    hmac.compare_digest(session.previous_resume_token, body['resume_token']))):
            return [self._reject(conn, 'RESUME_REJECTED', 'unknown session or token')]
        out = []
        if session.conn is not None and session.conn != conn:
            old = session.conn
            out.append((old, message('BYE', {'reason': 'resumed_elsewhere'}, session_id=session.session_id)))
            self.by_conn.pop(old, None)
        session.conn, session.disconnected_at = conn, None
        session.status = 'ONLINE' if session.realm else 'LOBBY'
        self.by_conn[conn] = session.session_id
        if session.previous_resume_token is None:
            # Until the client speaks on this authenticated connection, RESUMED may have been lost.
            # Retrying either token must return the same replacement, not start another rotation.
            session.previous_resume_token = session.resume_token
            session.resume_token = uuid.uuid4().hex
        pending = [m for m in session.outbox if m['seq'] > body['last_seq_received']]
        out.extend((conn, m) for m in pending)
        self._send(session, 'RESUMED', {'session_id': session.session_id, 'replayed': len(pending),
                                        'resume_token': session.resume_token}, out)
        self._broadcast_presence(out, exclude=session.session_id)
        return out

    # -- authenticated handlers: return (code, detail) to reject, None to accept -----------------------

    def _on_ack(self, session, msg, out):
        session.outbox = [m for m in session.outbox if m['seq'] > msg['body']['seq']]

    def _on_ping(self, session, msg, out):
        self._send(session, 'PONG', {'nonce': msg['body']['nonce']}, out)

    def _on_bye(self, session, msg, out):
        self._end(session, 'goodbye', out)
        self._broadcast_presence(out)

    def _on_presence(self, session, msg, out):
        status = msg['body']['status']
        if status not in ('ONLINE', 'AWAY'):
            return 'MALFORMED', 'clients may only set ONLINE or AWAY'
        if status == 'ONLINE' and session.realm is None:
            status = 'LOBBY'
        session.status = status
        self._broadcast_presence(out)

    def _on_chat(self, session, msg, out):
        if 'chat' not in session.capabilities:
            return 'CAPABILITY_MISSING', 'chat'
        now = self.clock()
        session.chat_times = [t for t in session.chat_times if now - t < self.config.chat_window_seconds]
        if len(session.chat_times) >= self.config.chat_per_window:
            return 'RATE_LIMITED', 'too many messages'
        body = msg['body']
        event = {'channel': body['channel'], 'text': body['text'], 'from': session.player_id,
                 'from_name': session.display_name, 'realm': session.realm}
        if body['channel'] == 'REALM':
            if session.realm is None:
                return 'WRONG_REALM', 'not in a realm'
            targets = [s for s in self.sessions.values() if s.realm == session.realm]
        elif body['channel'] == 'WHISPER':
            target = self.sessions.get(self.by_player.get(body['to'] or ''))
            if target is None or 'whisper' not in session.capabilities:
                return 'UNKNOWN_PLAYER', 'no such player'
            targets = [target, session]
        else:
            targets = list(self.sessions.values())
        session.chat_times.append(now)
        for target in targets:
            self._send(target, 'CHAT_EVENT', event, out)

    def _on_realm_switch(self, session, msg, out):
        transition, phase = msg['body']['transition'], msg['body']['phase']
        if 'realm_switch' not in session.capabilities:
            return 'CAPABILITY_MISSING', 'realm_switch'
        if transition['to_realm'] not in mode_realms(self.config.mode):
            return 'REALM_NOT_HOSTED', transition['to_realm']
        if phase == 'BEGIN':
            if session.transition is not None:
                return 'DUPLICATE_KEY_CONFLICT', 'a realm switch is already in progress'
            if transition['from_realm'] != session.realm:
                return 'WRONG_REALM', 'switch must start from the current realm'
            if not self._realms_for(session.content)[transition['to_realm']]['allowed']:
                return 'CONTENT_INCOMPATIBLE', transition['to_realm']
            session.transition, session.status = dict(transition), 'IN_TRANSITION'
            if self.store is not None:
                self.store.record_transition(session.player_id, transition)
        else:
            if session.transition is None or session.transition['transition_id'] != transition['transition_id']:
                return 'WRONG_SESSION', 'no such realm switch in progress'
            if phase == 'COMMIT':
                session.realm = transition['to_realm']
            session.transition, session.status = None, 'ONLINE' if session.realm else 'LOBBY'
            if self.store is not None:
                self.store.clear_transition(session.player_id, transition['transition_id'])
        self._broadcast_presence(out)

    def _on_txn_meta(self, session, msg, out):
        if 'transaction_metadata' not in session.capabilities:
            return 'CAPABILITY_MISSING', 'transaction_metadata'
        realms = set(msg['body']['realms'])
        if session.realm not in realms:
            return 'WRONG_REALM', 'metadata must involve the sender realm'
        for target in self.sessions.values():
            if target is not session and target.realm in realms:
                self._send(target, 'TXN_META', msg['body'], out)

    # -- read model --------------------------------------------------------------------------------

    def snapshot(self):
        populations = {realm: 0 for realm in REALMS if realm in mode_realms(self.config.mode)}
        lobby = 0
        for session in self.sessions.values():
            if session.realm in populations:
                populations[session.realm] += 1
            else:
                lobby += 1
        return {'name': self.config.name, 'mode': self.config.mode, 'players': len(self.sessions),
                'max_players': self.config.max_players, 'realm_populations': populations, 'lobby': lobby,
                'connected': sum(1 for s in self.sessions.values() if s.conn is not None),
                'player_list': self._player_list()['players']}
