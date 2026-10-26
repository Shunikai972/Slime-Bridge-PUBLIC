import os
import sys
import tempfile
import unittest
from pathlib import Path

from apps.launcher import exe_main


@unittest.skipUnless(os.name == 'nt', 'Zone.Identifier streams exist on NTFS only')
class DownloadMarkTests(unittest.TestCase):
    def test_marks_on_binaries_are_removed_and_other_files_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'pythonnet' / 'runtime').mkdir(parents=True)
            dll = root / 'pythonnet' / 'runtime' / 'Python.Runtime.dll'
            txt = root / 'README.md'
            for f in (dll, txt):
                f.write_bytes(b'x')
                with open(str(f) + exe_main.ZONE_STREAM, 'w') as s:
                    s.write('[ZoneTransfer]\nZoneId=3\n')
            self.assertEqual(exe_main.clear_download_mark(root), 1)
            self.assertFalse(os.path.exists(str(dll) + exe_main.ZONE_STREAM))
            self.assertTrue(os.path.exists(str(txt) + exe_main.ZONE_STREAM))
            self.assertEqual(dll.read_bytes(), b'x')

    def test_unmarked_tree_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.dll').write_bytes(b'x')
            self.assertEqual(exe_main.clear_download_mark(tmp), 0)


if __name__ == '__main__':
    unittest.main()
