"""Adversarial cache tests: scoped invalidation, poisoning, corruption and link refusal."""
import copy
import json
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bridge.cache.store import ContentCache, product_key
from bridge.content.canonical import sha256_text
from bridge.content.discovery import discover
from bridge.conversion import engine
from bridge.conversion.engine import SourceBundle, cache_inputs, convert_species
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.registry.reconcile import classify_species
from conversion import world
from portability import directory_link

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def scratch():
    root = ROOT / 'Lab' / 'GeneratedContent' / f'cache-test-{uuid.uuid4().hex}'
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def keys_for(universe, target='SR1', source='SR2', material_profile=None, behaviour_profile=None,
             target_profile=None):
    provider = ManifestSource(universe[source])
    bundle = SourceBundle(provider, AttributeMaterialMapper(source),
                          material_profile or world.source_material_profile(source),
                          behaviour_profile or world.source_behaviour_profile(source))
    classification = classify_species(ManifestSource(universe['SR1']).read_registry(),
                                      ManifestSource(universe['SR2']).read_registry())
    rows = [r for r in classification['species'] if r['classification'] == f'{source}_ONLY']
    graph = discover(provider, [r['native'][source]['native_ref'] for r in rows])['graph']
    profile = target_profile or world.target_profile(target)
    return {r['canonical_id']: product_key(cache_inputs(r['canonical_id'], classification, bundle, profile, graph))
            for r in rows}


def changed(before, after):
    return sorted(cid for cid in before if before[cid] != after.get(cid))


BASE = world.universe()
BASE_KEYS = keys_for(BASE)


class CacheKeyTests(unittest.TestCase):
    def test_keys_are_stable(self):
        self.assertEqual(keys_for(world.universe()), BASE_KEYS)

    def test_one_texture_invalidates_only_its_species(self):
        mutated = world.mutate(BASE, 'SR2', 'Fixture/Prism/Body1', hash=sha256_text('edited texture'))
        self.assertEqual(changed(BASE_KEYS, keys_for(mutated)), ['slime.prism'])

    def test_shared_dependency_invalidates_all_consumers_only(self):
        mutated = world.mutate(BASE, 'SR2', 'Fixture/Shader/Fixture/SlimeBody', hash=sha256_text('new shader'))
        users = sorted(set(BASE_KEYS) - {'slime.murk'})   # murk is the only species on another shader
        self.assertEqual(changed(BASE_KEYS, keys_for(mutated)), users)

    def test_unrelated_and_other_realm_changes_do_not_invalidate(self):
        mutated = world.mutate(BASE, 'SR2', 'Fixture/Shader/Fixture/Mystery', hash=sha256_text('x'))
        self.assertEqual(changed(BASE_KEYS, keys_for(mutated)), ['slime.murk'])
        sr1_edit = world.mutate(BASE, 'SR1', 'Fixture/Amber/Body0', hash=sha256_text('sr1 only'))
        self.assertEqual(changed(BASE_KEYS, keys_for(sr1_edit)), [])

    def test_build_converter_and_target_changes_invalidate_everything(self):
        rebuilt = copy.deepcopy(BASE)
        rebuilt['SR2']['build']['fingerprint'] = rebuilt['SR2']['registry']['build']['fingerprint'] = 'b2'
        self.assertEqual(changed(BASE_KEYS, keys_for(rebuilt)), sorted(BASE_KEYS))
        with patch.dict(engine.COMPONENT_VERSIONS, {'materials': 99}):
            self.assertEqual(changed(BASE_KEYS, keys_for(BASE)), sorted(BASE_KEYS))
        target = world.target_profile('SR1')
        target['build_fingerprint'] = 'another target build'
        self.assertEqual(changed(BASE_KEYS, keys_for(BASE, target_profile=target)), sorted(BASE_KEYS))
        destination = copy.deepcopy(BASE)
        destination['SR1']['build']['fingerprint'] = destination['SR1']['registry']['build']['fingerprint'] = 'a2'
        self.assertEqual(changed(BASE_KEYS, keys_for(destination)), sorted(BASE_KEYS))

    def test_profile_changes_are_scoped_to_users_of_the_entry(self):
        profile = world.source_material_profile('SR2')
        profile['shaders']['Fixture/Vfx']['properties']['_MainTex']['required'] = True
        self.assertEqual(changed(BASE_KEYS, keys_for(BASE, material_profile=profile)), ['slime.ember'])
        behaviour = world.source_behaviour_profile('SR2')
        behaviour['components']['Fixture.Wings']['capability'] = 'movement.teleport'
        self.assertEqual(changed(BASE_KEYS, keys_for(BASE, behaviour_profile=behaviour)), ['slime.gale'])
        behaviour = world.source_behaviour_profile('SR2')
        behaviour['components']['Fixture.Blink'] = {'capability': 'movement.teleport'}
        self.assertEqual(changed(BASE_KEYS, keys_for(BASE, behaviour_profile=behaviour)), ['slime.flicker'])

    def test_synthetic_and_real_never_share_keys(self):
        inputs = {'canonical_id': 'slime.a', 'synthetic': True}
        self.assertNotEqual(product_key(inputs), product_key({**inputs, 'synthetic': False}))


class CacheStoreTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {'canonical_id': 'slime.azure', 'source_realm': 'SR2', 'target_realm': 'SR1',
                       'synthetic': True, 'closure_hash': sha256_text('closure')}
        self.result = {'canonical_id': 'slime.azure', 'synthetic': True, 'final': 'EXPERIMENTAL'}

    def test_hit_after_put_and_explained_miss(self):
        with scratch() as root:
            cache = ContentCache(root)
            first = cache.get(self.inputs)
            self.assertEqual((first['status'], first['changed']), ('MISS', ['NO_PREVIOUS_PRODUCT']))
            cache.put(self.inputs, self.result)
            self.assertEqual(cache.get(self.inputs)['result'], self.result)
            changed_inputs = {**self.inputs, 'closure_hash': sha256_text('other')}
            miss = cache.get(changed_inputs)
            self.assertEqual((miss['status'], miss['changed']), ('MISS', ['closure_hash']))

    def test_poisoned_or_corrupt_entries_are_never_returned(self):
        with scratch() as root:
            cache = ContentCache(root)
            key = cache.put(self.inputs, self.result)
            path = root / 'products' / key[:2] / f'{key}.json'
            entry = json.loads(path.read_text())
            tampered = copy.deepcopy(entry)
            tampered['result']['final'] = 'AVAILABLE'
            path.write_text(json.dumps(tampered))
            self.assertEqual(cache.get(self.inputs)['status'], 'CORRUPT')
            rehashed = copy.deepcopy(tampered)
            from bridge.content.canonical import sha256_json
            rehashed['result_hash'] = sha256_json(rehashed['result'])
            rehashed['inputs']['closure_hash'] = sha256_text('forged')
            path.write_text(json.dumps(rehashed))
            self.assertEqual(cache.get(self.inputs)['status'], 'CORRUPT')
            other = {**self.inputs, 'canonical_id': 'slime.prism'}
            foreign = {**entry, 'key': product_key(other), 'inputs': other}
            path.write_text(json.dumps(foreign))
            self.assertEqual(cache.get(self.inputs)['status'], 'CORRUPT')
            path.write_text('{torn')
            self.assertEqual(cache.get(self.inputs)['status'], 'CORRUPT')

    def test_refuses_mismatched_put_and_links(self):
        with scratch() as root:
            cache = ContentCache(root)
            with self.assertRaises(ValueError):
                cache.put(self.inputs, {**self.result, 'canonical_id': 'slime.pink'})
            with self.assertRaises(ValueError):
                cache.put(self.inputs, {**self.result, 'synthetic': False})
            target = root / 'elsewhere'
            target.mkdir()
            with directory_link(root / 'products', target):
                with self.assertRaises(ValueError):
                    cache.put(self.inputs, self.result)
                self.assertEqual(list(target.iterdir()), [])
            link_root = root / 'linked-root'
            with directory_link(link_root, target):
                for candidate in (link_root, link_root / 'child'):
                    with self.subTest(root=candidate):
                        with self.assertRaises(ValueError):
                            ContentCache(candidate)
                self.assertEqual(list(target.iterdir()), [])

    def test_failed_publish_leaves_no_temp(self):
        with scratch() as root:
            cache = ContentCache(root)
            with patch('bridge.cache.fs.os.replace', side_effect=OSError('injected')):
                with self.assertRaises(OSError):
                    cache.put(self.inputs, self.result)
            leftovers = [p for p in root.rglob('*') if p.is_file()]
            self.assertEqual(leftovers, [])

    def test_real_conversion_round_trip(self):
        with scratch() as root:
            cache = ContentCache(root)
            universe = world.universe()
            provider = ManifestSource(universe['SR2'])
            bundle = SourceBundle(provider, AttributeMaterialMapper('SR2'), world.source_material_profile('SR2'),
                                  world.source_behaviour_profile('SR2'))
            classification = classify_species(ManifestSource(universe['SR1']).read_registry(),
                                              provider.read_registry())
            found = discover(provider, ['SlimeDefinition.Azure'])
            inputs = cache_inputs('slime.azure', classification, bundle, world.target_profile('SR1'), found['graph'])
            result = convert_species('slime.azure', classification=classification, source=bundle,
                                     target_profile=world.target_profile('SR1'), discovery=found)
            cache.put(inputs, result)
            self.assertEqual(cache.get(inputs)['result'], json.loads(json.dumps(result)))
            if hasattr(os, 'link'):
                key = product_key(inputs)
                os.link(root / 'products' / key[:2] / f'{key}.json', root / 'hardlink.json')
                with self.assertRaises(ValueError):
                    cache.put(inputs, result)


if __name__ == '__main__':
    unittest.main()


class OutputConfinementTests(unittest.TestCase):
    def test_dotdot_paths_never_escape_the_root(self):
        from bridge.cache.fs import atomic_write
        with scratch() as root:
            inner = root / 'inner'
            for bad in ('a/../../escape.json', '../escape.json', 'x/../../../escape.json'):
                with self.assertRaises(ValueError):
                    atomic_write(inner, inner / bad, b'{}')
            self.assertFalse((root / 'escape.json').exists())
            atomic_write(inner, inner / 'ok' / 'fine.json', b'{}')
            self.assertTrue((inner / 'ok' / 'fine.json').is_file())


class CorruptEntryTests(unittest.TestCase):
    def test_unhashable_or_hostile_entries_are_corrupt_not_crashes(self):
        inputs = {'canonical_id': 'slime.azure', 'synthetic': True, 'closure_hash': sha256_text('c')}
        with scratch() as root:
            cache = ContentCache(root)
            key = cache.put(inputs, {'canonical_id': 'slime.azure', 'synthetic': True})
            path = root / 'products' / key[:2] / f'{key}.json'
            entry = json.loads(path.read_text())
            entry['result']['x'] = 'NAN_PLACEHOLDER'
            path.write_text(json.dumps(entry).replace('"NAN_PLACEHOLDER"', 'NaN'))
            self.assertEqual(cache.get(inputs)['status'], 'CORRUPT')
            path.write_text('[' * 100000 + ']' * 100000)
            self.assertEqual(cache.get(inputs)['status'], 'CORRUPT')
            cache.put(inputs, {'canonical_id': 'slime.azure', 'synthetic': True})
            self.assertEqual(cache.get(inputs)['status'], 'HIT')
