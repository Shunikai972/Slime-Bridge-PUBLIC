"""Independent verification required before a counterpart may be linked.

A plugin reporting success is evidence of a native callback, not of persistence. The
``CounterpartVerificationPlan`` (a ``bridge-proof-plan`` with subject kind ``counterpart``)
requires independent evidence that the native save exists, belongs to the expected realm,
installation and creation provenance, matches its slot and full readback/hash where the realm
supports them, has no conflicting candidate, and (when the policy needs it) that a world loaded
against that exact save. Only receipts from a provider trusted for the *target* realm count, so an
SR1 adapter can never attest SR2 evidence or vice versa.
"""

from bridge.verification.plans import aggregate, make_plan, requirement
from bridge.verification.receipts import RUNTIME_EVIDENCE, make_receipt

READBACK = ('NATIVE_READBACK', 'DISK_READBACK')
STATIC_FROM_RESULT = ('counterpart.realm_identity', 'counterpart.installation', 'counterpart.creation_operation')


def counterpart_plan(intent, *, adapter_features, build_fingerprint=None, require_world_ready=False):
    """Plan for one observed creation. ``adapter_features`` comes from the realm adapter descriptor."""
    observed = intent['observed'] or {}
    realm = intent['target_realm']
    features = set(adapter_features)
    reqs = [requirement('counterpart.native_save_exists', 'COUNTERPART', 'native save exists (independent readback)',
                        accepted=READBACK, realm=realm, build=build_fingerprint),
            requirement('counterpart.realm_identity', 'COUNTERPART', 'identity belongs to the target realm',
                        accepted=('STATIC_ANALYSIS', *READBACK)),
            requirement('counterpart.installation', 'COUNTERPART', 'correct target installation',
                        accepted=('STATIC_ANALYSIS', *READBACK)),
            requirement('counterpart.creation_operation', 'COUNTERPART', 'creation provenance matches the intent',
                        accepted=('STATIC_ANALYSIS', *READBACK)),
            requirement('counterpart.no_conflicting_candidate', 'COUNTERPART', 'exactly one matching native save',
                        accepted=READBACK, realm=realm, build=build_fingerprint),
            requirement('counterpart.slot_matches', 'COUNTERPART', 'slot matches the observed creation',
                        accepted=READBACK, realm=realm, build=build_fingerprint,
                        not_applicable_allowed=observed.get('slot') is None),
            requirement('counterpart.full_readback', 'COUNTERPART', 'full native readback of the save',
                        accepted=READBACK, realm=realm, build=build_fingerprint,
                        blocking='full_readback' in features, not_applicable_allowed='full_readback' not in features),
            requirement('counterpart.save_hash', 'COUNTERPART', 'save content hash matches the result',
                        accepted=READBACK, realm=realm, build=build_fingerprint,
                        blocking='save_hash' in features, not_applicable_allowed='save_hash' not in features),
            requirement('counterpart.world_loaded', 'COUNTERPART', 'a world loaded against this exact save',
                        accepted=('RUNTIME_OBSERVATION',), realm=realm, build=build_fingerprint,
                        blocking=require_world_ready, not_applicable_allowed=not require_world_ready)]
    plan = make_plan('counterpart', intent['creation_id'], reqs, realm=realm)
    plan['binding'] = {'profile_id': intent['profile_id'], 'target_realm': realm,
                       'installation_id': intent['target_installation_id'], 'observed': observed}
    return plan


def static_receipts(plan, intent, adapter, verifier):
    """Consistency checks of the accepted result against the intent (no native claim)."""
    observed = intent['observed'] or {}
    expected = adapter.expected_identity(intent['creation_id'])
    checks = {'counterpart.realm_identity': observed.get('realm') == intent['target_realm'] == expected['realm'],
              'counterpart.installation': observed.get('installation_id') == intent['target_installation_id'],
              'counterpart.creation_operation': observed.get('native_save_id') == expected['native_save_id']}
    return [make_receipt(check_id, 'counterpart', plan['subject_id'], 'PASSED' if ok else 'FAILED', 'STATIC_ANALYSIS',
                         provider=verifier) for check_id, ok in sorted(checks.items())]


def _bound_save(plan):
    binding = plan.get('binding') or {}
    save = binding.get('observed') or binding.get('endpoint') or {}
    return save.get('native_save_id'), save.get('save_hash')


def evaluate(plan, receipts, *, trust):
    """Aggregate after binding: runtime/readback receipts must name the exact observed save.

    A trusted provider attesting *some* save is not enough: every runtime-evidence receipt must
    carry ``details.native_save_id`` equal to the plan's bound save, and a ``save`` artifact hash
    that differs from the observed save hash is rejected as a different save.
    """
    save_id, save_hash = _bound_save(plan)
    kept, extra = [], []
    for receipt in receipts:
        if isinstance(receipt, dict) and receipt.get('evidence_type') in RUNTIME_EVIDENCE:
            details = receipt.get('details') if isinstance(receipt.get('details'), dict) else {}
            hashes = receipt.get('artifact_hashes') if isinstance(receipt.get('artifact_hashes'), dict) else {}
            reason = None
            if details.get('native_save_id') != save_id or save_id is None:
                reason = 'UNBOUND_SAVE'
            elif save_hash is not None and hashes.get('save') not in (None, save_hash):
                reason = 'WRONG_SAVE_HASH'
            if reason:
                extra.append({'receipt_id': receipt.get('receipt_id'), 'check_id': receipt.get('check_id'),
                              'reason': reason})
                continue
        kept.append(receipt)
    result = aggregate(plan, kept, trust=trust)
    result['rejected'] = sorted(result['rejected'] + extra, key=lambda r: (str(r['check_id']), str(r['receipt_id'])))
    return result


def endpoint_plan(profile_id, realm, endpoint, *, build_fingerprint, adapter_features=(), require_world_loaded=False):
    """Re-verification of an already linked endpoint after a build change (never relinks or deletes)."""
    features = set(adapter_features)
    reqs = [requirement('endpoint.native_save_exists', 'COUNTERPART', 'the linked native save still exists',
                        accepted=READBACK, realm=realm, build=build_fingerprint),
            requirement('endpoint.identity_matches', 'COUNTERPART', 'native identity equals the linked identity',
                        accepted=READBACK, realm=realm, build=build_fingerprint),
            requirement('endpoint.full_readback', 'COUNTERPART', 'full native readback under the new build',
                        accepted=READBACK, realm=realm, build=build_fingerprint,
                        blocking='full_readback' in features, not_applicable_allowed='full_readback' not in features),
            requirement('endpoint.world_loaded', 'COUNTERPART', 'a world loaded against this save under the new build',
                        accepted=('RUNTIME_OBSERVATION',), realm=realm, build=build_fingerprint,
                        blocking=require_world_loaded, not_applicable_allowed=not require_world_loaded)]
    plan = make_plan('counterpart', f'endpoint:{profile_id}:{realm}', reqs, realm=realm)
    plan['binding'] = {'profile_id': profile_id, 'target_realm': realm, 'endpoint': dict(endpoint),
                       'build_fingerprint': build_fingerprint}
    return plan
