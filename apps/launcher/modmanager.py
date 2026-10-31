"""Mod manager: find a mod for Slime Rancher or Slime Rancher 2, put it in the right game, take it out again.

* Search: the game's Nexus Mods catalogue (public GraphQL API, no account needed).
* Install: a .zip or .dll goes to the game's mod folder (SR1: SRML/Mods, SR2: Mods, SR2 libraries: UserLibs). Direct downloads
  need the player's own Nexus API key on a Premium account; otherwise Nexus wants its "Manual download" button clicked on
  the website, and the launcher installs the file as soon as it lands in the Downloads folder (watch_downloads).
* Remove: never deletes; the mod's files move to <game>/SlimeBridge/removed-mods/<time>/ (put back by moving them back).
* What SlimeBridge manages itself (its plugin, SRMP, Ranching Together, Starlight) is listed but protected here.
Every install is recorded in <game>/SlimeBridge/mods.json (name, Nexus id, version, files) so removal takes exactly those files.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

NEXUS_GQL = 'https://api.nexusmods.com/v2/graphql'
NEXUS_V1 = 'https://api.nexusmods.com/v1'
DOMAINS = {'SR1': 'slimerancher', 'SR2': 'slimerancher2'}
PROTECTED = {'SlimeBridge', 'SlimeBridge.SR2.dll', 'SlimeBridge.SR2.pdb', 'SRMP.dll', 'SRMP.install.json', 'SR2MP.dll', 'Starlight.dll'}
MOD_EXT = ('.dll',)
MAX_ARCHIVE = 300 * 1024 * 1024


class ModError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def mod_dir(realm, root) -> Path:
    return Path(root) / ('SRML/Mods' if realm == 'SR1' else 'Mods')


def _record_path(root) -> Path:
    return Path(root) / 'SlimeBridge' / 'mods.json'


def _records(root) -> dict:
    try:
        data = json.loads(_record_path(root).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_records(root, data):
    path = _record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')


# ---- search ---------------------------------------------------------------------------------------------------------
QUERY = '''query($filter: ModsFilter, $sort: [ModsSort!], $count: Int, $offset: Int) {
  mods(filter: $filter, sort: $sort, count: $count, offset: $offset) {
    totalCount nodes { modId name summary version author endorsements downloads updatedAt thumbnailUrl } } }'''


def search(realm, text='', page=0, count=20, opener=urllib.request.urlopen):
    """Mods of that game whose name matches ``text`` (most downloaded first; empty text = the most popular)."""
    domain = DOMAINS[realm]
    flt = {'gameDomainName': [{'value': domain}]}
    if text.strip():
        flt['name'] = [{'value': text.strip()[:60], 'op': 'WILDCARD'}]
    body = {'query': QUERY, 'variables': {'filter': flt, 'sort': [{'downloads': {'direction': 'DESC'}}], 'count': count, 'offset': page * count}}
    req = urllib.request.Request(NEXUS_GQL, json.dumps(body).encode(), {'Content-Type': 'application/json', 'User-Agent': 'SlimeBridge'})
    try:
        with opener(req, timeout=20) as response:
            data = json.loads(response.read())
    except (OSError, ValueError) as exc:
        raise ModError('SEARCH_FAILED', str(exc)) from exc
    if data.get('errors'):
        raise ModError('SEARCH_FAILED', str(data['errors'][0].get('message', ''))[:200])
    mods = (data.get('data') or {}).get('mods') or {}
    out = []
    for node in mods.get('nodes') or []:
        out.append({'id': node['modId'], 'name': node['name'], 'summary': (node.get('summary') or '')[:240], 'version': node.get('version'),
                    'author': node.get('author'), 'downloads': node.get('downloads') or 0, 'endorsements': node.get('endorsements') or 0,
                    'updated': node.get('updatedAt'), 'thumbnail': node.get('thumbnailUrl'),
                    'url': f'https://www.nexusmods.com/{domain}/mods/{node["modId"]}'})
    return {'total': mods.get('totalCount') or 0, 'mods': out}


# ---- installed ------------------------------------------------------------------------------------------------------
def installed(realm, root):
    """Everything in the game's mod folder: mods installed here (with their name), others found there, protected ones."""
    folder = mod_dir(realm, root)
    records = _records(root).get(realm, {})
    owned = {f: key for key, rec in records.items() for f in rec.get('files', [])}
    out, seen = [], set()
    for key, rec in records.items():
        present = [f for f in rec.get('files', []) if (Path(root) / f).exists()]
        if present:
            out.append({'key': key, 'name': rec.get('name') or key, 'version': rec.get('version'), 'nexus_id': rec.get('nexus_id'),
                        'files': present, 'protected': False, 'managed': True})
            seen.update(present)
    if folder.is_dir():
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            rel = entry.relative_to(root).as_posix()
            if rel in seen or rel in owned or entry.name.startswith('.'):
                continue
            if entry.is_file() and entry.suffix.lower() not in MOD_EXT:
                continue
            out.append({'key': 'file:' + rel, 'name': entry.stem if entry.is_file() else entry.name, 'version': None, 'nexus_id': None,
                        'files': [rel], 'protected': entry.name in PROTECTED, 'managed': False})
    return out


# ---- install --------------------------------------------------------------------------------------------------------
def _safe_member(name):
    parts = Path(name.replace('\\', '/')).parts
    return name and not name.startswith(('/', '\\')) and '..' not in parts and ':' not in name


def install_file(realm, root, path, meta=None):
    """Put a downloaded .zip or .dll into the game. Returns the record (files are relative to the game folder)."""
    path, root = Path(path), Path(root)
    meta = dict(meta or {})
    if not path.is_file():
        raise ModError('FILE_NOT_FOUND', str(path))
    target = mod_dir(realm, root)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    if path.suffix.lower() == '.dll':
        dest = target / path.name
        if dest.name in PROTECTED:
            raise ModError('PROTECTED', dest.name)
        shutil.copy2(path, dest)
        written.append(dest)
    elif path.suffix.lower() == '.zip':
        if path.stat().st_size > MAX_ARCHIVE:
            raise ModError('TOO_BIG', path.name)
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if not n.endswith('/')]
            if not names or not all(_safe_member(n) for n in names):
                raise ModError('BAD_ARCHIVE', path.name)
            plan = _plan(realm, root, names)
            if not plan:
                raise ModError('NO_MOD_IN_ARCHIVE', path.name)
            for member, dest in plan:
                if dest.name in PROTECTED:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest, 'wb') as out:
                    shutil.copyfileobj(src, out)
                written.append(dest)
    else:
        raise ModError('UNSUPPORTED_FILE', path.suffix + ' (zip or dll only; extract other archives first)')
    if not written:
        raise ModError('NO_MOD_IN_ARCHIVE', path.name)
    key = str(meta.get('nexus_id') or path.stem)
    record = {'name': meta.get('name') or path.stem, 'version': meta.get('version'), 'nexus_id': meta.get('nexus_id'),
              'source': meta.get('url') or str(path.name), 'installed': time.strftime('%Y-%m-%d %H:%M'),
              'files': sorted({p.relative_to(root).as_posix() for p in written})}
    data = _records(root)
    data.setdefault(realm, {})[key] = record
    _save_records(root, data)
    return {'key': key, **record}


def _plan(realm, root, names):
    """Where each archive member goes. Archives that already mirror the game layout (Mods/, UserLibs/, SRML/Mods/) keep
    it; otherwise the folder holding the DLLs becomes the mod (SR1: its own folder under SRML/Mods, SR2: Mods/)."""
    root = Path(root)
    lowered = {n: n.replace('\\', '/') for n in names}
    plan = []
    layout = [n for n, l in lowered.items() if any(f'/{k}/' in '/' + l.lower() for k in ('mods', 'userlibs', 'plugins'))]
    if layout:
        for n in layout:
            l = lowered[n]
            low = '/' + l.lower()
            for key, base in (('srml/mods/', root / 'SRML' / 'Mods'), ('userlibs/', root / 'UserLibs'), ('mods/', mod_dir(realm, root)),
                              ('plugins/', root / 'Plugins')):
                idx = low.find('/' + key)
                if idx >= 0:
                    plan.append((n, base / l[idx + len(key):]))
                    break
        return plan
    dlls = [n for n, l in lowered.items() if l.lower().endswith('.dll')]
    if not dlls:
        return []
    base_dir = os.path.dirname(lowered[dlls[0]])
    for n, l in lowered.items():
        if os.path.dirname(l).startswith(base_dir) if base_dir else True:
            rel = l[len(base_dir) + 1:] if base_dir else l
            if realm == 'SR1':
                folder = Path(dlls[0]).stem if not base_dir else Path(base_dir).name
                plan.append((n, mod_dir(realm, root) / folder / rel))
            elif rel.lower().endswith('.dll') and '/' not in rel:
                plan.append((n, mod_dir(realm, root) / rel))
            else:
                plan.append((n, mod_dir(realm, root) / rel))
    return plan


def remove(realm, root, key):
    """Move a mod's files out of the game (to SlimeBridge/removed-mods/<time>/); never deletes, never touches protected files."""
    root = Path(root)
    entry = next((m for m in installed(realm, root) if m['key'] == key), None)
    if entry is None:
        raise ModError('NOT_INSTALLED', key)
    if entry['protected']:
        raise ModError('PROTECTED', entry['name'])
    stash = root / 'SlimeBridge' / 'removed-mods' / time.strftime('%Y%m%d-%H%M%S')
    moved = []
    for rel in entry['files']:
        src = root / rel
        if not src.exists():
            continue
        dest = stash / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved.append(rel)
    data = _records(root)
    data.get(realm, {}).pop(key, None)
    _save_records(root, data)
    return {'removed': moved, 'kept_in': str(stash)}


# ---- download -------------------------------------------------------------------------------------------------------
def nexus_download(realm, mod_id, api_key, opener=urllib.request.urlopen):
    """Direct download (needs the player's Nexus API key on a Premium account). Returns the local file path."""
    domain = DOMAINS[realm]
    headers = {'apikey': api_key, 'User-Agent': 'SlimeBridge', 'Accept': 'application/json'}

    def get(url):
        with opener(urllib.request.Request(url, headers=headers), timeout=60) as response:
            return json.loads(response.read())
    try:
        files = get(f'{NEXUS_V1}/games/{domain}/mods/{int(mod_id)}/files.json').get('files', [])
        main = [f for f in files if f.get('category_name') == 'MAIN'] or files
        if not main:
            raise ModError('NO_FILE')
        chosen = max(main, key=lambda f: f.get('uploaded_timestamp') or 0)
        links = get(f'{NEXUS_V1}/games/{domain}/mods/{int(mod_id)}/files/{chosen["file_id"]}/download_link.json')
    except urllib.error.HTTPError as exc:
        raise ModError('NEEDS_PREMIUM' if exc.code == 403 else 'NEXUS_REFUSED', str(exc.code)) from exc
    except (OSError, ValueError) as exc:
        raise ModError('DOWNLOAD_FAILED', str(exc)) from exc
    if not links:
        raise ModError('NEEDS_PREMIUM')
    url = links[0]['URI']
    dest = Path(tempfile.mkdtemp(prefix='sb-mod-')) / chosen['file_name']
    with opener(urllib.request.Request(url, headers={'User-Agent': 'SlimeBridge'}), timeout=600) as response, open(dest, 'wb') as out:
        shutil.copyfileobj(response, out)
    return {'path': str(dest), 'version': chosen.get('version'), 'file': chosen.get('file_name')}


def downloads_folder() -> Path:
    return Path(os.environ.get('USERPROFILE') or Path.home()) / 'Downloads'


def newest_download(since, folder=None):
    """The newest .zip/.dll that appeared in the Downloads folder after ``since`` (complete, not still downloading)."""
    folder = Path(folder or downloads_folder())
    best = None
    try:
        for entry in folder.iterdir():
            if entry.suffix.lower() in ('.zip', '.dll') and entry.is_file():
                mtime = entry.stat().st_mtime
                if mtime >= since and (best is None or mtime > best.stat().st_mtime):
                    best = entry
    except OSError:
        return None
    if best is None or time.time() - best.stat().st_mtime < 2:          # still being written
        return None
    return best


_THUMBS: dict = {}
THUMB_HOSTS = ('staticdelivery.nexusmods.com', 'staticdelivery.nexus-cdn.com', 'images.nexusmods.com')


def thumbnail_data(url, opener=urllib.request.urlopen):
    """A small data: picture of a mod's thumbnail (Nexus image servers only), or None."""
    from urllib.parse import urlparse
    if url in _THUMBS:
        return _THUMBS[url]
    parts = urlparse(url or '')
    if parts.scheme != 'https' or not (parts.hostname or '').endswith(THUMB_HOSTS):
        return None
    try:
        import base64
        import io
        from PIL import Image
        with opener(urllib.request.Request(url, headers={'User-Agent': 'SlimeBridge'}), timeout=15) as response:
            raw = response.read(3_000_000)
        image = Image.open(io.BytesIO(raw)).convert('RGB')
        image.thumbnail((320, 180))
        out = io.BytesIO()
        image.save(out, 'JPEG', quality=82)
        data = 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode()
    except Exception:  # noqa: BLE001 - a missing thumbnail only shows the placeholder
        data = None
    _THUMBS[url] = data
    return data
