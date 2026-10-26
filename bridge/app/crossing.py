"""Portal crossing: the application flow that ties portals, save pairs, warm transitions and multiplayer.

``begin`` checks the portal state, requires a LINKED save pair, builds the realm-neutral transition
intent from the pair's endpoints (the destination save is never chosen by hand), starts the durable
transition and announces a two-phase realm switch to the multiplayer server. ``finish`` mirrors the
terminal transition into the server (COMMIT or ABORT) and, for a rollback, plans the refund of an
ON_USE portal cost. Native effects (charging currency, moving windows) stay with local adapters.
"""

from bridge.portal.contracts import evaluate, plan_charge
from bridge.transition.machine import TERMINAL, make_intent

ENDPOINT_CORE = ('realm', 'installation_id', 'native_save_id', 'native_save_identity')


class CrossingError(ValueError):
    def __init__(self, code, message):
        super().__init__(f'{code}: {message}')
        self.code = code


class PortalCrossing:
    def __init__(self, *, machine, counterparts, portals, announce=None):
        """``counterparts``: CounterpartService; ``announce(phase, transition)``: multiplayer client hook."""
        self.machine, self.counterparts, self.portals = machine, counterparts, dict(portals)
        self.announce = announce or (lambda phase, transition: None)

    def begin(self, profile_id, portal_id, *, source_session, destination_session=None, facts, balance=None,
              current_builds=None):
        portal = self.portals.get(portal_id)
        if portal is None:
            raise CrossingError('UNKNOWN_PORTAL', portal_id)
        if balance is not None:
            facts = dict(facts, balance=balance)
        state = evaluate(portal, facts)['state']
        if state not in ('READY', 'REQUIREMENTS_MET'):
            raise CrossingError('PORTAL_NOT_READY', state)
        pair = self.counterparts.pair(profile_id, current_builds=current_builds)
        if pair['status'] != 'LINKED':
            raise CrossingError('PAIR_NOT_LINKED', pair['status'])
        src, dst = portal['source_realm'], portal['target_realm']
        endpoint = {realm: {k: pair['endpoints'][realm][k] for k in ENDPOINT_CORE} for realm in (src, dst)}
        charge = None
        if portal['cost'] and (portal['cost']['charge'] == 'ON_USE' or state == 'REQUIREMENTS_MET'):
            charge = plan_charge(portal, profile_id, balance=balance)
            if charge['status'] != 'PLANNED':
                raise CrossingError('COST_NOT_PAYABLE', charge['status'])
        intent = make_intent(profile_id=profile_id, portal_id=portal_id, mode=portal['transition']['mode'],
                             source={'realm': src, 'session': source_session, 'endpoint': endpoint[src]},
                             destination={'realm': dst, 'session': destination_session, 'endpoint': endpoint[dst]},
                             expected_build=pair['endpoints'][dst]['recorded_build'], charge=charge)
        transition_id = self.machine.begin(intent)  # the planned charge is durable inside the intent
        self.announce('BEGIN', {'transition_id': transition_id, 'from_realm': src, 'to_realm': dst,
                                'portal_id': portal_id})
        return {'transition_id': transition_id, 'charge': charge, 'portal_state': state}

    def finish(self, transition_id):
        transition = self.machine.get(transition_id)
        if transition['stage'] not in TERMINAL:
            raise CrossingError('TRANSITION_NOT_FINISHED', transition['stage'])
        intent = transition['intent']
        announced = {'transition_id': transition_id, 'from_realm': intent['source']['realm'],
                     'to_realm': intent['destination']['realm'], 'portal_id': intent['portal_id']}
        charge = intent.get('charge')
        if transition['stage'] == 'COMMITTED':
            self.announce('COMMIT', announced)
            return {'outcome': 'COMMITTED', 'settle': charge and dict(charge, settlement_action='COMMIT')}
        self.announce('ABORT', announced)
        refund = charge and charge['refund_on_rollback'] and dict(charge, settlement_action='RELEASE')
        return {'outcome': transition['stage'], 'settle': refund or None}
