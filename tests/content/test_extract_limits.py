import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.content import species_pipeline as pipeline
from bridge.content import memcap


class RamTierTests(unittest.TestCase):
    def tearDown(self):
        pipeline._RAM_GB = None

    def test_bundle_work_is_refused_below_its_memory_need(self):
        pipeline._RAM_GB = 4.0
        with self.assertRaises(pipeline.RamLimit) as caught:
            pipeline._bundle_assets(Path('sr2'))
        self.assertEqual(caught.exception.needed_gb, pipeline.BUNDLE_LEAN_GB)
        pipeline._RAM_GB = 8.0
        with self.assertRaises(pipeline.RamLimit) as caught:
            pipeline._bundle_assets(Path('sr2'), pipeline.FULL_EXTRAS)
        self.assertEqual(caught.exception.needed_gb, pipeline.BUNDLE_FULL_GB)

    def test_no_limit_means_no_refusal(self):
        pipeline._RAM_GB = None
        pipeline._require_ram(1000)          # does not raise

    def test_a_row_over_the_limit_is_skipped_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            sr2 = Path(tmp) / 'sr2'
            sr2.mkdir()
            (sr2 / 'resources.assets').write_bytes(b'x')

            def fake_build(row, assets, sr2_dir, cache_root, force):
                pipeline._RAM_GB = 4.0
                pipeline._require_ram(pipeline.BUNDLE_LEAN_GB)

            with mock.patch.object(pipeline, 'Sr2Assets', return_value=object()), mock.patch.object(pipeline, 'build_species', fake_build):
                outcome = pipeline.run([{'canonical': 'largo.a.b', 'species': 'AB', 'kind': 'largo'}], sr2, Path(tmp), ram_gb=4)
        self.assertTrue(outcome['ok'])
        self.assertEqual(outcome['by_status'], {'SKIPPED_RAM': 1})
        self.assertIn('raise the limit', outcome['species'][0]['error'])

    def test_the_limit_is_cleared_after_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            sr2 = Path(tmp) / 'sr2'
            sr2.mkdir()
            (sr2 / 'resources.assets').write_bytes(b'x')
            with mock.patch.object(pipeline, 'Sr2Assets', return_value=object()):
                pipeline.run([], sr2, Path(tmp), ram_gb=6)
        self.assertIsNone(pipeline._RAM_GB)


class MemcapTests(unittest.TestCase):
    def test_nonsense_limits_do_nothing(self):
        self.assertFalse(memcap.limit_process_memory(0))
        self.assertFalse(memcap.limit_process_memory(None))
        self.assertFalse(memcap.limit_process_memory(-3))


class WorkerProtocolTests(unittest.TestCase):
    def test_worker_reports_progress_and_result_lines(self):
        import io
        from contextlib import redirect_stdout
        from bridge.content import extract_worker
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            request.write_text(json.dumps({'rows': [{'canonical': 'slime.x', 'species': 'X'}], 'sr2_dir': tmp, 'cache_root': tmp}), encoding='utf-8')

            def fake_run(rows, sr2_dir, cache_root, force=False, progress=None, cancelled=None, ram_gb=None):
                progress(0, 1, '[slime.x] working')
                progress(1, 1, '[slime.x] BUILT')
                return {'ok': True, 'count': 1, 'by_status': {'BUILT': 1}, 'species': []}

            out = io.StringIO()
            with mock.patch.object(pipeline, 'run', fake_run), redirect_stdout(out):
                code = extract_worker.main([str(request)])
        events = [json.loads(line[2:]) for line in out.getvalue().splitlines() if line.startswith('@@')]
        self.assertEqual(code, 0)
        self.assertEqual([e['event'] for e in events], ['start', 'progress', 'progress', 'result'])
        self.assertEqual(events[-1]['outcome']['by_status'], {'BUILT': 1})

    def test_worker_reports_memory_exhaustion_as_fatal(self):
        import io
        from contextlib import redirect_stdout
        from bridge.content import extract_worker
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            request.write_text(json.dumps({'rows': [], 'sr2_dir': tmp, 'cache_root': tmp, 'ram_gb': 3}), encoding='utf-8')
            out = io.StringIO()
            with mock.patch.object(pipeline, 'run', side_effect=MemoryError), redirect_stdout(out):
                code = extract_worker.main([str(request)])
        self.assertEqual(code, 3)
        self.assertIn('out of memory', out.getvalue())


if __name__ == '__main__':
    unittest.main()
