import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path

from apps.launcher import integrity


def _make(root, files):
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'x')


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _complete_sr2(self, root):
        files = [f if isinstance(f, str) else f[0] for f in integrity.REQUIRED['SR2']]
        files += [f'{integrity.SR2_BUNDLES}/b{i}.bundle' for i in range(integrity.MIN_SR2_BUNDLES)]
        _make(root, files)

    def test_complete_install_passes(self):
        root = self.base / 'Slime Rancher 2'
        self._complete_sr2(root)
        self.assertTrue(integrity.check('SR2', root)['complete'])

    def test_google_drive_part_is_named_with_the_fix(self):
        root = self.base / 'Slime.Rancher.2.v1.3.0-20261027T155201Z-1-001' / 'Slime.Rancher.2.v1.3.0' / 'Slime Rancher 2'
        self._complete_sr2(root)
        (root / 'SlimeRancher2_Data' / 'resources.assets').unlink()
        state = integrity.check('SR2', root)
        self.assertFalse(state['complete'])
        self.assertIn('SlimeRancher2_Data/resources.assets', state['missing'])
        self.assertEqual((state['hint_code'], state['part']), ('drive_part', 1))
        with self.assertRaises(integrity.IncompleteGame) as ctx:
            integrity.require('SR2', root)
        self.assertIn('extract ALL the parts', str(ctx.exception))

    def test_old_json_catalog_is_accepted(self):
        root = self.base / 'sr2_062'
        self._complete_sr2(root)
        (root / 'SlimeRancher2_Data' / 'StreamingAssets' / 'aa' / 'catalog.bin').unlink()
        _make(root, ['SlimeRancher2_Data/StreamingAssets/aa/catalog.json'])
        self.assertTrue(integrity.check('SR2', root)['complete'])

    def test_sr1_missing_assets(self):
        root = self.base / 'sr1'
        _make(root, integrity.REQUIRED['SR1'][:2])
        state = integrity.check('SR1', root)
        self.assertEqual((state['complete'], state['hint_code']), (False, 'missing'))


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def _game(self, root, skip=()):
        files = [f if isinstance(f, str) else f[0] for f in integrity.REQUIRED['SR2']]
        files += [f'{integrity.SR2_BUNDLES}/b{i}.bundle' for i in range(integrity.MIN_SR2_BUNDLES)]
        _make(root, [f for f in files if f not in skip])
        return files

    def test_missing_files_come_back_from_the_original_zip_even_when_nested_twice(self):
        # Downloads/Game.v1/Game.v1/Slime Rancher 2: a zip extracted into a folder of its own name
        outer = self.base / 'Downloads' / 'Game.v1'
        root = outer / 'Game.v1' / 'Slime Rancher 2'
        files = self._game(root, skip={'SlimeRancher2_Data/resources.assets'})
        with zipfile.ZipFile(self.base / 'Downloads' / 'Game.v1.zip', 'w') as zf:
            for f in files:
                zf.writestr(f'Game.v1/Slime Rancher 2/{f}', b'data')
        with mock.patch.object(integrity, 'MIN_ZIP', 0):
            cands = integrity.repair_candidates('SR2', root, [self.base / 'Downloads'])
            self.assertEqual([(Path(c['base']), c['missing']) for c in cands], [(outer, 1)])
            out = integrity.repair('SR2', root, [self.base / 'Downloads'])
        self.assertEqual(out['restored'], 1)
        self.assertTrue(out['state']['complete'])
        self.assertFalse((outer / 'Slime Rancher 2').exists())       # nothing written at the wrong nesting level

    def test_an_unrelated_zip_is_ignored(self):
        root = self.base / 'Games' / 'Game.v1' / 'Slime Rancher 2'
        self._game(root, skip={'SlimeRancher2_Data/resources.assets'})
        with zipfile.ZipFile(self.base / 'Games' / 'Other.zip', 'w') as zf:
            zf.writestr('Other/readme.txt', b'x')
        with mock.patch.object(integrity, 'MIN_ZIP', 0):
            self.assertEqual(integrity.repair_candidates('SR2', root, [self.base / 'Games']), [])

    def test_move_short_renames_on_the_same_drive(self):
        root = self.base / 'deep' / 'Slime Rancher 2'
        self._game(root)
        target = self.base / 'Jeux' / 'Slime Rancher 2'
        with mock.patch.object(integrity, 'short_home', return_value=target):
            new = integrity.move_short('SR2', root)
        self.assertEqual(new, target)
        self.assertTrue((target / 'SlimeRancher2.exe').is_file())
        self.assertFalse(root.exists())


if __name__ == '__main__':
    unittest.main()
