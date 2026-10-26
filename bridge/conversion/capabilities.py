"""Behaviour capability model: native components -> canonical capabilities -> target realization.

Visual conversion and gameplay behaviour are separate. A source behaviour profile (data,
verified locally) maps native component types to canonical capabilities; core capabilities
are derived from registry facts (diet, plort). A target behaviour profile says how each
capability is realized: NATIVE, BRIDGE_ADAPTER (an isolated adapter, the only allowed
species-specific code) or UNSUPPORTED. Unknown native behaviour becomes ``unknown.<type>``
with policy UNKNOWN: never silently dropped and never assumed harmless.

Policy: REQUIRED/UNKNOWN capability without a realization -> PENDING_ADAPTER (blocking);
DEGRADABLE capability without one -> PENDING_ADAPTER for behaviour but the species may still
be EXPERIMENTAL. One species' unsupported behaviour never affects another species.
"""

import re

from bridge.content.canonical import REALMS, diag, sha256_json, sort_diagnostics
from bridge.content.graph import NodeKind

# Canonical vocabulary (Bridge-owned, not native knowledge). Policy is the default degrade rule.
CATALOG = {
    'standard.vacpack': {'policy': 'REQUIRED', 'summary': 'can be vacuumed/stored (CAN_VAC)'},
    'standard.diet': {'policy': 'REQUIRED', 'summary': 'eats food groups/favourites (CAN_EAT)'},
    'standard.plort_production': {'policy': 'REQUIRED', 'summary': 'produces plorts (CAN_PRODUCE_PLORT)'},
    'standard.largo_fusion': {'policy': 'DEGRADABLE', 'summary': 'can become a largo (CAN_LARGO)'},
    'movement.flight': {'policy': 'DEGRADABLE', 'summary': 'flies or glides (FLIGHT)'},
    'movement.teleport': {'policy': 'DEGRADABLE', 'summary': 'teleports (TELEPORT)'},
    'environment.sun_sensitive': {'policy': 'DEGRADABLE', 'summary': 'reacts to daylight (SUN_SENSITIVE)'},
    'environment.water_dependent': {'policy': 'DEGRADABLE', 'summary': 'needs water (WATER_DEPENDENT)'},
    'hazard.explosive': {'policy': 'DEGRADABLE', 'summary': 'explodes (EXPLOSIVE)'},
    'hazard.aura': {'policy': 'DEGRADABLE', 'summary': 'damaging/affecting aura (AURA)'},
    'hazard.tangle': {'policy': 'DEGRADABLE', 'summary': 'grabs/tangles (TANGLE)'},
    'diet.fire_feeding': {'policy': 'REQUIRED', 'summary': 'eats only via a special route (FIRE_FEEDING)'},
    'resource.special_production': {'policy': 'DEGRADABLE',
                                    'summary': 'produces non-plort resources (SPECIAL_RESOURCE_PRODUCTION)'},
}
MODES = ('NATIVE', 'BRIDGE_ADAPTER', 'UNSUPPORTED')
_UNKNOWN = re.compile(r'^unknown\.[a-z0-9_]+$')


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_') or 'unnamed'


def validate_behaviour_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'bridge-behaviour-profile' or \
            profile.get('schema_version') != 1 or profile.get('realm') not in REALMS or \
            type(profile.get('synthetic')) is not bool or not isinstance(profile.get('components'), dict):
        raise ValueError('Expected bridge-behaviour-profile v1')
    for name, row in profile['components'].items():
        if not isinstance(row, dict) or (row.get('capability') is not None and row['capability'] not in CATALOG):
            raise ValueError(f'Invalid component mapping {name!r}')
        if row.get('policy') is not None and row['policy'] not in ('REQUIRED', 'DEGRADABLE'):
            raise ValueError(f'Invalid policy override {name!r}')
    return profile


def validate_target_behaviour_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'bridge-target-behaviour-profile' or \
            profile.get('schema_version') != 1 or profile.get('realm') not in REALMS or \
            type(profile.get('synthetic')) is not bool or not isinstance(profile.get('capabilities'), dict):
        raise ValueError('Expected bridge-target-behaviour-profile v1')
    for name, row in profile['capabilities'].items():
        if name not in CATALOG or not isinstance(row, dict) or row.get('mode') not in MODES:
            raise ValueError(f'Invalid target capability {name!r}')
        if row['mode'] == 'BRIDGE_ADAPTER' and (not isinstance(row.get('adapter'), str) or
                                                type(row.get('implemented')) is not bool):
            raise ValueError(f'Bridge adapter for {name} needs an adapter id and implemented flag')
    return profile


def source_capabilities(graph, analysis, registry_native, profile):
    """Canonical capabilities for one species from its closure + registry facts."""
    validate_behaviour_profile(profile)
    found, diags, behaviours = {}, [], []

    def add(cap, policy, source, evidence):
        row = found.setdefault(cap, {'id': cap, 'policy': policy, 'sources': set(), 'evidence': set()})
        if policy == 'REQUIRED' or (policy == 'UNKNOWN' and row['policy'] == 'DEGRADABLE'):
            row['policy'] = policy
        row['sources'].add(source)
        row['evidence'].add(evidence)

    if registry_native.get('role') == 'base':
        add('standard.vacpack', 'REQUIRED', 'registry', 'base slime registry entry')
    if registry_native.get('produces'):
        add('standard.plort_production', 'REQUIRED', 'registry', 'registry produces plort')
        add('standard.diet', 'REQUIRED', 'registry', 'registry diet produces plort')
    for nid in analysis['closure']:
        node = graph.nodes[nid]
        if node['kind'] != NodeKind.BEHAVIOUR:
            continue
        attributes = node.get('attributes') or {}
        native_type = attributes.get('type') if isinstance(attributes.get('type'), str) else None
        mapping = profile['components'].get(native_type) if native_type else None
        capability = None
        if native_type is None or mapping is None:
            capability = f'unknown.{_slug(native_type or node["ref"])}'
            diags.append(diag('UNKNOWN_BEHAVIOUR', 'warning', nid, f'native type {native_type!r} unmapped'))
            add(capability, 'UNKNOWN', nid, 'unmapped native behaviour')
        elif mapping.get('capability') is None:
            diags.append(diag('BEHAVIOUR_DECLARED_INERT', 'info', nid, native_type))
        else:
            capability = mapping['capability']
            add(capability, mapping.get('policy') or CATALOG[capability]['policy'], nid, f'profile:{native_type}')
        behaviours.append({'id': nid, 'type': native_type or 'unknown', 'hash': node['hash'],
                           'capability': capability})
    caps = [{'id': r['id'], 'policy': r['policy'], 'sources': sorted(r['sources']),
             'evidence': '; '.join(sorted(r['evidence']))} for r in sorted(found.values(), key=lambda r: r['id'])]
    used = sorted({b['type'] for b in behaviours})
    profile_hash = sha256_json({t: profile['components'].get(t) for t in used})
    return {'capabilities': caps, 'native_behaviours': sorted(behaviours, key=lambda b: b['id']),
            'profile_entry_hash': profile_hash, 'diagnostics': sort_diagnostics(diags)}


def plan_behaviour(capabilities, target_profile):
    """Per-capability realization plus overall behaviour status and whether it blocks the species."""
    validate_target_behaviour_profile(target_profile)
    rows, diags, blocking, degraded = [], [], False, False
    for cap in capabilities:
        support = target_profile['capabilities'].get(cap['id'], {'mode': 'UNSUPPORTED'})
        mode = support['mode']
        ready = mode == 'NATIVE' or (mode == 'BRIDGE_ADAPTER' and support['implemented'])
        row = {'id': cap['id'], 'policy': cap['policy'], 'mode': mode, 'adapter': support.get('adapter'),
               'status': 'READY' if ready else 'PENDING_ADAPTER',
               'evidence': support.get('evidence', 'profile')}
        if not ready:
            hard = cap['policy'] in ('REQUIRED', 'UNKNOWN')
            blocking = blocking or hard
            degraded = degraded or not hard
            reason = 'adapter not implemented' if mode == 'BRIDGE_ADAPTER' else 'no target realization'
            diags.append(diag('CAPABILITY_PENDING_ADAPTER', 'error' if hard else 'warning', cap['id'],
                              f"{cap['policy']}: {reason}"))
        rows.append(row)
    status = 'PENDING_ADAPTER' if (blocking or degraded) else 'READY'
    used = sorted(c['id'] for c in capabilities)
    return {'capabilities': rows, 'status': status, 'blocking': blocking,
            'profile_entry_hash': sha256_json({c: target_profile['capabilities'].get(c) for c in used}),
            'diagnostics': sort_diagnostics(diags)}
