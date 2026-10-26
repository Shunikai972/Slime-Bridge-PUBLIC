"""Build graph: incremental, resumable, partial, isolated; generic executor invariants."""
import unittest
from unittest.mock import patch

from bridge.content.canonical import sha256_text
from bridge.conversion import engine
from bridge.conversion.engine import SourceBundle
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.packages import buildgraph as B
from conversion import world
from conversion.test_cache import scratch


def build(store, universe=None, **kw):
    universe = universe or world.universe()
    bundle = SourceBundle(ManifestSource(universe['SR2']), AttributeMaterialMapper('SR2'),
                          world.source_material_profile('SR2'), world.source_behaviour_profile('SR2'))
    return B.content_build(source=bundle, destination_registry=ManifestSource(universe['SR1']),
                           target_profile=world.target_profile('SR1'), store=store, **kw)


def statuses(report, kind):
    return {n['id'].split(':', 1)[1]: n['status'] for n in report['nodes'] if n['kind'] == kind}


class ContentBuildTests(unittest.TestCase):
    def test_first_build_then_full_reuse(self):
        store = B.MemoryBuildStore()
        first = build(store)
        self.assertEqual(set(statuses(first, 'conversion').values()), {'RUN'})
        self.assertIn('slime.azure', first['packages'])
        self.assertNotIn('slime.pink', first['packages'])          # shared native: never packaged
        second = build(store)
        self.assertEqual(set(statuses(second, 'conversion').values()), {'REUSED'})
        self.assertEqual(set(statuses(second, 'package').values()), {'REUSED'})
        self.assertEqual(first['packages'], second['packages'])
        stage_ids = [n['id'] for n in second['nodes'] if n['kind'] == 'stage' and n['id'].startswith('stage:slime.azure:')]
        self.assertEqual(len(stage_ids), 9)

    def test_one_changed_dependency_rebuilds_only_its_dependents(self):
        store = B.MemoryBuildStore()
        build(store)
        edited = world.mutate(world.universe(), 'SR2', 'Fixture/Prism/Body1', hash=sha256_text('new texture'))
        report = build(store, edited)
        runs = sorted(k for k, v in statuses(report, 'conversion').items() if v == 'RUN')
        self.assertEqual(runs, ['slime.prism'])
        self.assertEqual(sorted(k for k, v in statuses(report, 'package').items() if v == 'RUN'), ['slime.prism'])

    def test_converter_semantics_change_invalidates_outputs(self):
        store = B.MemoryBuildStore()
        build(store)
        with patch.dict(engine.COMPONENT_VERSIONS, {'targets': 99}):
            report = build(store)
        self.assertEqual(set(statuses(report, 'conversion').values()), {'RUN'})

    def test_failure_isolation_blocks_only_dependents(self):
        original = B.convert_species

        def explode(cid, **kw):
            if cid == 'slime.prism':
                raise RuntimeError('converter bug')
            return original(cid, **kw)
        with patch.object(B, 'convert_species', side_effect=explode):
            report = build(B.MemoryBuildStore())
        self.assertEqual(statuses(report, 'conversion')['slime.prism'], 'FAILED')
        self.assertEqual(statuses(report, 'package')['slime.prism'], 'BLOCKED')
        self.assertEqual(statuses(report, 'package')['slime.azure'], 'RUN')
        self.assertNotIn('slime.prism', report['packages'])

    def test_resume_after_crash_reuses_completed_nodes(self):
        class CrashingStore(B.MemoryBuildStore):
            def __init__(self, budget):
                super().__init__()
                self.budget = budget

            def put(self, key, record):
                if self.budget == 0:
                    raise KeyboardInterrupt('simulated crash')
                self.budget -= 1
                super().put(key, record)
        store = CrashingStore(budget=5)
        with self.assertRaises(KeyboardInterrupt):
            build(store)
        persisted = len(store.records)
        store.budget = 10 ** 6
        resumed = build(store)
        reused = sum(1 for n in resumed['nodes'] if n['status'] == 'REUSED')
        self.assertEqual(reused, persisted)
        self.assertEqual(set(statuses(resumed, 'package').values()) - {'RUN', 'REUSED'}, set())

    def test_partial_rebuild_targets_one_species(self):
        report = build(B.MemoryBuildStore(), only=['slime.azure'])
        self.assertEqual(set(statuses(report, 'conversion')), {'slime.azure'})
        self.assertEqual(list(report['packages']), ['slime.azure'])

    def test_directory_store_rejects_corrupt_records(self):
        with scratch() as root:
            store = B.DirectoryBuildStore(root / 'build')
            build(store)
            for path in (root / 'build' / 'nodes').rglob('*.json'):
                path.write_text('{"key": "forged", "output": {}, "output_hash": "0"}')
            report = build(store)
            self.assertEqual(set(statuses(report, 'conversion').values()), {'RUN'})
            codes = {d['code'] for n in report['nodes'] for d in n['diagnostics']}
            self.assertIn('BUILD_RECORD_CORRUPT', codes)


class ExecutorTests(unittest.TestCase):
    def test_order_cycles_and_unknown_deps(self):
        nodes = [B.Node('c', 'k', lambda d: (1, 1), deps=['a', 'b']), B.Node('b', 'k', lambda d: (1, 1), deps=['a']),
                 B.Node('a', 'k', lambda d: (1, 1))]
        self.assertEqual(B.topological(nodes), ['a', 'b', 'c'])
        self.assertEqual(B.topological(list(reversed(nodes))), ['a', 'b', 'c'])
        with self.assertRaises(ValueError):
            B.topological([B.Node('x', 'k', None, deps=['y']), B.Node('y', 'k', None, deps=['x'])])
        with self.assertRaises(ValueError):
            B.topological([B.Node('x', 'k', None, deps=['ghost'])])

    def test_keys_depend_on_version_params_and_inputs(self):
        def run(version='1', params=None, source=1):
            store = B.MemoryBuildStore()
            nodes = [B.Node('src', 'k', lambda d: (source, source), volatile=True),
                     B.Node('out', 'k', lambda d: (d['src'] * 2, d['src'] * 2), deps=['src'], version=version,
                            params=params)]
            return B.execute(nodes, store)['out']['key']
        base = run()
        self.assertEqual(base, run())
        self.assertNotEqual(base, run(version='2'))
        self.assertNotEqual(base, run(params={'p': 1}))
        self.assertNotEqual(base, run(source=2))


if __name__ == '__main__':
    unittest.main()
