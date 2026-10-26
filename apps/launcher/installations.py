"""Installer discovery; never modifies a candidate game directory."""

import json
import re
from dataclasses import dataclass
from pathlib import Path


_EXECUTABLES = {'SR1': 'SlimeRancher.exe', 'SR2': 'SlimeRancher2.exe'}


@dataclass(frozen=True)
class GameInstallation:
    game: str
    root: Path
    executable: Path
    store: str
    unity_runtime: str


def discover_manual(path: str | Path, store: str = 'manual') -> GameInstallation | None:
    candidate = Path(path).expanduser()
    selected_exe = candidate.name.casefold() if candidate.is_file() else None
    root = candidate.parent if candidate.is_file() else candidate
    if not root.is_dir():
        return None
    for game, exe_name in _EXECUTABLES.items():
        # A chosen file must be the game executable itself, not e.g. UnityCrashHandler64.exe.
        if selected_exe is not None and selected_exe != exe_name.casefold():
            continue
        exe = root / exe_name
        data = root / (exe.stem + '_Data')
        if not exe.is_file() or not data.is_dir() or not (root / 'UnityPlayer.dll').is_file():
            continue
        if game == 'SR1' and (data / 'Managed' / 'Assembly-CSharp.dll').is_file():
            return GameInstallation(game, root.resolve(), exe.resolve(), store, 'mono')
        if game == 'SR2' and (root / 'GameAssembly.dll').is_file() and (data / 'il2cpp_data' / 'Metadata' / 'global-metadata.dat').is_file():
            return GameInstallation(game, root.resolve(), exe.resolve(), store, 'il2cpp')
    return None


def _vdf_tokens(text: str) -> list[str]:
    return re.findall(r'"((?:\\.|[^"\\])*)"|([{}])', re.sub(r'//[^\n]*', '', text))


def _vdf_values(text: str, key: str) -> list[str]:
    tokens = _vdf_tokens(text)
    return [tokens[i + 1][0].replace('\\\\', '\\') for i in range(len(tokens) - 1)
            if tokens[i][0].lower() == key.lower() and tokens[i + 1][0]]


def discover_stores(steam_roots: list[Path] | None = None, epic_manifest_dir: Path | None = None) -> list[GameInstallation]:
    """Scan known manifests, then verify every executable and Unity runtime in place."""
    if steam_roots is None:
        steam_roots = [Path(r'C:\Program Files (x86)\Steam'), Path(r'C:\Program Files\Steam')]
    if epic_manifest_dir is None:
        epic_manifest_dir = Path(r'C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests')
    found: dict[tuple[str, Path], GameInstallation] = {}
    for base in steam_roots:
        libraries = [base]
        library_file = base / 'steamapps' / 'libraryfolders.vdf'
        if library_file.is_file():
            try:
                libraries += [Path(p) for p in _vdf_values(library_file.read_text(encoding='utf-8-sig'), 'path')]
            except (OSError, UnicodeError):
                pass
        for library in libraries:
            for app_id in ('433340', '1657630'):
                manifest = library / 'steamapps' / f'appmanifest_{app_id}.acf'
                if not manifest.is_file():
                    continue
                try:
                    names = _vdf_values(manifest.read_text(encoding='utf-8-sig'), 'installdir')
                    if not names:
                        continue
                    candidate = discover_manual(library / 'steamapps' / 'common' / names[0], 'steam')
                    if candidate:
                        found[(candidate.game, candidate.root)] = candidate
                except (OSError, UnicodeError):
                    continue
    if epic_manifest_dir.is_dir():
        for manifest in epic_manifest_dir.glob('*.item'):
            try:
                data = json.loads(manifest.read_text(encoding='utf-8-sig'))
                candidate = discover_manual(data.get('InstallLocation', ''), 'epic')
                if candidate:
                    found[(candidate.game, candidate.root)] = candidate
            except (OSError, UnicodeError, ValueError, TypeError):
                continue
    return sorted(found.values(), key=lambda item: (item.game, item.store, str(item.root)))
