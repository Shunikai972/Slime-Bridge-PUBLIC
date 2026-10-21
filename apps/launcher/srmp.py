"""Online play for Slime Rancher 1: install / update / remove SRMP Revival (the owner's maintained SRMP build) next to SlimeBridge.

SRMP Revival is a separate GPL-3.0 mod (its own source, license and installer); SlimeBridge only places its DLL the same way
SRMP's own ``Install SRMP.cmd`` does, so either tool can update or uninstall what the other installed:

* ``SRML/Mods/SRMP.dll`` is the mod; ``SRML/Mods/SRMP.install.json`` records the install (FormatVersion 1);
* a different SRMP.dll already there is moved to ``SRML/Backups/SRMP/`` first (outside ``Mods``: SRML would load a backup
  left in ``Mods`` as a second copy), and ``remove`` restores it.

Hosting and joining happen in game from SRMP's own menu (Epic Online Services lobbies). Every player needs the same SlimeBridge
content (see ``bridge.content.content_manifest``) to see the imported species and Largos.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DLL = 'SRMP.dll'
RECORD = 'SRMP.install.json'
PRODUCT = 'SRMP Revival'


class SrmpError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def source_candidates(repo_root=None):
    """A release ships ``plugins/SRMP/SRMP.dll``; a developer checkout can point at its SRMP build."""
    root = Path(repo_root or REPO_ROOT)
    out = [root / 'plugins' / 'SRMP' / DLL]
    if os.environ.get('SLIMEBRIDGE_SRMP_DLL'):
        out.append(Path(os.environ['SLIMEBRIDGE_SRMP_DLL']))
    out.append(root.parent / 'srmp-public' / 'Builds' / 'SRMP' / DLL)
    return out


def find_source(repo_root=None):
    return next((p for p in source_candidates(repo_root) if p.is_file()), None)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _reparse(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def paths(game_root):
    root = Path(game_root)
    srml = root / 'SRML'
    return {'loader': root / 'SlimeRancher_Data' / 'Managed' / 'SRML.dll', 'mods': srml / 'Mods',
            'dll': srml / 'Mods' / DLL, 'record': srml / 'Mods' / RECORD, 'backups': srml / 'Backups' / 'SRMP'}


def read_record(game_root):
    record = paths(game_root)['record']
    try:
        return json.loads(record.read_text(encoding='utf-8-sig')) if record.is_file() else None
    except (OSError, ValueError):
        return None


def status(game_root, repo_root=None):
    """``missing`` | ``current`` | ``outdated`` | ``other`` (an SRMP the launcher has no source for) | ``unavailable``."""
    p = paths(game_root)
    source = find_source(repo_root)
    loader = p['loader'].is_file()
    if not p['dll'].is_file():
        state = 'missing' if source else 'unavailable'
        installed_sha = None
    else:
        installed_sha = _sha(p['dll'])
        if source is None:
            state = 'other'
        else:
            state = 'current' if installed_sha == _sha(source) else 'outdated'
    record = read_record(game_root) or {}
    return {'state': state, 'loader': loader, 'source': str(source) if source else None, 'installed_sha256': installed_sha,
            'installed_utc': record.get('InstalledUtc'), 'installed_by': record.get('InstalledBy', 'SRMP installer' if record else None)}


def _stamp():
    return _dt.datetime.now().strftime('%Y%m%d-%H%M%S')


def install(game_root, repo_root=None):
    """Copy SRMP.dll into SRML/Mods with a verified atomic replace; back up a different existing copy outside Mods."""
    p = paths(game_root)
    if not p['loader'].is_file():
        raise SrmpError('LOADER_MISSING', 'SRML')
    source = find_source(repo_root)
    if source is None:
        raise SrmpError('NO_SRMP_BUILD')
    for part in (Path(game_root), p['mods'].parent, p['mods'], p['dll'], p['backups'].parent):
        if part.exists() and _reparse(part):
            raise SrmpError('LINK_REFUSED', str(part))
    p['mods'].mkdir(parents=True, exist_ok=True)
    source_sha = _sha(source)
    backup = None
    if p['dll'].is_file():
        if _sha(p['dll']) == source_sha:
            return {'ok': True, 'state': 'current', 'changed': False, 'file': str(p['dll'])}
        p['backups'].mkdir(parents=True, exist_ok=True)
        backup = p['backups'] / f'SRMP-{_stamp()}-{uuid.uuid4().hex[:6]}.dll'
        backup.write_bytes(p['dll'].read_bytes())
        if _sha(backup) != _sha(p['dll']):
            raise SrmpError('BACKUP_VERIFY_FAILED')
    temp = p['mods'] / f'.{DLL}.{uuid.uuid4().hex}.installing'
    try:
        temp.write_bytes(source.read_bytes())
        if _sha(temp) != source_sha:
            raise SrmpError('COPY_VERIFY_FAILED')
        os.replace(temp, p['dll'])
    except PermissionError as exc:
        raise SrmpError('FILE_IN_USE', DLL) from exc
    finally:
        if temp.exists():
            temp.unlink()
    previous = (read_record(game_root) or {}).get('PreviousBackup')
    record = {'FormatVersion': 1, 'Product': PRODUCT, 'InstalledUtc': _dt.datetime.now(_dt.timezone.utc).isoformat(),
              'GamePath': str(Path(game_root)), 'ModsPath': str(p['mods']), 'InstalledFile': str(p['dll']),
              'SourceSha256': source_sha.upper(), 'PreviousBackup': str(backup) if backup else previous,
              'InstalledBy': 'SlimeBridge'}
    p['record'].write_text(json.dumps(record, indent=4), encoding='utf-8')
    return {'ok': True, 'state': 'current', 'changed': True, 'file': str(p['dll']), 'backup': str(backup) if backup else None}


def remove(game_root):
    """Remove SRMP; restore the copy that was there before (if one was backed up)."""
    p = paths(game_root)
    if p['dll'].exists() and _reparse(p['dll']):
        raise SrmpError('LINK_REFUSED', str(p['dll']))
    record = read_record(game_root) or {}
    restored = None
    try:
        if p['dll'].is_file():
            p['dll'].unlink()
        backup = Path(record['PreviousBackup']) if record.get('PreviousBackup') else None
        if backup and backup.is_file() and p['backups'] in backup.parents:
            p['dll'].write_bytes(backup.read_bytes())
            restored = str(backup)
    except PermissionError as exc:
        raise SrmpError('FILE_IN_USE', DLL) from exc
    if p['record'].is_file():
        p['record'].unlink()
    return {'ok': True, 'restored': restored}
