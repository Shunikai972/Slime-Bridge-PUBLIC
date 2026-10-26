"""``bridge-proof-plan`` v1 and receipt aggregation.

A proof plan lists requirements in sections. For generated packages:

- SOURCE: definition identity, runtime-anchored registry, dependency closure, build binding
- CANONICAL_IR: canonical id, renderable nodes, material semantics, behaviour classification,
  plort/icon/pedia relations
- TARGET_PACKAGE: materialized artifacts, registrable plan, exact target binding, behaviour
  adapter readiness
- RUNTIME: spawn, mesh visible, materials, face, animation, diet, favourite, plort production,
  market, icon, pedia, save, exit, reload, plus one check per capability realization

Static sections can be evaluated in Cloud (STATIC_ANALYSIS receipts from the static verifier).
RUNTIME requirements accept only runtime evidence (observation/native/disk readback) from a
provider trusted for exactly that realm; synthetic receipts never satisfy them, so Cloud can
never mark a runtime requirement PASSED.
"""

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from .receipts import RUNTIME_EVIDENCE, make_receipt, validate_receipt

PLAN_SCHEMA = 'bridge-proof-plan'
SECTIONS = ('SOURCE', 'CANONICAL_IR', 'TARGET_PACKAGE', 'RUNTIME', 'COUNTERPART', 'TRANSITION', 'MULTIPLAYER',
            'PROVIDER')
STATIC_VERIFIER = {'provider_id': 'bridge.static-verifier', 'provider_version': '1.0.0'}
RUNTIME_SECTIONS = ('RUNTIME', 'COUNTERPART', 'TRANSITION', 'MULTIPLAYER')
RUNTIME_CHECKS = (('runtime.actor_spawned', 'actor spawns in the target realm'),
                  ('runtime.mesh_visible', 'correct mesh is visible'),
                  ('runtime.materials_valid', 'materials render as planned'),
                  ('runtime.face_valid', 'face/expressions display'),
                  ('runtime.animations_operational', 'animator and clips run'),
                  ('runtime.diet', 'eats its food groups'),
                  ('runtime.favourite', 'favourite food behaviour'),
                  ('runtime.plort_production', 'produces its plort'),
                  ('runtime.market', 'plort sells through the market'),
                  ('runtime.icon', 'icon appears in UI'),
                  ('runtime.pedia', 'pedia/localization entries resolve'),
                  ('runtime.save', 'save contains the converted content'),
                  ('runtime.exit', 'game exits cleanly'),
                  ('runtime.reload', 'reload restores the converted content'))


def requirement(check_id, section, description, *, accepted, realm=None, build=None, blocking=True,
                not_applicable_allowed=False):
    return {'check_id': check_id, 'section': section, 'description': description, 'accepted_evidence': sorted(accepted),
            'realm': realm, 'build': build, 'blocking': blocking, 'not_applicable_allowed': not_applicable_allowed}


def make_plan(subject_kind, subject_id, requirements, *, realm=None):
    ids = [r['check_id'] for r in requirements]
    if len(ids) != len(set(ids)) or any(r['section'] not in SECTIONS for r in requirements):
        raise ValueError('requirements need unique ids and known sections')
    plan = {'schema': PLAN_SCHEMA, 'schema_version': 1, 'subject_kind': subject_kind, 'subject_id': subject_id,
            'realm': realm, 'requirements': sorted(requirements, key=lambda r: (SECTIONS.index(r['section']),
                                                                               r['check_id']))}
    plan['plan_id'] = sha256_json(plan)
    return plan


def package_plan(package):
    """Proof plan for one generated package (source realm for SOURCE, target realm for RUNTIME)."""
    static = ['STATIC_ANALYSIS']
    runtime = list(RUNTIME_EVIDENCE)
    src, tgt = package['lineage']['source_realm'], package['lineage']['target_realm']
    target_build = (package['target']['build_compatibility']['fingerprints'] or [None])[0]
    reqs = [requirement('source.definition_identity', 'SOURCE', 'source definition identity and hash', accepted=static),
            requirement('source.registry_proof', 'SOURCE', 'registry traversal is runtime-anchored', accepted=static),
            requirement('source.dependency_closure', 'SOURCE', 'closure hash recorded, no missing critical nodes',
                        accepted=static),
            requirement('source.build_binding', 'SOURCE', 'source build fingerprint bound', accepted=static),
            requirement('ir.canonical_id', 'CANONICAL_IR', 'canonical id and IR hash', accepted=static),
            requirement('ir.required_nodes', 'CANONICAL_IR', 'renderable root/mesh present', accepted=static),
            requirement('ir.material_semantics', 'CANONICAL_IR', 'every essential material converted', accepted=static),
            requirement('ir.behaviour_classification', 'CANONICAL_IR', 'no unknown behaviour', accepted=static),
            requirement('ir.relations', 'CANONICAL_IR', 'plort/icon/pedia relations present', accepted=static),
            requirement('package.artifacts', 'TARGET_PACKAGE', 'all required artifacts materialized', accepted=static),
            requirement('package.registration_plan', 'TARGET_PACKAGE', 'registration plan registrable', accepted=static),
            requirement('package.target_binding', 'TARGET_PACKAGE', 'exact target build binding', accepted=static),
            requirement('package.behaviour_requirements', 'TARGET_PACKAGE', 'behaviour adapters ready', accepted=static)]
    for check_id, text in RUNTIME_CHECKS:
        optional = check_id in ('runtime.face_valid', 'runtime.animations_operational', 'runtime.pedia')
        reqs.append(requirement(check_id, 'RUNTIME', text, accepted=runtime, realm=tgt, build=target_build,
                                not_applicable_allowed=optional or check_id in ('runtime.diet', 'runtime.favourite',
                                                                                'runtime.plort_production',
                                                                                'runtime.market')))
    for cap in package['behaviour_requirements']:
        if cap['mode'] != 'UNSUPPORTED':
            reqs.append(requirement(f"runtime.capability:{cap['capability']}", 'RUNTIME',
                                    f"{cap['mode']} realization of {cap['capability']}", accepted=runtime, realm=tgt,
                                    build=target_build, blocking=cap['policy'] != 'DEGRADABLE'))
    plan = make_plan('package', package['package_id'], reqs, realm=tgt)
    plan['binding'] = {'source_realm': src, 'source_build': package['source']['build_fingerprint'],
                       'target_realm': tgt, 'target_build': target_build}
    return plan


def evaluate_static(plan, package):
    """STATIC_ANALYSIS receipts for every non-runtime package requirement (deterministic, no clock)."""
    tp = package.get('target_package') or {}
    plan_reg = package.get('registration_plan') or {}
    deps = package['source']['dependency_hashes']
    quarantined = package['status'] == 'QUARANTINED'
    reasons = set(package['status_reasons'])
    checks = {
        'source.definition_identity': (bool(package['source']['definition_hash']), 'definition hash missing'),
        'source.registry_proof': (package['source']['data_evidence'] == 'RUNTIME_ANCHORED',
                                  f"source evidence {package['source']['data_evidence']}"),
        'source.dependency_closure': (bool(package['source']['closure_hash']) and not any(
            d['critical'] and d['resolution'] != 'RESOLVED' for d in deps), 'closure missing or critical gaps'),
        'source.build_binding': (package['source']['build_fingerprint'] not in (None, 'unknown'), 'no source build'),
        'ir.canonical_id': (package['content_graph_hash'] is not None, 'no valid IR'),
        'ir.required_nodes': (not quarantined and bool(tp.get('prefab', {}).get('renderers')), 'no renderable mesh'),
        'ir.material_semantics': ('MATERIAL_PENDING_ADAPTER' not in reasons and not quarantined,
                                  'essential material not converted'),
        'ir.behaviour_classification': (not any(c['capability'].startswith('unknown.')
                                                for c in package['behaviour_requirements']), 'unknown behaviour'),
        'ir.relations': (bool(package['canonical_ids']['plorts']) and bool((tp.get('associated') or {}).get('icon')),
                         'plort or icon relation missing'),
        'package.artifacts': (package['materialized'] and not any(a['required'] and a['sha256'] is None
                                                                  for a in package['artifacts']), 'not materialized'),
        'package.registration_plan': (plan_reg.get('registrable') is True, 'plan not registrable'),
        'package.target_binding': (package['target']['build_compatibility']['policy'] == 'EXACT',
                                   'target build unbound'),
        'package.behaviour_requirements': (not any(c['status'] != 'READY' and c['policy'] != 'DEGRADABLE'
                                                   for c in package['behaviour_requirements']), 'adapter pending'),
    }
    receipts = []
    for req in plan['requirements']:
        if req['section'] == 'RUNTIME' or req['check_id'] not in checks:
            continue
        ok, why = checks[req['check_id']]
        blocked = req['check_id'] == 'source.registry_proof' and not ok
        status = 'PASSED' if ok else ('BLOCKED' if blocked else 'FAILED')
        diags = [] if ok else [D.make('STATIC_CHECK_' + ('BLOCKED' if blocked else 'FAILED'), 'warning',
                                      'verification', why, subject=req['check_id'],
                                      evidence='NEEDS_LOCAL_RUNTIME_PROOF' if blocked else None)]
        receipts.append(make_receipt(req['check_id'], plan['subject_kind'], plan['subject_id'], status,
                                     'STATIC_ANALYSIS', provider=STATIC_VERIFIER, diagnostics=diags))
    return receipts


def aggregate(plan, receipts, *, trust):
    """Requirement statuses from untrusted receipts.

    ``trust`` maps provider_id -> realm it may attest for (or '*' only for the static verifier).
    Returns per-requirement status (PASSED/FAILED/BLOCKED/NOT_APPLICABLE/NOT_RUN/CONFLICT), the
    accepted receipt ids, rejected receipts with reasons, and an overall verdict
    VERIFIED / FAILED / INCOMPLETE.
    """
    by_check = {r['check_id']: r for r in plan['requirements']}
    accepted = {cid: [] for cid in by_check}
    rejected = []
    for receipt in receipts:
        problems = validate_receipt(receipt)
        reason = None
        if problems:
            reason = 'INVALID'
        elif receipt['subject_kind'] != plan['subject_kind'] or receipt['subject_id'] != plan['subject_id']:
            reason = 'WRONG_SUBJECT'
        elif receipt['check_id'] not in by_check:
            reason = 'UNKNOWN_CHECK'
        else:
            req = by_check[receipt['check_id']]
            provider = (receipt['provider'] or {}).get('provider_id')
            waiver = receipt['status'] in ('NOT_APPLICABLE', 'BLOCKED')
            if receipt['status'] in ('PASSED', 'FAILED') and receipt['evidence_type'] not in req['accepted_evidence']:
                reason = 'EVIDENCE_NOT_ACCEPTED'
            elif receipt['evidence_type'] == 'SYNTHETIC' and req['section'] in RUNTIME_SECTIONS:
                reason = 'SYNTHETIC_ON_RUNTIME'
            elif req['realm'] is not None and (receipt['evidence_type'] in RUNTIME_EVIDENCE or waiver) and \
                    receipt['realm'] != req['realm']:
                reason = 'WRONG_REALM'
            elif req['build'] is not None and receipt['evidence_type'] in RUNTIME_EVIDENCE and \
                    receipt['build_fingerprint'] != req['build']:
                reason = 'WRONG_BUILD'
            elif provider is not None and (
                    trust.get(provider) is None or
                    (trust[provider] == '*' and receipt['evidence_type'] != 'STATIC_ANALYSIS') or
                    (trust[provider] != '*' and trust[provider] != receipt['realm'])):
                reason = 'UNTRUSTED_PROVIDER'
            elif receipt['status'] == 'NOT_APPLICABLE' and not req['not_applicable_allowed']:
                reason = 'NOT_APPLICABLE_FORBIDDEN'
        if reason:
            rejected.append({'receipt_id': receipt.get('receipt_id') if isinstance(receipt, dict) else None,
                             'check_id': receipt.get('check_id') if isinstance(receipt, dict) else None,
                             'reason': reason})
        else:
            accepted[receipt['check_id']].append(receipt)
    results = {}
    for cid, items in accepted.items():
        statuses = {r['status'] for r in items}
        if {'PASSED', 'FAILED'} <= statuses:
            status = 'CONFLICT'
        else:
            status = next((s for s in ('FAILED', 'PASSED', 'BLOCKED', 'NOT_APPLICABLE') if s in statuses), 'NOT_RUN')
        results[cid] = {'status': status, 'receipts': sorted(r['receipt_id'] for r in items),
                        'section': by_check[cid]['section'], 'blocking': by_check[cid]['blocking']}
    blocking = [v for v in results.values() if v['blocking']]
    if any(v['status'] in ('FAILED', 'CONFLICT') for v in blocking):
        verdict = 'FAILED'
    elif all(v['status'] in ('PASSED', 'NOT_APPLICABLE') for v in blocking):
        verdict = 'VERIFIED'
    else:
        verdict = 'INCOMPLETE'
    sections = {}
    for v in results.values():
        sections.setdefault(v['section'], set()).add(v['status'])
    return {'plan_id': plan['plan_id'], 'verdict': verdict, 'requirements': dict(sorted(results.items())),
            'sections': {k: sorted(s) for k, s in sorted(sections.items())},
            'rejected': sorted(rejected, key=lambda r: (str(r['check_id']), str(r['receipt_id'])))}
