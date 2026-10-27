"""Sanctuary tooling for the launcher: read the player's own SR1, build the island layout, install/remove it.

Nothing from the game is copied into the repository or the mod: the *kit* is a description of native objects (names,
transforms, parameters) written under ``Lab/`` and the *layout* places them. Installing writes one small JSON file into
``<SR1>/SlimeBridge/sanctuary/`` that the SR1 plugin reads; removing deletes exactly that folder's two files.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KIT_ROOT = REPO_ROOT / 'Lab' / 'AssetCache'
LAYOUT_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'sanctuary'
# shipped island kit: a description of native SR1 objects (names, transforms, parameters; no game data), so every
# install can build its island without reading the game first
BUNDLED_KIT_ROOT = REPO_ROOT / 'bridge' / 'sanctuary' / 'bundled'
INSTALL_PARTS = ('SlimeBridge', 'sanctuary')
INSTALL_FILES = ('sanctuary-layout.json', 'install-receipt.json')
DEPENDENCIES = {'UnityPy': 'UnityPy==1.25.4', 'TypeTreeGeneratorAPI': 'TypeTreeGeneratorAPI==0.0.10', 'dnfile': 'dnfile'}
KEEP = 3                                                      # newest kits / layouts kept; older fresh folders are pruned


def dependencies():
    missing = [pip for module, pip in DEPENDENCIES.items() if importlib.util.find_spec(module) is None]
    return {'ok': not missing, 'missing': missing, 'hint': ('pip install ' + ' '.join(missing)) if missing else None}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _newest(root, prefix, marker):
    if not Path(root).is_dir():
        return None
    folders = sorted((p for p in Path(root).iterdir() if p.is_dir() and p.name.startswith(prefix) and (p / marker).is_file()),
                     key=lambda p: p.name)
    return folders[-1] if folders else None


def _kit_dir(root=None):
    folder = _newest(root or KIT_ROOT, 'sanctuary-kit-', 'sanctuary-kit.json')
    if folder is None and root is None:
        folder = _newest(BUNDLED_KIT_ROOT, 'sanctuary-kit-', 'sanctuary-kit.json')
    return folder


def latest_kit(root=None):
    folder = _kit_dir(root)
    if folder is None:
        return None
    try:
        kit = json.loads((folder / 'sanctuary-kit.json').read_text(encoding='utf-8'))
        catalog = kit['statue_catalog']
    except (OSError, ValueError, KeyError):
        return None
    counts = {}
    for row in catalog:
        counts[row['status']] = counts.get(row['status'], 0) + 1
    return {'path': str(folder), 'name': folder.name, 'sha256': _sha(folder / 'sanctuary-kit.json'), 'statues': counts,
            'native_constructs': len(kit.get('statues', [])), 'walkway_end': (kit.get('walkway', {}).get('end_pieces') or [{}])[0].get('name')}


def latest_layout(root=None):
    folder = _newest(root or LAYOUT_ROOT, 'layout-', 'sanctuary-layout.json')
    if folder is None:
        return None
    try:
        layout = json.loads((folder / 'sanctuary-layout.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return {'path': str(folder), 'name': folder.name, 'sha256': _sha(folder / 'sanctuary-layout.json'),
            'statues': len(layout.get('statues', [])), 'required': len(layout.get('requirement_manifest', {}).get('required', [])),
            'pieces': len(layout.get('pieces', [])), 'access_mode': layout.get('config', {}).get('sr1_bridge_access_mode'),
            'gold_cost': layout.get('config', {}).get('sr1_gold_plort_gate_cost')}


def refresh_layout(kit_root=None, layout_root=None, publisher=None):
    """Rebuild the island layout when it was made from another kit or by older generator code (same settings).

    Returns the new layout folder name, or None when the newest layout is already current (or there is no kit to build from).
    """
    from bridge.sanctuary import layout as layout_module
    kit_dir = _kit_dir(kit_root)
    if kit_dir is None:
        return None
    layout_root = Path(layout_root or LAYOUT_ROOT)
    current = _newest(layout_root, 'layout-', 'sanctuary-layout.json')
    config = {}
    if current is not None:
        try:
            existing = json.loads((current / 'sanctuary-layout.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            existing = {}
        if existing.get('kit_sha256') == _sha(kit_dir / 'sanctuary-kit.json') and existing.get('generator') == layout_module.generator_id():
            return None
        config = existing.get('config') or {}
    layout_root.mkdir(parents=True, exist_ok=True)
    out = layout_root / f"layout-{time.strftime('%Y%m%d-%H%M%S')}"
    counter = 2
    while out.exists():                                       # two rebuilds inside one second must not collide
        out = layout_root / f"layout-{time.strftime('%Y%m%d-%H%M%S')}-{counter}"
        counter += 1
    (publisher or layout_module.publish_layout)(layout_module.build_layout(layout_module.load_kit(kit_dir), config), out)
    _prune(layout_root, 'layout-')
    return out.name


def _prune(root, prefix, keep=KEEP):
    folders = sorted((p for p in Path(root).iterdir() if p.is_dir() and p.name.startswith(prefix)), key=lambda p: p.name)
    for old in folders[:-keep]:
        shutil.rmtree(old, ignore_errors=True)


def _reparse(path):
    path = Path(path)
    return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)


def install_dir(game_root):
    return Path(game_root).joinpath(*INSTALL_PARTS)


def installed(game_root):
    """``{'present', 'layout_sha256', 'kit_sha256', 'installed_utc', 'matches_latest'}`` for one SR1 folder."""
    if not game_root:
        return {'present': False}
    folder = install_dir(game_root)
    layout, receipt = folder / INSTALL_FILES[0], folder / INSTALL_FILES[1]
    if not layout.is_file():
        return {'present': False}
    try:
        info = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
    except (OSError, ValueError):
        info = {}
    latest = latest_layout()
    sha = _sha(layout)
    return {'present': True, 'layout_sha256': sha, 'kit_sha256': info.get('kit_sha256'), 'installed_utc': info.get('installed_utc'),
            'matches_latest': bool(latest and latest['sha256'] == sha)}


def plugin_installed(game_root):
    return bool(game_root) and (Path(game_root) / 'SRML' / 'Mods' / 'SlimeBridge' / 'SlimeBridge.SR1.dll').is_file()


def install_layout(layout_folder, game_root):
    """Copy the built layout next to the game (additive, atomic, no reparse points, nothing else touched)."""
    source = Path(layout_folder) / 'sanctuary-layout.json'
    root = Path(game_root)
    if not source.is_file():
        raise ValueError('build the sanctuary first')
    if not (root / 'SlimeRancher_Data').is_dir():
        raise ValueError('not an SR1 folder')
    folder = install_dir(root)
    for part in (root, root / INSTALL_PARTS[0], folder):
        if part.exists() and _reparse(part):
            raise ValueError('install folder is a link: refused')
    folder.mkdir(parents=True, exist_ok=True)
    layout_sha = _sha(source)
    kit = latest_kit()
    temp = folder / ('.install-' + uuid.uuid4().hex)
    try:
        shutil.copyfile(source, temp)
        if _sha(temp) != layout_sha:
            raise OSError('copy verification failed')
        os.replace(temp, folder / INSTALL_FILES[0])
    finally:
        if temp.exists():
            temp.unlink()
    (folder / INSTALL_FILES[1]).write_text(json.dumps({
        'schema': 'bridge-sanctuary-install', 'schema_version': 1, 'layout_sha256': layout_sha,
        'kit_sha256': kit['sha256'] if kit else None, 'installed_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source_layout': Path(layout_folder).name}, indent=2) + '\n', encoding='utf-8')
    return {'ok': True, 'folder': str(folder), 'layout_sha256': layout_sha}


def remove_installed(game_root):
    """Delete the two files this module wrote (and the folders if they end up empty). Progress sidecars are untouched."""
    folder = install_dir(game_root)
    removed = []
    if folder.is_dir() and not _reparse(folder):
        for name in INSTALL_FILES:
            path = folder / name
            if path.is_file():
                path.unlink()
                removed.append(name)
        for directory in (folder, folder.parent):
            try:
                directory.rmdir()
            except OSError:
                pass
    return {'ok': True, 'removed': removed}


class SanctuaryJob:
    """Extract the kit from the player's SR1, then build and publish the layout (background thread, pollable)."""

    PHASES = (('kit', 'Reading your Slime Rancher', 0.70), ('layout', 'Placing the island', 0.30))

    def __init__(self, sr1_data_dir, config, *, extractor=None, builder=None, kit_root=None, layout_root=None,
                 clock=time.monotonic):
        self.data_dir = Path(sr1_data_dir)
        self.config = dict(config)
        self.kit_root, self.layout_root, self._clock = Path(kit_root or KIT_ROOT), Path(layout_root or LAYOUT_ROOT), clock
        self._extractor, self._builder = extractor, builder
        self.id = uuid.uuid4().hex[:12]
        self._lock = threading.Lock()
        self._thread = None
        self.state = {'status': 'QUEUED', 'phase': None, 'phase_label': None, 'progress': 0.0, 'error': None, 'result': None,
                      'started': None, 'finished': None}

    def snapshot(self):
        with self._lock:
            return dict(self.state, id=self.id)

    def _set(self, **fields):
        with self._lock:
            self.state.update(fields)

    def start(self):
        self._thread = threading.Thread(target=self._run, name=f'sanctuary-{self.id}', daemon=True)
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def _run(self):
        self._set(status='RUNNING', started=self._clock())
        try:
            stamp = time.strftime('%Y%m%d-%H%M%S')
            self._set(phase='kit', phase_label=self.PHASES[0][1], progress=0.05)
            extractor = self._extractor
            if extractor is None:
                from bridge.content import sr1_sanctuary
                extractor = sr1_sanctuary.extract_kit
            kit_out = self.kit_root / f'sanctuary-kit-{stamp}'
            self.kit_root.mkdir(parents=True, exist_ok=True)
            kit_result = extractor(self.data_dir, kit_out, True)
            self._set(phase='layout', phase_label=self.PHASES[1][1], progress=0.72)
            builder = self._builder
            if builder is None:
                from bridge.sanctuary import layout as layout_module
                builder = lambda kit_dir, config, out: layout_module.publish_layout(      # noqa: E731
                    layout_module.build_layout(layout_module.load_kit(kit_dir), config), out)
            layout_out = self.layout_root / f'layout-{stamp}'
            self.layout_root.mkdir(parents=True, exist_ok=True)
            layout_result = builder(kit_out, self.config, layout_out)
            _prune(self.kit_root, 'sanctuary-kit-')
            _prune(self.layout_root, 'layout-')
            self._set(status='DONE', progress=1.0, finished=self._clock(),
                      result={'kit': str(kit_out), 'layout': str(layout_out), 'kit_sha256': (kit_result or {}).get('kit_sha256'),
                              'statues': (layout_result or {}).get('statues'), 'required': (layout_result or {}).get('required')})
        except Exception as exc:                                  # noqa: BLE001 - shown to the player, never swallowed
            self._set(status='FAILED', error=f'{type(exc).__name__}: {exc}'[:400], finished=self._clock())
