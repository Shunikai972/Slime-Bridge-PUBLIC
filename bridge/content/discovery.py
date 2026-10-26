"""Generic content discovery: start at definition roots and walk provider references.

Nobody supplies mesh/texture/material/plort/icon filenames: every dependency is reached
through typed references the provider reports for objects it has already returned. The walk
is breadth-first over sorted references (deterministic even under a truncation bound),
bounded, cycle-safe, and isolates provider failures to the node that failed.
"""

from collections import deque

from .canonical import canonical_json, diag, is_sha256, sort_diagnostics
from .graph import GraphBuilder, NodeKind, Resolution

DEFAULT_MAX_NODES = 20000
_KINDS = {str(k) for k in NodeKind}


def _reference(raw, default_provenance, subject, diags):
    if not isinstance(raw, dict) or not isinstance(raw.get('relation'), str) or not raw['relation'] or \
            not isinstance(raw.get('target'), str) or not raw['target']:
        return None
    slot = raw.get('slot', '')
    critical = raw.get('critical')
    candidates = raw.get('candidates')
    if not isinstance(slot, str) or (critical is not None and type(critical) is not bool) or \
            (candidates is not None and (not isinstance(candidates, list) or
                                         not all(isinstance(c, str) and c for c in candidates))):
        return None
    target_kind = raw.get('target_kind')
    if target_kind is not None and target_kind not in _KINDS:
        diags.append(diag('UNKNOWN_NODE_KIND', 'warning', subject, f'reference target_kind {target_kind!r}'))
        target_kind = None
    provenance = raw.get('provenance') if isinstance(raw.get('provenance'), str) and raw['provenance'] \
        else default_provenance
    return {'relation': raw['relation'], 'target': raw['target'], 'slot': slot, 'critical': critical,
            'candidates': candidates, 'target_kind': target_kind, 'provenance': provenance}


def _descriptor(ref, found, subject, diags):
    """Validate a provider descriptor; return (kind, hash, attributes, provenance, references) or None."""
    if not isinstance(found, dict) or found.get('ref') != ref or not isinstance(found.get('kind'), str):
        return None
    content_hash = found.get('hash')
    if content_hash is not None and not is_sha256(content_hash):
        return None
    attributes = found.get('attributes')
    if attributes is not None:
        try:
            canonical_json(attributes)  # NaN/inf, non-string keys or cycles would break hashing later
        except (TypeError, ValueError, RecursionError):
            return None
        if not isinstance(attributes, dict):
            return None
    provenance = found.get('provenance') if isinstance(found.get('provenance'), str) and found['provenance'] \
        else 'provider'
    raw_refs = found.get('references', [])
    if not isinstance(raw_refs, list):
        return None
    references = []
    for raw in raw_refs:
        parsed = _reference(raw, provenance, subject, diags)
        if parsed is None:
            return None
        references.append(parsed)
    kind = found['kind']
    if kind not in _KINDS:
        diags.append(diag('UNKNOWN_NODE_KIND', 'warning', subject, f'{kind!r} preserved as unknown'))
        kind = NodeKind.UNKNOWN
    return kind, content_hash, attributes, provenance, references


def discover(provider, roots, *, max_nodes=DEFAULT_MAX_NODES, root_kind=NodeKind.SLIME_DEFINITION,
             builder=None):
    """Walk from ``roots`` (native refs). Returns graph, diagnostics, inspected count and truncation."""
    realm = provider.realm
    builder = builder or GraphBuilder()
    diags = []
    ordered = sorted(set(roots))
    queue, seen = deque(ordered), set(ordered)
    inspected, truncated = 0, False
    while queue:
        if inspected >= max_nodes:
            truncated = True
            break
        ref = queue.popleft()
        inspected += 1
        subject = f'{realm}:{ref}'
        is_root = ref in ordered
        try:
            found = provider.inspect(ref)
        except Exception as exc:  # provider bugs are isolated to this node
            diags.append(diag('PROVIDER_EXCEPTION', 'error', subject, f'{type(exc).__name__}: {str(exc)[:200]}'))
            builder.add_node(realm, ref, root_kind if is_root else NodeKind.UNKNOWN, None,
                             provenance=f'provider-error:{type(exc).__name__}', resolution=Resolution.PROVIDER_ERROR)
            continue
        if found is None:
            if is_root:
                builder.add_node(realm, ref, root_kind, None, provenance='not found by provider',
                                 resolution=Resolution.UNRESOLVED)
            continue
        parsed = _descriptor(ref, found, subject, diags)
        if parsed is None:
            diags.append(diag('MALFORMED_PROVIDER_OUTPUT', 'error', subject, 'descriptor failed validation'))
            builder.add_node(realm, ref, root_kind if is_root else NodeKind.UNKNOWN, None,
                             provenance='malformed provider output', resolution=Resolution.PROVIDER_ERROR)
            continue
        kind, content_hash, attributes, provenance, references = parsed
        builder.add_node(realm, ref, kind, content_hash, provenance=provenance, attributes=attributes)
        for item in sorted(references, key=lambda r: (r['relation'], r['target'], r['slot'])):
            builder.add_edge(realm, ref, item['relation'], item['target'], provenance=item['provenance'],
                             slot=item['slot'], critical=item['critical'], candidates=item['candidates'],
                             target_kind=item['target_kind'])
            if item['target'] not in seen:
                seen.add(item['target'])
                queue.append(item['target'])
    if truncated:
        diags.append(diag('DISCOVERY_LIMIT_EXCEEDED', 'error', realm,
                          f'stopped after {max_nodes} inspections; {len(queue)} refs not inspected'))
    graph = builder.freeze()
    return {'graph': graph, 'diagnostics': sort_diagnostics(diags + graph.diagnostics),
            'inspected': inspected, 'truncated': truncated, 'uninspected': sorted(queue)}
