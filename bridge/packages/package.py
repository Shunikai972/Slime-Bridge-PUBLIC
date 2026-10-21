"""``bridge-generated-package`` v1: the installable unit produced from one Phase 1 conversion.

A generated package is deterministic metadata: identity/lineage, source and target binding,
converter identity, content graph hash, dependency hashes, the Phase 1 target package,
registration plan and verification checks, plus an artifact manifest of confined relative paths.
It is first *planned* (artifact digests unknown) and later *materialized* from validated local
extraction receipts. Proprietary bytes never enter the manifest; artifact files live in an
ignored local store and are checked by digest. ``package_id`` is the content address of the
manifest, so any change produces a new id.
"""

import copy

from bridge.content.canonical import Status, sha256_json
from bridge.content.ir import ir_hash
from bridge.core import diagnostics as D
from bridge.core import schema as S
from bridge.core.paths import hash_file, resolve_confined, safe_relative

PACKAGE_SCHEMA = 'bridge-generated-package'
PACKAGE_SCHEMA_VERSION = 1
CONVERSION_SCHEMA = 1
REALMS = ('SR1', 'SR2')

_LINEAGE = S.obj({'canonical_id': S.string(r'slime\.[a-z0-9]+(?:_[a-z0-9]+)*'), 'source_realm': S.enum(*REALMS),
                  'target_realm': S.enum(*REALMS), 'direction': S.string(r'SR[12]->SR[12]')})
_ARTIFACT = S.obj({'path': S.string(max_length=1024), 'kind': S.string(), 'source_node': S.string(max_length=1024),
                   'source_hash': S.sha256(), 'required': S.boolean(), 'transform': S.string(),
                   'sha256': S.optional(S.sha256()), 'missing': S.boolean()})
_SPEC = S.obj({
    'schema': S.enum(PACKAGE_SCHEMA), 'schema_version': S.enum(PACKAGE_SCHEMA_VERSION),
    'package_id': S.sha256(), 'lineage': _LINEAGE,
    'canonical_ids': S.obj({'species': S.string(), 'plorts': S.list_of(S.string(), unique=True)}),
    'status': S.enum(str(Status.EXPERIMENTAL), str(Status.PENDING_ADAPTER), str(Status.QUARANTINED)),
    'status_reasons': S.list_of(S.string(max_length=2048)), 'synthetic': S.boolean(),
    'source': S.obj({'realm': S.enum(*REALMS), 'build_fingerprint': S.string(), 'definition_hash': S.optional(S.sha256()),
                     'closure_hash': S.optional(S.sha256()), 'data_evidence': S.optional(S.string()),
                     'dependency_hashes': S.list_of(S.obj({'id': S.string(max_length=1024), 'kind': S.string(),
                                                           'hash': S.optional(S.sha256()),
                                                           'resolution': S.string(), 'critical': S.boolean()}))}),
    'target': S.obj({'realm': S.enum(*REALMS), 'loader': S.string(), 'runtime': S.string(), 'unity': S.string(),
                     'build_compatibility': S.obj({'policy': S.enum('EXACT', 'UNBOUND'),
                                                   'fingerprints': S.list_of(S.string(), unique=True)})}),
    'converter': S.any_json(), 'conversion_schema': S.integer(minimum=1),
    'content_graph_hash': S.optional(S.sha256()),
    'artifacts': S.list_of(_ARTIFACT), 'materialized': S.boolean(),
    'target_package': S.optional(S.any_json()), 'registration_plan': S.optional(S.any_json()),
    'runtime_checks': S.list_of(S.string(max_length=256)),
    'behaviour_requirements': S.list_of(S.any_json()),
    'provenance': S.obj({'conversion_result_hash': S.sha256(), 'phase1_package_id': S.optional(S.sha256()),
                         'registration_plan_hash': S.optional(S.sha256()), 'stage_hashes': S.any_json()}),
    'diagnostics': S.list_of(S.any_json()),
})


def _converter_tuple(converter):
    try:
        return tuple(int(x) for x in str(converter['converter_version']).split('.'))
    except (KeyError, TypeError, ValueError):
        return ()


def _seal(pkg):
    body = {k: v for k, v in pkg.items() if k != 'package_id'}
    pkg['package_id'] = sha256_json(body)
    return pkg


def build_generated_package(result, *, dependency_hashes=()):
    """Generated package from a Phase 1 ``bridge-conversion-result`` (never raises on bad species)."""
    products = result.get('products') or {}
    target_package = products.get('package')
    plan = products.get('registration_plan')
    ir = products.get('ir')
    cid = result['canonical_id']
    source_realm, target_realm = result['direction'].split('->')
    final = result['final']
    diags = []
    for stage in result.get('stages', []):
        for item in stage['diagnostics']:
            if item['severity'] in ('error', 'warning'):
                diags.append(D.from_phase1(item, 'conversion', content_id=cid, realm=source_realm))
    artifacts = []
    for item in (target_package or {}).get('artifacts', []):
        artifacts.append({'path': 'artifacts/' + item['artifact'], 'kind': item['kind'],
                          'source_node': item['source_node'], 'source_hash': item['source_hash'],
                          'required': item['required'], 'transform': item['transform'], 'sha256': None,
                          'missing': False})
    target = (target_package or {}).get('target') or {'realm': target_realm, 'loader': 'unknown',
                                                     'runtime': 'unknown', 'unity': 'unknown', 'build_fingerprint': None}
    build = target.get('build_fingerprint')
    behaviour = (target_package or {}).get('behaviour') or {'capabilities': []}
    pkg = {
        'schema': PACKAGE_SCHEMA, 'schema_version': PACKAGE_SCHEMA_VERSION,
        'lineage': {'canonical_id': cid, 'source_realm': source_realm, 'target_realm': target_realm,
                    'direction': result['direction']},
        'canonical_ids': {'species': cid, 'plorts': sorted({p['canonical_id'] for p in
                                                            ((target_package or {}).get('gameplay') or {}).get('plorts', [])})},
        'status': final if final in (Status.EXPERIMENTAL, Status.PENDING_ADAPTER) else str(Status.QUARANTINED),
        'status_reasons': sorted(result.get('reasons', [])), 'synthetic': bool(result.get('synthetic')),
        'source': {'realm': source_realm,
                   'build_fingerprint': (ir or {}).get('identity', {}).get('source_build_fingerprint')
                   or (target_package or {}).get('source', {}).get('build_fingerprint') or 'unknown',
                   'definition_hash': (target_package or {}).get('source', {}).get('definition_hash'),
                   'closure_hash': (target_package or {}).get('source', {}).get('closure_hash'),
                   'data_evidence': (ir or {}).get('provenance', {}).get('data_evidence'),
                   'dependency_hashes': [{'id': d['id'], 'kind': str(d['kind']), 'hash': d['hash'],
                                          'resolution': str(d['resolution']), 'critical': d['critical']}
                                         for d in sorted(dependency_hashes, key=lambda d: d['id'])]},
        'target': {'realm': target_realm, 'loader': target['loader'], 'runtime': target['runtime'],
                   'unity': target['unity'],
                   'build_compatibility': {'policy': 'EXACT' if build else 'UNBOUND',
                                           'fingerprints': [build] if build else []}},
        'converter': result['converter'], 'conversion_schema': CONVERSION_SCHEMA,
        'content_graph_hash': ir_hash(ir) if ir and final != Status.QUARANTINED else None,
        'artifacts': sorted(artifacts, key=lambda a: a['path']), 'materialized': False,
        'target_package': target_package, 'registration_plan': plan,
        'runtime_checks': sorted(c['check'] for c in ((products.get('verification_plan') or {}).get('checks', []))),
        'behaviour_requirements': [{'capability': c['id'], 'mode': c['mode'], 'adapter': c.get('adapter'),
                                    'policy': c['policy'], 'status': c['status']}
                                   for c in behaviour.get('capabilities', [])],
        'provenance': {'conversion_result_hash': sha256_json(result),
                       'phase1_package_id': (target_package or {}).get('package_id'),
                       'registration_plan_hash': (plan or {}).get('plan_hash'),
                       'stage_hashes': {s['stage']: s['output_hash'] for s in result.get('stages', [])}},
        'diagnostics': D.sort(diags),
    }
    return _seal(pkg)


def validate_package(pkg):
    """Unified diagnostics for an untrusted package manifest (empty list = structurally valid)."""
    def problem(code, message, **details):
        return D.make(code, 'error', 'package', message, details=details,
                      content_id=(pkg.get('lineage') or {}).get('canonical_id') if isinstance(pkg, dict) else None)
    issues = S.check(_SPEC, pkg)
    if issues:
        return [problem('PACKAGE_SCHEMA_INVALID', 'manifest does not match bridge-generated-package v1',
                        problems=issues[:20])]
    out = []
    if _seal(copy.deepcopy(pkg))['package_id'] != pkg['package_id']:
        out.append(problem('PACKAGE_ID_MISMATCH', 'package_id is not the content address of the manifest'))
    lineage = pkg['lineage']
    if lineage['direction'] != f"{lineage['source_realm']}->{lineage['target_realm']}" or \
            lineage['source_realm'] == lineage['target_realm']:
        out.append(problem('LINEAGE_INVALID', 'direction must join two different realms'))
    if pkg['source']['realm'] != lineage['source_realm'] or pkg['target']['realm'] != lineage['target_realm'] or \
            pkg['canonical_ids']['species'] != lineage['canonical_id']:
        out.append(problem('LINEAGE_INCONSISTENT', 'source/target/canonical fields disagree with lineage'))
    paths = [a['path'] for a in pkg['artifacts']]
    for path in paths:
        if not safe_relative(path) or not path.startswith('artifacts/'):
            out.append(problem('ARTIFACT_PATH_UNSAFE', 'artifact path escapes the package store', path=path))
    if len(set(paths)) != len(paths):
        out.append(problem('ARTIFACT_PATH_DUPLICATE', 'two artifacts share a path'))
    for artifact in pkg['artifacts']:
        if pkg['materialized'] and artifact['required'] and artifact['sha256'] is None:
            out.append(problem('ARTIFACT_NOT_MATERIALIZED', 'required artifact has no digest', path=artifact['path']))
        if artifact['missing'] and artifact['required']:
            out.append(problem('REQUIRED_ARTIFACT_MISSING', 'required artifact missing', path=artifact['path']))
    plan, target_package = pkg['registration_plan'], pkg['target_package']
    quarantined = pkg['status'] == Status.QUARANTINED
    if not quarantined:
        if not isinstance(target_package, dict) or not isinstance(plan, dict):
            out.append(problem('PACKAGE_INCOMPLETE', 'non-quarantined package needs target package and plan'))
        else:
            if plan.get('package_id') != target_package.get('package_id') or \
                    pkg['provenance']['phase1_package_id'] != target_package.get('package_id'):
                out.append(problem('PLAN_PACKAGE_MISMATCH', 'registration plan built for another target package'))
            if any(op.get('canonical_id') != lineage['canonical_id'] for op in plan.get('operations', [])):
                out.append(problem('FOREIGN_REGISTRATION', 'registration operation for another canonical id'))
            if plan.get('target_realm') != lineage['target_realm'] or \
                    target_package.get('canonical_id') != lineage['canonical_id']:
                out.append(problem('LINEAGE_INCONSISTENT', 'target package disagrees with lineage'))
            if bool(plan.get('synthetic')) != pkg['synthetic'] or bool(target_package.get('synthetic')) != pkg['synthetic']:
                out.append(problem('EVIDENCE_CONTRADICTION', 'synthetic flags disagree'))
    return D.sort(out)


def installable(pkg):
    """A package can be deployed only if valid, not quarantined, materialized and registrable."""
    plan = pkg.get('registration_plan') or {}
    return (not validate_package(pkg) and pkg['status'] != Status.QUARANTINED and pkg['materialized'] and
            plan.get('registrable') is True and not any(a['missing'] and a['required'] for a in pkg['artifacts']))


def materialize(pkg, receipts):
    """New package with artifact digests from Phase 1 extraction receipts (validated, never trusted).

    Receipts must name the planned node, the planned source hash, the run's synthetic flag and the
    planned location. Optional artifacts may be missing; a required one makes the result invalid.
    """
    out = copy.deepcopy(pkg)
    by_node = {}
    diags = list(out['diagnostics'])
    for receipt in receipts:
        if isinstance(receipt, dict) and isinstance(receipt.get('node'), str):
            if receipt['node'] in by_node:
                diags.append(D.make('RECEIPT_DUPLICATE', 'error', 'package', 'two receipts for one node',
                                    subject=receipt['node']))
            by_node[receipt['node']] = receipt
    for artifact in out['artifacts']:
        receipt = by_node.get(artifact['source_node'])
        ok = (isinstance(receipt, dict) and receipt.get('status') == 'EXTRACTED' and
              receipt.get('source_hash') == artifact['source_hash'] and receipt.get('synthetic') is out['synthetic'] and
              'artifacts/' + str(receipt.get('location')) == artifact['path'] and
              isinstance(receipt.get('sha256'), str) and len(receipt['sha256']) == 64)
        if ok:
            artifact['sha256'], artifact['missing'] = receipt['sha256'], False
        else:
            artifact['sha256'], artifact['missing'] = None, True
            diags.append(D.make('ARTIFACT_RECEIPT_REJECTED' if receipt else 'ARTIFACT_RECEIPT_MISSING',
                                'error' if artifact['required'] else 'warning', 'package',
                                'artifact digest unavailable', subject=artifact['path']))
    out['materialized'] = True
    out['diagnostics'] = D.sort(diags)
    return _seal(out)


def verify_artifact_store(pkg, root):
    """Re-hash every materialized artifact under ``root`` (read-only, link-refusing)."""
    out = []
    for artifact in pkg['artifacts']:
        if artifact['sha256'] is None:
            continue
        path = resolve_confined(root, artifact['path'])
        try:
            if path is None:
                raise ValueError('unsafe or linked path')
            digest, _ = hash_file(path)
        except (OSError, ValueError) as exc:
            out.append(D.make('ARTIFACT_UNREADABLE', 'error', 'package', str(exc)[:200], subject=artifact['path']))
            continue
        if digest != artifact['sha256']:
            out.append(D.make('ARTIFACT_DIGEST_MISMATCH', 'error', 'package', 'file differs from manifest',
                              subject=artifact['path']))
    return D.sort(out)


def target_compatibility(pkg, target_realm, target_build):
    """COMPATIBLE / REVERIFY_REQUIRED / INCOMPATIBLE. A build change never means deletion."""
    if pkg['target']['realm'] != target_realm:
        return 'INCOMPATIBLE'
    compat = pkg['target']['build_compatibility']
    if compat['policy'] == 'EXACT' and target_build in compat['fingerprints']:
        return 'COMPATIBLE'
    return 'REVERIFY_REQUIRED'


def compare(installed, candidate):
    """IDENTICAL / UPGRADE / DOWNGRADE / REBUILD / INCOMPATIBLE for two packages of one lineage."""
    if installed['lineage'] != candidate['lineage']:
        return 'INCOMPATIBLE'
    if installed['package_id'] == candidate['package_id']:
        return 'IDENTICAL'
    old, new = _converter_tuple(installed['converter']), _converter_tuple(candidate['converter'])
    if new > old:
        return 'UPGRADE'
    if new < old:
        return 'DOWNGRADE'
    return 'REBUILD'


def diff(old, new):
    """Structured, deterministic difference between two packages."""
    def by(items, key):
        return {item[key]: item for item in items}
    a, b = by(old['artifacts'], 'path'), by(new['artifacts'], 'path')
    ops_a = {op['op_id'] for op in (old.get('registration_plan') or {}).get('operations', [])}
    ops_b = {op['op_id'] for op in (new.get('registration_plan') or {}).get('operations', [])}
    keys = sorted(set(old) | set(new))
    return {'relation': compare(old, new),
            'fields_changed': [k for k in keys if k != 'package_id' and sha256_json(old.get(k)) != sha256_json(new.get(k))],
            'artifacts': {'added': sorted(set(b) - set(a)), 'removed': sorted(set(a) - set(b)),
                          'changed': sorted(p for p in set(a) & set(b) if a[p] != b[p])},
            'registration_ops': {'added': sorted(ops_b - ops_a), 'removed': sorted(ops_a - ops_b)}}


def quarantine_record(pkg, reasons, subsystem='package'):
    """External quarantine state (the package itself is immutable)."""
    return {'package_id': pkg.get('package_id'), 'lineage': pkg.get('lineage'), 'reasons': sorted(set(reasons)),
            'diagnostics': D.sort([D.make('PACKAGE_QUARANTINED', 'error', subsystem, reason,
                                          content_id=(pkg.get('lineage') or {}).get('canonical_id'))
                                   for reason in sorted(set(reasons))])}
