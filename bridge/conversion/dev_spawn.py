"""LAB-only dev spawn of imported species (``bridge-dev-spawn`` v1).

A developer/test-only path used to prove that an imported species is registered in a
foreign realm. It is NOT the player progression: the intended gameplay is hunting the
species in its native game, carrying it through SlimeBridge and releasing it in the other
world. The dev spawn:

  * is explicitly marked ``mode: TEST_ONLY`` / ``purpose: LAB_REGISTRATION_PROOF`` and is
    a one-shot operation that never touches natural spawn tables (``spawn_tables_modified:
    false``) - it cannot accidentally become normal natural spawning;
  * refuses unknown species, unregistered species, species native in the requested realm
    (or requested in their own native realm) and any non-LAB or fingerprint-mismatched
    installation - original/reference installs are always refused;
  * keeps the native execution behind ``BLOCKED_ON_LOCAL_QUERY`` (Q033) until the LAB
    handler that performs a real one-shot native spawn is implemented and verified.

The decision document is deterministic and self-contained: same request + same inputs give
the same bytes, and the receipt pins the exact request/classification/adapter/policy hashes.
"""
from __future__ import annotations

from bridge.content.canonical import REALMS, is_sha256, sha256_json
from bridge.conversion import foreign_spawn
from bridge.conversion.species_adapter import validate_adapter
from bridge.conversion.species_parity import CLASS_SOURCE, CLASS_TARGET, derive_roster

DEV_SPAWN_SCHEMA = 'bridge-dev-spawn'
DEV_SPAWN_SCHEMA_VERSION = 1
DEV_SPAWN_QUERY = 'Q033'
MODE = 'TEST_ONLY'
PURPOSE = 'LAB_REGISTRATION_PROOF'
REFUSAL_CODES = ('DEV_SPAWN_UNKNOWN_SPECIES', 'DEV_SPAWN_UNREGISTERED_SPECIES',
                 'DEV_SPAWN_NATIVE_REALM', 'DEV_SPAWN_NATIVE_COLLISION',
                 'DEV_SPAWN_NOT_LAB_INSTALL', 'DEV_SPAWN_INSTALL_REALM_MISMATCH',
                 'DEV_SPAWN_BUILD_MISMATCH', 'DEV_SPAWN_INVALID_REQUEST')
CHECK_NAMES = ('REQUEST_SHAPE', 'SPECIES_KNOWN', 'SPECIES_IMPORTED', 'ADAPTER_REGISTERED',
               'INSTALL_REALM', 'INSTALLATION_LAB', 'BUILD_FINGERPRINT', 'POLICY_RECORDED',
               'SPAWN_TABLES_UNTOUCHED')


def plan_dev_spawn(request, classification, adapters, install, policy=None):
    """Authorize (or refuse) one LAB dev spawn; pure and deterministic.

    ``request``: {'canonical_id': 'slime.ringtail', 'target_realm': 'SR1'}
    ``install``: {'realm', 'root', 'lab', 'build_fingerprint'}
    ``adapters``: the prepared adapter documents (mapping or list)
    """
    checks, refusals = [], []

    def check(name, passed, detail):
        checks.append({'check': name, 'passed': bool(passed), 'detail': detail})
        return bool(passed)

    canonical_id = request.get('canonical_id') if isinstance(request, dict) else None
    target_realm = request.get('target_realm') if isinstance(request, dict) else None
    shape_ok = (isinstance(canonical_id, str) and canonical_id.count('.') >= 1 and
                target_realm in REALMS)
    if not check('REQUEST_SHAPE', shape_ok, 'canonical_id and target_realm required'):
        refusals.append({'code': 'DEV_SPAWN_INVALID_REQUEST',
                         'detail': 'canonical_id must be a canonical slime id and target_realm SR1/SR2'})
        canonical_id, target_realm = (canonical_id if isinstance(canonical_id, str) else None), target_realm

    rows = {row.get('canonical_id'): row for row in classification.get('species', [])
            if isinstance(row, dict) and isinstance(row.get('canonical_id'), str)}
    row = rows.get(canonical_id)
    if not check('SPECIES_KNOWN', row is not None, f'{canonical_id!r} is in the classification table'):
        refusals.append({'code': 'DEV_SPAWN_UNKNOWN_SPECIES',
                         'detail': 'species is not in the captured classification table'})

    roster = derive_roster(classification)
    classification_ = row.get('classification') if row else None
    source_realm = CLASS_SOURCE.get(classification_)
    imported_ids = set(roster['sr1_only'] + roster['sr2_only'])
    importable = False
    if row is None:
        check('SPECIES_IMPORTED', False, 'unknown species cannot be imported')
    elif classification_ == 'SHARED_NATIVE':
        check('SPECIES_IMPORTED', False, 'shared species are native in both realms')
        refusals.append({'code': 'DEV_SPAWN_NATIVE_COLLISION',
                         'detail': 'species is native in the target realm; nothing to import'})
    elif target_realm == source_realm:
        check('SPECIES_IMPORTED', False, f'target realm is the native realm ({source_realm})')
        refusals.append({'code': 'DEV_SPAWN_NATIVE_REALM',
                         'detail': 'target realm is the species native realm; capture it there instead'})
    elif classification_ in ('SR1_ONLY', 'SR2_ONLY') and \
            target_realm == CLASS_TARGET[classification_] and canonical_id in imported_ids:
        importable = True
        check('SPECIES_IMPORTED', True,
              f'{classification_} species for target {CLASS_TARGET[classification_]}')
    elif classification_ in ('SR1_ONLY', 'SR2_ONLY'):
        check('SPECIES_IMPORTED', False, 'excluded from the roster (quarantined or inconsistent)')
        refusals.append({'code': 'DEV_SPAWN_UNREGISTERED_SPECIES',
                         'detail': 'species is excluded from the derived parity roster'})
    else:
        check('SPECIES_IMPORTED', False, 'classification is not an importable base class')
        refusals.append({'code': 'DEV_SPAWN_UNREGISTERED_SPECIES',
                         'detail': 'species classification does not support import'})

    adapter = None
    adapter_id = None
    if importable:
        adapter_id = f'adapter.{source_realm.lower()}-to-{target_realm.lower()}.{canonical_id}'
        docs = adapters.values() if isinstance(adapters, dict) else (adapters or [])
        adapter = next((doc for doc in docs if isinstance(doc, dict) and doc.get('adapter_id') == adapter_id), None)
        clean = adapter is not None and adapter.get('status') == 'EXPERIMENTAL' and \
            not validate_adapter(adapter)
        if check('ADAPTER_REGISTERED', clean, f'prepared adapter {adapter_id} must be EXPERIMENTAL-clean'):
            pass
        else:
            refusals.append({'code': 'DEV_SPAWN_UNREGISTERED_SPECIES',
                             'detail': f'no clean prepared adapter for {canonical_id}'})
    else:
        check('ADAPTER_REGISTERED', False, 'no adapter applies to this request')

    install_ok = isinstance(install, dict)
    install_realm = install.get('realm') if install_ok else None
    if check('INSTALL_REALM', install_ok and install_realm == target_realm,
             f'installation realm must equal the target realm {target_realm!r}'):
        pass
    else:
        refusals.append({'code': 'DEV_SPAWN_INSTALL_REALM_MISMATCH',
                         'detail': 'installation is not the requested target realm'})
    lab_ok = install_ok and install.get('lab') is True and isinstance(install.get('root'), str) \
        and bool(install.get('root'))
    if not check('INSTALLATION_LAB', lab_ok, 'only verified LAB installations may be used'):
        refusals.append({'code': 'DEV_SPAWN_NOT_LAB_INSTALL',
                         'detail': 'original/reference installations are refused; LAB copies only'})

    expected_build = None
    if isinstance(classification.get('builds'), dict):
        expected_build = classification['builds'].get(target_realm)
    fingerprint = install.get('build_fingerprint') if install_ok else None
    if check('BUILD_FINGERPRINT',
             is_sha256(expected_build) and isinstance(fingerprint, str) and
             fingerprint.lower() == expected_build.lower(),
             f'installation fingerprint must equal the captured {target_realm} build'):
        pass
    else:
        refusals.append({'code': 'DEV_SPAWN_BUILD_MISMATCH',
                         'detail': 'installation build fingerprint does not match the classification build'})

    normalized, policy_diags = foreign_spawn.normalize_policy(
        policy, imported_ids=roster['sr1_only'] + roster['sr2_only'])
    effective = foreign_spawn.effective_policy(normalized, imported_ids=roster['sr1_only'] + roster['sr2_only'])
    species_policy = effective['species'].get(canonical_id or '', {}) if canonical_id else {}
    check('POLICY_RECORDED', True,
          f'policy mode {normalized["mode"]}; requested natural spawn '
          f'{species_policy.get("requested_natural_spawn_foreign")}')
    check('SPAWN_TABLES_UNTOUCHED', True, 'dev spawn is one-shot and never modifies spawn tables')

    decision = 'AUTHORIZED' if all(entry['passed'] for entry in checks) else 'REFUSED'
    execution = ({'state': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': DEV_SPAWN_QUERY,
                  'reason': 'native curated dev-spawn handler is not implemented/verified yet'}
                 if decision == 'AUTHORIZED' else {'state': 'NOT_ATTEMPTED'})
    doc = {
        'schema': DEV_SPAWN_SCHEMA,
        'schema_version': DEV_SPAWN_SCHEMA_VERSION,
        'mode': MODE,
        'purpose': PURPOSE,
        'decision': decision,
        'request': {'canonical_id': canonical_id, 'target_realm': target_realm,
                    'source_realm': source_realm},
        'installation': {'realm': install_realm, 'root': install.get('root') if install_ok else None,
                         'lab': bool(install.get('lab')) if install_ok else False,
                         'build_fingerprint': fingerprint},
        'checks': checks,
        'refusals': sorted(refusals, key=lambda entry: (entry['code'], entry['detail'])),
        'execution': execution,
        'policy': {'mode': normalized['mode'], 'document_hash': sha256_json(normalized),
                   'requested_natural_spawn_foreign': species_policy.get('requested_natural_spawn_foreign'),
                   'active_natural_spawn_foreign': species_policy.get('active_natural_spawn_foreign')},
        'receipt': {'test_only': True, 'one_shot': True, 'spawn_tables_modified': False,
                    'purpose': PURPOSE,
                    'request_hash': sha256_json(request if isinstance(request, dict) else {}),
                    'classification_hash': sha256_json(classification),
                    'adapter_id': adapter_id if adapter is not None else None,
                    'adapter_hash': sha256_json(adapter) if isinstance(adapter, dict) else None,
                    'build_fingerprint': fingerprint},
        'diagnostics': policy_diags,
    }
    return doc


def validate_dev_spawn(doc) -> list:
    """Structural validation; empty means the decision document is coherent."""
    problems = []
    if not isinstance(doc, dict):
        return ['$: object required']
    top = {'schema', 'schema_version', 'mode', 'purpose', 'decision', 'request', 'installation',
           'checks', 'refusals', 'execution', 'policy', 'receipt', 'diagnostics'}
    for key in sorted(set(doc) - top):
        problems.append(f'$.{key}: unknown field')
    for key in top:
        if key not in doc:
            problems.append(f'$.{key}: missing')
    if problems:
        return problems
    if doc['schema'] != DEV_SPAWN_SCHEMA or doc['schema_version'] != DEV_SPAWN_SCHEMA_VERSION:
        problems.append('$.schema: wrong schema or version')
    if doc['mode'] != MODE or doc['purpose'] != PURPOSE:
        problems.append('$.mode: dev spawn must stay TEST_ONLY / LAB_REGISTRATION_PROOF')
    checks = doc['checks']
    if (not isinstance(checks, list) or [entry.get('check') for entry in checks] != list(CHECK_NAMES)
            or any(not isinstance(entry, dict) or not isinstance(entry.get('passed'), bool)
                   or not isinstance(entry.get('detail'), str) for entry in checks)):
        problems.append('$.checks: the exact check sequence with boolean results is required')
    else:
        all_passed = all(entry['passed'] for entry in checks)
        if doc['decision'] == 'AUTHORIZED' and not all_passed:
            problems.append('$.decision: authorized despite failed checks')
        if doc['decision'] not in ('AUTHORIZED', 'REFUSED'):
            problems.append('$.decision: AUTHORIZED or REFUSED required')
    refusals = doc['refusals']
    if not isinstance(refusals, list) or any(not isinstance(entry, dict) or entry.get('code') not in REFUSAL_CODES
                                             for entry in refusals):
        problems.append('$.refusals: refusal vocabulary required')
    elif (doc['decision'] == 'REFUSED') != bool(refusals):
        problems.append('$.refusals: refusals must exist exactly when refused')
    execution = doc['execution']
    if not isinstance(execution, dict):
        problems.append('$.execution: object required')
    elif doc['decision'] == 'AUTHORIZED':
        if execution.get('state') != 'BLOCKED_ON_LOCAL_QUERY' or \
                execution.get('local_query') != DEV_SPAWN_QUERY:
            problems.append('$.execution: native execution must stay blocked on Q033')
    elif execution.get('state') != 'NOT_ATTEMPTED':
        problems.append('$.execution: refused requests must not attempt execution')
    policy = doc['policy']
    requested = policy.get('requested_natural_spawn_foreign') if isinstance(policy, dict) else None
    active = policy.get('active_natural_spawn_foreign') if isinstance(policy, dict) else None
    policy_ok = ((requested in (True, False) and active is False) or
                 (requested is None and active is None))
    if not isinstance(policy, dict) or policy.get('mode') not in foreign_spawn.MODES or \
            not is_sha256(policy.get('document_hash')) or not policy_ok:
        problems.append('$.policy: policy block must stay fail-closed with the active switch false')
    receipt = doc['receipt']
    if not isinstance(receipt, dict) or not all((receipt.get('test_only') is True,
                                                 receipt.get('one_shot') is True,
                                                 receipt.get('spawn_tables_modified') is False,
                                                 is_sha256(receipt.get('request_hash')))):
        problems.append('$.receipt: test-only one-shot receipt with hashes required')
    return problems
