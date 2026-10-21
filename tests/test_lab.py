import tempfile
import unittest
from pathlib import Path

from apps.launcher.installations import discover_manual
from tools.lab.copy_game import create_lab_copy


class LabCopyTests(unittest.TestCase):
    def test_copy_independent_files_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            dest = base / 'lab' / 'Games' / 'SR1'
            (source / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            (source / 'UnityPlayer.dll').write_bytes(b'unity')
            (source / 'SlimeRancher.exe').write_bytes(b'executable')
            (source / 'SlimeRancher_Data' / 'Managed' / 'Assembly-CSharp.dll').write_bytes(b'assembly')
            manifest = create_lab_copy(discover_manual(source), dest, lab_root=base / 'lab' / 'Games')
            self.assertEqual(manifest['game'], 'SR1')
            self.assertEqual((dest / 'SlimeRancher.exe').read_bytes(), b'executable')
            (dest / 'SlimeRancher.exe').write_bytes(b'modified')
            self.assertEqual((source / 'SlimeRancher.exe').read_bytes(), b'executable')
            with self.assertRaises(FileExistsError):
                create_lab_copy(discover_manual(source), dest, lab_root=base / 'lab' / 'Games')

    def test_source_changing_during_copy_is_not_promoted(self):
        from unittest.mock import patch
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            dest = base / 'lab' / 'Games' / 'SR1'
            (source / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                (source / name).write_bytes(b'v1')
            real = shutil.copytree
            # shutil.copytree recurses through its own module global; keep the mock out of the way
            # by copying with the real function, then simulating a game update of the source.
            def copy_then_update(src, dst, **kw):
                with patch('shutil.copytree', real):
                    out = real(src, dst, **kw)
                (Path(src) / 'SlimeRancher_Data' / 'Managed' / 'Assembly-CSharp.dll').write_bytes(b'v2')
                return out
            with patch('tools.lab.copy_game.shutil.copytree', side_effect=copy_then_update):
                with self.assertRaisesRegex(RuntimeError, 'changed during copy'):
                    create_lab_copy(discover_manual(source), dest, lab_root=base / 'lab' / 'Games')
            self.assertFalse(dest.exists())

    def test_copy_refuses_new_folder_inside_other_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'original-game'
            other = base / 'other-original-game'
            for root in (source, other):
                (root / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
                for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                    (root / name).touch()
            forbidden = other / 'Lab' / 'Games' / 'SR1'
            with self.assertRaises(ValueError):
                create_lab_copy(discover_manual(source), forbidden, lab_root=base / 'lab' / 'Games')
            self.assertFalse(forbidden.exists())

    def test_copy_refuses_junction_inside_source(self):
        import subprocess
        import sys
        if sys.platform != 'win32':
            self.skipTest('Windows junction test')
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / 'source'
            (source / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            for name in ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                (source / name).touch()
            outside = base / 'outside'
            outside.mkdir()
            (outside / 'unrelated.txt').write_text('private')
            junction = source / 'nested-junction'
            subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], check=True, capture_output=True)
            try:
                dest = base / 'lab' / 'Games' / 'SR1'
                with self.assertRaises(ValueError):
                    create_lab_copy(discover_manual(source), dest, lab_root=base / 'lab' / 'Games')
                self.assertFalse(dest.exists())
                self.assertFalse(dest.with_name('SR1.partial').exists())
            finally:
                junction.rmdir()

    def test_lab_inside_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'SlimeRancher_Data' / 'Managed').mkdir(parents=True)
            for name in ('UnityPlayer.dll', 'SlimeRancher.exe', 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'):
                (source / name).touch()
            with self.assertRaises(ValueError):
                create_lab_copy(discover_manual(source), source / 'Lab')


if __name__ == '__main__':
    unittest.main()
