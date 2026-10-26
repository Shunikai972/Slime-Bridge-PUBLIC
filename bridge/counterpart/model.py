"""Realm-neutral counterpart schemas: creation request, creation result, save identity, slots.

``bridge-counterpart-request`` v1 and ``bridge-counterpart-result`` v1 carry no realm-specific
native detail; a realm adapter translates them to and from its plugin wire format. A display name,
slot number, timestamp or creation marker alone is never a save identity: identity is at least
realm + installation + native save id + native save identity, strengthened by filename, slot,
build, content hash and readback evidence when the realm supports them.
"""

from bridge.content.canonical import sha256_json
from bridge.core import schema as S

REALMS = ('SR1', 'SR2')
CREATION_ID = r'[0-9a-f]{32}'
OPERATION_ID = r'[A-Za-z0-9:_-]{1,128}'
SLOT_STATUS = ('FREE', 'OCCUPIED', 'UNKNOWN', 'CONFLICT')
OUTCOMES = ('CREATED', 'ALREADY_EXISTS', 'ABSENT', 'NO_CAPACITY', 'SLOT_CONFLICT', 'FAILED', 'UNCERTAIN')
NATIVE_EFFECT = ('NONE', 'POSSIBLE', 'CONFIRMED')
RESULT_EVIDENCE = ('NATIVE_READBACK', 'CALLBACK_ONLY', 'NONE')
COMPATIBILITY = ('VALID', 'REVERIFY_REQUIRED', 'INCOMPATIBLE', 'UNKNOWN')
TRIGGERS = ('AUTOMATIC', 'OPERATOR')
_TEXT = dict(max_length=128)

ENDPOINT = S.obj({'realm': S.enum(*REALMS), 'installation_id': S.string(**_TEXT),
                  'native_save_id': S.string(**_TEXT), 'native_save_identity': S.string(**_TEXT)})
SESSION = S.obj({'pid': S.integer(minimum=1, maximum=2 ** 32 - 1), 'session_id': S.string(**_TEXT)})
PROVIDER = S.obj({'provider_id': S.string(**_TEXT), 'provider_version': S.string(**_TEXT)})

REQUEST = S.obj({
    'schema': S.enum('bridge-counterpart-request'), 'schema_version': S.enum(1),
    'creation_id': S.string(CREATION_ID), 'operation_id': S.string(OPERATION_ID),
    'profile_id': S.string(**_TEXT), 'source': ENDPOINT, 'target_realm': S.enum(*REALMS),
    'target_installation_id': S.string(**_TEXT),
    'slot_policy': S.obj({'mode': S.enum('ANY_FREE', 'PREFERRED'), 'preferred_slot': S.optional(S.integer(minimum=0))}),
    'gameplay_mode': S.optional(S.string(r'[a-z_]+', max_length=64)),
    'creation_policy': S.obj({'trigger': S.enum(*TRIGGERS), 'require_world_ready': S.boolean()}),
    'provenance_ref': S.string(**_TEXT),
})

RESULT = S.obj({
    'schema': S.enum('bridge-counterpart-result'), 'schema_version': S.enum(1),
    'creation_id': S.string(CREATION_ID), 'operation_id': S.string(OPERATION_ID), 'operation_kind': S.enum('create', 'reconcile'),
    'profile_id': S.string(**_TEXT), 'target_realm': S.enum(*REALMS), 'installation_id': S.string(**_TEXT),
    'outcome': S.enum(*OUTCOMES), 'native_effect': S.enum(*NATIVE_EFFECT),
    'native_save_id': S.optional(S.string(**_TEXT)), 'native_save_identity': S.optional(S.string(**_TEXT)),
    'slot': S.optional(S.integer(minimum=0)), 'slot_status': S.optional(S.enum(*SLOT_STATUS)),
    'native_filename': S.optional(S.string(**_TEXT)), 'save_hash': S.optional(S.sha256()),
    'provider': PROVIDER, 'session': SESSION, 'evidence': S.enum(*RESULT_EVIDENCE),
    'diagnostics': S.list_of(S.any_json()),
})


def make_request(*, creation_id, operation_id, profile_id, source, target_realm, target_installation_id,
                 slot_policy=None, gameplay_mode=None, creation_policy=None):
    request = {'schema': 'bridge-counterpart-request', 'schema_version': 1, 'creation_id': creation_id,
               'operation_id': operation_id, 'profile_id': profile_id, 'source': dict(source),
               'target_realm': target_realm, 'target_installation_id': target_installation_id,
               'slot_policy': slot_policy or {'mode': 'ANY_FREE', 'preferred_slot': None},
               'gameplay_mode': gameplay_mode,
               'creation_policy': creation_policy or {'trigger': 'AUTOMATIC', 'require_world_ready': False}}
    request['provenance_ref'] = sha256_json(request)[:64]
    problems = validate_request(request)
    if problems:
        raise ValueError(f'invalid counterpart request: {problems[:5]}')
    return request


def validate_request(request):
    problems = S.check(REQUEST, request)
    if not problems:
        if request['source']['realm'] == request['target_realm']:
            problems.append('$.target_realm: must differ from the source realm')
        body = {k: v for k, v in request.items() if k != 'provenance_ref'}
        if sha256_json(body)[:64] != request['provenance_ref']:
            problems.append('$.provenance_ref: does not bind this request')
        if request['slot_policy']['mode'] == 'PREFERRED' and request['slot_policy']['preferred_slot'] is None:
            problems.append('$.slot_policy: PREFERRED needs a slot (still revalidated natively)')
    return problems


def make_result(**fields):
    result = {'schema': 'bridge-counterpart-result', 'schema_version': 1, 'native_save_id': None,
              'native_save_identity': None, 'slot': None, 'slot_status': None, 'native_filename': None,
              'save_hash': None, 'diagnostics': [], **fields}
    return result


def validate_result(result):
    problems = S.check(RESULT, result)
    if problems:
        return problems
    outcome = result['outcome']
    has_identity = result['native_save_id'] is not None and result['native_save_identity'] is not None
    if outcome in ('CREATED', 'ALREADY_EXISTS') and not has_identity:
        problems.append('$.outcome: an existing native save needs a native identity')
    if outcome in ('CREATED', 'ALREADY_EXISTS') and result['native_effect'] != 'CONFIRMED':
        problems.append('$.native_effect: an existing save is a confirmed native effect')
    if outcome in ('ABSENT', 'NO_CAPACITY', 'SLOT_CONFLICT') and (has_identity or result['native_effect'] == 'CONFIRMED'):
        problems.append('$.outcome: a refusal/absence cannot carry a native save')
    if outcome == 'ABSENT' and result['operation_kind'] != 'reconcile':
        problems.append('$.outcome: ABSENT is only a reconciliation answer')
    if outcome == 'FAILED' and (result['native_effect'] != 'NONE' or result['native_save_id'] is not None or
                                result['native_save_identity'] is not None):
        problems.append('$.outcome: FAILED asserts no native effect and carries no save (use UNCERTAIN)')
    if outcome == 'UNCERTAIN' and result['native_effect'] == 'NONE':
        problems.append('$.native_effect: UNCERTAIN cannot assert no native effect')
    return problems


def endpoint_of(result):
    return {'realm': result['target_realm'], 'installation_id': result['installation_id'],
            'native_save_id': result['native_save_id'], 'native_save_identity': result['native_save_identity']}


def identity_strength(identity):
    """How many independent identity facts a save identity carries (display names do not count)."""
    keys = ('realm', 'installation_id', 'native_save_id', 'native_save_identity', 'native_filename', 'slot',
            'build_fingerprint', 'save_hash', 'readback')
    return sum(1 for k in keys if identity.get(k) is not None)


def same_save(a, b):
    """True only if every identity fact both sides know agrees and the core four are present."""
    core = ('realm', 'installation_id', 'native_save_id', 'native_save_identity')
    if any(a.get(k) is None or b.get(k) is None for k in core):
        return False
    return all(a.get(k) == b.get(k) for k in set(a) & set(b) if a.get(k) is not None and b.get(k) is not None
               and k not in ('display_name', 'timestamp', 'readback'))
