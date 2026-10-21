"""Typed, cycle-safe asset dependency graph with deterministic Merkle closure hashes.

Nodes are ``<realm>:<native ref>`` identities with a neutral kind, an optional metadata hash
and a resolution state. Edges carry a typed relation, a slot, criticality and provenance.
Nothing is silently dropped: unknown relations become ``references`` edges, references
to undiscovered objects become UNRESOLVED nodes, and conflicting reports become AMBIGUOUS.

All algorithms are iterative and order independent; the frozen graph is identical for any
insertion order. A node's closure hash covers exactly the subgraph reachable from it, so an
unrelated asset change never alters it while any reachable change always does.
"""

from collections import deque
from enum import StrEnum

from .canonical import REALMS, diag, is_sha256, sha256_json, sort_diagnostics

GRAPH_SCHEMA = 'bridge-dependency-graph'
GRAPH_SCHEMA_VERSION = 1


class NodeKind(StrEnum):
    SLIME_DEFINITION = 'slime_definition'
    PLORT_DEFINITION = 'plort_definition'
    PREFAB = 'prefab'
    TRANSFORM = 'transform'
    COMPONENT = 'component'
    RENDERER = 'renderer'
    SKINNED_RENDERER = 'skinned_renderer'
    MESH = 'mesh'
    MATERIAL = 'material'
    SHADER = 'shader'
    TEXTURE = 'texture'
    APPEARANCE = 'appearance'
    FACE = 'face'
    FACE_EXPRESSION = 'face_expression'
    ANIMATOR = 'animator'
    ANIMATION_CLIP = 'animation_clip'
    VFX = 'vfx'
    AUDIO = 'audio'
    ICON = 'icon'
    LOCALIZATION = 'localization'
    PEDIA = 'pedia'
    BEHAVIOUR = 'behaviour'
    SPAWN = 'spawn'
    MARKET = 'market'
    FOOD = 'food'
    UNKNOWN = 'unknown'


class Resolution(StrEnum):
    RESOLVED = 'RESOLVED'
    UNRESOLVED = 'UNRESOLVED'
    AMBIGUOUS = 'AMBIGUOUS'
    PROVIDER_ERROR = 'PROVIDER_ERROR'


K = NodeKind
_ANY = frozenset(NodeKind)
_HOSTS = frozenset({K.PREFAB, K.TRANSFORM})
_COMPONENTS = frozenset({K.COMPONENT, K.RENDERER, K.SKINNED_RENDERER, K.ANIMATOR, K.VFX, K.AUDIO, K.BEHAVIOUR})
# relation: (allowed sources, allowed targets, default criticality or None = by target kind)
EDGE_RULES = {
    'has_prefab': ({K.SLIME_DEFINITION, K.PLORT_DEFINITION}, {K.PREFAB}, True),
    'has_appearance': ({K.SLIME_DEFINITION}, {K.APPEARANCE}, True),
    'has_child': (_HOSTS, {K.TRANSFORM}, True),
    'has_component': (_HOSTS, _COMPONENTS, None),
    'uses_mesh': ({K.RENDERER, K.SKINNED_RENDERER, K.APPEARANCE}, {K.MESH}, True),
    'uses_bone': ({K.SKINNED_RENDERER}, {K.TRANSFORM}, True),
    'uses_material': ({K.RENDERER, K.SKINNED_RENDERER, K.APPEARANCE, K.FACE, K.FACE_EXPRESSION, K.VFX},
                      {K.MATERIAL}, True),
    'uses_shader': ({K.MATERIAL}, {K.SHADER}, True),
    'uses_texture': ({K.MATERIAL, K.ICON, K.FACE_EXPRESSION}, {K.TEXTURE}, False),
    'has_face': ({K.SLIME_DEFINITION, K.APPEARANCE, K.PREFAB}, {K.FACE}, False),
    'has_expression': ({K.FACE}, {K.FACE_EXPRESSION}, False),
    'uses_animator': ({K.SLIME_DEFINITION, K.APPEARANCE, *_HOSTS, K.COMPONENT}, {K.ANIMATOR}, False),
    'has_clip': ({K.ANIMATOR}, {K.ANIMATION_CLIP}, False),
    'produces_plort': ({K.SLIME_DEFINITION}, {K.PLORT_DEFINITION}, True),
    'has_icon': ({K.SLIME_DEFINITION, K.PLORT_DEFINITION}, {K.ICON}, True),
    'has_localization': ({K.SLIME_DEFINITION, K.PLORT_DEFINITION, K.PEDIA}, {K.LOCALIZATION}, False),
    'has_pedia': ({K.SLIME_DEFINITION, K.PLORT_DEFINITION}, {K.PEDIA}, False),
    'has_behaviour': ({K.SLIME_DEFINITION, *_HOSTS}, {K.BEHAVIOUR}, False),
    'has_vfx': ({K.SLIME_DEFINITION, K.APPEARANCE, K.BEHAVIOUR, *_HOSTS}, {K.VFX}, False),
    'has_audio': ({K.SLIME_DEFINITION, K.BEHAVIOUR, *_HOSTS}, {K.AUDIO}, False),
    'has_spawn': ({K.SLIME_DEFINITION}, {K.SPAWN}, False),
    'has_market': ({K.PLORT_DEFINITION}, {K.MARKET}, False),
    'eats': ({K.SLIME_DEFINITION}, {K.FOOD}, False),
    'favourite': ({K.SLIME_DEFINITION}, {K.FOOD}, False),
    'has_base': ({K.SLIME_DEFINITION}, {K.SLIME_DEFINITION}, True),
    'references': (_ANY, _ANY, False),
}
_CRITICAL_COMPONENTS = frozenset({K.RENDERER, K.SKINNED_RENDERER})


def node_id(realm, ref):
    return f'{realm}:{ref}'


def _default_target_kind(relation):
    targets = EDGE_RULES[relation][1]
    return next(iter(targets)) if len(targets) == 1 else K.UNKNOWN


class GraphBuilder:
    """Accumulates provider observations in any order; ``freeze`` produces the canonical graph."""

    def __init__(self):
        self._nodes = {}
        self._edges = {}
        self._diags = []

    def add_node(self, realm, ref, kind, content_hash, *, provenance, attributes=None,
                 resolution=Resolution.RESOLVED):
        if realm not in REALMS or not isinstance(ref, str) or not ref:
            raise ValueError('Node needs a realm and a non-empty native reference')
        kind, resolution = NodeKind(kind), Resolution(resolution)
        if content_hash is not None and not is_sha256(content_hash):
            raise ValueError(f'Node {ref} hash must be SHA-256 or None')
        nid = node_id(realm, ref)
        entry = self._entry(realm, ref)
        entry['observations'].add((kind, content_hash, resolution))
        entry['provenance'].add(provenance)
        if attributes:
            entry['attribute_values'][sha256_json(attributes)] = attributes
        return nid

    def _entry(self, realm, ref):
        return self._nodes.setdefault(node_id(realm, ref), {
            'id': node_id(realm, ref), 'realm': realm, 'ref': ref, 'observations': set(),
            'provenance': set(), 'attribute_values': {}, 'candidates': set()})

    def add_edge(self, realm, from_ref, relation, to_ref, *, provenance, slot='', critical=None,
                 candidates=None, target_kind=None):
        if not isinstance(to_ref, str) or not to_ref or not isinstance(slot, str):
            raise ValueError('Edge needs a target reference and string slot')
        native_relation = relation
        if relation not in EDGE_RULES:
            self._diags.append(diag('UNKNOWN_EDGE_KIND', 'warning', node_id(realm, from_ref),
                                    f'{relation} preserved as references'))
            relation = 'references'
        default = EDGE_RULES[relation][2]
        if critical is not None and type(critical) is not bool:
            raise ValueError('critical must be a boolean or None')
        expected = NodeKind(target_kind) if target_kind else _default_target_kind(relation)
        key = (node_id(realm, from_ref), relation, node_id(realm, to_ref), slot)
        edge = self._edges.setdefault(key, {'requested': set(), 'provenance': set(),
                                            'native_relations': set(), 'expected': set(),
                                            'default': default})
        edge['requested'].add(critical)
        edge['provenance'].add(provenance)
        edge['native_relations'].add(native_relation)
        edge['expected'].add(expected)
        if candidates and len(set(candidates)) > 1:
            self._entry(realm, to_ref)['candidates'].update(candidates)
        return key

    def freeze(self):
        diags = list(self._diags)
        nodes = {}
        expected_kinds = {}
        for (_, _, dst, _), data in self._edges.items():
            expected_kinds.setdefault(dst, set()).update(data['expected'] - {K.UNKNOWN})
        for nid, entry in self._nodes.items():
            observations = sorted(entry['observations'], key=lambda o: (o[0], o[1] or '', o[2]))
            kinds = {o[0] for o in observations}
            hashes = {o[1] for o in observations}
            states = {o[2] for o in observations}
            node = {'id': nid, 'realm': entry['realm'], 'ref': entry['ref'],
                    'provenance': sorted(entry['provenance']), 'candidates': sorted(entry['candidates']),
                    'conflicting_hashes': [], 'attributes': None}
            values = entry['attribute_values']
            if len(values) == 1:
                node['attributes'] = next(iter(values.values()))
            elif len(values) > 1:
                diags.append(diag('CONFLICTING_ATTRIBUTES', 'warning', nid, 'multiple attribute reports'))
            if not observations:
                expected = sorted(expected_kinds.get(nid, ())) or [K.UNKNOWN]
                node.update(kind=NodeKind(expected[0]), hash=None, resolution=Resolution.UNRESOLVED)
            elif len(kinds) > 1 or len(hashes) > 1 or len(states) > 1:
                diags.append(diag('CONFLICTING_NODE', 'error', nid, 'inconsistent kind/hash/resolution reports'))
                node.update(kind=observations[0][0] if len(kinds) == 1 else K.UNKNOWN, hash=None,
                            resolution=Resolution.AMBIGUOUS,
                            conflicting_hashes=sorted(x for x in hashes if x))
            else:
                kind, content_hash, state = observations[0]
                if kind == K.UNKNOWN and state != Resolution.RESOLVED and expected_kinds.get(nid):
                    kind = NodeKind(sorted(expected_kinds[nid])[0])  # failed read: keep the referrer's type
                node.update(kind=kind, hash=content_hash if state == Resolution.RESOLVED else None,
                            resolution=state)
            if node['candidates'] and node['resolution'] != Resolution.PROVIDER_ERROR:
                node['resolution'] = Resolution.AMBIGUOUS
                node['hash'] = None
                diags.append(diag('AMBIGUOUS_REFERENCE', 'warning', nid, ','.join(node['candidates'])))
            nodes[nid] = node
        edges = []
        for (src, relation, dst, slot), data in self._edges.items():
            if dst not in nodes:
                expected = sorted(data['expected'] - {K.UNKNOWN}) or [K.UNKNOWN]
                nodes[dst] = {'id': dst, 'realm': dst.split(':', 1)[0], 'ref': dst.split(':', 1)[1],
                              'kind': NodeKind(expected[0]), 'hash': None, 'resolution': Resolution.UNRESOLVED,
                              'provenance': [], 'candidates': [], 'conflicting_hashes': [], 'attributes': None}
            if src not in nodes:
                nodes[src] = {'id': src, 'realm': src.split(':', 1)[0], 'ref': src.split(':', 1)[1],
                              'kind': K.UNKNOWN, 'hash': None, 'resolution': Resolution.UNRESOLVED,
                              'provenance': [], 'candidates': [], 'conflicting_hashes': [], 'attributes': None}
            if len(data['native_relations']) > 1 or len(data['requested'] - {None}) > 1:
                diags.append(diag('DUPLICATE_EDGE', 'info', src, f'{relation}->{dst} merged'))
            edges.append({'from': src, 'kind': relation, 'to': dst, 'slot': slot, 'data': data})
        for edge in edges:
            data = edge.pop('data')
            default = data['default']
            if default is None:
                default = nodes[edge['to']]['kind'] in _CRITICAL_COMPONENTS
            requested = data['requested'] - {None}
            critical = default or True in requested
            if default and False in requested:
                diags.append(diag('CRITICALITY_DEMOTION_REFUSED', 'warning', edge['from'],
                                  f"{edge['kind']}->{edge['to']} stays critical"))
            sources, targets, _ = EDGE_RULES[edge['kind']]
            src_kind, dst_kind = nodes[edge['from']]['kind'], nodes[edge['to']]['kind']
            valid = edge['kind'] == 'references' or (
                (src_kind in sources or src_kind == K.UNKNOWN) and (dst_kind in targets or dst_kind == K.UNKNOWN))
            if not valid:
                diags.append(diag('EDGE_TYPE_MISMATCH', 'error', edge['from'],
                                  f"{src_kind} -{edge['kind']}-> {dst_kind} ({edge['to']})"))
            native = sorted(data['native_relations'])
            edge.update(critical=critical, valid=valid, provenance=sorted(data['provenance']),
                        native_relation=native[0] if len(native) == 1 else ','.join(native))
        return DependencyGraph(nodes, edges, diags)


def _tarjan(order, adjacency):
    """Iterative Tarjan SCC; components come out children-first (reverse topological)."""
    index, low, on_stack, stack, out, counter = {}, {}, set(), [], [], 0
    for start in order:
        if start in index:
            continue
        index[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        work = [(start, 0)]
        while work:
            v, i = work[-1]
            children = adjacency[v]
            if i < len(children):
                work[-1] = (v, i + 1)
                w = children[i]
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                out.append(sorted(component))
    return out


class DependencyGraph:
    """Immutable analysed graph. Use ``analyze(root)`` for per-species dependency verdicts."""

    def __init__(self, nodes, edges, diagnostics):
        self.nodes = dict(sorted(nodes.items()))
        self.edges = sorted(edges, key=lambda e: (e['from'], e['kind'], e['to'], e['slot']))
        self.diagnostics = sort_diagnostics(diagnostics)
        self.out = {nid: [] for nid in self.nodes}
        for edge in self.edges:
            self.out[edge['from']].append(edge)
        adjacency = {nid: sorted({e['to'] for e in out}) for nid, out in self.out.items()}
        self._components = _tarjan(list(self.nodes), adjacency)
        self._component_of = {nid: i for i, comp in enumerate(self._components) for nid in comp}
        self._scc_hash = {}
        for i, comp in enumerate(self._components):  # children-first, so child hashes exist
            members = set(comp)
            self._scc_hash[i] = sha256_json({
                'members': [self._node_signature(n) for n in comp],
                'edges': sorted([[e['from'], e['kind'], e['slot'], e['critical'], e['valid'],
                                  e['to'] if e['to'] in members else self._scc_hash[self._component_of[e['to']]]]
                                 for n in comp for e in self.out[n]])})

    def _node_signature(self, nid):
        n = self.nodes[nid]
        return [n['id'], n['kind'], n['hash'], n['resolution'], n['candidates'], n['conflicting_hashes'],
                sha256_json(n['attributes']) if n['attributes'] is not None else None]

    def cycles(self):
        return sorted(comp for comp in self._components
                      if len(comp) > 1 or any(e['to'] == comp[0] for e in self.out[comp[0]]))

    def closure_hash(self, root):
        return sha256_json({'root': root, 'scc': self._scc_hash[self._component_of[root]]})

    def analyze(self, root, max_nodes=None):
        """Closure, critical subset and dependency diagnostics for one root (iterative BFS)."""
        if root not in self.nodes:
            raise KeyError(root)
        reach, critical, truncated = {root: 0}, {root}, False
        queue = deque([root])
        while queue:
            current = queue.popleft()
            for edge in self.out[current]:
                child = edge['to']
                if child not in reach:
                    if max_nodes is not None and len(reach) >= max_nodes:
                        truncated = True
                        continue
                    reach[child] = reach[current] + 1
                    queue.append(child)
        queue = deque([root])
        while queue:
            current = queue.popleft()
            for edge in self.out[current]:
                if edge['critical'] and edge['to'] in reach and edge['to'] not in critical:
                    critical.add(edge['to'])
                    queue.append(edge['to'])
        diags = []
        if truncated:
            diags.append(diag('GRAPH_LIMIT_EXCEEDED', 'error', root, f'closure bounded at {max_nodes} nodes'))
        for nid in sorted(reach):
            node, is_critical = self.nodes[nid], nid in critical
            state = node['resolution']
            if state == Resolution.UNRESOLVED or state == Resolution.PROVIDER_ERROR:
                code = 'MISSING_CRITICAL_DEPENDENCY' if is_critical else 'MISSING_OPTIONAL_DEPENDENCY'
                diags.append(diag(code, 'error' if is_critical else 'warning', nid, f'{node["kind"]} {state}'))
            elif state == Resolution.AMBIGUOUS:
                code = 'AMBIGUOUS_CRITICAL_DEPENDENCY' if is_critical else 'AMBIGUOUS_OPTIONAL_DEPENDENCY'
                diags.append(diag(code, 'error' if is_critical else 'warning', nid, node['kind']))
            if node['kind'] == K.UNKNOWN:
                diags.append(diag('UNKNOWN_REFERENCE', 'warning', nid, 'kind unknown; preserved'))
            for edge in self.out[nid]:
                if not edge['valid']:
                    diags.append(diag('EDGE_TYPE_MISMATCH', 'error', nid, f"{edge['kind']}->{edge['to']}"))
        for comp in self.cycles():
            if comp[0] in reach:
                diags.append(diag('GRAPH_CYCLE', 'warning', root, ' -> '.join(comp)))
        return {'root': root, 'closure': sorted(reach), 'critical': sorted(critical),
                'depth': {nid: reach[nid] for nid in sorted(reach)}, 'truncated': truncated,
                'closure_hash': self.closure_hash(root), 'diagnostics': sort_diagnostics(diags)}

    def dependency_hashes(self, root):
        """Sorted (id, kind, hash, resolution) rows for every node reachable from root."""
        report = self.analyze(root)
        critical = set(report['critical'])
        return [{'id': nid, 'kind': self.nodes[nid]['kind'], 'hash': self.nodes[nid]['hash'],
                 'resolution': self.nodes[nid]['resolution'], 'critical': nid in critical}
                for nid in report['closure']]

    def to_json(self):
        return {'schema': GRAPH_SCHEMA, 'schema_version': GRAPH_SCHEMA_VERSION,
                'nodes': [dict(n, kind=str(n['kind']), resolution=str(n['resolution']))
                          for n in self.nodes.values()],
                'edges': [dict(e) for e in self.edges], 'cycles': self.cycles(),
                'diagnostics': self.diagnostics}

    def graph_hash(self):
        return sha256_json(self.to_json())
