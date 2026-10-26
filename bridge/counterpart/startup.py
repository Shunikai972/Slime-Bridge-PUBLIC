"""Automatic dual-realm startup: an ordered, fact-driven plan from one launched realm to a ready pair.

The plan is recomputed from facts every time (pure function); it never advances on a timer. The
caller feeds it what the host knows now (attached source session, the identified source save, the
resolved profile, the counterpart policy decision, the destination endpoint/session and a
validated WORLD_READY verdict) and acts on ``current``. ``transition_available`` is the only
signal the warm-transition machine may start from.
"""

STEPS = ('ATTACH_SOURCE', 'IDENTIFY_SOURCE_SAVE', 'RESOLVE_PROFILE', 'RECONCILE_COUNTERPART', 'CREATE_COUNTERPART',
         'ENSURE_DESTINATION_ENDPOINT', 'PREPARE_DESTINATION', 'AWAIT_READINESS', 'TRANSITION_AVAILABLE')
STATES = ('DONE', 'ACTIVE', 'BLOCKED', 'PENDING', 'SKIPPED')

# Which step a counterpart policy decision acts in, and whether it blocks there.
_DECISION_STEP = {
    'RECONCILE_PENDING_CREATION': ('RECONCILE_COUNTERPART', False),
    'WAIT_FOR_NATIVE_RESULT': ('RECONCILE_COUNTERPART', False),
    'CREATE_COUNTERPART': ('CREATE_COUNTERPART', False),
    'VERIFY_EXISTING_CANDIDATE': ('ENSURE_DESTINATION_ENDPOINT', False),
    'BLOCK_CONFLICT': ('ENSURE_DESTINATION_ENDPOINT', True),
    'BLOCK_NO_CAPACITY': ('CREATE_COUNTERPART', True),
    'BLOCK_PROVIDER_UNAVAILABLE': ('RECONCILE_COUNTERPART', True),
    'NEEDS_LOCAL_RUNTIME_PROOF': ('CREATE_COUNTERPART', True),
}


def plan_startup(*, source_session=None, source_save=None, profile=None, decision=None, destination_endpoint=None,
                 destination_session=None, destination_save_confirmed=False, world_ready=None, warm=True):
    """Return ``{'steps': [{'step', 'state', 'reason'}], 'current', 'blocked', 'transition_available'}``.

    ``world_ready`` is the ``(ready, diagnostics)`` tuple from ``evaluate_world_ready`` or None.
    ``warm=False`` skips preparing the destination ahead of the transition (cold switch).
    """
    states = {}
    reason = {}
    current = None

    def mark(step, state, why=None):
        nonlocal current
        states[step] = state
        if why:
            reason[step] = why
        if state in ('ACTIVE', 'BLOCKED') and current is None:
            current = step

    def gate(step, ok, why):
        if current is not None:
            states[step] = 'PENDING'
        elif ok:
            mark(step, 'DONE')
        else:
            mark(step, 'ACTIVE', why)

    gate('ATTACH_SOURCE', source_session is not None, 'waiting for the source realm session')
    gate('IDENTIFY_SOURCE_SAVE', source_save is not None, 'waiting for the source save identity')
    gate('RESOLVE_PROFILE', profile is not None, 'resolving the Bridge profile')

    decided = decision['decision'] if decision else None
    if current is None and decided is None:
        mark('RECONCILE_COUNTERPART', 'ACTIVE', 'evaluating the counterpart policy')
    if current is None and decided is not None and decided != 'USE_EXISTING_LINK' and decided not in _DECISION_STEP:
        mark('RECONCILE_COUNTERPART', 'BLOCKED', f'UNKNOWN_DECISION: {str(decided)[:64]}')  # never read as a link
    if current is None and decided in _DECISION_STEP:
        step, blocked = _DECISION_STEP[decided]
        for before in STEPS[STEPS.index('RECONCILE_COUNTERPART'):STEPS.index(step)]:
            # A candidate exists only because reconcile/create already happened; otherwise not needed.
            states[before] = 'DONE' if decided == 'VERIFY_EXISTING_CANDIDATE' else 'SKIPPED'
        mark(step, 'BLOCKED' if blocked else 'ACTIVE', decided + (': ' + ', '.join(decision['reasons'])
                                                                     if decision.get('reasons') else ''))
    if current is None:  # USE_EXISTING_LINK
        for step in ('RECONCILE_COUNTERPART', 'CREATE_COUNTERPART'):
            states.setdefault(step, 'SKIPPED')
        gate('ENSURE_DESTINATION_ENDPOINT', destination_endpoint is not None, 'linked endpoint not loaded yet')
    if warm:
        gate('PREPARE_DESTINATION', destination_session is not None and destination_save_confirmed,
             'starting the destination realm on the linked save')
        ready = bool(world_ready and world_ready[0])
        gate('AWAIT_READINESS', ready, 'waiting for a validated WORLD_READY')
    else:
        if current is None:
            states['PREPARE_DESTINATION'] = states['AWAIT_READINESS'] = 'SKIPPED'
    gate('TRANSITION_AVAILABLE', current is None, None)
    steps = [{'step': step, 'state': states.get(step, 'PENDING'), 'reason': reason.get(step)} for step in STEPS]
    blocked = next((s for s in steps if s['state'] == 'BLOCKED'), None)
    return {'steps': steps, 'current': current or 'TRANSITION_AVAILABLE',
            'blocked': blocked and {'step': blocked['step'], 'reason': blocked['reason']},
            'transition_available': states.get('TRANSITION_AVAILABLE') == 'DONE',
            'mode': 'WARM' if warm else 'COLD'}
