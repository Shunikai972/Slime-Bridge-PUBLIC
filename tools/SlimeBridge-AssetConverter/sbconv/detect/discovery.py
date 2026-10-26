"""Locate Slime Rancher 1/2 installs: explicit paths, Steam, GOG, Epic and user-supplied search roots.

Discovery only *reads* directory listings, registry keys and launcher manifests. It never touches
game contents beyond checking that the expected executable exists.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

GAMES = {
    'SR1': {'exe': 'SlimeRancher.exe', 'data': 'SlimeRancher_Data', 'steam_appid': '433340',
            'steam_dirs': ('Slime Rancher',)},
    'SR2': {'exe': 'SlimeRancher2.exe', 'data': 'SlimeRancher2_Data', 'steam_appid': '1657630',
            'steam_dirs': ('Slime Rancher 2',)},
}


@dataclass
class Candidate:
    game: str
    root: str
    source: str  # explicit | steam | gog | epic | search-root

    def key(self):
        return (self.game, os.path.normcase(os.path.realpath(self.root)))


def _is_root(path, game):
    spec = GAMES[game]
    return (Path(path) / spec['exe']).is_file() and (Path(path) / spec['data']).is_dir()


def find_game_root(path, game, max_depth=5):
    """Return the real game root at/below ``path`` (it may be a wrapper folder, e.g. an archive
    extraction directory holding the install several levels down), or None."""
    base = Path(path)
    if not base.is_dir():
        return None
    if _is_root(base, game):
        return str(base)
    frontier = [base]
    for _ in range(max_depth):
        nxt = []
        for d in frontier:
            try:
                kids = [k for k in d.iterdir() if k.is_dir()]
            except OSError:
                continue
            for k in kids:
                if _is_root(k, game):
                    return str(k)
                nxt.append(k)
        frontier = nxt
    return None


def detect_game_at(path):
    """Identify which game (if any) lives at/below ``path``."""
    for game in GAMES:
        root = find_game_root(path, game)
        if root:
            return game, root
    return None, None


def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, sub, val in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                               (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    roots.append(winreg.QueryValueEx(k, val)[0])
            except OSError:
                pass
    except ImportError:
        pass
    roots += [r'C:\Program Files (x86)\Steam', r'C:\Program Files\Steam']
    return [r for r in dict.fromkeys(os.path.normpath(r) for r in roots) if os.path.isdir(r)]


def steam_libraries():
    libs = []
    for steam in _steam_roots():
        libs.append(steam)
        vdf = Path(steam) / 'steamapps' / 'libraryfolders.vdf'
        try:
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding='utf-8', errors='replace')):
                libs.append(m.group(1).replace('\\\\', '\\'))
        except OSError:
            pass
    # libraryfolders.vdf can be stale: also probe conventional library folders on every drive
    for letter in 'CDEFGH':
        for name in ('SteamLibrary', 'Steam', r'Games\Steam'):
            p = f'{letter}:\\{name}'
            if os.path.isdir(os.path.join(p, 'steamapps')):
                libs.append(p)
    return list(dict.fromkeys(os.path.normpath(l) for l in libs if os.path.isdir(l)))


def _steam_candidates():
    for lib in steam_libraries():
        apps = Path(lib) / 'steamapps'
        for game, spec in GAMES.items():
            names = list(spec['steam_dirs'])
            mf = apps / f"appmanifest_{spec['steam_appid']}.acf"
            try:
                m = re.search(r'"installdir"\s+"([^"]+)"', mf.read_text(encoding='utf-8', errors='replace'))
                if m:
                    names.insert(0, m.group(1))
            except OSError:
                pass
            for n in names:
                root = find_game_root(apps / 'common' / n, game, max_depth=1)
                if root:
                    yield Candidate(game, root, 'steam')


def _gog_candidates():
    try:
        import winreg
    except ImportError:
        return
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\GOG.com\Games', 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with base:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(base, sub) as k:
                        path = winreg.QueryValueEx(k, 'path')[0]
                except OSError:
                    continue
                game, root = detect_game_at(path)
                if game:
                    yield Candidate(game, root, 'gog')


def _epic_candidates():
    manifests = Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests'
    try:
        items = list(manifests.glob('*.item'))
    except OSError:
        return
    for item in items:
        try:
            data = json.loads(item.read_text(encoding='utf-8', errors='replace'))
        except (OSError, ValueError):
            continue
        loc = data.get('InstallLocation')
        if loc and 'slime' in (data.get('DisplayName', '') + loc).lower():
            game, root = detect_game_at(loc)
            if game:
                yield Candidate(game, root, 'epic')


def default_search_roots():
    roots = [p for p in os.environ.get('SBCONV_SEARCH_PATHS', '').split(os.pathsep) if p]
    home = Path.home()
    roots += [str(home / sub) for sub in ('Desktop', 'Downloads', 'Games')]
    return roots


def _search_root_candidates(roots, max_depth=6):
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            depth = len(Path(dirpath).relative_to(base).parts)
            if depth >= max_depth:
                dirnames[:] = []
            for game, spec in GAMES.items():
                if spec['exe'] in filenames and (Path(dirpath) / spec['data']).is_dir():
                    yield Candidate(game, dirpath, 'search-root')


def discover(extra_roots=(), explicit=None, deep=False):
    """Return {game: [Candidate...]} (deduplicated). ``explicit`` = {game: path}."""
    found = {g: [] for g in GAMES}
    seen = set()

    def add(c):
        if c.key() not in seen:
            seen.add(c.key())
            found[c.game].append(c)

    for game, path in (explicit or {}).items():
        if path:
            root = find_game_root(path, game)
            if root:
                add(Candidate(game, root, 'explicit'))
    for gen in (_steam_candidates, _gog_candidates, _epic_candidates):
        for c in gen():
            add(c)
    roots = list(extra_roots)
    if deep:
        roots += default_search_roots()
    for c in _search_root_candidates(roots):
        add(c)
    return found
