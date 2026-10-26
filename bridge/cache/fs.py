"""Link-refusing, atomic file output confined to a root (used by the cache and the content CLI)."""

import os
import uuid
from pathlib import Path


def confined_root(path) -> Path:
    """Absolute root path whose existing components are not symlinks or junctions."""
    root = Path(os.path.abspath(path))
    for ancestor in (root, *root.parents):
        if ancestor.is_symlink() or ancestor.is_junction():
            raise ValueError(f'Output path traverses a symlink or junction: {ancestor}')
    return root


def _relative_parts(root: Path, path: Path):
    """Components of ``path`` below ``root``; '..'/'.' are refused (relative_to is only lexical)."""
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        raise ValueError(f'Path outside output root: {path}') from None
    if any(part in ('..', '.', '') for part in parts):
        raise ValueError(f'Path with relative components refused: {path}')
    return parts


def refuse_links(root: Path, path: Path):
    """Refuse a symlink or junction anywhere from ``root`` down to ``path`` (which must be inside root)."""
    if root.is_symlink() or root.is_junction():
        raise ValueError(f'Root is a symlink or junction: {root}')
    current = root
    for part in _relative_parts(root, path):
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError(f'Symlink or junction inside output root: {current}')


def ensure_dir(root: Path, directory: Path):
    """Create directories one component at a time so a planted link is never followed."""
    parts = _relative_parts(root, directory)
    root.mkdir(parents=True, exist_ok=True)
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError(f'Symlink or junction inside output root: {current}')
        if not current.exists():
            current.mkdir()
        elif not current.is_dir():
            raise ValueError(f'Not a directory inside output root: {current}')


def atomic_write(root: Path, path: Path, data: bytes):
    """Exclusive temp + fsync + atomic replace; refuses links and hardlinked targets."""
    _relative_parts(root, path)
    ensure_dir(root, path.parent)
    refuse_links(root, path)
    if path.exists() and path.stat().st_nlink > 1:
        raise ValueError(f'Refusing to replace a hardlinked file: {path}')
    tmp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    created = False
    try:
        with open(tmp, 'xb') as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        refuse_links(root, path)
        os.replace(tmp, path)
    finally:
        if created and tmp.exists():
            tmp.unlink()
