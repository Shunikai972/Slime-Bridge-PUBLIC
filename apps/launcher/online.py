"""Online play for both games, installed together with the SlimeBridge plugin (one step for the player).

* Slime Rancher: SRMP Revival, shipped in the release (``plugins/SRMP``), placed by ``apps.launcher.srmp``.
* Slime Rancher 2: Ranching Together (SR2MP) + its Starlight dependency, downloaded from their official GitHub releases on
  first use (Starlight's license forbids redistributing its DLL), checked against the pinned SHA-256 below, cached under
  ``Lab/Cache/online`` and copied into ``<SR2>/Mods``. A different copy already there is moved to
  ``<SR2>/SlimeBridge/backups`` first; ``<SR2>/SlimeBridge/online.json`` records what SlimeBridge placed so ``remove`` only
  takes back its own files.

Network use: two HTTPS downloads from github.com, only when a component is missing from the cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

from apps.launcher import srmp

REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / 'Lab' / 'Cache' / 'online'

# Paired releases (published the same day, Starlight is SR2MP's required dependency); tested on SR2 1.3.0 + MelonLoader 0.7.3.
SR2_COMPONENTS = (
    {'id': 'starlight', 'name': 'Starlight', 'version': '4.0.4', 'file': 'Starlight.dll',
     'url': 'https://github.com/ThatFinn/Starlight/releases/download/v4.0.4/Starlight.dll',
     'sha256': '56568caf16a5c401ab80f3b3c251e2e725e8d86b69b658730a4794f08231b13c'},
    {'id': 'sr2mp', 'name': 'Ranching Together', 'version': 'Beta 0.3.9', 'file': 'SR2MP.dll',
     'url': 'https://github.com/pyeight/SlimeRancher2Multiplayer/releases/download/Beta_0.3.9/SR2MP.dll',
     'sha256': '9cf2823c635e0d4e515828b14a50e8b42442f45bfb31e175f6a80984f437ea28'},
)
SR2_PORT = 1919        # Ranching Together's default UDP host port


class OnlineError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _reparse(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def cached(component, cache=CACHE, download=True, opener=urllib.request.urlopen):
    """Path of the verified component in the cache, downloading it once when allowed (else None)."""
    target = Path(cache) / f"{component['id']}-{component['sha256'][:12]}.dll"
    if target.is_file() and _sha(target) == component['sha256']:
        return target
    if not download:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(f'.{uuid.uuid4().hex}.part')
    try:
        request = urllib.request.Request(component['url'], headers={'User-Agent': 'SlimeBridge-launcher'})
        with opener(request, timeout=60) as response, temp.open('wb') as out:
            shutil.copyfileobj(response, out)
        if _sha(temp) != component['sha256']:
            raise OnlineError('DOWNLOAD_VERIFY_FAILED', component['name'])
        os.replace(temp, target)
    except OnlineError:
        raise
    except OSError as exc:
        raise OnlineError('DOWNLOAD_FAILED', f"{component['name']}: {exc}") from exc
    finally:
        if temp.exists():
            temp.unlink()
    return target


def _sr2_paths(game_root):
    root = Path(game_root)
    return {'loader': root / 'MelonLoader' / 'net6' / 'MelonLoader.dll', 'mods': root / 'Mods',
            'record': root / 'SlimeBridge' / 'online.json', 'backups': root / 'SlimeBridge' / 'backups'}


def _read_record(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else {}
    except (OSError, ValueError):
        return {}


def sr2_status(game_root, cache=CACHE):
    p = _sr2_paths(game_root)
    parts = []
    for c in SR2_COMPONENTS:
        dest = p['mods'] / c['file']
        state = 'missing'
        if dest.is_file():
            state = 'current' if _sha(dest) == c['sha256'] else 'other'
        parts.append({'id': c['id'], 'name': c['name'], 'version': c['version'], 'state': state,
                      'cached': cached(c, cache, download=False) is not None})
    states = {part['state'] for part in parts}
    overall = 'current' if states == {'current'} else ('missing' if states == {'missing'} else 'outdated')
    return {'state': overall, 'loader': p['loader'].is_file(), 'components': parts, 'port': SR2_PORT}


def sr2_install(game_root, cache=CACHE, opener=urllib.request.urlopen):
    p = _sr2_paths(game_root)
    if not p['loader'].is_file():
        raise OnlineError('LOADER_MISSING', 'MelonLoader')
    for part in (Path(game_root), p['mods'], p['record'].parent):
        if part.exists() and _reparse(part):
            raise OnlineError('LINK_REFUSED', str(part))
    sources = {c['id']: cached(c, cache, download=True, opener=opener) for c in SR2_COMPONENTS}   # all or nothing
    p['mods'].mkdir(parents=True, exist_ok=True)
    record = _read_record(p['record'])
    placed = dict(record.get('placed', {}))
    changed = []
    for c in SR2_COMPONENTS:
        dest = p['mods'] / c['file']
        if dest.exists() and _reparse(dest):
            raise OnlineError('LINK_REFUSED', str(dest))
        if dest.is_file() and _sha(dest) == c['sha256']:
            placed[c['id']] = c['sha256']
            continue
        if dest.is_file():
            p['backups'].mkdir(parents=True, exist_ok=True)
            backup = p['backups'] / f"{dest.stem}-{time.strftime('%Y%m%d-%H%M%S')}{dest.suffix}"
            shutil.copy2(dest, backup)
            record.setdefault('backups', {})[c['id']] = str(backup)
        temp = p['mods'] / f".{c['file']}.{uuid.uuid4().hex}.installing"
        try:
            shutil.copyfile(sources[c['id']], temp)
            if _sha(temp) != c['sha256']:
                raise OnlineError('COPY_VERIFY_FAILED', c['name'])
            os.replace(temp, dest)
        except PermissionError as exc:
            raise OnlineError('FILE_IN_USE', c['file']) from exc
        finally:
            if temp.exists():
                temp.unlink()
        placed[c['id']] = c['sha256']
        changed.append(c['name'])
    record.update({'schema': 'slimebridge-online', 'schema_version': 1, 'placed': placed})
    p['record'].parent.mkdir(parents=True, exist_ok=True)
    p['record'].write_text(json.dumps(record, indent=1), encoding='utf-8')
    return {'ok': True, 'changed': changed}


def sr2_remove(game_root):
    """Take back only the files SlimeBridge placed (same hash), restore what they replaced."""
    p = _sr2_paths(game_root)
    record = _read_record(p['record'])
    removed = []
    for c in SR2_COMPONENTS:
        dest = p['mods'] / c['file']
        if record.get('placed', {}).get(c['id']) and dest.is_file() and not _reparse(dest) and _sha(dest) == record['placed'][c['id']]:
            dest.unlink()
            removed.append(c['name'])
            backup = record.get('backups', {}).get(c['id'])
            if backup and Path(backup).is_file() and p['backups'] in Path(backup).parents:
                shutil.copy2(backup, dest)
    if p['record'].is_file():
        p['record'].unlink()
    return {'ok': True, 'removed': removed}


# ---- one entry point per realm ---------------------------------------------------------------------------------------
def status(realm, game_root):
    if realm == 'SR1':
        return srmp.status(game_root)
    return sr2_status(game_root)


def install(realm, game_root):
    """Install/update the online components of one game. Raises OnlineError / srmp.SrmpError with a code."""
    if realm == 'SR1':
        return srmp.install(game_root)
    return sr2_install(game_root)


def remove(realm, game_root):
    if realm == 'SR1':
        return srmp.remove(game_root)
    return sr2_remove(game_root)


def ensure(realm, game_root):
    """Best effort used by the plugin install and the Play preparation: never raises, says what happened."""
    try:
        state = status(realm, game_root)
        if state.get('state') == 'current':
            return {'step': 'online', 'status': 'CURRENT'}
        if not state.get('loader'):
            return {'step': 'online', 'status': 'LOADER_MISSING'}
        install(realm, game_root)
        return {'step': 'online', 'status': 'INSTALLED'}
    except (OnlineError, srmp.SrmpError) as exc:
        return {'step': 'online', 'status': 'FAILED', 'detail': f'{exc.code}: {exc.detail}' if exc.detail else exc.code}
    except OSError as exc:
        return {'step': 'online', 'status': 'FAILED', 'detail': str(exc)}
