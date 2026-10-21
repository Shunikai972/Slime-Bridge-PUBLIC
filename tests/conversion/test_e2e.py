"""Synthetic end-to-end acceptance gate, both directions, from committed fixture files.

registry -> classification -> graph -> IR -> conversion -> target plan -> batch report ->
cache/rebuild, plus the prepared cloud-fixture-v1 scenarios and the CLI. All data is
fictional; passing proves the generic pipeline logic only (UNIT_TESTED), never runtime.
"""
import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from bridge.cache.store import ContentCache
from bridge.content.canonical import sha256_text
from bridge.content.ir import PAYLOAD_KEYS, validate_ir
from bridge.conversion import cli
from bridge.conversion.batch import render_text, run_batch
from bridge.conversion.engine import SourceBundle
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from conversion import legacy_fixture, world
from conversion.test_cache import scratch

ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / 'fixtures' / 'cloud' / 'pipeline'

# Expected verdicts per direction for the committed universe (classification, final).
EXPECTED = {
    'SR2->SR1': {'slime.pink': ('SHARED_NATIVE', 'EXPERIMENTAL'), 'slime.azure': ('SR2_ONLY', 'EXPERIMENTAL'),
                 'slime.prism': ('SR2_ONLY', 'EXPERIMENTAL'), 'slime.ember': ('SR2_ONLY', 'EXPERIMENTAL'),
                 'slime.gale': ('SR2_ONLY', 'EXPERIMENTAL'), 'slime.moss': ('SR2_ONLY', 'EXPERIMENTAL'),
                 'slime.hollow': ('SR2_ONLY', 'QUARANTINED'), 'slime.flicker': ('SR2_ONLY', 'PENDING_ADAPTER'),
                 'slime.opal': ('SR2_ONLY', 'EXPERIMENTAL'), 'slime.murk': ('SR2_ONLY', 'PENDING_ADAPTER'),
                 'slime.twin': ('QUARANTINED', 'QUARANTINED'), 'slime.amber': ('SR1_ONLY', 'EXPERIMENTAL'),
                 'slime.broken': ('QUARANTINED', 'QUARANTINED'), 'slime.cursed': ('QUARANTINED', 'QUARANTINED')},
    'SR1->SR2': {'slime.pink': ('SHARED_NATIVE', 'EXPERIMENTAL'), 'slime.amber': ('SR1_ONLY', 'EXPERIMENTAL'),
                 'slime.quartz': ('SR1_ONLY', 'EXPERIMENTAL'), 'slime.cinder': ('SR1_ONLY', 'PENDING_ADAPTER'),
                 'slime.loop': ('SR1_ONLY', 'EXPERIMENTAL'), 'slime.broken': ('QUARANTINED', 'QUARANTINED'),
                 'slime.cursed': ('QUARANTINED', 'QUARANTINED'), 'slime.azure': ('SR2_ONLY', 'EXPERIMENTAL')},
}


def load(name):
    return json.loads((PIPELINE / name).read_text(encoding='utf-8'))


def fixture_universe():
    return {'SR1': load('source-sr1.json'), 'SR2': load('source-sr2.json')}


def run(universe, source, cache=None, material=None, behaviour=None, target=None):
    other = 'SR1' if source == 'SR2' else 'SR2'
    bundle = SourceBundle(ManifestSource(universe[source]), AttributeMaterialMapper(source),
                          material or load(f'material-profile-{source.lower()}.json'),
                          behaviour or load(f'behaviour-profile-{source.lower()}.json'))
    return run_batch(source=bundle, destination_registry=ManifestSource(universe[other]),
                     target_profile=target or load(f'target-profile-{other.lower()}.json'), cache=cache)


class FixtureIntegrityTests(unittest.TestCase):
    def test_committed_fixtures_match_generator(self):
        for name, data in world.fixture_files().items():
            self.assertEqual(load(name), json.loads(json.dumps(data)), name)
        self.assertEqual(sorted(p.name for p in PIPELINE.glob('*.json')), sorted(world.fixture_files()))

    def test_fixtures_are_synthetic_metadata_only(self):
        for path in PIPELINE.glob('*.json'):
            data = json.loads(path.read_text())
            self.assertIs(data['synthetic'], True, path.name)
            stack = [data]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    self.assertFalse(PAYLOAD_KEYS & set(item), path.name)
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, str):
                    self.assertLess(len(item), 200, path.name)


class EndToEndTests(unittest.TestCase):
    def test_both_directions_full_pipeline(self):
        universe = fixture_universe()
        for source, direction in (('SR2', 'SR2->SR1'), ('SR1', 'SR1->SR2')):
            out = run(universe, source)
            report = out['report']
            self.assertEqual(report['direction'], direction)
            rows = {e['canonical_id']: (e['classification'], e['final']) for e in report['entries']}
            for cid, expected in EXPECTED[direction].items():
                self.assertEqual(rows[cid], expected, f'{direction} {cid}')
            for cid, result in out['results'].items():
                ir = result['products']['ir']
                if result['final'] != 'QUARANTINED':
                    self.assertEqual(validate_ir(ir), [], cid)
                    package = result['products']['package']
                    self.assertEqual(package['direction'], direction)
                    self.assertTrue(package['synthetic'])
                    self.assertTrue(all(c['passed'] is None for c in
                                        result['products']['verification_plan']['checks']))
                self.assertNotEqual(result['final'], 'AVAILABLE')
            self.assertIsNotNone(report['first_candidate']['candidate'])
            self.assertIn('SYNTHETIC DATA', render_text(report))

    def test_associated_content_relations_reach_the_package(self):
        result = run(fixture_universe(), 'SR2')['results']['slime.azure']
        ir, package = result['products']['ir'], result['products']['package']
        self.assertEqual([p['canonical_id'] for p in ir['associated']['plorts']], ['plort.azure'])
        self.assertEqual(ir['associated']['icon']['id'], 'SR2:Fixture/Azure/Icon')
        self.assertEqual(ir['associated']['pedia']['attributes']['title_key'], 'fixture.azure.title')
        self.assertEqual(ir['associated']['market']['attributes']['base_value'], 12)
        self.assertIsNotNone(ir['associated']['localization'])
        self.assertEqual(package['gameplay']['plorts'][0]['native_in_target'], False)
        self.assertTrue(package['associated']['icon']['artifact'].startswith('sprite/'))
        self.assertEqual(len(package['associated']['icon']['textures']), 1)
        expressions = package['prefab']['face']['expressions']
        self.assertEqual(len(expressions), 2)
        self.assertTrue(all(e['textures'] and e['textures'][0].startswith('texture/') for e in expressions))
        artifacts = {a['artifact'] for a in package['artifacts']}
        self.assertTrue({t for e in expressions for t in e['textures']} <= artifacts)
        self.assertEqual(ir['gameplay']['diet']['favourite_foods'], ['food.fixture_food_berry'])

    def test_changed_asset_and_changed_build_rebuild_only_what_they_must(self):
        with scratch() as root:
            cache = ContentCache(root)
            universe = fixture_universe()
            first = run(universe, 'SR2', cache)['report']
            converted = first['cache']['MISS']
            self.assertEqual(run(universe, 'SR2', cache)['report']['cache']['HIT'], converted)
            edited = world.mutate(universe, 'SR2', 'Fixture/Gale/Body0', hash=sha256_text('new gale texture'))
            third = run(edited, 'SR2', cache)['report']
            self.assertEqual(sorted(e['canonical_id'] for e in third['entries'] if e['cache'] == 'MISS'),
                             ['slime.gale'])
            rebuilt = copy.deepcopy(universe)
            rebuilt['SR2']['build']['fingerprint'] = rebuilt['SR2']['registry']['build']['fingerprint'] = 'b2'
            fourth = run(rebuilt, 'SR2', cache)['report']
            self.assertEqual(fourth['cache']['MISS'], converted)
            self.assertTrue(all('source_build' in e['cache_changed'] for e in fourth['entries'] if e['cache']))


class LegacyHandoffFixtureTests(unittest.TestCase):
    """fixtures/cloud/content-universe.json + scenarios.json through the generic pipeline."""

    def setUp(self):
        self.universe = json.loads((ROOT / 'fixtures/cloud/content-universe.json').read_text())
        self.cases = {c['id']: c for c in json.loads((ROOT / 'fixtures/cloud/scenarios.json').read_text())['cases']}

    def run_legacy(self, universe, source, cache=None):
        migrated = legacy_fixture.migrate(universe)
        material, behaviour = legacy_fixture.profiles(source)
        return run(migrated, source, cache, material=material, behaviour=behaviour,
                   target=world.target_profile('SR1' if source == 'SR2' else 'SR2'))

    def finals(self, out):
        return {e['canonical_id']: (e['classification'], e['final']) for e in out['report']['entries']}

    def test_baseline_classifies_like_the_handoff_expectations(self):
        expected = {d['canonical_id']: d['expected_synthetic_classification'] for d in self.universe['definitions']}
        out = self.run_legacy(self.universe, 'SR2')
        self.assertEqual({cid: c for cid, (c, _) in self.finals(out).items()}, expected)
        self.assertEqual(self.finals(out)['slime.azure_test'], ('SR2_ONLY', 'EXPERIMENTAL'))
        self.assertEqual(self.finals(self.run_legacy(self.universe, 'SR1'))['slime.amber_test'],
                         ('SR1_ONLY', 'EXPERIMENTAL'))
        unknown = out['results']['slime.azure_test']['products']['ir']['visual']['materials'][0]['semantic']
        self.assertEqual([p['name'] for p in unknown['unknown_properties']], ['_Roughness'])

    def test_missing_assets_quarantine(self):
        case = self.cases['missing-assets']
        out = self.run_legacy(legacy_fixture.apply_scenario(self.universe, case), 'SR2')
        self.assertEqual(self.finals(out)['slime.azure_test'][1], case['expected'])

    def test_unknown_behaviour_is_pending_adapter(self):
        case = self.cases['unknown-behaviour']
        out = self.run_legacy(legacy_fixture.apply_scenario(self.universe, case), 'SR2')
        self.assertEqual(self.finals(out)['slime.azure_test'][1], case['expected'])

    def test_new_game_build_is_cache_miss(self):
        case = self.cases['new-game-build']
        with scratch() as root:
            cache = ContentCache(root)
            self.run_legacy(self.universe, 'SR2', cache)
            out = self.run_legacy(legacy_fixture.apply_scenario(self.universe, case), 'SR2', cache)
            self.assertEqual(case['expected'], 'CACHE_MISS')
            self.assertEqual(out['report']['cache'], {'HIT': 0, 'MISS': 1, 'CORRUPT': 0})

    def test_future_schema_is_rejected(self):
        case = self.cases['future-schema']
        with self.assertRaises(ValueError) as caught:
            legacy_fixture.migrate(legacy_fixture.apply_scenario(self.universe, case))
        self.assertIn(case['expected'], str(caught.exception))

    def test_cycle_report_is_deterministic(self):
        case = self.cases['cycle']
        universe = legacy_fixture.apply_scenario(self.universe, case)
        reports = []
        for _ in range(3):
            result = self.run_legacy(universe, 'SR2')['results']['slime.azure_test']
            cycles = sorted({d['detail'] for s in result['stages'] for d in s['diagnostics']
                             if d['code'] == 'GRAPH_CYCLE'})
            reports.append(cycles)
        self.assertEqual(case['expected'], 'DETERMINISTIC_CYCLE_REPORT')
        self.assertTrue(reports[0])
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(reports[0], reports[2])


class CliTests(unittest.TestCase):
    def call(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, json.loads(buffer.getvalue())

    def test_batch_and_select_first_through_cli(self):
        with scratch() as root:
            args = ['--sr1', str(PIPELINE / 'source-sr1.json'), '--sr2', str(PIPELINE / 'source-sr2.json'),
                    '--from', 'SR2', '--to', 'SR1',
                    '--material-profile', str(PIPELINE / 'material-profile-sr2.json'),
                    '--behaviour-profile', str(PIPELINE / 'behaviour-profile-sr2.json'),
                    '--target-profile', str(PIPELINE / 'target-profile-sr1.json')]
            code, summary = self.call('batch', *args, '--out-dir', str(root / 'batch'),
                                      '--cache-dir', str(root / 'cache'))
            self.assertEqual(code, 0, summary)
            self.assertEqual(summary['first_candidate'], 'slime.azure')
            self.assertTrue((root / 'batch' / 'report.txt').is_file())
            self.assertTrue((root / 'batch' / 'results' / 'slime.gale.json').is_file())
            code, picked = self.call('select-first', '--report', str(root / 'batch' / 'report.json'))
            self.assertEqual((code, picked['candidate']), (0, 'slime.azure'))
            code, single = self.call('convert', *args, '--species', 'slime.gale', '--out-dir', str(root / 'one'))
            self.assertEqual((code, single['final']), (0, 'EXPERIMENTAL'))
            code, scanned = self.call('scan', '--realm', 'SR2', '--source', str(PIPELINE / 'source-sr2.json'),
                                      '--out-dir', str(root / 'scan'))
            self.assertEqual((code, scanned['completeness']), (0, 'COMPLETE'))
            code, extracted = self.call('extract', '--sr1', str(PIPELINE / 'source-sr1.json'),
                                        '--sr2', str(PIPELINE / 'source-sr2.json'), '--from', 'SR2',
                                        '--species', 'slime.azure', '--out-dir', str(root / 'extract'))
            self.assertEqual((code, extracted['invalid'], extracted['synthetic']), (0, 0, True))

    def test_runtime_anchored_registry_through_existing_scanner_gate(self):
        import hashlib
        with scratch() as root:
            build = root / 'active-build.bin'
            build.write_bytes(b'stand-in for a user-owned build file (synthetic test)')
            digest = hashlib.sha256(build.read_bytes()).hexdigest()
            manifest = load('source-sr2.json')
            manifest['build']['fingerprint'] = manifest['registry']['build']['fingerprint'] = digest
            (root / 'sr2.json').write_text(json.dumps(manifest))
            definition = {'canonical_id': 'slime.azure', 'canonical_evidence': ['test mapping'], 'native_id': None,
                          'reference_id': 'SlimeDefinition.Azure', 'object_ref': 'SlimeDefinition.Azure',
                          'name': 'Azure', 'is_largo': False, 'can_largofy': True, 'base_slimes': [],
                          'produced_plorts': ['IdentifiableType.AzurePlort'], 'prefab_refs': [], 'evidence': ['test']}
            snapshot = {'schema_version': 1, 'realm': 'SR2', 'game_build': 'synthetic', 'completeness': 'COMPLETE',
                        'registry_traversed': True, 'build_match': 'VERIFIED',
                        'build_proof': {'active_sha256': digest, 'scanned_sha256': digest,
                                        'active_path': str(build), 'provenance': 'test'},
                        'traversal_proof': {'registry_identity': 'synthetic-registry', 'registry_sha256': 'b' * 64,
                                            'expected_count': 1, 'observed_count': 1},
                        'definitions': [definition]}
            receipt = {'realm': 'SR2', 'active_sha256': digest, 'registry_identity': 'synthetic-registry',
                       'registry_sha256': 'b' * 64, 'object_refs': ['SlimeDefinition.Azure']}
            (root / 'snapshot.json').write_text(json.dumps(snapshot))
            (root / 'receipt.json').write_text(json.dumps(receipt))
            anchored = ['--sr1', str(PIPELINE / 'source-sr1.json'), '--sr2', str(root / 'sr2.json'),
                        '--sr2-runtime-snapshot', str(root / 'snapshot.json'), '--sr2-active-build', str(build),
                        '--sr2-traversal-receipt', str(root / 'receipt.json')]
            code, summary = self.call('classify', *anchored, '--out-dir', str(root / 'classify'))
            self.assertEqual(code, 0, summary)
            result = json.loads((root / 'classify' / 'classification.json').read_text())
            self.assertEqual(result['data_evidence']['SR2'], 'RUNTIME_ANCHORED')
            self.assertEqual({r['canonical_id']: r['classification'] for r in result['species']}['slime.azure'],
                             'SR2_ONLY')
            code, summary = self.call('classify', *anchored[:-2], '--out-dir', str(root / 'unanchored'))
            self.assertEqual((code, summary['status']), (4, 'INVALID_INPUT'))   # COMPLETE without a receipt

    def test_native_paths_fail_closed_and_outputs_are_confined(self):
        code, result = self.call('scan', '--realm', 'SR2', '--game-root', '/nonexistent/install')
        self.assertEqual((code, result['status']), (3, 'BLOCKED_ON_LOCAL_QUERY'))
        self.assertTrue(result['local_queries'])
        code, result = self.call('scan', '--realm', 'SR2', '--source', str(PIPELINE / 'source-sr2.json'),
                                 '--out-dir', str(ROOT / 'docs'))
        self.assertEqual((code, result['status']), (4, 'INVALID_INPUT'))
        code, result = self.call('convert', '--sr1', str(PIPELINE / 'source-sr1.json'), '--sr2-game-root', '/x',
                                 '--from', 'SR2', '--to', 'SR1', '--species', 'slime.azure',
                                 '--material-profile', str(PIPELINE / 'material-profile-sr2.json'),
                                 '--behaviour-profile', str(PIPELINE / 'behaviour-profile-sr2.json'))
        self.assertEqual(code, 3)
        for bad in ('../../../../escape', 'slime.ok/../../x', 'plort.pink'):
            code, result = self.call('plan-extraction', '--sr1', str(PIPELINE / 'source-sr1.json'),
                                     '--sr2', str(PIPELINE / 'source-sr2.json'), '--from', 'SR2', '--species', bad)
            self.assertEqual((code, result['status']), (4, 'INVALID_INPUT'), bad)


if __name__ == '__main__':
    unittest.main()
