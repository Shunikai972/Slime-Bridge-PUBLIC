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
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from apps.launcher.online_session import OnlineSession

DESTINATIONS = {'portal.sr1.sanctuary_gate': 'SR2', 'portal.sr2.return_portal': 'SR1'}
ACTIVE = ('NEEDS_LINK', 'PREPARING', 'SWITCHING', 'HOLDING')
DEADLINES = {'NEEDS_LINK': 240.0, 'PREPARING_WARM': 45.0, 'PREPARING_COLD': 420.0, 'SWITCHING': 30.0, 'HOLDING': 15.0}
KEEP = 20
CLOSE_HOST_LOST_GRACE = 30.0     # seconds a save-and-close stays guarded after BridgeHost was lost
NEW_SAVE = '__NEW__'
NEW_NAME = re.compile(r'[^A-Za-z0-9 _-]+')
SAVE_MODES = ('LINKED', 'CONTINUE')
# AUTO: a save with no partner gets one without asking (the open save of the other game, else a new save made for it);
# ASK: the launcher asks the player which save to link (the old dialog)
LINK_MODES = ('AUTO', 'ASK')


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


class LastSession:
    """Where the player was last (game + save), so the launcher can offer "Continue where you were"."""

    def __init__(self, path):
        self.path = Path(path)

    def get(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get('realm') not in ('SR1', 'SR2'):
            return None
        return {'realm': data['realm'], 'save': data.get('save') if isinstance(data.get('save'), str) else None, 'at': data.get('at')}

    def set(self, realm, save, at=None):
        if realm not in ('SR1', 'SR2'):
            return
        current = self.get()
        if current and current['realm'] == realm and current['save'] == (save or current['save']):
            return                                                    # unchanged: no disk write every tick
        data = {'realm': realm, 'save': save or (current['save'] if current and current['realm'] == realm else None), 'at': at or time.time()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(data), encoding='utf-8')
        temp.replace(self.path)


class TravelCoordinator:
    def __init__(self, host, launcher, install_for, backup_acknowledged, windows, links, *, settings=None, opener=urlopen,
                 clock=time.time, list_saves=None, notify=None, session=None, prepare_destination=None,
                 select_audio_front=None):
        """``settings()`` -> dict with ``travel_save_mode`` and ``background_policy`` (optional)."""
        self._host, self._launcher, self._install_for = host, launcher, install_for
        self._acknowledged, self._windows, self._links = backup_acknowledged, windows, links
        self._settings = settings or (lambda: {})
        self._opener, self._clock = opener, clock
        self._handled, self._since = set(), 0
        self._events = {}
        self._list_saves_fn = list_saves or (lambda realm: [])
        self._notify_fn = notify
        self._lock = threading.RLock()                 # polled by a background thread and by the window
        self._session = session
        self._prepare_destination = prepare_destination
        self._select_audio_front = select_audio_front or (lambda realm: None)
        self.transitions = []
        self._close = {}
        # The two games are one session: the one the player is in (front) and the one waiting at its portal (parked).
        self._front = self._parked = None
        self._parked_identity = None
        self._parked_child = None
        # A game the launcher started in the background (behind the one being played), still loading its save.
        self._waiting = {}
        # Both games started with the front one not being the game played last: its vac must come over (no teleport).
        self._pending_sync = None
        # Play with both games: start the other game once the player opened a save in the chosen one.
        self._follow = None
        # Online play with one shared code (hosting / joining both games' online mods).
        self.online = OnlineSession(self._send, self._heartbeat, self._launcher.running, self._world_ready,
                                    lambda record_id: self._events.get(record_id, []), self._clock, cancel=self._cancel)

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

    def _cancel(self, transition):
        reply = self._call('POST', '/v1/travel/cancel', {'transition_id': transition['id']})
        return bool(reply and reply.get('ok'))

    def _drain_events(self):
        reply = self._call('GET', f'/v1/travel/events?since={self._since}')
        for event in (reply or {}).get('events', []):
            self._since = max(self._since, event['seq'])
            if not event.get('ack_id'):
                continue
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
        beat = (self._host.state().get('heartbeats') or {}).get(realm) or {}
        current_pid = getattr(self._launcher, 'current_pid', None)
        if current_pid is not None:
            pid = current_pid(realm)
            if type(pid) is not int or pid != beat.get('pid'):
                return {}
        return beat

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

    def _identity(self, realm):
        beat = self._heartbeat(realm)
        pid, save = beat.get('pid'), (beat.get('status') or {}).get('save_id')
        return (pid, save) if type(pid) is int and pid > 0 and save else None

    def _window(self, transition, action, realm):
        # A native window exists before the plugin sends its first heartbeat. The backend still verifies the launch image.
        pid = self._pid(realm) or (transition.get('destination_pid') if realm == transition['to'] else None)
        if not pid:
            return False
        try:
            return getattr(self._windows, action)(pid) is not False
        except Exception as exc:                                      # noqa: BLE001 - report and let the state machine retry
            self._log(transition, f'window {action} {realm}: {exc}')
            return False

    # ---- state machine ----------------------------------------------------------------------------------------------
    def _game_closing(self, transition):
        closing = getattr(self._launcher, 'closing', None)
        return any(self.session_closing(realm) or (closing is not None and closing(realm))
                   for realm in (transition['from'], transition['to']))

    def session_closing(self, realm):
        return realm in self._close

    def session_close_unconfirmed(self, realm):
        return (self._close.get(realm) or {}).get('code') == 'SESSION_CLOSE_HOST_LOST'

    def _append(self, row):
        self.transitions.append(row)
        while len(self.transitions) > KEEP:
            outdated = next((i for i, t in enumerate(self.transitions[:-1])
                             if t['state'] not in ACTIVE and t['state'] != 'CLOSING_SESSION'), None)
            if outdated is None:
                break
            del self.transitions[outdated]

    def _begin(self, request):
        portal = request.get('portal_id')
        destination = DESTINATIONS.get(portal)
        transition = {'id': uuid.uuid4().hex[:16], 'request_id': request['request_id'], 'portal_id': portal, 'from': request.get('realm'),
                      'to': destination, 'source_save': request.get('save_id'), 'state': 'PREPARING', 'since': self._clock(),
                      'started': self._clock(), 'detail': None, 'history': [], 'launched': False, 'deadline': DEADLINES['PREPARING_WARM'],
                      'dest_save': None, 'candidates': []}
        self._append(transition)
        if destination is None or destination == transition['from']:
            return self._refuse(transition, 'UNKNOWN_PORTAL')
        # Stop and poll use this coordinator's lock: queued portal requests cannot pass
        # a previously accepted close intent while that retained child is still alive.
        if self._game_closing(transition):
            return self._refuse(transition, 'GAME_CLOSING')
        if any(t['state'] in ACTIVE and t is not transition for t in self.transitions):
            return self._refuse(transition, 'ANOTHER_CROSSING_ACTIVE')
        if self.online.pending_in((transition['from'], destination)):
            return self._refuse(transition, 'ONLINE_REQUEST_PENDING')
        mode = (self._settings().get('travel_save_mode') or 'LINKED').upper()
        running = self._launcher.running(destination)
        running_save = (self._heartbeat(destination).get('status') or {}).get('save_id') if running else None
        if running and not running_save and self._loading_behind(destination) is None:
            # Open on its menu (started by hand, or for online play): the player would wait forever.
            if self._restart_at_menu(transition, destination):
                running = False
        linked = self._links.lookup(transition['from'], transition['source_save'] or '') if mode == 'LINKED' else None
        if mode == 'LINKED':
            if linked and running and running_save and running_save != linked:
                # Never switch a running game to another save behind the player's back.
                transition['detail'] = {'running': running_save, 'linked': linked}
                return self._refuse(transition, 'DESTINATION_WRONG_SAVE', transition['detail'])
            # The launcher lists a bounded subset of saves. Only the native loader can
            # resolve a linked save, and an unsuccessful load must not drop the link.
            if not linked:
                if (self._settings().get('travel_link_mode') or 'AUTO').upper() == 'ASK':
                    return self._ask_for_link(transition, running_save)
                return self._auto_link(transition, running_save)
        transition['dest_save'] = linked
        return self._start_destination(transition)

    def _auto_link(self, transition, running_save):
        """No partner yet: the other game's open save if it runs one, else a new save made for this ranch. Never asks."""
        source = transition['source_save'] or ''
        used = {key.split(':', 1)[1] for key in self._links.all()} if transition['to'] == 'SR1' else set(self._links.all().values())
        if running_save and running_save not in used:
            sr1, sr2 = (source, running_save) if transition['from'] == 'SR1' else (running_save, source)
            self._links.remember(sr1, sr2)
            transition['dest_save'] = running_save
            self._log(transition, f"linked {transition['from']} {source} <-> {transition['to']} {running_save} (the save open over there)")
            return self._start_destination(transition)
        loading = self._loading_behind(transition['to'])
        if loading is not None and not loading['save'] and not running_save:
            # started behind the portal to make this ranch's new save: link it as soon as it is open
            transition['create_new'] = transition.get('create_new') or 'SlimeBridge'
            transition['dest_save'] = None
            return self._start_destination(transition)
        if self._launcher.running(transition['to']):
            # it runs another ranch's save: never switch it behind the player's back
            return self._refuse(transition, 'DESTINATION_BUSY', {'running': running_save})
        transition['create_new'] = NEW_NAME.sub('', f"SlimeBridge {source}"[:48]).strip() or 'SlimeBridge'
        transition['dest_save'] = None
        self._log(transition, f"no {transition['to']} save for this ranch yet: a new one is created ({transition['create_new']})")
        return self._start_destination(transition)

    def _refuse(self, transition, code, detail=None):
        """A crossing that cannot start: the player, still in the source game, is told why (instead of nothing happening)."""
        if (transition.get('from') in ('SR1', 'SR2') and not self.session_closing(transition['from'])
                and code != 'ONLINE_REQUEST_PENDING'):
            self._send(transition['from'], 'resume', transition, reason=code[:60])
        return self._finish(transition, 'FAILED', code, detail)

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
        with self._lock:
            return self._choose_link(transition_id, save_group)

    def _choose_link(self, transition_id, save_group):
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
        if self._game_closing(transition):
            return self._refuse(transition, 'GAME_CLOSING')
        destination, installation = transition['to'], self._install_for(transition['to'])
        running = self._launcher.running(destination)
        if not running and not installation:
            return self._refuse(transition, 'DESTINATION_UNAVAILABLE')
        if not running and not self._acknowledged():
            return self._refuse(transition, 'NEEDS_BACKUP_CONFIRMATION')
        if self.online.pending_in((transition['from'], destination)):
            return self._refuse(transition, 'ONLINE_REQUEST_PENDING')
        self._select_audio_front(transition['from'])
        # Cover and park the source while plugin verification and process startup run,
        # not after them. Preparation/launch failures still resume it through _refuse.
        self._send(transition['from'], 'prepare_departure', transition, destination=destination, portal_id=transition['portal_id'],
                   carry_mode=self._carry_mode())
        if running:
            self._log(transition, f'{destination} is already running')
            if self._loading_behind(destination) is not None and not self._world_ready(destination):
                transition['deadline'] = DEADLINES['PREPARING_COLD']     # still loading its save behind the portal
        else:
            if self._prepare_destination is not None:
                try:
                    prepared = self._prepare_destination(installation)
                except Exception as exc:
                    return self._refuse(transition, 'DESTINATION_PREPARATION_FAILED', type(exc).__name__)
                if not isinstance(prepared, dict) or prepared.get('ok') is not True:
                    return self._refuse(transition, 'DESTINATION_PREPARATION_FAILED')
                self._log(transition, f'{destination} plugin preparation verified')
            try:
                launched = self._launcher.launch(installation, extra_env=self._destination_env(transition))
                if isinstance(launched, dict) and type(launched.get('pid')) is int and launched['pid'] > 0:
                    transition['destination_pid'] = launched['pid']
                transition['launched'] = True
                transition['deadline'] = DEADLINES['PREPARING_COLD']
                self._log(transition, f'starting {destination}')
            except Exception as exc:                                  # noqa: BLE001
                return self._refuse(transition, 'LAUNCH_FAILED', type(exc).__name__)
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
        if transition.get('sync'):
            # a vac sync failed: the game played last keeps its vac and stays parked behind the one in front
            self._cancel(transition)
            self._send(source, 'resume', transition, reason=code[:60])
            return self._finish(transition, 'ROLLED_BACK', code)
        self._cancel(transition)
        self._select_audio_front(source)
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
            if (transition['launched'] and (not transition.get('parked') or not self._world_ready(dst))
                    and (self._pid(dst) or transition.get('destination_pid'))):
                # Recheck throughout loading: Unity's player window can appear after the loader console was parked.
                transition['parked'] = self._window(transition, 'minimize', dst)
                self._window(transition, 'focus', src)
            if transition.get('create_new') and not transition.get('dest_save') and self._world_ready(dst):
                created = (self._heartbeat(dst).get('status') or {}).get('save_id')
                if created:
                    transition['dest_save'] = created
                    sr1, sr2 = ((transition['source_save'], created) if src == 'SR1' else (created, transition['source_save']))
                    self._links.remember(sr1, sr2)
                    self._log(transition, f'linked {src} {transition["source_save"]} <-> {dst} {created} (new save)')
            if transition.get('sync') and self._saw(transition, src, 'departure_ready') and self._world_ready(dst):
                offer = self._latest_carry(transition, src, 'departure_ready') if self._carry_mode() != 'NONE' else {}
                transition['carry_offered'] = offer
                if not self._send(dst, 'arrive', transition, source=src, reason='sync', **({'carry': offer} if offer else {})):
                    return self._rollback(transition, 'DESTINATION_QUEUE_FAILED')
                return self._enter(transition, 'SWITCHING', 'vac offered to the game in front')
            if self._saw(transition, src, 'departure_ready') and self._world_ready(dst) and self._launcher.running(dst):
                dest_save = (self._heartbeat(dst).get('status') or {}).get('save_id')
                if transition['dest_save'] and dest_save != transition['dest_save']:
                    return self._rollback(transition, 'DESTINATION_WRONG_SAVE')
                self._window(transition, 'restore', dst)
                focused = self._window(transition, 'focus', dst)
                if getattr(self._windows, 'requires_focus_confirmation', False) and not focused:
                    if not transition.get('waiting_foreground'):
                        self._log(transition, f'waiting for verified {dst} foreground before arrival')
                    transition['waiting_foreground'] = True
                    return
                transition.pop('waiting_foreground', None)
                self._select_audio_front(dst)
                # The vac follows the player: the destination adds what it can, then the source gives up exactly that.
                offer = self._latest_carry(transition, src, 'departure_ready') if self._carry_mode() != 'NONE' else {}
                transition['carry_offered'] = offer
                extra = {'carry': offer} if offer else {}
                if not self._send(dst, 'arrive', transition, source=src, portal_id=transition['portal_id'], **extra):
                    return self._rollback(transition, 'DESTINATION_QUEUE_FAILED')
                self._enter(transition, 'SWITCHING', 'both sides ready: arrival requested')
        elif state == 'SWITCHING':
            if self._saw(transition, dst, 'failed'):
                return self._rollback(transition, 'DESTINATION_FAILED')
            if self._saw(transition, dst, 'arrived'):
                policy = (self._settings().get('background_policy') or 'FREEZE').upper()
                offered = transition.get('carry_offered') or {}
                # Never take more from the source than it offered, whatever the destination reports.
                accepted = {k: min(v, offered[k]) for k, v in self._latest_carry(transition, dst, 'arrived').items()
                            if isinstance(v, int) and v > 0 and isinstance(offered.get(k), int) and offered[k] > 0}
                transition['carry_moved'] = accepted
                extra = {'carry': accepted} if accepted else {}
                if not self._send(src, 'hold', transition, policy=policy[:20], **extra):
                    return self._rollback(transition, 'SOURCE_QUEUE_FAILED')
                if accepted:
                    self._log(transition, 'carried through the portal: ' + ', '.join(f'{k} x{v}' for k, v in sorted(accepted.items())))
                left = {k: v - accepted.get(k, 0) for k, v in offered.items() if isinstance(v, int) and v > accepted.get(k, 0)}
                if left:
                    transition['carry_left'] = left
                    self._log(transition, f'left in the {src} vacpack (no room in the {dst} vac, or {dst} has no such item): ' + ', '.join(f'{k} x{v}' for k, v in sorted(left.items())))
                # The game left behind stays behind the one in front, not minimized: bringing a minimized fullscreen Unity
                # window back flashes the desktop, a plain focus swap does not.
                self._enter(transition, 'HOLDING', 'player arrived: the other game is being throttled')
        elif state == 'HOLDING':
            if self._saw(transition, src, 'failed'):
                return self._rollback(transition, 'SOURCE_FAILED')
            if self._saw(transition, src, 'held'):
                removed = self._latest_carry(transition, src, 'held')
                if removed != (transition.get('carry_moved') or {}):
                    transition['carry_mismatch'] = {'moved': transition.get('carry_moved') or {}, 'removed': removed}
                    self._log(transition, f'carry mismatch: {src} removed {removed} for {transition.get("carry_moved") or {}}')
                self._finish(transition, 'COMPLETE', 'ARRIVED')
                self._waiting.pop(dst, None)
                self.park(dst, src)
                if self._session is not None:
                    self._session.set(dst, transition.get('dest_save') or (self._heartbeat(dst).get('status') or {}).get('save_id'))

    def poll(self):
        with self._lock:
            return self._poll()

    def _poll(self):
        status = self._host.state()
        owner = getattr(self._host, 'owned_child', None)
        host_child = owner() if owner is not None else None
        for realm, close in list(self._close.items()):
            if (self.session_close_unconfirmed(realm) or status.get('state') == 'STOPPED' or
                    (owner is not None and close['host_child'] is not host_child)):
                if not self.session_close_unconfirmed(realm):
                    # A prepare or commit may already be in the game thread. Report
                    # uncertainty without replaying or releasing an active native operation.
                    self._finish(close, 'FAILED', 'SESSION_CLOSE_HOST_LOST')
                close.setdefault('host_lost_at', self._clock())
                child = close['target_child']
                exited = child.poll() is not None if child is not None else not self._launcher.running(realm)
                # Bounded: a game that had its commit quits within seconds; one that never got it stays open and is
                # the player's again, so the launcher stops guarding it instead of refusing it forever.
                if exited or self._clock() - close['host_lost_at'] > CLOSE_HOST_LOST_GRACE:
                    self._close.pop(realm, None)
        if status.get('state') != 'RUNNING':
            return [self.public(t) for t in self.transitions]
        self._drain_events()
        for transition in list(self.transitions):
            if transition['state'] in ACTIVE:
                self._advance(transition)
        paired = self._parked is not None or bool(self._close)
        for transition in list(self._close.values()):
            if not self.session_close_unconfirmed(transition['to']):
                self._advance_session_close(transition)
        self._watch_session_end()
        self._maybe_follow()
        self._maybe_start_sync()
        reply = self._call('GET', '/v1/portal/requests') or {}
        for request in reply.get('requests', []):
            rid = request.get('request_id')
            if rid and rid not in self._handled:
                self._handled.add(rid)
                self._begin(request)
        self.online.tick()
        if not paired or (self._parked is None and self._front is not None):
            self._note_session()
        return [self.public(t) for t in self.transitions]

    def request_sync(self, source, destination, source_save=None):
        """Once both games have their save open, move the vac of ``source`` (played last) into ``destination`` (in front).
        With ``source_save``, only when that is the save open over there (the player may have picked another ranch)."""
        with self._lock:
            self._pending_sync = {'from': source, 'to': destination, 'since': self._clock(), 'source_save': source_save}

    def follow(self, front, back, start):
        """``back`` starts (``start(realm, env)``) once the player opened a save in ``front``: its linked save, or a new one."""
        with self._lock:
            self._follow = {'front': front, 'back': back, 'start': start, 'since': self._clock(), 'launched': False}

    def _maybe_follow(self):
        f = self._follow
        if f is None:
            return
        front, back = f['front'], f['back']
        save = lambda realm: (self._heartbeat(realm).get('status') or {}).get('save_id') if self._world_ready(realm) else None
        if not self._launcher.running(front) or self._clock() - f['since'] > 3600:
            self._follow = None
            return
        if f['launched']:
            if f.get('new_name'):
                made = save(back)
                if made:
                    # the new game made for the player's new ranch is its partner from now on
                    sr1, sr2 = (f['front_save'], made) if front == 'SR1' else (made, f['front_save'])
                    self._links.remember(sr1, sr2)
                    self._follow = None
                elif not self._launcher.running(back):
                    self._follow = None
            return
        if self._launcher.running(back):
            self._follow = None                                    # started by hand meanwhile: leave it alone
            return
        front_save = save(front)
        if not front_save:
            return                                                 # still on the menu
        partner = self._links.lookup(front, front_save)
        env = {'SLIMEBRIDGE_TRAVEL_MODE': 'DESTINATION'}
        if partner:
            env['SLIMEBRIDGE_TRAVEL_SAVE'] = partner
        else:
            f['new_name'] = NEW_NAME.sub('', f"SlimeBridge {front_save}"[:48]).strip() or 'SlimeBridge'
            env['SLIMEBRIDGE_TRAVEL_NEW'] = f['new_name']
        f.update(launched=True, front_save=front_save)
        try:
            f['start'](back, env)
        except Exception:                                          # noqa: BLE001 - the player keeps playing; portal starts it later
            self._follow = None
            return
        if partner:
            self._follow = None

    def _maybe_start_sync(self):
        pending = self._pending_sync
        if pending is None:
            return
        src, dst = pending['from'], pending['to']
        if self._clock() - pending['since'] > DEADLINES['PREPARING_COLD'] or not (self._launcher.running(src) and self._launcher.running(dst)):
            self._pending_sync = None
            return
        if any(t['state'] in ACTIVE for t in self.transitions) or not (self._world_ready(src) and self._world_ready(dst)):
            return
        self._pending_sync = None
        save = lambda realm: (self._heartbeat(realm).get('status') or {}).get('save_id')
        if pending.get('source_save') and save(src) != pending['source_save']:
            return                                                 # another ranch is open over there: its vac is not this one's
        if self._links.lookup(src, save(src) or '') not in (None, save(dst)):
            return                                                 # the two open saves are not partners
        transition = {'id': uuid.uuid4().hex[:16], 'request_id': None, 'portal_id': None, 'from': src, 'to': dst, 'state': 'PREPARING',
                      'since': self._clock(), 'started': self._clock(), 'detail': None, 'history': [], 'launched': False,
                      'deadline': DEADLINES['PREPARING_WARM'], 'dest_save': save(dst), 'source_save': save(src), 'candidates': [],
                      'sync': True}
        self._append(transition)
        self._log(transition, f'{src} was played last: its vac moves to {dst}, where the player is')
        if not self._send(src, 'prepare_departure', transition, destination=dst, portal_id='sync', carry_mode=self._carry_mode()):
            self._finish(transition, 'FAILED', 'SYNC_QUEUE_FAILED')

    def note_waiting(self, realm, save):
        """``realm`` was started to wait behind the portal on ``save`` (None: a new save is being made for the session)."""
        with self._lock:
            self._waiting[realm] = {'save': save, 'since': self._clock()}

    def _loading_behind(self, realm):
        waiting = self._waiting.get(realm)
        if waiting is None or not self._launcher.running(realm) or self._clock() - waiting['since'] > DEADLINES['PREPARING_COLD']:
            self._waiting.pop(realm, None)
            return None
        return waiting

    def _restart_at_menu(self, transition, realm, wait=12.0):
        """A game sitting on its main menu (no save open) cannot receive the player: close it so it starts on the save."""
        pid = self._pid(realm) or getattr(getattr(self._launcher, '_children', {}).get(realm), 'pid', None)
        close = getattr(self._windows, 'request_close', None)
        if not pid or close is None:
            return False
        try:
            if close(pid) is False:
                return False
        except Exception:                                          # noqa: BLE001 - a game that refuses stays as it is
            return False
        deadline = time.monotonic() + wait
        while self._launcher.running(realm) and time.monotonic() < deadline:
            time.sleep(0.25)
        stopped = not self._launcher.running(realm)
        self._log(transition, f'{realm} was on its main menu: ' + ('closed, it starts again on the save' if stopped else 'it did not close'))
        return stopped

    def park(self, front, parked):
        """Both games were started together (Continue): ``parked`` waits at its portal while the player is in ``front``."""
        with self._lock:
            self._front, self._parked = front, parked
            child_for = getattr(self._launcher, 'owned_child', None)
            self._parked_child = child_for(parked) if child_for is not None else None
            self._parked_identity = self._identity(parked)

    def _watch_session_end(self):
        """The game the player was in has closed: the parked one saves and closes too (the two games are one session)."""
        front, parked = self._front, self._parked
        if parked is None or any(t['state'] in ACTIVE for t in self.transitions):
            return
        if not self._launcher.running(parked):
            self._front = self._parked = None                     # nothing is waiting any more
            self._parked_identity = None
            self._parked_child = None
            return
        child = self._parked_child
        child_for = getattr(self._launcher, 'owned_child', None)
        same_child = child is not None and child_for(parked) is child
        if front is not None and self._launcher.running(front):
            if self._parked_identity is None and same_child:
                self._parked_identity = self._identity(parked)
            # Focus alone (Alt+Tab, a newly opened window or a loader console) never starts a portal transaction.
            return
        self._front = self._parked = None
        self._parked_child = None
        identity, self._parked_identity = self._parked_identity, None
        record = {'id': uuid.uuid4().hex[:16], 'request_id': None, 'portal_id': None, 'from': front, 'to': parked, 'state': 'CLOSING_SESSION',
                  'code': None, 'since': self._clock(), 'started': self._clock(), 'detail': None, 'history': [], 'target': identity,
                  'launched': False, 'source_save': None, 'dest_save': None, 'candidates': [], 'target_child': child}
        self._append(record)
        if (child_for is not None and not same_child) or identity is None or identity != self._identity(parked):
            return self._finish(record, 'FAILED', 'SESSION_CLOSE_TARGET_CHANGED')
        closing = getattr(self._launcher, 'closing', None)
        if closing is not None and closing(parked):
            return self._finish(record, 'FAILED', 'SESSION_SAVE_UNCONFIRMED')
        owner = getattr(self._host, 'owned_child', None)
        record['host_child'] = owner() if owner is not None else None
        if owner is not None and record['host_child'] is None:
            return self._finish(record, 'FAILED', 'SESSION_CLOSE_HOST_LOST')
        self.online.cancel_for_close(parked)
        self._close[parked] = record
        record['phase'] = 'prepare' if parked == 'SR2' else 'legacy'
        kind = 'close_prepare' if parked == 'SR2' else 'save_quit'
        if not self._send_close(record, kind) and parked != 'SR2':
            return self._close_finish(record, 'FAILED', 'SESSION_CLOSE_QUEUE_FAILED')
        self._log(record, f'{front} closed: requested save and exit from {parked}')

    def _send_close(self, record, kind):
        pid, save = record['target']
        return self._send(record['to'], kind, record, target_pid=pid, target_save=save)

    def _close_finish(self, record, state, code):
        self._close.pop(record['to'], None)
        self._cancel(record)
        return self._finish(record, state, code)

    def _advance_session_close(self, record):
        realm = record['to']
        phase = record['phase']
        if phase == 'abort' and self._saw(record, realm, 'close_aborted'):
            return self._close_finish(record, 'FAILED', record['abort_reason'])
        if phase != 'abort' and self._saw(record, realm, 'failed'):
            return self._close_finish(record, 'FAILED', 'SESSION_SAVE_FAILED')
        if not self._launcher.running(realm):
            saved = phase != 'abort' and self._saw(record, realm, 'saved')
            return self._close_finish(record, 'COMPLETE' if saved else 'FAILED',
                                'SESSION_CLOSED' if saved else 'SESSION_SAVE_UNCONFIRMED')
        child = record.get('target_child')
        if child is not None and self._launcher.owned_child(realm) is not child:
            return self._close_finish(record, 'FAILED', 'SESSION_CLOSE_TARGET_CHANGED')
        current_pid = getattr(self._launcher, 'current_pid', None)
        if current_pid is not None and current_pid(realm) != record['target'][0]:
            return self._close_finish(record, 'FAILED', 'SESSION_CLOSE_TARGET_CHANGED')
        identity = self._identity(realm)
        changed = identity not in (None, record['target'])
        expired = self._clock() - record['since'] > 15
        if phase == 'legacy':
            if changed or expired:
                return self._close_finish(record, 'FAILED', 'SESSION_CLOSE_TARGET_CHANGED' if changed else 'SESSION_CLOSE_TIMEOUT')
            return
        if phase == 'abort':
            if not record.get('abort_queued'):
                record['abort_queued'] = self._send_close(record, 'close_abort')
        elif changed or expired:
            record['phase'] = 'abort'
            record['abort_reason'] = 'SESSION_CLOSE_TARGET_CHANGED' if changed else 'SESSION_CLOSE_TIMEOUT'
            record['abort_queued'] = self._send_close(record, 'close_abort')
        elif phase == 'prepare' and identity == record['target'] and self._saw(record, realm, 'close_ready'):
            # Commit is a distinct authorization. A retried close_ready receipt never grants it.
            record['phase'] = 'commit'
            record['since'] = self._clock()
            self._send_close(record, 'close_commit')

    def _note_session(self):
        """Outside a crossing, the one game that is running with a loaded save is where the player is."""
        if self._session is None or any(t['state'] in ACTIVE for t in self.transitions):
            return
        playing = [realm for realm in ('SR1', 'SR2') if self._launcher.running(realm) and self._world_ready(realm)]
        if len(playing) == 1:
            self._session.set(playing[0], (self._heartbeat(playing[0]).get('status') or {}).get('save_id'))

    def public(self, transition):
        keys = ('id', 'request_id', 'portal_id', 'from', 'to', 'state', 'code', 'detail', 'started', 'since', 'launched', 'source_save', 'dest_save', 'candidates')
        return {**{k: transition.get(k) for k in keys}, 'history': list(transition['history'][-14:])}
