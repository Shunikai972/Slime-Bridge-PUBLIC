"""Local, versioned BridgeHost heartbeat transport and 2PC transfer protocol endpoints."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

import hmac
import json
import os
import re
import secrets
from urllib.parse import parse_qs, urlsplit
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, TYPE_CHECKING

from bridge.counterpart.legacy_quarantine import require_not_retired
from bridge.counterpart.realms import CONVENTIONS
from bridge.counterpart.world_ready import evaluate_world_ready
from bridge.transfer.binding import TransferBindingError, TransferBindingResolver, binding_for_row
from bridge.transfer.store import SUPPORTED_CANONICAL_ITEMS

if TYPE_CHECKING:
    from bridge.transfer.store import TransferStore

HEARTBEAT_STALE_AFTER = 20.0


def _bounded_str(value, limit: int) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= limit


PORTAL_REQUEST_LIMIT = 64
TRAVEL_EVENT_LIMIT = 500
TRAVEL_QUEUE_LIMIT = 16
TRAVEL_MESSAGE_KINDS = ('prepare_departure', 'hold', 'resume', 'arrive', 'abort')
TRAVEL_EVENT_KINDS = ('departure_ready', 'held', 'resumed', 'arrived', 'aborted', 'failed')
_TRAVEL_ID = re.compile(r'[A-Za-z0-9._-]{1,64}')
CARRY_MODES = ('ALL', 'SLIMES_PLORTS', 'NONE')
CARRY_ENTRY_LIMIT = 64


def _valid_carry(carry) -> bool:
    """Items that travel with the player: native names of the sending game -> counts (never more than a few dozen kinds)."""
    if not isinstance(carry, dict) or len(carry) > CARRY_ENTRY_LIMIT:
        return False
    return all(isinstance(k, str) and _TRAVEL_ID.fullmatch(k) is not None and type(v) is int and 1 <= v <= 9999 for k, v in carry.items())


def _valid_travel_event(data) -> bool:
    """Plugin -> host progress report for one crossing (what happened, never a command)."""
    if not isinstance(data, dict) or not {'protocol_version', 'message_type', 'realm', 'transition_id', 'event'} <= set(data):
        return False
    if set(data) - {'protocol_version', 'message_type', 'realm', 'transition_id', 'event', 'ack_id', 'detail', 'carry'}:
        return False
    if 'carry' in data and not _valid_carry(data['carry']):
        return False
    detail = data.get('detail', '')
    return (data['protocol_version'] == 1 and data['message_type'] == 'travel_event' and data['realm'] in ('SR1', 'SR2')
            and isinstance(data['transition_id'], str) and _TRAVEL_ID.fullmatch(data['transition_id']) is not None
            and data['event'] in TRAVEL_EVENT_KINDS and isinstance(detail, str) and len(detail) <= 200
            and (data.get('ack_id') is None or (isinstance(data['ack_id'], str) and _TRAVEL_ID.fullmatch(data['ack_id']) is not None)))


def _valid_travel_send(data) -> bool:
    """Launcher -> host: queue one instruction for a realm's plugin."""
    if not isinstance(data, dict) or set(data) != {'realm', 'message'} or data['realm'] not in ('SR1', 'SR2'):
        return False
    message = data['message']
    if not isinstance(message, dict) or message.get('kind') not in TRAVEL_MESSAGE_KINDS:
        return False
    if not isinstance(message.get('transition_id'), str) or _TRAVEL_ID.fullmatch(message['transition_id']) is None:
        return False
    allowed = {'kind', 'transition_id', 'destination', 'source', 'portal_id', 'reason', 'policy', 'carry', 'carry_mode'}
    if set(message) - allowed:
        return False
    if 'carry' in message and not _valid_carry(message['carry']):
        return False
    if message.get('carry_mode', 'NONE') not in CARRY_MODES:
        return False
    return all(isinstance(message.get(k, ''), str) and len(message.get(k, '')) <= 80
               for k in allowed - {'kind', 'transition_id', 'carry'})


def _valid_portal_cross(data) -> bool:
    """A plugin asks the host to start a crossing: who/where only, never an inventory or a destination choice."""
    if not isinstance(data, dict) or set(data) != {'protocol_version', 'message_type', 'realm', 'portal_id', 'save_id', 'request_id'}:
        return False
    return (data['protocol_version'] == 1 and data['message_type'] == 'portal_cross' and data['realm'] in ('SR1', 'SR2')
            and _bounded_str(data['portal_id'], 64) and re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', data['portal_id']) is not None
            and _bounded_str(data['save_id'], 128) and bool(data['save_id'].strip()) and _bounded_str(data['request_id'], 128))


def _valid_heartbeat(data) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get('protocol_version') != 1 or data.get('message_type') != 'heartbeat':
        return False
    if data.get('realm') not in ('SR1', 'SR2') or not _bounded_str(data.get('request_id'), 128):
        return False
    pid = data.get('pid')
    if pid is not None and (type(pid) is not int or not (0 < pid < 2 ** 32)):
        return False
    version = data.get('plugin_version')
    if version is not None and not _bounded_str(version, 64):
        return False
    if 'status' in data:
        status = data['status']
        required = {'player_alive', 'save_id'}
        counts = {'pink_slime_count', 'pink_plort_count'}
        if not isinstance(status, dict) or not required <= set(status) or set(status) - (required | counts):
            return False
        # Counts describe one inventory snapshot: both or neither (unknown is omitted, never zero).
        if len(counts & set(status)) == 1:
            return False
        if type(status['player_alive']) is not bool or not _bounded_str(status['save_id'], 128) or not status['save_id'].strip():
            return False
        if any(type(status[key]) is not int or not 0 <= status[key] <= 1_000_000
               for key in counts & set(status)):
            return False
    return True


OP_ID_RE = re.compile(r'\A[A-Za-z0-9:_-]{1,128}\Z')
COMMAND_KINDS = ('query', 'remove', 'add', 'lab_give')


def _valid_command_result(data) -> bool:
    """Plugin -> host report of one executed (or refused) inventory command."""
    if not isinstance(data, dict):
        return False
    if data.get('protocol_version') != 1 or data.get('message_type') != 'command_result':
        return False
    if data.get('realm') not in ('SR1', 'SR2'):
        return False
    op_id = data.get('op_id')
    if not isinstance(op_id, str) or not OP_ID_RE.match(op_id):
        return False
    if type(data.get('ok')) is not bool or type(data.get('applied')) is not bool:
        return False
    pid = data.get('pid')
    if type(pid) is not int or not (0 < pid < 2 ** 32):
        return False
    for key in ('before', 'after'):
        value = data.get(key)
        if value is not None and (type(value) is not int or not -1 <= value <= 1_000_000):
            return False
    for key, limit in (('reason', 256), ('save_id', 128), ('item', 64)):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            return False
    if 'can_add' in data and type(data['can_add']) is not bool:
        return False
    if 'count' in data and (type(data['count']) is not int or not 0 <= data['count'] <= 30):
        return False
    for key in ('uncertain', 'persisted', 'verified'):
        if key in data and type(data[key]) is not bool:
            return False
    return _inventory_receipt_complete(data) if data.get('kind') in COMMAND_KINDS else True


def _inventory_receipt_complete(data: dict) -> bool:
    """Incomplete success must remain deliverable, never become a poisoned first result."""
    required = {'kind', 'item', 'count', 'save_id', 'before', 'after', 'uncertain', 'persisted'}
    if not required <= data.keys():
        return False
    if (data['kind'] not in COMMAND_KINDS or not isinstance(data['item'], str) or not data['item']
            or type(data['count']) is not int or not 0 <= data['count'] <= 30
            or not isinstance(data['save_id'], str) or not data['save_id']
            or type(data['before']) is not int or type(data['after']) is not int
            or type(data['uncertain']) is not bool or type(data['persisted']) is not bool):
        return False
    if data['kind'] == 'query':
        return (data['applied'] is False and (data['ok'] is False or
                (data['uncertain'] is False and data['before'] >= 0 and data['before'] == data['after']
                 and type(data.get('can_add')) is bool)))
    if data['ok'] is True:
        digest, name = data.get('sha256'), data.get('save_file')
        expected_delta = -data['count'] if data['kind'] == 'remove' else data['count']
        return (data['applied'] is True and data['uncertain'] is False and data['persisted'] is True
                and data.get('verified') is True and data['count'] > 0
                and data['before'] >= 0 and data['after'] >= 0
                and data['after'] - data['before'] == expected_delta
                and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) is not None
                and isinstance(name, str) and 0 < len(name) <= 128 and name not in ('.', '..')
                and re.fullmatch(r'[A-Za-z0-9_.-]+', name) is not None)
    return True  # A fully described refusal/uncertainty is retained for reconciliation, not success.


def _matches_creation_result(data: dict, command: dict, expected_pid: int, *, realm: str) -> bool:
    """Bind even refused replies to the issued native creation and owned game process."""
    if (data.get('kind') != command['kind'] or data.get('creation_id') != command['creation_id'] or
            data.get('pid') != expected_pid):
        return False
    save_id = CONVENTIONS[realm].native_save_id(command['creation_id'])
    if data.get('save_id') not in (save_id, '', None):
        return False
    verified = data.get('verified')
    if verified is not None and type(verified) is not bool:
        return False
    if data['ok'] and data.get('save_id') != save_id:
        return False
    if data['ok']:
        if (data.get('uncertain') is not False or type(data.get('persisted')) is not bool or
                type(data.get('before')) is not int or type(data.get('after')) is not int):
            return False
        if verified is not True:
            # A read-only reconcile may prove absence; a create ACK cannot.
            return (verified is False and command['kind'] == 'reconcile_save' and
                    type(data.get('can_create')) is bool and
                    data['applied'] is False and data['persisted'] is False and
                    data['before'] == data['after'] == 0 and
                    data.get('save_file') in (None, '') and data.get('sha256') in (None, ''))
        # SR1 read-only reconciliation/existing-save observations leave Persisted false.
        if (((data['applied'] or realm == 'SR2') and data['persisted'] is not True) or
                (command['kind'] == 'reconcile_save' and data['applied'] is not False) or
                data['before'] < 1 or data['before'] != data['after']):
            return False
    if verified is True:
        filename, digest = data.get('save_file'), data.get('sha256')
        if (data['ok'] is not True or data.get('save_id') != save_id or
                not isinstance(filename, str) or
                not CONVENTIONS[realm].filename_ok(command['creation_id'], filename) or
                not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            return False
    return True


class BridgeHost:
    def __init__(self, token: str | None = None, port: int = 0, transfer_store: Optional[TransferStore] = None,
                 *, creation_realms=('SR1',), transfer_bindings: TransferBindingResolver | None = None,
                 endpoint_provider=None, endpoint_trust=None, allow_lab_give=False, portal_handler=None):
        # SR2 is a protected lab opt-in, never an automatic runtime-proof attestation.
        if not isinstance(creation_realms, (tuple, list)) or any(realm not in CONVENTIONS for realm in creation_realms):
            raise ValueError('invalid native creation realms')
        self.creation_realms = tuple(creation_realms)
        if transfer_bindings is not None and not isinstance(transfer_bindings, TransferBindingResolver):
            raise ValueError('transfer binding resolver is required')
        if endpoint_provider is not None and not callable(endpoint_provider):
            raise ValueError('local endpoint provider must be callable')
        if type(allow_lab_give) is not bool:
            raise ValueError('lab give opt-in must be boolean')
        if portal_handler is not None and not callable(portal_handler):
            raise ValueError('portal handler must be callable')
        self._portal_handler = portal_handler          # launcher-side PortalCrossing hook: request dict -> status dict
        self._portal_requests: list[dict] = []
        self._travel_queue: dict[str, list[dict]] = {'SR1': [], 'SR2': []}
        self._travel_events: list[dict] = []
        self._travel_seq = 0
        self.transfer_bindings = transfer_bindings
        self.endpoint_provider = endpoint_provider
        self.endpoint_trust = dict(endpoint_trust or {})
        self._allow_lab_give = allow_lab_give  # Controller-only opt-in, never accepted from HTTP.
        self.token = token or secrets.token_urlsafe(32)
        self._realm_tokens = {realm: secrets.token_urlsafe(32) for realm in ('SR1', 'SR2')}
        self.transfer_store = transfer_store
        self._heartbeats: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Host -> plugin command mailbox. A pending command is redelivered on every poll until a
        # result arrives (at-least-once); plugins de-duplicate by op_id so it applies at most once.
        self._cmd_cond = threading.Condition()
        self._pending: dict[str, list[dict]] = {'SR1': [], 'SR2': []}
        self._results: dict[str, dict] = {}
        self._op_realm: dict[str, str] = {}
        self._commands: dict[str, dict] = {}
        self._issued: dict[str, tuple[str, int, int]] = {}
        self._session_epoch: dict[str, int] = {'SR1': 0, 'SR2': 0}
        self._creation_context: dict[str, tuple[dict, int]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def _authorized(self, realm: str | None = None, *, admin_only: bool = False) -> bool:
                supplied = self.headers.get('Authorization', '')
                if hmac.compare_digest(supplied, 'Bearer ' + outer.token):
                    return True
                if admin_only or realm not in outer._realm_tokens:
                    return False
                return hmac.compare_digest(supplied, 'Bearer ' + outer._realm_tokens[realm])

            def do_GET(self):
                split = urlsplit(self.path)
                if split.path == '/v1/commands/next':
                    realm = (parse_qs(split.query).get('realm') or [''])[0]
                    if realm not in ('SR1', 'SR2'):
                        return self._reply(400, {'error': 'invalid_realm'})
                    if not self._authorized(realm):
                        return self._reply(401, {'error': 'unauthorized'})
                    with outer._cmd_cond:
                        queue = outer._pending[realm]
                        queue[:] = [c for c in queue if not outer._refund_fenced(realm, c)]
                        session = outer._live_session(realm)
                        # A creation owned by an old PID must not stall another live operation.
                        command = next((dict(candidate) for candidate in queue
                                        if candidate['op_id'] not in outer._creation_context or
                                        (session is not None and session[0] ==
                                         outer._creation_context[candidate['op_id']][1])), None)
                        if command is not None and command['op_id'] not in outer._creation_context:
                            try:
                                outer._command_authorized(realm, command)
                            except (ValueError, RuntimeError):
                                command = None
                        if command is not None:
                            session = outer._live_session(realm)
                            if session is None or (
                                    command['op_id'] in outer._creation_context and
                                    session[0] != outer._creation_context[command['op_id']][1]):
                                command = None
                            else:
                                if (outer.transfer_store is not None and command['kind'] == 'add'
                                        and command['op_id'].endswith(':add')
                                        and outer.transfer_store.get(command['op_id'][:-4]) is not None):
                                    if not outer.transfer_store.mark_receiver_dispatched(command['op_id'][:-4], realm, command):
                                        queue.remove(command)
                                        command = None
                                if command is not None:
                                    outer._issued[command['op_id']] = (realm, *session)
                    return self._reply(200, {'protocol_version': 1, 'command': command})
                if split.path == '/v1/realm-token':
                    realm = (parse_qs(split.query).get('realm') or [''])[0]
                    if realm not in ('SR1', 'SR2'):
                        return self._reply(400, {'error': 'invalid_realm'})
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    return self._reply(200, {'protocol_version': 1, 'realm': realm, 'token': outer._realm_tokens[realm]})
                if split.path == '/v1/travel/next':
                    realm = (parse_qs(split.query).get('realm') or [''])[0]
                    if realm not in ('SR1', 'SR2'):
                        return self._reply(400, {'error': 'invalid_realm'})
                    if not self._authorized(realm):
                        return self._reply(401, {'error': 'unauthorized'})
                    return self._reply(200, {'protocol_version': 1, 'message': outer.travel_peek(realm)})
                if split.path == '/v1/travel/events':
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    try:
                        since = int((parse_qs(split.query).get('since') or ['0'])[0])
                    except ValueError:
                        return self._reply(400, {'error': 'invalid_since'})
                    return self._reply(200, {'protocol_version': 1, 'events': outer.travel_events(since)})
                if split.path == '/v1/portal/requests':
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    return self._reply(200, {'protocol_version': 1, 'requests': outer.portal_requests()})
                if self.path.startswith('/v1/transfer/'):
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    if outer.transfer_store is None:
                        return self._reply(503, {'error': 'transfer_store_unavailable'})
                    tid = self.path[len('/v1/transfer/'):].strip()
                    row = outer.transfer_store.get(tid)
                    if row is None:
                        return self._reply(404, {'error': 'transfer_not_found'})
                    return self._reply(200, {'protocol_version': 1, 'ok': True, 'transfer': asdict(row)})
                return self._reply(404, {'error': 'not_found'})

            def do_POST(self):
                supplied = self.headers.get('Authorization', '')
                if not any(hmac.compare_digest(supplied, 'Bearer ' + token)
                           for token in (outer.token, *outer._realm_tokens.values())):
                    return self._reply(401, {'error': 'unauthorized'})
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                    return self._reply(415, {'error': 'unsupported_media_type'})
                try:
                    length = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    length = -1
                if length < 1 or length > 4096:
                    return self._reply(413, {'error': 'invalid_length'})
                try:
                    data = json.loads(self.rfile.read(length))
                except (UnicodeError, ValueError):
                    return self._reply(400, {'error': 'invalid_json'})

                if self.path == '/v1/heartbeat':
                    if not _valid_heartbeat(data):
                        return self._reply(400, {'error': 'invalid_message'})
                    if not self._authorized(data['realm']):
                        return self._reply(401, {'error': 'unauthorized'})
                    # Mailbox -> heartbeat is the shared lock order. Epoch rotation cannot
                    # interleave with command delivery or result validation/cache insertion.
                    with outer._cmd_cond, outer._lock:
                        now = time.time()
                        tick = time.monotonic()
                        previous = outer._heartbeats.get(data['realm'], {})
                        # A different PID or a stale heartbeat begins a fresh session.
                        if previous and (previous.get('pid') != data.get('pid') or
                                         tick - previous.get('received_monotonic', 0) > HEARTBEAT_STALE_AFTER):
                            previous = {}
                        if not previous:
                            outer._session_epoch[data['realm']] += 1
                        outer._heartbeats[data['realm']] = {
                            'request_id': data['request_id'],
                            'address': self.client_address[0],
                            'pid': data.get('pid'),
                            'plugin_version': data.get('plugin_version'),
                            'first_received_unix': previous.get('first_received_unix', now),
                            'received_unix': now,
                            'received_monotonic': tick,
                            'count': previous.get('count', 0) + 1,
                            **({'status': data['status']} if 'status' in data else {}),
                        }
                    return self._reply(200, {'protocol_version': 1, 'message_type': 'heartbeat_ack', 'request_id': data['request_id']})

                if self.path == '/v1/travel/event':
                    if not _valid_travel_event(data):
                        return self._reply(400, {'error': 'invalid_message'})
                    if not self._authorized(data['realm']):
                        return self._reply(401, {'error': 'unauthorized'})
                    outer.travel_record(data)
                    return self._reply(200, {'protocol_version': 1, 'ok': True})
                if self.path == '/v1/travel/send':
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    if not _valid_travel_send(data):
                        return self._reply(400, {'error': 'invalid_message'})
                    message_id = outer.travel_enqueue(data['realm'], data['message'])
                    if message_id is None:
                        return self._reply(429, {'error': 'queue_full'})
                    return self._reply(200, {'protocol_version': 1, 'ok': True, 'message_id': message_id})
                if self.path == '/v1/portal/cross':
                    if not _valid_portal_cross(data):
                        return self._reply(400, {'error': 'invalid_message'})
                    if not self._authorized(data['realm']):
                        return self._reply(401, {'error': 'unauthorized'})
                    request = outer.record_portal_request(data)
                    return self._reply(200, {'protocol_version': 1, 'ok': True, 'request_id': data['request_id'],
                                             'state': request['state']})

                if self.path == '/v1/commands/result':
                    if not _valid_command_result(data):
                        return self._reply(400, {'error': 'invalid_message'})
                    if not self._authorized(data['realm']):
                        return self._reply(401, {'error': 'unauthorized'})
                    with outer._cmd_cond:
                        expected = outer._op_realm.get(data['op_id'])
                        if expected != data['realm'] or data['op_id'] not in outer._commands:
                            return self._reply(409, {'error': 'realm_mismatch'})
                        issued = outer._issued.get(data['op_id'])
                        session = outer._live_session(data['realm'])
                        if (issued is None or session is None or issued != (data['realm'], *session)
                                or data['pid'] != session[0]):
                            return self._reply(409, {'error': 'plugin_session_mismatch'})
                        creation = outer._creation_context.get(data['op_id'])
                        if creation is not None and not _matches_creation_result(data, *creation, realm=data['realm']):
                            return self._reply(409, {'error': 'creation_provenance_mismatch'})
                        if creation is None and data.get('kind') in ('create_save', 'reconcile_save'):
                            return self._reply(409, {'error': 'unknown_creation_operation'})
                        if creation is None:
                            command = outer._commands[data['op_id']]
                            try:
                                outer._command_authorized(data['realm'], command)
                            except Exception:
                                return self._reply(409, {'error': 'endpoint_ownership_mismatch'})
                            if (data.get('kind') != command['kind'] or data.get('item') != command['item']
                                    or type(data.get('count')) is not int or data['count'] != command['count']
                                    or data.get('save_id') != command.get('save_id')):
                                return self._reply(409, {'error': 'command_provenance_mismatch'})
                            if outer._refund_fenced(data['realm'], command):
                                return self._reply(409, {'error': 'receiver_add_fenced'})
                        previous = outer._results.get(data['op_id'])
                        if previous is not None:
                            if {k: v for k, v in previous.items() if k != 'received_unix'} != data:
                                return self._reply(409, {'error': 'conflicting_result'})
                            return self._reply(200, {'protocol_version': 1, 'ok': True})
                        queue = outer._pending[data['realm']]
                        match = next((c for c in queue if c['op_id'] == data['op_id']), None)
                        if match is None and creation is None:
                            return self._reply(409, {'error': 'command_not_pending'})
                        # Reconciliation stops native-create redelivery, not a previously
                        # issued completion's eligibility. Issued/current-session and creation
                        # provenance were validated above; an undelivered create still fails.
                        if match is not None:
                            queue.remove(match)
                        # Keep the first result: a replayed report must not overwrite proof.
                        if data['op_id'] not in outer._results:
                            outer._results[data['op_id']] = {**deepcopy(data), 'received_unix': time.time()}
                        outer._cmd_cond.notify_all()
                    return self._reply(200, {'protocol_version': 1, 'ok': True})

                if self.path.startswith('/v1/transfer/'):
                    if not self._authorized(admin_only=True):
                        return self._reply(401, {'error': 'unauthorized'})
                    if outer.transfer_store is None:
                        return self._reply(503, {'error': 'transfer_store_unavailable'})
                    action = self.path[len('/v1/transfer/'):].strip()
                    try:
                        if not isinstance(data, dict):
                            raise ValueError('transfer payload must be an object')
                        if action == 'prepare':
                            binding = outer.resolve_transfer_binding(
                                data.get('bridge_profile_id'), data.get('sender_realm', ''),
                                data.get('sender_save_id', ''), data.get('receiver_realm', ''),
                                data.get('receiver_save_id', ''))
                            tid = outer.transfer_store.prepare_transfer(
                                sender_realm=data.get('sender_realm', ''),
                                sender_save_id=data.get('sender_save_id', ''),
                                receiver_realm=data.get('receiver_realm', ''),
                                receiver_save_id=data.get('receiver_save_id', ''),
                                canonical_item_id=data.get('canonical_item_id', ''),
                                count=data.get('count', 0),
                                idempotency_key=data.get('idempotency_key'), binding=binding,
                            )
                            return self._reply(200, {'protocol_version': 1, 'ok': True, 'transfer_id': tid})
                        tid = data.get('transfer_id')
                        if not isinstance(tid, str) or not tid.strip():
                            return self._reply(400, {'error': 'missing_transfer_id'})
                        if action not in ('lock', 'escrow', 'commit', 'abort', 'refund'):
                            return self._reply(404, {'error': 'unknown_transfer_action'})
                        outer._finalize_transfer(action, tid, data.get('reason', ''))
                        return self._reply(200, {'protocol_version': 1, 'ok': True})
                    except TransferBindingError as ex:
                        return self._reply(409, {'error': 'ownership_or_receipt_unavailable', 'detail': str(ex)})
                    except ValueError as ve:
                        return self._reply(400, {'error': 'invalid_arguments', 'detail': str(ve)})
                    except Exception as ex:
                        return self._reply(409, {'error': 'conflict', 'detail': str(ex)})

                return self._reply(404, {'error': 'not_found'})

            def _reply(self, code, data):
                payload = json.dumps(data).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return 'http://127.0.0.1:' + str(self._server.server_port)

    def heartbeats(self) -> dict:
        """Return only live sessions; do not expose internal monotonic clock values."""
        now = time.monotonic()
        with self._lock:
            result = {}
            for key, entry in self._heartbeats.items():
                if now - entry['received_monotonic'] <= HEARTBEAT_STALE_AFTER:
                    copied = {}
                    for field, value in entry.items():
                        if field == 'received_monotonic':
                            continue
                        copied[field] = dict(value) if isinstance(value, dict) else value
                    result[key] = copied
            return result

    def record_portal_request(self, data: dict) -> dict:
        """Remember a crossing request (bounded, newest last) and hand it to the launcher hook, if any.

        The hook decides (portal state, linked saves, transition mode). A repeated request id is answered with
        the stored state, never started twice. Exceptions in the hook become state HANDLER_ERROR.
        """
        with self._lock:
            for existing in self._portal_requests:
                if existing['request_id'] == data['request_id']:
                    return dict(existing)
            request = {'request_id': data['request_id'], 'realm': data['realm'], 'portal_id': data['portal_id'],
                       'save_id': data['save_id'], 'received_unix': time.time(), 'state': 'RECEIVED'}
            self._portal_requests.append(request)
            del self._portal_requests[:-PORTAL_REQUEST_LIMIT]
        state, detail = 'RECEIVED', None
        if self._portal_handler is not None:
            try:
                outcome = self._portal_handler(dict(request)) or {}
                state = outcome.get('state', 'ACCEPTED') if isinstance(outcome, dict) else 'ACCEPTED'
                detail = outcome.get('detail') if isinstance(outcome, dict) else None
            except Exception as ex:                                   # noqa: BLE001 - the plugin only needs a status
                state, detail = 'HANDLER_ERROR', type(ex).__name__
        if not isinstance(state, str) or re.fullmatch(r'[A-Z_]{1,40}', state) is None:
            state = 'HANDLER_ERROR'
        with self._lock:
            request['state'] = state
            if detail is not None:
                request['detail'] = str(detail)[:200]
            return dict(request)

    # ---- travel mailbox: the launcher drives a crossing, the realm plugins report and obey (at-least-once, acked)
    def travel_enqueue(self, realm: str, message: dict):
        with self._lock:
            queue = self._travel_queue[realm]
            if message['kind'] in ('resume', 'abort'):
                # A way back must never wait behind an instruction the plugin is stuck on.
                queue[:] = [m for m in queue if m['transition_id'] != message['transition_id']]
            if len(queue) >= TRAVEL_QUEUE_LIMIT:
                return None
            entry = dict(message, message_id=secrets.token_hex(8))
            queue.append(entry)
            return entry['message_id']

    def travel_peek(self, realm: str):
        with self._lock:
            return dict(self._travel_queue[realm][0]) if self._travel_queue[realm] else None

    def travel_record(self, data: dict) -> None:
        with self._lock:
            ack = data.get('ack_id')
            if ack:
                queue = self._travel_queue[data['realm']]
                queue[:] = [m for m in queue if m['message_id'] != ack]
            self._travel_seq += 1
            self._travel_events.append({'seq': self._travel_seq, 'realm': data['realm'], 'transition_id': data['transition_id'],
                                        'event': data['event'], 'detail': data.get('detail', ''), 'carry': dict(data.get('carry') or {}),
                                        'received_unix': time.time()})
            del self._travel_events[:-TRAVEL_EVENT_LIMIT]

    def travel_events(self, since: int = 0) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._travel_events if e['seq'] > since]

    def portal_requests(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._portal_requests]

    def realm_token(self, realm: str) -> str:
        if realm not in self._realm_tokens:
            raise ValueError('unknown realm')
        return self._realm_tokens[realm]

    def _live_session(self, realm: str) -> tuple[int, int] | None:
        with self._lock:
            heartbeat = self._heartbeats.get(realm)
            if (heartbeat is None or type(heartbeat.get('pid')) is not int or
                    time.monotonic() - heartbeat['received_monotonic'] > HEARTBEAT_STALE_AFTER):
                return None
            return heartbeat['pid'], self._session_epoch[realm]

    def resolve_transfer_binding(self, profile_id, sender, sender_save, receiver, receiver_save):
        if self.transfer_bindings is None:
            raise TransferBindingError('profile/pair authority unavailable')
        binding = self.transfer_bindings.resolve(profile_id, sender, sender_save, receiver, receiver_save)
        self._validate_live_binding(binding, (sender, receiver))
        return binding

    def validate_transfer(self, row, *, realms=None):
        if self.transfer_bindings is None:
            raise TransferBindingError('profile/pair authority unavailable')
        binding = self.transfer_bindings.validate_row(row)
        self._validate_live_binding(binding, (binding.sender_realm, binding.receiver_realm)
                                    if realms is None else realms)
        return binding

    def _validate_live_binding(self, binding, realms):
        if self.endpoint_provider is None:
            raise TransferBindingError('trusted local endpoint provider unavailable')
        builds = self.transfer_bindings.current_builds()
        for realm in realms:
            session = self._live_session(realm)
            if session is None:
                raise TransferBindingError('live plugin session unavailable')
            expected = {**binding.endpoint(realm), 'build_fingerprint': builds.get(realm),
                        'session': {'pid': session[0], 'session_id': str(session[1])}}
            try:
                report = self.endpoint_provider(realm, session)
                ready, _ = evaluate_world_ready(report, expected=expected, trust=self.endpoint_trust)
                observed = datetime.fromisoformat(report['observed_at'].replace('Z', '+00:00'))
                age = (datetime.now(timezone.utc) - observed).total_seconds()
                if (not ready or report['process_running'] is not True or
                        report['load_callback_returned'] is not True or
                        not 0 <= age <= HEARTBEAT_STALE_AFTER or self._live_session(realm) != session):
                    raise TransferBindingError('current native endpoint is not attested')
            except TransferBindingError:
                raise
            except Exception as exc:
                raise TransferBindingError('local endpoint observation unavailable') from exc

    def _bound_command_row(self, realm, command):
        if self.transfer_store is None:
            raise TransferBindingError('transfer mutation requires its durable ledger')
        tid, separator, suffix = command['op_id'].rpartition(':')
        row = self.transfer_store.get(tid) if separator else None
        binding_for_row(row)
        contracts = {
            'remove': (row.sender_realm, 'remove', row.sender_save_id, ('LOCKED',)),
            'add': (row.receiver_realm, 'add', row.receiver_save_id, ('ESCROWED',)),
            'refund': (row.sender_realm, 'add', row.sender_save_id, ('REFUND_PENDING',)),
            'q-src': (row.sender_realm, 'query', row.sender_save_id, ('PREPARED',)),
            'q-dst': (row.receiver_realm, 'query', row.receiver_save_id, ('PREPARED',)),
        }
        contract = contracts.get(suffix)
        if (contract is None or (realm, command['kind'], command.get('save_id')) != contract[:3]
                or row.status not in contract[3] or command['item'] != row.canonical_item_id
                or command['count'] != row.count):
            raise TransferBindingError('command differs from durable transfer ownership/phase')
        self.validate_transfer(row, realms=(realm,))
        return row

    def _command_authorized(self, realm, command):
        if command['kind'] in ('remove', 'add') or command['op_id'].endswith((':q-src', ':q-dst')):
            self._bound_command_row(realm, command)
        elif command['kind'] == 'lab_give' and not self._allow_lab_give:
            raise TransferBindingError('lab grant is not enabled by the local controller')

    def _current_result(self, op_id):
        result = self._results.get(op_id)
        command = self._commands.get(op_id)
        realm = self._op_realm.get(op_id)
        if result is None or command is None:
            return None
        # Creation receipts were session/provenance-checked on admission and remain
        # historical observations, not authority for a current inventory endpoint.
        if op_id not in self._creation_context:
            session = self._live_session(realm)
            if (session is None or self._issued.get(op_id) != (realm, *session) or
                    result.get('pid') != session[0]):
                return None
            try:
                self._command_authorized(realm, command)
            except Exception:
                return None
        return deepcopy(result)

    def _finalize_transfer(self, action, tid, reason):
        from bridge.transfer.coordinator import _refused, _succeeded
        with self.transfer_store.transfer_guard(tid), self._cmd_cond:
            row = self.transfer_store.get(tid)
            binding_for_row(row)
            self.validate_transfer(row, realms=(row.sender_realm,) if action == 'refund' else None)
            if action == 'lock':
                source = self._current_result(tid + ':q-src')
                destination = self._current_result(tid + ':q-dst')
                if (source is None or destination is None or source.get('ok') is not True or
                        type(source.get('before')) is not int or source['before'] < row.count or
                        destination.get('ok') is not True or destination.get('can_add') is not True):
                    raise TransferBindingError('current bound inventory queries required for lock')
                self.transfer_store.lock(tid)
            elif action == 'abort':
                if row.status != 'PREPARED' and not _refused(self._current_result(tid + ':remove')):
                    raise TransferBindingError('clean sender refusal required for a locked abort')
                self.transfer_store.abort(tid, reason)
            else:
                suffix, delta = {'escrow': ('remove', -row.count), 'commit': ('add', row.count),
                                 'refund': ('refund', row.count)}[action]
                if not _succeeded(self._current_result(tid + ':' + suffix), delta):
                    raise TransferBindingError('current bound native persistence receipt required')
                if action == 'escrow':
                    self.transfer_store.escrow(tid)
                elif action == 'commit':
                    self.transfer_store.commit(tid)
                else:
                    self.transfer_store.refund(tid, reason)

    def bind_transfer_store(self, store: TransferStore) -> bool:
        """Attach the actual coordinator ledger; reopen may replace only the same database."""
        if not store.is_open:
            raise ValueError('cannot bind a closed transfer ledger')
        with self._cmd_cond:
            if (self.transfer_store is not None
                    and self.transfer_store.database_path != store.database_path):
                raise ValueError('command channel is already bound to another transfer ledger')
            self.transfer_store = store
        return True

    def _refund_fenced(self, realm: str, command: dict) -> bool:
        if self.transfer_store is None or command.get('kind') != 'add':
            return False
        op_id = command['op_id']
        if not op_id.endswith(':add'):
            return False
        row = self.transfer_store.get(op_id[:-4])
        return bool(row and row.receiver_realm == realm and row.status in ('REFUND_PENDING', 'REFUNDED'))

    def enqueue_command(self, realm: str, kind: str, item: str, count: int = 1,
                        op_id: str | None = None, save_id: str | None = None) -> str:
        """Queue an inventory command for one realm's plugin; returns its op_id."""
        if realm not in ('SR1', 'SR2') or kind not in COMMAND_KINDS:
            raise ValueError('invalid realm or command kind')
        if item not in SUPPORTED_CANONICAL_ITEMS:
            raise ValueError('unsupported item')
        if type(count) is not int or not 0 <= count <= 30:
            raise ValueError('count out of range')
        op_id = op_id or (kind + '-' + secrets.token_hex(12))
        if not OP_ID_RE.match(op_id):
            raise ValueError('invalid op_id')
        if not isinstance(save_id, str) or not save_id.strip() or len(save_id) > 128:
            raise ValueError('save_id is required for native inventory commands')

        command = {'op_id': op_id, 'kind': kind, 'item': item, 'count': count}
        if save_id is not None:
            command['save_id'] = save_id
        with self._cmd_cond:
            self._command_authorized(realm, command)
            if self._refund_fenced(realm, command):
                raise ValueError('receiver add fenced by durable refund decision')
            known = self._op_realm.get(op_id)
            prior = self._commands.get(op_id)
            if known is not None and (known != realm or prior != command):
                raise ValueError('op_id already bound to another command')
            self._op_realm[op_id] = realm
            self._commands[op_id] = command
            if op_id in self._results and self._current_result(op_id) is None:
                del self._results[op_id]
            if op_id not in self._results and all(c['op_id'] != op_id for c in self._pending[realm]):
                self._pending[realm].append(command)
        return op_id

    def enqueue_creation_command(self, realm: str, kind: str, creation_id: str,
                                 op_id: str | None = None, *, expected_pid: int) -> str:
        """PID-bound native creation transport; SR2 requires an explicit protected lab opt-in."""
        require_not_retired(creation_id)  # retired legacy ids never reach a plugin, even as reconcile
        if (realm not in self.creation_realms or kind not in ('create_save', 'reconcile_save') or
                not isinstance(creation_id, str) or re.fullmatch(r'[0-9a-f]{32}', creation_id) is None or
                type(expected_pid) is not int or not 0 < expected_pid < 2 ** 32):
            raise ValueError('invalid native creation command or process')
        op_id = op_id or creation_id + (':create' if kind == 'create_save' else ':reconcile')
        if not OP_ID_RE.fullmatch(op_id):
            raise ValueError('invalid op_id')
        command = {'op_id': op_id, 'kind': kind, 'creation_id': creation_id}
        context = (command, expected_pid)
        with self._cmd_cond:
            known = self._op_realm.get(op_id)
            prior = self._creation_context.get(op_id)
            if known is not None and (known != realm or prior != context or self._commands.get(op_id) != command):
                raise ValueError('op_id already bound to another command, creation or process')
            self._op_realm[op_id] = realm
            self._commands[op_id] = command
            self._creation_context[op_id] = context
            if kind == 'reconcile_save':
                # Supersede only this creation's pending mutation; retain its audit provenance.
                self._pending[realm] = [candidate for candidate in self._pending[realm]
                                        if not (candidate['kind'] == 'create_save' and
                                                candidate.get('creation_id') == creation_id)]
            if op_id not in self._results and all(c['op_id'] != op_id for c in self._pending[realm]):
                self._pending[realm].append(command)
        return op_id

    def cancel_command(self, realm: str, op_id: str) -> bool:
        """Stop redelivering a command. It may already have been applied: check with the plugin."""
        with self._cmd_cond:
            before = len(self._pending[realm])
            self._pending[realm] = [c for c in self._pending[realm] if c['op_id'] != op_id]
            return len(self._pending[realm]) != before

    def forget_volatile_state(self) -> None:
        """Lab: model a BridgeHost process restart. Drops in-memory mailboxes and received results;
        only the durable ledger survives, exactly as after a real host crash."""
        with self._cmd_cond:
            self._pending = {'SR1': [], 'SR2': []}
            self._results.clear()
            self._op_realm.clear()
            self._commands.clear()
            self._issued.clear()
            self._creation_context.clear()

    def wait_result(self, op_id: str, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        with self._cmd_cond:
            while op_id not in self._results:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cmd_cond.wait(remaining)
            return self._current_result(op_id)

    def __enter__(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self.transfer_store is not None and hasattr(self.transfer_store, 'close'):
            try:
                self.transfer_store.close()
            except Exception:
                pass


def _write_status_file(path: Path, data: dict) -> None:
    """Never write through a pre-existing .tmp hard link or truncate status in place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.slimebridge-status-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    """Development entry point: listen on a fixed loopback port and write the session token to a file.

    The token never goes to stdout. Plugins read SLIMEBRIDGE_URL / SLIMEBRIDGE_TOKEN from their
    environment, which the launcher sets when it starts a lab game.
    """
    import argparse
    import os
    from pathlib import Path

    parser = argparse.ArgumentParser(description='SlimeBridge development BridgeHost (loopback only)')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--token-file', type=Path, required=True, help='Written with the session token')
    parser.add_argument('--status-file', type=Path, help='Rewritten with the heartbeat table every second')
    args = parser.parse_args()
    with BridgeHost(port=args.port) as host:
        args.token_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(args.token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(host.token)
        print('BridgeHost listening on ' + host.url, flush=True)
        try:
            while True:
                if args.status_file is not None:
                    _write_status_file(args.status_file, {'url': host.url, 'heartbeats': host.heartbeats()})
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
