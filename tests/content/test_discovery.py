"""Generic discovery: start from one definition, walk to every dependency through a provider."""
import random
import unittest

from bridge.content.canonical import sha256_text
from bridge.content.discovery import discover
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.providers import (AssetDependencyProvider, NativeInstallProvider, NotAvailableInCloud,
                                         ProviderError, SourceRegistryProvider)


def obj(ref, kind, refs=(), **extra):
    return {'ref': ref, 'kind': kind, 'hash': sha256_text(ref), 'attributes': extra.pop('attributes', None),
            'provenance': 'fixture', 'references': [
                {'relation': rel, 'target': target, 'slot': slot, 'critical': None, 'target_kind': None,
                 'candidates': None, 'provenance': 'fixture'} for rel, target, slot in refs], **extra}


OBJECTS = [
    obj('Def.Foo', 'slime_definition', [('has_prefab', 'Prefab.Foo', ''), ('produces_plort', 'Plort.Foo', ''),
                                        ('has_icon', 'Icon.Foo', '')]),
    obj('Prefab.Foo', 'prefab', [('has_component', 'Renderer.Foo', ''), ('has_child', 'Bone.Foo', '')]),
    obj('Bone.Foo', 'transform'),
    obj('Renderer.Foo', 'skinned_renderer', [('uses_mesh', 'Mesh.Foo', ''), ('uses_material', 'Mat.Foo', '0'),
                                             ('uses_bone', 'Bone.Foo', '0')]),
    obj('Mesh.Foo', 'mesh'), obj('Mat.Foo', 'material', [('uses_shader', 'Shader.X', ''),
                                                         ('uses_texture', 'Tex.Foo', 'albedo')]),
    obj('Shader.X', 'shader'), obj('Tex.Foo', 'texture'),
    obj('Plort.Foo', 'plort_definition', [('has_icon', 'Icon.Plort', '')]), obj('Icon.Plort', 'icon'),
    obj('Icon.Foo', 'icon', [('uses_texture', 'Tex.Icon', '')]), obj('Tex.Icon', 'texture'),
]


def manifest(objects=OBJECTS, **extra):
    return {'schema': 'bridge-content-source', 'schema_version': 1, 'realm': 'SR2', 'synthetic': True,
            'data_evidence': 'SYNTHETIC', 'build': {'fingerprint': 'fixture-build', 'label': None},
            'registry': {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': 'SR2',
                         'build': {'fingerprint': 'fixture-build', 'label': None}, 'data_evidence': 'SYNTHETIC',
                         'traversal': {'completeness': 'COMPLETE', 'registry_identity': 'fixture',
                                       'expected_count': 0, 'observed_count': 0},
                         'entries': [], 'diagnostics': []},
            'objects': objects, 'roots': {}, **extra}


class FlakyProvider:
    realm = 'SR2'

    def __init__(self, inner, fail=(), garbage=(), shuffle_seed=None):
        self.inner, self.fail, self.garbage = inner, set(fail), set(garbage)
        self.rng = random.Random(shuffle_seed) if shuffle_seed is not None else None

    def build_fingerprint(self):
        return self.inner.build_fingerprint()

    def definition_root(self, entry):
        return entry['native_ref']

    def inspect(self, ref):
        if ref in self.fail:
            raise ProviderError(f'cannot read {ref}')
        if ref in self.garbage:
            return {'ref': ref, 'kind': 'mesh', 'hash': 'zzz', 'references': 'nope'}
        found = self.inner.inspect(ref)
        if found and self.rng:
            found = dict(found, references=self.rng.sample(found['references'], len(found['references'])))
        return found


def codes(diags):
    return {d['code'] for d in diags}


class DiscoveryTests(unittest.TestCase):
    def test_walks_from_one_definition_without_filenames(self):
        source = ManifestSource(manifest())
        self.assertIsInstance(source, AssetDependencyProvider)
        self.assertIsInstance(source, SourceRegistryProvider)
        result = discover(source, ['Def.Foo'])
        report = result['graph'].analyze('SR2:Def.Foo')
        self.assertEqual(len(report['closure']), len(OBJECTS))
        self.assertEqual([d for d in report['diagnostics'] if d['severity'] == 'error'], [])
        self.assertEqual(result['inspected'], len(OBJECTS))

    def test_provider_ordering_never_changes_the_graph(self):
        reference = discover(ManifestSource(manifest()), ['Def.Foo'])['graph'].to_json()
        for seed in range(15):
            provider = FlakyProvider(ManifestSource(manifest()), shuffle_seed=seed)
            self.assertEqual(discover(provider, ['Def.Foo'])['graph'].to_json(), reference)

    def test_provider_failure_is_isolated_and_typed(self):
        provider = FlakyProvider(ManifestSource(manifest()), fail={'Mesh.Foo'}, garbage={'Tex.Foo'})
        result = discover(provider, ['Def.Foo'])
        nodes = {n['id']: n for n in result['graph'].to_json()['nodes']}
        self.assertEqual(nodes['SR2:Mesh.Foo']['resolution'], 'PROVIDER_ERROR')
        self.assertEqual(nodes['SR2:Tex.Foo']['resolution'], 'PROVIDER_ERROR')
        self.assertEqual(nodes['SR2:Mesh.Foo']['kind'], 'mesh')
        self.assertIn('PROVIDER_EXCEPTION', codes(result['diagnostics']))
        self.assertIn('MALFORMED_PROVIDER_OUTPUT', codes(result['diagnostics']))
        report = result['graph'].analyze('SR2:Def.Foo')
        self.assertIn(('MISSING_CRITICAL_DEPENDENCY', 'SR2:Mesh.Foo'),
                      {(d['code'], d['subject']) for d in report['diagnostics']})
        self.assertIn(('MISSING_OPTIONAL_DEPENDENCY', 'SR2:Tex.Foo'),
                      {(d['code'], d['subject']) for d in report['diagnostics']})
        self.assertEqual(nodes['SR2:Shader.X']['resolution'], 'RESOLVED')

    def test_missing_objects_become_unresolved_not_dropped(self):
        objects = [o for o in OBJECTS if o['ref'] != 'Shader.X']
        result = discover(ManifestSource(manifest(objects)), ['Def.Foo'])
        nodes = {n['id']: n for n in result['graph'].to_json()['nodes']}
        self.assertEqual((nodes['SR2:Shader.X']['kind'], nodes['SR2:Shader.X']['resolution']),
                         ('shader', 'UNRESOLVED'))

    def test_unknown_object_kind_is_preserved_with_diagnostic(self):
        objects = OBJECTS + [obj('Odd.Thing', 'hologram')]
        objects[0] = obj('Def.Foo', 'slime_definition', [('has_prefab', 'Prefab.Foo', ''),
                                                         ('references', 'Odd.Thing', '')])
        result = discover(ManifestSource(manifest(objects)), ['Def.Foo'])
        nodes = {n['id']: n for n in result['graph'].to_json()['nodes']}
        self.assertEqual(nodes['SR2:Odd.Thing']['kind'], 'unknown')
        self.assertIn('UNKNOWN_NODE_KIND', codes(result['diagnostics']))

    def test_bound_and_reference_to_self_terminate(self):
        objects = list(OBJECTS)
        objects[1] = obj('Prefab.Foo', 'prefab', [('has_component', 'Renderer.Foo', ''),
                                                  ('references', 'Prefab.Foo', ''), ('has_child', 'Bone.Foo', '')])
        result = discover(ManifestSource(manifest(objects)), ['Def.Foo'], max_nodes=4)
        self.assertIn('DISCOVERY_LIMIT_EXCEEDED', codes(result['diagnostics']))
        self.assertLessEqual(result['inspected'], 4)

    def test_manifest_validation(self):
        with self.assertRaises(ValueError):
            ManifestSource(manifest(OBJECTS + [OBJECTS[0]]))
        with self.assertRaises(ValueError):
            ManifestSource({**manifest(), 'schema_version': 2})
        with self.assertRaises(ValueError):
            ManifestSource({**manifest(), 'synthetic': True, 'data_evidence': 'RUNTIME_ANCHORED'})
        with self.assertRaises(ValueError):
            ManifestSource({**manifest(), 'realm': 'SR1'})  # registry realm disagrees

    def test_native_stub_fails_closed_with_local_queries(self):
        provider = NativeInstallProvider('SR2', '/not/a/game')
        with self.assertRaises(NotAvailableInCloud) as caught:
            provider.read_registry()
        self.assertEqual(caught.exception.evidence, 'BLOCKED_ON_LOCAL_QUERY')
        self.assertTrue(caught.exception.local_queries)
        result = discover(provider, ['Def.Foo'])
        self.assertIn('PROVIDER_EXCEPTION', codes(result['diagnostics']))
        nodes = result['graph'].to_json()['nodes']
        self.assertEqual([(n['id'], n['resolution']) for n in nodes], [('SR2:Def.Foo', 'PROVIDER_ERROR')])


if __name__ == '__main__':
    unittest.main()


class DescriptorRobustnessTests(unittest.TestCase):
    def test_unhashable_attributes_fail_only_that_node(self):
        objects = list(OBJECTS)
        objects[4] = obj('Mesh.Foo', 'mesh', attributes={'scale': float('nan')})
        objects[5] = obj('Mat.Foo', 'material', [('uses_shader', 'Shader.X', '')], attributes={1: 'bad key'})
        result = discover(ManifestSource(manifest(objects)), ['Def.Foo'])
        nodes = {n['id']: n for n in result['graph'].to_json()['nodes']}
        self.assertEqual(nodes['SR2:Mesh.Foo']['resolution'], 'PROVIDER_ERROR')
        self.assertEqual(nodes['SR2:Mat.Foo']['resolution'], 'PROVIDER_ERROR')
        self.assertEqual(nodes['SR2:Prefab.Foo']['resolution'], 'RESOLVED')
        self.assertIn('MALFORMED_PROVIDER_OUTPUT', codes(result['diagnostics']))
