"""``BridgeSavePair``: one Bridge profile's view across both realms, plus build-change compatibility.

The ProfileStore stays the canonical UNLINKED/CREATING/LINKED record; the pair adds what the
journal knows (pending creation, verification verdicts, the build each endpoint was last proven
under) and what the content manager reports. A build change never deletes or relinks anything:
it only moves an endpoint to REVERIFY_REQUIRED/INCOMPATIBLE/UNKNOWN until fresh evidence arrives.
"""

from .journal import TERMINAL
from .legacy_quarantine import is_retired, retired_endpoint_creation_id

PAIR_SCHEMA = 'bridge-save-pair'
PAIR_STATUS = ('SR1_ONLY', 'SR2_ONLY', 'PENDING_COUNTERPART', 'LINKED', 'CONFLICTED', 'REVERIFY_REQUIRED')
COMPATIBILITY = ('VALID', 'REVERIFY_REQUIRED', 'INCOMPATIBLE', 'UNKNOWN')
CONTENT_COMPATIBILITY = ('COMPATIBLE', 'REVERIFY_REQUIRED', 'INCOMPATIBLE', 'UNKNOWN')


def endpoint_compatibility(recorded_build, current_build, *, supported_builds=('*',), verdict='VERIFIED'):
    """Compatibility of a recorded endpoint with the build that is installed now."""
    if current_build is None:
        return 'UNKNOWN'
    if '*' not in supported_builds and current_build not in supported_builds:
        return 'INCOMPATIBLE'
    if verdict == 'FAILED':
        return 'INCOMPATIBLE'
    if recorded_build is None or recorded_build != current_build or verdict != 'VERIFIED':
        return 'REVERIFY_REQUIRED'
    return 'VALID'


def save_pair(profile, intents, endpoint_meta, *, current_builds=None, supported_builds=None,
              content_compatibility='UNKNOWN'):
    """Build the pair document from ProfileStore + journal snapshots (pure; never mutates)."""
    current_builds = current_builds or {}
    supported_builds = supported_builds or {}
    if content_compatibility not in CONTENT_COMPATIBILITY:
        raise ValueError('unknown content compatibility')
    endpoints, reasons = {}, []
    if any(retired_endpoint_creation_id(profile, realm) is not None for realm in ('SR1', 'SR2')) or \
            any(is_retired(i['creation_id']) for i in intents) or \
            any(is_retired(m.get('creation_id')) for m in endpoint_meta.values()):
        reasons.append('RETIRED_CREATION_ID')
    for realm in ('SR1', 'SR2'):
        endpoint = profile.get(realm.lower())
        if endpoint is None:
            endpoints[realm] = None
            continue
        meta = endpoint_meta.get(realm)
        identity = {'realm': realm, **endpoint}
        if meta is not None and meta['endpoint'] != identity:
            reasons.append(f'{realm}_RECORD_IDENTITY_MISMATCH')
        compat = endpoint_compatibility(meta and meta['build_fingerprint'], current_builds.get(realm),
                                        supported_builds=supported_builds.get(realm, ('*',)),
                                        verdict=meta['verdict'] if meta else 'REVERIFY_REQUIRED')
        endpoints[realm] = {**identity, 'origin': meta['origin'] if meta else 'UNRECORDED',
                            'creation_id': meta['creation_id'] if meta else None,
                            'recorded_build': meta['build_fingerprint'] if meta else None,
                            'verdict': meta['verdict'] if meta else None, 'compatibility': compat}
    present = [realm for realm, endpoint in endpoints.items() if endpoint]
    pending = [i for i in intents if i['phase'] not in TERMINAL]
    conflicts = [i['creation_id'] for i in intents if i['phase'] == 'CONFLICT']
    if conflicts:
        reasons.append('CREATION_CONFLICT')
    if len(pending) > 1:
        reasons.append('MULTIPLE_ACTIVE_INTENTS')
    if len(present) == 2 and profile.get('state') != 'LINKED':
        reasons.append('STORE_INCONSISTENT')
    if any(e and e['compatibility'] == 'INCOMPATIBLE' for e in endpoints.values()):
        reasons.append('ENDPOINT_INCOMPATIBLE')

    if conflicts or 'RETIRED_CREATION_ID' in reasons or 'MULTIPLE_ACTIVE_INTENTS' in reasons or \
            'STORE_INCONSISTENT' in reasons or \
            'ENDPOINT_INCOMPATIBLE' in reasons or any(r.endswith('_RECORD_IDENTITY_MISMATCH') for r in reasons):
        status = 'CONFLICTED'
    elif len(present) == 2:
        status = 'LINKED' if all(e['compatibility'] == 'VALID' for e in endpoints.values()) else 'REVERIFY_REQUIRED'
    elif pending:
        status = 'PENDING_COUNTERPART'
    elif present == ['SR1']:
        status = 'SR1_ONLY'
    elif present == ['SR2']:
        status = 'SR2_ONLY'
    else:
        status, reasons = 'CONFLICTED', reasons + ['NO_ENDPOINT']
    return {'schema': PAIR_SCHEMA, 'schema_version': 1, 'profile_id': profile['bridge_profile_id'],
            'store_state': profile.get('state'), 'status': status, 'endpoints': endpoints,
            'generation': max([i['generation'] for i in intents] + [0]),
            'pending': ({'creation_id': pending[0]['creation_id'], 'phase': pending[0]['phase'],
                         'target_realm': pending[0]['target_realm']} if len(pending) == 1 else None),
            'verification': {i['creation_id']: (i['verification'] or {}).get('verdict') for i in intents},
            'content_compatibility': content_compatibility,
            'build_fingerprints': {realm: current_builds.get(realm) for realm in ('SR1', 'SR2')},
            'reasons': sorted(set(reasons))}
