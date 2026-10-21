"""UI-facing projection of a transition: mode, progress and copy derived from durable state only.

Progress is the stage's position in the forward path, never elapsed time, so the overlay can
animate smoothly while readiness itself is only ever shown when the machine accepted it.
"""

from .machine import FORWARD, ROLL_FORWARD_FROM

UI_MODES = ('IDLE', 'PREPARING', 'STARTING_DESTINATION', 'VERIFYING_SAVE', 'LOADING_WORLD', 'READY', 'SWITCHING',
            'COMMITTED', 'ROLLING_BACK', 'ROLLED_BACK', 'FAILED', 'OFFLINE')
REALM_NAMES = {'SR1': 'Slime Rancher', 'SR2': 'Slime Rancher 2'}
_COPY = {
    'IDLE': 'Ready when you are',
    'PREPARING': 'Securing your ranch before the jump',
    'STARTING_DESTINATION': 'Waking up {destination}',
    'VERIFYING_SAVE': 'Checking that {destination} opened your linked save',
    'LOADING_WORLD': 'Loading your {destination} world',
    'READY': '{destination} is ready',
    'SWITCHING': 'Crossing the bridge',
    'COMMITTED': 'Welcome to {destination}',
    'ROLLING_BACK': 'Taking you safely back to {source}',
    'ROLLED_BACK': 'You are back in {source}; nothing was lost',
    'FAILED': 'The bridge needs attention',
    'OFFLINE': 'The bridge to {destination} is not available right now',
}


def ui_mode(transition, *, provider_available=True):
    if transition is None:
        return 'IDLE' if provider_available else 'OFFLINE'
    stage = transition['stage']
    if stage in ('PREPARING', 'SOURCE_LOCKED'):
        return 'PREPARING' if provider_available else 'OFFLINE'
    if stage == 'DESTINATION_STARTING':
        return 'VERIFYING_SAVE' if transition['facts']['destination_started'] else 'STARTING_DESTINATION'
    if stage == 'DESTINATION_LOADING':
        return 'LOADING_WORLD'
    if stage in ('DESTINATION_WORLD_READY', 'TRANSITION_READY'):
        return 'READY'
    if stage in FORWARD[FORWARD.index('VISUAL_TRANSITION_STARTED'):FORWARD.index('COMMITTED')]:
        return 'SWITCHING'
    return stage if stage in UI_MODES else 'FAILED'


def view_model(transition, *, provider_available=True, profile_label=None):
    """Everything the overlay needs; pure data (no Qt)."""
    mode = ui_mode(transition, provider_available=provider_available)
    if transition is None:
        return {'mode': mode, 'progress': 0.0, 'title': _COPY[mode].format(source='', destination=''),
                'source': None, 'destination': None, 'can_cancel': False, 'details': []}
    intent = transition['intent']
    source, destination = intent['source']['realm'], intent['destination']['realm']
    stage = transition['stage']
    if stage in FORWARD:
        progress = FORWARD.index(stage) / (len(FORWARD) - 1)
    elif stage == 'ROLLED_BACK':
        progress = 0.0
    else:
        progress = FORWARD.index(transition['furthest']) / (len(FORWARD) - 1)
    total = len(transition['compensations'])
    return {'mode': mode, 'stage': stage, 'progress': round(progress, 4),
            'title': _COPY[mode].format(source=REALM_NAMES[source], destination=REALM_NAMES[destination]),
            'source': {'realm': source, 'name': REALM_NAMES[source]},
            'destination': {'realm': destination, 'name': REALM_NAMES[destination]},
            'profile': profile_label or intent['profile_id'][:8],
            'save': intent['destination']['endpoint']['native_save_id'],
            'can_cancel': stage in FORWARD[1:FORWARD.index(ROLL_FORWARD_FROM[0])],
            'rollback_remaining': total, 'generation': transition['generation'],
            'details': [{'key': k, 'value': v} for k, v in sorted(transition['facts'].items())
                        if k != 'destination_session']}
