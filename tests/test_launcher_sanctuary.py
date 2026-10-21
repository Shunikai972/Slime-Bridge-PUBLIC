"""Launcher side of the sanctuary: build job, install/remove next to the game, API surface."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from apps.launcher import sanctuary
from apps.launcher.desktop import LauncherAPI
from bridge.sanctuary import layout as layout_module
from tests.sanctuary.fixtures import copy_kit
from tests.test_launcher_backend import make_sr1


def fake_extractor(data_dir, out, allow_nonlab):
    out = Path(out)
    out.mkdir(parents=True)
    kit = copy_kit()
    (out / 'sanctuary-kit.json').write_text(json.dumps(kit), encoding='utf-8')
    return {'ok': True, 'kit_sha256': 'k' * 64}


def real_builder(kit_dir, config, out):
    return _publish(kit_dir, config, out)


def _publish(kit_dir, config, out):
    layout = layout_module.build_layout(layout_module.load_kit(kit_dir), config)
    out = Path(out)
    out.mkdir(parents=True)
    (out / 'sanctuary-layout.json').write_bytes(layout_module.layout_bytes(layout))
    return {'ok': True, 'statues': len(layout['statues']), 'required': len(layout['requirement_manifest']['required'])}


class JobTests(unittest.TestCase):
    def run_job(self, tmp, extractor=fake_extractor, builder=real_builder, config=None):
        job = sanctuary.SanctuaryJob(Path(tmp) / 'SlimeRancher_Data', config or {}, extractor=extractor, builder=builder,
                                     kit_root=Path(tmp) / 'kits', layout_root=Path(tmp) / 'layouts')
        job.start().join(30)
        return job

    def test_builds_a_kit_then_a_layout_and_reports_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self.run_job(tmp)
            state = job.snapshot()
            self.assertEqual(state['status'], 'DONE', state)
            self.assertEqual(state['progress'], 1.0)
            self.assertEqual(state['result']['required'], 9)
            self.assertTrue(Path(state['result']['layout'], 'sanctuary-layout.json').is_file())
            self.assertEqual(sanctuary.latest_layout(Path(tmp) / 'layouts')['required'], 9)
            kit = sanctuary.latest_kit(Path(tmp) / 'kits')
            self.assertEqual(kit['statues']['native'], 4)
            self.assertEqual(kit['statues']['synthesized'], 6)

    def test_refresh_rebuilds_a_layout_made_by_older_generator_code_or_another_kit(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.run_job(tmp)
            kits, layouts = Path(tmp) / 'kits', Path(tmp) / 'layouts'
            def publish(layout, out):
                out = Path(out)
                out.mkdir(parents=True)
                (out / 'sanctuary-layout.json').write_bytes(layout_module.layout_bytes(layout))
            self.assertIsNone(sanctuary.refresh_layout(kits, layouts, publish))        # current: nothing to do
            newest = sanctuary._newest(layouts, 'layout-', 'sanctuary-layout.json')
            document = json.loads((newest / 'sanctuary-layout.json').read_text(encoding='utf-8'))
            document['generator'] = 'old-generator'
            (newest / 'sanctuary-layout.json').write_text(json.dumps(document), encoding='utf-8')
            rebuilt = sanctuary.refresh_layout(kits, layouts, publish)
            self.assertTrue(rebuilt and rebuilt.startswith('layout-'))
            fresh = json.loads((layouts / rebuilt / 'sanctuary-layout.json').read_text(encoding='utf-8'))
            self.assertEqual(fresh['generator'], layout_module.generator_id())
            self.assertEqual(fresh['config'], document['config'])                       # the player's settings carry over

    def test_a_failing_extractor_is_reported_not_raised(self):
        def broken(*_args):
            raise ValueError('SR1 data directory missing')
        with tempfile.TemporaryDirectory() as tmp:
            state = self.run_job(tmp, extractor=broken).snapshot()
            self.assertEqual(state['status'], 'FAILED')
            self.assertIn('SR1 data directory missing', state['error'])
            self.assertIsNone(sanctuary.latest_layout(Path(tmp) / 'layouts'))

    def test_settings_reach_the_layout_and_old_runs_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            import time
            for i in range(5):
                job = self.run_job(tmp, config={'sr1_gold_plort_gate_cost': 10 + i})
                self.assertEqual(job.snapshot()['status'], 'DONE')
                time.sleep(1.05)                                    # run folders are stamped to the second
            self.assertEqual(len(list((Path(tmp) / 'layouts').iterdir())), sanctuary.KEEP)
            self.assertEqual(sanctuary.latest_layout(Path(tmp) / 'layouts')['gold_cost'], 14)


class InstallTests(unittest.TestCase):
    def make_layout(self, tmp):
        folder = Path(tmp) / 'layout-1'
        _publish_dir = _publish
        kit_dir = Path(tmp) / 'kit'
        fake_extractor(None, kit_dir, True)
        _publish_dir(kit_dir, {}, folder)
        return folder

    def test_install_is_additive_verified_and_removable(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make_sr1(Path(tmp) / 'SR1')
            (game / 'keep.txt').write_text('mine')
            folder = self.make_layout(tmp)
            result = sanctuary.install_layout(folder, game)
            self.assertTrue(result['ok'])
            target = game / 'SlimeBridge' / 'sanctuary'
            self.assertEqual((target / 'sanctuary-layout.json').read_bytes(), (folder / 'sanctuary-layout.json').read_bytes())
            self.assertEqual(sorted(p.name for p in target.iterdir()), ['install-receipt.json', 'sanctuary-layout.json'])
            self.assertTrue(sanctuary.installed(game)['present'])
            removed = sanctuary.remove_installed(game)
            self.assertEqual(sorted(removed['removed']), ['install-receipt.json', 'sanctuary-layout.json'])
            self.assertFalse((game / 'SlimeBridge').exists())
            self.assertEqual((game / 'keep.txt').read_text(), 'mine')

    def test_refuses_a_folder_that_is_not_sr1_and_a_missing_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.make_layout(tmp)
            with self.assertRaises(ValueError):
                sanctuary.install_layout(folder, Path(tmp) / 'not-a-game')
            with self.assertRaises(ValueError):
                sanctuary.install_layout(Path(tmp) / 'no-layout', make_sr1(Path(tmp) / 'SR1'))

    def test_remove_leaves_foreign_files_in_the_folder_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make_sr1(Path(tmp) / 'SR1')
            sanctuary.install_layout(self.make_layout(tmp), game)
            progress = game / 'SlimeBridge' / 'sanctuary' / 'sanctuary-save.json'
            progress.write_text('{}')
            sanctuary.remove_installed(game)
            self.assertTrue(progress.is_file())

    def test_dependencies_report_what_to_install(self):
        report = sanctuary.dependencies()
        self.assertIn('ok', report)
        if not report['ok']:
            self.assertIn('pip install', report['hint'])


class ApiTests(unittest.TestCase):
    def api(self, tmp):
        game = make_sr1(Path(tmp) / 'SR1')
        api = LauncherAPI(config_path=Path(tmp) / 'Launcher' / 'installations.json', known_paths=(), lab_games=Path(tmp) / 'nolab')
        api.add_manual_path(str(game))
        return api, game

    def test_state_without_a_build_and_build_refusals(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(sanctuary, 'LAYOUT_ROOT', Path(tmp) / 'none'),                 patch.object(sanctuary, 'KIT_ROOT', Path(tmp) / 'none-kits'):
            api, game = self.api(tmp)
            state = api.sanctuary_state()
            self.assertTrue(state['ok'])
            self.assertEqual(Path(state['sr1_root']).resolve(), game.resolve())
            self.assertEqual(state['settings']['sr1_gold_plort_gate_cost'], 10)
            self.assertFalse(state['installed']['present'])
            self.assertFalse(state['plugin'])
            self.assertEqual(api.sanctuary_install()['error'], 'NOT_BUILT')

    def test_remove_and_status_are_safe_when_nothing_is_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            api, _ = self.api(tmp)
            self.assertEqual(api.sanctuary_remove()['removed'], [])
            self.assertIsNone(api.sanctuary_status()['job'])


if __name__ == '__main__':
    unittest.main()
