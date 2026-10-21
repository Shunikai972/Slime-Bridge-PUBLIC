"""Deterministic complexity scoring and first-candidate selection from actual batch data.

No species is hardcoded: every factor comes from the converted species' graph/IR metrics.
Lower is easier. Ranking: final status (EXPERIMENTAL before PENDING_ADAPTER), then score,
then canonical id. QUARANTINED, blocked and non-converted species are never candidates.
"""

WEIGHTS = {
    'closure_size': 1, 'critical_size': 1, 'materials': 4, 'material_channels': 1,
    'unknown_material_properties': 6, 'renderers': 3, 'skinned_renderers': 10, 'bones': 2,
    'animation_clips': 3, 'vfx': 8, 'special_capabilities': 12, 'unknown_capabilities': 40,
    'missing_optional': 5, 'missing_critical': 100, 'cycles': 15, 'unknown_references': 6,
}
NO_DIET_PENALTY = 20
PLORT_PENALTY = 20  # anything other than exactly one plort is non-standard
# A degraded component is a fidelity risk for a first runtime proof, beyond its raw size.
COMPONENT_PENALTIES = {('visual', 'DEGRADED'): 10, ('material', 'DEGRADED'): 15,
                       ('behaviour', 'PENDING_ADAPTER'): 20}
SELECTION_VERSION = 2
_FINAL_RANK = {'EXPERIMENTAL': 0, 'PENDING_ADAPTER': 1}


def complexity(metrics, components=None):
    breakdown = {name: weight * int(metrics.get(name, 0)) for name, weight in WEIGHTS.items()}
    breakdown['no_standard_diet'] = 0 if metrics.get('has_diet') else NO_DIET_PENALTY
    breakdown['non_standard_plort'] = 0 if metrics.get('plorts') == 1 else PLORT_PENALTY
    for (component, status), penalty in COMPONENT_PENALTIES.items():
        breakdown[f'{component}_{status.lower()}'] = penalty if (components or {}).get(component) == status else 0
    return {'score': sum(breakdown.values()), 'breakdown': dict(sorted(breakdown.items())),
            'selection_version': SELECTION_VERSION}


def select_first(entries, direction):
    """Lowest-risk convertible species for ``direction`` (e.g. 'SR2->SR1'), with the full ranking."""
    ranked = sorted((e for e in entries if e['conversion'] == 'CONVERTED' and e['final'] in _FINAL_RANK
                     and e.get('complexity')),
                    key=lambda e: (_FINAL_RANK[e['final']], e['complexity']['score'], e['canonical_id']))
    ranking = [{'canonical_id': e['canonical_id'], 'final': e['final'], 'score': e['complexity']['score']}
               for e in ranked]
    if not ranked:
        return {'direction': direction, 'candidate': None, 'ranking': [],
                'reason': 'no converted species reached EXPERIMENTAL or PENDING_ADAPTER'}
    best = ranked[0]
    reason = 'lowest complexity among EXPERIMENTAL species' if best['final'] == 'EXPERIMENTAL' else \
        'no EXPERIMENTAL species; lowest complexity PENDING_ADAPTER species (adapter work required)'
    return {'direction': direction, 'candidate': best['canonical_id'], 'final': best['final'],
            'score': best['complexity']['score'], 'breakdown': best['complexity']['breakdown'],
            'ranking': ranking, 'reason': reason, 'selection_version': SELECTION_VERSION}
