"""From durable stage to the next adapter command; adapters report events back to the machine.

Adapter roles (all implemented locally; Cloud has fakes in ``simulator``):
- ``source``: lock gameplay + flush/sync the source realm, unlock, restore audio
- ``process``: start/attach the destination (warm: reuse a prepared process), load the linked save,
  report WORLD_READY, throttle/park/stop a realm in the background
- ``window``: hide/show realm windows (no Windows API in Cloud)
- ``input``: move keyboard/mouse/controller focus between realms
- ``overlay``: prepare and run the SlimeBridge transition overlay
The coordinator never waits on a timer: it issues the command for the current stage and the
stage only advances when the adapter's event is accepted by the machine.
"""

from .machine import TERMINAL

STAGE_COMMANDS = {
    'PREPARING': ('source', 'lock_and_sync'),
    'SOURCE_LOCKED': ('process', 'start_destination'),
    'DESTINATION_STARTING': ('process', 'load_linked_save'),
    'DESTINATION_LOADING': ('process', 'await_world_ready'),
    'DESTINATION_WORLD_READY': ('overlay', 'prepare'),
    'TRANSITION_READY': ('overlay', 'start_visual'),
    'VISUAL_TRANSITION_STARTED': ('window', 'hide_source'),
    'SOURCE_HIDDEN': ('window', 'show_destination'),
    'DESTINATION_VISIBLE': ('bridge', 'request_input_switch'),
    'INPUT_SWITCHING': ('input', 'transfer_to_destination'),
    'PLAYER_CONTROL_READY': ('process', 'throttle_source'),
    'BACKGROUND_REALM_THROTTLED': ('bridge', 'commit'),
}
COMPENSATION_COMMANDS = {
    'RETURN_INPUT_TO_SOURCE': ('input', 'return_to_source'),
    'SHOW_SOURCE': ('window', 'show_source'),
    'HIDE_DESTINATION': ('window', 'hide_destination'),
    'RESTORE_SOURCE_AUDIO': ('source', 'restore_audio'),
    'UNLOCK_SOURCE': ('source', 'unlock'),
    'PARK_DESTINATION': ('process', 'park_destination'),
    'STOP_DESTINATION': ('process', 'stop_destination'),
}


def next_command(transition):
    """The single command the host should issue now, or None (terminal)."""
    stage = transition['stage']
    if stage in TERMINAL:
        return None
    if stage == 'ROLLING_BACK':
        step = transition['compensations'][0]
        adapter, command = COMPENSATION_COMMANDS[step]
        return {'adapter': adapter, 'command': command, 'compensation': step}
    adapter, command = STAGE_COMMANDS[stage]
    return {'adapter': adapter, 'command': command, 'compensation': None}


class TransitionDriver:
    """Synchronous driver: issue the next command, feed the adapter's events back (Cloud/demo)."""

    def __init__(self, machine, adapters):
        self.machine, self.adapters = machine, adapters

    def step(self, transition_id):
        transition = self.machine.get(transition_id)
        command = next_command(transition)
        if command is None:
            return {'command': None, 'results': []}
        if command['adapter'] == 'bridge':
            event = 'commit' if command['command'] == 'commit' else 'input_switch_requested'
            return {'command': command, 'results': [self.machine.apply(transition_id, event)]}
        events = self.adapters[command['adapter']].execute(command, transition)
        results = [self.machine.apply(transition_id, event, session=session, payload=payload)
                   for event, session, payload in events]
        return {'command': command, 'results': results}

    def run(self, transition_id, *, max_steps=64, on_step=None):
        trace = []
        for _ in range(max_steps):
            before = self.machine.get(transition_id)
            entry = self.step(transition_id)
            trace.append(entry)
            if on_step is not None:
                on_step(entry, self.machine.get(transition_id))
            after = self.machine.get(transition_id)
            if entry['command'] is None or (after['stage'] == before['stage'] and
                                            after['compensations'] == before['compensations']):
                break
        return trace
