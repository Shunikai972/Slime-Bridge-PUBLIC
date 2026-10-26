"""Fail-closed path boundary for game labs; never follow Windows junctions into source installs."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LAB_GAMES = REPO_ROOT / 'Lab' / 'Games'


def lexical_path(path: Path) -> Path:
    """Normalize dots without silently following any filesystem reparse point."""
    return Path(os.path.abspath(os.fspath(path)))


def refuse_reparse(path: Path) -> None:
    """Reject links and junctions in existing ancestors as well as in the target itself."""
    path = lexical_path(path)
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f'Reparse point in lab path: {component}')


def validate_spec_destination(path: Path, spec_root: Path) -> Path:
    """Containment + reparse validation for Bridge-owned spec documents.

    All checks run on LEXICAL paths BEFORE resolution: a junction on the root, an ancestor or the
    candidate itself would disappear behind resolve() and let the containment check pass while the
    real target lives outside the intended root.
    """
    root = lexical_path(spec_root)
    target = lexical_path(path)
    if root not in target.parents:
        raise ValueError(f'Refusing spec path outside {root}: {target}')
    refuse_reparse(target)
    if root.resolve() not in target.resolve().parents:
        raise ValueError(f'Refusing spec path that escapes its root via links: {target}')
    return target.resolve()


def validate_lab_destination(destination: Path, lab_root: Path = LAB_GAMES) -> Path:
    root = lexical_path(lab_root)
    target = lexical_path(destination)
    if root not in target.parents:
        raise ValueError(f'Refusing path outside Lab/Games: {target}')
    refuse_reparse(target)
    # A non-link reparse or mount point must not silently redirect the path either.
    if root.resolve() not in target.resolve().parents:
        raise ValueError(f'Lab destination escapes its root: {target}')
    return target
