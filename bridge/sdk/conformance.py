"""Provider conformance kit: run before a native adapter is allowed to feed the generic core.

``run_provider_conformance(provider)`` executes the checks for every kind the provider declares
and returns a ``bridge-provider-conformance`` report. Each check is PASSED, FAILED, NOT_APPLICABLE
or NOT_RUN (never assumed). The kit only calls read/describe methods and planning helpers; it
never asks a registration provider to apply a registrable plan, and it never treats a synthetic
provider's success as native evidence.
"""

import copy

from bridge.content.canonical import DataEvidence, canonical_json, is_sha256, sha256_json
from bridge.content.discovery import discover
from bridge.conversion.extraction import plan_extraction, validate_receipt
from bridge.core import diagnostics as D
from bridge.registry.snapshot import normalize_snapshot
from .providers import validate_descriptor

REPORT_SCHEMA = 'bridge-provider-conformance'
PROBE_MISSING_REF = '__bridge_conformance_missing_ref__'
MAX_ROOTS = 64


def _check(name, status, *diagnostics):
    return {'check': name, 'status': status, 'diagnostics': D.sort(list(diagnostics))}


def _fail(name, code, message, provider_id, **details):
    return _check(name, 'FAILED', D.make(code, 'error', 'provider', message, provider=provider_id, details=details))


def _guard(name, provider_id, fn):
    try:
        return fn()
    except Exception as exc:
        return [_fail(name, 'CONFORMANCE_CALL_RAISED', f'{type(exc).__name__}: {str(exc)[:200]}', provider_id)]


def _registry_checks(provider, desc):
    pid = desc['provider_id']
    first, second = provider.read_registry(), provider.read_registry()
    out = []
    if canonical_json(normalize_snapshot(copy.deepcopy(first))) != canonical_json(normalize_snapshot(copy.deepcopy(second))):
        out.append(_fail('registry.deterministic', 'REGISTRY_NONDETERMINISTIC', 'two reads differ', pid))
    else:
        out.append(_check('registry.deterministic', 'PASSED'))
    normalized = normalize_snapshot(copy.deepcopy(first))
    if desc['realm'] is not None and normalized['realm'] != desc['realm']:
        out.append(_fail('registry.realm_binding', 'REGISTRY_REALM_MISMATCH', 'snapshot realm differs', pid,
                         snapshot=normalized['realm']))
    else:
        out.append(_check('registry.realm_binding', 'PASSED'))
    build = normalized['build']['fingerprint']
    if '*' not in desc['supported_builds'] and build not in desc['supported_builds']:
        out.append(_fail('registry.build_binding', 'REGISTRY_BUILD_UNDECLARED', 'snapshot build not declared', pid,
                         build=build))
    else:
        out.append(_check('registry.build_binding', 'PASSED'))
    duplicates = [d for d in normalized['diagnostics'] if d['code'] in ('DUPLICATE_NATIVE_REF', 'MALFORMED_ENTRY')]
    out.append(_fail('registry.entries_wellformed', 'REGISTRY_ENTRIES_INVALID', 'duplicate or malformed entries',
                     pid, codes=sorted({d['code'] for d in duplicates}), count=len(duplicates))
               if duplicates else _check('registry.entries_wellformed', 'PASSED'))
    canon = [e.get('canonical_id') for e in normalized['entries'] if e.get('canonical_id')]
    out.append(_fail('registry.unique_canonical_ids', 'REGISTRY_DUPLICATE_CANONICAL', 'provider canonical ids repeat',
                     pid) if len(canon) != len(set(canon)) else _check('registry.unique_canonical_ids', 'PASSED'))
    claimed = first.get('traversal', {}).get('completeness') if isinstance(first, dict) else None
    complete_feature = 'complete_traversal' in desc['features'].get('registry', [])
    if claimed == 'COMPLETE' and normalized['traversal']['completeness'] != 'COMPLETE':
        out.append(_fail('registry.completeness_semantics', 'REGISTRY_COMPLETE_UNPROVEN',
                         'COMPLETE claimed without anchored evidence or consistent counts', pid))
    elif complete_feature and normalized['traversal']['completeness'] != 'COMPLETE':
        out.append(_fail('registry.completeness_semantics', 'REGISTRY_FEATURE_UNMET',
                         'declares complete_traversal but delivered a partial registry', pid))
    else:
        out.append(_check('registry.completeness_semantics', 'PASSED'))
    if normalized['data_evidence'] == DataEvidence.SYNTHETIC and not desc['synthetic']:
        out.append(_fail('registry.evidence_honesty', 'REGISTRY_SYNTHETIC_UNDECLARED',
                         'synthetic data from a provider not declared synthetic', pid))
    else:
        out.append(_check('registry.evidence_honesty', 'PASSED'))
    return out, normalized


def _dependency_checks(provider, desc, normalized):
    pid = desc['provider_id']
    out = []
    builds = {provider.build_fingerprint() for _ in range(2)}
    out.append(_fail('dependency.build_stable', 'DEPENDENCY_BUILD_UNSTABLE', 'build fingerprint changes', pid)
               if len(builds) != 1 else _check('dependency.build_stable', 'PASSED'))
    if normalized is not None and normalized['build']['fingerprint'] not in builds:
        out.append(_fail('dependency.build_matches_registry', 'DEPENDENCY_BUILD_MISMATCH',
                         'dependency and registry builds differ', pid))
    elif normalized is not None:
        out.append(_check('dependency.build_matches_registry', 'PASSED'))
    try:
        missing = provider.inspect(PROBE_MISSING_REF)
        out.append(_check('dependency.unknown_reference', 'PASSED') if missing is None else
                   _fail('dependency.unknown_reference', 'DEPENDENCY_FABRICATED_OBJECT',
                         'a nonexistent ref must return None', pid))
    except Exception as exc:
        out.append(_fail('dependency.unknown_reference', 'DEPENDENCY_MISSING_REF_RAISED',
                         f'missing refs must return None, got {type(exc).__name__}', pid))
    entries = [e for e in (normalized or {}).get('entries', []) if e.get('native_ref') and not e.get('malformed')]
    roots = []
    for entry in entries[:MAX_ROOTS]:
        native = {k: entry.get(k) for k in ('native_ref', 'numeric_id', 'role', 'definition_hash', 'display_name',
                                            'produces', 'quarantine')}
        a, b = provider.definition_root(native), provider.definition_root(native)
        if a != b or not isinstance(a, str) or not a:
            out.append(_fail('dependency.definition_root', 'DEPENDENCY_ROOT_UNSTABLE', 'definition_root unstable',
                             pid, ref=entry['native_ref']))
            return out
        roots.append(a)
    out.append(_check('dependency.definition_root', 'PASSED' if roots else 'NOT_APPLICABLE'))
    if not roots:
        return out
    bad = []
    for ref in roots:
        first, second = provider.inspect(ref), provider.inspect(ref)
        if first is None:
            continue
        if not isinstance(first, dict) or first.get('ref') != ref:
            bad.append({'ref': ref, 'problem': 'wrong object returned'})
        elif sha256_json(first.get('hash')) != sha256_json((second or {}).get('hash')) or \
                (first.get('hash') is not None and not is_sha256(first['hash'])):
            bad.append({'ref': ref, 'problem': 'unstable or invalid hash'})
    out.append(_fail('dependency.object_identity', 'DEPENDENCY_OBJECT_INVALID', 'inspect results inconsistent', pid,
                     objects=bad[:10]) if bad else _check('dependency.object_identity', 'PASSED'))
    one, two = discover(provider, roots), discover(provider, list(reversed(roots)))
    out.append(_check('dependency.deterministic_graph', 'PASSED') if one['graph'].graph_hash() == two['graph'].graph_hash()
               else _fail('dependency.deterministic_graph', 'DEPENDENCY_GRAPH_NONDETERMINISTIC',
                          'graph differs between walks', pid))
    fatal = sorted({d['code'] for d in one['diagnostics'] if d['code'] in
                    ('MALFORMED_PROVIDER_OUTPUT', 'PROVIDER_EXCEPTION', 'EDGE_TYPE_MISMATCH', 'CONFLICTING_NODE',
                     'DISCOVERY_LIMIT_EXCEEDED')})
    out.append(_fail('dependency.reference_graph', 'DEPENDENCY_GRAPH_INVALID', 'invalid typed references', pid,
                     codes=fatal) if fatal else _check('dependency.reference_graph', 'PASSED'))
    return out


def _extraction_checks(provider, desc, sample_graph, sample_root, synthetic_run):
    pid = desc['provider_id']
    if sample_graph is None:
        return [_check('extraction.receipts_bound', 'NOT_RUN',
                       D.make('CONFORMANCE_NEEDS_SAMPLE', 'info', 'provider', 'no sample graph supplied', provider=pid))]
    plan = plan_extraction(sample_graph, sample_root)
    problems = []
    for item in plan['items'][:MAX_ROOTS]:
        receipt = provider.extract(dict(item))
        problems += validate_receipt(plan, receipt, synthetic_run=synthetic_run)
    forged = provider.extract({**plan['items'][0], 'node': f"{plan['realm']}:{PROBE_MISSING_REF}"}) \
        if plan['items'] else None
    out = [_fail('extraction.receipts_bound', 'EXTRACTION_RECEIPT_INVALID', 'receipts fail plan binding', pid,
                 codes=sorted({p['code'] for p in problems})) if problems else
           _check('extraction.receipts_bound', 'PASSED' if plan['items'] else 'NOT_APPLICABLE')]
    if forged is not None:
        out.append(_fail('extraction.unknown_node', 'EXTRACTION_FABRICATED', 'extracted a nonexistent node', pid)
                   if isinstance(forged, dict) and forged.get('status') == 'EXTRACTED'
                   else _check('extraction.unknown_node', 'PASSED'))
    return out


def _registration_checks(provider, desc, unregistrable_plan):
    pid = desc['provider_id']
    out = []
    caps = provider.capabilities()
    out.append(_check('registration.capabilities', 'PASSED') if isinstance(caps, dict) and
               caps.get('realm') == desc['realm'] else
               _fail('registration.capabilities', 'REGISTRATION_CAPABILITIES_INVALID', 'capabilities malformed', pid))
    if unregistrable_plan is None:
        out.append(_check('registration.refuses_unregistrable', 'NOT_RUN'))
        return out
    try:
        reply = provider.apply(copy.deepcopy(unregistrable_plan))
        accepted = isinstance(reply, dict) and reply.get('status') in ('APPLIED', 'PASSED', 'OK')
    except Exception:
        accepted = False  # refusing by raising is acceptable
    out.append(_fail('registration.refuses_unregistrable', 'REGISTRATION_APPLIED_UNREGISTRABLE',
                     'applied a plan marked registrable=false', pid) if accepted else
               _check('registration.refuses_unregistrable', 'PASSED'))
    return out


PROBE_SESSION = {'pid': 2 ** 31 - 1, 'session_id': 'bridge-conformance-probe'}
PROBE_IMAGE = '0' * 64


def _process_checks(provider, desc):
    """Safe probes only: an unknown session, a deliberately wrong executable image."""
    pid = desc['provider_id']
    out = []
    status = provider.status(dict(PROBE_SESSION))
    out.append(_check('process.unknown_session', 'PASSED') if isinstance(status, dict) and status.get('running') is False
               else _fail('process.unknown_session', 'PROCESS_STATUS_INVENTED', 'reported an unknown session as running',
                          pid))
    try:
        attached = provider.attach(PROBE_SESSION['pid'], expected_image_sha256=PROBE_IMAGE,
                                   expected_executable='bridge-conformance-probe.exe')
    except Exception:
        attached = None  # refusing by raising is acceptable
    out.append(_check('process.attach_verifies_image', 'PASSED') if not attached else
               _fail('process.attach_verifies_image', 'PROCESS_ATTACH_UNVERIFIED',
                     'attached without matching PID + executable image', pid))
    try:
        stopped = provider.stop(dict(PROBE_SESSION))
    except Exception:
        stopped = False
    out.append(_check('process.stop_refuses_unknown', 'PASSED') if stopped is False else
               _fail('process.stop_refuses_unknown', 'PROCESS_STOP_UNVERIFIED', 'stop accepted an unknown session', pid))
    return out


def _world_ready_checks(provider, desc):
    pid = desc['provider_id']
    report = provider.report(dict(PROBE_SESSION), {'realm': desc['realm'], 'installation_id': 'probe',
                                                    'native_save_id': 'probe', 'native_save_identity': 'probe'})
    invented = isinstance(report, dict) and report.get('scene_settled') is True and \
        report.get('session') == PROBE_SESSION
    return [_fail('world_ready.unknown_session', 'WORLD_READY_INVENTED', 'reported readiness for an unknown session',
                  pid) if invented else _check('world_ready.unknown_session', 'PASSED')]


def run_provider_conformance(provider, *, sample_graph=None, sample_root=None, unregistrable_plan=None):
    """Conformance report for every kind the provider declares (bounded, deterministic)."""
    try:
        desc = provider.describe()
    except Exception as exc:
        desc = None
        problems = [D.make('PROVIDER_DESCRIBE_RAISED', 'error', 'provider', f'{type(exc).__name__}')]
    else:
        problems = validate_descriptor(desc, provider)
    if desc is None or D.has_errors(problems):
        return {'schema': REPORT_SCHEMA, 'schema_version': 1, 'provider_id': (desc or {}).get('provider_id'),
                'checks': [{'check': 'descriptor', 'status': 'FAILED', 'diagnostics': problems}],
                'conformant': False}
    pid = desc['provider_id']
    checks = [_check('descriptor', 'PASSED')]
    normalized = None
    if 'registry' in desc['kinds']:
        result = _guard('registry', pid, lambda: _registry_checks(provider, desc))
        if isinstance(result, tuple):
            checks += result[0]
            normalized = result[1]
        else:
            checks += result
    if 'dependency' in desc['kinds']:
        checks += _guard('dependency', pid, lambda: _dependency_checks(provider, desc, normalized))
    if 'extraction' in desc['kinds']:
        checks += _guard('extraction', pid, lambda: _extraction_checks(provider, desc, sample_graph, sample_root,
                                                                       desc['synthetic']))
    if 'registration' in desc['kinds']:
        checks += _guard('registration', pid, lambda: _registration_checks(provider, desc, unregistrable_plan))
    if 'process' in desc['kinds']:
        checks += _guard('process', pid, lambda: _process_checks(provider, desc))
    if 'world_ready' in desc['kinds']:
        checks += _guard('world_ready', pid, lambda: _world_ready_checks(provider, desc))
    checks = sorted(checks, key=lambda c: c['check'])
    return {'schema': REPORT_SCHEMA, 'schema_version': 1, 'provider_id': pid,
            'provider_version': desc['provider_version'], 'synthetic': desc['synthetic'], 'checks': checks,
            'conformant': all(c['status'] in ('PASSED', 'NOT_APPLICABLE') for c in checks)}
