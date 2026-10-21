"""Real filesystem links for synthetic tests, without requiring Windows elevation."""

import errno
import os
import subprocess
import unittest
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def directory_link(link, target):
    """Create a real junction on Windows or a directory symlink on POSIX; never skip."""
    link, target = Path(link), Path(target).resolve(strict=True)
    if not target.is_dir():
        raise ValueError('directory link target must be a directory')
    if os.name == 'nt':
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        if os.name == 'nt' and not link.is_junction():
            raise AssertionError('Windows directory-link fixture is not a junction')
        if link.resolve(strict=True) != target:
            raise AssertionError('directory-link fixture has the wrong target')
        yield link
    finally:
        # Remove only the link itself, before the enclosing temporary tree is cleaned.
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()


def file_symlink(link, target):
    """Skip only the calling file-link test when the OS lacks symlink capability."""
    try:
        Path(link).symlink_to(target)
    except OSError as exc:
        unsupported = exc.errno in (errno.ENOSYS, errno.EOPNOTSUPP)
        missing_privilege = os.name == 'nt' and getattr(exc, 'winerror', None) == 1314
        if unsupported or missing_privilege:
            raise unittest.SkipTest(f'file symlinks unavailable: {exc}') from exc
        raise
