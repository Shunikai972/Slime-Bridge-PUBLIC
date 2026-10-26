"""Cross-game species adapter descriptors (``bridge-species-adapter`` v2).

One realm-neutral, metadata-only descriptor per prepared species, so the later visual phase
attaches meshes, materials and icons without rewriting behaviour, registration, economy or
persistence logic. This module ASSEMBLES and VALIDATES descriptors from already-reviewed
inputs; it never registers anything natively and never carries asset payloads.

The full exclusive base-species roster is prepared (every captured ``SR1_ONLY`` species
becomes one SR1->SR2 descriptor, every ``SR2_ONLY`` species one SR2->SR1 descriptor); the
roster itself is derived by ``bridge.conversion.species_parity``, never hardcoded. Species
whose captured definition produces no plort use ``plort.mode == 'NONE'`` instead of
inventing one.

Inputs:
  * ``bridge-classification`` rows prove exclusivity, identity evidence and native presence;
  * runtime registry snapshots (real captures) provide native refs, numeric ids, definition
    hashes, largo relations and produced plorts;
  * the reviewed target profile (``bridge.conversion.targets``) provides registration
    operations; anything without a verified native API stays ``BLOCKED_ON_LOCAL_QUERY``,
    exactly like the conversion engine.

Every descriptor ends with ``visual.status == 'VISUAL_ASSET_BLOCKED'`` until the dedicated
visual phase fills the visual sections. Overall status is capped at EXPERIMENTAL
(QUARANTINED on hard errors), mirroring the engine.
"""
from __future__ import annotations

import re

from bridge.content.canonical import (DataEvidence, Evidence, REALMS, Status, canonical_json,
                                      diag, is_sha256, sha256_json, sort_diagnostics)
from bridge.content.ir import PAYLOAD_KEYS
from . import targets

ADAPTER_SCHEMA = 'bridge-species-adapter'
ADAPTER_SCHEMA_VERSION = 2
DIRECTIONS = (('SR1', 'SR2'), ('SR2', 'SR1'))
VISUAL_STATUS = 'VISUAL_ASSET_BLOCKED'
PLORT_ID_PATTERN = r'^plort\.[a-z0-9]+(?:_[a-z0-9]+)*$'
PLORT_ID_RE = re.compile(PLORT_ID_PATTERN)

#: Which registration operation implements which adapter section (later phases consume this).
OP_SECTIONS = {
    'RESERVE_CANONICAL_IDENTITY': 'identity',
    'REGISTER_SLIME_DEFINITION': 'identity',
    'REGISTER_LOCALIZATION': 'identity',
    'REGISTER_PEDIA': 'identity',
    'REGISTER_DIET': 'diet',
    'REGISTER_PLORT': 'plort',
    'REFERENCE_NATIVE_PLORT': 'plort',
    'REGISTER_MARKET': 'economy',
    'ATTACH_BEHAVIOUR': 'spawn',
    'BIND_PERSISTENCE': 'save',
    'REGISTER_ICON': 'visual',
    'BUILD_MATERIALS': 'visual',
    'BUILD_PREFAB': 'visual',
}
VISUAL_ASSETS = ('slime_mesh', 'slime_materials', 'slime_face', 'slime_icon',
                 'plort_mesh', 'plort_materials', 'plort_icon')

#: Native-shaped keys that must never appear in the target sections (fail closed).
_FORBIDDEN_TARGET_KEYS = frozenset({'numeric_id', 'native_id', 'slot_index', 'save_id', 'instance_id'})


def adapter_id(source_realm: str, target_realm: str, canonical_id: str) -> str:
    return f'adapter.{source_realm.lower()}-to-{target_realm.lower()}.{canonical_id}'


def _snapshot_rows(snapshot: dict) -> list:
    """Snapshots carry rows under ``definitions`` (fixtures) or ``entries`` (live captures)."""
    for key in ('definitions', 'entries'):
        rows = snapshot.get(key)
        if isinstance(rows, list):
            return rows
    return []


def find_snapshot_row(snapshot: dict, native_ref: str) -> dict | None:
    """The captured row for one native definition ref (exact native_ref/reference_id match)."""
    for row in _snapshot_rows(snapshot):
        if not isinstance(row, dict):
            continue
        if (row.get('reference_id') == native_ref or row.get('native_ref') == native_ref or
                str(row.get('object_ref', '')).endswith(':' + native_ref)):
            return row
    return None


def _blocked(what: str, query: str, detail: str) -> dict:
    return {'status': str(Evidence.BLOCKED_ON_LOCAL_QUERY), 'what': what, 'local_query': query,
            'detail': detail}


def plort_slug_agrees(canonical_id: str, native_ref: str) -> bool:
    """Canonical-to-native crosswalk for one plort: the native name's slug must match.

    ``IdentifiableType.RingtailPlort`` -> ``ringtail``, ``RAD_PLORT`` -> ``rad``; both equal
    the slug of ``plort.ringtail`` / ``plort.rad`` after normalization. This is the only
    available corroboration when the plorts bucket was not captured.
    """
    if not isinstance(canonical_id, str) or not canonical_id.startswith('plort.') or not isinstance(native_ref, str):
        return False
    slug = canonical_id.split('.', 1)[1]
    name = native_ref.rsplit('.', 1)[-1].rsplit(':', 1)[-1]
    for suffix in ('Plort', '_PLORT', 'plort'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    return re.sub(r'[^a-z0-9]', '', name.lower()) == re.sub(r'[^a-z0-9]', '', slug.lower())


def build_adapter(classification: dict, source_realm: str, target_realm: str, canonical_id: str,
                  snapshot: dict) -> dict:
    """Assemble one descriptor; returns the document (diagnostics inside). Never raises for
    data problems: hard problems set ``status=QUARANTINED`` and error diagnostics."""
    problems: list = []
    if (source_realm, target_realm) not in DIRECTIONS:
        raise ValueError(f'unsupported direction {source_realm}->{target_realm}')
    if snapshot.get('realm') != source_realm:
        raise ValueError('snapshot realm does not match the source realm')
    if not isinstance(canonical_id, str) or canonical_id.count('.') < 1:
        raise ValueError('canonical id must be realm-neutral (e.g. slime.ringtail)')

    row = next((r for r in classification.get('species', []) if r.get('canonical_id') == canonical_id), None)
    if row is None:
        raise ValueError(f'{canonical_id} is not in the classification species table')
    expected_class = f'{source_realm}_ONLY'
    if row.get('classification') != expected_class:
        problems.append(diag('ADAPTER_NOT_EXCLUSIVE', 'error', canonical_id,
                             f'classification is {row.get("classification")!r}, expected {expected_class!r}'))
    present_map = row.get('present') or {}
    raw_present = present_map.get(target_realm)
    if isinstance(raw_present, bool):
        target_present = raw_present
    elif type(raw_present) is int and raw_present in (0, 1):
        target_present = bool(raw_present)  # captures serialize presence as 0/1
    else:
        target_present = None
    if target_present is True:
        problems.append(diag('ADAPTER_TARGET_PRESENT', 'error', canonical_id,
                             f'classification claims {expected_class} but the target realm marks it present'))
    elif target_present is not False:
        # A missing/null target entry proves nothing; absence must be captured explicitly.
        problems.append(diag('ADAPTER_TARGET_PRESENCE_UNCAPTURED', 'error', canonical_id,
                             'classification does not prove the target realm is free of this species'))
    native = (row.get('native') or {}).get(source_realm)
    if not isinstance(native, dict):
        problems.append(diag('ADAPTER_MISSING_SOURCE', 'error', canonical_id, 'no native source entry'))
        native = {}
    elif native.get('role') != 'base':
        problems.append(diag('ADAPTER_NOT_BASE', 'error', canonical_id,
                             f'role is {native.get("role")!r}, only base slimes are prepared'))
    elif native.get('quarantine') is not None or native.get('malformed'):
        problems.append(diag('ADAPTER_SOURCE_QUARANTINED', 'error', canonical_id,
                             'quarantined or malformed source definition'))
    if not native.get('native_ref') or not native.get('definition_hash'):
        problems.append(diag('ADAPTER_INCOMPLETE_SOURCE', 'error', canonical_id,
                             'source requires native_ref and definition_hash'))
    elif not is_sha256(native['definition_hash']):
        problems.append(diag('ADAPTER_BAD_HASH', 'error', canonical_id, 'definition_hash is not a sha256'))

    definition = find_snapshot_row(snapshot, native.get('native_ref') or '') if native.get('native_ref') else None
    if definition is None and not problems:
        problems.append(diag('ADAPTER_SNAPSHOT_ROW_MISSING', 'error', canonical_id,
                             'source snapshot has no row for the native ref'))

    produces = [ref for ref in (native.get('produces') or []) if isinstance(ref, str)]
    definition_produces = [ref for ref in (definition or {}).get('produces', [])
                           if isinstance(ref, str)] if isinstance(definition, dict) else []
    #: A captured no-plort species ('NONE') never invents a plort; a species whose captures
    #: disagree (definition refs / plorts bucket) quarantines instead of guessing.
    no_plort = not produces
    plort_row, plort_native, presence_pending = None, {}, False
    plort_present = None
    if not produces:
        if definition_produces:
            problems.append(diag('ADAPTER_PLORT_PRESENCE_MISMATCH', 'error', canonical_id,
                                 'classification lists no produced plort but the source definition names refs'))
        species_slug = re.sub(r'[^a-z0-9]', '', canonical_id.split('.', 1)[1].lower())
        for candidate in classification.get('plorts', []):
            plort_id = candidate.get('canonical_id') if isinstance(candidate, dict) else None
            if isinstance(plort_id, str) and plort_id.startswith('plort.') and \
                    re.sub(r'[^a-z0-9]', '', plort_id.split('.', 1)[1].lower()) == species_slug:
                problems.append(diag('ADAPTER_PLORT_PRESENCE_MISMATCH', 'error', canonical_id,
                                     'the plorts bucket has a plort row for this species but the '
                                     'captured definition produces nothing'))
                break
        if not definition_produces:
            problems.append(diag('ADAPTER_NO_PLORT_SPECIES', 'info', canonical_id,
                                 'source species produces no plort; no plort registration is planned'))
    elif len(produces) != 1:
        problems.append(diag('ADAPTER_PLORT_CARDINALITY', 'error', canonical_id,
                             f'expected exactly one produced plort, saw {len(produces)}'))
    else:
        plort_row = next((r for r in classification.get('plorts', [])
                          if r.get('canonical_id') == produces[0]), None)
        if plort_row is not None:
            raw_present = (plort_row.get('present') or {}).get(target_realm)
            if isinstance(raw_present, bool):
                plort_present = raw_present
            elif type(raw_present) is int and raw_present in (0, 1):
                plort_present = bool(raw_present)
            else:
                plort_present = None
            if plort_present is True:
                problems.append(diag('ADAPTER_TARGET_ALREADY_NATIVE', 'error', produces[0],
                                     'the target realm already has this plort natively'))
            elif plort_present is not False:
                problems.append(diag('ADAPTER_PLORT_PRESENCE_UNCAPTURED', 'error', produces[0],
                                     'the plort row does not prove the target realm is free'))
                presence_pending = True
            plort_native = ((plort_row.get('native') or {}).get(source_realm)) or {}
            if not plort_native.get('native_ref'):
                problems.append(diag('ADAPTER_PLORT_INCOMPLETE', 'error', produces[0],
                                     'source plort requires native_ref'))
            elif not plort_slug_agrees(produces[0], plort_native['native_ref']):
                problems.append(diag('ADAPTER_PLORT_CROSSWALK_MISMATCH', 'error', produces[0],
                                     'canonical plort id and native ref do not identify the same item'))
            # The source slime definition must also name that same plort; a captured plort row
            # alone is not a crosswalk.
            if isinstance(definition, dict):
                def_produces = [ref for ref in definition.get('produces', []) if isinstance(ref, str)]
                if plort_native.get('native_ref') not in def_produces:
                    problems.append(diag('ADAPTER_PLORT_CROSSWALK_MISMATCH', 'error', produces[0],
                                         'the source definition does not produce the plort row ref'))
        else:
            # The plorts bucket was not captured in this run. The definition's produces relation
            # still names the native plort ref; target presence stays unproven, so every native
            # use of it remains blocked on the deeper capture instead of claiming absence.
            presence_pending = True
            source_produces = [ref for ref in (definition or {}).get('produces', [])
                               if isinstance(ref, str)]
            plort_native = {'native_ref': source_produces[0] if len(source_produces) == 1 else None,
                            'numeric_id': None, 'definition_hash': None}
            if len(source_produces) != 1:
                problems.append(diag('ADAPTER_PLORT_SOURCE_AMBIGUOUS', 'error', produces[0],
                                     f'definition names {len(source_produces)} produced refs; '
                                     'exactly one is required for an unambiguous mapping'))
            elif not plort_slug_agrees(produces[0], source_produces[0]):
                problems.append(diag('ADAPTER_PLORT_CROSSWALK_MISMATCH', 'error', produces[0],
                                     'canonical plort id and the definition-produced ref do not '
                                     'identify the same item'))
            problems.append(diag('PLORT_ROW_PENDING_CAPTURE', 'warning', produces[0],
                                 'plort rows are not in this capture; identity comes from the '
                                 'definition produces relation'))
    plort_present_bool = plort_present if isinstance(plort_present, bool) else None

    target_profile = targets.builtin_target_profile(target_realm)
    registration = target_profile['registration']
    plan = []
    for op in targets.REGISTRATION_OPS:
        entry = dict(registration[op])
        entry['operation'] = op
        entry['section'] = OP_SECTIONS[op]
        plan.append(entry)

    prefab_refs = []
    if isinstance(definition, dict):
        prefab_refs = [ref for ref in definition.get('prefab_refs', []) if isinstance(ref, str)]

    doc = {
        'schema': ADAPTER_SCHEMA,
        'schema_version': ADAPTER_SCHEMA_VERSION,
        'adapter_id': adapter_id(source_realm, target_realm, canonical_id),
        'direction': {'source_realm': source_realm, 'target_realm': target_realm},
        'canonical_id': canonical_id,
        'classification': row.get('classification'),
        'identity': {
            'canonical_id': canonical_id,
            'identity_basis': (row.get('identity_basis') or {}).get(source_realm),
            'identity_evidence': (row.get('identity_evidence') or {}).get(source_realm, []),
            'source': {'realm': source_realm, 'native_ref': native.get('native_ref'),
                       'numeric_id': native.get('numeric_id'),
                       'definition_hash': native.get('definition_hash'),
                       'display_name': native.get('display_name'),
                       'role': native.get('role')},
            'target': {'realm': target_realm,
                       'native_present': target_present if isinstance(target_present, bool) else None,
                       'reserved_canonical_id': canonical_id,
                       'registration_evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY)},
            'data_evidence': str(DataEvidence.RUNTIME_ANCHORED),
        },
        'diet': _blocked('diet groups and favourites', registration['REGISTER_DIET']['local_query'],
                         'source snapshot does not carry Diet food groups/favourites yet; a deeper '
                         'runtime capture answers this without guessing'),
        'plort': ({
            'mode': 'NONE',
            'canonical_id': None,
            'source': {'realm': source_realm, 'native_ref': None, 'numeric_id': None,
                       'definition_hash': None},
            'production_relation': 'none',
            'target_native_present': None,
            'presence_evidence': 'NOT_APPLICABLE',
            'local_query': None,
            'data_evidence': str(DataEvidence.RUNTIME_ANCHORED),
        } if no_plort else {
            'mode': 'PLORT',
            'canonical_id': produces[0] if len(produces) == 1 else None,
            'source': {'realm': source_realm, 'native_ref': plort_native.get('native_ref'),
                       'numeric_id': plort_native.get('numeric_id'),
                       'definition_hash': plort_native.get('definition_hash')},
            'production_relation': 'produces',
            'target_native_present': plort_present_bool,
            'presence_evidence': 'PENDING_CAPTURE' if presence_pending else 'CLASSIFIED',
            'local_query': registration['REGISTER_PLORT']['local_query'] if presence_pending else None,
            'data_evidence': str(DataEvidence.RUNTIME_ANCHORED),
        }),
        'economy': _blocked('plort market value and registration', registration['REGISTER_MARKET']['local_query'],
                            'market value is native data; registration requires the verified market API'),
        'inventory': {
            'vacpack_identity': str(Evidence.BLOCKED_ON_LOCAL_QUERY),
            'local_query': registration['BUILD_PREFAB']['local_query'],
            'items': ([{'kind': 'slime', 'canonical_id': canonical_id}] if no_plort else
                      [{'kind': 'slime', 'canonical_id': canonical_id},
                       {'kind': 'plort', 'canonical_id': produces[0] if len(produces) == 1 else None}]),
            'notes': 'canonical item ids only; vacpack slot wiring is native registration work',
        },
        'spawn': _blocked('spawn registration', registration['ATTACH_BEHAVIOUR']['local_query'],
                          'spawn placement and behaviour attachment need the verified runtime API'),
        'save': {
            'persistence_identity': str(Evidence.BLOCKED_ON_LOCAL_QUERY),
            'local_query': registration['BIND_PERSISTENCE']['local_query'],
            'serialization_schema_version': 1,
            'notes': 'Bridge-owned serialization stays versioned; native save binding needs the '
                     'verified persistence API',
        },
        'target_registration': plan,
        'visual': {
            'status': VISUAL_STATUS,
            'evidence': str(Evidence.NEEDS_LOCAL_VISUAL_PROOF),
            'required_assets': list(VISUAL_ASSETS),
            'source_asset_refs': prefab_refs,
            'guidance': 'attach in the later visual phase; prefer reusing local SR1 decompiled '
                        'assets where suitable instead of authoring new visuals',
        },
        'versions': {'adapter': ADAPTER_SCHEMA_VERSION,
                     'classification_schema': classification.get('schema_version'),
                     'snapshot_schema': snapshot.get('schema_version'),
                     'target_profile': target_profile['schema_version']},
        'hashes': {
            'classification_row': sha256_json(row),
            'snapshot_definition': sha256_json(definition) if isinstance(definition, dict) else None,
            'registration_table': sha256_json(registration),
        },
        'diagnostics': [],
        'status': None,
    }
    doc['diagnostics'] = sort_diagnostics(problems)
    doc['status'] = str(Status.QUARANTINED if any(d['severity'] == 'error' for d in doc['diagnostics'])
                        else Status.EXPERIMENTAL)
    return doc


_TOP_KEYS = frozenset({'schema', 'schema_version', 'adapter_id', 'direction', 'canonical_id',
                       'classification', 'identity', 'diet', 'plort', 'economy', 'inventory',
                       'spawn', 'save', 'target_registration', 'visual', 'versions', 'hashes',
                       'diagnostics', 'status'})
#: Exact allowed keys per section; the source sections are the ONLY places native identifiers
#: may appear, and only as the exact paths listed here.
_SECTION_KEYS = {
    '$.direction': {'source_realm', 'target_realm'},
    '$.identity': {'canonical_id', 'identity_basis', 'identity_evidence', 'source', 'target',
                   'data_evidence'},
    '$.identity.source': {'realm', 'native_ref', 'numeric_id', 'definition_hash', 'display_name',
                          'role'},
    '$.identity.target': {'realm', 'native_present', 'reserved_canonical_id',
                          'registration_evidence'},
    '$.plort': {'mode', 'canonical_id', 'source', 'production_relation', 'target_native_present',
                'presence_evidence', 'local_query', 'data_evidence'},
    '$.plort.source': {'realm', 'native_ref', 'numeric_id', 'definition_hash'},
    '$.visual': {'status', 'evidence', 'required_assets', 'source_asset_refs', 'guidance'},
}
_PLAN_ROW_KEYS = {'api', 'evidence', 'local_query', 'operation', 'section'}


def validate_adapter(doc) -> list:
    """Structural + policy validation; returns problem strings (never raises).

    An empty list means the descriptor is well-formed AND eligible: EXPERIMENTAL, exclusive,
    base-role, target-absent, non-native plort, payload-free, with every registration op still
    blocked behind a local query and the visual asset marker present. QUARANTINED descriptors
    never validate clean.
    """
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
    if doc['schema'] != ADAPTER_SCHEMA or doc['schema_version'] != ADAPTER_SCHEMA_VERSION:
        problems.append('$.schema: wrong schema or version')
    direction = doc['direction']
    if (not isinstance(direction, dict) or
            (direction.get('source_realm'), direction.get('target_realm')) not in DIRECTIONS):
        problems.append('$.direction: unsupported direction')
        return problems
    source_realm, target_realm = direction['source_realm'], direction['target_realm']
    if doc['adapter_id'] != adapter_id(source_realm, target_realm, str(doc['canonical_id'])):
        problems.append('$.adapter_id: does not match direction and species')
    if doc['classification'] != f'{source_realm}_ONLY':
        problems.append('$.classification: descriptor is only for exclusive source species')
    if doc['status'] not in (str(Status.EXPERIMENTAL), str(Status.QUARANTINED)):
        problems.append('$.status: adapters are capped at EXPERIMENTAL/QUARANTINED')
    diagnostics = doc['diagnostics']
    if (not isinstance(diagnostics, list) or
            any(not isinstance(d, dict) or not d.get('code') or
                d.get('severity') not in ('error', 'warning', 'info') for d in diagnostics)):
        problems.append('$.diagnostics: list of {code, severity} diagnostics required')
    else:
        has_error = any(d['severity'] == 'error' for d in diagnostics)
        if doc['status'] == str(Status.EXPERIMENTAL) and has_error:
            problems.append('$.status: EXPERIMENTAL descriptor with error diagnostics')
        if doc['status'] == str(Status.QUARANTINED):
            problems.append('$.status: quarantined descriptor cannot be used')
            if not has_error:
                problems.append('$.diagnostics: quarantined descriptor without an error diagnostic')
    identity = doc['identity']
    if not isinstance(identity, dict):
        problems.append('$.identity: object required')
    else:
        source = identity.get('source')
        if not isinstance(source, dict) or source.get('role') != 'base':
            problems.append('$.identity.source.role: only base-role species may be prepared')
        target = identity.get('target')
        if not isinstance(target, dict) or target.get('native_present') is not False:
            problems.append('$.identity.target.native_present: the species must be absent from the target')
    # No asset payloads anywhere, and no native target identifiers outside the two exact source
    # sections (source native ids are captured truth and stay allowed ONLY there).
    stack = [(doc, '$')]
    while stack:
        node, path = stack.pop()
        if isinstance(node, dict):
            allowed = _SECTION_KEYS.get(path)
            if allowed is not None:
                for key in sorted(set(node) - allowed):
                    problems.append(f'{path}.{key}: unknown field for this section')
            if path.startswith('$.target_registration['):
                for key in sorted(set(node) - _PLAN_ROW_KEYS):
                    problems.append(f'{path}.{key}: unknown field for this section')
            source_section = path in ('$.identity.source', '$.plort.source')
            for key, child in node.items():
                if key in PAYLOAD_KEYS or (key in _FORBIDDEN_TARGET_KEYS and not source_section):
                    problems.append(f'{path}.{key}: forbidden key')
                stack.append((child, f'{path}.{key}'))
        elif isinstance(node, list):
            stack.extend((child, f'{path}[{i}]') for i, child in enumerate(node))
        elif isinstance(node, float):
            problems.append(f'{path}: floats are not allowed (deterministic metadata only)')
    plan = doc['target_registration']
    if not isinstance(plan, list) or [row.get('operation') for row in plan] != list(targets.REGISTRATION_OPS):
        problems.append('$.target_registration: must list every registration operation in order')
    else:
        for row in plan:
            if row.get('api') is not None or not row.get('local_query'):
                problems.append(f'$.target_registration.{row.get("operation")}: native api unresolved '
                                'entries must stay api=None with a local_query')
    visual = doc['visual']
    if not isinstance(visual, dict) or visual.get('status') != VISUAL_STATUS:
        problems.append('$.visual: missing VISUAL_ASSET_BLOCKED marker')
    plort = doc['plort']
    mode = plort.get('mode') if isinstance(plort, dict) else None
    if mode == 'NONE':
        problems.extend(_validate_no_plort(plort))
    elif mode == 'PLORT':
        problems.extend(_validate_plort(plort))
    else:
        problems.append('$.plort.mode: PLORT or NONE required')
    if not _inventory_matches(doc):
        problems.append('$.inventory.items: must mirror the plort mode and canonical ids')
    return problems


def _validate_plort(plort) -> list:
    problems = []
    if not isinstance(plort.get('canonical_id'), str) or PLORT_ID_RE.fullmatch(plort['canonical_id'] or '') is None:
        problems.append('$.plort.canonical_id: canonical plort id required')
    if (not isinstance(plort.get('source'), dict) or
            not isinstance(plort['source'].get('native_ref'), str) or not plort['source']['native_ref']):
        problems.append('$.plort.source.native_ref: source plort ref required')
    if plort.get('production_relation') != 'produces':
        problems.append('$.plort.production_relation: produces required for plort species')
    value = plort.get('target_native_present')
    evidence = plort.get('presence_evidence')
    if value is not True and value is not False and value is not None:
        # Type-strict: false-like values (0, '') must never pass as proven absence.
        problems.append('$.plort.target_native_present: true, false or null required')
    elif evidence not in ('CLASSIFIED', 'PENDING_CAPTURE'):
        problems.append('$.plort.presence_evidence: CLASSIFIED or PENDING_CAPTURE required')
    elif value is True:
        problems.append('$.plort.target_native_present: native plorts must never be adapted')
    elif value is False and evidence != 'CLASSIFIED':
        problems.append('$.plort.presence_evidence: proven absence requires CLASSIFIED evidence')
    elif value is None and evidence != 'PENDING_CAPTURE':
        problems.append('$.plort: unknown target presence must carry the PENDING_CAPTURE marker')
    return problems


def _validate_no_plort(plort) -> list:
    """A no-plort species must prove emptiness: no id, no source ref, no pending query."""
    problems = []
    if plort.get('canonical_id') is not None:
        problems.append('$.plort.canonical_id: no-plort species must not carry a plort id')
    source = plort.get('source')
    if not isinstance(source, dict) or source.get('native_ref') is not None:
        problems.append('$.plort.source: no-plort species must not carry a source ref')
    if plort.get('production_relation') != 'none':
        problems.append('$.plort.production_relation: none required for no-plort species')
    if plort.get('target_native_present') is not None:
        problems.append('$.plort.target_native_present: null required for no-plort species')
    if plort.get('presence_evidence') != 'NOT_APPLICABLE':
        problems.append('$.plort.presence_evidence: NOT_APPLICABLE required for no-plort species')
    if plort.get('local_query') is not None:
        problems.append('$.plort.local_query: no local query applies to a no-plort species')
    return problems


def _inventory_matches(doc) -> bool:
    """The canoncial item list must mirror the plort mode exactly (slime, plus plort if any)."""
    items = (doc.get('inventory') or {}).get('items')
    canonical_id = doc.get('canonical_id')
    plort = doc.get('plort') or {}
    if plort.get('mode') == 'NONE':
        return items == [{'kind': 'slime', 'canonical_id': canonical_id}]
    return items == [{'kind': 'slime', 'canonical_id': canonical_id},
                     {'kind': 'plort', 'canonical_id': plort.get('canonical_id')}]
