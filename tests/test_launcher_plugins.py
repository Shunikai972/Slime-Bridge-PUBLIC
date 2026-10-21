import tempfile
import unittest
from pathlib import Path

from apps.launcher import plugins


def make_game(root, realm, loader=True):
    root = Path(root)
    if realm == 'SR1':
        if loader:
            (root / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            (root / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').write_bytes(b'srml')
    elif loader:
        (root / 'MelonLoader' / 'net6').mkdir(parents=True)
        (root / 'MelonLoader' / 'net6' / 'MelonLoader.dll').write_bytes(b'ml')
        (root / 'version.dll').write_bytes(b'v')
    return root


def make_build(repo, realm, payload=b'plugin-v1'):
    folder = plugins.source_dirs(realm, repo)[1]
    folder.mkdir(parents=True, exist_ok=True)
    for name in plugins.FILES[realm]:
        (folder / name).write_bytes(payload + name.encode())


class PluginInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.repo = self.base / 'repo'

    def tearDown(self):
        self.tmp.cleanup()

    def test_install_update_remove_for_both_games(self):
        for realm in ('SR1', 'SR2'):
            game = make_game(self.base / realm, realm)
            self.assertEqual(plugins.status(realm, game, self.repo)['state'], 'unavailable')
            make_build(self.repo, realm)
            self.assertEqual(plugins.status(realm, game, self.repo), {'state': 'missing', 'loader': True, 'source': str(plugins.source_dirs(realm, self.repo)[1])})
            result = plugins.install(realm, game, self.repo)
            self.assertTrue(result['ok'])
            self.assertEqual(plugins.status(realm, game, self.repo)['state'], 'current')
            make_build(self.repo, realm, b'plugin-v2')
            self.assertEqual(plugins.status(realm, game, self.repo)['state'], 'outdated')
            plugins.install(realm, game, self.repo)
            self.assertEqual(plugins.status(realm, game, self.repo)['state'], 'current')
            self.assertEqual(plugins.remove(realm, game)['removed'][0], plugins.FILES[realm][0])
            self.assertEqual(plugins.status(realm, game, self.repo)['state'], 'missing')
            self.assertFalse(list(plugins.destination(realm, game).glob('.*installing')) if plugins.destination(realm, game).exists() else [])

    def test_without_a_mod_loader_nothing_is_written(self):
        game = make_game(self.base / 'SR2', 'SR2', loader=False)
        make_build(self.repo, 'SR2')
        with self.assertRaises(plugins.PluginError) as ctx:
            plugins.install('SR2', game, self.repo)
        self.assertEqual(ctx.exception.code, 'LOADER_MISSING')
        self.assertFalse((game / 'Mods').exists())

    def test_nothing_to_install_is_reported(self):
        game = make_game(self.base / 'SR1', 'SR1')
        with self.assertRaises(plugins.PluginError) as ctx:
            plugins.install('SR1', game, self.repo)
        self.assertEqual(ctx.exception.code, 'NO_BUILT_PLUGIN')

    def test_a_packaged_plugins_folder_wins_over_the_local_build(self):
        make_build(self.repo, 'SR2', b'build')
        packaged = self.repo / 'plugins' / 'SR2'
        packaged.mkdir(parents=True)
        (packaged / 'SlimeBridge.SR2.dll').write_bytes(b'packaged')
        game = make_game(self.base / 'SR2', 'SR2')
        plugins.install('SR2', game, self.repo)
        self.assertEqual((game / 'Mods' / 'SlimeBridge.SR2.dll').read_bytes(), b'packaged')


if __name__ == '__main__':
    unittest.main()
