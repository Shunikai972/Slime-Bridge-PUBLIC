"""Conversion-side source-cache wiring: explicit selection, atomic verified publish, staleness.

The conversion job must materialize the complete offline source cache (geometry + face + icon
+ clips) through the existing conversion ecosystem: a provider-compatible materializer, an
optional engine step, and a CLI verb. Selection is explicit-only: without reviewed clip IDs
and an exact default name the job is REFUSED/BLOCKED - no Animator graph is ever inferred.

TDD world: ``source_cache_world`` (fake SR2 data + deterministic extractor double). No game
files are read here; the real-data comparison is an opt-in test gated by environment.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from bridge.content.canonical import is_sha256, sha256_json
from bridge.conversion import cli, source_cache as sc
from bridge.conversion.engine import STAGES, SourceBundle, convert_species
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.materials import AttributeMaterialMapper
from bridge.registry.reconcile import classify_species
from conversion import source_cache_world as scw
from conversion import world

REPO = Path(__file__).resolve().parents[2]
WORLD = world.universe()
CLASSIFICATION = classify_species(ManifestSource(WORLD['SR1']).read_registry(),
                                  ManifestSource(WORLD['SR2']).read_registry())


def engine_bundle(realm='SR2'):
    provider = ManifestSource(WORLD[realm])
    return SourceBundle(provider, AttributeMaterialMapper(realm), world.source_material_profile(realm),
                        world.source_behaviour_profile(realm))


def engine_convert(cid, source_cache=None):
    return convert_species(cid, classification=CLASSIFICATION, source=engine_bundle(),
                           target_profile=world.target_profile('SR1'), source_cache=source_cache)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dir_files(directory):
    return sorted(p.name for p in Path(directory).iterdir() if p.is_file())


class SourceCacheJobWorld:
    """Shared scratch: fresh private dirs under the worktree Lab, cleaned by the test."""

    def make_world(self, case):
        scratch = scw.scratch_dir(f'sc-{case.__class__.__name__.lower()}')
        case.addCleanup(scw.cleanup, scratch)
        sr2 = scw.make_sr2_dir(scratch / 'source')
        cache = scratch / 'RuntTail-cache'
        return scratch, sr2, cache


class SelectionValidationTests(unittest.TestCase):
    def test_valid_selection_is_normalized(self):
        self.assertEqual(sc.validate_selection([3, 1, 2], 'Idle v4'), (3, 1, 2))

    def test_selection_must_be_explicit_and_bounded(self):
        bad = [(None, 'Idle v4'), ([], 'Idle v4'), ([0], 'Idle v4'), ([-1], 'Idle v4'),
               ([True], 'Idle v4'), ([1, 1], 'Idle v4'), (list(range(1, 18)), 'Idle v4'),
               ([1], None), ([1], ''), ([1], 7), ('2170', 'Idle v4'), ([2170.0], 'Idle v4')]
        for clip_ids, default in bad:
            with self.assertRaises(sc.SelectionRequired, msg=f'{clip_ids!r}/{default!r}'):
                sc.validate_selection(clip_ids, default)


class MaterializeTests(unittest.TestCase):
    def world(self):
        return self.make_world(self)

    def make_world(self, case):
        return SourceCacheJobWorld().make_world(case)

    def test_refuses_without_explicit_selection(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        for missing in ({'clip_ids': None, 'default_clip': 'Idle_Cotton_Ringtail'},
                        {'clip_ids': [2170], 'default_clip': None},
                        {'clip_ids': [], 'default_clip': 'Idle_Cotton_Ringtail'}):
            payload = scw.job(cache, sr2)
            payload.update(missing)
            with self.assertRaises(sc.SelectionRequired):
                sc.materialize_source_cache(payload, runner=runner)
        self.assertEqual(runner.calls, [])
        self.assertFalse(cache.exists())

    def test_fresh_publish_is_atomic_hash_verified_and_receipted(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        receipt = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(receipt['status'], 'BUILT')
        self.assertTrue(receipt['complete'])
        self.assertEqual(len(runner.calls), 1)
        manifest = read_json(cache / 'cache-manifest.json')
        self.assertEqual(manifest['schema'], 'bridge-asset-cache-manifest')
        self.assertEqual(manifest['canonical_id'], 'slime.ringtail')
        self.assertEqual(manifest['converter_version'], sc.EXTRACTOR_CONVERTER_VERSION)
        # every manifest file exists and hashes exactly as recorded
        for name, digest in manifest['files'].items():
            self.assertTrue((cache / name).is_file(), name)
            self.assertEqual(scw.sha256_file(cache / name), digest, name)
        # no unlisted file can ride along
        self.assertEqual(dir_files(cache), sorted(list(manifest['files']) + ['cache-manifest.json']))
        # the sibling receipt covers every file in the cache, manifest included
        receipt_path = sc.receipt_path(Path(cache))
        self.assertTrue(receipt_path.is_file())
        stored = read_json(receipt_path)
        self.assertEqual(stored, receipt)
        self.assertEqual(sorted(receipt['files']), dir_files(cache))
        for name, digest in receipt['files'].items():
            self.assertEqual(scw.sha256_file(cache / name), digest, name)
        self.assertTrue(is_sha256(receipt['receipt_hash']))
        self.assertTrue(receipt['extractor_output']['ok'])
        # nothing staged or superseded is left behind
        leftovers = sorted(p.name for p in Path(cache).parent.iterdir()
                           if 'staging' in p.name or 'superseded' in p.name or p.name.startswith('.'))
        self.assertEqual(leftovers, [])

    def test_second_run_is_an_idempotent_hit(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        first = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        before = {name: scw.sha256_file(cache / name) for name in dir_files(cache)}
        mtimes = {name: (cache / name).stat().st_mtime_ns for name in dir_files(cache)}
        second = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(second['status'], 'HIT')
        self.assertEqual(len(runner.calls), 1, 'a verified published cache must not re-run the extractor')
        self.assertEqual(second['receipt_hash'], first['receipt_hash'])
        self.assertEqual({name: scw.sha256_file(cache / name) for name in dir_files(cache)}, before)
        self.assertEqual({name: (cache / name).stat().st_mtime_ns for name in dir_files(cache)}, mtimes)

    def test_source_change_is_stale_and_rebuilds(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        first = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        time.sleep(0.01)
        with (sr2 / 'resources.assets.resS').open('ab') as stream:
            stream.write(b'patched-source')
        second = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(second['status'], 'REBUILT')
        self.assertEqual(len(runner.calls), 2)
        self.assertNotEqual(second['receipt_hash'], first['receipt_hash'])
        provenance = read_json(cache / 'source-animation-provenance.json')
        self.assertEqual(provenance['source_files'][str(sr2 / 'resources.assets.resS')],
                         scw.sha256_file(sr2 / 'resources.assets.resS'))
        # the republished cache fully verifies against the new identity
        report = sc.verify_source_cache(scw.job(cache, sr2))
        self.assertEqual(report['status'], 'PUBLISHED', report['problems'])

    def test_selection_change_rebuilds(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        changed = scw.job(cache, sr2, clip_ids=(2187, 2165), default_clip='Pounce v4')
        second = sc.materialize_source_cache(changed, runner=runner)
        self.assertEqual(second['status'], 'REBUILT')
        provenance = read_json(cache / 'source-animation-provenance.json')
        self.assertEqual([row['source_path_id'] for row in provenance['clips']], [2187, 2165])
        self.assertEqual(second['selection'], {'clip_ids': [2187, 2165], 'default_clip': 'Pounce v4'})

    def test_tampered_payload_and_rogue_files_regenerate(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        (cache / 'model.json').write_bytes(b'{"tampered": true}')
        (cache / 'rogue.bin').write_bytes(b'rogue')
        second = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(second['status'], 'REBUILT')
        self.assertFalse((cache / 'rogue.bin').exists())
        report = sc.verify_source_cache(scw.job(cache, sr2))
        self.assertEqual(report['status'], 'PUBLISHED', report['problems'])

    def test_missing_receipt_forces_rebuild(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        sc.receipt_path(Path(cache)).unlink()
        second = sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(second['status'], 'REBUILT')
        self.assertEqual(len(runner.calls), 2)

    def test_incomplete_coverage_refuses_publish_with_report(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor(omit=('face',))
        with self.assertRaises(sc.SourceCacheIncomplete) as caught:
            sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        coverage = caught.exception.coverage
        self.assertFalse(coverage['complete'])
        self.assertIn('NO_FACE_PART', coverage['problems'])
        self.assertFalse(cache.exists(), 'nothing may be published from an incomplete build')
        self.assertEqual([p.name for p in scratch.iterdir() if 'staging' in p.name], [])

    def test_failed_rebuild_keeps_the_previous_published_cache(self):
        scratch, sr2, cache = self.world()
        good = scw.FakeExtractor()
        first = sc.materialize_source_cache(scw.job(cache, sr2), runner=good)
        with (sr2 / 'resources.assets.resS').open('ab') as stream:
            stream.write(b'patched-source')
        bad = scw.FakeExtractor(omit=('face',))
        with self.assertRaises(sc.SourceCacheIncomplete):
            sc.materialize_source_cache(scw.job(cache, sr2), runner=bad)
        # fail closed: the old complete cache and its receipt are untouched
        self.assertTrue(cache.is_dir())
        self.assertEqual(read_json(sc.receipt_path(Path(cache)))['receipt_hash'], first['receipt_hash'])
        self.assertEqual([p.name for p in scratch.iterdir() if 'staging' in p.name], [])
        self.assertEqual([p.name for p in scratch.iterdir() if 'superseded' in p.name], [])

    def test_runner_writing_nothing_is_refused(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor(write_nothing=True)
        with self.assertRaises(sc.SourceCacheError):
            sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertFalse(cache.exists())

    def test_canonical_cache_path_is_never_written(self):
        scratch, sr2, _ = self.world()
        canonical = REPO / 'Lab' / 'AssetCache' / 'sr2-to-sr1' / 'slime.ringtail'
        # The lab may legitimately hold a canonical cache at this path; the contract is that
        # the materializer never touches it, not that it does not exist.
        before = ({p.name: p.stat().st_mtime_ns for p in canonical.iterdir()}
                  if canonical.is_dir() else None)
        runner = scw.FakeExtractor()
        with self.assertRaises(sc.SourceCacheError):
            sc.materialize_source_cache(scw.job(canonical, sr2), runner=runner)
        self.assertEqual(runner.calls, [])
        after = ({p.name: p.stat().st_mtime_ns for p in canonical.iterdir()}
                 if canonical.is_dir() else None)
        self.assertEqual(after, before)

    def test_source_outside_lab_requires_allow_nonlab(self):
        scratch, _, cache = self.world()
        with tempfile.TemporaryDirectory(prefix='sc-outside-') as outside:
            sr2 = scw.make_sr2_dir(outside)
            runner = scw.FakeExtractor()
            with self.assertRaises(sc.SourceCacheError):
                sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
            self.assertEqual(runner.calls, [])
            receipt = sc.materialize_source_cache(scw.job(cache, sr2, allow_nonlab=True), runner=runner)
            self.assertEqual(receipt['status'], 'BUILT')
            self.assertTrue(runner.calls[-1].allow_nonlab)

    def test_verify_reports_missing_stale_and_broken(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        report = sc.verify_source_cache(scw.job(cache, sr2))
        self.assertEqual(report['status'], 'MISSING')
        sc.materialize_source_cache(scw.job(cache, sr2), runner=runner)
        self.assertEqual(sc.verify_source_cache(scw.job(cache, sr2))['status'], 'PUBLISHED')
        selection = sc.verify_source_cache(scw.job(cache, sr2, clip_ids=(3001,), default_clip='Slither v4'))
        self.assertEqual(selection['status'], 'STALE')
        self.assertIn('CLIP_SELECTION_CHANGED', selection['changed'])
        (cache / 'model.json').write_bytes(b'{}')
        broken = sc.verify_source_cache(scw.job(cache, sr2))
        self.assertEqual(broken['status'], 'BROKEN')
        self.assertTrue(broken['problems'])


class SubprocessEnvTests(unittest.TestCase):
    def test_child_env_is_clean_and_keeps_dotnet_root(self):
        poisoned = {'PYTHONPATH': 'x', 'PYTHONHOME': 'y', 'VIRTUAL_ENV': 'z', 'CONDA_PREFIX': 'c',
                    'DOTNET_ROOT': 'D:/dotnet', 'PATH': os.environ.get('PATH', ''),
                    'PYTEST_CURRENT_TEST': 'leak'}
        with mock.patch.dict(os.environ, poisoned, clear=True):
            env = sc.child_env()
        for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'CONDA_PREFIX', 'PYTEST_CURRENT_TEST'):
            self.assertNotIn(key, env)
        self.assertEqual(env['DOTNET_ROOT'], 'D:/dotnet')
        self.assertEqual(env['PYTHONIOENCODING'], 'utf-8')

    def test_extractor_command_pins_dependencies_and_explicit_selection(self):
        scratch, sr2, cache = self.world()
        request = sc.ExtractRequest(species='Ringtail', canonical_id='slime.ringtail', sr2_dir=sr2,
                                    out_dir=cache, clip_ids=(2170, 2187), default_clip='Idle v4',
                                    allow_nonlab=False)
        argv = sc.extractor_argv(request, uv='uv')
        self.assertEqual(argv[:2], ['uv', 'run'])
        self.assertIn('UnityPy==1.25.4', argv)
        self.assertIn('TypeTreeGeneratorAPI==0.0.10', argv)
        self.assertIn('--complete-source', argv)
        self.assertEqual(argv[argv.index('--default-clip') + 1], 'Idle v4')
        self.assertEqual([argv[i + 1] for i, token in enumerate(argv) if token == '--clip-id'],
                         ['2170', '2187'])
        self.assertNotIn('--allow-nonlab', argv)

    def world(self):
        return SourceCacheJobWorld().make_world(self)


class EngineStepTests(unittest.TestCase):
    class Stub:
        def __init__(self, receipt=None, error=None):
            self.receipt = receipt
            self.error = error
            self.calls = []

        def materialize(self, canonical_id):
            self.calls.append(canonical_id)
            if self.error is not None:
                raise self.error
            return dict(self.receipt)

    def test_engine_materializes_complete_cache_and_default_path_is_unchanged(self):
        baseline = engine_convert('slime.azure')
        receipt = {'schema': 'bridge-source-cache-receipt', 'schema_version': 1, 'status': 'BUILT',
                   'canonical_id': 'slime.azure', 'complete': True, 'files': {}, 'receipt_hash': sha256_json({'x': 1})}
        stub = self.Stub(receipt=receipt)
        result = engine_convert('slime.azure', source_cache=stub)
        self.assertEqual([s['stage'] for s in result['stages']], list(STAGES))
        self.assertTrue(all(s['status'] in ('OK', 'DEGRADED') for s in result['stages']))
        self.assertEqual(stub.calls, ['slime.azure'])
        self.assertEqual(result['products']['source_cache'], receipt)
        self.assertEqual(result['products']['package'], baseline['products']['package'])
        self.assertNotIn('source_cache', baseline['products'])

    def test_engine_blocks_when_selection_is_missing(self):
        stub = self.Stub(error=sc.SelectionRequired('explicit clip IDs and an exact default clip name are required'))
        result = engine_convert('slime.azure', source_cache=stub)
        statuses = {s['stage']: s['status'] for s in result['stages']}
        self.assertEqual(statuses['PACKAGE_ASSEMBLY'], 'FAILED')
        self.assertEqual(result['final'], 'QUARANTINED')
        self.assertIn('SOURCE_CACHE_BLOCKED', result['reasons'])
        codes = {d['code'] for s in result['stages'] for d in s['diagnostics']}
        self.assertIn('SOURCE_CACHE_REFUSED', codes)
        self.assertEqual(result['products']['source_cache']['status'], 'BLOCKED')

    def test_engine_blocks_on_incomplete_receipt(self):
        stub = self.Stub(receipt={'schema': 'bridge-source-cache-receipt', 'status': 'BUILT', 'complete': False,
                                  'coverage': {'complete': False, 'problems': ['NO_ICON']}})
        result = engine_convert('slime.azure', source_cache=stub)
        statuses = {s['stage']: s['status'] for s in result['stages']}
        self.assertEqual(statuses['PACKAGE_ASSEMBLY'], 'FAILED')
        self.assertEqual(result['final'], 'QUARANTINED')
        codes = {d['code'] for s in result['stages'] for d in s['diagnostics']}
        self.assertIn('SOURCE_CACHE_INCOMPLETE', codes)


class CLITests(unittest.TestCase):
    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(argv)
        try:
            return code, json.loads(buf.getvalue())
        except ValueError:
            self.fail(f'non-JSON CLI output: {buf.getvalue()!r}')

    def test_refuses_without_explicit_selection_exit5(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        with mock.patch.object(sc, 'default_runner', runner):
            code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                          '--sr2-dir', str(sr2), '--cache-dir', str(cache)])
        self.assertEqual(code, cli.EXIT_BLOCKED)
        self.assertEqual(payload['status'], 'BLOCKED')
        self.assertEqual(payload['code'], 'SOURCE_CACHE_SELECTION_REQUIRED')
        self.assertEqual(runner.calls, [])
        self.assertFalse(cache.exists())

    def test_receipt_failure_is_blocked_exit5(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor(omit=('face',))
        with mock.patch.object(sc, 'default_runner', runner):
            code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                          '--sr2-dir', str(sr2), '--cache-dir', str(cache),
                                          '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        self.assertEqual(code, cli.EXIT_BLOCKED)
        self.assertEqual(payload['code'], 'SOURCE_CACHE_INCOMPLETE')
        self.assertFalse(payload['coverage']['complete'])
        self.assertFalse(cache.exists())

    def test_invalid_input_exit4_for_canonical_cache_and_missing_source(self):
        scratch, sr2, _ = self.world()
        canonical = REPO / 'Lab' / 'AssetCache' / 'sr2-to-sr1' / 'slime.ringtail'
        code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                      '--sr2-dir', str(sr2), '--cache-dir', str(canonical),
                                      '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        self.assertEqual(code, cli.EXIT_INPUT)
        self.assertEqual(payload['status'], 'INVALID_INPUT')
        code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                      '--sr2-dir', str(scratch / 'missing'), '--cache-dir', str(scratch / 'c2'),
                                      '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        self.assertEqual(code, cli.EXIT_INPUT)
        self.assertFalse((scratch / 'c2').exists())

    def test_full_fake_job_exit0_with_receipt_output(self):
        scratch, sr2, cache = self.world()
        out = REPO / 'Lab' / 'GeneratedContent' / f'sc-cli-{os.getpid()}-{id(self)}'
        self.addCleanup(scw.cleanup, out)
        runner = scw.FakeExtractor()
        with mock.patch.object(sc, 'default_runner', runner):
            code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                          '--sr2-dir', str(sr2), '--cache-dir', str(cache), '--out-dir', str(out),
                                          '--clip-id', '2170', '--clip-id', '2187', '--default-clip', 'Idle v4'])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['status'], 'BUILT')
        self.assertTrue(payload['complete'])
        receipt_file = out / 'slime.ringtail.source-cache-receipt.json'
        self.assertTrue(receipt_file.is_file())
        self.assertEqual(read_json(receipt_file)['receipt_hash'], payload['receipt_hash'])
        self.assertTrue((cache / 'cache-manifest.json').is_file())

    def test_job_file_drives_the_selection_and_visual_mode(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        job_file = scratch / 'job.json'
        job_file.write_text(json.dumps({'visual_mode': 'SOURCE_COMPLETE', 'species': 'Ringtail',
                                        'canonical_id': 'slime.ringtail', 'sr2_dir': str(sr2),
                                        'cache_dir': str(cache), 'clip_ids': [2170, 2187],
                                        'default_clip': 'Idle v4'}), encoding='utf-8')
        with mock.patch.object(sc, 'default_runner', runner):
            code, payload = self.run_cli(['source-cache', '--job', str(job_file)])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload['status'], 'BUILT')
        template = scratch / 'template.json'
        scratch_cache = scratch / 'template-cache'
        template.write_text(json.dumps({'visual_mode': 'PLACEHOLDER_TEMPLATE', 'species': 'Ringtail',
                                        'canonical_id': 'slime.ringtail', 'sr2_dir': str(sr2),
                                        'cache_dir': str(scratch_cache), 'clip_ids': [2170],
                                        'default_clip': 'Idle_Cotton_Ringtail'}), encoding='utf-8')
        code, payload = self.run_cli(['source-cache', '--job', str(template)])
        self.assertEqual(code, cli.EXIT_INPUT)
        self.assertIn('visual_mode', payload['error'])
        self.assertFalse(scratch_cache.exists())

    def test_events_pin_mismatch_blocks_before_any_extraction(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        pinned = {str(Path(sr2) / name): '0' * 64 for name in sc.SOURCE_FILE_NAMES}
        buf = io.StringIO()
        with mock.patch.object(sc, 'default_runner', runner), contextlib.redirect_stdout(buf):
            code = cli.main(['source-cache', '--events', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                             '--sr2-dir', str(sr2), '--cache-dir', str(cache), '--clip-id', '2170',
                             '--default-clip', 'Idle_Cotton_Ringtail',
                             '--expect-source-files', json.dumps(pinned)])
        lines = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(code, cli.EXIT_BLOCKED, lines)
        self.assertEqual((lines[-1]['status'], lines[-1]['code']), ('BLOCKED', 'SOURCE_CHANGED'))
        stages = [(event['stage'], event['status']) for event in lines if event.get('event') == 'source-cache-stage']
        self.assertIn(('SOURCE_FINGERPRINT', 'RUNNING'), stages)
        self.assertIn(('SOURCE_FINGERPRINT', 'FAILED'), stages)
        self.assertNotIn(('SOURCE_EXTRACTION', 'RUNNING'), stages)
        self.assertEqual(runner.calls, [])
        self.assertFalse(cache.exists())

    def test_events_report_pinned_fingerprints_and_a_matching_pin_builds(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        current = {str(Path(sr2) / name): sc.sha256_file(Path(sr2) / name) for name in sc.SOURCE_FILE_NAMES}
        buf = io.StringIO()
        with mock.patch.object(sc, 'default_runner', runner), contextlib.redirect_stdout(buf):
            code = cli.main(['source-cache', '--events', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                             '--sr2-dir', str(sr2), '--cache-dir', str(cache), '--clip-id', '2170',
                             '--default-clip', 'Idle_Cotton_Ringtail',
                             '--expect-source-files', json.dumps(current)])
        lines = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(code, 0, lines[-1])
        reported = [event for event in lines if event.get('stage') == 'SOURCE_FINGERPRINT'
                    and event.get('status') == 'OK']
        self.assertEqual(len(reported), 1)
        self.assertEqual(reported[0]['source_files'], current)
        self.assertEqual(len(runner.calls), 1)

    def test_staged_verification_failure_emits_a_failed_stage(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor(omit=('face',))
        buf = io.StringIO()
        with mock.patch.object(sc, 'default_runner', runner), contextlib.redirect_stdout(buf):
            code = cli.main(['source-cache', '--events', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                             '--sr2-dir', str(sr2), '--cache-dir', str(cache),
                             '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        lines = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(code, cli.EXIT_BLOCKED, lines[-1])
        verification = [event for event in lines if event.get('stage') == 'CACHE_VERIFICATION']
        self.assertEqual([event['status'] for event in verification], ['RUNNING', 'FAILED'])
        self.assertIn('incomplete source-cache coverage', verification[-1]['error'])

    def test_publication_failure_keeps_the_verified_ok_and_emits_the_real_stage_failed(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        buf = io.StringIO()
        with mock.patch.object(sc, 'default_runner', runner), \
                mock.patch.object(sc.SourceCacheProvider, '_publish',
                                  side_effect=sc.SourceCacheError('publish exploded')), \
                contextlib.redirect_stdout(buf):
            code = cli.main(['source-cache', '--events', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                             '--sr2-dir', str(sr2), '--cache-dir', str(cache),
                             '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        lines = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(code, cli.EXIT_INPUT, lines[-1])
        verification = [event['status'] for event in lines if event.get('stage') == 'CACHE_VERIFICATION']
        self.assertEqual(verification, ['RUNNING', 'OK'])  # no contradictory FAILED after OK
        publication = [event['status'] for event in lines if event.get('stage') == 'CACHE_PUBLICATION']
        self.assertEqual(publication, ['RUNNING', 'FAILED'])
        self.assertFalse(cache.exists())

    def test_receipt_write_failure_does_not_unverify_the_cache(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        buf = io.StringIO()
        with mock.patch.object(sc, 'default_runner', runner), \
                mock.patch.object(sc.SourceCacheProvider, '_write_receipt',
                                  side_effect=sc.SourceCacheError('receipt exploded')), \
                contextlib.redirect_stdout(buf):
            code = cli.main(['source-cache', '--events', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                             '--sr2-dir', str(sr2), '--cache-dir', str(cache),
                             '--clip-id', '2170', '--default-clip', 'Idle_Cotton_Ringtail'])
        lines = [json.loads(line) for line in buf.getvalue().splitlines()]
        self.assertEqual(code, cli.EXIT_INPUT, lines[-1])
        verification = [event['status'] for event in lines if event.get('stage') == 'CACHE_VERIFICATION']
        self.assertEqual(verification, ['RUNNING', 'OK'])
        receipt = [event['status'] for event in lines if event.get('stage') == 'RECEIPT_WRITE']
        self.assertEqual(receipt, ['RUNNING', 'FAILED'])

    def test_expect_source_files_without_events_is_refused(self):
        scratch, sr2, cache = self.world()
        runner = scw.FakeExtractor()
        pinned = {str(Path(sr2) / name): '0' * 64 for name in sc.SOURCE_FILE_NAMES}
        with mock.patch.object(sc, 'default_runner', runner):
            code, payload = self.run_cli(['source-cache', '--canonical', 'slime.ringtail', '--asset-name', 'Ringtail',
                                          '--sr2-dir', str(sr2), '--cache-dir', str(cache), '--clip-id', '2170',
                                          '--default-clip', 'Idle_Cotton_Ringtail',
                                          '--expect-source-files', json.dumps(pinned)])
        self.assertEqual(code, cli.EXIT_INPUT)
        self.assertIn('requires --events', payload['error'])
        self.assertEqual(runner.calls, [])
        self.assertFalse(cache.exists())

    def world(self):
        return SourceCacheJobWorld().make_world(self)


REAL_SR2 = Path(os.environ.get('SLIMEBRIDGE_SR2_DATA',
                               'Lab/Games/SR2/SlimeRancher2_Data'))


@unittest.skipUnless(os.environ.get('SLIMEBRIDGE_REAL_SOURCE_CACHE') == '1' and REAL_SR2.is_dir()
                     and shutil.which('uv'),
                     'opt-in real extraction (uv + SR2 data + SLIMEBRIDGE_REAL_SOURCE_CACHE=1)')
class RealExtractionTests(unittest.TestCase):
    """Opt-in: the pinned extractor against the real read-only SR2 install.

    Asserts the independently verified Ringtail facts: explicit selection, four clips, the
    body/face/ears/tail parts, 877 vertices and the confirmed model.json sha256.
    """

    def test_real_ringtail_matches_the_verified_model(self):
        scratch = scw.scratch_dir('sc-real')
        self.addCleanup(scw.cleanup, scratch)
        cache = scratch / 'Ringtail-optin'
        receipt = sc.materialize_source_cache(scw.job(cache, REAL_SR2, allow_nonlab=True), timeout=1800)
        self.assertEqual(receipt['status'], 'BUILT')
        self.assertTrue(receipt['complete'])
        manifest = read_json(cache / 'cache-manifest.json')
        self.assertEqual(manifest['files']['model.json'],
                         'd86cbc8055af15a942c4396b18dbf9783b89df865f28408abc0c11370d52bf91')
        self.assertEqual([row['source_path_id'] for row in receipt['coverage']['clips']], [2170, 2187, 2165, 2188])
        self.assertEqual(receipt['coverage']['default_clip'], 'Idle_Cotton_Ringtail')
        self.assertEqual(sorted(receipt['coverage']['parts']), ['body', 'ears', 'face', 'tail'])
        self.assertEqual(receipt['coverage']['vertices'], 877)
        self.assertEqual(len(manifest['files']), 11)


if __name__ == '__main__':
    unittest.main()
