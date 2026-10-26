"""Install / update / remove the SlimeBridge plugin of a game (SRML mod for SR1, MelonLoader mod for SR2).

Only the plugin's own files are written, only inside the loader's mod folder, never through a link, never while the game is running
(the caller checks) and only when the mod loader itself is already present (the launcher does not install loaders).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FILES = {
    'SR1': ('SlimeBridge.SR1.dll', 'SlimeBridge.SR1.pdb', 'modinfo.json'),
    'SR2': ('SlimeBridge.SR2.dll', 'SlimeBridge.SR2.pdb'),
}
REQUIRED = {'SR1': ('SlimeBridge.SR1.dll', 'modinfo.json'), 'SR2': ('SlimeBridge.SR2.dll',)}


class PluginError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def source_dirs(realm, repo_root=None):
    """Where a built plugin can come from: a packaged ``plugins/<realm>`` folder first, else the local build output."""
    root = Path(repo_root or REPO_ROOT)
    build = {'SR1': root / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472', 'SR2': root / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'}[realm]
    return [root / 'plugins' / realm, build]


def find_source(realm, repo_root=None):
    for folder in source_dirs(realm, repo_root):
        if all((folder / name).is_file() for name in REQUIRED[realm]):
            return folder
    return None


def destination(realm, game_root):
    root = Path(game_root)
    return root / 'SRML' / 'Mods' / 'SlimeBridge' if realm == 'SR1' else root / 'Mods'


def loader_ready(realm, game_root):
    root = Path(game_root)
    if realm == 'SR1':
        return (root / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').is_file()
    return (root / 'version.dll').is_file() and (root / 'MelonLoader' / 'net6' / 'MelonLoader.dll').is_file()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _reparse(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def status(realm, game_root, repo_root=None):
    """``missing`` | ``current`` | ``outdated`` | ``unavailable`` (no built plugin to install) plus whether the loader is there."""
    source = find_source(realm, repo_root)
    dest = destination(realm, game_root)
    main = FILES[realm][0]
    state = 'unavailable' if source is None else 'missing'
    if (dest / main).is_file():
        state = 'current' if source is not None and _sha(dest / main) == _sha(source / main) else ('outdated' if source is not None else 'current')
    return {'state': state, 'loader': loader_ready(realm, game_root), 'source': str(source) if source else None}


def install(realm, game_root, repo_root=None):
    if realm not in FILES:
        raise PluginError('UNKNOWN_REALM')
    if not loader_ready(realm, game_root):
        raise PluginError('LOADER_MISSING', 'SRML' if realm == 'SR1' else 'MelonLoader')
    source = find_source(realm, repo_root)
    if source is None:
        raise PluginError('NO_BUILT_PLUGIN')
    dest = destination(realm, game_root)
    for part in (Path(game_root), *[p for p in dest.parents if Path(game_root) in p.parents], dest):
        if part.exists() and _reparse(part):
            raise PluginError('LINK_REFUSED', str(part))
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in FILES[realm]:
        src = source / name
        if not src.is_file():
            continue
        target = dest / name
        if target.exists() and _reparse(target):
            raise PluginError('LINK_REFUSED', str(target))
        temp = dest / f'.{name}.{uuid.uuid4().hex}.installing'
        try:
            temp.write_bytes(src.read_bytes())
            if _sha(temp) != _sha(src):
                raise PluginError('COPY_VERIFY_FAILED', name)
            os.replace(temp, target)
        except PermissionError as exc:
            raise PluginError('FILE_IN_USE', name) from exc
        finally:
            if temp.exists():
                temp.unlink()
        copied.append(name)
    return {'ok': True, 'folder': str(dest), 'files': copied}


def remove(realm, game_root):
    dest = destination(realm, game_root)
    removed = []
    for name in FILES[realm]:
        target = dest / name
        if target.is_file() and not _reparse(target):
            try:
                target.unlink()
                removed.append(name)
            except PermissionError as exc:
                raise PluginError('FILE_IN_USE', name) from exc
    from apps.launcher.pack import remove_player_pointer
    if remove_player_pointer(game_root):
        removed.append('SlimeBridge/content.json')
    if realm == 'SR1' and dest.is_dir() and not any(dest.iterdir()):
        dest.rmdir()
    return {'ok': True, 'removed': removed}
