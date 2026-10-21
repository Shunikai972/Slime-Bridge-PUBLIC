"""pack.py: LAB copies get plugin + island + species armed in one call; real installs are left alone."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.launcher import pack


def make_repo(root, with_spec=True, cache_for=()):
    (root / 'Lab' / 'Games' / 'SR1').mkdir(parents=True)
    if with_spec:
        spec_dir = root / 'Lab' / 'GeneratedContent' / 'species-import' / '20260101-r1'
        spec_dir.mkdir(parents=True)
        (spec_dir / 'import-spec-SR1.json').write_text(json.dumps({'entries': [
            {'canonical_id': 'slime.ringtail', 'display_name': 'Ringtail', 'status': 'READY'},
            {'canonical_id': 'slime.cotton', 'display_name': 'Cotton', 'status': 'READY'},
            {'canonical_id': 'slime.shadow', 'display_name': 'Shadow', 'status': 'PENDING_DATA'}]}), encoding='utf-8')
    for canonical in cache_for:
        folder = root / 'Lab' / 'AssetCache' / 'sr2-to-sr1' / canonical
        folder.mkdir(parents=True)
        for name in ('icon-descriptor.json', 'source-animation-provenance.json', 'cache-manifest.json', 'model.json'):
            (folder / name).write_text('{}', encoding='utf-8')
    return root


class PackTests(unittest.TestCase):
    def test_only_the_lab_copy_is_a_lab_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            self.assertTrue(pack.is_lab_copy('SR1', repo / 'Lab' / 'Games' / 'SR1', repo))
            self.assertFalse(pack.is_lab_copy('SR1', repo / 'elsewhere', repo))
            self.assertFalse(pack.is_lab_copy('SR2', repo / 'Lab' / 'Games' / 'SR1', repo))

    def test_lab_env_arms_the_species_for_the_lab_sr1_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp))
            env = pack.lab_env('SR1', repo / 'Lab' / 'Games' / 'SR1', repo)
            self.assertEqual(set(env), {'SLIMEBRIDGE_LAB_EXE', 'SLIMEBRIDGE_IMPORT_SPEC', 'SLIMEBRIDGE_IMPORT_OUT',
                                        'SLIMEBRIDGE_IMPORT_RUN'})
            self.assertTrue(env['SLIMEBRIDGE_IMPORT_SPEC'].endswith('import-spec-SR1.json'))
            self.assertTrue(env['SLIMEBRIDGE_LAB_EXE'].endswith('SlimeRancher.exe'))
            self.assertTrue(Path(env['SLIMEBRIDGE_IMPORT_OUT']).is_dir())
            self.assertEqual(pack.lab_env('SR1', repo / 'real', repo), {})
            self.assertEqual(pack.lab_env('SR2', repo / 'Lab' / 'Games' / 'SR1', repo), {})
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), with_spec=False)
            self.assertEqual(pack.lab_env('SR1', repo / 'Lab' / 'Games' / 'SR1', repo), {})

    def test_species_ready_reports_missing_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), cache_for=('slime.ringtail',))
            self.assertEqual(pack.species_ready(repo), (['slime.cotton'], 2))

    def test_prepare_installs_plugin_and_island_and_reports_missing_species(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), cache_for=('slime.ringtail',))
            root = repo / 'Lab' / 'Games' / 'SR1'
            layout = {'path': str(repo / 'layout-001'), 'name': 'layout-001', 'sha256': 'x', 'statues': 17}
            with patch('apps.launcher.pack.plugins.status', return_value={'state': 'outdated', 'loader': True, 'source': 's'}), \
                    patch('apps.launcher.pack.plugins.install',
                          return_value={'ok': True, 'folder': 'f', 'files': ['SlimeBridge.SR1.dll']}) as install, \
                    patch('apps.launcher.pack.sanctuary.latest_layout', return_value=layout), \
                    patch('apps.launcher.pack.sanctuary.installed', return_value={'present': False}), \
                    patch('apps.launcher.pack.sanctuary.install_layout', return_value={'ok': True}) as island:
                result = pack.prepare('SR1', root, repo)
            install.assert_called_once()
            island.assert_called_once_with(layout['path'], root)
            status = {s['step']: s['status'] for s in result['steps']}
            self.assertEqual(status, {'plugin': 'INSTALLED', 'island': 'INSTALLED', 'species': 'CACHES_MISSING', 'largos': 'NO_CACHES'})
            self.assertTrue(result['ok'])
            self.assertTrue(any('slime.cotton' in w for w in result['warnings']))

    def test_prepare_leaves_a_current_install_alone_and_a_real_install_gets_no_species_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(Path(tmp), cache_for=('slime.ringtail', 'slime.cotton'))
            lab = repo / 'Lab' / 'Games' / 'SR1'
            layout = {'path': 'p', 'name': 'layout-001', 'sha256': 'x', 'statues': 17}
            with patch('apps.launcher.pack.plugins.status', return_value={'state': 'current', 'loader': True, 'source': 's'}), \
                    patch('apps.launcher.pack.plugins.install') as install, \
                    patch('apps.launcher.pack.sanctuary.latest_layout', return_value=layout), \
                    patch('apps.launcher.pack.sanctuary.installed', return_value={'present': True, 'matches_latest': True}), \
                    patch('apps.launcher.pack.sanctuary.install_layout') as island:
                result = pack.prepare('SR1', lab, repo)
                real = pack.prepare('SR1', repo / 'real', repo)
            install.assert_not_called()
            island.assert_not_called()
            self.assertEqual({s['step']: s['status'] for s in result['steps']},
                             {'plugin': 'CURRENT', 'island': 'CURRENT', 'species': 'READY', 'largos': 'NO_CACHES'})
            self.assertNotIn('species', {s['step'] for s in real['steps']})


if __name__ == '__main__':
    unittest.main()
