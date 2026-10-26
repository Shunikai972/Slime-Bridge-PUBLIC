"""Deterministic species parity matrix (``bridge-species-parity`` v1).

Reads the reviewed classification and the realm registry snapshots, derives the exact
base-species roster (SHARED_NATIVE / SR1_ONLY / SR2_ONLY with explicit exclusions), and
assembles one honest machine-readable row per included species: presence, native identity,
record hashes, plort mapping, adapter descriptor state, runtime-state vocabulary, foreign
natural-spawn policy and known blockers.

The matrix never claims runtime success. Every native step that has no verified API stays
``BLOCKED_ON_LOCAL_QUERY`` with the exact query; foreign natural spawning stays inactive for
every imported species until Q034 is answered. Largos, quarantined definitions and any
non-base row can never enter the roster: derivation fails closed per row with a recorded
reason instead of silently dropping or including it.
"""
from __future__ import annotations

import re

from bridge.content.canonical import REALMS, canonical_json, is_sha256, sha256_json
from bridge.content.ir import PAYLOAD_KEYS
from bridge.conversion import foreign_spawn
from bridge.conversion.species_adapter import (PLORT_ID_RE, find_snapshot_row, plort_slug_agrees,
                                               validate_adapter)

PARITY_SCHEMA = 'bridge-species-parity'
PARITY_SCHEMA_VERSION = 1
ROSTER_CLASSES = ('SHARED_NATIVE', 'SR1_ONLY', 'SR2_ONLY')
CLASS_TARGET = {'SR1_ONLY': 'SR2', 'SR2_ONLY': 'SR1'}
CLASS_SOURCE = {'SR1_ONLY': 'SR1', 'SR2_ONLY': 'SR2'}

EXCLUSION_REASONS = (
    'ROW_NOT_OBJECT', 'MISSING_CANONICAL_ID', 'DUPLICATE_CANONICAL_ID', 'CLASSIFICATION_UNSUPPORTED',
    'PRESENCE_UNCAPTURED', 'PRESENCE_MISMATCH', 'NATIVE_FOR_ABSENT_REALM', 'NATIVE_ROLE_NOT_BASE',
    'NATIVE_QUARANTINED', 'NATIVE_INCOMPLETE',
)
BLOCKER_CODES = (
    'ADAPTER_MISSING', 'ADAPTER_QUARANTINED', 'NATIVE_REGISTRATION_NOT_IMPLEMENTED',
    'PLORT_TARGET_PRESENCE_PENDING_CAPTURE', 'BEHAVIOR_DATA_UNCAPTURED', 'DIET_DATA_UNCAPTURED',
    'ECONOMY_DATA_UNCAPTURED', 'FOREIGN_NATURAL_SPAWN_NOT_IMPLEMENTED', 'VISUAL_ASSETS_UNATTACHED',
)
BEHAVIOR_CLASSES = ('GENERIC', 'DATA_DRIVEN', 'SPECIAL_NATIVE_COMPONENT', 'BLOCKED_ON_LOCAL_QUERY',
                    'UNSUPPORTED_PENDING_PROOF')
BEHAVIOR_QUERY = {'SR1': 'Q009', 'SR2': 'Q010'}
PLORT_STATUSES = ('NATIVE_BOTH_REALMS', 'PENDING_CAPTURE', 'CLASSIFIED_ABSENT', 'NONE', 'QUARANTINED')
_ADAPTER_ID_RE = re.compile(r'^adapter\.(sr1|sr2)-to-(sr1|sr2)\.(.+)$')
_ROW_KEYS = frozenset({'canonical_id', 'display_name', 'classification', 'required_adapter_direction',
                       'presence', 'native', 'record_hashes', 'plort', 'adapter', 'states', 'blockers',
                       'foreign_world_spawn', 'evidence'})
_TOP_KEYS = frozenset({'schema', 'schema_version', 'inputs', 'builds', 'data_evidence', 'traversal',
                       'roster', 'collisions', 'adapters', 'policy', 'blocked_queries', 'species'})


def _captured_bool(value):
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def _source_of(classification):
    return CLASS_SOURCE.get(classification)


def _expected_presence(classification):
    return {'SHARED_NATIVE': {'SR1': True, 'SR2': True},
            'SR1_ONLY': {'SR1': True, 'SR2': False},
            'SR2_ONLY': {'SR1': False, 'SR2': True}}[classification]


def _row_problems(row):
    """Fail-closed inclusion checks; returns exclusion reasons (empty means includable)."""
    reasons = []
    classification = row.get('classification')
    if classification not in ROSTER_CLASSES:
        return ['CLASSIFICATION_UNSUPPORTED']
    present = row.get('present')
    if not isinstance(present, dict):
        return ['PRESENCE_UNCAPTURED']
    values = {realm: _captured_bool(present.get(realm)) for realm in REALMS}
    if any(value is None for value in values.values()):
        return ['PRESENCE_UNCAPTURED']
    if values != _expected_presence(classification):
        reasons.append('PRESENCE_MISMATCH')
    native = row.get('native') if isinstance(row.get('native'), dict) else {}
    for realm in REALMS:
        block = native.get(realm)
        if values[realm]:
            if not isinstance(block, dict):
                reasons.append('NATIVE_INCOMPLETE')
                continue
            if block.get('role') != 'base':
                reasons.append('NATIVE_ROLE_NOT_BASE')
            if block.get('quarantine') is not None or block.get('malformed'):
                reasons.append('NATIVE_QUARANTINED')
            if not block.get('native_ref') or not is_sha256(block.get('definition_hash')):
                reasons.append('NATIVE_INCOMPLETE')
        elif block is not None:
            reasons.append('NATIVE_FOR_ABSENT_REALM')
    return reasons


def _display_name(row):
    for realm in REALMS:
        block = (row.get('native') or {}).get(realm)
        if isinstance(block, dict) and block.get('display_name'):
            return block['display_name']
    return None


def derive_roster(classification) -> dict:
    """The exact roster: sorted canonical ids per class plus excluded rows with reasons."""
    rows = classification.get('species') if isinstance(classification, dict) else None
    if not isinstance(rows, list):
        raise ValueError('classification document without a species table')
    buckets = {'shared_native': [], 'sr1_only': [], 'sr2_only': []}
    names = {}
    excluded = []
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            excluded.append({'canonical_id': None, 'classification': None, 'reasons': ['ROW_NOT_OBJECT']})
            continue
        canonical_id = row.get('canonical_id')
        if not isinstance(canonical_id, str) or not canonical_id:
            excluded.append({'canonical_id': None, 'classification': row.get('classification'),
                             'reasons': ['MISSING_CANONICAL_ID']})
            continue
        grouped.setdefault(canonical_id, []).append(row)
    for canonical_id in sorted(cid for cid, items in grouped.items() if len(items) > 1):
        excluded.append({'canonical_id': canonical_id, 'classification': grouped[canonical_id][0].get('classification'),
                         'reasons': ['DUPLICATE_CANONICAL_ID']})
    for canonical_id in sorted(cid for cid, items in grouped.items() if len(items) == 1):
        row = grouped[canonical_id][0]
        reasons = sorted(set(_row_problems(row)))
        if reasons:
            excluded.append({'canonical_id': canonical_id, 'classification': row.get('classification'),
                             'reasons': reasons})
            continue
        classification_ = row['classification']
        key = {'SHARED_NATIVE': 'shared_native', 'SR1_ONLY': 'sr1_only', 'SR2_ONLY': 'sr2_only'}[classification_]
        buckets[key].append(canonical_id)
        maybe_name = _display_name(row)
        names[canonical_id] = maybe_name
    for key in buckets:
        buckets[key].sort()
    excluded.sort(key=lambda entry: (str(entry.get('canonical_id')), canonical_json(entry)))
    counts = {key: len(buckets[key]) for key in ('shared_native', 'sr1_only', 'sr2_only')}
    counts['excluded'] = len(excluded)
    counts['rows_scanned'] = len(rows)
    return {**buckets, 'names': names, 'excluded': excluded, 'counts': counts}


def native_identity_sets(classification) -> dict:
    """Captured native canonical identities per realm (species and plorts)."""
    species = {realm: set() for realm in REALMS}
    plorts = {realm: set() for realm in REALMS}
    for row in classification.get('species', []):
        if not isinstance(row, dict) or not isinstance(row.get('canonical_id'), str):
            continue
        for realm in REALMS:
            if _captured_bool((row.get('present') or {}).get(realm)):
                species[realm].add(row['canonical_id'])
    for row in classification.get('plorts', []):
        if not isinstance(row, dict) or not isinstance(row.get('canonical_id'), str):
            continue
        for realm in REALMS:
            if _captured_bool((row.get('present') or {}).get(realm)):
                plorts[realm].add(row['canonical_id'])
    return {'species': species, 'plorts': plorts}


def _produces_of(row, realm):
    block = (row.get('native') or {}).get(realm)
    if not isinstance(block, dict):
        return []
    return sorted({ref for ref in (block.get('produces') or []) if isinstance(ref, str)})


def target_identity_collisions(classification, roster) -> list:
    """Reserved imported identities must never collide with a target realm's native set."""
    problems = []
    native = native_identity_sets(classification)
    rows = {row.get('canonical_id'): row for row in classification.get('species', [])
            if isinstance(row, dict) and isinstance(row.get('canonical_id'), str)}
    reserved_species = {realm: [] for realm in REALMS}
    reserved_plorts = {realm: [] for realm in REALMS}
    for bucket, target in (('sr1_only', 'SR2'), ('sr2_only', 'SR1')):
        source = 'SR1' if target == 'SR2' else 'SR2'
        for canonical_id in roster[bucket]:
            reserved_species[target].append(canonical_id)
            if canonical_id in native['species'][target]:
                problems.append(f'{canonical_id}: reserved species id collides with a native {target} species')
            for plort_id in _produces_of(rows.get(canonical_id) or {}, source):
                reserved_plorts[target].append(plort_id)
                if plort_id in native['plorts'][target]:
                    problems.append(f'{plort_id}: reserved plort id collides with a native {target} plort')
    for realm in REALMS:
        for kind, values in (('species', reserved_species), ('plort', reserved_plorts)):
            duplicates = sorted({value for value in values[realm] if values[realm].count(value) > 1})
            for duplicate in duplicates:
                problems.append(f'{duplicate}: reserved {kind} id twice for {realm}')
    return problems


def _index_adapters(adapters) -> dict:
    if isinstance(adapters, dict):
        docs = list(adapters.values())
    elif isinstance(adapters, (list, tuple)):
        docs = list(adapters)
    else:
        raise ValueError('adapters must be a mapping or list of adapter documents')
    indexed = {}
    for doc in docs:
        if not isinstance(doc, dict) or not isinstance(doc.get('adapter_id'), str):
            raise ValueError('adapter document without an adapter_id')
        indexed[doc['adapter_id']] = doc
    return indexed


def _adapter_entry(doc) -> dict:
    direction = doc.get('direction') or {}
    return {'adapter_id': doc['adapter_id'],
            'direction': f'{direction.get("source_realm")}->{direction.get("target_realm")}',
            'status': doc.get('status'),
            'validation_problems': len(validate_adapter(doc)),
            'plort_mode': (doc.get('plort') or {}).get('mode')}


def _plort_for_exclusive(row, doc, target_status_override=None):
    if doc is None:
        produces = _produces_of(row, _source_of(row.get('classification')))
        mode = 'PLORT' if len(produces) == 1 else ('NONE' if not produces else 'AMBIGUOUS')
        entry = {'mode': mode, 'canonical_id': produces[0] if mode == 'PLORT' else None,
                 'source': None, 'target_status': 'QUARANTINED', 'presence_evidence': None,
                 'local_query': None}
        return entry
    plort = doc['plort']
    source = plort.get('source') or {}
    mode = plort.get('mode')
    if mode == 'NONE':
        return {'mode': 'NONE', 'canonical_id': None, 'source': None, 'target_status': 'NONE',
                'presence_evidence': plort.get('presence_evidence'), 'local_query': None}
    if doc.get('status') == 'QUARANTINED':
        status = 'QUARANTINED'
    elif target_status_override is not None:
        status = target_status_override
    elif plort.get('presence_evidence') == 'PENDING_CAPTURE':
        status = 'PENDING_CAPTURE'
    elif plort.get('target_native_present') is False:
        status = 'CLASSIFIED_ABSENT'
    else:
        status = 'QUARANTINED'
    return {'mode': mode, 'canonical_id': plort.get('canonical_id'),
            'source': {'realm': source.get('realm'), 'native_ref': source.get('native_ref')},
            'target_status': status, 'presence_evidence': plort.get('presence_evidence'),
            'local_query': plort.get('local_query')}


def _plort_for_shared(row, snapshots):
    realm_produces = {realm: _produces_of(row, realm) for realm in REALMS}
    produces = sorted({canonical for values in realm_produces.values() for canonical in values})
    mode = 'PLORT' if len(produces) == 1 else ('NONE' if not produces else 'AMBIGUOUS')
    entry = {'mode': mode, 'canonical_id': produces[0] if mode == 'PLORT' else None,
             'source': None, 'target_status': 'NATIVE_BOTH_REALMS', 'presence_evidence': None,
             'local_query': None}
    if mode == 'AMBIGUOUS':
        entry['canonical_ids'] = produces
    if mode == 'PLORT':
        canonical = produces[0]
        source = None
        for realm in REALMS:
            block = (row.get('native') or {}).get(realm)
            if not isinstance(block, dict) or not block.get('native_ref'):
                continue
            snapshot_row = find_snapshot_row(snapshots.get(realm) or {}, block['native_ref'])
            refs = snapshot_row.get('produces', []) if isinstance(snapshot_row, dict) else []
            ref = next((ref for ref in refs if isinstance(ref, str) and plort_slug_agrees(canonical, ref)), None)
            if ref is not None:
                source = {'realm': realm, 'native_ref': ref}
                break
        entry['source'] = source if source is not None else {
            'realm': 'SR1' if realm_produces['SR1'] else 'SR2', 'native_ref': None}
    return entry


def _exclusive_states(doc, source_realm, plort_entry):
    reg_query = None
    if doc is not None and doc.get('target_registration'):
        reg_query = doc['target_registration'][0].get('local_query')
    clean = doc is not None and doc.get('status') == 'EXPERIMENTAL' and not validate_adapter(doc)

    def section(name):
        return (doc or {}).get(name) or {}

    return {
        'runtime_registration': {'state': 'PLAN_READY' if clean else 'QUARANTINED',
                                 'execution': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': reg_query},
        'inventory': {'state': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': section('inventory').get('local_query')},
        'release': {'state': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': section('inventory').get('local_query')},
        'save_load': {'state': 'SCHEMA_READY', 'native_binding': 'BLOCKED_ON_LOCAL_QUERY',
                      'local_query': section('save').get('local_query'),
                      'serialization_schema_version': section('save').get('serialization_schema_version')},
        'diet': {'state': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': section('diet').get('local_query')},
        'behavior': {'classification': 'BLOCKED_ON_LOCAL_QUERY',
                     'local_query': BEHAVIOR_QUERY[source_realm]},
        'economy': {'state': 'BLOCKED_ON_LOCAL_QUERY', 'local_query': section('economy').get('local_query')},
        'plort': {'state': plort_entry['target_status'], 'local_query': plort_entry['local_query']},
        'visual': {'state': 'VISUAL_ASSET_BLOCKED', 'evidence': 'NEEDS_LOCAL_VISUAL_PROOF'},
    }


def _blockers_for(doc, plort_entry, source_realm):
    blockers = []
    if doc is None:
        blockers.append({'code': 'ADAPTER_MISSING', 'local_query': None})
    else:
        if doc.get('status') == 'QUARANTINED':
            blockers.append({'code': 'ADAPTER_QUARANTINED', 'local_query': None})
        blockers.append({'code': 'NATIVE_REGISTRATION_NOT_IMPLEMENTED',
                         'local_query': (doc.get('target_registration') or [{}])[0].get('local_query')})
        blockers.append({'code': 'BEHAVIOR_DATA_UNCAPTURED', 'local_query': BEHAVIOR_QUERY[source_realm]})
        blockers.append({'code': 'DIET_DATA_UNCAPTURED', 'local_query': (doc.get('diet') or {}).get('local_query')})
        blockers.append({'code': 'ECONOMY_DATA_UNCAPTURED', 'local_query': (doc.get('economy') or {}).get('local_query')})
        blockers.append({'code': 'VISUAL_ASSETS_UNATTACHED', 'local_query': None})
    if plort_entry['target_status'] == 'PENDING_CAPTURE':
        blockers.append({'code': 'PLORT_TARGET_PRESENCE_PENDING_CAPTURE',
                         'local_query': plort_entry['local_query']})
    blockers.append({'code': 'FOREIGN_NATURAL_SPAWN_NOT_IMPLEMENTED',
                     'local_query': foreign_spawn.SPAWN_INTEGRATION_QUERY})
    return sorted(blockers, key=lambda entry: (entry['code'], str(entry['local_query'])))


def _species_row(canonical_id, row, roster, adapters_by_id, snapshots, effective, classification):
    classification_ = row['classification']
    presence = {realm: _captured_bool((row.get('present') or {}).get(realm)) for realm in REALMS}
    native_out, record_hashes = {}, {}
    for realm in REALMS:
        block = (row.get('native') or {}).get(realm)
        if presence[realm] and isinstance(block, dict):
            native_out[realm] = {'native_ref': block.get('native_ref'),
                                 'numeric_id': block.get('numeric_id'),
                                 'definition_hash': block.get('definition_hash')}
            snapshot_row = find_snapshot_row(snapshots.get(realm) or {}, block.get('native_ref') or '')
            record_hashes[realm] = sha256_json(snapshot_row) if isinstance(snapshot_row, dict) else None
        else:
            native_out[realm] = None
            record_hashes[realm] = None
    out = {
        'canonical_id': canonical_id,
        'display_name': roster['names'].get(canonical_id),
        'classification': classification_,
        'presence': presence,
        'native': native_out,
        'record_hashes': record_hashes,
        'evidence': {'data_evidence': {'SR1': (classification.get('data_evidence') or {}).get('SR1'),
                                       'SR2': (classification.get('data_evidence') or {}).get('SR2')},
                     'identity_basis': row.get('identity_basis'),
                     'identity_evidence': row.get('identity_evidence'),
                     'source_row_sha256': sha256_json(row)},
    }
    if classification_ == 'SHARED_NATIVE':
        out.update({'required_adapter_direction': None, 'adapter': None,
                    'plort': _plort_for_shared(row, snapshots),
                    'states': {'import': {'state': 'NOT_REQUIRED_NATIVE_BOTH_REALMS'}},
                    'blockers': [],
                    'foreign_world_spawn': {'applicable': False, 'reason': 'NATIVE_BOTH_REALMS'}})
        return out
    source_realm = CLASS_SOURCE[classification_]
    target_realm = CLASS_TARGET[classification_]
    expected_id = f'adapter.{source_realm.lower()}-to-{target_realm.lower()}.{canonical_id}'
    doc = adapters_by_id.get(expected_id)
    plort_entry = _plort_for_exclusive(row, doc)
    policy_entry = effective['species'].get(canonical_id) or {}
    out.update({
        'required_adapter_direction': f'{source_realm}->{target_realm}',
        'adapter': _adapter_entry(doc) if doc is not None else None,
        'plort': plort_entry,
        'states': _exclusive_states(doc, source_realm, plort_entry),
        'blockers': _blockers_for(doc, plort_entry, source_realm),
        'foreign_world_spawn': {'applicable': True,
                                'requested': policy_entry.get('requested_natural_spawn_foreign'),
                                'requested_source': policy_entry.get('requested_source'),
                                'active': False, 'mode': effective.get('mode'),
                                'integration': 'BLOCKED_ON_LOCAL_QUERY',
                                'local_query': foreign_spawn.SPAWN_INTEGRATION_QUERY},
    })
    return out


def build_parity_matrix(classification, snapshots, adapters, policy=None) -> dict:
    """Assemble the deterministic parity document; raises only on programmer errors."""
    roster = derive_roster(classification)
    collisions = target_identity_collisions(classification, roster)
    policy = policy if isinstance(policy, dict) else foreign_spawn.default_policy()
    exclusive_ids = roster['sr1_only'] + roster['sr2_only']
    effective = foreign_spawn.effective_policy(policy, imported_ids=exclusive_ids)
    adapters_by_id = _index_adapters(adapters)
    rows = {row.get('canonical_id'): row for row in classification.get('species', [])
            if isinstance(row, dict) and isinstance(row.get('canonical_id'), str)}
    species_rows = []
    included = sorted(set(roster['shared_native']) | set(exclusive_ids))
    for canonical_id in included:
        species_rows.append(_species_row(canonical_id, rows[canonical_id], roster, adapters_by_id,
                                         snapshots, effective, classification))
    doc = {
        'schema': PARITY_SCHEMA,
        'schema_version': PARITY_SCHEMA_VERSION,
        'inputs': {
            'classification_schema': classification.get('schema'),
            'classification_schema_version': classification.get('schema_version'),
            'snapshot_schemas': {realm: (snapshots.get(realm) or {}).get('schema') for realm in REALMS},
            'input_hashes': {'classification': sha256_json(classification),
                             'SR1_snapshot': sha256_json(snapshots.get('SR1') or {}),
                             'SR2_snapshot': sha256_json(snapshots.get('SR2') or {})},
        },
        'builds': classification.get('builds'),
        'data_evidence': classification.get('data_evidence'),
        'traversal': classification.get('traversal'),
        'roster': {'shared_native': roster['shared_native'], 'sr1_only': roster['sr1_only'],
                   'sr2_only': roster['sr2_only'], 'names': roster['names'],
                   'excluded': roster['excluded'], 'counts': roster['counts']},
        'collisions': collisions,
        'adapters': {adapter_id: _adapter_entry(doc_) for adapter_id, doc_ in adapters_by_id.items()},
        'policy': {'document_hash': sha256_json(policy), 'mode': policy.get('mode'),
                   'effective_hash': sha256_json(effective)},
        'blocked_queries': [],
        'species': species_rows,
    }
    doc['blocked_queries'] = _collect_queries(doc)
    return doc


def _collect_queries(doc) -> list:
    found = set()
    for row in doc.get('species', []):
        for value in (row.get('plort') or {}).values():
            if isinstance(value, str) and re.fullmatch(r'Q\d{3}', value):
                found.add(value)
        spawn = row.get('foreign_world_spawn') or {}
        if isinstance(spawn.get('local_query'), str):
            found.add(spawn['local_query'])
        for blocker in row.get('blockers') or []:
            if isinstance(blocker.get('local_query'), str):
                found.add(blocker['local_query'])
        for entry in (row.get('states') or {}).values():
            if isinstance(entry, dict) and isinstance(entry.get('local_query'), str):
                found.add(entry['local_query'])
    return sorted(found)


def validate_parity(doc) -> list:
    """Structural + policy validation; returns problems (never raises); empty means ready."""
    problems = []
    if not isinstance(doc, dict):
        return ['$: object required']
    for key in sorted(set(doc) - _TOP_KEYS):
        problems.append(f'$.{key}: unknown field')
    for key in _TOP_KEYS:
        if key not in doc:
            problems.append(f'$.{key}: missing')
    if problems:
        return problems
    if doc['schema'] != PARITY_SCHEMA or doc['schema_version'] != PARITY_SCHEMA_VERSION:
        problems.append('$.schema: wrong schema or version')
    roster = doc['roster']
    if not isinstance(roster, dict):
        return problems + ['$.roster: object required']
    counts = roster.get('counts')
    if not isinstance(counts, dict) or set(counts) != {'shared_native', 'sr1_only', 'sr2_only',
                                                       'excluded', 'rows_scanned'}:
        problems.append('$.roster.counts: exact count keys required')
    else:
        for key in ('shared_native', 'sr1_only', 'sr2_only'):
            values = roster.get(key)
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                problems.append(f'$.roster.{key}: list of canonical ids required')
                continue
            if values != sorted(values) or len(set(values)) != len(values):
                problems.append(f'$.roster.{key}: sorted unique canonical ids required')
            if counts[key] != len(values):
                problems.append(f'$.roster.counts.{key}: does not match the list length')
        if set(roster['shared_native']) & set(roster['sr1_only'] + roster['sr2_only']):
            problems.append('$.roster: buckets must be disjoint')
        for entry in roster.get('excluded') or []:
            if not isinstance(entry, dict) or set(entry) != {'canonical_id', 'classification', 'reasons'}:
                problems.append('$.roster.excluded: entries need canonical_id, classification, reasons')
                break
            if entry['canonical_id'] is not None and not isinstance(entry['canonical_id'], str):
                problems.append('$.roster.excluded: canonical_id must be a string or null')
            reasons = entry['reasons']
            if not isinstance(reasons, list) or not reasons or any(r not in EXCLUSION_REASONS for r in reasons):
                problems.append('$.roster.excluded: reasons must come from the exclusion vocabulary')
                break
        if counts.get('excluded') != len(roster.get('excluded') or []):
            problems.append('$.roster.counts.excluded: does not match the excluded list')
    if not isinstance(roster.get('names'), dict):
        problems.append('$.roster.names: object required')
    if doc.get('collisions'):
        problems.append('$.collisions: target identity collisions present')
    if not isinstance(doc.get('collisions'), list):
        problems.append('$.collisions: list required')
    adapters = doc['adapters']
    if not isinstance(adapters, dict):
        problems.append('$.adapters: object required')
    else:
        for adapter_key, entry in sorted(adapters.items()):
            match = _ADAPTER_ID_RE.fullmatch(adapter_key)
            if match is None or not isinstance(entry, dict) or set(entry) != {
                    'adapter_id', 'direction', 'status', 'validation_problems', 'plort_mode'}:
                problems.append(f'$.adapters.{adapter_key}: malformed adapter entry')
                continue
            if entry['adapter_id'] != adapter_key or entry['direction'] != \
                    f'{match.group(1).upper()}->{match.group(2).upper()}':
                problems.append(f'$.adapters.{adapter_key}: direction mismatch')
            if entry['plort_mode'] not in ('PLORT', 'NONE'):
                problems.append(f'$.adapters.{adapter_key}: plort mode vocabulary required')
    policy = doc['policy']
    if not isinstance(policy, dict) or policy.get('mode') not in foreign_spawn.MODES:
        problems.append('$.policy.mode: NATIVE_ONLY or ALL_IMPORTED required')
    elif not is_sha256(policy.get('document_hash')) or not is_sha256(policy.get('effective_hash')):
        problems.append('$.policy: document and effective hashes required')
    rows = doc['species']
    if not isinstance(rows, list):
        return problems + ['$.species: list required']
    ids = [row.get('canonical_id') if isinstance(row, dict) else None for row in rows]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        problems.append('$.species: rows must be sorted by unique canonical id')
    expected_total = sum(counts[key] for key in ('shared_native', 'sr1_only', 'sr2_only')) \
        if isinstance(counts, dict) and all(isinstance(counts.get(k), int) for k in
                                            ('shared_native', 'sr1_only', 'sr2_only')) else None
    if expected_total is not None and expected_total != len(rows):
        problems.append('$.species: row count does not match the roster counts')
    row_adapter_ids = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != _ROW_KEYS:
            problems.append('$.species[]: exact row keys required')
            continue
        canonical_id = row['canonical_id']
        classification_ = row['classification']
        if classification_ not in ROSTER_CLASSES:
            problems.append(f'{canonical_id}: invalid classification')
            continue
        if '_largo' in str(canonical_id):
            problems.append(f'{canonical_id}: largo ids must never appear in the parity matrix')
        presence = row['presence']
        if not isinstance(presence, dict) or set(presence) != set(REALMS) or \
                any(not isinstance(v, bool) for v in presence.values()):
            problems.append(f'{canonical_id}: presence must map both realms to booleans')
        elif presence != _expected_presence(classification_):
            problems.append(f'{canonical_id}: presence contradicts its classification')
        readable = presence if isinstance(presence, dict) else {}
        for realm in REALMS:
            block = row['native'].get(realm) if isinstance(row['native'], dict) else None
            record_hash = row['record_hashes'].get(realm) if isinstance(row['record_hashes'], dict) else None
            if readable.get(realm) is True:
                if not isinstance(block, dict) or not isinstance(block.get('native_ref'), str) \
                        or not block.get('native_ref') or not is_sha256(block.get('definition_hash')):
                    problems.append(f'{canonical_id}: present realm {realm} needs native_ref and definition_hash')
                if block is not None and not (block.get('numeric_id') is None or type(block.get('numeric_id')) is int):
                    problems.append(f'{canonical_id}: numeric_id must be an int or null')
                if not is_sha256(record_hash):
                    problems.append(f'{canonical_id}: present realm {realm} needs the snapshot record hash')
            else:
                if block is not None or record_hash is not None:
                    problems.append(f'{canonical_id}: absent realm {realm} must carry no native data')
        if classification_ == 'SHARED_NATIVE':
            if row['required_adapter_direction'] is not None or row['adapter'] is not None:
                problems.append(f'{canonical_id}: shared species must not be adapted')
            if row['states'] != {'import': {'state': 'NOT_REQUIRED_NATIVE_BOTH_REALMS'}}:
                problems.append(f'{canonical_id}: shared species import state marker required')
            if row['blockers'] != []:
                problems.append(f'{canonical_id}: shared species cannot carry import blockers')
            if row['foreign_world_spawn'] != {'applicable': False, 'reason': 'NATIVE_BOTH_REALMS'}:
                problems.append(f'{canonical_id}: shared species spawn policy must be not applicable')
        else:
            expected_direction = f'{CLASS_SOURCE[classification_]}->{CLASS_TARGET[classification_]}'
            if row['required_adapter_direction'] != expected_direction:
                problems.append(f'{canonical_id}: wrong required adapter direction')
            adapter = row['adapter']
            if not isinstance(adapter, dict) or set(adapter) != {'adapter_id', 'direction', 'status',
                                                                'validation_problems', 'plort_mode'}:
                problems.append(f'{canonical_id}: exclusive species need an adapter entry')
            else:
                indexed = (adapters or {}).get(adapter['adapter_id'])
                if indexed is None:
                    problems.append(f'{canonical_id}: adapter entry missing from $.adapters')
                elif indexed.get('plort_mode') != row['plort'].get('mode'):
                    # A row may never claim a different plort relation than its prepared descriptor.
                    problems.append(f'{canonical_id}: plort mode contradicts its adapter descriptor')
                if adapter.get('plort_mode') not in ('PLORT', 'NONE') or \
                        adapter.get('plort_mode') != row['plort'].get('mode') or \
                        (indexed is not None and adapter.get('plort_mode') != indexed.get('plort_mode')):
                    # The row-local descriptor duplicates the index entry; both must stay coherent.
                    problems.append(f'{canonical_id}: row adapter descriptor must agree with the plort '
                                    'mapping and the index')
                row_adapter_ids.add(adapter['adapter_id'])
                if adapter['status'] != 'EXPERIMENTAL' or adapter['validation_problems'] != 0:
                    problems.append(f'{canonical_id}: adapter is not EXPERIMENTAL-clean')
            spawn = row['foreign_world_spawn']
            if not isinstance(spawn, dict) or spawn.get('applicable') is not True or \
                    spawn.get('active') is not False or spawn.get('mode') not in foreign_spawn.MODES or \
                    spawn.get('integration') != 'BLOCKED_ON_LOCAL_QUERY' or \
                    spawn.get('local_query') != foreign_spawn.SPAWN_INTEGRATION_QUERY or \
                    spawn.get('requested') not in (True, False):
                problems.append(f'{canonical_id}: foreign spawn policy must stay inactive on Q034')
            problems.extend(_validate_exclusive_states(canonical_id, row))
            problems.extend(_validate_plort(canonical_id, row))
            if not isinstance(row['blockers'], list) or \
                    any(not isinstance(b, dict) or set(b) != {'code', 'local_query'} or
                        b.get('code') not in BLOCKER_CODES for b in row['blockers']):
                problems.append(f'{canonical_id}: blockers must use the blocker vocabulary')
        evidence = row['evidence']
        if not isinstance(evidence, dict) or set(evidence) != {'data_evidence', 'identity_basis',
                                                              'identity_evidence', 'source_row_sha256'} \
                or not is_sha256(evidence.get('source_row_sha256')):
            problems.append(f'{canonical_id}: evidence block required with the source row hash')
    if isinstance(adapters, dict):
        if row_adapter_ids != set(adapters):
            problems.append('$.adapters: adapter index must exactly match the prepared descriptors')
        roster_buckets = {'SR1->SR2': set(roster.get('sr1_only') or []),
                          'SR2->SR1': set(roster.get('sr2_only') or [])}
        for adapter_key in sorted(adapters):
            match = _ADAPTER_ID_RE.fullmatch(adapter_key)
            if match is None:
                continue
            direction = f'{match.group(1).upper()}->{match.group(2).upper()}'
            if match.group(3) not in roster_buckets.get(direction, set()):
                problems.append(f'$.adapters.{adapter_key}: adapter outside the exclusive roster')
    if isinstance(counts, dict) and isinstance(counts.get('rows_scanned'), int) and \
            expected_total is not None and counts['rows_scanned'] < expected_total:
        problems.append('$.roster.counts.rows_scanned: inconsistent')
    if _collect_queries(doc) != doc.get('blocked_queries'):
        problems.append('$.blocked_queries: does not match the queries referenced by the rows')
    if isinstance(doc.get('blocked_queries'), list) and \
            doc['blocked_queries'] != sorted(set(doc['blocked_queries'])):
        problems.append('$.blocked_queries: sorted unique query ids required')
    problems.extend(_walk_forbidden(doc))
    return problems


def _validate_plort(canonical_id, row):
    problems = []
    plort = row['plort']
    if not isinstance(plort, dict):
        return [f'{canonical_id}: plort object required']
    if row['classification'] == 'SHARED_NATIVE':
        if plort.get('target_status') != 'NATIVE_BOTH_REALMS' or plort.get('local_query') is not None:
            problems.append(f'{canonical_id}: shared plort mapping must be native in both realms')
        if plort.get('mode') == 'PLORT' and not isinstance(plort.get('canonical_id'), str):
            problems.append(f'{canonical_id}: shared single-plort mapping needs its canonical id')
        if plort.get('mode') == 'AMBIGUOUS' and len(plort.get('canonical_ids') or []) < 2:
            problems.append(f'{canonical_id}: ambiguous plort mapping needs every canonical id')
        return problems
    mode = plort.get('mode')
    status = plort.get('target_status')
    if status not in PLORT_STATUSES or status == 'NATIVE_BOTH_REALMS':
        problems.append(f'{canonical_id}: imported plort status invalid')
    if mode == 'NONE':
        if plort.get('canonical_id') is not None or plort.get('source') is not None or status != 'NONE':
            problems.append(f'{canonical_id}: no-plort species must carry no plort identity')
    elif mode == 'PLORT':
        if not isinstance(plort.get('canonical_id'), str) or PLORT_ID_RE.fullmatch(plort['canonical_id']) is None:
            problems.append(f'{canonical_id}: canonical plort id required')
        source = plort.get('source')
        if not isinstance(source, dict) or not isinstance(source.get('native_ref'), str) or \
                not source.get('native_ref'):
            problems.append(f'{canonical_id}: plort source ref required')
        if status == 'PENDING_CAPTURE' and not plort.get('local_query'):
            problems.append(f'{canonical_id}: pending plort presence needs its local query')
        if status == 'CLASSIFIED_ABSENT' and plort.get('local_query') is not None:
            problems.append(f'{canonical_id}: classified plort presence carries no pending query')
    elif mode in ('AMBIGUOUS', 'QUARANTINED'):
        pass
    else:
        problems.append(f'{canonical_id}: plort mode invalid')
    return problems


def _validate_exclusive_states(canonical_id, row):
    problems = []
    states = row['states']
    expected_keys = {'runtime_registration', 'inventory', 'release', 'save_load', 'diet', 'behavior',
                     'economy', 'plort', 'visual'}
    if not isinstance(states, dict) or set(states) != expected_keys:
        return [f'{canonical_id}: exact runtime state keys required']
    if any(not isinstance(states[key], dict) for key in sorted(expected_keys)):
        return [f'{canonical_id}: runtime state entries must be objects']
    runtime = states['runtime_registration']
    if runtime.get('state') not in ('PLAN_READY', 'QUARANTINED') or \
            runtime.get('execution') != 'BLOCKED_ON_LOCAL_QUERY' or not runtime.get('local_query'):
        problems.append(f'{canonical_id}: runtime registration must stay plan-ready behind a query')
    for key in ('inventory', 'release', 'diet', 'economy'):
        entry = states[key]
        if entry.get('state') != 'BLOCKED_ON_LOCAL_QUERY' or not entry.get('local_query'):
            problems.append(f'{canonical_id}: {key} must stay BLOCKED_ON_LOCAL_QUERY with its query')
    save = states['save_load']
    if save.get('state') != 'SCHEMA_READY' or save.get('native_binding') != 'BLOCKED_ON_LOCAL_QUERY' or \
            save.get('serialization_schema_version') != 1 or not save.get('local_query'):
        problems.append(f'{canonical_id}: save/load must be schema-ready with native binding blocked')
    behavior = states['behavior']
    if behavior.get('classification') not in BEHAVIOR_CLASSES or not behavior.get('local_query'):
        problems.append(f'{canonical_id}: behavior classification must stay on its query')
    plort_state = states['plort']
    if plort_state.get('state') not in PLORT_STATUSES or plort_state.get('state') == 'NATIVE_BOTH_REALMS':
        problems.append(f'{canonical_id}: plort state invalid')
    elif plort_state.get('state') != row['plort'].get('target_status') or \
            plort_state.get('local_query') != row['plort'].get('local_query'):
        problems.append(f'{canonical_id}: plort state must mirror the plort mapping')
    if plort_state.get('state') == 'PENDING_CAPTURE' and not plort_state.get('local_query'):
        problems.append(f'{canonical_id}: pending plort state needs its query')
    visual = states['visual']
    if visual.get('state') != 'VISUAL_ASSET_BLOCKED' or visual.get('evidence') != 'NEEDS_LOCAL_VISUAL_PROOF':
        problems.append(f'{canonical_id}: visual state must stay VISUAL_ASSET_BLOCKED')
    return problems


def _walk_forbidden(doc):
    problems = []
    stack = [(doc, '$')]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                if key in PAYLOAD_KEYS:
                    problems.append(f'{path}.{key}: forbidden payload key')
                stack.append((child, f'{path}.{key}'))
        elif isinstance(node, list):
            stack.extend((child, f'{path}[{i}]') for i, child in enumerate(node))
        elif isinstance(node, float):
            problems.append(f'{path}: floats are not allowed (deterministic metadata only)')
    return problems
