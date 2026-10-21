"""In-memory transport and a minimal client for Cloud tests and demo mode (synthetic).

``Network`` connects ``Client`` objects to one ``BridgeServer`` through deterministic queues with
fault injection (drop, duplicate, hold-and-release reordering, forced disconnect). A real
deployment replaces it with sockets; the server core is unchanged.
"""

import uuid

from bridge.sim.faults import FaultPlan
from .protocol import message, validate


class Client:
    """Tracks session, sequence numbers and resume token; never trusts server messages blindly."""

    def __init__(self, display_name, credential, *, content, realm=None, profile=None,
                 capabilities=('presence', 'chat', 'whisper', 'realm_switch', 'resume', 'transaction_metadata'),
                 versions=(1,), provider='dev-token'):
        self.conn = None
        self.display_name, self.credential, self.provider = display_name, credential, provider
        self.content, self.realm, self.profile = dict(content), realm, profile
        self.capabilities, self.versions = list(capabilities), list(versions)
        self.session_id = self.resume_token = self.player_id = None
        self.seq, self.last_server_seq = 0, 0
        self.inbox, self.rejections, self.welcome = [], [], None

    def _next(self, type_, body, *, idempotency_key=None, realm=None):
        self.seq += 1
        return message(type_, body, seq=self.seq, session_id=self.session_id, player_id=self.player_id,
                       realm=realm, idempotency_key=idempotency_key)

    def hello(self):
        return self._next('HELLO', {'versions': self.versions, 'capabilities': self.capabilities,
                                    'display_name': self.display_name,
                                    'auth': {'provider': self.provider, 'credential': self.credential},
                                    'content': self.content, 'profile': self.profile, 'realm': self.realm})

    def resume(self):
        return message('RESUME', {'session_id': self.session_id, 'resume_token': self.resume_token,
                                  'last_seq_received': self.last_server_seq})

    def chat(self, text, channel='GLOBAL', to=None, key=None, realm=None):
        return self._next('CHAT', {'channel': channel, 'text': text, 'to': to}, idempotency_key=key, realm=realm)

    def switch(self, transition, phase, key=None):
        return self._next('REALM_SWITCH', {'transition': transition, 'phase': phase}, idempotency_key=key)

    def presence(self, status):
        return self._next('PRESENCE', {'status': status})

    def bye(self):
        return self._next('BYE', {'reason': 'goodbye'})

    def ack(self):
        return self._next('ACK', {'seq': self.last_server_seq, 'message_id': uuid.uuid4().hex})

    def deliver(self, msg):
        if validate(msg, direction='server'):
            self.rejections.append({'code': 'CLIENT_DROPPED_MALFORMED'})
            return
        if msg['type'] == 'WELCOME' and msg['body']['session_id'] != self.session_id:
            body = msg['body']  # a new session: its sequence numbers start again
            self.session_id, self.resume_token, self.player_id = body['session_id'], body['resume_token'], body['player_id']
            self.welcome, self.last_server_seq = body, 0
        ours = msg['seq'] > 0 and msg['session_id'] == self.session_id
        if ours and msg['seq'] <= self.last_server_seq:
            return  # duplicate or already-seen replay
        if ours:
            self.last_server_seq = msg['seq']
        if msg['type'] == 'RESUMED':
            self.resume_token = msg['body']['resume_token']
        elif msg['type'] == 'REJECT':
            self.rejections.append(msg['body'])
        elif msg['type'] == 'PLAYER_LIST':
            self.players = msg['body']['players']
            me = next((p for p in self.players if p['player_id'] == self.player_id), None)
            if me is not None:
                self.realm = me['realm']
        self.inbox.append(msg)

    def received(self, type_):
        return [m for m in self.inbox if m['type'] == type_]


class Network:
    def __init__(self, server, faults=()):
        self.server, self.faults = server, FaultPlan(faults)
        self.clients, self.held = {}, []
        self._next_conn = 0

    def join(self, client):
        self._next_conn += 1
        client.conn = f'conn-{self._next_conn}'
        self.clients[client.conn] = client
        self.server.connect(client.conn)
        return client

    def send(self, client, msg):
        if self.faults.check('net.drop_client', type=msg['type']):
            return []
        replies = self.server.receive(client.conn, msg)
        if self.faults.check('net.duplicate_client', type=msg['type']):
            replies = replies + self.server.receive(client.conn, msg)
        self._route(replies)
        return replies

    def _route(self, replies):
        for conn, msg in replies:
            client = self.clients.get(conn)
            if client is None or self.faults.check('net.drop_server', type=msg['type']):
                continue
            if self.faults.check('net.hold_server', type=msg['type']):
                self.held.append((conn, msg))
                continue
            client.deliver(msg)
            if self.faults.check('net.duplicate_server', type=msg['type']):
                client.deliver(msg)

    def release(self):
        held, self.held = self.held, []
        for conn, msg in held:
            if conn in self.clients:
                self.clients[conn].deliver(msg)

    def drop(self, client):
        """The client's connection dies; it may later reconnect on a new connection id."""
        self.clients.pop(client.conn, None)
        self._route(self.server.disconnect(client.conn))
        client.conn = None

    def reconnect(self, client):
        self.join(client)
        return self.send(client, client.resume())

    def tick(self):
        self._route(self.server.tick())

    def announce(self, text):
        self._route(self.server.announce(text))
