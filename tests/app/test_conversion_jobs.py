"""Disk fixtures and process-boundary tests; no game or cache inspection mocks."""
import json
from pathlib import Path
import shutil

import pytest

from bridge.app import conversion_jobs as jobs
from bridge.content.canonical import sha256_json
from bridge.conversion import source_cache as sc


REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def synthetic_repo(tmp_path, monkeypatch):
    """Give guard-aware fixtures their own repo and private Lab, wholly under pytest scratch."""
    root = tmp_path / 'synthetic-repo'
    (root / 'Lab' / 'RuntimeArtifacts').mkdir(parents=True)
    (root / 'Lab' / 'AssetCache').mkdir(parents=True)
    docs = root / 'docs'
    docs.mkdir()
    shutil.copyfile(REPO / 'docs' / 'SPECIES_ADAPTER.md', docs / 'SPECIES_ADAPTER.md')
    monkeypatch.setattr(sc, 'REPO_ROOT', root)
    monkeypatch.setattr(jobs.cache_state, '__kwdefaults__', {'repo_root': root})
    return root


def launcher_root(name='launcher-conversion'):
    return sc.REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / name


def request(tmp_path, cid='slime.ringtail'):
    data = tmp_path / 'source' / 'SlimeRancher2_Data'
    data.mkdir(parents=True, exist_ok=True)
    for name in sc.SOURCE_FILE_NAMES:
        (data / name).write_bytes(b'test source')
    cache = sc.REPO_ROOT / 'Lab' / 'AssetCache' / 'conversion-fixtures' / 'cache'
    return sc.SourceCacheJob(cid, 'Ringtail', data, cache, (2170,),
                             'Idle_Cotton_Ringtail', True)


def published(job):
    """Write actual hash-bound minimal manifests (not a mocked verifier)."""
    root = job.cache_dir
    root.mkdir(parents=True)
    source = {str(job.sr2_dir / n): sc.sha256_file(job.sr2_dir / n) for n in sc.SOURCE_FILE_NAMES}
    model = {'schema': 'bridge-native-mesh', 'schema_version': 2,
             'skinned_renderers': [{'role': r, 'vertices': [[0, 0, 0]], 'materials': ['mat']}
                                   for r in ('body', 'face')],
             'source_animation': {'default_clip': job.default_clip,
                                  'clips': [{'name': {2170: 'Idle_Cotton_Ringtail', 2187: 'Idle v4',
                                                     2165: 'Pounce v4', 2188: 'Bite v4'}[pid]}
                                            for pid in job.clip_ids]}}
    docs = {'model.json': model,
            'source-animation-provenance.json': {'selection': 'explicit-source-clip-ids',
                'source_files': source, 'clips': [{'source_path_id': pid, 'name': model['source_animation']['clips'][i]['name']}
                                                 for i, pid in enumerate(job.clip_ids)]},
            'icon-descriptor.json': {'file': 'icon.png'},
            'dump-receipt.json': {'model_sha256': None}}
    for name, doc in docs.items():
        if name == 'dump-receipt.json':
            doc['model_sha256'] = sc.sha256_file(root / 'model.json')
        (root / name).write_text(json.dumps(doc), encoding='utf-8')
    (root / 'icon.png').write_bytes(b'fixture icon')
    manifest = {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                'canonical_id': job.canonical_id, 'converter_version': sc.EXTRACTOR_CONVERTER_VERSION,
                'files': {p.name: sc.sha256_file(p) for p in root.iterdir()}}
    (root / 'cache-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    provider = sc.SourceCacheProvider(repo_root=sc.REPO_ROOT)
    identity = provider._identity(job, source)
    scan = sc._scan_cache(root)
    receipt = provider._receipt(job, root, identity, sha256_json(identity), 'BUILT', scan['files'],
                                scan['manifest'], sc._coverage(scan, job.clip_ids, job.default_clip),
                                {'ok': True}, 'fixture')
    sc.receipt_path(root).write_text(json.dumps(receipt), encoding='utf-8')


def test_cache_state_is_read_from_hash_bound_disk_on_every_call(tmp_path):
    job = request(tmp_path)
    assert jobs.cache_state(job, repo_root=sc.REPO_ROOT)['state'] == 'ABSENT'
    published(job)
    assert jobs.cache_state(job, repo_root=sc.REPO_ROOT)['state'] == 'READY'
    (job.sr2_dir / 'resources.assets').write_bytes(b'new game version')
    stale = jobs.cache_state(job, repo_root=sc.REPO_ROOT)
    assert stale['state'] == 'STALE'
    assert 'SOURCE_FILES_CHANGED' in stale['reason']
    (job.cache_dir / 'model.json').write_bytes(b'corrupted')
    broken = jobs.cache_state(job, repo_root=sc.REPO_ROOT)
    assert broken['state'] == 'BROKEN'
    assert 'HASH_MISMATCH' in broken['reason']


class FakeProcess:
    def __init__(self):
        self.lines, self.returncode, self.stopped = [], None, False

    def drain_lines(self):
        lines, self.lines = self.lines, []
        return lines

    def poll(self):
        return self.returncode

    def cancel(self):
        self.stopped = True
        self.returncode = -1


class FakeRunner:
    def __init__(self):
        self.calls, self.children = [], []

    def start(self, argv, *, cwd, env):
        self.calls.append((argv, cwd, env))
        child = FakeProcess()
        self.children.append(child)
        return child


def source_record(tmp_path):
    from bridge.app.installations import record
    request(tmp_path)
    return record('SR2', str(tmp_path / 'source'), role='SOURCE', store='manual')


def test_explicit_queue_real_events_exit_codes_retry_and_bounded_log(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    assert service.job('slime.ringtail')['state'] == 'NOT_STARTED'
    import pytest
    with pytest.raises(jobs.ConversionRefused, match='SELECTION_EMPTY'):
        service.submit([], source)
    assert runner.calls == []
    queued = service.submit(['slime.ringtail'], source)[0]
    assert queued['state'] == 'QUEUED'
    assert runner.calls == []
    with pytest.raises(jobs.ConversionRefused, match='JOB_ALREADY_ACTIVE'):
        service.submit(['slime.ringtail'], source)
    service.poll()
    process = runner.children[0]
    process.lines = [json.dumps({'event': 'source-cache-stage', 'stage': 'SOURCE_EXTRACTION', 'status': 'RUNNING'})]
    service.poll()
    assert service.job('slime.ringtail')['stage'] == 'SOURCE_EXTRACTION'
    argv, cwd, env = runner.calls[0]
    assert argv[1:4] == ['-m', 'bridge.conversion.cli', 'source-cache']
    assert '--events' in argv and '--allow-nonlab' in argv
    assert argv[argv.index('--clip-id') + 1] == '2170'
    assert env['TEMP'].startswith(str(service.root))
    process.lines = ['x' * 100000, json.dumps({'status': 'BLOCKED', 'code': 'SOURCE_CACHE_INCOMPLETE', 'error': 'NO_FACE_PART'})]
    process.returncode = 5
    service.poll()
    failure = service.job('slime.ringtail')
    assert (failure['state'], failure['exit_code'], failure['code']) == ('FAILED', 5, 'SOURCE_CACHE_INCOMPLETE')
    assert 'NO_FACE_PART' in failure['reason']
    assert Path(failure['log_path']).stat().st_size <= jobs.LOG_BYTES
    service.retry('slime.ringtail')
    assert service.job('slime.ringtail')['state'] == 'QUEUED'
    service.poll()
    process = runner.children[1]
    process.lines = [json.dumps({'status': 'INVALID_INPUT', 'error': 'source missing'})]
    process.returncode = 4
    service.poll()
    assert service.job('slime.ringtail')['state'] == 'REFUSED'
    service.close()


def complete_process(service, source, process):
    snapshot = service.job('slime.ringtail')
    job = sc.SourceCacheJob('slime.ringtail', 'Ringtail', Path(source['root']) / 'SlimeRancher2_Data',
                           Path(snapshot['cache_directory']), (2170, 2187, 2165, 2188), 'Idle_Cotton_Ringtail', True)
    published(job)
    process.lines = [json.dumps({'status': 'BUILT', 'complete': True, 'canonical_id': job.canonical_id,
                                'cache_directory': str(job.cache_dir)})]
    process.returncode = 0
    service.poll()
    return job


def test_verified_success_disk_reopen_stale_rebuild_and_safe_cancellation(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    assert service.inspect('slime.ringtail', source)['state'] == 'ABSENT'
    service.submit(['slime.ringtail'], source)
    service.poll()
    first = complete_process(service, source, runner.children[0])
    assert service.job('slime.ringtail')['state'] == 'SUCCEEDED'
    assert service.inspect('slime.ringtail', source)['state'] == 'READY'
    reopened = jobs.ConversionJobs(root=service.root, runner=FakeRunner())
    assert reopened.inspect('slime.ringtail', source)['state'] == 'READY'
    pointer = first.cache_dir.parent / 'current.json'
    original = pointer.read_bytes()
    (first.sr2_dir / 'resources.assets').write_bytes(b'updated source')
    assert service.inspect('slime.ringtail', source)['state'] == 'STALE'
    service.rebuild('slime.ringtail', source)
    assert service.cancel('slime.ringtail')
    assert pointer.read_bytes() == original
    assert service.job('slime.ringtail')['state'] == 'CANCELLED'
    service.retry('slime.ringtail')
    service.poll()
    assert service.cancel('slime.ringtail')
    assert runner.children[-1].stopped
    assert pointer.read_bytes() == original and first.cache_dir.is_dir()
    service.rebuild('slime.ringtail', source)
    service.poll()
    latest = complete_process(service, source, runner.children[-1])
    assert latest.cache_dir != first.cache_dir
    assert service.inspect('slime.ringtail', source)['state'] == 'READY'
    service.close()
    reopened.close()


def test_roster_unsupported_and_cross_launcher_species_fence(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    rows = service.rows
    assert len(rows) == 15
    assert sum(r['source'] == 'SR2' for r in rows) == 9
    assert {r['canonical_id'] for r in rows if r['supported']} == {'slime.ringtail', 'slime.cotton', 'slime.angler'}
    source = source_record(tmp_path)
    assert service.inspect('slime.batty', source)['state'] == 'UNSUPPORTED'
    with pytest.raises(jobs.ConversionRefused, match='UNSUPPORTED'):
        service.submit(['slime.ringtail', 'slime.batty'], source)
    assert service.job('slime.ringtail')['state'] == 'NOT_STARTED'
    service.submit(['slime.ringtail'], source)
    other = jobs.ConversionJobs(root=service.root, runner=FakeRunner())
    with pytest.raises(jobs.ConversionRefused, match='JOB_ALREADY_ACTIVE'):
        other.submit(['slime.ringtail'], source)
    service.close()
    other.submit(['slime.ringtail'], source)
    other.close()


def test_exit_zero_without_hash_bound_result_is_not_success(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    service.submit(['slime.ringtail'], source)
    service.poll()
    runner.children[0].returncode = 0
    service.poll()
    assert service.job('slime.ringtail')['state'] == 'FAILED'
    assert service.job('slime.ringtail')['code'] == 'RESULT_UNVERIFIED'
    service.close()


def test_default_runner_invokes_real_cli_and_reports_actual_failed_stage(tmp_path):
    import time
    runner = jobs.SubprocessRunner()
    original_start = runner.start

    def start_real_cli(argv, *, cwd, env):
        child_env = dict(env)
        child_env['PYTHONDONTWRITEBYTECODE'] = '1'
        return original_start(argv, cwd=REPO, env=child_env)

    runner.start = start_real_cli
    service = jobs.ConversionJobs(root=launcher_root('launcher-conversion-real'), runner=runner)
    source = source_record(tmp_path)
    (tmp_path / 'source' / 'SlimeRancher2_Data').rename(tmp_path / 'source' / 'not-data')
    service.submit(['slime.ringtail'], source)
    deadline = time.monotonic() + 20
    while service.job('slime.ringtail')['state'] in jobs.ACTIVE and time.monotonic() < deadline:
        service.poll()
        time.sleep(.03)
    snapshot = service.job('slime.ringtail')
    assert (snapshot['state'], snapshot['exit_code']) == ('REFUSED', 4)
    assert 'SR2 data dir not found' in snapshot['reason']
    assert any(e['stage'] == 'SOURCE_VALIDATION' and e['status'] == 'FAILED' for e in snapshot['events'])
    service.close()


def test_real_runner_cancels_owned_descendant_tree_before_returning(tmp_path):
    import sys
    import time
    script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    runner = jobs.SubprocessRunner()
    child = runner.start([sys.executable, '-c', script], cwd=REPO, env=dict(__import__('os').environ))
    pid, deadline = None, time.monotonic() + 10
    try:
        while pid is None and time.monotonic() < deadline:
            lines = child.drain_lines()
            if lines:
                pid = int(lines[0])
            time.sleep(.02)
        assert pid is not None
        if __import__('os').name == 'nt':
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x100000, False, pid)
            assert handle
            try:
                child.cancel()
                assert api.WaitForSingleObject(handle, 3000) == 0
            finally:
                api.CloseHandle(handle)
        else:
            child.cancel()
        assert child.poll() is not None
    finally:
        child.cancel()


def test_cancel_tolerates_a_queued_job_missing_from_the_queue(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    service.submit(['slime.ringtail'], source)
    service._queue.clear()  # simulate a partially lost queue
    assert service.cancel('slime.ringtail') is True
    assert service.job('slime.ringtail')['state'] == 'CANCELLED'
    service.close()


def test_retry_pins_source_fingerprints_and_reports_a_changed_source(tmp_path):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    fingerprint = {str(Path(source['root']) / 'SlimeRancher2_Data' / name): 'a' * 64
                   for name in sc.SOURCE_FILE_NAMES}
    service.submit(['slime.ringtail'], source)
    service.poll()
    child = runner.children[0]
    child.lines = [json.dumps({'event': 'source-cache-stage', 'stage': 'SOURCE_FINGERPRINT', 'status': 'OK',
                               'source_files': fingerprint}),
                   json.dumps({'status': 'INVALID_INPUT', 'error': 'attempt failed after fingerprinting'})]
    child.returncode = 4
    service.poll()
    assert service.job('slime.ringtail')['state'] == 'REFUSED'
    service.retry('slime.ringtail')
    assert service.job('slime.ringtail')['state'] == 'QUEUED'
    service.poll()
    argv = runner.calls[1][0]
    assert argv[argv.index('--expect-source-files') + 1] == json.dumps(fingerprint, sort_keys=True)
    child = runner.children[1]
    child.lines = [json.dumps({'event': 'source-cache-stage', 'stage': 'SOURCE_FINGERPRINT', 'status': 'FAILED',
                               'error': 'source files changed since the pinned attempt'}),
                   json.dumps({'status': 'BLOCKED', 'code': 'SOURCE_CHANGED',
                               'error': 'source files changed since the pinned attempt; select the source again '
                                        'or rebuild explicitly'})]
    child.returncode = 5
    service.poll()
    snapshot = service.job('slime.ringtail')
    assert (snapshot['state'], snapshot['code']) == ('REFUSED', 'SOURCE_CHANGED')
    assert 'pinned attempt' in snapshot['reason']
    # An explicit rebuild starts a fresh attempt without the pin, so newly changed source
    # bytes can only be consumed by that deliberate action.
    service.submit(['slime.ringtail'], source)
    service.poll()
    assert '--expect-source-files' not in runner.calls[2][0]
    service.cancel('slime.ringtail')
    service.close()


def test_submit_write_failure_rolls_back_locks_queue_and_state(tmp_path, monkeypatch):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    real_write = jobs.atomic_write

    def failing(root, path, payload):
        if Path(path).name == 'slime.ringtail.log':
            raise OSError('disk full')
        return real_write(root, path, payload)

    monkeypatch.setattr(jobs, 'atomic_write', failing)
    with pytest.raises(OSError):
        service.submit(['slime.cotton', 'slime.ringtail'], source)
    assert service.job('slime.cotton')['state'] == 'NOT_STARTED'
    assert service.job('slime.ringtail')['state'] == 'NOT_STARTED'
    assert service.job('slime.ringtail')['attempt'] == 0
    assert list(service._queue) == []
    assert not (service.root / 'jobs' / 'slime.cotton.json').exists()
    assert not (service.root / 'jobs' / 'slime.ringtail.json').exists()
    monkeypatch.setattr(jobs, 'atomic_write', real_write)
    # The species locks were released by the rollback: a clean submission must be accepted.
    service.submit(['slime.ringtail'], source)
    assert service.job('slime.ringtail')['state'] == 'QUEUED'
    service.close()


def test_owned_stdout_buffer_is_byte_capped_without_losing_the_newest_line(tmp_path):
    class Stream:
        def __init__(self, lines):
            self._lines, self.closed = list(lines), False

        def readline(self, size):
            return self._lines.pop(0) if self._lines else ''

        def close(self):
            self.closed = True

    class Process:
        def __init__(self, lines):
            self.stdout, self.returncode = Stream(lines), None

        def poll(self):
            return self.returncode

    line = 'x' * 8191
    process = jobs._OwnedProcess(Process([line + '\n'] * 40), None)
    process._reader.join(timeout=10)
    lines = process.drain_lines()
    assert lines[-1] == line
    assert sum(len(value) + 1 for value in lines) <= jobs.LOG_BYTES
    assert len(lines) < 40


def test_retry_write_failure_restores_previous_memory_and_disk_state(tmp_path, monkeypatch):
    runner = FakeRunner()
    service = jobs.ConversionJobs(root=launcher_root(), runner=runner)
    source = source_record(tmp_path)
    service.submit(['slime.ringtail'], source)
    service.poll()
    child = runner.children[0]
    child.returncode = 4
    child.lines = [json.dumps({'status': 'INVALID_INPUT', 'error': 'first attempt failed'})]
    service.poll()
    assert service.job('slime.ringtail')['state'] == 'REFUSED'
    real_write = jobs.atomic_write

    def failing(root, path, payload):
        if Path(path).name == 'slime.ringtail.log':
            raise OSError('disk full during retry')
        return real_write(root, path, payload)

    monkeypatch.setattr(jobs, 'atomic_write', failing)
    with pytest.raises(OSError):
        service.retry('slime.ringtail')
    snapshot = service.job('slime.ringtail')
    assert (snapshot['state'], snapshot['attempt']) == ('REFUSED', 1)  # in-memory restored
    on_disk = json.loads((service.root / 'jobs' / 'slime.ringtail.json').read_text(encoding='utf-8'))
    assert (on_disk['state'], on_disk['attempt']) == ('REFUSED', 1)  # disk restored too
    monkeypatch.setattr(jobs, 'atomic_write', real_write)
    service.retry('slime.ringtail')
    assert service.job('slime.ringtail')['state'] == 'QUEUED'
    assert service.job('slime.ringtail')['attempt'] == 2
    service.close()
