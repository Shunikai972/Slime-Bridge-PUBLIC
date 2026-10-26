import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.launcher import srmp

ROOT = Path(__file__).resolve().parents[1]


def make_game(root: Path, existing: bytes | None = None) -> Path:
    game = root / 'game'
    (game / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
    (game / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').write_bytes(b'srml')
    (game / 'SRML' / 'Mods').mkdir(parents=True)
    if existing is not None:
        (game / 'SRML' / 'Mods' / 'SRMP.dll').write_bytes(existing)
    return game


class SrmpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'build' / 'SRMP.dll'
        self.source.parent.mkdir()
        self.source.write_bytes(b'srmp revival 1652')
        patcher = patch('apps.launcher.srmp.find_source', return_value=self.source)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_install_backs_up_a_different_copy_outside_mods_and_remove_restores_it(self):
        game = make_game(self.base, existing=b'old srmp')
        self.assertEqual(srmp.status(game)['state'], 'outdated')
        result = srmp.install(game)
        self.assertTrue(result['changed'])
        self.assertEqual((game / 'SRML' / 'Mods' / 'SRMP.dll').read_bytes(), b'srmp revival 1652')
        backup = Path(result['backup'])
        self.assertEqual(backup.read_bytes(), b'old srmp')
        self.assertEqual(backup.parent, game / 'SRML' / 'Backups' / 'SRMP')        # never left inside Mods (SRML would load it)
        self.assertEqual(sorted(p.name for p in (game / 'SRML' / 'Mods').iterdir()), ['SRMP.dll', 'SRMP.install.json'])
        record = json.loads((game / 'SRML' / 'Mods' / 'SRMP.install.json').read_text(encoding='utf-8'))
        self.assertEqual((record['FormatVersion'], record['Product'], record['InstalledBy']), (1, 'SRMP Revival', 'SlimeBridge'))
        self.assertEqual(record['PreviousBackup'], str(backup))
        self.assertEqual(srmp.status(game)['state'], 'current')
        self.assertFalse(srmp.install(game)['changed'])                             # idempotent
        removed = srmp.remove(game)
        self.assertEqual(removed['restored'], str(backup))
        self.assertEqual((game / 'SRML' / 'Mods' / 'SRMP.dll').read_bytes(), b'old srmp')
        self.assertFalse((game / 'SRML' / 'Mods' / 'SRMP.install.json').exists())

    def test_reads_the_record_written_by_the_srmp_installer(self):
        game = make_game(self.base, existing=b'srmp revival 1652')
        (game / 'SRML' / 'Mods' / 'SRMP.install.json').write_text(
            '﻿{"FormatVersion": 1, "Product": "SRMP Revival", "InstalledUtc": "2026-09-25T20:47:34Z"}', encoding='utf-8')
        state = srmp.status(game)
        self.assertEqual((state['state'], state['installed_by'], state['installed_utc']),
                         ('current', 'SRMP installer', '2026-09-25T20:47:34Z'))

    def test_refuses_without_srml_and_without_a_build(self):
        game = make_game(self.base)
        (game / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').unlink()
        with self.assertRaises(srmp.SrmpError) as err:
            srmp.install(game)
        self.assertEqual(err.exception.code, 'LOADER_MISSING')
        with patch('apps.launcher.srmp.find_source', return_value=None):
            self.assertEqual(srmp.status(make_game(self.base / 'b'))['state'], 'unavailable')


class SpeciesIdTableTests(unittest.TestCase):
    def test_committed_species_ids_are_unique_stepped_and_paired_with_their_plort(self):
        table = json.loads((ROOT / 'bridge' / 'content' / 'species_ids.json').read_text(encoding='utf-8'))
        for realm in ('SR1', 'SR2'):
            seen = set()
            for canonical, (native, plort) in table[realm].items():
                self.assertTrue(canonical.startswith('slime.'))
                self.assertEqual(native % 10, 0)
                self.assertIn(plort, (0, native + 1))
                self.assertNotIn(native, seen)
                seen.add(native)

    def test_the_spec_builder_takes_the_committed_ids_first(self):
        from tools.content.build_import_spec import committed_species_ids
        self.assertEqual(committed_species_ids('SR1')['slime.ringtail'], (900040, 900041))
        self.assertEqual(committed_species_ids('SR2')['slime.quicksilver'], (900040, 0))


if __name__ == '__main__':
    unittest.main()
