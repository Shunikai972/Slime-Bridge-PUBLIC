"""Cross-game Largo models that no game contains: catalogue, optional download, local install.

The 24 designs are the combinations of the three SR1-only Largo-able species (Rad, Quantum, Mosaic) with the eight
SR2-only ones. Only names live here: the models themselves are made by the author, published as a manifest
(``slimebridge-largo-models`` v1) that the player points the launcher at, and downloaded only when the player clicks.

Download rules: https only (plain http solely for loopback tests), a hard size cap, SHA-256 from the manifest checked
before anything is unpacked, a file-type allowlist, no path escaping, atomic install, nothing executable ever.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCHEMA = 'slimebridge-largo-models'
# the MODELS branch release of SlimeBridge (the author's finished largos, adapted for both games)
DEFAULT_MANIFEST = 'https://github.com/Shunikai972/Slime-Bridge-PUBLIC/releases/download/largos/largo-models.json'
SR1_ONLY = ('rad', 'quantum', 'mosaic')
SR2_ONLY = ('slumber', 'cotton', 'flutter', 'angler', 'batty', 'ringtail', 'hyper', 'twin')
DISPLAY = {'rad': 'Rad', 'quantum': 'Quantum', 'mosaic': 'Mosaic', 'slumber': 'Sloomber', 'cotton': 'Cotton',
           'flutter': 'Flutter', 'angler': 'Angler', 'batty': 'Batty', 'ringtail': 'Ringtail', 'hyper': 'Hyper',
           'twin': 'Twin'}
MAX_MANIFEST = 1_000_000
MAX_DOWNLOAD = 400_000_000
MAX_UNPACKED = 900_000_000
MAX_FILES = 400
ALLOWED_SUFFIXES = {'.glb', '.gltf', '.bin', '.png', '.jpg', '.jpeg', '.json', '.txt', '.md', '.ogg', '.wav', '.sbmesh'}
_ID = re.compile(r'^[a-z0-9_]{3,40}$')
_HEX = re.compile(r'^[0-9a-f]{64}$')


def catalogue():
    return [{'id': f'{a}_{b}', 'parents': [a, b], 'name': f'{DISPLAY[a]} × {DISPLAY[b]}',
             'family': a, 'partner': b} for a in SR1_ONLY for b in SR2_ONLY]


class ModelError(ValueError):
    pass


def validate_manifest(doc):
    if not isinstance(doc, dict) or doc.get('schema') != SCHEMA or doc.get('schema_version') != 1:
        raise ModelError(f'not a {SCHEMA} v1 manifest')
    known = {m['id'] for m in catalogue()}
    entries, seen = [], set()
    for raw in doc.get('models') or []:
        if not isinstance(raw, dict):
            raise ModelError('model entries must be objects')
        mid = raw.get('id')
        if not isinstance(mid, str) or not _ID.match(mid) or mid not in known:
            raise ModelError(f'unknown model id {mid!r}')
        if mid in seen:
            raise ModelError(f'duplicate model id {mid}')
        seen.add(mid)
        url, digest, size = raw.get('url'), raw.get('sha256'), raw.get('size')
        if not isinstance(url, str) or not _url_allowed(url):
            raise ModelError(f'{mid}: url must be https')
        if not isinstance(digest, str) or not _HEX.match(digest):
            raise ModelError(f'{mid}: sha256 must be 64 lowercase hex characters')
        if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD:
            raise ModelError(f'{mid}: size must be 1..{MAX_DOWNLOAD} bytes')
        version = raw.get('version')
        if not isinstance(version, str) or not re.match(r'^[0-9A-Za-z._-]{1,32}$', version):
            raise ModelError(f'{mid}: bad version')
        entries.append({'id': mid, 'url': url, 'sha256': digest, 'size': size, 'version': version})
    return {'models': entries, 'name': str(doc.get('name', ''))[:80], 'updated': str(doc.get('updated', ''))[:32]}


def _url_allowed(url):
    parts = urlparse(url)
    if parts.scheme == 'https' and parts.hostname:
        return True
    return parts.scheme == 'http' and parts.hostname in ('127.0.0.1', 'localhost', '::1')


class ModelStore:
    def __init__(self, root, opener=urlopen):
        self.root = Path(root)
        self._open = opener

    def installed(self):
        out = {}
        if not self.root.is_dir():
            return out
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir() and _ID.match(p.name)):
            try:
                out[folder.name] = json.loads((folder / '.sb-install.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
        return out

    def rows(self, manifest=None):
        published = {m['id']: m for m in (manifest or {}).get('models', [])}
        installed = self.installed()
        rows = []
        for item in catalogue():
            entry, local = published.get(item['id']), installed.get(item['id'])
            if local and (not entry or entry['version'] == local.get('version')):
                state = 'INSTALLED'
            elif local and entry:
                state = 'UPDATE'
            elif entry:
                state = 'AVAILABLE'
            else:
                state = 'NOT_PUBLISHED'
            rows.append({**item, 'state': state, 'version': (entry or local or {}).get('version'),
                         'size': (entry or {}).get('size')})
        return rows

    def fetch_manifest(self, url):
        if not _url_allowed(url):
            raise ModelError('manifest url must be https')
        request = Request(url, headers={'User-Agent': 'SlimeBridge-Launcher', 'Accept': 'application/json'})
        with self._open(request, timeout=15) as response:
            data = response.read(MAX_MANIFEST + 1)
        if len(data) > MAX_MANIFEST:
            raise ModelError('manifest too large')
        try:
            return validate_manifest(json.loads(data.decode('utf-8')))
        except (UnicodeError, ValueError) as exc:
            if isinstance(exc, ModelError):
                raise
            raise ModelError('manifest is not valid JSON') from exc

    def download(self, entry, progress=None, cancelled=lambda: False):
        """Fetch ``entry`` (a validated manifest entry), verify SHA-256, unpack safely, install atomically."""
        self.root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix='.dl-', dir=self.root))
        try:
            archive = workdir / 'model.zip'
            digest, total = hashlib.sha256(), 0
            request = Request(entry['url'], headers={'User-Agent': 'SlimeBridge-Launcher'})
            with self._open(request, timeout=30) as response, archive.open('wb') as out:
                while True:
                    if cancelled():
                        raise InterruptedError('cancelled')
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > min(MAX_DOWNLOAD, entry['size'] + 1024):
                        raise ModelError('download larger than announced')
                    digest.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(total / entry['size'])
            if digest.hexdigest() != entry['sha256']:
                raise ModelError('SHA-256 mismatch: the file is not the one the manifest announced')
            staging = workdir / 'unpacked'
            written = _safe_unzip(archive, staging)
            meta = {'id': entry['id'], 'version': entry['version'], 'sha256': entry['sha256'], 'files': written,
                    'source': 'download'}
            (staging / '.sb-install.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
            target = self.root / entry['id']
            old = self.root / f'.old-{entry["id"]}-{uuid.uuid4().hex[:6]}'
            if target.exists():
                os.replace(target, old)
            os.replace(staging, target)
            shutil.rmtree(old, ignore_errors=True)
            return meta
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def remove(self, model_id):
        if not _ID.match(model_id):
            raise ModelError('bad model id')
        shutil.rmtree(self.root / model_id, ignore_errors=True)
        return {'removed': model_id}


def _safe_unzip(archive, destination):
    destination = Path(destination)
    destination.mkdir(parents=True)
    root = destination.resolve()
    written = 0
    with zipfile.ZipFile(archive) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise ModelError('too many files in the archive')
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ModelError('archive unpacks to more than the allowed size')
        for info in infos:
            name = info.filename.replace('\\', '/')
            if name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '..' in name.split('/'):
                raise ModelError(f'unsafe path in archive: {info.filename!r}')
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ModelError(f'links are not allowed in archives: {info.filename!r}')
            if Path(name).suffix.lower() not in ALLOWED_SUFFIXES:
                raise ModelError(f'file type not allowed: {Path(name).suffix or name!r}')
            target = (root / name).resolve()
            if root not in target.parents:
                raise ModelError(f'unsafe path in archive: {info.filename!r}')
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, target.open('wb') as out:
                shutil.copyfileobj(source, out, 1 << 16)
            written += 1
    return written


class DownloadJob:
    """Background download of one or several models; poll ``snapshot`` from the UI."""

    def __init__(self, store, entries):
        self.id = uuid.uuid4().hex[:12]
        self.store, self.entries = store, list(entries)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.state = {'status': 'QUEUED', 'index': 0, 'total': len(self.entries), 'current': None, 'progress': 0.0,
                      'done': [], 'errors': []}
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name=f'models-{self.id}')
        self._thread.start()
        return self

    def join(self, timeout=None):
        if self._thread:
            self._thread.join(timeout)

    def cancel(self):
        self._cancel.set()

    def snapshot(self):
        with self._lock:
            return {'id': self.id, **json.loads(json.dumps(self.state))}

    def _update(self, **changes):
        with self._lock:
            self.state.update(changes)

    def _run(self):
        self._update(status='RUNNING')
        for index, entry in enumerate(self.entries):
            if self._cancel.is_set():
                self._update(status='CANCELLED')
                return
            self._update(index=index, current=entry['id'], progress=index / max(1, len(self.entries)))
            span = 1 / max(1, len(self.entries))

            def report(fraction, base=index * span, span=span):
                self._update(progress=min(1.0, base + span * min(1.0, fraction)))

            try:
                meta = self.store.download(entry, progress=report, cancelled=self._cancel.is_set)
                with self._lock:
                    self.state['done'].append(meta['id'])
            except InterruptedError:
                self._update(status='CANCELLED')
                return
            except (ModelError, OSError, zipfile.BadZipFile) as exc:
                with self._lock:
                    self.state['errors'].append({'id': entry['id'], 'error': str(exc)[:300]})
        self._update(status='DONE' if not self.state['errors'] else 'FAILED', progress=1.0, current=None)
