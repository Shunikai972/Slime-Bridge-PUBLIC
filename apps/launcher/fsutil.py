"""Shared low-level file utilities for the launcher: stat-cached SHA-256 and reparse-point checks.

Deduplicates the identical ``_sha`` / ``_reparse`` helpers that used to live in ``plugins``, ``loaders``,
``online``, ``srmp``, ``sanctuary`` and ``pack``. The SHA-256 cache is keyed on ``(normalized_path, mtime_ns, size)``
so any file write or replacement invalidates automatically with zero staleness risk.
"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import os
from pathlib import Path
import threading

_SHA_CACHE_LIMIT = 512
_SHA_LOCK = threading.Lock()
_SHA_CACHE: OrderedDict[tuple[str, int, int], str] = OrderedDict()


def clear_sha256_cache() -> None:
    """Drop all cached file digests (primarily for tests)."""
    with _SHA_LOCK:
        _SHA_CACHE.clear()


def sha256_file(path: str | os.PathLike) -> str:
    """Return the hex SHA-256 of ``path``, reusing the cached digest when path, mtime_ns and size match."""
    target = Path(path)
    st = target.stat()
    key = (os.path.normcase(os.path.abspath(target)), st.st_mtime_ns, st.st_size)
    with _SHA_LOCK:
        cached = _SHA_CACHE.get(key)
        if cached is not None:
            _SHA_CACHE.move_to_end(key)
            return cached
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    value = digest.hexdigest()
    try:
        st_after = target.stat()
    except OSError:
        return value
    if st_after.st_mtime_ns == st.st_mtime_ns and st_after.st_size == st.st_size:
        with _SHA_LOCK:
            _SHA_CACHE[key] = value
            _SHA_CACHE.move_to_end(key)
            while len(_SHA_CACHE) > _SHA_CACHE_LIMIT:
                _SHA_CACHE.popitem(last=False)
    return value


def is_reparse(path: str | os.PathLike) -> bool:
    """True when ``path`` is a symlink, Windows junction, or other NTFS reparse point (False if missing)."""
    target = Path(path)
    if target.is_symlink() or (hasattr(target, 'is_junction') and target.is_junction()):
        return True
    try:
        return bool(getattr(target.lstat(), 'st_file_attributes', 0) & 0x400)
    except OSError:
        return False
