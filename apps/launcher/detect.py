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


def fixed_drives():
    """Existing drive roots (``C:\\`` ...); on non-Windows just the filesystem root."""
    if os.name != 'nt':
        return [Path('/')]
    return [Path(f'{letter}:\\') for letter in string.ascii_uppercase if Path(f'{letter}:\\').exists()]


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
    out, seen = [], set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen and root.is_dir():
            seen.add(key)
            out.append(root)
    return out


def folder_candidates(drives=None):
    """Game folders in the usual non-store places (GOG, Epic custom paths, plain folders), shallow only."""
    bases = []
    for drive in drives if drives is not None else fixed_drives():
        for rel in ('GOG Games', 'Program Files (x86)/GOG Galaxy/Games', 'Program Files/GOG Galaxy/Games',
                    'Epic Games', 'Program Files/Epic Games', 'Games', 'Jeux', 'XboxGames', ''):
            bases.append(drive / rel)
    home = Path.home()
    bases += [home / 'Games', home / 'Desktop', home / 'Downloads']
    out = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            children = [c for c in base.iterdir() if c.is_dir()]
        except OSError:
            continue
        for child in children[:400]:
            lowered = child.name.casefold().replace(' ', '').replace('.', '').replace('_', '')
            if 'slimerancher' in lowered or lowered in {n.casefold().replace(' ', '').replace('.', '') for n in _NAMES}:
                out.append(child)
                try:  # wrapper folders ("Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4/...") one or two levels deep
                    for sub in [s for s in child.iterdir() if s.is_dir()][:20]:
                        out.append(sub)
                        out += [s for s in sub.iterdir() if s.is_dir()][:20]
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
    for candidate in folder_candidates(drives):
        item = discover_manual(candidate, store='manual')
        if item is not None and (item.game, item.root) not in found:
            found[(item.game, item.root)] = GameInstallation(item.game, item.root, item.executable,
                                                             store_label(item.root, item.store), item.unity_runtime)
    return sorted(found.values(), key=lambda i: (i.game, i.store, str(i.root)))


def resolve_install(path, max_depth=3, store='manual'):
    """Install for ``path`` or for a game folder nested up to ``max_depth`` levels below it (wrapper folders such as
    ``Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4/Slime.Rancher.v1.4.4``). Breadth-first, bounded, links not followed."""
    start = Path(path).expanduser()
    item = discover_manual(start, store=store)
    if item is not None or not start.is_dir():
        return item
    level, visited = [start], 0
    for _ in range(max_depth):
        nxt = []
        for folder in level:
            try:
                children = sorted(c for c in folder.iterdir() if c.is_dir() and not c.is_symlink() and not c.is_junction())
            except OSError:
                continue
            for child in children:
                visited += 1
                if visited > 400:
                    return None
                item = discover_manual(child, store=store)
                if item is not None:
                    return item
                nxt.append(child)
        level = nxt
    return None
