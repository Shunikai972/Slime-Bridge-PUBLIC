"""Test link fixtures use real filesystem redirection and narrowly scoped skips."""

import errno
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from portability import directory_link, file_symlink


class PortabilityTests(unittest.TestCase):
    def test_directory_link_redirects_and_cleanup_preserves_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target, link = root / 'real target', root / 'directory link'
            target.mkdir()
            sentinel = target / 'keep.txt'
            sentinel.write_text('synthetic target')
            with directory_link(link, target):
                self.assertEqual(link.resolve(strict=True), target.resolve(strict=True))
                self.assertEqual((link / sentinel.name).read_text(), 'synthetic target')
                if os.name == 'nt':
                    self.assertTrue(link.is_junction())
                    self.assertFalse(link.is_symlink())
                else:
                    self.assertTrue(link.is_symlink())
            self.assertFalse(link.exists())
            self.assertEqual(sentinel.read_text(), 'synthetic target')

    def test_only_unavailable_file_symlink_capability_is_skipped(self):
        missing_privilege = OSError('symlink privilege unavailable')
        missing_privilege.winerror = 1314
        for error in (missing_privilege, OSError(errno.ENOSYS, 'unsupported'),
                      OSError(errno.EOPNOTSUPP, 'unsupported')):
            with self.subTest(error=error):
                with patch('portability.os', SimpleNamespace(name='nt')):
                    with patch.object(Path, 'symlink_to', side_effect=error):
                        with self.assertRaises(unittest.SkipTest):
                            file_symlink(Path('unused-link'), Path('unused-target'))

    def test_unexpected_file_symlink_errors_are_not_skipped(self):
        for error in (FileNotFoundError(errno.ENOENT, 'bad fixture'),
                      PermissionError(errno.EACCES, 'unexpected access failure'),
                      OSError(errno.EIO, 'I/O failure')):
            with self.subTest(error=error):
                with patch.object(Path, 'symlink_to', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        file_symlink(Path('unused-link'), Path('unused-target'))
                    self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
