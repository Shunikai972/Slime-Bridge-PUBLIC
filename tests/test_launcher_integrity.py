import tempfile
import unittest
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


if __name__ == '__main__':
    unittest.main()
