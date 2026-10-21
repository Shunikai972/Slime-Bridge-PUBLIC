"""Source-complete cache materialization for the conversion job (provider + idempotent step).

Wires the offline complete-source extractor (``bridge.content.sr2_asset_extract
--complete-source``, run through ``uv`` with pinned UnityPy / TypeTreeGeneratorAPI versions in
a clean child environment) into the conversion ecosystem as an ``AssetExtractionProvider``-shaped
provider plus an idempotent job step. A conversion job that wants the source-complete visual
path must carry an explicit, reviewed clip selection: clip IDs and the exact default clip name.
Without that selection the materializer REFUSES (``SelectionRequired``): no Animator controller
graph, default state or clip name is ever inferred.

The materializer is idempotent and fail-closed:

* a published cache is re-verified by re-hashing every manifest-covered file, refusing unlisted
  files and cross-checking the model / provenance / icon / dump-receipt bindings;
* a cache whose recorded identity (source file hashes, explicit selection, extractor
  version/pins) no longer matches the job is STALE and is regenerated;
* extraction stages into a fresh sibling directory, the staged build must verify and cover
  geometry (body + face), icon, every requested clip and the exact default name, and only then
  is it published with an atomic directory swap - a refused, incomplete or failed rebuild
  leaves the previous published cache untouched;
* the returned receipt hash-covers every file in the published cache (manifest included) and
  reports the build coverage, so an incomplete coverage is always visible.

Sources are read-only; outputs live only in this checkout's private ignored ``Lab/AssetCache``
or ``Lab/RuntimeArtifacts`` trees, never the canonical ``sr2-to-sr1`` cache. This module never
imports UnityPy.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from bridge.cache.fs import atomic_write, ensure_dir
from bridge.content.canonical import pretty_json, sha256_json
from bridge.content.sr2_asset_extract import (CACHE_SCHEMA as EXTRACTOR_CACHE_SCHEMA,
                                              CACHE_SCHEMA_VERSION as EXTRACTOR_CACHE_SCHEMA_VERSION,
                                              CONVERTER_VERSION as EXTRACTOR_CONVERTER_VERSION,
                                              validate_canonical)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTRACTOR_MODULE = 'bridge.content.sr2_asset_extract'
PYTHON_PIN = '3.12'
UNITYPY_PIN = 'UnityPy==1.25.4'
TYPETREE_PIN = 'TypeTreeGeneratorAPI==0.0.10'
SOURCE_FILE_NAMES = ('resources.assets', 'resources.assets.resS')
PRIVATE_ROOTS = ('AssetCache', 'RuntimeArtifacts')
DEFAULT_TIMEOUT = 1800

SOURCE_CACHE_VERSION = 1
JOB_SCHEMA = 'bridge-source-cache-job'
RECEIPT_SCHEMA = 'bridge-source-cache-receipt'
VERIFICATION_SCHEMA = 'bridge-source-cache-verification'
RECEIPT_STATUSES = ('BUILT', 'REBUILT', 'HIT')
RECEIPT_STABLE_KEYS = ('schema', 'schema_version', 'canonical_id', 'species', 'identity', 'job_hash',
                       'selection', 'manifest', 'files', 'clip_coverage', 'coverage', 'complete',
                       'extractor_output')

EXTRACTOR_PINS = {'module': EXTRACTOR_MODULE, 'python': PYTHON_PIN, 'converter_version': EXTRACTOR_CONVERTER_VERSION,
                  'pins': {'UnityPy': '1.25.4', 'TypeTreeGeneratorAPI': '0.0.10'}}

_POISONED_ENV = frozenset({'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONEXECUTABLE', 'VIRTUAL_ENV',
                           'CONDA_PREFIX', 'CONDA_DEFAULT_ENV', 'CONDA_SHLVL', 'UV_PROJECT_ENVIRONMENT',
                           'UV_PYTHON', 'PYTHONIOENCODING'})
_REPARSE = 0x400


class SelectionRequired(ValueError):
    """The job did not carry an explicit, reviewed clip selection: refuse, never infer."""

    code = 'SOURCE_CACHE_SELECTION_REQUIRED'


class SourceCacheError(RuntimeError):
    """The source-cache job could not produce (or prove) a verified publication."""


class SourceChanged(SourceCacheError):
    """A retried attempt's pinned source fingerprints no longer match the disk: fail closed."""

    code = 'SOURCE_CHANGED'


class SourceCacheIncomplete(SourceCacheError):
    """The staged (or published) build does not cover the requested explicit selection."""

    def __init__(self, message, coverage):
        self.coverage = coverage
        super().__init__(message)


def _normcase(value):
    return os.path.normcase(str(value))


def _utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_selection(clip_ids, default_clip):
    """Explicit-only selection: 1..16 distinct positive integer IDs plus a non-empty exact name."""
    if (not isinstance(clip_ids, (list, tuple)) or not 1 <= len(clip_ids) <= 16
            or any(type(pid) is not int or pid <= 0 for pid in clip_ids)
            or len(set(clip_ids)) != len(clip_ids)
            or not isinstance(default_clip, str) or not default_clip):
        raise SelectionRequired(
            'source-complete materialization requires explicit clip IDs (up to 16 distinct '
            'positive integers) and the exact default clip name; the Animator graph/default '
            'state is never inferred')
    return tuple(clip_ids)


def receipt_path(cache_dir):
    cache_dir = Path(cache_dir)
    return cache_dir.with_name(f'{cache_dir.name}.receipt.json')


def child_env(base=None):
    """Clean subprocess environment: no inherited Python/venv poisoning; DOTNET_ROOT preserved."""
    source = os.environ if base is None else base
    env = {}
    for key, value in source.items():
        upper = key.upper()
        if upper in _POISONED_ENV or upper.startswith('PYTEST'):
            continue
        env[key] = value
    env['PYTHONIOENCODING'] = 'utf-8'
    env['DOTNET_CLI_TELEMETRY_OPTOUT'] = '1'
    env.setdefault('DOTNET_NOLOGO', '1')
    if 'DOTNET_ROOT' not in env:
        dotnet = shutil.which('dotnet')
        if dotnet:
            env['DOTNET_ROOT'] = str(Path(dotnet).resolve().parent)
    return env


@dataclass(frozen=True)
class ExtractRequest:
    """One explicit complete-source extraction request handed to a runner."""
    species: str
    canonical_id: str
    sr2_dir: Path
    out_dir: Path
    clip_ids: tuple
    default_clip: str
    allow_nonlab: bool = False


@dataclass(frozen=True)
class SourceCacheJob:
    """A conversion job's explicit source-complete cache request (validated, absolute paths)."""
    canonical_id: str
    species: str
    sr2_dir: Path
    cache_dir: Path
    clip_ids: tuple
    default_clip: str
    allow_nonlab: bool = False

    @classmethod
    def from_mapping(cls, value):
        if not isinstance(value, dict):
            raise SourceCacheError('source-cache job must be a mapping')
        species = value.get('species')
        sr2_dir, cache_dir = value.get('sr2_dir'), value.get('cache_dir')
        if not isinstance(species, str) or not species.strip():
            raise SourceCacheError('source-cache job needs the source species name (e.g. Ringtail)')
        if not isinstance(sr2_dir, str) or not sr2_dir:
            raise SourceCacheError('source-cache job needs sr2_dir')
        if not isinstance(cache_dir, str) or not cache_dir:
            raise SourceCacheError('source-cache job needs cache_dir')
        clip_ids = validate_selection(value.get('clip_ids'), value.get('default_clip'))
        return cls(canonical_id=validate_canonical(value.get('canonical_id')), species=species.strip(),
                   sr2_dir=Path(os.path.abspath(sr2_dir)), cache_dir=Path(os.path.abspath(cache_dir)),
                   clip_ids=clip_ids, default_clip=value.get('default_clip'),
                   allow_nonlab=bool(value.get('allow_nonlab')))


def extractor_argv(request, uv='uv'):
    """The exact pinned extractor command; selection is always explicit, never derived."""
    argv = [uv, 'run', '--python', PYTHON_PIN, '--with', UNITYPY_PIN, '--with', TYPETREE_PIN,
            'python', '-m', EXTRACTOR_MODULE,
            '--species', request.species, '--canonical', request.canonical_id,
            '--sr2-dir', str(request.sr2_dir), '--out', str(request.out_dir), '--complete-source']
    for clip_id in request.clip_ids:
        argv += ['--clip-id', str(clip_id)]
    argv += ['--default-clip', request.default_clip]
    if request.allow_nonlab:
        argv.append('--allow-nonlab')
    return argv


def default_runner(request, *, uv='uv', timeout=DEFAULT_TIMEOUT):
    """Run the pinned extraction subprocess with a clean environment; return its JSON summary."""
    argv = extractor_argv(request, uv=uv)
    try:
        completed = subprocess.run(argv, cwd=str(REPO_ROOT), env=child_env(), capture_output=True,
                                   text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except OSError as exc:
        raise SourceCacheError(f'cannot launch the extractor subprocess ({argv[0]}): {exc}') from exc
    if completed.returncode != 0:
        tail = ' '.join((completed.stderr or completed.stdout or '').split())[-500:]
        raise SourceCacheError(f'extractor subprocess failed (exit {completed.returncode}): {tail}')
    try:
        summary = json.loads(completed.stdout)
    except ValueError as exc:
        raise SourceCacheError(f'extractor subprocess produced no JSON summary: {exc}') from exc
    if not isinstance(summary, dict) or summary.get('ok') is not True:
        raise SourceCacheError(f'extractor subprocess reported failure: {str(summary)[:400]}')
    return summary


def _remove(path):
    path = Path(path)
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError:
        pass


def _scan_cache(cache_dir):
    """Self-consistency scan of a published cache: every file hash-covered, nothing unlisted."""
    cache_dir = Path(cache_dir)
    result = {'problems': [], 'files': {}, 'manifest': None, 'model': None, 'provenance': None,
              'icon': None, 'dump_receipt': None}
    problems = result['problems']
    if not cache_dir.is_dir():
        problems.append('CACHE_NOT_A_DIRECTORY')
        return result
    manifest_path = cache_dir / 'cache-manifest.json'
    raw = None
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode('utf-8'))
    except OSError:
        manifest = None
        problems.append('MANIFEST_MISSING')
    except ValueError:
        manifest = None
        problems.append('MANIFEST_UNREADABLE')
    if manifest is not None and not isinstance(manifest, dict):
        manifest = None
        problems.append('MANIFEST_UNREADABLE')
    result['manifest'] = manifest
    if manifest is None:
        return result
    if manifest.get('schema') != EXTRACTOR_CACHE_SCHEMA:
        problems.append(f"MANIFEST_SCHEMA:{manifest.get('schema')}")
    if manifest.get('schema_version') != EXTRACTOR_CACHE_SCHEMA_VERSION:
        problems.append('MANIFEST_SCHEMA_VERSION')
    file_map = manifest.get('files')
    if not isinstance(file_map, dict):
        problems.append('MANIFEST_FILES_INVALID')
        file_map = {}
    for name in sorted(file_map, key=str):
        digest = file_map[name]
        if (not isinstance(name, str) or not name or '/' in name or '\\' in name
                or name in ('.', '..') or not isinstance(digest, str)):
            problems.append(f'MANIFEST_ENTRY_INVALID:{name}')
            continue
        path = cache_dir / name
        if path.is_symlink() or not path.is_file():
            problems.append(f'FILE_MISSING:{name}')
            continue
        actual = sha256_file(path)
        result['files'][name] = actual
        if actual != digest:
            problems.append(f'FILE_HASH_MISMATCH:{name}')
    unlisted = sorted({p.name for p in cache_dir.iterdir()} - set(file_map) - {'cache-manifest.json'})
    for name in unlisted:
        problems.append(f'UNLISTED:{name}')
    result['files']['cache-manifest.json'] = hashlib.sha256(raw).hexdigest()
    for name, key in (('model.json', 'model'), ('source-animation-provenance.json', 'provenance'),
                      ('icon-descriptor.json', 'icon')):
        try:
            result[key] = json.loads((cache_dir / name).read_text(encoding='utf-8'))
        except OSError:
            problems.append(f'{key.upper()}_MISSING')
        except ValueError:
            problems.append(f'{key.upper()}_UNREADABLE')
    try:
        result['dump_receipt'] = json.loads((cache_dir / 'dump-receipt.json').read_text(encoding='utf-8'))
    except OSError:
        problems.append('DUMP_RECEIPT_MISSING')
    except ValueError:
        problems.append('DUMP_RECEIPT_UNREADABLE')
    model, provenance, icon = result['model'], result['provenance'], result['icon']
    if isinstance(model, dict) and (model.get('schema') != 'bridge-native-mesh'
                                    or model.get('schema_version') != 2):
        problems.append('MODEL_SCHEMA')
    if isinstance(provenance, dict) and provenance.get('selection') != 'explicit-source-clip-ids':
        problems.append('PROVENANCE_SELECTION_NOT_EXPLICIT')
    if isinstance(icon, dict):
        icon_name = icon.get('file')
        if not isinstance(icon_name, str) or icon_name not in file_map:
            problems.append('ICON_NOT_IN_MANIFEST')
    if isinstance(result['dump_receipt'], dict) and result['files'].get('model.json'):
        if result['dump_receipt'].get('model_sha256') != result['files']['model.json']:
            problems.append('MODEL_RECEIPT_MISMATCH')
    return result


def _coverage(scan, clip_ids, default_clip):
    """What the build actually covers: geometry parts, icon, textures, materials, exact selection."""
    problems = []
    manifest = scan.get('manifest') or {}
    model = scan.get('model') or {}
    provenance = scan.get('provenance') or {}
    icon = scan.get('icon') or {}
    renderers = [row for row in (model.get('skinned_renderers') or []) if isinstance(row, dict)] + \
                [row for row in (model.get('static_renderers') or []) if isinstance(row, dict)]
    roles = [row.get('role') for row in renderers]
    if 'body' not in roles:
        problems.append('NO_BODY_PART')
    if 'face' not in roles:
        problems.append('NO_FACE_PART')
    icon_name = icon.get('file')
    if not isinstance(icon_name, str) or icon_name not in (manifest.get('files') or {}):
        problems.append('NO_ICON')
    textures = sorted(name for name in (manifest.get('files') or {}) if str(name).endswith('.png'))
    if not textures:
        problems.append('NO_TEXTURES')
    material_names = []
    for row in renderers:
        for entry in (row.get('materials') or []):
            name = entry if isinstance(entry, str) else (entry.get('name') if isinstance(entry, dict) else None)
            if name:
                material_names.append(name)
    materials = sorted(set(material_names))
    if not materials:
        problems.append('NO_MATERIALS')
    clips = [row for row in (provenance.get('clips') or []) if isinstance(row, dict)]
    published = [{'source_path_id': row.get('source_path_id'), 'name': row.get('name')} for row in clips]
    if [row['source_path_id'] for row in published] != list(clip_ids):
        problems.append('CLIP_SELECTION_MISMATCH')
    names = [row['name'] for row in published]
    model_names = [row.get('name') for row in ((model.get('source_animation') or {}).get('clips') or [])
                   if isinstance(row, dict)]
    if model_names != names:
        problems.append('CLIP_NAMES_MISMATCH')
    if default_clip not in names:
        problems.append('DEFAULT_CLIP_NOT_DECODED')
    return {'complete': not problems, 'problems': problems, 'parts': roles, 'requested': list(clip_ids),
            'clips': published, 'default_clip': default_clip, 'textures': textures, 'materials': materials,
            'vertices': sum(len(row.get('vertices') or []) for row in renderers)}


def _identity_problems(scan, identity):
    """Which recorded facts no longer match the current job (staleness reasons)."""
    changed = []
    manifest = scan.get('manifest') or {}
    if manifest.get('canonical_id') != identity['canonical_id']:
        changed.append('CANONICAL_MISMATCH')
    if manifest.get('converter_version') != identity['extractor']['converter_version']:
        changed.append(f"CONVERTER_VERSION_CHANGED:{manifest.get('converter_version')}")
    provenance = scan.get('provenance') or {}
    recorded = provenance.get('source_files')
    if not isinstance(recorded, dict):
        changed.append('SOURCE_FILES_UNRECORDED')
    elif {_normcase(k): v for k, v in recorded.items()} != {_normcase(k): v for k, v in identity['source_files'].items()}:
        changed.append('SOURCE_FILES_CHANGED')
    clips = [row for row in (provenance.get('clips') or []) if isinstance(row, dict)]
    if [row.get('source_path_id') for row in clips] != list(identity['clip_ids']):
        changed.append('CLIP_SELECTION_CHANGED')
    model = scan.get('model') or {}
    if ((model.get('source_animation') or {}).get('default_clip')) != identity['default_clip']:
        changed.append('DEFAULT_CLIP_CHANGED')
    return changed


class SourceCacheProvider:
    """Provider-shaped materializer (``extract(request) -> receipt``) for the complete source cache."""

    def __init__(self, *, runner=None, repo_root=REPO_ROOT, timeout=DEFAULT_TIMEOUT):
        self.runner = runner
        self.repo_root = Path(repo_root)
        self.timeout = timeout

    def _runner(self):
        return self.runner or default_runner

    # -- job surface ---------------------------------------------------------------

    def extract(self, request):
        """AssetExtractionProvider-compatible surface: request mapping -> verified receipt."""
        return self.materialize(request)

    def materialize(self, request):
        """Idempotent: HIT on a verified matching publication, BUILT fresh, REBUILT when stale."""
        job = request if isinstance(request, SourceCacheJob) else SourceCacheJob.from_mapping(request)
        started = _utc_now()
        self._check_source(job)
        cache_dir = self._check_cache_dir(job.cache_dir)
        source_files = self._source_fingerprint(job.sr2_dir)
        identity = self._identity(job, source_files)
        job_hash = sha256_json(identity)
        state = self._inspect(job, identity, job_hash)
        if state['status'] == 'PUBLISHED':
            scan, coverage = state['scan'], state['coverage']
            stored, _ = self._read_receipt(cache_dir)
            output = (stored or {}).get('extractor_output')
            receipt = self._receipt(job, cache_dir, identity, job_hash, 'HIT', scan['files'], scan['manifest'],
                                    coverage, output, started)
            self._write_receipt(cache_dir, receipt)
            return receipt
        status = 'BUILT' if state['status'] == 'MISSING' else 'REBUILT'
        return self._build(job, cache_dir, identity, job_hash, status, started)

    def verify(self, request):
        """Verification report for a job (PUBLISHED / MISSING / STALE / BROKEN) without building."""
        return verify_source_cache(request, repo_root=self.repo_root)

    # -- preflight -----------------------------------------------------------------

    def _check_source(self, job):
        if not job.sr2_dir.is_dir():
            raise SourceCacheError(f'SR2 data dir not found: {job.sr2_dir}')
        if not job.allow_nonlab:
            try:
                job.sr2_dir.resolve().relative_to((self.repo_root / 'Lab').resolve())
            except ValueError:
                raise SourceCacheError('untrusted SR2 data dir outside the repository Lab tree '
                                       '(pass allow_nonlab to read a user-owned install read-only)') from None

    def _private_root(self, cache_dir):
        for name in PRIVATE_ROOTS:
            root = self.repo_root / 'Lab' / name
            if Path(cache_dir).is_relative_to(root):
                return root
        raise SourceCacheError('cache directory is not inside a private Lab root')

    def _check_cache_dir(self, cache_dir):
        raw = Path(str(cache_dir))
        if '..' in raw.parts:
            raise SourceCacheError('cache path traversal refused')
        candidate = Path(os.path.abspath(str(cache_dir)))
        if 'sr2-to-sr1' in candidate.parts:
            raise SourceCacheError('the canonical sr2-to-sr1 cache is never materialized through this path')
        roots = [self.repo_root / 'Lab' / name for name in PRIVATE_ROOTS]
        if not any(candidate.is_relative_to(root) and candidate != root for root in roots):
            raise SourceCacheError('cache directory must be a NEW private Lab directory under '
                                   'Lab/AssetCache or Lab/RuntimeArtifacts')
        for part in (candidate, *candidate.parents):
            if part == self.repo_root:
                break
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & _REPARSE):
                raise SourceCacheError(f'cache path chain contains a link/reparse point: {part}')
        return candidate

    def _source_fingerprint(self, sr2_dir):
        out = {}
        for name in SOURCE_FILE_NAMES:
            path = sr2_dir / name
            if not path.is_file():
                raise SourceCacheError(f'SR2 source file missing: {path}')
            out[str(path)] = sha256_file(path)
        return out

    def _identity(self, job, source_files):
        return {'schema': JOB_SCHEMA, 'schema_version': 1, 'canonical_id': job.canonical_id,
                'species': job.species, 'clip_ids': list(job.clip_ids), 'default_clip': job.default_clip,
                'sr2_dir': _normcase(job.sr2_dir), 'source_files': dict(sorted(source_files.items())),
                'extractor': EXTRACTOR_PINS, 'source_cache_version': SOURCE_CACHE_VERSION}

    # -- state ---------------------------------------------------------------------

    def _read_receipt(self, cache_dir):
        path = receipt_path(cache_dir)
        try:
            raw = path.read_bytes()
        except OSError:
            return None, 'MISSING'
        try:
            doc = json.loads(raw.decode('utf-8'))
        except ValueError:
            return None, 'UNREADABLE'
        if not isinstance(doc, dict) or doc.get('schema') != RECEIPT_SCHEMA or doc.get('schema_version') != 1:
            return None, 'UNREADABLE'
        return doc, None

    def _inspect(self, job, identity, job_hash):
        cache_dir = job.cache_dir
        if not cache_dir.exists():
            return {'status': 'MISSING', 'problems': [], 'changed': ['NO_PREVIOUS_CACHE']}
        scan = _scan_cache(cache_dir)
        if scan['problems']:
            return {'status': 'BROKEN', 'problems': scan['problems'], 'changed': ['UNVERIFIED_PUBLICATION']}
        changed = _identity_problems(scan, identity)
        receipt, error = self._read_receipt(cache_dir)
        if error == 'MISSING':
            changed.append('RECEIPT_MISSING')
        elif error == 'UNREADABLE':
            changed.append('RECEIPT_UNREADABLE')
        elif receipt.get('job_hash') != job_hash:
            changed.append('RECEIPT_JOB_CHANGED')
        elif receipt.get('files') != scan['files']:
            changed.append('RECEIPT_FILES_CHANGED')
        if changed:
            return {'status': 'STALE', 'problems': [], 'changed': changed}
        coverage = _coverage(scan, job.clip_ids, job.default_clip)
        if not coverage['complete']:
            return {'status': 'STALE', 'problems': [], 'changed': ['COVERAGE_INCOMPLETE'] + coverage['problems'],
                    'scan': scan, 'coverage': coverage}
        return {'status': 'PUBLISHED', 'problems': [], 'changed': [], 'scan': scan, 'coverage': coverage}

    # -- build / publish -----------------------------------------------------------

    def _check_runner_output(self, output, staging):
        if not isinstance(output, dict) or output.get('ok') is not True:
            raise SourceCacheError(f'extractor subprocess reported failure: {str(output)[:400]}')
        claimed = output.get('cache_directory')
        if claimed is not None and _normcase(Path(str(claimed))) != _normcase(staging):
            raise SourceCacheError('extractor subprocess wrote to an unexpected directory')

    def _build(self, job, cache_dir, identity, job_hash, status, started):
        root = self._private_root(cache_dir)
        ensure_dir(root, cache_dir.parent)
        staging = cache_dir.parent / f'.{cache_dir.name}.staging-{uuid.uuid4().hex}'
        request = ExtractRequest(species=job.species, canonical_id=job.canonical_id, sr2_dir=job.sr2_dir,
                                 out_dir=staging, clip_ids=job.clip_ids, default_clip=job.default_clip,
                                 allow_nonlab=job.allow_nonlab)
        try:
            output = self._runner()(request)
        except SourceCacheError:
            _remove(staging)
            raise
        except Exception as exc:
            _remove(staging)
            raise SourceCacheError(f'extraction failed: {type(exc).__name__}: {str(exc)[:300]}') from exc
        try:
            self._check_runner_output(output, staging)
            scan = _scan_cache(staging)
            if scan['problems']:
                raise SourceCacheError('staged extraction failed verification: ' + '; '.join(scan['problems'][:6]))
            changed = _identity_problems(scan, identity)
            if changed:
                raise SourceCacheError('staged extraction does not match the job: ' + '; '.join(changed[:6]))
            coverage = _coverage(scan, job.clip_ids, job.default_clip)
            if not coverage['complete']:
                raise SourceCacheIncomplete('incomplete source-cache coverage: '
                                            + '; '.join(coverage['problems']), coverage)
        except BaseException:
            _remove(staging)
            raise
        try:
            self._publish(staging, cache_dir)
        except BaseException:
            _remove(staging)
            raise
        summary = dict(output)
        summary['cache_directory'] = str(cache_dir)
        receipt = self._receipt(job, cache_dir, identity, job_hash, status, scan['files'], scan['manifest'],
                                coverage, summary, started)
        self._write_receipt(cache_dir, receipt)
        return receipt

    def _publish(self, staging, cache_dir):
        superseded = None
        if cache_dir.exists() or cache_dir.is_symlink():
            superseded = cache_dir.parent / f'.{cache_dir.name}.superseded-{uuid.uuid4().hex}'
            os.rename(cache_dir, superseded)
        try:
            os.rename(staging, cache_dir)
        except OSError:
            if superseded is not None and not cache_dir.exists():
                os.rename(superseded, cache_dir)
            raise
        if superseded is not None:
            _remove(superseded)

    def _receipt(self, job, cache_dir, identity, job_hash, status, files, manifest, coverage, output, started):
        manifest = manifest or {}
        receipt = {'schema': RECEIPT_SCHEMA, 'schema_version': 1, 'status': status,
                   'canonical_id': job.canonical_id, 'species': job.species, 'cache_directory': str(cache_dir),
                   'identity': identity, 'job_hash': job_hash,
                   'selection': {'clip_ids': list(job.clip_ids), 'default_clip': job.default_clip},
                   'manifest': {key: manifest.get(key) for key in
                                ('schema', 'schema_version', 'converter_version', 'source_game')},
                   'files': dict(sorted(files.items())),
                   'clip_coverage': {'requested': list(job.clip_ids), 'published': coverage['clips'],
                                     'default_clip': job.default_clip},
                   'coverage': coverage, 'complete': bool(coverage['complete']),
                   'extractor_output': output, 'started_utc': started, 'finished_utc': _utc_now()}
        receipt['receipt_hash'] = sha256_json({key: receipt[key] for key in RECEIPT_STABLE_KEYS})
        return receipt

    def _write_receipt(self, cache_dir, receipt):
        root = self._private_root(cache_dir)
        atomic_write(root, receipt_path(cache_dir), pretty_json(receipt).encode('utf-8'))


def materialize_source_cache(request, *, runner=None, repo_root=REPO_ROOT, timeout=DEFAULT_TIMEOUT):
    """Idempotent entry point: materialize one source-complete cache slot from an explicit job."""
    return SourceCacheProvider(runner=runner, repo_root=repo_root, timeout=timeout).materialize(request)


def verify_source_cache(request, *, repo_root=REPO_ROOT):
    """Verification report for a slot: MISSING / BROKEN / STALE / PUBLISHED (with reasons)."""
    provider = SourceCacheProvider(repo_root=repo_root)
    job = request if isinstance(request, SourceCacheJob) else SourceCacheJob.from_mapping(request)
    provider._check_source(job)
    cache_dir = provider._check_cache_dir(job.cache_dir)
    source_files = provider._source_fingerprint(job.sr2_dir)
    identity = provider._identity(job, source_files)
    state = provider._inspect(job, identity, sha256_json(identity))
    report = {'schema': VERIFICATION_SCHEMA, 'schema_version': 1, 'canonical_id': job.canonical_id,
              'cache_directory': str(cache_dir), 'status': state['status'], 'problems': state['problems'],
              'changed': state['changed']}
    if state.get('scan'):
        report['files'] = state['scan']['files']
        report['coverage'] = state.get('coverage')
    return report
