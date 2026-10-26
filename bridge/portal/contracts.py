"""Generic portal definitions and their evaluation into one UI state.

``bridge-portal`` v1 describes a gateway between realms: activation conditions, the destination
(resolved through the linked save pair, never chosen manually), an abstract cost in a canonical
resource, lock/unlock rules, transition policy, prompt keys and cooldown. Charging a cost or
reading a balance needs a native realm adapter that is NOT verified yet (LOCAL_QUERY Q030), so this
module only plans a charge; settlement belongs to the local transfer/escrow layer.

Project requirements captured as defaults (configurable, not hard-coded in logic):
- SR1 sanctuary gate (SR1 -> SR2): unlock costs 10 Gold Plorts; converted/native species relevant
  to progression appear in the sanctuary, impossible species remain decorative
- SR2 return portal (SR2 -> SR1): unlock costs 250000 Newbucks by default
"""

import copy

from bridge.core import schema as S

PORTAL_STATES = ('LOCKED', 'REQUIREMENTS_UNMET', 'REQUIREMENTS_MET', 'ACTIVATING', 'PREPARING_TRANSITION',
                 'DESTINATION_UNAVAILABLE', 'COUNTERPART_MISSING', 'CREATING_COUNTERPART', 'READY', 'FAILED',
                 'ROLLBACK', 'COOLDOWN')
CONDITION_KINDS = ('PROGRESSION_FLAG', 'SPECIES_KNOWN', 'RESOURCE_AT_LEAST')
_ID = r'[a-z0-9][a-z0-9._-]{0,63}'
_RESOURCE = r'(sr1|sr2)\.[a-z0-9_.]{1,64}'
PORTAL = S.obj({
    'schema': S.enum('bridge-portal'), 'schema_version': S.enum(1), 'portal_id': S.string(_ID),
    'source_realm': S.enum('SR1', 'SR2'), 'target_realm': S.enum('SR1', 'SR2'),
    'conditions': S.list_of(S.obj({'kind': S.enum(*CONDITION_KINDS), 'ref': S.string(r'[a-z0-9_.:-]{1,128}'),
                                   'amount': S.optional(S.integer(minimum=1, maximum=10 ** 9))}), max_items=16),
    'cost': S.optional(S.obj({'resource': S.string(_RESOURCE), 'amount': S.integer(minimum=1, maximum=10 ** 9),
                              'charge': S.enum('ON_UNLOCK', 'ON_USE'), 'refund_on_rollback': S.boolean()})),
    'lock': S.obj({'starts_locked': S.boolean(), 'unlock_flag': S.string(r'[a-z0-9_]{1,64}')}),
    'transition': S.obj({'mode': S.enum('WARM', 'COLD'), 'overlay': S.boolean()}),
    'prompt': S.obj({'title_key': S.string(r'[a-z0-9_.]{1,96}'), 'body_key': S.string(r'[a-z0-9_.]{1,96}')}),
    'cooldown_seconds': S.integer(minimum=0, maximum=86400),
    'sanctuary': S.optional(S.obj({'decorative_policy': S.enum('NON_CONVERTIBLE_DECORATIVE', 'HIDE_NON_CONVERTIBLE')})),
    'evidence': S.string(max_length=64),
})

DEFAULT_PORTALS = {
    'portal.sr1.sanctuary_gate': {
        'schema': 'bridge-portal', 'schema_version': 1, 'portal_id': 'portal.sr1.sanctuary_gate',
        'source_realm': 'SR1', 'target_realm': 'SR2', 'conditions': [],
        'cost': {'resource': 'sr1.plort.gold', 'amount': 10, 'charge': 'ON_UNLOCK', 'refund_on_rollback': True},
        'lock': {'starts_locked': True, 'unlock_flag': 'sr1_portal_unlocked'},
        'transition': {'mode': 'WARM', 'overlay': True},
        'prompt': {'title_key': 'portal.sanctuary.title', 'body_key': 'portal.sanctuary.body'},
        'cooldown_seconds': 10, 'sanctuary': {'decorative_policy': 'NON_CONVERTIBLE_DECORATIVE'},
        'evidence': 'BLOCKED_ON_LOCAL_QUERY'},
    'portal.sr2.return_portal': {
        'schema': 'bridge-portal', 'schema_version': 1, 'portal_id': 'portal.sr2.return_portal',
        'source_realm': 'SR2', 'target_realm': 'SR1', 'conditions': [],
        'cost': {'resource': 'sr2.currency.newbucks', 'amount': 250000, 'charge': 'ON_UNLOCK',
                 'refund_on_rollback': True},
        'lock': {'starts_locked': True, 'unlock_flag': 'sr2_portal_unlocked'},
        'transition': {'mode': 'WARM', 'overlay': True},
        'prompt': {'title_key': 'portal.return.title', 'body_key': 'portal.return.body'},
        'cooldown_seconds': 10, 'sanctuary': None, 'evidence': 'BLOCKED_ON_LOCAL_QUERY'},
}


def validate_portal(portal):
    problems = S.check(PORTAL, portal)
    if not problems and portal['source_realm'] == portal['target_realm']:
        problems.append('$.target_realm: must differ from the source realm')
    if not problems and portal['cost'] and not portal['cost']['resource'].startswith(portal['source_realm'].lower()):
        problems.append('$.cost.resource: the cost is paid in the source realm')
    return problems


def configure(portal, **changes):
    """Copy with configurable fields changed (e.g. a server's return-portal price), revalidated."""
    out = copy.deepcopy(portal)
    if 'cost_amount' in changes:
        out['cost']['amount'] = changes.pop('cost_amount')
    out.update(changes)
    problems = validate_portal(out)
    if problems:
        raise ValueError('; '.join(problems[:5]))
    return out


def evaluate(portal, facts):
    """One UI state + a requirement checklist from live facts.

    ``facts``: {'unlocked': bool, 'balance': int|None (None = unknown, adapter missing),
    'flags': set, 'known_species': set, 'counterpart': policy decision name|None,
    'transition_stage': str|None, 'provider_available': bool, 'cooldown_remaining': int}
    """
    problems = validate_portal(portal)
    if problems:
        return {'state': 'FAILED', 'checklist': [], 'reasons': problems[:3]}
    checklist = []
    for condition in portal['conditions']:
        if condition['kind'] == 'PROGRESSION_FLAG':
            ok = condition['ref'] in facts.get('flags', set())
        elif condition['kind'] == 'SPECIES_KNOWN':
            ok = condition['ref'] in facts.get('known_species', set())
        else:
            ok = (facts.get('resources') or {}).get(condition['ref'], 0) >= (condition['amount'] or 0)
        checklist.append({'requirement': f"{condition['kind'].lower()}:{condition['ref']}", 'met': ok})
    cost = portal['cost']
    balance = facts.get('balance')
    needs_payment = cost is not None and (cost['charge'] == 'ON_USE' or not facts.get('unlocked'))
    if needs_payment:
        checklist.append({'requirement': f"cost:{cost['resource']}:{cost['amount']}",
                          'met': None if balance is None else balance >= cost['amount']})
    stage = facts.get('transition_stage')
    decision = facts.get('counterpart')
    if stage in ('ROLLING_BACK', 'ROLLED_BACK'):
        state = 'ROLLBACK'
    elif stage == 'FAILED':
        state = 'FAILED'
    elif stage == 'COMMITTED':
        state = 'READY'
    elif stage in ('PREPARING', 'SOURCE_LOCKED', 'DESTINATION_STARTING', 'DESTINATION_LOADING'):
        state = 'PREPARING_TRANSITION'
    elif stage is not None:
        state = 'ACTIVATING'
    elif not facts.get('provider_available', False):
        state = 'DESTINATION_UNAVAILABLE'
    elif facts.get('cooldown_remaining', 0) > 0:
        state = 'COOLDOWN'
    elif portal['lock']['starts_locked'] and not facts.get('unlocked') and (cost is None or balance is None or
                                                                            balance < cost['amount']):
        state = 'LOCKED'
    elif not all(item['met'] for item in checklist):
        state = 'REQUIREMENTS_UNMET'
    elif needs_payment:
        state = 'REQUIREMENTS_MET'  # the player can pay/unlock; the counterpart is created automatically
    elif decision in ('CREATE_COUNTERPART', 'RECONCILE_PENDING_CREATION', 'WAIT_FOR_NATIVE_RESULT',
                      'VERIFY_EXISTING_CANDIDATE'):
        state = 'CREATING_COUNTERPART'
    elif decision == 'USE_EXISTING_LINK':
        state = 'READY'
    elif decision == 'BLOCK_PROVIDER_UNAVAILABLE':
        state = 'DESTINATION_UNAVAILABLE'
    else:
        state = 'COUNTERPART_MISSING'
    return {'state': state, 'checklist': checklist, 'reasons': [] if decision is None else [decision],
            'needs_payment': needs_payment and state not in ('LOCKED',)}


def plan_charge(portal, profile_id, *, balance):
    """A charge intent for the local escrow layer; never mutates native currency itself."""
    cost = portal['cost']
    if cost is None:
        return None
    if balance is None:
        return {'status': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': 'Q030', 'resource': cost['resource']}
    if balance < cost['amount']:
        return {'status': 'INSUFFICIENT', 'resource': cost['resource'], 'missing': cost['amount'] - balance}
    return {'status': 'PLANNED', 'profile_id': profile_id, 'portal_id': portal['portal_id'],
            'resource': cost['resource'], 'amount': cost['amount'], 'charge': cost['charge'],
            'refund_on_rollback': cost['refund_on_rollback'], 'settlement': 'LOCAL_ESCROW'}


def sanctuary_roster(catalogue_doc, portal):
    """Which species the sanctuary shows as playable progression vs decorative only."""
    policy = (portal.get('sanctuary') or {}).get('decorative_policy')
    playable, decorative = [], []
    for row in catalogue_doc['rows']:
        finals = {d['final'] for d in row['directions'].values()}
        usable = row['classification'] == 'SHARED_NATIVE' or 'EXPERIMENTAL' in finals or 'AVAILABLE' in finals
        (playable if usable else decorative).append(row['canonical_id'])
    if policy == 'HIDE_NON_CONVERTIBLE':
        decorative = []
    return {'playable': sorted(playable), 'decorative': sorted(decorative), 'policy': policy}
