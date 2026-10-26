"""Batch planning: full coverage, shared skip, isolation, incremental cache, deterministic selection."""
import copy
import random
import unittest
from unittest.mock import patch

from bridge.cache.store import ContentCache
from bridge.content.canonical import sha256_json, sha256_text
from bridge.conversion import batch as batch_module
from bridge.conversion.batch import render_text, run_batch
from bridge.conversion.engine import SourceBundle
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.conversion.selection import complexity, select_first
from conversion import world
from conversion.test_cache import scratch


def run(universe=None, source='SR2', cache=None, **kw):
    universe = universe or world.universe()
    target = 'SR1' if source == 'SR2' else 'SR2'
    provider = ManifestSource(universe[source])
    bundle = SourceBundle(provider, AttributeMaterialMapper(source), world.source_material_profile(source),
                          world.source_behaviour_profile(source))
    return run_batch(source=bundle, destination_registry=ManifestSource(universe[target]),
                     target_profile=world.target_profile(target), cache=cache, **kw)


def entries(out):
    return {e['canonical_id']: e for e in out['report']['entries']}


class BatchTests(unittest.TestCase):
    def test_every_base_species_has_one_entry_and_shared_is_skipped(self):
        out = run()
        rows = entries(out)
        self.assertEqual(sorted(rows), sorted(r['canonical_id'] for r in out['classification']['species']))
        self.assertEqual(rows['slime.pink']['conversion'], 'SKIPPED_SHARED_NATIVE')
        self.assertEqual(rows['slime.pink']['final'], 'EXPERIMENTAL')   # synthetic destination evidence
        self.assertIn('DESTINATION_NATIVE_EVIDENCE_SYNTHETIC', rows['slime.pink']['reasons'])
        self.assertNotIn('slime.pink', out['results'])
        self.assertEqual(rows['slime.amber']['conversion'], 'SKIPPED_TARGET_NATIVE')
        self.assertEqual((rows['slime.twin']['conversion'], rows['slime.twin']['final']),
                         ('NOT_CONVERTIBLE', 'QUARANTINED'))
        self.assertEqual(rows['slime.hollow']['final'], 'QUARANTINED')
        self.assertEqual(rows['slime.azure']['final'], 'EXPERIMENTAL')
        gale = rows['slime.gale']
        self.assertEqual((gale['classification'], gale['components'], gale['final']),
                         ('SR2_ONLY', {'visual': 'READY', 'material': 'READY', 'behaviour': 'PENDING_ADAPTER',
                                       'target_registration': 'NEEDS_LOCAL_RUNTIME_PROOF'}, 'EXPERIMENTAL'))
        self.assertTrue(out['report']['synthetic'])

    def test_human_report_lists_example_shape(self):
        text = render_text(run()['report'])
        self.assertIn('SYNTHETIC DATA', text)
        block = text.split('slime.gale\n', 1)[1].split('\n\n', 1)[0]
        for line in ('SR2_ONLY', 'visual=READY', 'material=READY', 'behaviour=PENDING_ADAPTER',
                     'target_registration=NEEDS_LOCAL_RUNTIME_PROOF', 'final=EXPERIMENTAL'):
            self.assertIn(line, block)

    def test_one_broken_species_never_stops_the_batch(self):
        original = batch_module.convert_species

        def explode_for_prism(cid, **kw):
            if cid == 'slime.prism':
                raise RuntimeError('injected species failure')
            return original(cid, **kw)
        with patch.object(batch_module, 'convert_species', side_effect=explode_for_prism):
            rows = entries(run())
        self.assertEqual((rows['slime.prism']['conversion'], rows['slime.prism']['final']), ('FAILED', 'QUARANTINED'))
        self.assertTrue(rows['slime.prism']['reasons'][0].startswith('BATCH_EXCEPTION'))
        self.assertEqual(rows['slime.azure']['final'], 'EXPERIMENTAL')

    def test_provider_failure_for_one_species_is_isolated(self):
        universe = world.universe()
        provider = ManifestSource(universe['SR2'])
        original = provider.inspect

        def flaky(ref):
            if ref == 'Fixture/Azure/Mesh':
                raise OSError('injected read failure')
            return original(ref)
        provider.inspect = flaky
        bundle = SourceBundle(provider, AttributeMaterialMapper('SR2'), world.source_material_profile('SR2'),
                              world.source_behaviour_profile('SR2'))
        rows = entries(run_batch(source=bundle, destination_registry=ManifestSource(universe['SR1']),
                                 target_profile=world.target_profile('SR1')))
        self.assertEqual(rows['slime.azure']['final'], 'QUARANTINED')
        self.assertEqual(rows['slime.prism']['final'], 'EXPERIMENTAL')

    def test_incremental_rebuild_only_touches_affected_species(self):
        with scratch() as root:
            cache = ContentCache(root)
            first = run(cache=cache)
            converted = sum(e['conversion'] == 'CONVERTED' for e in first['report']['entries'])
            self.assertEqual(first['report']['cache'], {'HIT': 0, 'MISS': converted, 'CORRUPT': 0})
            second = run(cache=cache)
            self.assertEqual(second['report']['cache'], {'HIT': converted, 'MISS': 0, 'CORRUPT': 0})
            self.assertEqual(second['report']['report_hash'], first['report']['report_hash'])
            edited = world.mutate(world.universe(), 'SR2', 'Fixture/Prism/Body2', hash=sha256_text('retexture'))
            third = run(edited, cache=cache)
            misses = sorted(e['canonical_id'] for e in third['report']['entries'] if e['cache'] == 'MISS')
            self.assertEqual(misses, ['slime.prism'])
            self.assertIn('closure_hash', entries(third)['slime.prism']['cache_changed'])
            rebuilt = world.universe()
            rebuilt['SR2']['build']['fingerprint'] = rebuilt['SR2']['registry']['build']['fingerprint'] = 'b2'
            fourth = run(rebuilt, cache=cache)
            self.assertEqual(fourth['report']['cache']['MISS'], converted)

    def test_selection_is_deterministic_and_data_driven(self):
        out = run()
        first = out['report']['first_candidate']
        self.assertEqual(first['final'], 'EXPERIMENTAL')
        ranking = [r['canonical_id'] for r in first['ranking']]
        self.assertEqual(ranking[0], first['candidate'])
        scores = {r['canonical_id']: r['score'] for r in first['ranking']}
        # Clean species beat degraded ones of similar size; extra materials cost more.
        self.assertEqual(first['candidate'], 'slime.azure')
        self.assertLess(scores['slime.azure'], scores['slime.prism'])
        for degraded in ('slime.opal', 'slime.moss', 'slime.gale'):
            self.assertLess(scores['slime.azure'], scores[degraded])
        pending = [r for r in first['ranking'] if r['final'] == 'PENDING_ADAPTER']
        self.assertTrue(all(ranking.index(p['canonical_id']) > ranking.index(first['candidate']) for p in pending))
        universe = world.universe()
        for seed in range(5):
            shuffled = copy.deepcopy(universe)
            random.Random(seed).shuffle(shuffled['SR2']['registry']['entries'])
            random.Random(seed).shuffle(shuffled['SR2']['objects'])
            self.assertEqual(run(shuffled)['report']['first_candidate'], first)
        reverse = run(source='SR1')['report']['first_candidate']
        self.assertEqual(reverse['direction'], 'SR1->SR2')
        self.assertIn(reverse['candidate'], ('slime.amber', 'slime.loop', 'slime.quartz'))
        self.assertEqual(reverse['candidate'], 'slime.amber')

    def test_selection_ignores_blocked_and_handles_empty(self):
        self.assertIsNone(select_first([], 'SR2->SR1')['candidate'])
        only_pending = [{'canonical_id': 'slime.x', 'conversion': 'CONVERTED', 'final': 'PENDING_ADAPTER',
                         'complexity': complexity({'has_diet': True, 'plorts': 1})},
                        {'canonical_id': 'slime.y', 'conversion': 'CONVERTED', 'final': 'QUARANTINED',
                         'complexity': complexity({})}]
        picked = select_first(only_pending, 'SR2->SR1')
        self.assertEqual(picked['candidate'], 'slime.x')
        self.assertIn('adapter work required', picked['reason'])

    def test_anchored_destination_registry_reports_native_availability(self):
        from test_largo_coverage import snapshot, definition
        from bridge.registry.snapshot import snapshot_from_scan

        class AnchoredRegistry:
            realm = 'SR1'

            def read_registry(self):
                return snapshot_from_scan(snapshot('SR1', [definition('pink', 'SR1')]))
        universe = world.universe()
        bundle = SourceBundle(ManifestSource(universe['SR2']), AttributeMaterialMapper('SR2'),
                              world.source_material_profile('SR2'), world.source_behaviour_profile('SR2'))
        out = run_batch(source=bundle, destination_registry=AnchoredRegistry(),
                        target_profile=world.target_profile('SR1'), only=['slime.azure'])
        rows = entries(out)
        self.assertEqual(out['classification']['data_evidence']['SR1'], 'RUNTIME_ANCHORED')
        self.assertEqual((rows['slime.pink']['conversion'], rows['slime.pink']['final']),
                         ('SKIPPED_SHARED_NATIVE', 'AVAILABLE'))
        self.assertEqual(rows['slime.azure']['final'], 'EXPERIMENTAL')

    def test_shared_exclusive_plort_is_registered_once(self):
        universe = world.universe()
        for obj in universe['SR2']['objects']:
            if obj['ref'] == 'SlimeDefinition.Prism':
                for ref in obj['references']:
                    if ref['relation'] == 'produces_plort':
                        ref['target'] = 'IdentifiableType.AzurePlort'
        for row in universe['SR2']['registry']['entries']:
            if row['native_ref'] == 'SlimeDefinition.Prism':
                row['produces'] = ['IdentifiableType.AzurePlort']
        out = run(universe)
        rows = entries(out)
        self.assertEqual(out['report']['registration_conflicts'],
                         [{'plort': 'plort.azure', 'owner': 'slime.azure', 'species': ['slime.azure', 'slime.prism']}])
        self.assertEqual(rows['slime.azure']['final'], 'EXPERIMENTAL')
        self.assertEqual(rows['slime.prism']['final'], 'PENDING_ADAPTER')
        self.assertIn('PLORT_REGISTRATION_CONFLICT:plort.azure', rows['slime.prism']['reasons'])
        loser = out['results']['slime.prism']
        self.assertEqual(loser['final'], 'PENDING_ADAPTER')
        self.assertFalse(loser['products']['registration_plan']['registrable'])
        self.assertIn('PLORT_OWNED_BY:slime.azure:plort.azure', loser['products']['registration_plan']['blockers'])
        self.assertTrue(out['results']['slime.azure']['products']['registration_plan']['registrable'])
        self.assertEqual(rows['slime.prism']['result_hash'], sha256_json(loser))

    def test_plort_owner_must_be_able_to_register(self):
        universe = world.universe()
        # Murk (PENDING_ADAPTER: unknown shader) sorts before Opal and claims the same plort.
        for obj in universe['SR2']['objects']:
            if obj['ref'] in ('SlimeDefinition.Murk', 'SlimeDefinition.Opal'):
                for ref in obj['references']:
                    if ref['relation'] == 'produces_plort':
                        ref['target'] = 'IdentifiableType.MurkPlort'
        for row in universe['SR2']['registry']['entries']:
            if row['native_ref'] in ('SlimeDefinition.Murk', 'SlimeDefinition.Opal'):
                row['produces'] = ['IdentifiableType.MurkPlort']
        out = run(universe)
        conflict = out['report']['registration_conflicts'][0]
        self.assertEqual((conflict['plort'], conflict['owner']), ('plort.murk', 'slime.opal'))
        self.assertEqual(entries(out)['slime.opal']['final'], 'EXPERIMENTAL')

    def test_only_filter_converts_lazily(self):
        rows = entries(run(only=['slime.azure']))
        self.assertEqual(rows['slime.azure']['conversion'], 'CONVERTED')
        self.assertEqual(rows['slime.prism']['conversion'], 'NOT_REQUESTED')


if __name__ == '__main__':
    unittest.main()


class DiscoveryIsolationTests(unittest.TestCase):
    def test_discovery_crash_fails_species_not_the_report(self):
        with patch.object(batch_module, 'discover', side_effect=RuntimeError('walk exploded')):
            out = run()
        rows = entries(out)
        self.assertEqual((rows['slime.azure']['conversion'], rows['slime.azure']['final']), ('FAILED', 'QUARANTINED'))
        self.assertTrue(any(r.startswith('DISCOVERY_EXCEPTION') for r in rows['slime.azure']['reasons']))
        self.assertEqual(rows['slime.pink']['conversion'], 'SKIPPED_SHARED_NATIVE')
