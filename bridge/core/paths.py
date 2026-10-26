"""Relative artifact paths and read-only, link-refusing file hashing (shared Phase 2 helpers)."""

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath

_SEGMENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def safe_relative(rel):
    """True for a canonical relative POSIX path without '..', '.', empty, drive or backslash parts."""
    if not isinstance(rel, str) or not rel or '\\' in rel or ':' in rel or len(rel) > 1024:
        return False
    pure = PurePosixPath(rel)
    return (not pure.is_absolute() and pure.as_posix() == rel and
            all(part not in ('.', '..') and _SEGMENT.fullmatch(part) for part in pure.parts))


def resolve_confined(root, rel):
    """Path for ``rel`` under ``root`` or None if unsafe or any component is a symlink."""
    if not safe_relative(rel):
        return None
    current = Path(root)
    if current.is_symlink():
        return None
    for part in PurePosixPath(rel).parts:
        current = current / part
        if current.is_symlink():
            return None
    return current


def hash_file(path, limit=1024 * 1024 * 1024):
    """Streamed SHA-256 of a regular file opened read-only without following a final link."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('not a regular file')
        digest, size = hashlib.sha256(), 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(chunk)
                if size > limit:
                    raise ValueError('file exceeds size limit')
                digest.update(chunk)
        return digest.hexdigest(), size
    finally:
        os.close(fd)
