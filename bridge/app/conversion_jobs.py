"""Qt-free jobs around the real source-cache CLI. No game launches or automatic conversion.

Each attempt builds a fresh generation. Only a verified successful result can change the selected
disk cache (current.json). Cancelled/failed attempts cannot replace a usable cache.
"""
import json
import os
import re
import sys
import uuid
from collections import deque
from pathlib import Path

from bridge.app import installations as I
from bridge.app.species import parity_report
from bridge.cache.fs import atomic_write, confined_root, ensure_dir
from bridge.conversion import source_cache as sc, species_parity

LOG_BYTES = 65536
JOB_STATES = ('NOT_STARTED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'REFUSED', 'CANCELLED')
ACTIVE = ('QUEUED', 'RUNNING')
STAGES = ('SOURCE_VALIDATION', 'SOURCE_FINGERPRINT', 'CACHE_INSPECTION', 'SOURCE_EXTRACTION',
          'CACHE_VERIFICATION', 'CACHE_PUBLICATION', 'RECEIPT_WRITE')
# Explicit reviewed selections, NOT inferred Animator defaults or clip IDs. Metadata receipts:
# docs/reviews/{ringtail-source-animation,cotton-source-runtime,angler-source-runtime}.json.
CLIP_CONFIGS = {
    'slime.ringtail': ('Ringtail', (2170, 2187, 2165, 2188), 'Idle_Cotton_Ringtail'),
    'slime.cotton': ('Cotton', (2170, 2187, 2165, 2188), 'Idle_Cotton_Ringtail'),
    'slime.angler': ('Angler', (2187, 2165, 2188), 'Idle v4'),
}


class ConversionRefused(ValueError):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(f'{code}: {reason}')


def conversion_catalogue(*, classification=None, parity_path=None):
    """Exclusive roster from validated parity/classification or its documented prepared roster.

    The documentation fallback is labelled, not runtime registry evidence. Invalid caller-supplied
    captures fail closed; they are never replaced with the fallback.
    """
    if parity_path is not None:
        report, diags = parity_report(parity_path)
        if report is None:
            raise ConversionRefused('PARITY_INVALID', '; '.join(d['message'] for d in diags))
        roster, evidence = report['roster'], 'VALIDATED_PARITY'
    elif classification is not None:
        roster, evidence = species_parity.derive_roster(classification), 'CAPTURED_CLASSIFICATION'
    else:
        text = (sc.REPO_ROOT / 'docs' / 'SPECIES_ADAPTER.md').read_text(encoding='utf-8')
        roster = {'sr1_only': [], 'sr2_only': [], 'names': {}}
        for source, target, cells in re.findall(r'^\| (SR[12]) -> (SR[12]) \(\d+\) \| (.+) \|$', text, re.M):
            roster['sr1_only' if source == 'SR1' else 'sr2_only'] = re.findall(r'`(slime\.[a-z_]+)`', cells)
        evidence = 'DOCUMENTED_PARITY_ROSTER'
    rows = []
    for bucket, source, target in (('sr1_only', 'SR1', 'SR2'), ('sr2_only', 'SR2', 'SR1')):
        for cid in sorted(roster[bucket]):
            config = CLIP_CONFIGS.get(cid) if source == 'SR2' else None
            reason = ('SR1 source-cache extraction is not implemented by the current CLI' if source == 'SR1'
                      else 'No reviewed explicit clip IDs and exact default clip are configured for this species')
            rows.append({'canonical_id': cid, 'name': roster.get('names', {}).get(cid) or cid[6:].replace('_', ' ').title(),
                         'source': source, 'target': target, 'supported': config is not None,
                         'reason': '' if config else reason, 'roster_evidence': evidence,
                         'clip_ids': list(config[1]) if config else [], 'default_clip': config[2] if config else None})
    return rows


def cache_state(request, *, repo_root=sc.REPO_ROOT):
    """Re-read the existing engine's manifest/hash/identity verifier; never use a memory verdict."""
    try:
        report = sc.verify_source_cache(request, repo_root=repo_root)
    except (ValueError, OSError, sc.SourceCacheError) as exc:
        return {'state': 'BROKEN', 'reason': str(exc), 'report': None}
    state = {'PUBLISHED': 'READY', 'MISSING': 'ABSENT'}.get(report['status'], report['status'])
    reasons = report['problems'] + report['changed']
    return {'state': state, 'reason': '; '.join(reasons) or 'Verified local manifest and file hashes', 'report': report}


class _SpeciesLock:
    """OS lock released on owner death; never delete a potentially held lock file."""
    def __init__(self, path):
        self.file = open(path, 'a+b')
        try:
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                if path.stat().st_size == 0:
                    self.file.write(b'0')
                    self.file.flush()
                    self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ConversionRefused('JOB_ALREADY_ACTIVE', 'Another launcher owns this species job') from None

    def close(self):
        self.file.close()


class ConversionJobs:
    """Explicit queue with actual process events. ONLY the runner/process boundary is injectable."""
    def __init__(self, *, root=None, runner=None, classification=None, parity_path=None):
        self.root = confined_root(root or sc.REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'launcher-conversion')
        if not any(self.root.is_relative_to(sc.REPO_ROOT / 'Lab' / n) and self.root != sc.REPO_ROOT / 'Lab' / n
                   for n in sc.PRIVATE_ROOTS):
            raise ConversionRefused('OUTPUT_REFUSED', 'Outputs must stay in this checkout private Lab')
        self.rows = conversion_catalogue(classification=classification, parity_path=parity_path)
        self.runner = runner or SubprocessRunner()
        self._jobs, self._queue, self._running = {}, deque(), None

    def job(self, cid):
        item = self._jobs.get(cid)
        if item is None:
            return {'canonical_id': cid, 'state': 'NOT_STARTED', 'stage': None, 'code': None,
                    'reason': '', 'exit_code': None, 'events': [], 'log_path': None, 'attempt': 0}
        return {k: v for k, v in item.items() if not k.startswith('_')}

    def _row(self, cid):
        row = next((r for r in self.rows if r['canonical_id'] == cid), None)
        if row is None or not row['supported']:
            raise ConversionRefused('UNSUPPORTED', row['reason'] if row else 'Not in the exclusive parity roster')
        return row

    def _request(self, cid, source, cache):
        self._row(cid)
        _, diags, _ = I.assess(source)
        if any(d['severity'] == 'error' for d in diags) or source.get('realm') != 'SR2':
            raise ConversionRefused('SOURCE_REFUSED', 'Select a detected SR2 installation; the CLI does not extract SR1')
        name, ids, default = CLIP_CONFIGS[cid]
        return sc.SourceCacheJob.from_mapping({'canonical_id': cid, 'species': name,
            'sr2_dir': str(Path(source['root']) / 'SlimeRancher2_Data'), 'cache_dir': str(cache),
            'clip_ids': list(ids), 'default_clip': default, 'allow_nonlab': True})

    def submit(self, canonical_ids, source, expected_source_files=None):
        ids = sorted(set(canonical_ids))
        if not ids:
            raise ConversionRefused('SELECTION_EMPTY', 'Select at least one supported species explicitly')
        requests = []
        for cid in ids:
            if self.job(cid)['state'] in ACTIVE:
                raise ConversionRefused('JOB_ALREADY_ACTIVE', 'Only one active job per species is allowed')
            cache = self.root / 'cache' / cid / ('build-' + uuid.uuid4().hex)
            requests.append((cid, self._request(cid, source, cache)))
        ensure_dir(self.root, self.root / 'jobs')
        locks = []
        try:
            for cid, _ in requests:
                path = confined_root(self.root / 'jobs' / f'{cid}.lock')
                if path.exists() and path.stat().st_nlink > 1:
                    raise ConversionRefused('OUTPUT_REFUSED', 'Hardlinked job lock refused')
                locks.append(_SpeciesLock(path))
        except BaseException:
            for lock in locks:
                lock.close()
            raise
        # One transaction: a failed write must never leave a QUEUED job outside the queue or
        # holding its species lock; every partially written job is rolled back.
        created = []
        try:
            for (cid, request), lock in zip(requests, locks):
                previous = self.job(cid)
                previous_item = self._jobs.get(cid)
                item = {'canonical_id': cid, 'state': 'QUEUED', 'stage': None, 'code': None,
                        'reason': '', 'exit_code': None, 'events': [], 'attempt': previous['attempt'] + 1,
                        'cache_directory': str(request.cache_dir), 'log_path': str(self.root / 'jobs' / f'{cid}.log'),
                        '_source': dict(source), '_request': request, '_lock': lock, '_child': None, '_result': None,
                        '_expected_source_files': dict(expected_source_files) if expected_source_files else None,
                        '_source_files': None}
                self._jobs[cid] = item
                created.append((cid, previous, previous_item, lock))
                atomic_write(self.root, Path(item['log_path']), b'')
                self._save(cid)
                self._queue.append(cid)
        except BaseException:
            released = {id(lock) for _, _, _, lock in created}
            for lock in locks:
                if id(lock) not in released:
                    lock.close()
            for cid, previous, previous_item, lock in reversed(created):
                if cid in self._queue:
                    self._queue.remove(cid)
                # Restore BOTH the in-memory job and its persisted state, so a failed retry
                # still shows the previous terminal result (and its Retry affordance).
                if previous_item is not None:
                    self._jobs[cid] = previous_item
                else:
                    self._jobs.pop(cid, None)
                lock.close()
                path = self.root / 'jobs' / f'{cid}.json'
                if previous['attempt']:
                    atomic_write(self.root, path, json.dumps(previous, ensure_ascii=False).encode('utf-8'))
                else:
                    try:
                        path.unlink()
                    except OSError:
                        pass
            raise
        return [self.job(cid) for cid in ids]

    def _save(self, cid):
        atomic_write(self.root, self.root / 'jobs' / f'{cid}.json', json.dumps(self.job(cid), ensure_ascii=False).encode('utf-8'))

    def _argv(self, item):
        request = item['_request']
        args = [sys.executable, '-m', 'bridge.conversion.cli', 'source-cache', '--events',
                '--canonical', request.canonical_id, '--asset-name', request.species,
                '--sr2-dir', str(request.sr2_dir), '--cache-dir', str(request.cache_dir),
                '--default-clip', request.default_clip, '--allow-nonlab',
                '--out-dir', str(sc.REPO_ROOT / 'Lab' / 'GeneratedContent' / 'launcher-conversion' / request.canonical_id)]
        for pid in request.clip_ids:
            args.extend(('--clip-id', str(pid)))
        if item.get('_expected_source_files'):
            args.extend(('--expect-source-files', json.dumps(item['_expected_source_files'], sort_keys=True)))
        return args

    def _env(self):
        scratch = self.root / 'scratch'
        ensure_dir(self.root, scratch)
        env = sc.child_env()
        for key in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME'):
            env[key] = str(scratch)
        env['PYTHONUNBUFFERED'] = '1'
        return env

    def _consume(self, cid):
        item = self._jobs[cid]
        lines = item['_child'].drain_lines()
        if not lines:
            return False
        path = Path(item['log_path'])
        confined_root(path)
        log = path.read_bytes() if path.exists() else b''
        for line in lines:
            log = (log + (str(line).rstrip() + '\n').encode('utf-8', errors='replace'))[-LOG_BYTES:]
            try:
                doc = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(doc, dict):
                continue
            if doc.get('event') == 'source-cache-stage' and doc.get('stage') in STAGES:
                item['stage'] = doc['stage']
                item['events'] = (item['events'] + [doc])[-32:]
                if (doc.get('stage') == 'SOURCE_FINGERPRINT' and doc.get('status') == 'OK'
                        and isinstance(doc.get('source_files'), dict)):
                    item['_source_files'] = doc['source_files']
            elif 'status' in doc:
                item['_result'] = doc
        atomic_write(self.root, path, log)
        self._save(cid)
        return True

    def poll(self):
        changed = False
        if self._running is not None:
            cid = self._running
            child = self._jobs[cid]['_child']
            code = child.poll()
            changed = self._consume(cid)
            if code is not None:
                self._finish(cid, code)
                changed = True
        if self._running is None and self._queue:
            cid = self._queue.popleft()
            item = self._jobs[cid]
            try:
                item['_child'] = self.runner.start(self._argv(item), cwd=sc.REPO_ROOT, env=self._env())
                item['state'] = 'RUNNING'
                self._running = cid
                self._save(cid)
            except (OSError, RuntimeError) as exc:
                self._terminal(cid, 'FAILED', 'PROCESS_START_FAILED', str(exc))
            changed = True
        return changed

    def inspect(self, cid, source):
        try:
            self._row(cid)
        except ConversionRefused as exc:
            return {'state': 'UNSUPPORTED', 'reason': exc.reason, 'report': None}
        pointer = self.root / 'cache' / cid / 'current.json'
        try:
            confined_root(pointer)
            if not pointer.exists():
                return {'state': 'ABSENT', 'reason': 'No published local cache', 'report': None}
            generation = json.loads(pointer.read_text(encoding='utf-8'))['generation']
            if not isinstance(generation, str) or not re.fullmatch(r'build-[0-9a-f]{32}', generation):
                raise ValueError('Invalid cache generation pointer')
            target = confined_root(pointer.parent / generation)
            if not target.is_dir():
                return {'state': 'BROKEN', 'reason': 'Selected cache generation is missing', 'report': None}
            if source is None:
                return {'state': 'STALE', 'reason': 'Select the source installation to verify current source hashes', 'report': None}
            return cache_state(self._request(cid, source, target))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            return {'state': 'BROKEN', 'reason': str(exc), 'report': None}

    def rebuild(self, cid, source):
        if self.inspect(cid, source)['state'] not in ('STALE', 'BROKEN'):
            raise ConversionRefused('REBUILD_NOT_REQUIRED', 'Rebuild requires a stale or broken disk cache')
        return self.submit([cid], source)

    def _finish(self, cid, exit_code):
        item = self._jobs[cid]
        doc = item['_result'] or {}
        item['exit_code'] = exit_code
        if exit_code:
            refused = exit_code == 4 or doc.get('code') in ('SOURCE_CACHE_SELECTION_REQUIRED', 'SOURCE_CHANGED')
            self._terminal(cid, 'REFUSED' if refused else 'FAILED', doc.get('code') or f'CLI_EXIT_{exit_code}',
                           str(doc.get('error') or 'source-cache exited without a successful result; see job log'))
            return
        bound = (doc.get('canonical_id') == cid and doc.get('complete') is True and
                 doc.get('status') in sc.RECEIPT_STATUSES and
                 doc.get('cache_directory') == str(item['_request'].cache_dir))
        state = cache_state(item['_request'])
        if not bound or state['state'] != 'READY':
            self._terminal(cid, 'FAILED', 'RESULT_UNVERIFIED', state['reason'] if bound else 'CLI result is missing or not bound to this request')
            return
        try:
            atomic_write(self.root, item['_request'].cache_dir.parent / 'current.json',
                         json.dumps({'generation': item['_request'].cache_dir.name}).encode('utf-8'))
        except (OSError, ValueError) as exc:
            self._terminal(cid, 'FAILED', 'CACHE_SELECTION_FAILED', str(exc))
            return
        self._terminal(cid, 'SUCCEEDED', None, 'Local cache verified; deployment and runtime registration remain separate steps')

    def _terminal(self, cid, state, code, reason):
        item = self._jobs[cid]
        item.update(state=state, code=code, reason=reason)
        if self._running == cid:
            self._running = None
        try:
            self._save(cid)
        finally:
            item['_lock'].close()

    def retry(self, cid):
        item = self._jobs.get(cid)
        if item is None or item['state'] not in ('FAILED', 'REFUSED', 'CANCELLED'):
            raise ConversionRefused('RETRY_REFUSED', 'Retry requires a failed, refused or cancelled job')
        # A retry replays the original attempt: its pinned source fingerprints are revalidated
        # by the CLI before extraction, so a changed source is reported (SOURCE_CHANGED)
        # instead of silently converting different bytes. Rebuild is the explicit path to new
        # source bytes.
        return self.submit([cid], item['_source'], expected_source_files=item.get('_source_files'))

    def cancel(self, cid):
        item = self._jobs.get(cid)
        if item is None or item['state'] not in ACTIVE:
            return False
        if item['_child'] is not None:
            item['_child'].cancel()  # owned process tree must be stopped before claiming cancellation
            self._consume(cid)
        elif cid in self._queue:
            self._queue.remove(cid)
        self._terminal(cid, 'CANCELLED', 'USER_CANCELLED', 'Cancelled explicitly; the previous cache remains selected')
        return True

    def close(self):
        for cid in list(self._jobs):
            self.cancel(cid)


class _WindowsJob:
    """Kill-on-close Job Object: attach suspended CLI before it can spawn an extractor."""
    def __init__(self):
        import ctypes
        from ctypes import wintypes as W
        self.ctypes = ctypes
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
        self.api.CreateJobObjectW.restype = W.HANDLE
        self.api.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.api.TerminateJobObject.argtypes = [W.HANDLE, W.UINT]
        self.api.CloseHandle.argtypes = [W.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                        ('flags', W.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                        ('active', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_uint64 * 6), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, process):
        import ctypes
        from ctypes import wintypes as W
        if not self.api.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        native = ctypes.WinDLL('ntdll')
        native.NtResumeProcess.argtypes = [W.HANDLE]
        native.NtResumeProcess.restype = ctypes.c_long
        status = native.NtResumeProcess(W.HANDLE(int(process._handle)))
        if status != 0:
            raise OSError(f'NtResumeProcess failed: {status:#x}')

    def terminate(self):
        if self.handle and not self.api.TerminateJobObject(self.handle, 1):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class _OwnedProcess:
    def __init__(self, process, job):
        import threading
        import time
        self.process, self.job = process, job
        self._lines, self._mutex = deque(), threading.Lock()
        self._line_bytes = 0
        self._deadline = time.monotonic() + sc.DEFAULT_TIMEOUT + 60
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(8192)
                if not line:
                    break
                text = line.rstrip()
                with self._mutex:
                    self._lines.append(text)
                    self._line_bytes += len(text) + 1
                    while self._line_bytes > LOG_BYTES and len(self._lines) > 1:
                        self._line_bytes -= len(self._lines.popleft()) + 1
        finally:
            self.process.stdout.close()

    def drain_lines(self):
        with self._mutex:
            lines = list(self._lines)
            self._lines.clear()
            self._line_bytes = 0
        return lines

    def poll(self):
        import time
        code = self.process.poll()
        if code is None and time.monotonic() > self._deadline:
            self.cancel()
            with self._mutex:
                self._lines.append(json.dumps({'status': 'FAILED', 'code': 'PROCESS_TIMEOUT', 'error': 'source-cache exceeded its deadline'}))
            code = self.process.returncode
        if code is not None:
            if self.job:
                self.job.close()  # no abandoned descendant can keep stdout alive
            self._reader.join(timeout=1)
            return code if not self._reader.is_alive() else None
        return None

    def cancel(self):
        import signal
        if self.job:
            self.job.terminate()
        elif self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=10)
        if self.job:
            self.job.close()
        self._reader.join(timeout=10)
        if self._reader.is_alive():
            raise RuntimeError('Owned process stdout did not close after cancellation')


class SubprocessRunner:
    """Real CLI runner, argv-only. Windows descendants belong to an owned Job Object."""
    def start(self, argv, *, cwd, env):
        import subprocess
        job = _WindowsJob() if os.name == 'nt' else None
        process = None
        try:
            process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding='utf-8', errors='replace', shell=False,
                                       creationflags=0x4 | 0x08000000 if job else 0,
                                       start_new_session=job is None)
            if job:
                job.attach_and_resume(process)
            return _OwnedProcess(process, job)
        except BaseException:
            if job:
                job.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                if process.stdout:
                    process.stdout.close()
            raise
