"""Launcher audit regressions: synthetic installs only; no game/plugin execution."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from apps.launcher import modmanager as mm


class ModOwnershipRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mod-ownership-')
        self.addCleanup(self.tmp.cleanup)
        self.top = Path(self.tmp.name)
        real_move = mm.shutil.move

        def confined_probe_move(source, destination, *args, **kwargs):
            # A broken ledger consumer must not turn a negative test into a real root operation.
            for value in (source, destination):
                if not Path(value).resolve().is_relative_to(self.top.resolve()):
                    raise AssertionError('probe refused a move outside its synthetic fixture')
            return real_move(source, destination, *args, **kwargs)

        moving = patch.object(mm.shutil, 'move', side_effect=confined_probe_move)
        moving.start()
        self.addCleanup(moving.stop)
        self.root = self.top / 'game'
        (self.root / 'Mods').mkdir(parents=True)
        self.protected = self.root / 'Mods/SlimeBridge.SR2.dll'
        self.protected.write_bytes(b'synthetic bridge original')

    def archive(self, entries):
        path = self.top / 'download.zip'
        with zipfile.ZipFile(path, 'w') as zf:
            for name, value in entries:
                zf.writestr(name, value)
        return path

    def record(self, files):
        path = self.root / 'SlimeBridge/mods.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'SR2': {'bad': {'name': 'bad', 'files': files}}}), encoding='utf8')

    def test_direct_dll_case_alias_cannot_replace_a_protected_plugin(self):
        source = self.top / 'slimebridge.sr2.dll'
        source.write_bytes(b'untrusted replacement')
        with self.assertRaises(mm.ModError):
            mm.install_file('SR2', self.root, source)
        self.assertEqual(self.protected.read_bytes(), b'synthetic bridge original')

    def test_entire_zip_plan_is_checked_before_any_member_is_written(self):
        source = self.archive([('Mods/ordinary.dll', b'ordinary'),
                               ('Mods/slimebridge.sr2.dll', b'replacement')])
        with self.assertRaises(mm.ModError):
            mm.install_file('SR2', self.root, source)
        self.assertFalse((self.root / 'Mods/ordinary.dll').exists())
        self.assertEqual(self.protected.read_bytes(), b'synthetic bridge original')

    def test_sr1_protected_directory_is_not_bypassed_by_nested_payloads(self):
        target = self.root / 'SRML/Mods/SlimeBridge/custom.dll'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'protected subtree original')
        source = self.archive([('SRML/Mods/slimebridge/custom.dll', b'replacement')])
        with self.assertRaises(mm.ModError):
            mm.install_file('SR1', self.root, source)
        self.assertEqual(target.read_bytes(), b'protected subtree original')

    def test_tainted_removal_ledger_does_not_move_a_neighbour_file(self):
        outside = self.top / 'outside.txt'
        outside.write_bytes(b'not owned by this game')
        self.record(['../outside.txt'])
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'bad')
        self.assertEqual(outside.read_bytes(), b'not owned by this game')

    def test_tainted_ledger_cannot_claim_a_protected_case_alias(self):
        self.record(['Mods/slimebridge.sr2.dll'])
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'bad')
        self.assertEqual(self.protected.read_bytes(), b'synthetic bridge original')

    def test_every_ledger_path_is_validated_before_any_move(self):
        ordinary = self.root / 'Mods/ordinary.dll'
        ordinary.write_bytes(b'ordinary original')
        outside = self.top / 'outside.txt'
        outside.write_bytes(b'not owned')
        self.record(['Mods/ordinary.dll', '../outside.txt'])
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'bad')
        self.assertEqual(ordinary.read_bytes(), b'ordinary original')
        self.assertEqual(outside.read_bytes(), b'not owned')

    def test_ledger_cannot_claim_game_executables_outside_loader_mod_folders(self):
        executable = self.root / 'SlimeRancher2.exe'
        executable.write_bytes(b'synthetic game marker')
        self.record(['SlimeRancher2.exe'])
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'bad')
        self.assertEqual(executable.read_bytes(), b'synthetic game marker')

    def test_malformed_ledger_files_shape_is_refused_without_attribute_errors(self):
        (self.root / 'Mods/ordinary.dll').write_bytes(b'ordinary original')
        self.record({'Mods/ordinary.dll': None})
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'bad')


    def test_directory_junction_is_refused_without_writing_the_target(self):
        from portability import directory_link
        outside = self.top / 'outside mods'
        outside.mkdir()
        link = self.root / 'Mods/redirect'
        source = self.archive([('Mods/redirect/payload.dll', b'not permitted')])
        with directory_link(link, outside):
            with self.assertRaises(mm.ModError):
                mm.install_file('SR2', self.root, source)
            self.assertEqual(list(outside.iterdir()), [])

    def test_archive_case_collisions_are_rejected_before_overwriting(self):
        source = self.archive([('Mods/item.dll', b'a'), ('Mods/ITEM.dll', b'b')])
        with self.assertRaises(mm.ModError):
            mm.install_file('SR2', self.root, source)
        self.assertFalse((self.root / 'Mods/item.dll').exists())


if __name__ == '__main__':
    unittest.main()
