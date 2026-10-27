"""Mod loaders, installed by the launcher when a game does not have one yet (one step for the player).

* Slime Rancher: SRML 0.2.1c. Its official installer (SlimeRancherModding/SRML, GPL-3.0) is downloaded and run in its
  silent "Vortex" mode (``SRMLInstaller.exe -v <game>``): it copies SRML into ``SlimeRancher_Data/Managed``, patches
  ``Assembly-CSharp.dll`` (keeping ``Assembly-CSharp_old.dll``) and creates ``SRML/Mods``.
* Slime Rancher 2: MelonLoader 0.7.3 (LavaGang/MelonLoader, Apache-2.0), the version Ranching Together targets. Its
  ``MelonLoader.x64.zip`` is extracted into the game folder (``version.dll`` proxy + ``MelonLoader/``).

Both downloads come from the projects' official GitHub releases, are checked against the pinned SHA-256 and cached
under ``Lab/Cache/loaders``. ``<game>/SlimeBridge/loader.json`` records what was installed.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / 'Lab' / 'Cache' / 'loaders'

LOADERS = {
    'SR1': {'name': 'SRML', 'version': '0.2.1c', 'file': 'SRMLInstaller_0.2.1c.zip',
            'url': 'https://github.com/SlimeRancherModding/SRML/releases/download/v0.2.1/SRMLInstaller_0.2.1c.zip',
            'sha256': 'e3a832397920a03a682e554e9ace3631a558894d1ace32cb7942cd05fbbd308e'},
    'SR2': {'name': 'MelonLoader', 'version': '0.7.3', 'file': 'MelonLoader.x64.zip',
            'url': 'https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip',
            'sha256': '5b2b2f3d1cd42b59ec886c5bdc2663edae87a0097a4f4a8f58c0965a99dda416'},
}
# MelonLoader can be installed under any of these proxy names (its installer and manual installs both use them)
MELON_PROXIES = ('version.dll', 'winmm.dll', 'winhttp.dll', 'dinput8.dll', 'dsound.dll')
MELON_CORES = ('net6/MelonLoader.dll', 'net35/MelonLoader.dll', 'MelonLoader.dll')
SRML_TIMEOUT = 180


class LoaderError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def present(realm, game_root):
    """True when the game already has its mod loader (any MelonLoader 0.6+/0.7 layout, or SRML)."""
    root = Path(game_root)
    if realm == 'SR1':
        return (root / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll').is_file()
    melon = root / 'MelonLoader'
    return any((root / p).is_file() for p in MELON_PROXIES) and any((melon / c).is_file() for c in MELON_CORES)


def describe(realm, game_root):
    return LOADERS[realm]['name'] if present(realm, game_root) else 'Not configured'


def _sha(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def cached(realm, cache=CACHE, opener=urllib.request.urlopen):
    """The verified loader archive, downloaded once from its official release."""
    spec = LOADERS[realm]
    target = Path(cache) / f"{spec['sha256'][:12]}-{spec['file']}"
    if target.is_file() and _sha(target) == spec['sha256']:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f'.{uuid.uuid4().hex}.part')
    try:
        request = urllib.request.Request(spec['url'], headers={'User-Agent': 'SlimeBridge-launcher'})
        with opener(request, timeout=120) as response, temp.open('wb') as out:
            shutil.copyfileobj(response, out)
        if _sha(temp) != spec['sha256']:
            raise LoaderError('DOWNLOAD_VERIFY_FAILED', spec['name'])
        os.replace(temp, target)
    except LoaderError:
        raise
    except OSError as exc:
        raise LoaderError('DOWNLOAD_FAILED', f"{spec['name']}: {exc}") from exc
    finally:
        if temp.exists():
            temp.unlink()
    return target


def _check_game(realm, root):
    if realm == 'SR1':
        if not (root / 'SlimeRancher.exe').is_file() or not (root / 'SlimeRancher_Data' / 'Managed' / 'Assembly-CSharp.dll').is_file():
            raise LoaderError('NOT_A_GAME_FOLDER', str(root))
    elif not (root / 'SlimeRancher2.exe').is_file() or not (root / 'GameAssembly.dll').is_file():
        raise LoaderError('NOT_A_GAME_FOLDER', str(root))


def _record(root, realm):
    spec = LOADERS[realm]
    folder = root / 'SlimeBridge'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'loader.json').write_text(json.dumps({'loader': spec['name'], 'version': spec['version'], 'source': spec['url'],
                                                     'sha256': spec['sha256'], 'installed_unix': time.time()}, indent=1), encoding='utf-8')


def _install_melon(root, archive):
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace('\\', '/')
            if name.startswith('/') or '..' in name.split('/'):
                raise LoaderError('BAD_ARCHIVE', name)
            target = root / name
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_name(f'.{target.name}.{uuid.uuid4().hex}.installing')
            with zf.open(info) as src, temp.open('wb') as out:
                shutil.copyfileobj(src, out)
            try:
                os.replace(temp, target)
            except PermissionError as exc:
                temp.unlink(missing_ok=True)
                raise LoaderError('FILE_IN_USE', name) from exc


def _install_srml(root, archive, run=subprocess.run):
    work = Path(archive).parent / 'srml-installer'
    work.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extract('SRMLInstaller.exe', work)
    exe = work / 'SRMLInstaller.exe'
    # "-v <game>" = the installer's unattended mode (no questions, no "press any key")
    proc = run([str(exe), '-v', str(root)], cwd=str(work), capture_output=True, text=True, timeout=SRML_TIMEOUT,
               stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not present('SR1', root):
        tail = ((proc.stdout or '') + (proc.stderr or '')).strip()[-400:]
        raise LoaderError('SRML_INSTALL_FAILED', tail or f'exit code {proc.returncode}')


def install(realm, game_root, cache=CACHE, opener=urllib.request.urlopen, run=subprocess.run):
    """Install the game's mod loader; a loader already there is left untouched."""
    root = Path(game_root)
    if present(realm, root):
        return {'ok': True, 'state': 'current', 'changed': False, 'loader': LOADERS[realm]['name']}
    _check_game(realm, root)
    archive = cached(realm, cache, opener)
    if realm == 'SR2':
        _install_melon(root, archive)
    else:
        _install_srml(root, archive, run)
    if not present(realm, root):
        raise LoaderError('VERIFY_FAILED', LOADERS[realm]['name'])
    _record(root, realm)
    return {'ok': True, 'state': 'installed', 'changed': True, 'loader': LOADERS[realm]['name'],
            'version': LOADERS[realm]['version']}


def ensure(realm, game_root):
    """Best effort for the plugin install and the Play preparation: never raises, says what happened."""
    try:
        if present(realm, game_root):
            return {'step': 'loader', 'status': 'CURRENT'}
        install(realm, game_root)
        return {'step': 'loader', 'status': 'INSTALLED', 'detail': f"{LOADERS[realm]['name']} {LOADERS[realm]['version']}"}
    except LoaderError as exc:
        return {'step': 'loader', 'status': 'FAILED', 'detail': f'{exc.code}: {exc.detail}' if exc.detail else exc.code}
    except (OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        return {'step': 'loader', 'status': 'FAILED', 'detail': str(exc)}
