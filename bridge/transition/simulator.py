"""Synthetic warm-transition simulator: fake realm processes/windows/input around the REAL machine.

Each fake adapter mutates a ``SimRealmProcess`` and reports the events a real adapter would.
Deterministic faults (``bridge.sim.faults``) cover launch failure, wrong save, duplicate/stale
WORLD_READY, crashes of either realm at any stage, a Bridge restart at any stage, cancellation and
failing compensations. A stalled stage (an adapter that never answers) is treated as the host's
deadline elapsing, which requests a rollback. Nothing here is runtime evidence.

Invariants reported per run:
- ready_before_visible: the destination was never shown before a validated WORLD_READY
- never_stranded: at the end exactly one live realm is visible and owns input (or the run FAILED
  and says so); after ROLLED_BACK the source is unlocked, visible and has input
- committed_only_when_ready: COMMITTED implies the destination loaded the linked save, was
  WORLD_READY and owns input
"""

from bridge.sim.faults import Fault, FaultPlan
from .coordinator import TransitionDriver, next_command
from .machine import TERMINAL, TransitionMachine, make_intent

SIM_BUILD = 'sim-build-1'
SIM_CLOCK = '2026-01-01T00:00:00Z'


def provider_for(realm):
    return {'provider_id': f'sim.world-ready.{realm.lower()}', 'provider_version': '1.0.0'}


class SimRealmProcess:
    def __init__(self, realm, endpoint, *, pid, running=True):
        self.realm, self.endpoint = realm, dict(endpoint)
        self.pid = pid
        self.session = {'pid': pid, 'session_id': f'{realm.lower()}-{pid}'} if running else None
        self.loaded = None
        self.visible, self.has_input, self.locked, self.throttled, self.audio = running, running, False, False, True

    def start(self):
        if self.session is None:
            self.pid += 1
            self.session = {'pid': self.pid, 'session_id': f'{self.realm.lower()}-{self.pid}'}
        return self.session

    def crash(self):
        self.session, self.loaded, self.visible, self.has_input = None, None, False, False

    def world_ready_report(self, *, session=None):
        return {'schema': 'bridge-world-ready', 'schema_version': 1, 'realm': self.realm,
                'session': dict(session or self.session), 'installation_id': self.endpoint['installation_id'],
                'native_save_id': (self.loaded or {}).get('native_save_id', ''),
                'native_save_identity': (self.loaded or {}).get('native_save_identity', ''),
                'build_fingerprint': SIM_BUILD, 'process_running': True, 'load_callback_returned': True,
                'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True, 'observed_at': SIM_CLOCK,
                'provider': provider_for(self.realm), 'evidence': 'RUNTIME_OBSERVATION'}


class SimAdapters:
    """One object serving every adapter role over the two fake processes."""

    def __init__(self, source, destination, faults):
        self.src, self.dst, self.faults = source, destination, faults
        self.shown_before_ready = False
        self.world_ready_seen = False

    def __getitem__(self, role):
        return self

    def execute(self, command, transition):
        name, src, dst, faults = command['command'], self.src, self.dst, self.faults
        if command['compensation']:
            if faults.check('compensation.fail', step=command['compensation']):
                return [('compensation_failed', None, {'step': command['compensation']})]
            self._compensate(command['compensation'])
            return [('compensation_done', None, {'step': command['compensation']})]
        if name == 'lock_and_sync':
            if src.session is None or faults.check('source.lock_fail'):
                return []
            src.locked = True
            return [('source_locked', src.session, {}), ('source_synced', src.session, {})]
        if name == 'start_destination':
            if faults.check('destination.launch_fail'):
                return []
            return [('destination_started', dst.start(), {})]
        if name == 'load_linked_save':
            if dst.session is None:
                return []
            loaded = dict(dst.endpoint)
            if faults.check('destination.wrong_save'):
                loaded['native_save_id'] = 'some-other-save'
            dst.loaded = loaded
            return [('correct_save_confirmed', dst.session, dict(loaded))]
        if name == 'await_world_ready':
            if dst.session is None or faults.check('world_ready.never'):
                return []
            events = []
            if faults.check('world_ready.stale'):
                old = {'pid': dst.session['pid'] - 1, 'session_id': 'previous-process'}
                events.append(('world_ready', old, {'report': dst.world_ready_report(session=old),
                                                    'expected_build': SIM_BUILD}))
            report = dst.world_ready_report()
            events.append(('world_ready', dst.session, {'report': report, 'expected_build': SIM_BUILD}))
            if faults.check('world_ready.duplicate'):
                events.append(('world_ready', dst.session, {'report': report, 'expected_build': SIM_BUILD}))
            self.world_ready_seen = True
            return events
        if name == 'prepare':
            return [('overlay_ready', None, {})]
        if name == 'start_visual':
            src.audio = False
            return [('audio_fade_requested', None, {}), ('visual_started', None, {})]
        if name == 'hide_source':
            src.visible = False
            return [('source_hidden', src.session, {})]
        if name == 'show_destination':
            if dst.session is None:
                return []
            if not self.world_ready_seen:
                self.shown_before_ready = True
            dst.visible = True
            return [('destination_visible', dst.session, {})]
        if name == 'transfer_to_destination':
            if dst.session is None:
                return []
            src.has_input, dst.has_input = False, True
            return [('input_transferred', dst.session, {}), ('player_ready', dst.session, {})]
        if name == 'throttle_source':
            src.throttled = True
            return [('background_throttled', dst.session, {})]
        raise ValueError(f'unknown command {name}')

    def _compensate(self, step):
        src, dst = self.src, self.dst
        if step == 'RETURN_INPUT_TO_SOURCE':
            dst.has_input, src.has_input = False, src.session is not None
        elif step == 'SHOW_SOURCE':
            src.visible = src.session is not None
        elif step == 'HIDE_DESTINATION':
            dst.visible = False
        elif step == 'RESTORE_SOURCE_AUDIO':
            src.audio = True
        elif step == 'UNLOCK_SOURCE':
            src.locked = False
        elif step == 'PARK_DESTINATION':
            dst.visible, dst.has_input = False, False
        elif step == 'STOP_DESTINATION':
            dst.crash()


class TransitionSimulation:
    def __init__(self, root, *, source='SR1', destination='SR2', mode='WARM', faults=(), prewarmed=True):
        self.root, self.faults = root, FaultPlan(faults)
        endpoints = {realm: {'realm': realm, 'installation_id': f'{realm.lower()}-install',
                             'native_save_id': f'{realm.lower()}-linked-save',
                             'native_save_identity': f'{realm.lower()}-identity'} for realm in ('SR1', 'SR2')}
        self.src = SimRealmProcess(source, endpoints[source], pid=3000)
        self.dst = SimRealmProcess(destination, endpoints[destination], pid=4000, running=prewarmed)
        if prewarmed:
            self.dst.visible = self.dst.has_input = False
        self.adapters = SimAdapters(self.src, self.dst, self.faults)
        self.restarts = 0
        self._open()
        intent = make_intent(profile_id='sim-profile', mode=mode, expected_build=SIM_BUILD,
                             source={'realm': source, 'session': self.src.session, 'endpoint': endpoints[source]},
                             destination={'realm': destination, 'session': self.dst.session,
                                          'endpoint': endpoints[destination]}, portal_id='portal.sim')
        self.transition_id = self.machine.begin(intent)

    def _open(self):
        self.machine = TransitionMachine(self.root / 'transitions.sqlite3', allow_synthetic=True,
                                         trust={provider_for(r)['provider_id']: r for r in ('SR1', 'SR2')})
        self.driver = TransitionDriver(self.machine, self.adapters)

    def restart_bridge(self):
        self.machine.close()
        self.restarts += 1
        self._open()
        return self.machine.recover()

    def cancel(self):
        return self.machine.apply(self.transition_id, 'rollback', payload={'reason': 'player cancelled'})

    def close(self):
        self.machine.close()

    def current(self):
        return self.machine.get(self.transition_id)

    def step_once(self):
        """One simulation tick: injected faults first, then one adapter round-trip.

        Returns 'TERMINAL', 'PROGRESS' or 'STALLED' (an adapter that never answered: the host's
        deadline elapses and a rollback is requested; a stall while rolling back is final)."""
        transition = self.current()
        stage = transition['stage']
        if stage in TERMINAL:
            return 'TERMINAL'
        if self.faults.check(f'bridge.restart_at:{stage}'):
            self.restart_bridge()
            return 'PROGRESS'
        if self.faults.check(f'destination.crash_at:{stage}') and self.dst.session is not None:
            session = self.dst.session
            self.dst.crash()
            self.machine.apply(self.transition_id, 'destination_crashed', session=session)
            return 'PROGRESS'
        if self.faults.check(f'source.crash_at:{stage}') and self.src.session is not None:
            session = self.src.session
            self.src.crash()
            self.machine.apply(self.transition_id, 'source_crashed', session=session)
            return 'PROGRESS'
        if self.faults.check(f'cancel_at:{stage}'):
            self.cancel()
            return 'PROGRESS'
        self.driver.step(self.transition_id)
        after = self.current()
        if after['stage'] == stage and after['compensations'] == transition['compensations']:
            if stage == 'ROLLING_BACK':
                return 'STALLED'
            self.machine.apply(self.transition_id, 'rollback', payload={'reason': f'deadline at {stage}'})
        return 'PROGRESS'

    def run(self, max_steps=80):
        for _ in range(max_steps):
            if self.step_once() in ('TERMINAL', 'STALLED'):
                break
        return self.report()

    def report(self):
        transition = self.current()
        stage, facts = transition['stage'], transition['facts']
        live = [p for p in (self.src, self.dst) if p.session is not None]
        focused = [p for p in live if p.visible and p.has_input]
        never_stranded = stage == 'FAILED' or (len(focused) == 1 and (
            (stage == 'COMMITTED' and focused[0] is self.dst) or
            (stage == 'ROLLED_BACK' and focused[0] is self.src and not self.src.locked)) or
            (stage == 'ROLLED_BACK' and self.src.session is None))
        committed_ok = stage != 'COMMITTED' or (facts['world_ready'] and facts['save_confirmed'] and
                                                facts['input_owner'] == 'DESTINATION')
        return {'stage': stage, 'furthest': transition['furthest'], 'outcome': transition['outcome'],
                'events': [(e['event'], e['verdict']) for e in self.machine.events(self.transition_id)],
                'rejections': [e['details'].get('code') for e in self.machine.events(self.transition_id)
                               if e['verdict'] == 'REJECTED'],
                'faults_fired': self.faults.fired_points(), 'restarts': self.restarts,
                'invariants': {'ready_before_visible': not self.adapters.shown_before_ready,
                               'never_stranded': never_stranded, 'committed_only_when_ready': committed_ok}}


def _f(point, on_hit=1, times=1):
    return Fault(point, on_hit=on_hit, times=times)


# name -> (simulation kwargs, expected terminal stage)
SCENARIOS = {
    'sr1_to_sr2': (dict(source='SR1', destination='SR2'), 'COMMITTED'),
    'sr2_to_sr1': (dict(source='SR2', destination='SR1'), 'COMMITTED'),
    'cold_start': (dict(source='SR1', destination='SR2', mode='COLD', prewarmed=False), 'COMMITTED'),
    'destination_launch_fails': (dict(prewarmed=False, faults=(_f('destination.launch_fail'),)), 'ROLLED_BACK'),
    'destination_wrong_save': (dict(faults=(_f('destination.wrong_save'),)), 'ROLLED_BACK'),
    'duplicate_world_ready': (dict(faults=(_f('world_ready.duplicate'),)), 'COMMITTED'),
    'stale_world_ready': (dict(faults=(_f('world_ready.stale'),)), 'COMMITTED'),
    'world_ready_never': (dict(faults=(_f('world_ready.never', times=99),)), 'ROLLED_BACK'),
    'source_lock_fails': (dict(faults=(_f('source.lock_fail'),)), 'ROLLED_BACK'),
    'source_crash_while_loading': (dict(faults=(_f('source.crash_at:DESTINATION_LOADING'),)), 'ROLLED_BACK'),
    'source_crash_after_control': (dict(faults=(_f('source.crash_at:PLAYER_CONTROL_READY'),)), 'COMMITTED'),
    'destination_crash_while_loading': (dict(faults=(_f('destination.crash_at:DESTINATION_LOADING'),)), 'ROLLED_BACK'),
    'failure_after_source_hidden': (dict(faults=(_f('destination.crash_at:SOURCE_HIDDEN'),)), 'ROLLED_BACK'),
    'failure_after_destination_visible': (dict(faults=(_f('destination.crash_at:DESTINATION_VISIBLE'),)),
                                          'ROLLED_BACK'),
    'cancel_before_commit': (dict(faults=(_f('cancel_at:TRANSITION_READY'),)), 'ROLLED_BACK'),
    'compensation_fails': (dict(faults=(_f('cancel_at:SOURCE_HIDDEN'), _f('compensation.fail'))), 'FAILED'),
}
