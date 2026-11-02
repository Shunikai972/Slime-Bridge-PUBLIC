"""Static path guards and rollback-safe publication for locally owned content caches.

These checks refuse pre-existing links, not concurrent adversarial directory replacement.
Publication uses same-filesystem renames; a refused rename leaves the previous cache active.
"""
from __future__ import annotations

import re
import shutil
import stat
import tempfile
import threading
import uuid
from pathlib import Path

PUBLICATION_LOCK = threading.RLock()
_CACHE_NAME = re.compile(r'[a-z0-9]+(?:[._-][a-z0-9]+)*')


def cache_path(root, direction, canonical) -> Path:
    """A validated cache slot; reject unsafe identifiers rather than normalizing them."""
    if (direction not in ('sr1-to-sr2', 'sr2-to-sr1') or not isinstance(canonical, str) or
            len(canonical) > 127 or not _CACHE_NAME.fullmatch(canonical)):
        raise ValueError('invalid cache direction or canonical identifier')
    return checked_path(Path(root) / direction / canonical)


def checked_path(path) -> Path:
    """An absolute lexical path without relative-parent components or existing links."""
    path = Path(path)
    if '..' in path.parts:
        raise ValueError('relative-parent cache paths are refused')
    path = path.absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError(f'linked cache path is refused: {path}')
    return path


def checked_tree(path) -> Path:
    """Refuse links and non-regular entries before copying, replacing or removing a tree."""
    path = checked_path(path)
    pending = [path]
    while pending:
        current = pending.pop()
        if current.is_symlink() or current.is_junction():
            raise ValueError(f'linked cache entry is refused: {current}')
        if not current.exists():
            continue
        mode = current.stat().st_mode
        if stat.S_ISDIR(mode):
            pending.extend(current.iterdir())
        elif not stat.S_ISREG(mode):
            raise ValueError(f'non-regular cache entry is refused: {current}')
    return path


def publish_directory(source, target, *, backup=None, adapt=None):
    """Copy/adapt privately, then swap; restore the old target on a failed incoming rename.

An explicit backup is retained on success and must not already exist. Otherwise an owned
unique sibling holds the old target until commit, then is removed. Source bytes stay intact.
"""
    with PUBLICATION_LOCK:
        source, target = checked_tree(source), checked_tree(target)
        if not source.is_dir() or (target.exists() and not target.is_dir()):
            raise ValueError('cache publication requires directories')
        if source == target or source in target.parents or target in source.parents:
            raise ValueError('source and target caches overlap')
        previous = (checked_path(backup) if backup is not None else
                    target.with_name(f'.sb-{uuid.uuid4().hex}-previous'))
        if (previous == target or previous in target.parents or target in previous.parents or
                previous == source or previous in source.parents or source in previous.parents):
            raise ValueError('backup overlaps a cache directory')
        if previous.exists():
            raise ValueError('cache backup already exists')
        target.parent.mkdir(parents=True, exist_ok=True)
        previous.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.sb-', suffix='-incoming', dir=target.parent))
        moved_previous = False
        try:
            shutil.copytree(source, staging, dirs_exist_ok=True)
            if adapt is not None:
                adapt(staging)
            checked_tree(staging)
            checked_tree(target)
            checked_path(previous)
            if target.exists():
                target.rename(previous)
                moved_previous = True
            try:
                staging.rename(target)
            except BaseException:
                if moved_previous:
                    previous.rename(target)
                raise
            if moved_previous and backup is None:
                # Cleanup failure retains an old owned backup, never invalidates the new cache.
                shutil.rmtree(previous, ignore_errors=True)
        finally:
            if staging.exists():
                checked_tree(staging)
                shutil.rmtree(staging)
