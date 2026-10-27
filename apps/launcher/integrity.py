"""Is a game folder complete enough to read? Catches half-extracted copies before anything fails deep inside a reader.

The usual cause is a game downloaded as several archives - Google Drive splits big downloads into
``<name>-<date>T<time>Z-1-001.zip``, ``...-2-002.zip``, ... - with only the first part extracted, or an archive whose
long paths Windows dropped while extracting. The check is cheap (file existence only) and names what is missing.
"""
from __future__ import annotations

import re
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
    return {'complete': not missing, 'missing': missing[:6], 'missing_count': len(missing), 'hint': hint,
            'hint_code': code, 'part': part}


def require(game, root):
    """Raise ``IncompleteGame`` with a readable message when the folder cannot be read."""
    state = check(game, root)
    if not state['complete']:
        raise IncompleteGame(game, root, state)
    return state


class IncompleteGame(RuntimeError):
    def __init__(self, game, root, state):
        name = 'Slime Rancher 2' if game == 'SR2' else 'Slime Rancher'
        super().__init__(f"{name} folder is incomplete ({state['missing_count']} missing, e.g. {state['missing'][0]}). "
                         f"{state['hint']} Folder: {root}")
        self.game, self.root, self.state = game, str(root), state
