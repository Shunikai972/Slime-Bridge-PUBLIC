"""Typed dependency graph: determinism, cycles, criticality, ambiguity, Merkle closure hashes."""
import random
import unittest

from bridge.content.canonical import sha256_text
from bridge.content.graph import GraphBuilder, NodeKind


def h(text):
    return sha256_text(text)


NODES = [('slime.a', 'slime_definition'), ('prefab.a', 'prefab'), ('renderer.a', 'renderer'),
         ('mesh.a', 'mesh'), ('mat.a', 'material'), ('shader.a', 'shader'), ('tex.a', 'texture'),
         ('vfx.a', 'vfx'), ('vfxmat.a', 'material'), ('plort.a', 'plort_definition'), ('icon.a', 'icon')]
EDGES = [('slime.a', 'has_prefab', 'prefab.a'), ('prefab.a', 'has_component', 'renderer.a'),
         ('renderer.a', 'uses_mesh', 'mesh.a'), ('renderer.a', 'uses_material', 'mat.a'),
         ('mat.a', 'uses_shader', 'shader.a'), ('mat.a', 'uses_texture', 'tex.a'),
         ('prefab.a', 'has_vfx', 'vfx.a'), ('vfx.a', 'uses_material', 'vfxmat.a'),
         ('slime.a', 'produces_plort', 'plort.a'), ('slime.a', 'has_icon', 'icon.a')]


def build(nodes=NODES, edges=EDGES, seed=None, hashes=None, extra=lambda b: None):
    builder = GraphBuilder()
    node_ops = [('n', ref, kind) for ref, kind in nodes]
    edge_ops = [('e', *edge) for edge in edges]
    ops = node_ops + edge_ops
    if seed is not None:
        random.Random(seed).shuffle(ops)
    for op in ops:
        if op[0] == 'n':
            ref, kind = op[1], op[2]
            builder.add_node('SR2', ref, kind, (hashes or {}).get(ref, h(ref)), provenance='fixture')
        else:
            builder.add_edge('SR2', op[1], op[2], op[3], provenance='fixture')
    extra(builder)
    return builder.freeze()


def codes(diags):
    return {d['code'] for d in diags}


class GraphTests(unittest.TestCase):
    def test_insertion_order_never_changes_graph_or_hashes(self):
        reference = build()
        for seed in range(25):
            other = build(seed=seed)
            self.assertEqual(other.to_json(), reference.to_json())
            self.assertEqual(other.closure_hash('SR2:slime.a'), reference.closure_hash('SR2:slime.a'))

    def test_criticality_follows_all_critical_paths_only(self):
        graph = build()
        report = graph.analyze('SR2:slime.a')
        critical = set(report['critical'])
        self.assertTrue({'SR2:mesh.a', 'SR2:mat.a', 'SR2:shader.a', 'SR2:plort.a'} <= critical)
        self.assertNotIn('SR2:tex.a', critical)            # optional texture slot
        self.assertNotIn('SR2:vfxmat.a', critical)         # critical child of an optional VFX
        self.assertEqual(set(report['closure']), {'SR2:' + ref for ref, _ in NODES})

    def test_missing_critical_vs_optional(self):
        nodes = [n for n in NODES if n[0] not in ('mesh.a', 'tex.a', 'vfxmat.a')]
        report = build(nodes=nodes).analyze('SR2:slime.a')
        by_subject = {(d['code'], d['subject']) for d in report['diagnostics']}
        self.assertIn(('MISSING_CRITICAL_DEPENDENCY', 'SR2:mesh.a'), by_subject)
        self.assertIn(('MISSING_OPTIONAL_DEPENDENCY', 'SR2:tex.a'), by_subject)
        self.assertIn(('MISSING_OPTIONAL_DEPENDENCY', 'SR2:vfxmat.a'), by_subject)
        severities = {d['subject']: d['severity'] for d in report['diagnostics']
                      if d['code'].startswith('MISSING_')}
        self.assertEqual(severities['SR2:mesh.a'], 'error')
        self.assertEqual(severities['SR2:tex.a'], 'warning')
        graph = build(nodes=nodes)
        node = {n['id']: n for n in graph.to_json()['nodes']}['SR2:mesh.a']
        self.assertEqual((node['kind'], node['resolution']), ('mesh', 'UNRESOLVED'))

    def test_provider_cannot_demote_rule_critical_edge(self):
        def demote(builder):
            builder.add_edge('SR2', 'renderer.a', 'uses_mesh', 'mesh.b', critical=False, provenance='p')
        graph = build(extra=demote)
        report = graph.analyze('SR2:slime.a')
        self.assertIn('SR2:mesh.b', report['critical'])
        self.assertIn('CRITICALITY_DEMOTION_REFUSED', codes(graph.diagnostics))
        self.assertIn('MISSING_CRITICAL_DEPENDENCY', codes(report['diagnostics']))

    def test_cycles_are_reported_deterministically_and_traversal_terminates(self):
        edges = EDGES + [('mesh.a', 'references', 'prefab.a'), ('tex.a', 'references', 'tex.a')]
        graphs = [build(edges=edges, seed=s) for s in range(10)]
        cycles = graphs[0].cycles()
        self.assertEqual(cycles, [['SR2:mesh.a', 'SR2:prefab.a', 'SR2:renderer.a'], ['SR2:tex.a']])
        self.assertTrue(all(g.cycles() == cycles for g in graphs))
        self.assertEqual(len({g.closure_hash('SR2:slime.a') for g in graphs}), 1)
        report = graphs[0].analyze('SR2:slime.a')
        self.assertIn('GRAPH_CYCLE', codes(report['diagnostics']))
        self.assertNotEqual(graphs[0].closure_hash('SR2:mesh.a'), graphs[0].closure_hash('SR2:prefab.a'))

    def test_type_violations_and_unknown_relations_are_kept_not_discarded(self):
        def bad(builder):
            builder.add_edge('SR2', 'tex.a', 'uses_material', 'mat.a', provenance='p')
            builder.add_edge('SR2', 'mat.a', 'mystery_link', 'odd.thing', provenance='p')
        graph = build(extra=bad)
        self.assertIn('EDGE_TYPE_MISMATCH', codes(graph.diagnostics))
        self.assertIn('UNKNOWN_EDGE_KIND', codes(graph.diagnostics))
        edges = graph.to_json()['edges']
        self.assertTrue(any(e['to'] == 'SR2:odd.thing' and e['kind'] == 'references'
                            and e['native_relation'] == 'mystery_link' for e in edges))
        self.assertTrue(any(e['from'] == 'SR2:tex.a' and not e['valid'] for e in edges))
        report = graph.analyze('SR2:slime.a')
        self.assertIn('UNKNOWN_REFERENCE', codes(report['diagnostics']))

    def test_conflicting_duplicates_become_ambiguous(self):
        def dup(builder):
            builder.add_node('SR2', 'mesh.a', 'mesh', h('other bytes'), provenance='second scan')
            builder.add_edge('SR2', 'renderer.a', 'uses_mesh', 'mesh.a', provenance='again')
        graph = build(extra=dup)
        node = {n['id']: n for n in graph.to_json()['nodes']}['SR2:mesh.a']
        self.assertEqual(node['resolution'], 'AMBIGUOUS')
        self.assertEqual(node['conflicting_hashes'], sorted([h('mesh.a'), h('other bytes')]))
        self.assertIsNone(node['hash'])
        self.assertIn('CONFLICTING_NODE', codes(graph.diagnostics))
        self.assertEqual(sum(e['to'] == 'SR2:mesh.a' for e in graph.to_json()['edges']), 1)
        self.assertIn('AMBIGUOUS_CRITICAL_DEPENDENCY', codes(graph.analyze('SR2:slime.a')['diagnostics']))

    def test_ambiguous_reference_candidates(self):
        def amb(builder):
            builder.add_edge('SR2', 'mat.a', 'uses_texture', 'tex.shared', provenance='p',
                             candidates=['bundle1/tex.shared', 'bundle2/tex.shared'], critical=True)
        graph = build(extra=amb)
        report = graph.analyze('SR2:slime.a')
        self.assertIn('AMBIGUOUS_CRITICAL_DEPENDENCY', codes(report['diagnostics']))
        node = {n['id']: n for n in graph.to_json()['nodes']}['SR2:tex.shared']
        self.assertEqual((node['kind'], node['resolution']), ('texture', 'AMBIGUOUS'))
        self.assertEqual(node['candidates'], ['bundle1/tex.shared', 'bundle2/tex.shared'])

    def test_merkle_hash_scope(self):
        base = build()
        root = 'SR2:slime.a'
        unrelated = build(extra=lambda b: b.add_node('SR2', 'zzz.other', 'texture', h('x'), provenance='p'))
        self.assertEqual(unrelated.closure_hash(root), base.closure_hash(root))
        changed_tex = build(hashes={'tex.a': h('edited texture')})
        self.assertNotEqual(changed_tex.closure_hash(root), base.closure_hash(root))
        self.assertEqual(changed_tex.closure_hash('SR2:plort.a'), base.closure_hash('SR2:plort.a'))
        changed_vfx = build(hashes={'vfxmat.a': h('edited vfx material')})
        self.assertNotEqual(changed_vfx.closure_hash(root), base.closure_hash(root))
        slot = build(extra=lambda b: b.add_edge('SR2', 'mat.a', 'uses_texture', 'tex.a', slot='mask',
                                                provenance='p'))
        self.assertNotEqual(slot.closure_hash(root), base.closure_hash(root))
        resolved_later = build(nodes=[n for n in NODES if n[0] != 'tex.a'])
        self.assertNotEqual(resolved_later.closure_hash(root), base.closure_hash(root))

    def test_shared_dependency_invalidates_every_consumer(self):
        nodes = NODES + [('slime.b', 'slime_definition'), ('prefab.b', 'prefab'), ('renderer.b', 'renderer')]
        edges = EDGES + [('slime.b', 'has_prefab', 'prefab.b'), ('prefab.b', 'has_component', 'renderer.b'),
                         ('renderer.b', 'uses_mesh', 'mesh.a'), ('renderer.b', 'uses_material', 'mat.a')]
        before = build(nodes=nodes, edges=edges)
        after = build(nodes=nodes, edges=edges, hashes={'tex.a': h('new')})
        for root in ('SR2:slime.a', 'SR2:slime.b'):
            self.assertNotEqual(before.closure_hash(root), after.closure_hash(root))

    def test_traversal_bound_is_reported(self):
        report = build().analyze('SR2:slime.a', max_nodes=3)
        self.assertIn('GRAPH_LIMIT_EXCEEDED', codes(report['diagnostics']))
        self.assertLessEqual(len(report['closure']), 3)

    def test_deep_chains_do_not_recurse(self):
        builder = GraphBuilder()
        builder.add_node('SR1', 't0', 'prefab', h('t0'), provenance='p')
        for i in range(1, 30000):
            builder.add_node('SR1', f't{i}', 'transform', h(f't{i}'), provenance='p')
            builder.add_edge('SR1', f't{i - 1}', 'has_child', f't{i}', provenance='p')
        graph = builder.freeze()
        self.assertEqual(len(graph.analyze('SR1:t0')['closure']), 30000)
        self.assertEqual(len(graph.closure_hash('SR1:t0')), 64)

    def test_unknown_node_kind_is_refused_at_build_time(self):
        with self.assertRaises(ValueError):
            GraphBuilder().add_node('SR1', 'x', 'hologram', h('x'), provenance='p')
        self.assertEqual(NodeKind('mesh'), 'mesh')


if __name__ == '__main__':
    unittest.main()
