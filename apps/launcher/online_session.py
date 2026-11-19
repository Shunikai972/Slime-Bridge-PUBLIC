"""One online session for both games, with one code the players share (launcher, Slime Rancher and Slime Rancher 2).

The code is Ranching Together's join code (9 Base58 characters: the host's public address and port, made by its Auto
Host). SlimeBridge's SRMP build opens its Slime Rancher lobby under that same code, so a friend types the one code in
either game or in the launcher. When only Slime Rancher is online, SRMP's own 7-character code is the code.

Hosting both games: Slime Rancher 2 hosts first (the code exists only then), then Slime Rancher hosts with it. Each plugin
reports its online mod in every heartbeat (``online``: mod, signed_in, hosting, connected, code, error), so the code the
launcher shows is the one the games show, whoever started the session (the launcher or the game's own menu).
"""
from __future__ import annotations

import re
import uuid

BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
SESSION_CODE = re.compile(f'\\A[{BASE58}]{{9}}\\Z')
SR1_CODE = re.compile(r'\A[A-Z0-9]{7}\Z')
NAME = re.compile(r'\A[A-Za-z_][A-Za-z0-9_]{0,23}\Z')     # SRMP's own rule for a player name
MODES = ('BOTH', 'SR1', 'SR2')
DEADLINE = 420.0                     # starting a game, loading the save and Ranching Together's UPnP all fit in it


def clean(code):
    return ''.join(c for c in str(code or '') if c.isascii() and c.isalnum())[:16]


def realms_for_code(code):
    """Which games a code joins: a session code joins both, a 7-character SRMP code only Slime Rancher."""
    if SESSION_CODE.fullmatch(code):
        return ('SR1', 'SR2')
    if SR1_CODE.fullmatch(code.upper()):
        return ('SR1',)
    return ()


class OnlineSession:
    def __init__(self, send, heartbeat, running, world_ready, events, clock, *, cancel=None):
        """``send(realm, kind, record, **extra)`` relays an instruction; ``events(record_id)`` lists the plugins' reports."""
        self._send, self._heartbeat, self._running, self._world_ready = send, heartbeat, running, world_ready
        self._events, self._clock = events, clock
        self._cancel = cancel or (lambda record: True)
        self.request = None

    # ---- what the games say --------------------------------------------------------------------------------------
    def online(self, realm):
        value = self._heartbeat(realm).get('online') if self._running(realm) else None
        return dict(value) if isinstance(value, dict) else None

    def code(self):
        """The code to share right now: Slime Rancher 2's session code, else Slime Rancher's lobby code."""
        sr2, sr1 = self.online('SR2') or {}, self.online('SR1') or {}
        if sr2.get('hosting') and SESSION_CODE.fullmatch(sr2.get('code') or ''):
            return sr2['code']
        if (sr1.get('hosting') or sr1.get('connected')) and (sr1.get('code') or ''):
            return sr1['code']
        return ''

    def state(self):
        request = None
        if self.request is not None:
            request = {k: self.request.get(k) for k in ('kind', 'mode', 'code', 'step', 'error', 'done')}
        return {'code': self.code(), 'games': {realm: self.online(realm) for realm in ('SR1', 'SR2')}, 'request': request}

    # ---- what the player asked -------------------------------------------------------------------------------------
    @staticmethod
    def _name(name):
        if not isinstance(name, str):
            raise ValueError('BAD_NAME')
        name = (name or '').strip()
        if name and not NAME.fullmatch(name):
            raise ValueError('BAD_NAME')
        return name

    def _new(self, kind, mode, realms, code='', name=''):
        self.request = {'id': uuid.uuid4().hex[:16], 'kind': kind, 'mode': mode, 'realms': list(realms), 'code': code, 'name': name,
                        'since': self._clock(), 'sent': [], 'step': 'waiting_games', 'error': None, 'done': False, 'history': []}
        return dict(self.state()['request'])

    def host(self, mode, name=''):
        """``name``: the player's own online name for Slime Rancher (SRMP); empty keeps the one chosen in SRMP's menu."""
        if mode not in MODES:
            raise ValueError('mode must be BOTH, SR1 or SR2')
        name = self._name(name)
        realms = ('SR2', 'SR1') if mode == 'BOTH' else (mode,)
        return self._new('host', mode, realms, name=name)

    def join(self, code, name=''):
        code = clean(code)
        realms = realms_for_code(code)
        if not realms:
            raise ValueError('BAD_CODE')
        if realms != ('SR1',):
            raise ValueError('SR2_JOIN_IN_GAME')
        return self._new('join', 'SR1', realms, code.upper(), name=self._name(name))

    # ---- driven by the launcher's travel loop ----------------------------------------------------------------------
    def _fail(self, error):
        self.request.update(error=error, done=True, step='failed', cancelled=self._cancel(self.request))

    def cancel_for_close(self, realm):
        r = self.request
        if r and not r['done'] and realm in r['realms']:
            self._fail('GAME_CLOSING')

    def pending_in(self, realms):
        r = self.request
        return bool(r and (not r['done'] or r.get('cancelled') is False) and set(realms).intersection(r['realms']))

    def _reported_failure(self, realm):
        for event in self._events(self.request['id']):
            if event.get('realm') == realm and event.get('event') == 'failed':
                return event.get('detail') or 'failed'
        return None

    def _ask(self, realm, kind, **extra):
        if realm in self.request['sent']:
            return
        if self._send(realm, kind, self.request, **extra):
            self.request['sent'].append(realm)

    def tick(self):
        r = self.request
        if r is None:
            return
        if r['done']:
            if r.get('cancelled') is False:
                r['cancelled'] = self._cancel(r)
            return
        if self._clock() - r['since'] > DEADLINE:
            return self._fail('TIMEOUT_' + r['step'].upper())
        for realm in r['sent']:
            if not self._running(realm):
                return self._fail(f'{realm}_EXITED')
            failure = self._reported_failure(realm)
            if failure:
                return self._fail(f'{realm}: {failure}')
        if r['kind'] == 'host':
            self._tick_host(r)
        else:
            self._tick_join(r)

    def _ready_to_host(self, realm):
        online = self.online(realm)
        # Slime Rancher's plugin signs in to Epic itself when asked (SRMP needs a username first): only Slime Rancher 2 must
        # already have Ranching Together's menu up.
        ready_mod = online and online.get('mod') and (realm == 'SR1' or online.get('signed_in'))
        return bool(ready_mod and self._world_ready(realm))

    def _tick_host(self, r):
        if 'SR2' in r['realms']:
            sr2 = self.online('SR2') or {}
            ready = sr2.get('hosting') and SESSION_CODE.fullmatch(sr2.get('code') or '')
            if not ready or ('SR2' in r['sent'] and not any(
                    e.get('realm') == 'SR2' and e.get('event') == 'online_done' for e in self._events(r['id']))):
                if 'SR2' not in r['sent'] and not sr2.get('hosting'):
                    if not self._ready_to_host('SR2'):
                        r['step'] = 'waiting_games'
                        return
                    self._ask('SR2', 'online_host')
                r['step'] = 'sr2_auto_host'
                return
            r['code'] = sr2['code']
        if 'SR1' in r['realms']:
            sr1 = self.online('SR1') or {}
            if sr1.get('hosting') and sr1.get('code'):
                if r['code'] and sr1['code'] != r['code']:
                    return self._fail('SR1_OTHER_LOBBY')
                r['code'] = r['code'] or sr1['code']
            else:
                if 'SR1' not in r['sent']:
                    if not self._ready_to_host('SR1'):
                        r['step'] = 'waiting_games'
                        return
                    self._ask('SR1', 'online_host', code=r['code'], **({'name': r['name']} if r.get('name') else {}))
                r['step'] = 'sr1_lobby'
                return
        r.update(step='hosting', done=True)

    def _tick_join(self, r):
        online = self.online('SR1') or {}
        if online.get('connected') or online.get('hosting'):
            if online.get('code') != r['code']:
                return self._fail('SR1_OTHER_LOBBY')
            r.update(step='lobby_joined', done=True)
            return
        if online.get('mod'):                       # the plugin signs in to Epic first when needed
            self._ask('SR1', 'online_join', code=r['code'], **({'name': r['name']} if r.get('name') else {}))
        r['step'] = 'joining' if r['sent'] else 'waiting_games'
