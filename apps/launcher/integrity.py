"""Is a game folder complete enough to read? Catches half-extracted copies before anything fails deep inside a reader.

The usual cause is a game downloaded as several archives - Google Drive splits big downloads into
``<name>-<date>T<time>Z-1-001.zip``, ``...-2-002.zip``, ... - with only the first part extracted, or an archive whose
long paths Windows dropped while extracting. The check is cheap (file existence only) and names what is missing.
"""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from pathlib import Path

# files every working install has (SR2: both the 0.x JSON and the 1.x binary Addressables catalog are accepted)
REQUIRED = {
    'SR1': ('SlimeRancher.exe', 'UnityPlayer.dll', 'SlimeRancher_Data/globalgamemanagers',
            'SlimeRancher_Data/resources.assets', 'SlimeRancher_Data/sharedassets0.assets',
            'SlimeRancher_Data/Managed/Assembly-CSharp.dll'),
    'SR2': ('SlimeRancher2.exe', 'GameAssembly.dll', 'UnityPlayer.dll', 'SlimeRancher2_Data/globalgamemanagers',
            'SlimeRancher2_Data/globalgamemanagers.assets', 'SlimeRancher2_Data/resources.assets',
            'SlimeRancher2_Data/il2cpp_data/Metadata/global-metadata.dat',
            ('SlimeRancher2_Data/StreamingAssets/aa/catalog.bin', 'SlimeRancher2_Data/StreamingAssets/aa/catalog.json'),
            'SlimeRancher2_Data/StreamingAssets/aa/settings.json'),
}
SR2_BUNDLES = 'SlimeRancher2_Data/StreamingAssets/aa/StandaloneWindows64'
MIN_SR2_BUNDLES = 40             # 0.6.2 ships 80, 1.3.0 ships 824; a near-empty folder means a partial extraction
DRIVE_PART = re.compile(r'-\d{8}T\d{6}Z-(\d+)-\d{3}$')


def drive_part(root):
    """Part number when a folder on the path carries Google Drive's split-download suffix (else None)."""
    for part in Path(root).parts:
        match = DRIVE_PART.search(part)
        if match:
            return int(match.group(1))
    return None


MAX_PATH = 259


def long_paths_enabled():
    """Windows' opt-in for paths beyond 260 characters (off by default); without it games cannot open deeper files."""
    if os.name != 'nt':
        return True
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\FileSystem') as key:
            return bool(winreg.QueryValueEx(key, 'LongPathsEnabled')[0])
    except OSError:
        return False


def deepest_path(root):
    """Length of the longest file path in the game's asset folder (0 when there is none)."""
    folder = Path(root) / SR2_BUNDLES if (Path(root) / SR2_BUNDLES).is_dir() else Path(root)
    longest = 0
    try:
        with os.scandir(_long(folder)) as it:
            for entry in it:
                longest = max(longest, len(entry.name))
    except OSError:
        return 0
    return len(str(Path(folder).resolve())) + 1 + longest if longest else 0


def short_home(game, root):
    """Where a too-deep install can move on the same drive (a rename, instant)."""
    name = 'Slime Rancher 2' if game == 'SR2' else 'Slime Rancher'
    return Path(Path(root).resolve().anchor) / 'Jeux' / name


def check(game, root):
    """{'complete', 'missing' (first ones), 'missing_count', 'hint'} for an install folder."""
    root = Path(root)
    missing = []
    for item in REQUIRED.get(game, ()):
        options = item if isinstance(item, tuple) else (item,)
        if not any((root / o).is_file() for o in options):
            missing.append(options[0])
    if game == 'SR2':
        bundles = root / SR2_BUNDLES
        count = sum(1 for p in bundles.glob('*.bundle')) if bundles.is_dir() else 0
        if count < MIN_SR2_BUNDLES:
            missing.append(f'{SR2_BUNDLES}/*.bundle (found {count})')
    hint, code, part = '', '', None
    if missing:
        part = drive_part(root)
        code = 'drive_part' if part else 'missing'
        hint = ('This folder looks like part {0} of a Google Drive download that was split into several zip files: '
                'extract ALL the parts into the same folder, then select it again.'.format(part) if part else
                'Some game files are missing: re-extract the game (all archive parts, into a short path such as C:\\Games) '
                'or verify the game files in its store.')
    too_long = game == 'SR2' and not long_paths_enabled() and deepest_path(root) > MAX_PATH
    if too_long and not missing:
        code = 'too_long'
        hint = (f'The game folder is too deep for Windows ({deepest_path(root)} characters, limit 260): the game and '
                f'SlimeBridge cannot open some of its files. Move it to a short folder such as {short_home(game, root)}.')
    return {'complete': not missing and not too_long, 'missing': missing[:6], 'missing_count': len(missing), 'hint': hint,
            'hint_code': code, 'part': part, 'too_long': too_long, 'move_to': str(short_home(game, root)) if too_long else None}


def require(game, root):
    """Raise ``IncompleteGame`` with a readable message when the folder cannot be read."""
    state = check(game, root)
    if not state['complete']:
        raise IncompleteGame(game, root, state)
    return state


# ---- repair: put back the missing files from the game's own archives found on this PC -------------------------------
MIN_ZIP = 1 << 20


def _search_dirs(root):
    dirs = []
    try:
        from apps.launcher import detect
        dirs += detect.shell_folders()
    except Exception:  # noqa: BLE001 - registry access is best effort
        pass
    home = Path.home()
    dirs += [home / 'Downloads', home / 'Desktop', home / 'Téléchargements']
    dirs += list(Path(root).parents)[:4]            # the zips are often next to the extracted folder
    seen, out = set(), []
    for d in dirs:
        key = str(d).lower()
        if key not in seen and Path(d).is_dir():
            seen.add(key)
            out.append(Path(d))
    return out


def _long(path):
    """Windows long-path form, so files deeper than 260 characters can be written (what Explorer's extractor skips)."""
    s = str(Path(path).resolve())
    return '\\\\?\\' + s if os.name == 'nt' and not s.startswith('\\\\?\\') else s


def repair_candidates(game, root, search_dirs=None):
    """Zips on this PC that hold files this install is missing: the other parts of a split download, or the original zip.
    An archive matches when one of its top-level folders is named like a folder above the game (e.g. Slime.Rancher.2.v1.3.0)."""
    root = Path(root).resolve()
    ancestors = [a for a in [root, *root.parents] if a.name]
    found = []
    for folder in (search_dirs if search_dirs is not None else _search_dirs(root)):
        try:
            zips = [z for z in Path(folder).glob('*.zip') if z.stat().st_size >= MIN_ZIP]
        except OSError:
            continue
        for z in zips:
            try:
                with zipfile.ZipFile(z) as zf:
                    names = [n for n in zf.namelist() if not n.endswith('/')]
            except (OSError, zipfile.BadZipFile):
                continue
            names = [n.replace('\\', '/') for n in names]
            tops = {n.split('/', 1)[0].lower() for n in names}
            # a folder above the game named like a top folder of the archive; the same name can repeat
            # (Name/Name/Game when a zip is extracted into a folder of its own name): keep the base where the most
            # archive folders already exist, and only when it clearly is this game (half the folders found)
            dirs = sorted({n.rsplit('/', 1)[0] for n in names if '/' in n})
            best, best_score = None, 0
            for anc in ancestors:
                if anc.name.lower() not in tops:
                    continue
                base = anc.parent
                score = sum(1 for d in dirs if os.path.isdir(_long(base / d)))
                if score > best_score:
                    best, best_score = base, score
            if best is None or not dirs or best_score < 0.5 * len(dirs):
                continue
            missing = [n for n in names if not os.path.exists(_long(best / n))]
            if missing:
                found.append({'zip': str(z), 'base': str(best), 'missing': len(missing)})
    return found


def repair(game, root, search_dirs=None, progress=None):
    """Extract only the missing files from every matching archive (never overwrites), then re-check the install."""
    restored = 0
    for cand in repair_candidates(game, root, search_dirs):
        base = Path(cand['base'])
        with zipfile.ZipFile(cand['zip']) as zf:
            for info in zf.infolist():
                name = info.filename.replace('\\', '/')
                if name.endswith('/') or name.startswith('/') or '..' in name.split('/'):
                    continue
                target = _long(base / name)
                if os.path.exists(target):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temp = target + '.sbpart'
                with zf.open(info) as src, open(temp, 'wb') as out:
                    shutil.copyfileobj(src, out, 1 << 20)
                os.replace(temp, target)
                restored += 1
                if progress and restored % 50 == 0:
                    progress(restored)
    return {'restored': restored, 'state': check(game, root)}


def move_short(game, root):
    """Rename the install to a short folder on the same drive. Returns the new root."""
    src, dest = Path(root).resolve(), short_home(game, root)
    if dest.exists():
        raise OSError(f'{dest} already exists')
    if src.anchor.lower() != dest.anchor.lower():
        raise OSError('the short folder must be on the same drive')
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.rename(_long(src), _long(dest))
    return dest


class IncompleteGame(RuntimeError):
    def __init__(self, game, root, state):
        name = 'Slime Rancher 2' if game == 'SR2' else 'Slime Rancher'
        super().__init__(f"{name} folder is incomplete ({state['missing_count']} missing, e.g. {state['missing'][0]}). "
                         f"{state['hint']} Folder: {root}")
        self.game, self.root, self.state = game, str(root), state
