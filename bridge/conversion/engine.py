"""Staged, deterministic, per-species conversion engine (direction-agnostic).

Stages: DISCOVERY -> VALIDATION -> DEPENDENCY_RESOLUTION -> EXTRACTION_PLANNING ->
NEUTRALIZATION -> TARGET_TRANSFORMATION -> PACKAGE_ASSEMBLY -> REGISTRATION_PLANNING ->
VERIFICATION_PLANNING. Each stage records status (OK/DEGRADED/FAILED/SKIPPED), evidence,
diagnostics and input/output hashes. A FAILED stage skips the rest of *this* species only;
exceptions inside a stage are caught and fail closed (QUARANTINED). Cloud output never claims
runtime success: the best final status from a conversion is EXPERIMENTAL.
"""

from dataclasses import dataclass

from bridge.content.canonical import (DataEvidence, Evidence, Status, diag, has_errors, sha256_json,
                                      sort_diagnostics, worst_status)
from bridge.content.discovery import discover
from bridge.content.graph import GRAPH_SCHEMA_VERSION, NodeKind, Resolution
from bridge.content.ir import IR_SCHEMA_VERSION
from bridge.registry.reconcile import CLASSIFICATION_SCHEMA_VERSION
from . import targets
from .capabilities import plan_behaviour, validate_behaviour_profile, validate_target_behaviour_profile
from .extraction import plan_extraction
from .materials import plan_target_material, validate_source_profile, validate_target_profile, worst
from .neutralize import build_ir

CONVERTER_VERSION = '1.0.0'
COMPONENT_VERSIONS = {'ir': IR_SCHEMA_VERSION, 'graph': GRAPH_SCHEMA_VERSION,
                      'classification': CLASSIFICATION_SCHEMA_VERSION, 'materials': 1, 'capabilities': 1,
                      'neutralize': 1, 'extraction': 1, 'targets': 1, 'engine': 1, 'source_cache': 1}
STAGES = ('DISCOVERY', 'VALIDATION', 'DEPENDENCY_RESOLUTION', 'EXTRACTION_PLANNING', 'NEUTRALIZATION',
          'TARGET_TRANSFORMATION', 'PACKAGE_ASSEMBLY', 'REGISTRATION_PLANNING', 'VERIFICATION_PLANNING')
RESULT_SCHEMA = 'bridge-conversion-result'


def converter_identity():
    return {'converter_version': CONVERTER_VERSION, 'components': dict(COMPONENT_VERSIONS)}


@dataclass(frozen=True)
class SourceBundle:
    """Everything realm-specific about the source side (providers + verified data profiles)."""
    provider: object
    material_mapper: object
    material_profile: dict
    behaviour_profile: dict

    @property
    def realm(self):
        return self.provider.realm


def species_row(classification, canonical_id):
    for row in classification['species']:
        if row['canonical_id'] == canonical_id:
            return row
    raise KeyError(canonical_id)


def target_native_canonicals(classification, target_realm):
    """Canonical slimes/plorts that exist natively in the target realm (never re-registered)."""
    # Presence, not a unique mapping: an ambiguous (quarantined) target entry still exists natively.
    return sorted({row['canonical_id'] for bucket in ('species', 'plorts') for row in classification[bucket]
                   if row['present'].get(target_realm)})


def _plort_canonicals(classification, realm, provider):
    """Registry plort identity keyed by native ref AND by the provider's object ref for it."""
    out = {}
    for row in classification['plorts']:
        native = row['native'].get(realm)
        if native:
            out[native['native_ref']] = row['canonical_id']
            out[provider.definition_root(native)] = row['canonical_id']
    return out


def root_for(source, row):
    native = row['native'][source.realm]
    return f'{source.realm}:{source.provider.definition_root(native)}'


_EVIDENCE_RANK = {DataEvidence.SYNTHETIC: 0, DataEvidence.STATIC: 1, DataEvidence.RUNTIME_ANCHORED: 2}


def source_evidence(classification, realm, provider=None):
    """The weaker of the anchor-checked registry evidence and the source's own claim.

    A provider can only lower evidence (e.g. synthetic assets beside an anchored registry), never raise it.
    """
    evidence = DataEvidence(classification['data_evidence'][realm])
    claimed = getattr(provider, 'data_evidence', None)
    if getattr(provider, 'synthetic', False):
        claimed = DataEvidence.SYNTHETIC
    if claimed is not None:
        evidence = min(evidence, DataEvidence(claimed), key=_EVIDENCE_RANK.__getitem__)
    return evidence


def run_is_synthetic(classification, target_profile, provider=None):
    return bool(classification['synthetic'] or target_profile['synthetic'] or getattr(provider, 'synthetic', False))


def cache_inputs(canonical_id, classification, source, target_profile, graph):
    """Everything that can change this species' product, computable before conversion runs."""
    row = species_row(classification, canonical_id)
    root = root_for(source, row)
    analysis = graph.analyze(root) if root in graph.nodes else None
    closure = analysis['closure'] if analysis else []
    shaders, behaviours, plort_refs = set(), set(), set()
    for nid in closure:
        node = graph.nodes[nid]
        if node['kind'] == NodeKind.PLORT_DEFINITION:
            plort_refs.add(node['ref'])
        if node['kind'] == NodeKind.MATERIAL and node['resolution'] == Resolution.RESOLVED:
            shaders.add(source.material_mapper.describe(node, graph).get('shader'))
        if node['kind'] == NodeKind.BEHAVIOUR:
            behaviours.add((node['attributes'] or {}).get('type'))
    native_targets = target_native_canonicals(classification, target_profile['realm'])
    produced = row['native'][source.realm]['produces'] if row['native'].get(source.realm) else []
    return {
        'canonical_id': canonical_id, 'source_realm': source.realm, 'target_realm': target_profile['realm'],
        'source_build': source.provider.build_fingerprint(),
        'target_build': target_profile.get('build_fingerprint'),
        'destination_registry_build': classification['builds'][target_profile['realm']],
        'classification_row': sha256_json(row),
        'definition_hash': (row['native'].get(source.realm) or {}).get('definition_hash'),
        'closure_hash': analysis['closure_hash'] if analysis else None,
        'source_material_entries': sha256_json({s: source.material_profile['shaders'].get(s)
                                                for s in sorted(x for x in shaders if isinstance(x, str))}),
        'unknown_shader_count': sum(1 for s in shaders if not isinstance(s, str)),
        'source_behaviour_entries': sha256_json({b: source.behaviour_profile['components'].get(b)
                                                 for b in sorted(x for x in behaviours if isinstance(x, str))}),
        'target_material_profile': sha256_json(target_profile['material_profile']),
        'target_behaviour_profile': sha256_json(target_profile['behaviour_profile']),
        'target_registration': sha256_json(target_profile['registration']),
        'target_facts': sha256_json({k: target_profile[k] for k in ('loader', 'runtime', 'unity')}),
        'target_native_plorts': sorted(p for p in produced if p in native_targets),
        'plort_identity': {ref: _plort_canonicals(classification, source.realm, source.provider).get(ref)
                           for ref in sorted(plort_refs)},
        'source_evidence': str(source_evidence(classification, source.realm, source.provider)),
        'synthetic': run_is_synthetic(classification, target_profile, source.provider),
        'converter': converter_identity(),
    }


class _Run:
    def __init__(self):
        self.stages, self.failed = [], False

    def stage(self, name, fn, input_hash=None):
        if self.failed:
            self.stages.append({'stage': name, 'status': 'SKIPPED', 'evidence': None, 'diagnostics': [],
                                'input_hash': input_hash, 'output_hash': None})
            return None
        try:
            output, diags, evidence = fn()
            failed = has_errors(diags)
            status = 'FAILED' if failed else ('DEGRADED' if any(d['severity'] == 'warning' for d in diags) else 'OK')
        except Exception as exc:  # fail closed, isolated to this species
            output, failed, status, evidence = None, True, 'FAILED', None
            diags = [diag('STAGE_EXCEPTION', 'error', name, f'{type(exc).__name__}: {str(exc)[:200]}')]
        self.failed = self.failed or failed
        self.stages.append({'stage': name, 'status': status, 'evidence': evidence and str(evidence),
                            'diagnostics': sort_diagnostics(diags), 'input_hash': input_hash,
                            'output_hash': sha256_json(output) if output is not None else None})
        return output


def convert_species(canonical_id, *, classification, source, target_profile, discovery=None, max_nodes=None,
                    source_cache=None):
    """Run all stages for one exclusive species. Never raises for species-level problems.

    ``discovery`` is a shared ``discover()`` result for a batch; without it the species is
    discovered alone. Only diagnostics inside this species' closure are considered.
    ``source_cache`` (optional) is a local materializer with ``materialize(canonical_id) ->
    receipt`` (``bridge.conversion.source_cache``): when the job wants the source-complete
    visual path, PACKAGE_ASSEMBLY also materializes the complete cache for this species; a
    refusal or incomplete coverage fails the stage (BLOCKED), never an inferred selection.
    """
    validate_source_profile(source.material_profile)
    validate_behaviour_profile(source.behaviour_profile)
    targets.validate_target_profile(target_profile)
    validate_target_profile(target_profile['material_profile'])
    validate_target_behaviour_profile(target_profile['behaviour_profile'])
    realm, target_realm = source.realm, target_profile['realm']
    run, ctx = _Run(), {}
    impl = Evidence.IMPLEMENTED

    def stage_discovery():
        row = species_row(classification, canonical_id)
        ctx['row'] = row
        native = row['native'].get(realm)
        if native is None:
            return None, [diag('NOT_PRESENT_IN_SOURCE', 'error', canonical_id, realm)], impl
        ctx['root'] = root_for(source, row)
        found = discovery or discover(source.provider, [ctx['root'].split(':', 1)[1]],
                                      **({'max_nodes': max_nodes} if max_nodes else {}))
        ctx['graph'] = found['graph']
        if ctx['root'] not in ctx['graph'].nodes:
            return None, [diag('ROOT_NOT_DISCOVERED', 'error', ctx['root'], 'definition root missing')], impl
        closure = set(ctx['graph'].analyze(ctx['root'])['closure'])
        # Provider problems are re-judged by criticality in DEPENDENCY_RESOLUTION.
        diags = [{**d, 'severity': 'warning'} if d['severity'] == 'error' else d
                 for d in found['diagnostics'] if d['subject'] in closure]
        truncated = sorted(closure & {f'{realm}:{ref}' for ref in found.get('uninspected', [])})
        if truncated:
            diags.append(diag('DISCOVERY_LIMIT_EXCEEDED', 'error', ctx['root'],
                              f'{len(truncated)} uninspected refs inside this closure'))
        return {'root': ctx['root'], 'closure_size': len(closure)}, diags, impl

    def stage_validation():
        row, diags = ctx['row'], []
        expected = f'{realm}_ONLY'
        if row['classification'] != expected:
            diags.append(diag('NOT_CONVERTIBLE_CLASSIFICATION', 'error', canonical_id,
                              f"{row['classification']} is not {expected}"))
        if row['native'][realm].get('malformed') or row['native'][realm].get('quarantine'):
            diags.append(diag('SOURCE_ENTRY_UNSAFE', 'error', canonical_id, 'malformed or quarantined'))
        node = ctx['graph'].nodes[ctx['root']]
        if node['kind'] != NodeKind.SLIME_DEFINITION or node['resolution'] != Resolution.RESOLVED:
            diags.append(diag('ROOT_NOT_A_RESOLVED_DEFINITION', 'error', ctx['root'],
                              f"{node['kind']}/{node['resolution']}"))
        return {'classification': row['classification']}, diags, impl

    def stage_resolution():
        analysis = ctx['graph'].analyze(ctx['root'], **({'max_nodes': max_nodes} if max_nodes else {}))
        ctx['analysis'] = analysis
        return {'closure_hash': analysis['closure_hash'], 'closure_size': len(analysis['closure']),
                'critical_size': len(analysis['critical'])}, analysis['diagnostics'], impl

    def stage_extraction():
        plan = plan_extraction(ctx['graph'], ctx['root'], ctx['analysis'])
        ctx['extraction'] = plan
        return plan, plan['diagnostics'], Evidence.BLOCKED_ON_LOCAL_QUERY

    def stage_neutralization():
        row = ctx['row']
        built = build_ir(ctx['graph'], ctx['analysis'], canonical_id=canonical_id, source_realm=realm,
                         build_fingerprint=source.provider.build_fingerprint(), registry_native=row['native'][realm],
                         identity={'basis': row['identity_basis'][realm],
                                   'evidence': row['identity_evidence'][realm]},
                         plort_canonicals=_plort_canonicals(classification, realm, source.provider),
                         material_mapper=source.material_mapper, material_profile=source.material_profile,
                         behaviour_profile=source.behaviour_profile, converter_version=CONVERTER_VERSION,
                         data_evidence=source_evidence(classification, realm, source.provider))
        ctx['neutral'] = built
        diags = built['diagnostics']
        if built['status'] != Status.QUARANTINED:
            # PENDING_ADAPTER material/behaviour findings are statuses, not stage failures.
            return built['ir'], [{**d, 'severity': 'warning'} if d['severity'] == 'error' else d
                                 for d in diags], impl
        if not has_errors(diags):
            diags = diags + [diag('IR_QUARANTINED', 'error', canonical_id, ','.join(built['ir']['status_reasons']))]
        return built['ir'], diags, impl

    def stage_transformation():
        ir = ctx['neutral']['ir']
        essential = {r_m for r in ir['visual']['renderers'] for r_m in r['materials'] if r_m} & \
            set(ctx['analysis']['critical'])
        plans = [plan_target_material(s, target_profile['material_profile']) for s in ctx['neutral']['materials']]
        material_status = 'READY'
        for plan in plans:
            impact = plan['status'] if plan['material'] in essential else \
                ('DEGRADED' if plan['status'] != 'READY' else 'READY')
            material_status = worst(material_status, impact)
        behaviour = plan_behaviour(ir['gameplay']['capabilities'], target_profile['behaviour_profile'])
        ctx.update(material_plans=plans, material_status=material_status, behaviour=behaviour)
        diags = [{**d, 'severity': 'warning'} if d['severity'] == 'error' else d
                 for plan in plans for d in plan['diagnostics']] + \
                [{**d, 'severity': 'warning'} if d['severity'] == 'error' else d for d in behaviour['diagnostics']]
        return {'materials': plans, 'behaviour': behaviour, 'material_status': material_status}, diags, impl

    def stage_assembly():
        ir = ctx['neutral']['ir']
        native_targets = set(target_native_canonicals(classification, target_realm))
        plort_targets = [{'canonical_id': p['canonical_id'], 'node': f"{realm}:{p['native_ref']}",
                          'native_in_target': p['canonical_id'] in native_targets,
                          'market': ir['associated']['market']}
                         for p in ir['associated']['plorts'] if p['canonical_id']]
        artifact_of = {item['node']: item['artifact'] for item in ctx['extraction']['items']}
        child_artifacts = {nid: sorted({artifact_of[e['to']] for e in ctx['graph'].out[nid] if e['to'] in artifact_of})
                           for nid in ctx['analysis']['closure']}
        package = targets.assemble_package(ir=ir, source=realm, target_profile=target_profile,
                                           extraction_plan=ctx['extraction'], material_plans=ctx['material_plans'],
                                           behaviour_plan=ctx['behaviour'], plort_targets=plort_targets,
                                           converter=converter_identity(),
                                           child_artifacts={k: v for k, v in child_artifacts.items() if v},
                                           synthetic=run_is_synthetic(classification, target_profile,
                                                                      source.provider))
        ctx['package'] = package
        diags = [] if ir['associated']['icon'] else [diag('NO_ICON', 'warning', canonical_id,
                                                          'definition has no icon; nothing to register')]
        if source_cache is not None:
            ctx['source_cache'], blocked = _materialize_source_cache(source_cache, canonical_id, diags)
            if blocked:
                ctx['source_cache_blocked'] = True
        return package, diags, impl

    def stage_registration():
        native_targets = set(target_native_canonicals(classification, target_realm))
        plan = targets.plan_registration(ctx['package'], target_profile, native_targets)
        problems = targets.validate_registration_plan(plan, ctx['package'], native_targets)
        ctx['registration'] = plan
        return plan, problems, Evidence(plan['evidence'])

    def stage_verification():
        plan = targets.plan_verification(ctx['package'], ctx['registration'])
        ctx['verification'] = plan
        return plan, [], Evidence.NEEDS_LOCAL_RUNTIME_PROOF

    for name, fn in zip(STAGES, (stage_discovery, stage_validation, stage_resolution, stage_extraction,
                                 stage_neutralization, stage_transformation, stage_assembly, stage_registration,
                                 stage_verification)):
        run.stage(name, fn)
    return _result(canonical_id, realm, target_profile, ctx, run,
                   run_is_synthetic(classification, target_profile, source.provider))


def _result(canonical_id, realm, target_profile, ctx, run, synthetic):
    neutral = ctx.get('neutral')
    reasons = set()
    failed = [s for s in run.stages if s['status'] == 'FAILED']
    for stage in failed:
        reasons.add(f"STAGE_FAILED:{stage['stage']}")
    if ctx.get('source_cache_blocked'):
        reasons.add('SOURCE_CACHE_BLOCKED')
    analysis = ctx.get('analysis')
    visual_stages = {'DISCOVERY', 'DEPENDENCY_RESOLUTION', 'EXTRACTION_PLANNING', 'NEUTRALIZATION'}
    if any(s['stage'] in visual_stages for s in failed):
        visual = 'QUARANTINED'
    elif analysis is None:
        visual = 'NOT_EVALUATED'
    else:
        visual = 'DEGRADED' if any(d['severity'] == 'warning' for d in analysis['diagnostics']) else 'READY'
    material = ctx.get('material_status', 'NOT_EVALUATED')
    behaviour = ctx.get('behaviour')
    behaviour_status = behaviour['status'] if behaviour else 'NOT_EVALUATED'
    registration = ctx.get('registration')
    reg_problems = next((s['diagnostics'] for s in run.stages if s['stage'] == 'REGISTRATION_PLANNING'), [])
    target_registration = 'REFUSED' if has_errors(reg_problems) else \
        (registration['evidence'] if registration else 'NOT_PLANNED')
    if failed:
        final = Status.QUARANTINED
    else:
        final = Status.EXPERIMENTAL
        if material in ('PENDING_ADAPTER', 'QUARANTINED'):
            final = worst_status(final, Status.PENDING_ADAPTER)
            reasons.add('MATERIAL_PENDING_ADAPTER')
        if behaviour and behaviour['blocking']:
            final = worst_status(final, Status.PENDING_ADAPTER)
            reasons.add('BEHAVIOUR_PENDING_ADAPTER')
        if neutral and neutral['status'] == Status.PENDING_ADAPTER:
            final = worst_status(final, Status.PENDING_ADAPTER)
        if behaviour and behaviour['status'] == 'PENDING_ADAPTER' and not behaviour['blocking']:
            reasons.add('BEHAVIOUR_DEGRADED')
        if material == 'DEGRADED':
            reasons.add('MATERIAL_DEGRADED')
        if visual == 'DEGRADED':
            reasons.add('VISUAL_DEGRADED')
        if target_registration == Evidence.BLOCKED_ON_LOCAL_QUERY:
            reasons.add('REGISTRATION_BLOCKED_ON_LOCAL_QUERY')
        reasons.add('NEEDS_LOCAL_RUNTIME_PROOF')
    package = ctx.get('package')
    return {'schema': RESULT_SCHEMA, 'schema_version': 1, 'canonical_id': canonical_id,
            'direction': targets.direction(realm, target_profile['realm']),
            'classification': ctx['row']['classification'] if 'row' in ctx else None,
            'final': str(final), 'reasons': sorted(reasons),
            'components': {'visual': visual, 'material': material, 'behaviour': behaviour_status,
                           'target_registration': str(target_registration)},
            'stages': run.stages, 'synthetic': synthetic, 'converter': converter_identity(),
            'products': _products(ctx, neutral, package, registration),
            'metrics': _metrics(ctx)}


def _products(ctx, neutral, package, registration):
    """Conversion products; the source-complete cache receipt is only present when the job asked
    for it, so the default (without ``source_cache``) output stays byte-identical."""
    products = {'ir': neutral['ir'] if neutral else None, 'extraction_plan': ctx.get('extraction'),
                'package': package, 'registration_plan': registration,
                'verification_plan': ctx.get('verification')}
    if ctx.get('source_cache') is not None:
        products['source_cache'] = ctx['source_cache']
    return products


def _materialize_source_cache(materializer, canonical_id, diags):
    """Optional PACKAGE_ASSEMBLY step: materialize the explicit source-complete cache.

    A refusal (missing explicit selection, failed verification) or an incomplete coverage is an
    error diagnostic: the stage fails and the species ends up BLOCKED. The selection itself is
    never inferred here.
    """
    try:
        receipt = materializer.materialize(canonical_id)
    except Exception as exc:
        detail = f'{type(exc).__name__}: {str(exc)[:200]}'
        diags.append(diag('SOURCE_CACHE_REFUSED', 'error', canonical_id, detail))
        return {'status': 'BLOCKED', 'complete': False, 'canonical_id': canonical_id, 'error': detail}, True
    if not isinstance(receipt, dict) or receipt.get('complete') is not True:
        coverage = receipt.get('coverage') if isinstance(receipt, dict) else None
        detail = '; '.join((coverage or {}).get('problems', [])[:6]) or 'coverage incomplete'
        diags.append(diag('SOURCE_CACHE_INCOMPLETE', 'error', canonical_id, detail))
        blocked = receipt if isinstance(receipt, dict) else {'status': 'BLOCKED', 'complete': False,
                                                            'canonical_id': canonical_id}
        return blocked, True
    return receipt, False


def _metrics(ctx):
    """Inputs for complexity scoring; all derived from actual graph/IR data."""
    analysis, neutral = ctx.get('analysis'), ctx.get('neutral')
    if not analysis or not neutral:
        return None
    ir, graph = neutral['ir'], ctx['graph']
    caps = ir['gameplay']['capabilities']
    return {'closure_size': len(analysis['closure']), 'critical_size': len(analysis['critical']),
            'missing_optional': sum(d['code'] == 'MISSING_OPTIONAL_DEPENDENCY' for d in analysis['diagnostics']),
            'missing_critical': sum(d['code'] == 'MISSING_CRITICAL_DEPENDENCY' for d in analysis['diagnostics']),
            'cycles': sum(d['code'] == 'GRAPH_CYCLE' for d in analysis['diagnostics']),
            'materials': len(ir['visual']['materials']),
            'material_channels': sum(len((m['semantic'] or {}).get('channels', {})) for m in ir['visual']['materials']),
            'unknown_material_properties': sum(len((m['semantic'] or {}).get('unknown_properties', []))
                                               for m in ir['visual']['materials']),
            'renderers': len(ir['visual']['renderers']),
            'skinned_renderers': sum(r['kind'] == 'skinned' for r in ir['visual']['renderers']),
            'bones': len({b for r in ir['visual']['renderers'] for b in r['bones']}),
            'animation_clips': len(ir['visual']['animation_clips']), 'vfx': len(ir['visual']['vfx']),
            'special_capabilities': sum(not c['id'].startswith('standard.') for c in caps),
            'unknown_capabilities': sum(c['policy'] == 'UNKNOWN' for c in caps),
            'has_diet': ir['gameplay']['diet'] is not None, 'plorts': len(ir['associated']['plorts']),
            'unknown_references': len(ir['visual']['unknown_references']),
            'node_kinds': sorted({str(graph.nodes[n]['kind']) for n in analysis['closure']})}
