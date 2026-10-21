"""Static, deterministic mod compatibility analysis -> ``bridge-mod-compat-report`` v1.

Inputs are a ``bridge-mod-manifest`` (``manifest.py``), optional LOCAL static evidence
(``evidence.py``) and a ``known_apis`` catalogue (``known_apis.json``). Nothing is executed, loaded,
decompiled or downloaded. The analysis never upgrades a claim: without bound evidence the answer is
UNKNOWN with low confidence, and every portability verdict stays NEEDS_LOCAL_RUNTIME_PROOF (or
BLOCKED_ON_LOCAL_QUERY when a local query is listed).

Classification: the first matching rule wins. Rules R1-R7 only see evidence that is schema-valid
AND bound to the manifest (same mod id, same file hashes, every manifest assembly analyzed).

======  ==============================================================================  ==================
Rule    Condition                                                                       Classification
======  ==============================================================================  ==================
R0      no evidence, invalid evidence, or evidence not bound to this manifest            UNKNOWN
R1      a native indicator: P/Invoke module, detour indicator or function-pointer hook   NATIVE_DEEP_PATCH
R2      a Harmony patch entry or a PatchAll call                                         HARMONY_PATCH
R3      own code; every loader/game/interop reference is catalogued in known_apis; no    KNOWN_API_GAMEPLAY
        foreign-loader or undeclared third-party assembly; no reflection flag
R4      own code, anything else (uncatalogued surface, reflection, foreign loader...)     UNKNOWN
R5      no code; asset bundles, content files or content ids                             CONTENT_ONLY
R6      no code, no content; configuration files                                         CONFIG_ONLY
R7      nothing at all                                                                   UNKNOWN
======  ==============================================================================  ==================

Portability (``target_realm`` defaults to the source realm):

==================  ===========================================  ===============================================
Classification      Same realm                                   Cross realm
==================  ===========================================  ===============================================
CONFIG_ONLY         PORTABLE                                     ADAPTER_REQUIRED (config.schema_mapping)
CONTENT_ONLY        PORTABLE                                     ADAPTER_REQUIRED (content conversion+registration)
KNOWN_API_GAMEPLAY  PORTABLE                                     ADAPTER_REQUIRED; MANUAL_PORT if a row is MANUAL
HARMONY_PATCH       PORTABLE (targets unverified: Q055)          MANUAL_PORT
NATIVE_DEEP_PATCH   PORTABLE but blocked (NATIVE_HOOKS_UNREVIEWED)  NOT_PORTABLE
UNKNOWN             UNKNOWN                                      UNKNOWN
==================  ===========================================  ===============================================

A cross-realm verdict is never better than its worst capability row: UNSUPPORTED -> NOT_PORTABLE,
MANUAL -> MANUAL_PORT. A manifest with loader UNKNOWN is UNKNOWN portability (LOADER_UNKNOWN).

Confidence is integer points /100 with one reason per rule applied (``CONFIDENCE_POINTS``), capped at
0.85 for static analysis and at 0.30 for an UNKNOWN classification. No evidence -> 0.10.

Deploy-unit status (``unit_status``): QUARANTINED for NATIVE_DEEP_PATCH, NOT_PORTABLE or an invalid
manifest; READY only for PORTABLE + same realm + no blocker + no open local query; EXPERIMENTAL for
PORTABLE + same realm + no blocker but open local queries; PENDING_ADAPTER otherwise.
"""

import heapq
import re
from pathlib import Path

from bridge.content.canonical import Evidence, sha256_json
from bridge.core import diagnostics as D
from bridge.core import schema as S

from . import evidence as E
from .manifest import ASSEMBLY_PATTERN, ID_PATTERN, LOADER_REALM, REALMS, decode_json, deploy_key_id, diag, \
    printable, satisfies, validate_manifest

REPORT_SCHEMA = 'bridge-mod-compat-report'
SET_REPORT_SCHEMA = 'bridge-mod-set-report'
CATALOG_SCHEMA = 'bridge-mod-known-apis'
SCHEMA_VERSION = 1
CLASSIFICATIONS = ('CONFIG_ONLY', 'CONTENT_ONLY', 'KNOWN_API_GAMEPLAY', 'HARMONY_PATCH', 'NATIVE_DEEP_PATCH', 'UNKNOWN')
PORTABILITY = ('PORTABLE', 'ADAPTER_REQUIRED', 'MANUAL_PORT', 'NOT_PORTABLE', 'UNKNOWN')
RULES = ('R0', 'R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7')
EVIDENCE_STATES = ('ABSENT', 'INVALID', 'UNBOUND', 'BOUND')
DEPENDENCY_STATUS = ('RESOLVED', 'PLATFORM', 'MISSING', 'OPTIONAL_ABSENT', 'VERSION_CONFLICT', 'VERSION_UNKNOWN',
                     'REALM_MISMATCH', 'UNCHECKED')
ROW_STATUS = ('MAPPED', 'LOCAL_QUERY', 'MANUAL', 'UNSUPPORTED')
QUARANTINE_BLOCKERS = ('MANIFEST_INVALID', 'NATIVE_HOOKS_UNREVIEWED', 'CROSS_REALM_NOT_PORTABLE')
CONFIDENCE_CAP, UNKNOWN_CONFIDENCE_CAP = 85, 30
CONFIDENCE_POINTS = {
    'MANIFEST_VALID': 10, 'STATIC_EVIDENCE_BOUND': 40, 'ALL_REFERENCES_CATALOGUED': 10, 'NO_REFLECTION': 10,
    'SAME_REALM': 10, 'REFLECTION_USED': -15, 'HARMONY_REFERENCED_WITHOUT_PATCHES': -10,
    'HARMONY_TRANSPILERS': -10, 'THIRD_PARTY_ASSEMBLY_UNDECLARED': -5, 'DEPENDENCY_PROBLEM': -10,
    'LOADER_UNKNOWN': -20,
}

# Bridge-side LOCAL_QUERY block for mod compatibility (Q050-Q057, see docs/MOD_COMPATIBILITY.md) plus the
# existing content-pipeline queries it reuses (docs/LOCAL_QUERIES.md).
LOCAL_QUERIES = {
    'Q007': 'SR1 registration operations (SRML 0.2.1.0)',
    'Q008': 'SR2 registration operations (MelonLoader 0.7.3 / IL2CPP, v0.6.2)',
    'Q011': 'Asset transform feasibility across Unity versions/runtimes',
    'Q050': 'Local static evidence extractor for mod assemblies (bridge-mod-static-evidence)',
    'Q051': 'Local MelonLoader attribute reader (bridge-melon-metadata)',
    'Q052': 'SRML modinfo.json dependency, version and load-order semantics',
    'Q053': 'SR1 SRML and game API catalogue for known_apis.json',
    'Q054': 'SR2 MelonLoader and community API catalogue for known_apis.json',
    'Q055': 'Harmony patch target verification against the exact lab build',
    'Q056': 'Cross-realm adapter capability bindings',
    'Q057': 'Native hook / detour mod policy and sandboxing',
}
# Bridge capability vocabulary used for adapter rows (not native API names).
BRIDGE_CAPABILITIES = {
    'config.schema_mapping': 'Translate a configuration file to the target mod/config schema',
    'content.asset_conversion': 'Convert shipped assets for the target engine/runtime',
    'content.registration': 'Register shipped content ids with the target game',
    'game.uncatalogued_api': 'Game or loader surface not in the known_apis catalogue',
    'harmony.patch_retarget': 'Re-target Harmony patches onto target-game members',
    'il2cpp.interop': 'IL2CPP interop usage that has no Mono counterpart',
    'native.hook': 'Native detours / P/Invoke hooks',
    'runtime.retarget': 'Rebuild for the target loader runtime and framework',
    'unity.engine_api': 'Unity engine API calls across different Unity versions/runtimes',
}
_API_ID = r'[a-z0-9]+(?:[._][a-z0-9]+)+'
_PREFIX = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}'
_REALM_BLOCK = S.obj({
    'loader': S.enum('SRML', 'MELONLOADER'),
    **{k: S.list_of(S.string(ASSEMBLY_PATTERN, max_length=128), max_items=32, unique=True)
       for k in ('loader_assemblies', 'harmony_assemblies', 'game_assemblies', 'interop_assemblies')},
    **{k: S.list_of(S.string(_PREFIX, max_length=64), max_items=32, unique=True)
       for k in ('engine_prefixes', 'interop_prefixes')},
    'target_framework': S.string(r'[a-z0-9][a-z0-9.]{0,31}'),
    'sources': S.list_of(S.string(max_length=256), max_items=16),
})
_CATALOG_SPEC = S.obj({
    'schema': S.enum(CATALOG_SCHEMA), 'schema_version': S.enum(SCHEMA_VERSION), 'evidence': S.enum(*map(str, Evidence)),
    'notice': S.string(max_length=1024),
    'system_assemblies': S.list_of(S.string(ASSEMBLY_PATTERN, max_length=128), max_items=64, unique=True),
    'system_prefixes': S.list_of(S.string(_PREFIX, max_length=64), max_items=64, unique=True),
    'realms': S.obj({'SR1': _REALM_BLOCK, 'SR2': _REALM_BLOCK}),
    'apis': S.list_of(S.obj({
        'id': S.string(_API_ID, max_length=96), 'realm': S.enum(*REALMS), 'kind': S.enum('TYPE', 'MANIFEST'),
        'assembly': S.optional(S.string(ASSEMBLY_PATTERN, max_length=128)),
        'type': S.optional(S.string(E.NAME_PATTERN, max_length=256)),
        'members': S.optional(S.list_of(S.string(E.NAME_PATTERN, max_length=256), max_items=256, unique=True)),
        'capability': S.string(_API_ID, max_length=96), 'source': S.string(max_length=256),
        'evidence': S.enum(*map(str, Evidence))}), max_items=4096),
    'capabilities': S.map_of(S.string(_API_ID, max_length=96), S.obj({
        'description': S.string(max_length=256), 'SR1': S.optional(S.string(_API_ID, max_length=96)),
        'SR2': S.optional(S.string(_API_ID, max_length=96))})),
})
_CATALOG_PATH = Path(__file__).with_name('known_apis.json')
_DEFAULT_CATALOG = []


# ------------------------------------------------------------------------------------- catalogue

def load_known_apis(raw=None):
    """``(catalog|None, diagnostics)``; ``raw=None`` reads the packaged ``known_apis.json``."""
    if raw is None:
        raw = _CATALOG_PATH.read_bytes()
    doc, out = decode_json(raw, subject='known_apis.json', max_bytes=1024 * 1024)
    if doc is None:
        return None, out
    problems = S.check(_CATALOG_SPEC, doc)
    if problems:
        return None, [diag('KNOWN_APIS_INVALID', 'error', 'known_apis catalogue schema violation',
                           details={'problems': [printable(p, 200) for p in problems[:20]]})]
    apis = {}
    for api in doc['apis']:
        if api['id'] in apis:
            out.append(diag('KNOWN_APIS_INVALID', 'error', 'duplicate api id', subject=api['id']))
        apis[api['id']] = api
        typed = api['assembly'] is not None and api['type'] is not None
        if (api['kind'] == 'TYPE') != typed or (api['kind'] == 'MANIFEST' and api['members'] is not None):
            out.append(diag('KNOWN_APIS_INVALID', 'error', 'TYPE needs assembly+type; MANIFEST needs neither',
                            subject=api['id']))
        if api['capability'] not in doc['capabilities']:
            out.append(diag('KNOWN_APIS_INVALID', 'error', 'api capability is not declared', subject=api['id']))
    for cap, row in doc['capabilities'].items():
        for realm in REALMS:
            target = apis.get(row[realm]) if row[realm] is not None else None
            if row[realm] is not None and (target is None or target['realm'] != realm or target['capability'] != cap):
                out.append(diag('KNOWN_APIS_INVALID', 'error', 'capability row names a wrong api', subject=cap,
                                details={'realm': realm}))
    for realm in REALMS:
        if LOADER_REALM[doc['realms'][realm]['loader']] != realm:
            out.append(diag('KNOWN_APIS_INVALID', 'error', 'realm block names the other realm loader', subject=realm))
    return (None if D.has_errors(out) else doc), D.sort(out)


def default_known_apis():
    if not _DEFAULT_CATALOG:
        catalog, problems = load_known_apis()
        if catalog is None:
            raise RuntimeError(f'packaged known_apis.json is invalid: {[p["code"] for p in problems]}')
        _DEFAULT_CATALOG.append(catalog)
    return _DEFAULT_CATALOG[0]


# ------------------------------------------------------------------------------------- helpers

class _Index:
    """Lookup of valid manifests by mod id and by provided assembly name (case-insensitive)."""

    def __init__(self, manifests):
        self.mods, self.assemblies = {}, {}
        for manifest in manifests:
            self.mods.setdefault(manifest['id'].casefold(), manifest)
            for name in manifest['provides_assemblies']:
                self.assemblies.setdefault(name.casefold(), manifest)

    @classmethod
    def build(cls, manifests):
        return cls(m for m in manifests if not validate_manifest(m))


def _matches(name, exact, prefixes=()):
    folded = name.casefold()
    return folded in {e.casefold() for e in exact} or any(folded.startswith(p.casefold()) for p in prefixes)


def _category(name, realm, catalog, own, declared):
    block = catalog['realms'][realm]
    other = catalog['realms'][next(r for r in REALMS if r != realm)]
    if name.casefold() in own:
        return 'own'
    if _matches(name, catalog['system_assemblies'], catalog['system_prefixes']):
        return 'system'
    for category, exact, prefixes in (('loader', block['loader_assemblies'], ()),
                                      ('harmony', block['harmony_assemblies'], ()),
                                      ('interop', block['interop_assemblies'], block['interop_prefixes']),
                                      ('game', block['game_assemblies'], ()),
                                      ('engine', (), block['engine_prefixes'])):
        if _matches(name, exact, prefixes):
            return category
    if _matches(name, other['loader_assemblies']):
        return 'foreign_loader'
    return 'declared' if name.casefold() in declared else 'third_party'


def _platform_assembly(name, realm, catalog):
    return _category(name, realm, catalog, set(), set()) in ('system', 'loader', 'harmony', 'interop', 'game', 'engine')


def _resolve(manifest, index, catalog):
    rows, out = [], []
    realm = manifest['realm']
    for dep in manifest['dependencies']:
        row = {'id': dep['id'], 'kind': dep['kind'], 'constraint': dep['constraint'], 'optional': dep['optional'],
               'status': 'UNCHECKED', 'resolved_to': None, 'present_version': None}
        target = None
        if dep['kind'] == 'ASSEMBLY' and _platform_assembly(dep['id'], realm, catalog):
            row['status'] = 'PLATFORM'
        elif index is not None:
            target = (index.mods if dep['kind'] == 'MOD' else index.assemblies).get(dep['id'].casefold())
            if target is None:
                row['status'] = 'OPTIONAL_ABSENT' if dep['optional'] else 'MISSING'
            elif target['realm'] != realm:
                row['status'], row['resolved_to'] = 'REALM_MISMATCH', target['id']
            else:
                verdict = satisfies(target['version']['parts'], dep['constraint']) if dep['kind'] == 'MOD' else True
                row['status'] = {True: 'RESOLVED', False: 'VERSION_CONFLICT', None: 'VERSION_UNKNOWN'}[verdict]
                row['resolved_to'], row['present_version'] = target['id'], target['version']['text']
        rows.append(row)
        severity = 'info' if row['status'] == 'OPTIONAL_ABSENT' else ('warning' if dep['optional'] else 'error')
        code = {'MISSING': 'DEPENDENCY_MISSING', 'VERSION_CONFLICT': 'DEPENDENCY_VERSION_CONFLICT',
                'REALM_MISMATCH': 'DEPENDENCY_REALM_MISMATCH', 'OPTIONAL_ABSENT': 'DEPENDENCY_OPTIONAL_ABSENT'
                }.get(row['status'])
        if code is not None:
            out.append(diag(code, severity, f"dependency {row['status'].lower().replace('_', ' ')}",
                            subject=manifest['id'], details={'dependency': dep['id'], 'kind': dep['kind'],
                                                             'constraint': dep['constraint'],
                                                             'present_version': row['present_version']}))
    return sorted(rows, key=lambda r: (r['kind'], r['id'])), out


def _summarize(manifest, evidence, catalog):
    """Facts from bound evidence (names and counts only)."""
    realm = manifest['realm']
    own = {a['assembly_name'].casefold() for a in evidence['code']['assemblies']}
    declared = {d['id'].casefold() for d in manifest['dependencies'] if d['kind'] == 'ASSEMBLY'}
    types = {(a['assembly'].casefold(), a['type']): a for a in catalog['apis']
             if a['realm'] == realm and a['kind'] == 'TYPE'}
    used, uncatalogued_types, uncatalogued_members, uncatalogued_assemblies = {}, 0, 0, set()
    engine_types, third_party, foreign, harmony_referenced = 0, set(), set(), False
    for group in evidence['references']:
        category = _category(group['assembly'], realm, catalog, own, declared)
        if category == 'engine':
            engine_types += len(group['types'])
        elif category == 'harmony':
            harmony_referenced = True
        elif category == 'third_party':
            third_party.add(group['assembly'])
        elif category == 'foreign_loader':
            foreign.add(group['assembly'])
        if category not in ('loader', 'game', 'interop'):
            continue
        for type_name in group['types']:
            api = types.get((group['assembly'].casefold(), type_name))
            if api is None:
                uncatalogued_types += 1
                uncatalogued_assemblies.add(group['assembly'])
            else:
                used[api['id']] = api
        for member in group['members']:
            type_name, name = member.split('::', 1)
            api = types.get((group['assembly'].casefold(), type_name))
            if api is not None and api['members'] is not None and name not in api['members']:
                uncatalogued_members += 1
                uncatalogued_assemblies.add(group['assembly'])
    harmony = evidence['harmony']
    patches = harmony['patches']
    return {
        'code': bool(evidence['code']['assemblies']),
        'apis_used': [{'api_id': a['id'], 'capability': a['capability'], 'evidence': a['evidence']}
                      for _, a in sorted(used.items())],
        'references': {'uncatalogued_types': uncatalogued_types, 'uncatalogued_members': uncatalogued_members,
                       'uncatalogued_assemblies': sorted(uncatalogued_assemblies), 'engine_types': engine_types,
                       'third_party_assemblies': sorted(third_party), 'foreign_loader_assemblies': sorted(foreign)},
        'harmony': {'patch_count': len(patches), 'patch_all_calls': harmony['patch_all_calls'],
                    **{k: sum(p[k] for p in patches) for k in ('prefix', 'postfix', 'transpiler', 'finalizer')},
                    'targets': sorted({p['target'] for p in patches})[:64]},
        'harmony_referenced': harmony_referenced,
        'native': {'pinvoke_modules': sorted(evidence['native']['pinvoke_modules']),
                   'detour_indicators': sorted(evidence['native']['detour_indicators']),
                   'function_pointer_hooks': evidence['native']['function_pointer_hooks']},
        'content': bool(evidence['content']['asset_bundles'] or evidence['content']['content_files'] or
                        evidence['content']['content_ids']),
        'content_ids': sorted({c.casefold() for c in evidence['content']['content_ids']}),
        'config': bool(evidence['config_files']),
        'reflection': sorted(k for k, v in evidence['reflection'].items() if v),
        'il2cpp': evidence['il2cpp'],
    }


def _classify(facts):
    if facts is None:
        return 'UNKNOWN', 'R0'
    native = facts['native']
    if native['pinvoke_modules'] or native['detour_indicators'] or native['function_pointer_hooks']:
        return 'NATIVE_DEEP_PATCH', 'R1'
    if facts['harmony']['patch_count'] or facts['harmony']['patch_all_calls']:
        return 'HARMONY_PATCH', 'R2'
    if facts['code']:
        refs = facts['references']
        clean = not (refs['uncatalogued_types'] or refs['uncatalogued_members'] or refs['third_party_assemblies'] or
                     refs['foreign_loader_assemblies'] or facts['reflection'])
        return ('KNOWN_API_GAMEPLAY', 'R3') if clean else ('UNKNOWN', 'R4')
    if facts['content']:
        return 'CONTENT_ONLY', 'R5'
    if facts['config']:
        return 'CONFIG_ONLY', 'R6'
    return 'UNKNOWN', 'R7'


def _row(source_capability, status, *, target_capability=None, target_api=None, local_query=None):
    return {'source_capability': source_capability, 'target_capability': target_capability, 'status': status,
            'target_api': target_api, 'local_query': local_query}


def _capability_rows(classification, facts, source, target, catalog):
    """Adapter rows for a cross-realm port (empty for same realm or UNKNOWN)."""
    if source == target or classification == 'UNKNOWN':
        return []
    rows = {}

    def add(row):
        rows.setdefault(row['source_capability'], row)
    registration_query = 'Q007' if target == 'SR1' else 'Q008'
    if classification == 'CONFIG_ONLY':
        add(_row('config.schema_mapping', 'LOCAL_QUERY', target_capability='config.schema_mapping', local_query='Q056'))
    if classification == 'CONTENT_ONLY':
        add(_row('content.asset_conversion', 'LOCAL_QUERY', target_capability='content.asset_conversion',
                 local_query='Q011'))
        add(_row('content.registration', 'LOCAL_QUERY', target_capability='content.registration',
                 local_query=registration_query))
    if classification == 'NATIVE_DEEP_PATCH':
        add(_row('native.hook', 'UNSUPPORTED', local_query='Q057'))
    if classification == 'HARMONY_PATCH':
        add(_row('harmony.patch_retarget', 'MANUAL', target_capability='harmony.patch_retarget', local_query='Q055'))
    if facts['code']:
        add(_row('runtime.retarget', 'MAPPED', target_capability='runtime.retarget',
                 target_api=catalog['realms'][target]['target_framework']))
        for api in facts['apis_used']:
            counterpart = catalog['capabilities'][api['capability']][target]
            add(_row(api['capability'], 'MAPPED', target_capability=api['capability'], target_api=counterpart)
                if counterpart else _row(api['capability'], 'LOCAL_QUERY', local_query='Q056'))
        if facts['references']['engine_types']:
            add(_row('unity.engine_api', 'LOCAL_QUERY', target_capability='unity.engine_api', local_query='Q056'))
        il2cpp = facts['il2cpp']
        if source == 'SR2' and (il2cpp['interop_references'] or il2cpp['type_injection'] or
                                il2cpp['il2cpp_type_references']):
            add(_row('il2cpp.interop', 'MANUAL', local_query='Q056'))
        refs = facts['references']
        if refs['uncatalogued_types'] or refs['uncatalogued_members']:
            add(_row('game.uncatalogued_api', 'LOCAL_QUERY', local_query='Q053' if source == 'SR1' else 'Q054'))
    if facts['content'] and classification != 'CONTENT_ONLY':
        add(_row('content.registration', 'LOCAL_QUERY', target_capability='content.registration',
                 local_query=registration_query))
    return [rows[k] for k in sorted(rows)]


def _portability(classification, rows, cross, loader):
    if loader == 'UNKNOWN' or classification == 'UNKNOWN':
        return 'UNKNOWN'
    if not cross:
        return 'PORTABLE'
    verdict = {'CONFIG_ONLY': 'ADAPTER_REQUIRED', 'CONTENT_ONLY': 'ADAPTER_REQUIRED',
               'KNOWN_API_GAMEPLAY': 'ADAPTER_REQUIRED', 'HARMONY_PATCH': 'MANUAL_PORT',
               'NATIVE_DEEP_PATCH': 'NOT_PORTABLE'}[classification]
    statuses = {r['status'] for r in rows}
    if 'UNSUPPORTED' in statuses:
        return 'NOT_PORTABLE'
    if 'MANUAL' in statuses and verdict == 'ADAPTER_REQUIRED':
        return 'MANUAL_PORT'
    return verdict


def _confidence(state, facts, classification, cross, dependency_problem, loader_unknown):
    reasons, total = [], 0

    def add(code, points=None):
        nonlocal total
        points = CONFIDENCE_POINTS.get(code, 0) if points is None else points
        reasons.append({'code': code, 'delta': points / 100})
        total += points
    add('MANIFEST_VALID')
    if state != 'BOUND':
        add('NO_STATIC_EVIDENCE' if state == 'ABSENT' else 'STATIC_EVIDENCE_UNUSABLE', 0)
    else:
        add('STATIC_EVIDENCE_BOUND')
        refs = facts['references']
        if not (refs['uncatalogued_types'] or refs['uncatalogued_members'] or refs['foreign_loader_assemblies']):
            add('ALL_REFERENCES_CATALOGUED')
        add('REFLECTION_USED' if facts['reflection'] else 'NO_REFLECTION')
        if facts['harmony_referenced'] and not (facts['harmony']['patch_count'] or facts['harmony']['patch_all_calls']):
            add('HARMONY_REFERENCED_WITHOUT_PATCHES')
        if facts['harmony']['transpiler']:
            add('HARMONY_TRANSPILERS')
        if refs['third_party_assemblies']:
            add('THIRD_PARTY_ASSEMBLY_UNDECLARED')
        if not cross:
            add('SAME_REALM')
    if dependency_problem:
        add('DEPENDENCY_PROBLEM')
    if loader_unknown:
        add('LOADER_UNKNOWN')
    cap = UNKNOWN_CONFIDENCE_CAP if classification == 'UNKNOWN' else CONFIDENCE_CAP
    if total > cap:
        add('CLASSIFICATION_UNKNOWN_CAP' if classification == 'UNKNOWN' else 'STATIC_ANALYSIS_CAP', cap - total)
        total = cap
    return max(total, 0) / 100, reasons


def _finalize(report):
    report['report_id'] = sha256_json(report)
    return report


def _invalid_manifest_report(manifest, problems, target_realm):
    mod_id = manifest.get('id') if isinstance(manifest, dict) else None
    blocker = diag('MANIFEST_INVALID', 'error', 'manifest failed validation; nothing else was analyzed',
                   details={'codes': sorted({p['code'] for p in problems})})
    return _finalize({
        'schema': REPORT_SCHEMA, 'schema_version': SCHEMA_VERSION,
        'mod': {'id': mod_id if isinstance(mod_id, str) and re.fullmatch(ID_PATTERN, mod_id) else None, 'name': None,
                'version': None, 'version_parts': [], 'manifest_id': None, 'loader': None, 'synthetic': False},
        'source_realm': None, 'target_realm': target_realm, 'cross_realm': False, 'evidence_state': 'ABSENT',
        'evidence_sha256': None, 'classification': 'UNKNOWN', 'classification_rule': 'R0', 'portability': 'UNKNOWN',
        'dependencies': [], 'apis_used': [], 'references': None, 'harmony': None, 'native': None, 'content_ids': [],
        'required_capabilities': [], 'confidence': 0.0,
        'confidence_reasons': [{'code': 'MANIFEST_INVALID', 'delta': 0.0}], 'blockers': [blocker],
        'manual_review': [], 'local_queries': [], 'diagnostics': D.sort([blocker, *problems]),
        'data_evidence': 'NONE', 'evidence': str(Evidence.NEEDS_LOCAL_RUNTIME_PROOF)})


# ------------------------------------------------------------------------------------- analysis

def analyze(manifest, evidence=None, *, target_realm=None, known_apis=None, available=None):
    """``bridge-mod-compat-report`` for one mod. Never raises on manifest/evidence content.

    ``available`` (optional iterable of manifests) resolves dependencies; without it they stay UNCHECKED.
    ``target_realm``/``known_apis`` are trusted program inputs and raise ``ValueError`` when invalid.
    """
    catalog = _catalog(known_apis)
    index = _Index.build(available) if available is not None else None
    return _analyze(manifest, evidence, target_realm, catalog, index)


def _catalog(known_apis):
    if known_apis is None:
        return default_known_apis()
    catalog, problems = load_known_apis(known_apis)
    if catalog is None:
        raise ValueError(f'invalid known_apis catalogue: {[p["code"] for p in problems]}')
    return catalog


def _analyze(manifest, evidence, target_realm, catalog, index):
    if target_realm is not None and target_realm not in REALMS:
        raise ValueError(f'target_realm must be one of {REALMS}')
    problems = validate_manifest(manifest)
    if D.has_errors(problems):
        return _invalid_manifest_report(manifest, problems, target_realm)
    mod_id, source = manifest['id'], manifest['realm']
    target = target_realm or source
    cross = source != target
    diagnostics, manual, queries, blockers = [], set(), set(), []

    def block(code, message, **details):
        blockers.append(diag(code, 'error', message, subject=mod_id, details=details or None))

    state, facts, evidence_sha = 'ABSENT', None, None
    if evidence is not None:
        found = E.validate_evidence(evidence)
        diagnostics.extend(found)
        if D.has_errors(found):
            state = 'INVALID'
        else:
            evidence_sha = E.evidence_hash(evidence)
            binding = E.bind(manifest, evidence)
            diagnostics.extend(binding)
            state = 'UNBOUND' if binding else 'BOUND'
    if state == 'BOUND':
        facts = _summarize(manifest, evidence, catalog)
    else:
        queries.add('Q050')
        diagnostics.append(diag('STATIC_EVIDENCE_' + ('MISSING' if state == 'ABSENT' else 'UNUSABLE'), 'warning',
                                'no usable LOCAL static evidence; classification is UNKNOWN', subject=mod_id,
                                evidence='BLOCKED_ON_LOCAL_QUERY', details={'local_query': 'Q050'}))
    classification, rule = _classify(facts)
    dependencies, dep_diags = _resolve(manifest, index, catalog)
    diagnostics.extend(dep_diags)
    for item in dep_diags:
        if item['severity'] == 'error':
            blockers.append(item)
    rows = _capability_rows(classification, facts, source, target, catalog)
    portability = _portability(classification, rows, cross, manifest['loader'])
    queries.update(r['local_query'] for r in rows if r['local_query'] and r['status'] != 'MAPPED')

    if manifest['loader'] == 'UNKNOWN':
        block('LOADER_UNKNOWN', 'the mod loader is unknown; nothing can be deployed')
    if rule == 'R4':
        queries.add('Q053' if source == 'SR1' else 'Q054')
    if rule == 'R7':
        queries.add('Q050')
        diagnostics.append(diag('MOD_EVIDENCE_EMPTY', 'warning', 'bound evidence shows no code, content or config',
                                subject=mod_id, details={'local_query': 'Q050'}))
    if classification == 'UNKNOWN':
        block('CLASSIFICATION_UNKNOWN', 'compatibility cannot be established from the available evidence',
              rule=rule)
    if classification == 'NATIVE_DEEP_PATCH':
        queries.add('Q057')
        manual.add('NATIVE_HOOKS_PRESENT')
        if not cross:
            block('NATIVE_HOOKS_UNREVIEWED', 'native hooks are never enabled automatically')
    if portability == 'NOT_PORTABLE':
        block('CROSS_REALM_NOT_PORTABLE', f'cannot be ported from {source} to {target}')
    if classification == 'HARMONY_PATCH':
        queries.add('Q055')
        manual.add('HARMONY_TARGETS_UNVERIFIED')
    if manifest['loader'] == 'SRML' and (manifest['load_after'] or manifest['load_before'] or
                                         any(d['constraint'] != '*' for d in manifest['dependencies'])):
        queries.add('Q052')
        manual.add('SRML_DEPENDENCY_SEMANTICS_UNVERIFIED')
    if any(r['status'] == 'VERSION_UNKNOWN' for r in dependencies):
        manual.add('DEPENDENCY_VERSION_UNKNOWN')
    if facts is not None:
        refs = facts['references']
        for flag in facts['reflection']:
            manual.add('REFLECTION_' + flag.upper())
        if facts['harmony']['transpiler']:
            manual.add('HARMONY_TRANSPILERS')
        if facts['harmony_referenced'] and not (facts['harmony']['patch_count'] or facts['harmony']['patch_all_calls']):
            manual.add('HARMONY_REFERENCED_WITHOUT_PATCHES')
        if refs['third_party_assemblies']:
            manual.add('THIRD_PARTY_ASSEMBLY_UNDECLARED')
        if refs['foreign_loader_assemblies']:
            manual.add('FOREIGN_LOADER_REFERENCED')
        if refs['uncatalogued_types'] or refs['uncatalogued_members']:
            manual.add('UNCATALOGUED_REFERENCES')
            queries.add('Q053' if source == 'SR1' else 'Q054')
        il2cpp = facts['il2cpp']
        if source == 'SR1' and (il2cpp['interop_references'] or il2cpp['type_injection'] or
                                il2cpp['il2cpp_type_references']):
            manual.add('IL2CPP_INDICATORS_ON_MONO_REALM')
            diagnostics.append(diag('IL2CPP_INDICATORS_ON_MONO_REALM', 'warning',
                                    'IL2CPP interop indicators in an SR1 (Mono) mod', subject=mod_id))
        if cross and refs['engine_types']:
            manual.add('UNITY_VERSION_DIFFERS')
        for cid in facts['content_ids']:
            if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,159}', cid):
                diagnostics.append(diag('CONTENT_ID_NOT_DEPLOYABLE', 'info', 'content id cannot be a deploy-unit id',
                                        subject=mod_id, details={'content_id': cid}))
    if not manifest['game_versions']:
        diagnostics.append(diag('GAME_VERSION_UNDECLARED', 'info', 'the mod declares no game version',
                                subject=mod_id))
    dependency_problem = any(r['status'] in ('MISSING', 'VERSION_CONFLICT', 'REALM_MISMATCH') for r in dependencies)
    confidence, reasons = _confidence(state, facts, classification, cross, dependency_problem,
                                      manifest['loader'] == 'UNKNOWN')
    synthetic = manifest['source']['synthetic'] or (state == 'BOUND' and evidence['synthetic'])
    queries = sorted(queries)
    report = {
        'schema': REPORT_SCHEMA, 'schema_version': SCHEMA_VERSION,
        'mod': {'id': mod_id, 'name': manifest['name'], 'version': manifest['version']['text'],
                'version_parts': manifest['version']['parts'], 'manifest_id': manifest['manifest_id'],
                'loader': manifest['loader'], 'synthetic': manifest['source']['synthetic']},
        'source_realm': source, 'target_realm': target, 'cross_realm': cross, 'evidence_state': state,
        'evidence_sha256': evidence_sha, 'classification': classification, 'classification_rule': rule,
        'portability': portability, 'dependencies': dependencies,
        'apis_used': facts['apis_used'] if facts else [], 'references': facts['references'] if facts else None,
        'harmony': facts['harmony'] if facts else None, 'native': facts['native'] if facts else None,
        'content_ids': facts['content_ids'] if facts else [], 'required_capabilities': rows,
        'confidence': confidence, 'confidence_reasons': reasons, 'blockers': D.sort(blockers),
        'manual_review': sorted(manual), 'local_queries': queries, 'diagnostics': D.sort(diagnostics + blockers),
        'data_evidence': 'SYNTHETIC' if synthetic else 'STATIC',
        'evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY if queries else Evidence.NEEDS_LOCAL_RUNTIME_PROOF),
    }
    return _finalize(report)


# ----------------------------------------------------------------------------- report validation

def _diagnostic_list(max_items=1024):
    def inner(value, path, out):
        if not isinstance(value, list) or len(value) > max_items:
            out.append(f'{path}: list required')
            return
        for i, item in enumerate(value):
            if D.validate(item):
                out.append(f'{path}[{i}]: invalid diagnostic')
    return inner


def _unit_interval():
    def inner(value, path, out):
        if type(value) not in (int, float) or not 0 <= value <= 1:
            out.append(f'{path}: number in [0, 1] required')
    return inner


_Q = S.string(r'Q\d{3}', max_length=4)
_NAMES = S.list_of(S.string(ASSEMBLY_PATTERN, max_length=128), max_items=256, unique=True)
_REPORT_SPEC = S.obj({
    'schema': S.enum(REPORT_SCHEMA), 'schema_version': S.enum(SCHEMA_VERSION), 'report_id': S.sha256(),
    'mod': S.obj({'id': S.optional(S.string(ID_PATTERN, max_length=128)),
                  'name': S.optional(S.string(r'[^\x00-\x1f\x7f\ud800-\udfff]{1,128}', max_length=128)),
                  'version': S.optional(S.string(r'[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}', max_length=64)),
                  'version_parts': S.list_of(S.integer(minimum=0, maximum=999999999), max_items=4),
                  'manifest_id': S.optional(S.sha256()), 'loader': S.optional(S.enum('SRML', 'MELONLOADER', 'UNKNOWN')),
                  'synthetic': S.boolean()}),
    'source_realm': S.optional(S.enum(*REALMS)), 'target_realm': S.optional(S.enum(*REALMS)),
    'cross_realm': S.boolean(), 'evidence_state': S.enum(*EVIDENCE_STATES), 'evidence_sha256': S.optional(S.sha256()),
    'classification': S.enum(*CLASSIFICATIONS), 'classification_rule': S.enum(*RULES),
    'portability': S.enum(*PORTABILITY),
    'dependencies': S.list_of(S.obj({
        'id': S.string(max_length=128), 'kind': S.enum('MOD', 'ASSEMBLY'), 'constraint': S.string(max_length=128),
        'optional': S.boolean(), 'status': S.enum(*DEPENDENCY_STATUS),
        'resolved_to': S.optional(S.string(ID_PATTERN, max_length=128)),
        'present_version': S.optional(S.string(max_length=64))}), max_items=256),
    'apis_used': S.list_of(S.obj({'api_id': S.string(_API_ID, max_length=96), 'capability': S.string(_API_ID),
                                  'evidence': S.enum(*map(str, Evidence))}), max_items=4096),
    'references': S.optional(S.obj({
        'uncatalogued_types': S.integer(minimum=0), 'uncatalogued_members': S.integer(minimum=0),
        'uncatalogued_assemblies': _NAMES, 'engine_types': S.integer(minimum=0), 'third_party_assemblies': _NAMES,
        'foreign_loader_assemblies': _NAMES})),
    'harmony': S.optional(S.obj({
        **{k: S.integer(minimum=0) for k in ('patch_count', 'patch_all_calls', 'prefix', 'postfix', 'transpiler',
                                             'finalizer')},
        'targets': S.list_of(S.string(E.TARGET_PATTERN, max_length=514), max_items=64)})),
    'native': S.optional(S.obj({'pinvoke_modules': S.list_of(S.string(E.NATIVE_MODULE_PATTERN), max_items=64),
                                'detour_indicators': S.list_of(S.string(E.INDICATOR_PATTERN), max_items=64),
                                'function_pointer_hooks': S.boolean()})),
    'content_ids': S.list_of(S.string(E.CONTENT_ID_PATTERN, max_length=160), max_items=E.MAX_CONTENT_IDS),
    'required_capabilities': S.list_of(S.obj({
        'source_capability': S.string(_API_ID, max_length=96),
        'target_capability': S.optional(S.string(_API_ID, max_length=96)), 'status': S.enum(*ROW_STATUS),
        'target_api': S.optional(S.string(r'[a-z0-9][a-z0-9._]{0,95}', max_length=96)),
        'local_query': S.optional(_Q)}), max_items=64),
    'confidence': _unit_interval(),
    'confidence_reasons': S.list_of(S.obj({'code': S.string(r'[A-Z][A-Z0-9_]{0,63}'), 'delta': S.number()}),
                                    max_items=32),
    'blockers': _diagnostic_list(), 'manual_review': S.list_of(S.string(r'[A-Z][A-Z0-9_]{0,63}'), max_items=64,
                                                              unique=True),
    'local_queries': S.list_of(_Q, max_items=64, unique=True), 'diagnostics': _diagnostic_list(),
    'data_evidence': S.enum('SYNTHETIC', 'STATIC', 'NONE'), 'evidence': S.enum(*map(str, Evidence)),
})


def validate_report(report):
    """Diagnostics for an untrusted compat report (schema + content hash). Never raises."""
    problems = S.check(_REPORT_SPEC, report)
    if problems:
        return [diag('REPORT_SCHEMA_INVALID', 'error', 'compat report schema violation',
                     details={'problems': [printable(p, 200) for p in problems[:20]]})]
    if sha256_json({k: v for k, v in report.items() if k != 'report_id'}) != report['report_id']:
        return [diag('REPORT_ID_MISMATCH', 'error', 'report_id is not the content hash', subject=report['mod']['id'])]
    return []


def unit_status(report):
    """Deploy-unit status implied by a (valid) report; see the module docstring."""
    codes = {b['code'] for b in report['blockers']}
    if report['classification'] == 'NATIVE_DEEP_PATCH' or report['portability'] == 'NOT_PORTABLE' or \
            codes & set(QUARANTINE_BLOCKERS):
        return 'QUARANTINED'
    if report['portability'] == 'PORTABLE' and not report['cross_realm'] and not codes:
        return 'EXPERIMENTAL' if report['local_queries'] else 'READY'
    return 'PENDING_ADAPTER'


def to_deploy_unit(manifest, report):
    """``(bridge-deploy-unit | None, diagnostics)`` for a mod in the report's target realm.

    Same-realm units carry the manifest's files; a cross-realm unit is a placeholder with no files
    (the adapter does not exist yet) and can never be READY.
    """
    from bridge.deploy.units import make_unit, validate_unit
    out = [*validate_manifest(manifest), *validate_report(report)]
    if D.has_errors(out):
        return None, D.sort(out)
    if report['mod']['manifest_id'] != manifest['manifest_id']:
        return None, [diag('REPORT_MANIFEST_MISMATCH', 'error', 'report was computed for another manifest',
                           subject=manifest['id'])]
    target = report['target_realm']
    key = f"mod:{deploy_key_id(manifest['id'])}:{target}"
    requires = set()
    for dep in report['dependencies']:
        name = dep['id'] if dep['kind'] == 'MOD' else dep['resolved_to']
        if not dep['optional'] and name is not None and dep['status'] != 'PLATFORM':
            requires.add(f'mod:{deploy_key_id(name)}:{target}')
    requires.discard(key)
    provides = [c for c in report['content_ids'] if re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,159}', c)]
    unit = make_unit(key=key, kind='MOD', realm=target, version=manifest['version']['parts'] or [0],
                     files=manifest['files'] if not report['cross_realm'] else [], provides=provides,
                     requires=sorted(requires), status=unit_status(report), source_ref=report['report_id'],
                     evidence=report['evidence'])
    out = validate_unit(unit)
    return (None if D.has_errors(out) else unit), out


# ------------------------------------------------------------------------------------ set analysis

def _strongly_connected(nodes, edges):
    """Tarjan's algorithm, iterative and deterministic; returns sorted cycles (size>1 or self-loop)."""
    graph = {n: sorted({b for a, b in edges if a == n}) for n in nodes}
    index, low, on_stack, stack, cycles, counter = {}, {}, set(), [], [], [0]
    for root in sorted(nodes):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            successors = graph[node]
            if i < len(successors):
                work.append((node, i + 1))
                nxt = successors[i]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    item = stack.pop()
                    on_stack.discard(item)
                    component.append(item)
                    if item == node:
                        break
                if len(component) > 1 or node in graph[node]:
                    cycles.append(sorted(component))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(cycles)


def _load_order(nodes, edges):
    """Kahn's algorithm with a sorted frontier: prerequisites first. Returns (order, unordered)."""
    before = {n: set() for n in nodes}
    for dependent, prerequisite in edges:
        before[dependent].add(prerequisite)
    after = {n: set() for n in nodes}
    for dependent, prerequisites in before.items():
        for prerequisite in prerequisites:
            after[prerequisite].add(dependent)
    remaining = {n: len(p) for n, p in before.items()}
    frontier = [n for n, count in remaining.items() if count == 0]
    heapq.heapify(frontier)
    order = []
    while frontier:
        node = heapq.heappop(frontier)
        order.append(node)
        for dependent in sorted(after[node]):
            remaining[dependent] -= 1
            if remaining[dependent] == 0:
                heapq.heappush(frontier, dependent)
    return order, sorted(set(nodes) - set(order))


def _evidence_by_id(evidences, out):
    if evidences is None:
        return {}
    if isinstance(evidences, dict):
        return {str(k).casefold(): v for k, v in evidences.items()}
    found, duplicate = {}, set()
    for i, item in enumerate(evidences if isinstance(evidences, (list, tuple)) else []):
        mod_id = item.get('mod_id') if isinstance(item, dict) else None
        if not isinstance(mod_id, str) or not re.fullmatch(ID_PATTERN, mod_id):
            out.append(diag('SET_EVIDENCE_INVALID', 'warning', 'evidence without a usable mod_id ignored',
                            details={'index': i}))
            continue
        if mod_id.casefold() in found:
            duplicate.add(mod_id.casefold())
        found[mod_id.casefold()] = item
    for mod_id in sorted(duplicate):
        out.append(diag('SET_EVIDENCE_DUPLICATE', 'warning', 'two evidence documents for one mod; both ignored',
                        subject=mod_id))
        found.pop(mod_id)
    return found


def analyze_set(manifests, evidences, target_realm, *, known_apis=None):
    """``bridge-mod-set-report`` v1 for a whole mod list targeting one realm. Never raises on content.

    Finds duplicate ids (case-insensitive), deploy-key collisions, missing dependencies, version
    conflicts, dependency cycles, content-id and file claims shared by two mods (case-insensitive)
    and a deterministic load order (prerequisites first; ties by id).
    """
    if target_realm not in REALMS:
        raise ValueError(f'target_realm must be one of {REALMS}')
    catalog = _catalog(known_apis)
    out = []
    valid = []
    for i, manifest in enumerate(manifests):
        problems = validate_manifest(manifest)
        if D.has_errors(problems):
            out.append(diag('SET_MANIFEST_INVALID', 'error', 'manifest excluded from the set', details={
                'index': i, 'codes': sorted({p['code'] for p in problems})}))
        else:
            valid.append(manifest)
    by_id = {}
    for manifest in valid:
        by_id.setdefault(manifest['id'].casefold(), []).append(manifest)
    duplicates = []
    for mod_id, group in sorted(by_id.items()):
        group.sort(key=lambda m: (m['version']['parts'], m['manifest_id']), reverse=True)
        if len(group) > 1:
            versions = sorted({m['version']['text'] for m in group})
            duplicates.append({'id': mod_id, 'manifest_ids': sorted(m['manifest_id'] for m in group),
                               'versions': versions})
            out.append(diag('MOD_ID_DUPLICATE', 'error', 'two manifests share a mod id', subject=mod_id,
                            details={'count': len(group), 'versions': versions}))
            if len(versions) > 1:
                out.append(diag('MOD_VERSION_CONFLICT', 'error', 'one mod id at different versions', subject=mod_id,
                                details={'versions': versions}))
    chosen = {mod_id: group[0] for mod_id, group in by_id.items()}
    keys = {}
    for mod_id in chosen:
        keys.setdefault(deploy_key_id(mod_id), []).append(mod_id)
    for key, ids in sorted(keys.items()):
        if len(ids) > 1:
            out.append(diag('DEPLOY_KEY_COLLISION', 'error', 'mod ids map to one deploy-unit key', subject=key,
                            details={'ids': sorted(ids)}))
    evidence_map = _evidence_by_id(evidences, out)
    for orphan in sorted(set(evidence_map) - set(by_id)):
        out.append(diag('SET_EVIDENCE_ORPHAN', 'info', 'evidence for a mod not in the set', subject=orphan))
    index = _Index(chosen[k] for k in sorted(chosen))
    reports = [_analyze(m, evidence_map.get(m['id'].casefold()), target_realm, catalog, index)
               for m in sorted(valid, key=lambda m: (m['id'], m['manifest_id']))]
    chosen_reports = {r['mod']['id'].casefold(): r for r in reports
                      if chosen[r['mod']['id'].casefold()]['manifest_id'] == r['mod']['manifest_id']}

    nodes = sorted(chosen)
    edges, missing, version_conflicts = [], [], []
    for mod_id in nodes:
        manifest, report = chosen[mod_id], chosen_reports[mod_id]
        for dep in report['dependencies']:
            if dep['status'] in ('RESOLVED', 'VERSION_CONFLICT', 'VERSION_UNKNOWN') and dep['resolved_to']:
                edges.append({'from': mod_id, 'to': dep['resolved_to'].casefold(),
                              'kind': 'OPTIONAL' if dep['optional'] else 'REQUIRES'})
            if dep['status'] == 'MISSING':
                missing.append({'mod': mod_id, 'dependency': dep['id'], 'kind': dep['kind']})
            if dep['status'] == 'VERSION_CONFLICT':
                version_conflicts.append({'mod': mod_id, 'dependency': dep['id'], 'constraint': dep['constraint'],
                                          'present_version': dep['present_version']})
        for other in manifest['load_after']:
            if other in chosen and other != mod_id:
                edges.append({'from': mod_id, 'to': other, 'kind': 'LOAD_AFTER'})
        for other in manifest['load_before']:
            if other in chosen and other != mod_id:
                edges.append({'from': other, 'to': mod_id, 'kind': 'LOAD_BEFORE'})
    edges = sorted({(e['from'], e['to'], e['kind']): e for e in edges}.values(),
                   key=lambda e: (e['from'], e['to'], e['kind']))
    dependency_edges = {(e['from'], e['to']) for e in edges if e['kind'] in ('REQUIRES', 'OPTIONAL')}
    cycles = _strongly_connected(nodes, dependency_edges)
    for cycle in cycles:
        out.append(diag('DEPENDENCY_CYCLE', 'error', 'mods depend on each other', subject=cycle[0],
                        details={'mods': cycle}))
    order, unordered = _load_order(nodes, {(e['from'], e['to']) for e in edges})
    in_cycles = {m for cycle in cycles for m in cycle}
    hint_only = [m for m in unordered if m not in in_cycles]
    if hint_only:
        out.append(diag('LOAD_ORDER_UNRESOLVED', 'warning', 'load order blocked by a cycle or load-order hints',
                        details={'mods': hint_only[:32]}))

    content_claims, file_claims = {}, {}
    for mod_id in nodes:
        for cid in chosen_reports[mod_id]['content_ids']:
            content_claims.setdefault(cid.casefold(), set()).add(mod_id)
        for entry in chosen[mod_id]['files']:
            file_claims.setdefault(entry['path'].casefold(), []).append((mod_id, entry['path'], entry['sha256']))
    content_conflicts = [{'content_id': cid, 'mods': sorted(mods)}
                         for cid, mods in sorted(content_claims.items()) if len(mods) > 1]
    file_conflicts = []
    for folded, claims in sorted(file_claims.items()):
        mods = sorted({c[0] for c in claims})
        if len(mods) > 1:
            identical = len({c[2] for c in claims}) == 1
            file_conflicts.append({'path': folded, 'paths': sorted({c[1] for c in claims}), 'mods': mods,
                                   'identical': identical})
    for item in content_conflicts:
        out.append(diag('CONTENT_ID_CONFLICT', 'error', 'two mods claim one content id', subject=item['content_id'],
                        details={'mods': item['mods']}))
    for item in file_conflicts:
        out.append(diag('FILE_CONFLICT', 'warning' if item['identical'] else 'error',
                        'two mods ship one file path (case-insensitive)', subject=item['path'],
                        details={'mods': item['mods'], 'identical': item['identical']}))

    summary = {'classification': {}, 'portability': {}, 'unit_status': {}}
    for report in reports:
        for field, value in (('classification', report['classification']), ('portability', report['portability']),
                             ('unit_status', unit_status(report))):
            summary[field][value] = summary[field].get(value, 0) + 1
    result = {
        'schema': SET_REPORT_SCHEMA, 'schema_version': SCHEMA_VERSION, 'target_realm': target_realm,
        'mods': [{'id': r['mod']['id'], 'manifest_id': r['mod']['manifest_id'], 'report_id': r['report_id'],
                  'classification': r['classification'], 'portability': r['portability'],
                  'unit_status': unit_status(r), 'chosen': chosen[r['mod']['id'].casefold()]['manifest_id'] ==
                  r['mod']['manifest_id']} for r in reports],
        'reports': reports, 'graph': {'nodes': nodes, 'edges': edges}, 'cycles': cycles, 'missing': missing,
        'version_conflicts': version_conflicts, 'duplicates': duplicates,
        'conflicts': {'content_ids': content_conflicts, 'files': file_conflicts},
        'load_order': order, 'unordered': unordered,
        'summary': {k: dict(sorted(v.items())) for k, v in summary.items()},
        'diagnostics': D.sort(out),
    }
    result['set_id'] = sha256_json(result)
    return result
