"""Travelling between the two running games: the launcher drives, BridgeHost relays, the realm plugins obey and report.

Both games stay open ("warm"): the destination comes to the front and plays the portal arrival, the source plays its departure
animation, is throttled (few FPS, lowest quality, muted, frozen) and minimized. When nothing is running yet the launcher starts
the destination on the right save. Every step has a deadline; a step that fails rolls the source back (resume + window).

States of one crossing: PREPARING (source animation + destination loading, in parallel) -> SWITCHING -> HOLDING -> COMPLETE, or ROLLED_BACK / FAILED.
It is a pragmatic coordinator, not the durable attested state machine of ``bridge.transition`` (which stays available for later).
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DESTINATIONS = {'portal.sr1.sanctuary_gate': 'SR2', 'portal.sr2.return_portal': 'SR1'}
ACTIVE = ('NEEDS_LINK', 'PREPARING', 'SWITCHING', 'HOLDING')
DEADLINES = {'NEEDS_LINK': 240.0, 'PREPARING_WARM': 45.0, 'PREPARING_COLD': 420.0, 'SWITCHING': 30.0, 'HOLDING': 15.0}
KEEP = 20
NEW_SAVE = '__NEW__'
NEW_NAME = re.compile(r'[^A-Za-z0-9 _-]+')
SAVE_MODES = ('LINKED', 'CONTINUE')


def bridge_env(bridge_settings, realm):
    """Game environment that carries the player's Bridge options into a game started by the launcher."""
    if realm != 'SR2':
        return {}
    cost = int(bridge_settings.get('sr2_portal_cost', 250000)) if bridge_settings.get('sr2_portal_gate_enabled', True) else 0
    env = {'SLIMEBRIDGE_SR2_GATE_COST': str(max(0, cost))}
    if not bridge_settings.get('sr2_portal_enabled', True):
        env['SLIMEBRIDGE_SR2_PORTAL'] = '0'
    return env


class TravelLinks:
    """Which SR1 save goes with which SR2 save (remembered from what is loaded when a crossing happens)."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def lookup(self, realm, save_id):
        return self._load().get(f'{realm}:{save_id}')

    def unlink(self, realm, save_id):
        data = self._load()
        other = data.pop(f'{realm}:{save_id}', None)
        if other is not None:
            data.pop(f"{'SR2' if realm == 'SR1' else 'SR1'}:{other}", None)
            self._write(data)
        return other

    def all(self):
        return {key: value for key, value in self._load().items() if key.startswith('SR1:')}

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding='utf-8')
        temp.replace(self.path)

    def remember(self, sr1_save, sr2_save):
        if not sr1_save or not sr2_save:
            return
        data = self._load()
        # One ranch per world: replacing a link also frees the partner it used to point at.
        for realm, save in (('SR1', sr1_save), ('SR2', sr2_save)):
            old = data.pop(f'{realm}:{save}', None)
            if old is not None:
                data.pop(f"{'SR2' if realm == 'SR1' else 'SR1'}:{old}", None)
        data[f'SR1:{sr1_save}'] = sr2_save
        data[f'SR2:{sr2_save}'] = sr1_save
        self._write(data)


class TravelCoordinator:
    def __init__(self, host, launcher, install_for, backup_acknowledged, windows, links, *, settings=None, opener=urlopen,
                 clock=time.time, list_saves=None, notify=None):
        """``settings()`` -> dict with ``travel_save_mode`` and ``background_policy`` (optional)."""
        self._host, self._launcher, self._install_for = host, launcher, install_for
        self._acknowledged, self._windows, self._links = backup_acknowledged, windows, links
        self._settings = settings or (lambda: {})
        self._opener, self._clock = opener, clock
        self._handled, self._since = set(), 0
        self._events = {}
        self._list_saves_fn = list_saves or (lambda realm: [])
        self._notify_fn = notify
        self.transitions = []

    # ---- host relay -------------------------------------------------------------------------------------------------
    def _call(self, method, path, body=None):
        status = self._host.state()
        url, token = status.get('url') or '', self._host.admin_token()
        if status.get('state') != 'RUNNING' or not token or urlsplit(url).hostname not in ('127.0.0.1', 'localhost'):
            return None
        headers = {'Authorization': 'Bearer ' + token}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self._opener(Request(url.rstrip('/') + path, data, headers, method=method), timeout=2) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            return None

    def _send(self, realm, kind, transition, **extra):
        message = dict({'kind': kind, 'transition_id': transition['id']}, **extra)
        reply = self._call('POST', '/v1/travel/send', {'realm': realm, 'message': message})
        return bool(reply and reply.get('ok'))

    def _drain_events(self):
        reply = self._call('GET', f'/v1/travel/events?since={self._since}')
        for event in (reply or {}).get('events', []):
            self._since = max(self._since, event['seq'])
            self._events.setdefault(event['transition_id'], []).append(event)
            for transition in self.transitions:
                if transition['id'] == event['transition_id']:
                    self._log(transition, f"{event['realm']} {event['event']}" + (f": {event['detail']}" if event.get('detail') else ''))

    def _latest_carry(self, transition, realm, event):
        for e in reversed(self._events.get(transition['id'], [])):
            if e['realm'] == realm and e['event'] == event:
                return dict(e.get('carry') or {})
        return {}

    def _saw(self, transition, realm, event):
        return any(e['realm'] == realm and e['event'] == event for e in self._events.get(transition['id'], []))

    def _list_saves(self, realm):
        try:
            return [dict(entry) for entry in self._list_saves_fn(realm)]
        except Exception:                                              # noqa: BLE001 - an unreadable folder just means an empty list
            return []

    def _notify(self, transition):
        if self._notify_fn is not None:
            try:
                self._notify_fn(self.public(transition))
            except Exception:                                          # noqa: BLE001
                pass

    # ---- helpers ----------------------------------------------------------------------------------------------------
    def _heartbeat(self, realm):
        return (self._host.state().get('heartbeats') or {}).get(realm) or {}

    def _world_ready(self, realm):
        beat = self._heartbeat(realm)
        status = beat.get('status') or {}
        return bool(status.get('player_alive') and status.get('save_id'))

    def _log(self, transition, text):
        transition['history'].append({'at': self._clock(), 'text': text})
        del transition['history'][:-30]

    def _enter(self, transition, state, text=None):
        transition['state'] = state
        transition['since'] = self._clock()
        if text:
            self._log(transition, text)

    def _destination_env(self, transition):
        realm_env = getattr(self._host, 'realm_env', None)
        env = dict(realm_env(transition['to']) if realm_env else self._host.env())
        env.update(bridge_env(self._settings(), transition['to']))
        save = transition.get('dest_save')
        if save:
            env['SLIMEBRIDGE_TRAVEL_SAVE'] = save
        if transition.get('create_new') and not save:
            env['SLIMEBRIDGE_TRAVEL_NEW'] = transition['create_new']
        env['SLIMEBRIDGE_TRAVEL_MODE'] = 'DESTINATION'
        return env

    def _remember_pair(self, transition):
        source = (self._heartbeat(transition['from']).get('status') or {}).get('save_id')
        dest = (self._heartbeat(transition['to']).get('status') or {}).get('save_id')
        sr1, sr2 = (source, dest) if transition['from'] == 'SR1' else (dest, source)
        self._links.remember(sr1, sr2)

    def _pid(self, realm):
        return self._heartbeat(realm).get('pid')

    def _window(self, transition, action, realm):
        pid = self._pid(realm)
        if not pid:
            return
        try:
            getattr(self._windows, action)(pid)
        except Exception as exc:                                      # noqa: BLE001 - a window problem never aborts a crossing
            self._log(transition, f'window {action} {realm}: {exc}')

    # ---- state machine ----------------------------------------------------------------------------------------------
    def _begin(self, request):
        portal = request.get('portal_id')
        destination = DESTINATIONS.get(portal)
        transition = {'id': uuid.uuid4().hex[:16], 'request_id': request['request_id'], 'portal_id': portal, 'from': request.get('realm'),
                      'to': destination, 'source_save': request.get('save_id'), 'state': 'PREPARING', 'since': self._clock(),
                      'started': self._clock(), 'detail': None, 'history': [], 'launched': False, 'deadline': DEADLINES['PREPARING_WARM'],
                      'dest_save': None, 'candidates': []}
        self.transitions.append(transition)
        del self.transitions[:-KEEP]
        if destination is None or destination == transition['from']:
            return self._finish(transition, 'FAILED', 'UNKNOWN_PORTAL')
        if any(t['state'] in ACTIVE and t is not transition for t in self.transitions):
            return self._finish(transition, 'FAILED', 'ANOTHER_CROSSING_ACTIVE')
        mode = (self._settings().get('travel_save_mode') or 'LINKED').upper()
        running = self._launcher.running(destination)
        running_save = (self._heartbeat(destination).get('status') or {}).get('save_id') if running else None
        linked = self._links.lookup(transition['from'], transition['source_save'] or '') if mode == 'LINKED' else None
        if mode == 'LINKED':
            if linked and running and running_save and running_save != linked:
                # Never switch a running game to another save behind the player's back.
                transition['detail'] = {'running': running_save, 'linked': linked}
                return self._finish(transition, 'FAILED', 'DESTINATION_WRONG_SAVE', transition['detail'])
            if linked and not running and linked not in {c['group'] for c in self._list_saves(destination)}:
                self._links.unlink(transition['from'], transition['source_save'] or '')
                self._log(transition, f'the linked {destination} save {linked} no longer exists: link dropped')
                linked = None
            if not linked:
                return self._ask_for_link(transition, running_save)
        transition['dest_save'] = linked
        return self._start_destination(transition)

    def _ask_for_link(self, transition, running_save):
        dest = transition['to']
        saves = self._list_saves(dest)
        used = {key.split(':', 1)[1] if dest == 'SR1' else value for key, value in self._links.all().items()}
        for entry in saves:
            entry['linked'] = entry['group'] in used
            entry['open'] = entry['group'] == running_save
        if running_save and not any(entry['group'] == running_save for entry in saves):
            saves.insert(0, {'group': running_save, 'label': running_save, 'modified': None, 'linked': running_save in used, 'open': True})
        if dest == 'SR1' and running_save is None:
            saves.append({'group': NEW_SAVE, 'label': '', 'modified': None, 'linked': False, 'open': False, 'new': True})
        transition['candidates'] = saves
        transition['state'] = 'NEEDS_LINK'
        transition['since'] = self._clock()
        self._log(transition, f'no {dest} save is linked to this one yet: waiting for the player to choose')
        self._notify(transition)
        return transition

    def choose_link(self, transition_id, save_group):
        """The player picked the destination save (or ``None`` to cancel the crossing)."""
        transition = next((t for t in self.transitions if t['id'] == transition_id), None)
        if transition is None or transition['state'] != 'NEEDS_LINK':
            return {'ok': False, 'error': 'NOT_WAITING'}
        if save_group is None:
            self._finish(transition, 'CANCELLED', 'PLAYER_CANCELLED')
            return {'ok': True}
        if save_group not in {c['group'] for c in transition['candidates']}:
            return {'ok': False, 'error': 'UNKNOWN_SAVE'}
        if save_group == NEW_SAVE:
            # A fresh save in the destination: the game creates it itself, the link is recorded once it reports the new name.
            transition['create_new'] = NEW_NAME.sub('', f"SlimeBridge {transition['source_save'] or ''}"[:48]).strip() or 'SlimeBridge'
            transition['dest_save'] = None
            self._log(transition, f"a new {transition['to']} save will be created: {transition['create_new']}")
        else:
            sr1, sr2 = ((transition['source_save'], save_group) if transition['from'] == 'SR1' else (save_group, transition['source_save']))
            self._links.remember(sr1, sr2)
            transition['dest_save'] = save_group
            self._log(transition, f"linked {transition['from']} {transition['source_save']} <-> {transition['to']} {save_group}")
        transition['state'], transition['since'] = 'PREPARING', self._clock()
        self._start_destination(transition)
        return {'ok': True}

    def _start_destination(self, transition):
        destination, installation = transition['to'], self._install_for(transition['to'])
        if self._launcher.running(destination):
            self._log(transition, f'{destination} is already running')
        elif not installation:
            return self._finish(transition, 'FAILED', 'DESTINATION_UNAVAILABLE')
        elif not self._acknowledged():
            return self._finish(transition, 'FAILED', 'NEEDS_BACKUP_CONFIRMATION')
        else:
            try:
                self._launcher.launch(installation, extra_env=self._destination_env(transition))
                transition['launched'] = True
                transition['deadline'] = DEADLINES['PREPARING_COLD']
                self._log(transition, f'starting {destination}')
            except Exception as exc:                                  # noqa: BLE001
                return self._finish(transition, 'FAILED', 'LAUNCH_FAILED', type(exc).__name__)
        # The source starts its portal animation at once and waits, covered, while the destination loads.
        self._send(transition['from'], 'prepare_departure', transition, destination=destination, portal_id=transition['portal_id'],
                   carry_mode=self._carry_mode())
        return transition

    def _carry_mode(self):
        mode = (self._settings().get('travel_carry') or 'ALL').upper()
        return mode if mode in ('ALL', 'SLIMES_PLORTS', 'NONE') else 'ALL'

    def _finish(self, transition, state, code, detail=None):
        transition['state'], transition['detail'], transition['code'] = state, detail, code
        transition['since'] = self._clock()
        self._log(transition, f'{state}: {code}')
        return transition

    def _rollback(self, transition, code):
        source = transition['from']
        self._send(source, 'resume', transition, reason=code[:60])
        self._window(transition, 'restore', source)
        self._window(transition, 'focus', source)
        self._finish(transition, 'ROLLED_BACK', code)

    def _advance(self, transition):
        state, now = transition['state'], self._clock()
        src, dst = transition['from'], transition['to']
        if state == 'NEEDS_LINK':
            if now - transition['since'] > DEADLINES['NEEDS_LINK']:
                return self._finish(transition, 'FAILED', 'LINK_TIMEOUT')
            return
        if now - transition['since'] > (transition['deadline'] if state == 'PREPARING' else DEADLINES[state]):
            return self._rollback(transition, f'TIMEOUT_{state}')
        if state == 'PREPARING':
            if self._saw(transition, src, 'failed'):
                return self._rollback(transition, 'SOURCE_FAILED')
            if transition['launched'] and not transition.get('parked') and self._pid(dst):
                # A freshly started game opens in front: put the player back on the source until the destination is ready.
                transition['parked'] = True
                self._window(transition, 'minimize', dst)
                self._window(transition, 'focus', src)
            if transition.get('create_new') and not transition.get('dest_save') and self._world_ready(dst):
                created = (self._heartbeat(dst).get('status') or {}).get('save_id')
                if created:
                    transition['dest_save'] = created
                    sr1, sr2 = ((transition['source_save'], created) if src == 'SR1' else (created, transition['source_save']))
                    self._links.remember(sr1, sr2)
                    self._log(transition, f'linked {src} {transition["source_save"]} <-> {dst} {created} (new save)')
            if self._saw(transition, src, 'departure_ready') and self._world_ready(dst) and self._launcher.running(dst):
                self._window(transition, 'restore', dst)
                self._window(transition, 'focus', dst)
                offer = self._latest_carry(transition, src, 'departure_ready') if self._carry_mode() != 'NONE' else {}
                transition['carry_offered'] = offer
                extra = {'carry': offer} if offer else {}
                self._send(dst, 'arrive', transition, source=src, portal_id=transition['portal_id'], **extra)
                self._enter(transition, 'SWITCHING', 'both sides ready: arrival requested')
        elif state == 'SWITCHING':
            if self._saw(transition, dst, 'failed'):
                return self._rollback(transition, 'DESTINATION_FAILED')
            if self._saw(transition, dst, 'arrived'):
                policy = (self._settings().get('background_policy') or 'FREEZE').upper()
                accepted = self._latest_carry(transition, dst, 'arrived')
                transition['carry_moved'] = accepted
                extra = {'carry': accepted} if accepted else {}
                self._send(src, 'hold', transition, policy=policy[:20], **extra)
                if accepted:
                    self._log(transition, 'carried through the portal: ' + ', '.join(f'{k} x{v}' for k, v in sorted(accepted.items())))
                self._window(transition, 'minimize', src)
                self._enter(transition, 'HOLDING', 'player arrived: the other game is being throttled')
        elif state == 'HOLDING':
            if self._saw(transition, src, 'held'):
                self._finish(transition, 'COMPLETE', 'ARRIVED')

    def poll(self):
        status = self._host.state()
        if status.get('state') != 'RUNNING':
            return [self.public(t) for t in self.transitions]
        reply = self._call('GET', '/v1/portal/requests') or {}
        for request in reply.get('requests', []):
            rid = request.get('request_id')
            if rid and rid not in self._handled:
                self._handled.add(rid)
                self._begin(request)
        self._drain_events()
        for transition in list(self.transitions):
            if transition['state'] in ACTIVE:
                self._advance(transition)
        return [self.public(t) for t in self.transitions]

    def public(self, transition):
        keys = ('id', 'request_id', 'portal_id', 'from', 'to', 'state', 'code', 'detail', 'started', 'since', 'launched', 'source_save', 'dest_save', 'candidates')
        return {**{k: transition.get(k) for k in keys}, 'history': list(transition['history'][-14:])}
