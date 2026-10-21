"""Batch classification + conversion planning with failure isolation, caching and reports.

Every base species in both registries gets exactly one report entry. SHARED_NATIVE and
target-native species are skipped (destination-native content is used, never a converted
duplicate). Exclusives of the source realm go through the staged engine; each species is
isolated (an exception or broken graph affects only that entry). Products are looked up in
the content cache first, so unchanged species are not reconverted.
"""

import copy

from bridge.content.canonical import DataEvidence, sha256_json
from bridge.content.discovery import DEFAULT_MAX_NODES, discover
from bridge.registry.reconcile import classify_species
from .engine import cache_inputs, convert_species, converter_identity
from .selection import complexity, select_first
from .targets import direction as direction_name

BATCH_SCHEMA = 'bridge-batch-report'


def _entry(row, conversion, final, **extra):
    return {'canonical_id': row['canonical_id'], 'classification': row['classification'],
            'conversion': conversion, 'final': final, 'components': None, 'reasons': list(row['reasons']),
            'complexity': None, 'cache': None, 'cache_changed': [], 'package_id': None, 'result_hash': None,
            **extra}


def run_batch(*, source, destination_registry, target_profile, cache=None, aliases=None, only=None,
              max_nodes=DEFAULT_MAX_NODES):
    """Plan conversions of every ``source.realm``-exclusive base species into ``target_profile``."""
    src, tgt = source.realm, target_profile['realm']
    name = direction_name(src, tgt)
    if destination_registry.realm != tgt:
        raise ValueError('Destination registry realm must match the target profile')
    snapshots = {src: source.provider.read_registry(), tgt: destination_registry.read_registry()}
    classification = classify_species(snapshots['SR1'], snapshots['SR2'], aliases=aliases)
    wanted = None if only is None else set(only)
    convertible = [r for r in classification['species'] if r['classification'] == f'{src}_ONLY'
                   and (wanted is None or r['canonical_id'] in wanted)]
    roots, root_errors = [], {}
    for row in convertible:
        try:
            roots.append(source.provider.definition_root(row['native'][src]))
        except Exception as exc:
            root_errors[row['canonical_id']] = f'{type(exc).__name__}: {str(exc)[:160]}'
    try:
        discovery = discover(source.provider, roots, max_nodes=max_nodes)
    except Exception as exc:  # never lose the whole report to one walk
        discovery = None
        for row in convertible:
            root_errors.setdefault(row['canonical_id'], f'DISCOVERY_EXCEPTION:{type(exc).__name__}: {str(exc)[:160]}')
    entries, results = [], {}
    stats = {'HIT': 0, 'MISS': 0, 'CORRUPT': 0}
    # Destination-native content is AVAILABLE only when the destination registry is runtime-anchored.
    target_evidence = classification['data_evidence'][tgt]
    native_final = 'AVAILABLE' if target_evidence == DataEvidence.RUNTIME_ANCHORED else 'EXPERIMENTAL'
    native_reasons = [] if native_final == 'AVAILABLE' else [f'DESTINATION_NATIVE_EVIDENCE_{target_evidence}']
    for row in classification['species']:
        cid, cls = row['canonical_id'], row['classification']
        if cls in ('SHARED_NATIVE', f'{tgt}_ONLY'):
            conversion = 'SKIPPED_SHARED_NATIVE' if cls == 'SHARED_NATIVE' else 'SKIPPED_TARGET_NATIVE'
            entries.append(_entry(row, conversion, native_final, availability='DESTINATION_NATIVE',
                                  evidence=target_evidence, reasons=row['reasons'] + native_reasons))
        elif cls in ('BLOCKED_UNKNOWN', 'QUARANTINED'):
            entries.append(_entry(row, 'NOT_CONVERTIBLE', cls))
        elif wanted is not None and cid not in wanted:
            entries.append(_entry(row, 'NOT_REQUESTED', None))
        elif cid in root_errors:
            reason = root_errors[cid] if root_errors[cid].startswith('DISCOVERY_EXCEPTION') else \
                'ROOT_LOOKUP_FAILED:' + root_errors[cid]
            entries.append(_entry(row, 'FAILED', 'QUARANTINED', reasons=[reason]))
        else:
            entries.append(_convert(row, classification, source, target_profile, discovery, cache, stats, results))
    conflicts = _plort_conflicts(entries, results)
    counts = {}
    for entry in entries:
        key = f"{entry['conversion']}:{entry['final']}"
        counts[key] = counts.get(key, 0) + 1
    report = {'schema': BATCH_SCHEMA, 'schema_version': 1, 'direction': name, 'synthetic': bool(
              classification['synthetic'] or target_profile['synthetic']),
              'weakest_evidence': classification['weakest_evidence'],
              'classification': {k: classification[k] for k in ('builds', 'traversal', 'data_evidence', 'counts',
                                                                 'snapshot_hashes', 'diagnostics')},
              'target_profile_hash': sha256_json(target_profile), 'converter': converter_identity(),
              'discovery': {'inspected': discovery['inspected'], 'truncated': discovery['truncated'],
                            'graph_hash': discovery['graph'].graph_hash()} if discovery else
                           {'inspected': 0, 'truncated': False, 'graph_hash': None, 'failed': True},
              'entries': entries, 'counts': dict(sorted(counts.items())),
              'registration_conflicts': conflicts, 'cache': stats if cache else None,
              'first_candidate': select_first(entries, name)}
    # Cache status is operational; a cached run must hash exactly like a fresh one.
    content = {k: v for k, v in report.items() if k != 'cache'}
    content['entries'] = [{k: v for k, v in e.items() if k not in ('cache', 'cache_changed')} for e in entries]
    report['report_hash'] = sha256_json(content)
    return {'report': report, 'results': results, 'classification': classification,
            'graph': discovery['graph'] if discovery else None}


def _plort_conflicts(entries, results):
    """A plort may be registered by one converted species only.

    Plans are per species (and cached per species), so the batch is where two exclusives that
    produce the same new plort are detected. The owner is the first species in canonical order
    that is EXPERIMENTAL with a registrable plan; every other claimant gets a batch-adjusted copy
    of its result whose plan is not registrable (PLORT_OWNED_BY / PLORT_OWNER_UNRESOLVED).
    """
    claims = {}
    for cid in sorted(results):
        plan = results[cid]['products']['registration_plan']
        for op in (plan or {}).get('operations', []):
            if op['kind'] == 'REGISTER_PLORT':
                claims.setdefault(op['payload']['plort'], []).append(cid)
    conflicts = []
    by_id = {e['canonical_id']: e for e in entries}
    for plort, species in sorted(claims.items()):
        if len(species) < 2:
            continue
        eligible = [cid for cid in species if results[cid]['final'] == 'EXPERIMENTAL'
                    and results[cid]['products']['registration_plan']['registrable']]
        owner = eligible[0] if eligible else None
        conflicts.append({'plort': plort, 'owner': owner, 'species': species})
        for cid in species:
            if cid == owner:
                continue
            result = copy.deepcopy(results[cid])
            plan = result['products']['registration_plan']
            blocker = f'PLORT_OWNED_BY:{owner}:{plort}' if owner else f'PLORT_OWNER_UNRESOLVED:{plort}'
            plan['blockers'] = sorted(set(plan['blockers']) | {blocker})
            plan['registrable'] = False
            plan['plan_hash'] = sha256_json({k: v for k, v in plan.items() if k != 'plan_hash'})
            if result['products']['verification_plan']:
                result['products']['verification_plan']['registration_plan_hash'] = plan['plan_hash']
            result['reasons'] = sorted(set(result['reasons']) | {f'PLORT_REGISTRATION_CONFLICT:{plort}'})
            if result['final'] == 'EXPERIMENTAL':
                result['final'] = 'PENDING_ADAPTER'
            results[cid] = result
            by_id[cid].update(final=result['final'], reasons=result['reasons'], result_hash=sha256_json(result))
    return conflicts


def _convert(row, classification, source, target_profile, discovery, cache, stats, results):
    cid = row['canonical_id']
    try:
        lookup = None
        if cache is not None:
            inputs = cache_inputs(cid, classification, source, target_profile, discovery['graph'])
            lookup = cache.get(inputs)
            stats[lookup['status']] += 1
        if lookup and lookup['status'] == 'HIT':
            result = lookup['result']
        else:
            result = convert_species(cid, classification=classification, source=source,
                                     target_profile=target_profile, discovery=discovery)
            if cache is not None:
                cache.put(inputs, result)
    except Exception as exc:  # isolate: one species never stops the batch
        return _entry(row, 'FAILED', 'QUARANTINED', reasons=[f'BATCH_EXCEPTION:{type(exc).__name__}: {str(exc)[:160]}'])
    results[cid] = result
    package = result['products']['package']
    return _entry(row, 'CONVERTED', result['final'], components=result['components'], reasons=result['reasons'],
                  complexity=complexity(result['metrics'], result['components']) if result.get('metrics') else None,
                  cache=lookup['status'] if lookup else None, cache_changed=lookup['changed'] if lookup else [],
                  package_id=package['package_id'] if package else None, result_hash=sha256_json(result))


def render_text(report):
    """Human-readable compatibility report (deterministic)."""
    lines = [f"SlimeBridge content compatibility report: {report['direction']}"
             f"{' (SYNTHETIC DATA - not native evidence)' if report['synthetic'] else ''}",
             f"weakest input evidence: {report['weakest_evidence']}",
             f"converter: {report['converter']['converter_version']}",
             'classification: ' + ', '.join(f'{k}={v}' for k, v in report['classification']['counts'].items())]
    if report['cache']:
        lines.append('cache: ' + ', '.join(f'{k}={v}' for k, v in sorted(report['cache'].items())))
    first = report['first_candidate']
    lines.append(f"first candidate: {first['candidate'] or 'none'} ({first['reason']})")
    lines.append('')
    for entry in report['entries']:
        lines.append(entry['canonical_id'])
        lines.append(f"  {entry['classification']}")
        lines.append(f"  conversion={entry['conversion']}")
        for key, value in (entry['components'] or {}).items():
            lines.append(f'  {key}={value}')
        lines.append(f"  final={entry['final']}")
        if entry['complexity']:
            lines.append(f"  complexity={entry['complexity']['score']}")
        if entry['reasons']:
            lines.append('  reasons: ' + ', '.join(entry['reasons']))
        lines.append('')
    return '\n'.join(lines)
