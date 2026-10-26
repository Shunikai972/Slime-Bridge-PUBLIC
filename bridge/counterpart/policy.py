"""Automatic counterpart policy: one pure decision from durable facts, never from guesses.

Inputs are snapshots the caller already holds: the ProfileStore profile, every journal intent for
the profile, the target endpoint's build compatibility, live provider health, the adapter's slot
answer and whether native creation for the target realm has local runtime proof. The decision
never creates when anything about an earlier creation is unresolved: an issued-but-unanswered
creation is reconciled first, an observed save is verified first, and a terminal failure of a
non-abandonable creation id is surfaced to an operator instead of being retried.
"""

from .journal import TERMINAL
from .legacy_quarantine import is_retired, retired_endpoint_creation_id

DECISIONS = ('USE_EXISTING_LINK', 'RECONCILE_PENDING_CREATION', 'CREATE_COUNTERPART', 'WAIT_FOR_NATIVE_RESULT',
             'VERIFY_EXISTING_CANDIDATE', 'BLOCK_CONFLICT', 'BLOCK_NO_CAPACITY', 'BLOCK_PROVIDER_UNAVAILABLE',
             'NEEDS_LOCAL_RUNTIME_PROOF')
LIVE_HEALTH = ('HEALTHY', 'DEGRADED')
_WAITING = ('DISPATCH_PENDING', 'DISPATCHED', 'RECONCILING')
_CANDIDATE = {'NATIVE_OBSERVED': 'plan_verification', 'READBACK_PENDING': 'verify', 'VERIFIED': 'link',
              'LINK_PENDING': 'finish_link'}
_NO_CAPACITY = ('NO_CAPACITY', 'SLOT_CONFLICT')


def _decision(decision, *reasons, creation_id=None, next_step=None, **details):
    return {'decision': decision, 'reasons': list(reasons), 'creation_id': creation_id, 'next_step': next_step,
            'details': details}


def decide(*, profile, intents, target_realm, provider_health, slot_status, runtime_proven,
           endpoint_compatibility='UNKNOWN', slot_probe_supported=False, last_outcomes=None):
    """Return ``{'decision', 'reasons', 'creation_id', 'next_step', 'details'}``.

    ``profile`` is ``ProfileStore.get`` output, ``intents`` is ``CounterpartJournal.intents_for``
    output, ``last_outcomes`` maps creation id -> the last definitive result outcome (if any).
    """
    key = target_realm.lower()
    endpoints = {realm: profile.get(realm.lower()) for realm in ('SR1', 'SR2')}
    present = [realm for realm, endpoint in endpoints.items() if endpoint]
    mine = [i for i in intents if i['target_realm'] == target_realm and i['profile_id'] == profile['bridge_profile_id']]
    last_outcomes = last_outcomes or {}
    retired = retired_endpoint_creation_id(profile, target_realm) or next(
        (i['creation_id'] for i in mine if is_retired(i['creation_id'])), None)
    if retired is not None:
        return _decision('BLOCK_CONFLICT', 'RETIRED_CREATION_ID', creation_id=retired)

    if profile.get(key) is not None:
        if len(present) != 2 or profile.get('state') != 'LINKED':
            return _decision('BLOCK_CONFLICT', 'STORE_INCONSISTENT', state=profile.get('state'))
        return _linked(endpoint_compatibility, mine)
    if not present:
        return _decision('BLOCK_CONFLICT', 'NO_SOURCE_ENDPOINT')
    if present == [target_realm]:
        return _decision('BLOCK_CONFLICT', 'TARGET_IS_SOURCE')

    conflicts = [i for i in mine if i['phase'] == 'CONFLICT']
    if conflicts:
        return _decision('BLOCK_CONFLICT', 'CREATION_CONFLICT', creation_id=conflicts[0]['creation_id'])
    live = [i for i in mine if i['phase'] not in TERMINAL]
    if len(live) > 1:
        return _decision('BLOCK_CONFLICT', 'MULTIPLE_ACTIVE_INTENTS', creation_ids=[i['creation_id'] for i in live])
    if live:
        return _active(live[0], provider_health)
    if any(i['phase'] == 'LINKED' for i in mine):
        return _decision('BLOCK_CONFLICT', 'JOURNAL_LINKED_BUT_STORE_UNLINKED')
    failed = [i for i in mine if i['phase'] in ('FAILED', 'FAILED_ABSENT')]
    if failed:
        intent = failed[-1]
        reason = 'CREATION_NOT_ABANDONABLE'
        if last_outcomes.get(intent['creation_id']) in _NO_CAPACITY:
            return _decision('BLOCK_NO_CAPACITY', 'NATIVE_REFUSED_CAPACITY', reason,
                             creation_id=intent['creation_id'])
        return _decision('BLOCK_CONFLICT', reason, creation_id=intent['creation_id'], phase=intent['phase'])

    reserved = profile.get('creation_id') if profile.get('state') == 'CREATING' else None
    if reserved and profile.get('creation_target_realm') not in (None, target_realm):
        return _decision('BLOCK_CONFLICT', 'RESERVED_FOR_OTHER_REALM', creation_id=reserved)
    if reserved and profile.get('creation_issued'):
        # Issued by an earlier host without a journal record: adopt and reconcile, never re-create.
        if provider_health not in LIVE_HEALTH:
            return _decision('BLOCK_PROVIDER_UNAVAILABLE', 'RECONCILE_REQUIRED', creation_id=reserved)
        return _decision('RECONCILE_PENDING_CREATION', 'ISSUED_WITHOUT_JOURNAL', creation_id=reserved,
                         next_step='adopt_and_reconcile')
    if not runtime_proven:
        return _decision('NEEDS_LOCAL_RUNTIME_PROOF', 'TARGET_CREATION_NOT_RUNTIME_PROVEN', creation_id=reserved)
    if provider_health not in LIVE_HEALTH:
        return _decision('BLOCK_PROVIDER_UNAVAILABLE', f'PROVIDER_{provider_health}', creation_id=reserved)
    if slot_status == 'CONFLICT':
        return _decision('BLOCK_CONFLICT', 'SLOT_CONFLICT', creation_id=reserved)
    if slot_status == 'OCCUPIED':
        return _decision('BLOCK_NO_CAPACITY', 'NO_FREE_SLOT', creation_id=reserved)
    if slot_status == 'UNKNOWN' and slot_probe_supported:
        return _decision('BLOCK_PROVIDER_UNAVAILABLE', 'SLOT_PROBE_INCOMPLETE', creation_id=reserved)
    # FREE (or a realm without a slot probe): selection only; the plugin revalidates natively.
    return _decision('CREATE_COUNTERPART', 'NO_EXISTING_COUNTERPART', creation_id=reserved, next_step='request',
                     slot_status=slot_status)


def _linked(compatibility, intents):
    if compatibility == 'VALID':
        return _decision('USE_EXISTING_LINK', 'LINKED_AND_VALID')
    if compatibility == 'REVERIFY_REQUIRED':
        linked = [i['creation_id'] for i in intents if i['phase'] == 'LINKED']
        return _decision('VERIFY_EXISTING_CANDIDATE', 'BUILD_CHANGED', creation_id=linked[0] if linked else None,
                         next_step='reverify_endpoint')
    if compatibility == 'INCOMPATIBLE':
        return _decision('BLOCK_CONFLICT', 'ENDPOINT_INCOMPATIBLE_WITH_BUILD')
    return _decision('NEEDS_LOCAL_RUNTIME_PROOF', 'ENDPOINT_COMPATIBILITY_UNKNOWN')


def _active(intent, provider_health):
    cid, phase = intent['creation_id'], intent['phase']
    if phase in _CANDIDATE:
        return _decision('VERIFY_EXISTING_CANDIDATE', f'PHASE_{phase}', creation_id=cid, next_step=_CANDIDATE[phase])
    if phase in _WAITING:
        return _decision('WAIT_FOR_NATIVE_RESULT', f'PHASE_{phase}', creation_id=cid, next_step='await_or_timeout')
    if phase == 'UNCERTAIN':
        if provider_health not in LIVE_HEALTH:
            return _decision('BLOCK_PROVIDER_UNAVAILABLE', 'RECONCILE_REQUIRED', creation_id=cid)
        return _decision('RECONCILE_PENDING_CREATION', 'OUTCOME_UNCERTAIN', creation_id=cid, next_step='reconcile')
    # INTENT_PERSISTED: nothing was issued yet, so dispatching it is the (single) create.
    if provider_health not in LIVE_HEALTH:
        return _decision('BLOCK_PROVIDER_UNAVAILABLE', f'PROVIDER_{provider_health}', creation_id=cid)
    return _decision('CREATE_COUNTERPART', 'INTENT_NOT_DISPATCHED', creation_id=cid, next_step='dispatch')
