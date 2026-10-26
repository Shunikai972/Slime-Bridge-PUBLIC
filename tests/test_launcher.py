import tempfile
import unittest
import subprocess
import os
from pathlib import Path
from unittest.mock import patch

from apps.launcher.desktop import LauncherAPI
from apps.launcher.installations import discover_manual


class LauncherAPITests(unittest.TestCase):
    def test_desktop_api_does_not_expose_native_window_to_js_reflection(self):
        api = LauncherAPI(config_path=Path('C:/no-config'), known_paths=())
        self.assertFalse([name for name in vars(api) if not name.startswith('_')])

    def test_manual_exe_is_validated_saved_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / 'game'
            (game / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                (game / name).touch()
            config = root / 'lab' / 'Launcher' / 'installations.json'
            api = LauncherAPI(config_path=config, known_paths=(), lab_games=Path(tmp) / 'nolab')
            with patch('apps.launcher.desktop.available_installations', side_effect=lambda paths: [discover_manual(p) for p in paths]):
                self.assertEqual(api.add_manual_path(str(game / 'SlimeRancher.exe'))['game'], 'SR1')
                self.assertEqual(api.add_manual_path(str(game))['game'], 'SR1')
                self.assertEqual(len(api.get_overview()['installations']), 1)
            self.assertEqual(len(LauncherAPI(config_path=config, known_paths=(), lab_games=Path(tmp) / 'nolab').manual_paths()), 1)
            self.assertTrue(api.acknowledge_legal()['understood'])
            self.assertTrue(LauncherAPI(config_path=config, known_paths=(), lab_games=Path(tmp) / 'nolab').get_overview()['legal_understood'])

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_launcher_config_refuses_junction_into_another_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / 'outside'
            outside.mkdir()
            config_dir = root / 'Lab' / 'Launcher'
            config_dir.parent.mkdir()
            subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(config_dir), str(outside)],
                           check=True, capture_output=True)
            with self.assertRaises(ValueError):
                LauncherAPI(config_path=config_dir / 'installations.json', known_paths=()).acknowledge_legal()
            self.assertFalse((outside / 'installations.json').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_junction_to_source_install_is_not_labeled_lab(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source'
            (source / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                (source / name).touch()
            lab = root / 'Lab' / 'Games'
            lab.mkdir(parents=True)
            subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(lab / 'SR1'), str(source)],
                           check=True, capture_output=True)
            api = LauncherAPI(config_path=root / 'cfg.json', known_paths=(), lab_games=lab)
            self.assertEqual(api._lab_installations(), [])

    def test_bridge_status_names_lab_versus_source_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def make_sr1(game):
                (game / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
                for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                    (game / name).touch()
            source, lab_games = root / 'source', root / 'Lab' / 'Games'
            make_sr1(source)
            make_sr1(lab_games / 'SR1')
            plugin = lab_games / 'SR1' / 'SRML' / 'Mods' / 'SlimeBridge'
            plugin.mkdir(parents=True)
            (plugin / 'SlimeBridge.SR1.dll').touch()
            api = LauncherAPI(config_path=root / 'cfg.json', known_paths=(source, lab_games / 'SR1'), lab_games=lab_games)
            with patch('apps.launcher.main.discover_stores', return_value=[]):
                rows = api.get_overview()['installations']
            by_role = {row['role']: row for row in rows}
            self.assertEqual(len(rows), 2)
            self.assertFalse(by_role['source']['bridge_plugin'])
            self.assertTrue(by_role['lab']['bridge_plugin'])
            self.assertEqual(by_role['lab']['store'], 'lab')

    def test_invalid_manual_path_never_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'Launcher' / 'installations.json'
            api = LauncherAPI(config_path=config, known_paths=(), lab_games=Path(tmp) / 'nolab')
            with self.assertRaises(ValueError):
                api.add_manual_path(str(Path(tmp) / 'missing.exe'))
            self.assertFalse(config.exists())


if __name__ == '__main__':
    unittest.main()
