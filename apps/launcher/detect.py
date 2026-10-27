"""Automatic discovery of the player's own Slime Rancher installs (read-only).

Order of evidence: Steam (registry + every library folder, any drive), Epic manifests, GOG and plain game
folders in the usual places on every fixed drive. Every candidate is re-verified in place with
``discover_manual`` (executable + Unity runtime files), so a wrong guess can never be reported as a game.
Nothing is written, nothing is launched, no network is used.
"""

import os
import string
from pathlib import Path

from apps.launcher.installations import GameInstallation, discover_manual, discover_stores

_NAMES = ('Slime Rancher', 'Slime Rancher 2', 'SlimeRancher', 'SlimeRancher2', 'Slime.Rancher', 'Slime.Rancher.2')
_STEAM_REGISTRY = (('HKEY_CURRENT_USER', r'Software\Valve\Steam', 'SteamPath'),
                   ('HKEY_LOCAL_MACHINE', r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
                   ('HKEY_LOCAL_MACHINE', r'SOFTWARE\Valve\Steam', 'InstallPath'))
_SHELL_FOLDERS = ('Desktop', 'Personal', '{374DE290-123F-4565-9164-39C4925E467B}', '{7D83EE9B-2244-4E70-B1F5-5393042AF1E4}')
_SKIP_DRIVE_DIRS = {'windows', 'program files', 'program files (x86)', 'programdata', '$recycle.bin',
                    'system volume information', 'recovery', 'perflogs', 'msocache'}


def fixed_drives():
    """Existing drive roots (``C:\\`` ...); on non-Windows just the filesystem root."""
    if os.name != 'nt':
        return [Path('/')]
    return [Path(f'{letter}:\\') for letter in string.ascii_uppercase if Path(f'{letter}:\\').exists()]


def shell_folders(registry=True):
    """Windows shell folders (Downloads, Desktop, Documents) and sibling game folders even when redirected."""
    out = []
    if registry and os.name == 'nt':
        try:
            import winreg
            for sub in (r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders',
                        r'Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'):
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as handle:
                        for name in _SHELL_FOLDERS:
                            try:
                                raw = winreg.QueryValueEx(handle, name)[0]
                                folder = Path(os.path.expandvars(str(raw)))
                                out.append(folder)
                                for sib in ('Games', 'Jeux', 'Downloads', 'Desktop', 'Bureau'):
                                    out.append(folder.parent / sib)
                            except OSError:
                                continue
                except OSError:
                    continue
        except ImportError:  # pragma: no cover - non-Windows
            pass
    return out


def steam_roots(registry=True, drives=None):
    roots = []
    if registry and os.name == 'nt':
        try:
            import winreg
            for hive, key, value in _STEAM_REGISTRY:
                try:
                    with winreg.OpenKey(getattr(winreg, hive), key) as handle:
                        roots.append(Path(winreg.QueryValueEx(handle, value)[0]))
                except OSError:
                    continue
        except ImportError:  # pragma: no cover - non-Windows
            pass
    for drive in drives if drives is not None else fixed_drives():
        for rel in ('Program Files (x86)/Steam', 'Program Files/Steam', 'Steam', 'SteamLibrary'):
            roots.append(drive / rel)
    if os.name != 'nt' and drives is None:
        home = Path.home()
        roots += [home / '.steam' / 'steam', home / '.local' / 'share' / 'Steam',
                  home / '.var' / 'app' / 'com.valvesoftware.Steam' / '.local' / 'share' / 'Steam']
    out, seen = [], set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen and root.is_dir():
            seen.add(key)
            out.append(root)
    return out


def _child_priority(path: Path):
    norm = path.name.casefold().replace(' ', '').replace('.', '').replace('_', '').replace('-', '')
    return (0 if 'slimerancher' in norm else (1 if 'slime' in norm else 2), path.name.casefold())


def folder_candidates(drives=None, registry=True):
    """Game folders in the usual non-store places (GOG, Epic custom paths, user/plain folders), shallow only."""
    bases = []
    for drive in drives if drives is not None else fixed_drives():
        for rel in ('GOG Games', 'Program Files (x86)/GOG Galaxy/Games', 'Program Files/GOG Galaxy/Games',
                    'Epic Games', 'Program Files/Epic Games', 'Games', 'Jeux', 'Downloads', 'Desktop', 'Bureau',
                    'XboxGames', ''):
            bases.append(drive / rel)
        if drive.is_dir():
            try:
                for top in drive.iterdir():
                    if top.is_dir() and not top.name.startswith(('$', '.')) and top.name.casefold() not in _SKIP_DRIVE_DIRS:
                        for sub in ('Games', 'Jeux', 'Downloads', 'Desktop', 'Bureau'):
                            bases.append(top / sub)
            except OSError:
                pass
    if drives is None:
        home = Path.home()
        bases += [home / 'Games', home / 'Jeux', home / 'Desktop', home / 'Bureau', home / 'Downloads']
        bases += shell_folders(registry=registry)
    out, seen_bases = [], set()
    name_tokens = {n.casefold().replace(' ', '').replace('.', '') for n in _NAMES}
    for base in bases:
        key = str(base).casefold()
        if key in seen_bases or not base.is_dir():
            continue
        seen_bases.add(key)
        try:
            children = [c for c in base.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in sorted(children, key=_child_priority)[:600]:
            lowered = child.name.casefold().replace(' ', '').replace('.', '').replace('_', '')
            if 'slimerancher' in lowered or lowered in name_tokens:
                out.append(child)
                try:  # wrapper folders ("Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4/...") one or two levels deep
                    for sub in sorted((s for s in child.iterdir() if s.is_dir()), key=_child_priority)[:30]:
                        out.append(sub)
                        out += sorted((s for s in sub.iterdir() if s.is_dir()), key=_child_priority)[:30]
                except OSError:
                    continue
    return out


def store_label(path, found_store):
    text = str(path).casefold()
    if found_store not in ('manual', None):
        return found_store
    if 'gog' in text:
        return 'gog'
    if 'epic' in text:
        return 'epic'
    if 'steam' in text:
        return 'steam'
    return 'manual'


def auto_detect(drives=None, registry=True, epic_manifest_dir=None):
    """Every verified SR1/SR2 install found automatically, de-duplicated, stable order."""
    found = {}
    roots = steam_roots(registry, drives)
    kwargs = {'steam_roots': roots}
    if epic_manifest_dir is not None:
        kwargs['epic_manifest_dir'] = Path(epic_manifest_dir)
    for item in discover_stores(**kwargs):
        found[(item.game, item.root)] = item
    for candidate in folder_candidates(drives, registry=registry):
        item = resolve_install(candidate, max_depth=3, store='manual')
        if item is not None and (item.game, item.root) not in found:
            found[(item.game, item.root)] = GameInstallation(item.game, item.root, item.executable,
                                                             store_label(item.root, item.store), item.unity_runtime)
    return sorted(found.values(), key=lambda i: (i.game, i.store, str(i.root)))


def resolve_install(path, max_depth=3, store='manual'):
    """Install for ``path`` or for a game folder nested up to ``max_depth`` levels below it (wrapper folders such as
    ``Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4``), or enclosing ``path`` when a subfolder
    inside the game directory (e.g. ``SlimeRancher2_Data``) was selected. Breadth-first, bounded, links not followed."""
    start = Path(path).expanduser()
    item = discover_manual(start, store=store)
    if item is not None or not start.is_dir():
        return item
    for parent in list(start.parents)[:2]:
        item = discover_manual(parent, store=store)
        if item is not None:
            return item
    level, visited = [start], 0
    for _ in range(max_depth):
        nxt = []
        for folder in level:
            try:
                children = sorted(
                    (c for c in folder.iterdir() if c.is_dir() and not c.is_symlink() and not c.is_junction()),
                    key=_child_priority,
                )
            except OSError:
                continue
            for child in children:
                visited += 1
                if visited > 2000:
                    return None
                item = discover_manual(child, store=store)
                if item is not None:
                    return item
                nxt.append(child)
        level = nxt
    return None
