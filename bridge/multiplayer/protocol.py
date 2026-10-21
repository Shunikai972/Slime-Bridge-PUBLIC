"""``bridge-net`` protocol v1: versioned envelopes, message types, strict validation, negotiation.

Envelope (every message, both directions)::

    {"v": 1, "type": "CHAT", "message_id": "<32 hex>", "session_id": "<32 hex>|null",
     "player_id": "<id>|null", "realm": "SR1|SR2|null", "seq": 7, "idempotency_key": "<key>|null",
     "body": {...type-specific...}}

``seq`` is per session and per direction, strictly increasing for client messages (the server
rejects stale sequence numbers and re-acknowledges exact duplicates). ``idempotency_key`` lets a
client retry a side-effecting request (chat, realm switch) across a reconnect without doubling it.
Everything received is untrusted: sizes, enums, ids and text are bounded and checked here.
"""

import re
import uuid

from bridge.core import schema as S

PROTOCOL = 'bridge-net'
VERSIONS = (1,)
REALMS = ('SR1', 'SR2')
MODES = ('SR1_ONLY', 'SR2_ONLY', 'HYBRID')
CAPABILITIES = ('presence', 'chat', 'whisper', 'realm_switch', 'content_manifest', 'announcements', 'resume',
                'transaction_metadata')
CHANNELS = ('GLOBAL', 'REALM', 'WHISPER')
PRESENCE_STATUS = ('ONLINE', 'AWAY', 'IN_TRANSITION', 'LOBBY')
REJECT_CODES = ('PROTOCOL_UNSUPPORTED', 'AUTH_FAILED', 'SERVER_FULL', 'MALFORMED', 'STALE_SEQUENCE', 'WRONG_REALM',
                'WRONG_SESSION', 'NOT_AUTHENTICATED', 'RESUME_REJECTED', 'REALM_NOT_HOSTED', 'CONTENT_INCOMPATIBLE',
                'RATE_LIMITED', 'CAPABILITY_MISSING', 'UNKNOWN_PLAYER', 'DUPLICATE_KEY_CONFLICT')
MAX_TEXT = 500
MAX_NAME = 32
_HEX32 = r'[0-9a-f]{32}'
_PLAYER = r'[A-Za-z0-9_.:-]{1,64}'
_KEY = r'[A-Za-z0-9_.:-]{1,64}'
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f‪-‮⁦-⁩]')

_SESSION = S.optional(S.string(_HEX32))
_PROFILE = S.obj({'bridge_profile_id': S.string(r'[0-9a-f]{32}'),
                  'realm_saves': S.map_of(S.enum(*REALMS), S.string(max_length=128))})
_TRANSITION = S.obj({'transition_id': S.string(_HEX32), 'from_realm': S.enum(*REALMS), 'to_realm': S.enum(*REALMS),
                     'portal_id': S.optional(S.string(r'[a-z0-9._-]{1,64}'))})

BODIES = {
    'HELLO': S.obj({'versions': S.list_of(S.integer(minimum=1, maximum=1000), max_items=8, unique=True),
                    'capabilities': S.list_of(S.enum(*CAPABILITIES), max_items=16, unique=True),
                    'display_name': S.string(max_length=MAX_NAME),
                    'auth': S.obj({'provider': S.string(r'[a-z0-9._-]{1,64}'),
                                   'credential': S.string(max_length=4096)}),
                    'content': S.map_of(S.enum(*REALMS), S.sha256()),
                    'profile': S.optional(_PROFILE), 'realm': S.optional(S.enum(*REALMS))}),
    'WELCOME': S.obj({'version': S.integer(minimum=1), 'session_id': S.string(_HEX32),
                      'resume_token': S.string(_HEX32), 'mode': S.enum(*MODES),
                      'capabilities': S.list_of(S.enum(*CAPABILITIES), max_items=16, unique=True),
                      'realms': S.map_of(S.enum(*REALMS), S.obj({'allowed': S.boolean(),
                                                                 'reason': S.optional(S.string(max_length=64))})),
                      'player_id': S.string(_PLAYER), 'pending_transition': S.optional(_TRANSITION)}),
    'REJECT': S.obj({'code': S.enum(*REJECT_CODES), 'detail': S.string(max_length=200, allow_empty=True),
                     'ref': S.optional(S.string(_HEX32))}),
    'RESUME': S.obj({'session_id': S.string(_HEX32), 'resume_token': S.string(_HEX32),
                     'last_seq_received': S.integer(minimum=0)}),
    'RESUMED': S.obj({'session_id': S.string(_HEX32), 'replayed': S.integer(minimum=0),
                      'resume_token': S.string(_HEX32)}),
    'PRESENCE': S.obj({'status': S.enum(*PRESENCE_STATUS)}),
    'REALM_SWITCH': S.obj({'transition': _TRANSITION, 'phase': S.enum('BEGIN', 'COMMIT', 'ABORT')}),
    'CHAT': S.obj({'channel': S.enum(*CHANNELS), 'text': S.string(max_length=MAX_TEXT),
                   'to': S.optional(S.string(_PLAYER))}),
    'CHAT_EVENT': S.obj({'channel': S.enum(*CHANNELS), 'text': S.string(max_length=MAX_TEXT),
                         'from': S.string(_PLAYER), 'from_name': S.string(max_length=MAX_NAME),
                         'realm': S.optional(S.enum(*REALMS))}),
    'PLAYER_LIST': S.obj({'players': S.list_of(S.obj({
        'player_id': S.string(_PLAYER), 'display_name': S.string(max_length=MAX_NAME),
        'realm': S.optional(S.enum(*REALMS)), 'status': S.enum(*PRESENCE_STATUS)}), max_items=1024)}),
    'SERVER_MESSAGE': S.obj({'text': S.string(max_length=MAX_TEXT), 'level': S.enum('INFO', 'WARNING')}),
    'CONTENT_MANIFEST': S.obj({'realm': S.enum(*REALMS), 'manifest_hash': S.sha256(),
                               'required_packages': S.list_of(S.string(r'[A-Za-z0-9:._-]{1,160}'), max_items=1024)}),
    'TXN_META': S.obj({'transaction_id': S.string(_HEX32), 'kind': S.string(r'[a-z_]{1,32}'),
                       'state': S.string(r'[A-Z_]{1,32}'), 'realms': S.list_of(S.enum(*REALMS), max_items=2)}),
    'ACK': S.obj({'seq': S.integer(minimum=0), 'message_id': S.string(_HEX32)}),
    'PING': S.obj({'nonce': S.string(_HEX32)}),
    'PONG': S.obj({'nonce': S.string(_HEX32)}),
    'BYE': S.obj({'reason': S.string(r'[a-z_]{1,32}')}),
}
CLIENT_TYPES = ('HELLO', 'RESUME', 'PRESENCE', 'REALM_SWITCH', 'CHAT', 'PING', 'BYE', 'ACK', 'TXN_META')
SERVER_TYPES = ('WELCOME', 'REJECT', 'RESUMED', 'CHAT_EVENT', 'PLAYER_LIST', 'SERVER_MESSAGE', 'CONTENT_MANIFEST',
                'PONG', 'BYE', 'ACK', 'TXN_META')
_ENVELOPE = S.obj({'v': S.integer(minimum=1, maximum=1000), 'type': S.enum(*BODIES), 'message_id': S.string(_HEX32),
                   'session_id': _SESSION, 'player_id': S.optional(S.string(_PLAYER)),
                   'realm': S.optional(S.enum(*REALMS)), 'seq': S.integer(minimum=0, maximum=2 ** 53),
                   'idempotency_key': S.optional(S.string(_KEY)), 'body': S.any_json()})


def message(type_, body, *, seq=0, session_id=None, player_id=None, realm=None, idempotency_key=None, message_id=None,
            version=1):
    return {'v': version, 'type': type_, 'message_id': message_id or uuid.uuid4().hex, 'session_id': session_id,
            'player_id': player_id, 'realm': realm, 'seq': seq, 'idempotency_key': idempotency_key, 'body': body}


def validate(msg, *, direction=None):
    """'path: reason' problems; empty means well-formed. ``direction`` is 'client' or 'server'."""
    problems = S.check(_ENVELOPE, msg)
    if problems:
        return problems
    if msg['v'] not in VERSIONS:
        return [f"$.v: unsupported protocol version {msg['v']}"]
    allowed = {'client': CLIENT_TYPES, 'server': SERVER_TYPES}.get(direction)
    if allowed is not None and msg['type'] not in allowed:
        return [f"$.type: {msg['type']} is not a {direction} message"]
    problems = S.check(BODIES[msg['type']], msg['body'], '$.body')
    for key in ('text', 'display_name', 'from_name'):
        value = msg['body'].get(key) if isinstance(msg['body'], dict) else None
        if isinstance(value, str) and (_CONTROL.search(value) or not value.strip()):
            problems.append(f'$.body.{key}: control/bidi characters or blank text')
    if msg['type'] == 'REALM_SWITCH' and not problems:
        transition = msg['body']['transition']
        if transition['from_realm'] == transition['to_realm']:
            problems.append('$.body.transition: realms must differ')
    return problems


def negotiate(client_versions, server_versions=VERSIONS):
    common = sorted(set(client_versions) & set(server_versions))
    return common[-1] if common else None


def mode_realms(mode):
    return {'SR1_ONLY': ('SR1',), 'SR2_ONLY': ('SR2',), 'HYBRID': REALMS}[mode]
