"""Proof plan for a warm transition (runtime evidence only; Cloud simulations never satisfy it).

Every requirement is bound to the realm it is observed in and accepts runtime observation from a
provider trusted for that realm. Visual smoothness is recorded separately as a visual requirement
(screen capture reviewed locally) because "no black frame" cannot be proven by state alone.
"""

from bridge.verification.plans import make_plan, requirement

RUNTIME = ('RUNTIME_OBSERVATION',)


def transition_plan(intent, *, builds=None, include_rollback=True):
    builds = builds or {}
    source, destination = intent['source']['realm'], intent['destination']['realm']
    reqs = [requirement('transition.source_locked_and_synced', 'TRANSITION', 'source gameplay paused and flushed',
                        accepted=RUNTIME, realm=source, build=builds.get(source)),
            requirement('transition.destination_correct_save', 'TRANSITION', 'destination loaded the linked save',
                        accepted=('RUNTIME_OBSERVATION', 'NATIVE_READBACK'), realm=destination,
                        build=builds.get(destination)),
            requirement('transition.world_ready_before_visible', 'TRANSITION',
                        'destination shown only after a validated WORLD_READY', accepted=RUNTIME, realm=destination,
                        build=builds.get(destination)),
            requirement('transition.input_on_destination', 'TRANSITION', 'input owned by the destination',
                        accepted=RUNTIME, realm=destination, build=builds.get(destination)),
            requirement('transition.source_throttled', 'TRANSITION', 'source parked in the background',
                        accepted=RUNTIME, realm=source, build=builds.get(source), blocking=intent['mode'] == 'WARM',
                        not_applicable_allowed=intent['mode'] != 'WARM'),
            requirement('transition.visual_continuity', 'TRANSITION', 'no black frame or flash (screen capture review)',
                        accepted=RUNTIME, realm=destination, build=builds.get(destination), blocking=False)]
    if include_rollback:
        reqs.append(requirement('transition.rollback_restores_source', 'TRANSITION',
                                'a failed crossing returns the player to an unlocked, visible source',
                                accepted=RUNTIME, realm=source, build=builds.get(source)))
    plan = make_plan('transition', intent['profile_id'] + ':' + source + '->' + destination, reqs)
    plan['binding'] = {'portal_id': intent.get('portal_id'), 'mode': intent['mode'],
                       'source_save': intent['source']['endpoint']['native_save_id'],
                       'destination_save': intent['destination']['endpoint']['native_save_id']}
    return plan
