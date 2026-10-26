"""Owned BridgeHost lifecycle for the local launcher: real child process, real readiness, scoped stop.

The launcher starts exactly one BridgeHost for its own Lab root and surfaces its readiness: a
token-authenticated HTTP probe against the host's own command mailbox plus the plugin heartbeats the
host publishes to its status file. Nothing here ever acts on a process the launcher did not start —
``stop()`` terminates the child handle it owns, and a previous session's host is only stopped by an
explicit ``reclaim()`` that re-verifies the recorded executable image on the same process handle it
terminates (never by process name, never with a taskkill).

Secrets stay in files: the token lives in ``<root>/host/token`` (owner-only) and the record keeps
the path only, so a diagnostic bundle can never leak it.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bridge.cache.fs import atomic_write, confined_root, ensure_dir

ROOT = Path(__file__).resolve().parents[2]
HOST_MODULE = 'bridge.host.server'
RECORD_NAME = 'host.json'
READY_TIMEOUT = 25.0
PROBE_TIMEOUT = 3.0
STOP_TIMEOUT = 15.0
STARTING_TRUST_SECONDS = 120.0
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000  # required for WaitForSingleObject on the process handle
_LOG_URL = re.compile(r'http://127\.0\.0\.1:\d+')

STATE_TEXT = {
    'STOPPED': 'BridgeHost is not running',
    'STARTING': 'BridgeHost is starting',
    'RUNNING': 'BridgeHost is running — game plugins can connect',
    'EXTERNAL': 'Another launcher session already runs the BridgeHost for this Lab',
    'UNREACHABLE': 'A recorded process is alive but the BridgeHost does not answer',
    'STALE': 'A previous BridgeHost is gone; its record can be replaced',
}


class HostLifecycleError(RuntimeError):
    def __init__(self, code, detail=''):
        self.code, self.detail = str(code), str(detail)
        super().__init__(f'{self.code}: {self.detail}' if self.detail else self.code)


def image_of(pid):
    """Exact executable image for a PID, or None (fail closed; identity checks are Windows-only)."""
    if sys.platform != 'win32' or type(pid) is not int or pid <= 0:
        return None
    from tools.lab.process_guard import process_image_path  # Windows-only module, imported on demand
    try:
        return process_image_path(pid)
    except (OSError, NotImplementedError):
        return None


def terminate_verified(pid, expected_exe):
    """Terminate a PID only on the very handle that proved its image; False when anything differs.

    Never a taskkill, never a process name: the same kernel handle that reported the executable image
    is the one asked to terminate, so a recycled PID cannot be hit between the two steps.
    """
    if sys.platform != 'win32' or type(pid) is not int or pid <= 0 or not isinstance(expected_exe, str):
        return False
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                    ctypes.POINTER(wintypes.DWORD))
    kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return False
        if str(Path(buffer.value).resolve()).casefold() != str(Path(expected_exe).resolve()).casefold():
            return False
        if not kernel32.TerminateProcess(handle, 1):
            return False
        # TerminateProcess is asynchronous: wait on the SAME handle until the process has
        # actually exited, so callers can immediately remove files the child still held open
        # (e.g. its redirected host.log) without racing the exiting image. WAIT_OBJECT_0 == 0.
        return kernel32.WaitForSingleObject(handle, int(STOP_TIMEOUT * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def child_env(root, base=None):
    """Per-process environment for Lab children: private temp dirs, never the user's globals."""
    root = confined_root(root)
    scratch = root / 'scratch'
    ensure_dir(root, scratch)
    env = dict(os.environ if base is None else base)
    for key in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME'):
        env[key] = str(scratch)
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    existing = env.get('PYTHONPATH')
    env['PYTHONPATH'] = str(ROOT) if not existing else str(ROOT) + os.pathsep + existing
    return env


def probe_host(url, token, realm='SR1', *, timeout=PROBE_TIMEOUT):
    """Read-only readiness probe: the host must answer with a token this launcher owns."""
    empty = {'reachable': False, 'authorized': False, 'protocol_version': None, 'status': None, 'error': None}
    if not url or not token:
        return empty
    request = Request(f'{str(url).rstrip("/")}/v1/commands/next?realm={realm}',
                      headers={'Authorization': 'Bearer ' + str(token)})
    try:
        with urlopen(request, timeout=timeout) as response:
            document = json.loads(response.read())
    except HTTPError as exc:
        return {**empty, 'reachable': True, 'status': exc.code, 'error': str(exc.reason or exc)}
    except (URLError, OSError, ValueError, UnicodeError) as exc:
        return {**empty, 'error': str(exc)}
    version = document.get('protocol_version') if isinstance(document, dict) else None
    return {'reachable': True, 'authorized': version == 1, 'protocol_version': version, 'status': 200, 'error': None}


class HostController:
    """One launcher session's BridgeHost: start (idempotent), observe, stop the child it owns."""

    def __init__(self, root, *, python=None, timeout=READY_TIMEOUT, probe_timeout=PROBE_TIMEOUT):
        self.root = confined_root(root)
        self.python = Path(python or sys.executable).resolve()
        self.timeout = float(timeout)
        self.probe_timeout = float(probe_timeout)
        self._child = None
        self._generation = None
        self._url = None

    @property
    def runtime(self):
        """Scoped runtime directory for this Lab root's BridgeHost (record, token, status, log)."""
        return self.root / 'host'

    def record_path(self):
        return self.runtime / RECORD_NAME

    def state(self):
        """Read-only snapshot: record, process identity and real HTTP probe; rewrites nothing."""
        base = {'state': 'STOPPED', 'ready': False, 'owned': False, 'url': None, 'pid': None, 'heartbeats': {},
                'token_file': None, 'status_file': None, 'generation': None, 'detail': STATE_TEXT['STOPPED']}
        record = self._record()
        if record is None:
            return base
        generation = record.get('generation')
        pid = record.get('pid')
        alive = self._is_recorded_process(record)
        if record.get('state') != 'RUNNING' or pid is None:
            fresh = alive and time.time() - float(record.get('started_unix') or 0.0) <= STARTING_TRUST_SECONDS
            return {**base, 'state': 'STARTING' if fresh else 'STALE', 'pid': pid if fresh else None,
                    'generation': generation, 'detail': STATE_TEXT['STARTING' if fresh else 'STALE']}
        token = self._token(record)
        url = record.get('url') if isinstance(record.get('url'), str) else None
        probe = probe_host(url, token, timeout=self.probe_timeout) if (url and token) else {}
        answering = bool(probe.get('authorized'))
        ours = bool(self._child is not None and self._child.poll() is None and self._child.pid == pid
                    and self._generation == generation)
        common = {**base, 'url': url, 'pid': pid, 'generation': generation, 'token_file': record.get('token_file'),
                  'status_file': record.get('status_file')}
        if answering:
            return {**common, 'state': 'RUNNING' if ours else 'EXTERNAL', 'ready': True, 'owned': ours,
                    'heartbeats': self._heartbeats(record), 'detail': STATE_TEXT['RUNNING' if ours else 'EXTERNAL']}
        if ours or self._is_recorded_process(record):
            # Alive but silent: our own child (stoppable) or a process whose image matches the record.
            return {**common, 'state': 'UNREACHABLE', 'owned': ours, 'detail': STATE_TEXT['UNREACHABLE']}
        return {**common, 'state': 'STALE', 'pid': pid, 'detail': STATE_TEXT['STALE']}

    def start(self):
        """Start the scoped BridgeHost and return the ready state; idempotent, never a second host."""
        current = self.state()
        if current['state'] == 'RUNNING':
            return current
        if current['state'] in ('EXTERNAL', 'UNREACHABLE'):
            raise HostLifecycleError('HOST_ALREADY_RUNNING', current['detail'])
        if current['state'] == 'STARTING':
            raise HostLifecycleError('HOST_START_IN_PROGRESS', current['detail'])
        if current['state'] == 'STALE':
            self._unlink_record(current['generation'])
        generation = secrets.token_hex(8)
        ensure_dir(self.root, self.runtime)
        self._claim(generation)
        try:
            self._spawn(generation)
            self._await_ready()
            atomic_write(self.root, self.record_path(), self._json({
                'state': 'RUNNING', 'generation': generation, 'launcher_pid': os.getpid(),
                'pid': self._child.pid, 'exe': str(self.python), 'url': self._url,
                'token_file': str(self._files('token')), 'status_file': str(self._files('status.json')),
                'started_unix': time.time()}))
        except Exception as exc:
            self._terminate_child()
            self._unlink_record(generation)
            if isinstance(exc, HostLifecycleError):
                raise
            raise HostLifecycleError('HOST_START_FAILED', str(exc)) from exc
        return self.state()

    def stop(self):
        """Stop the BridgeHost this controller owns; another session's host is left untouched."""
        state = self.state()
        if not state['owned']:
            return {**state, 'stopped': False}
        generation = self._generation
        self._terminate_child()
        self._unlink_record(generation)
        return {**self.state(), 'stopped': True}

    def close(self):
        """Best-effort stop of the owned child only (safe from teardown paths)."""
        try:
            self.stop()
        except Exception:
            pass

    def reclaim(self):
        """Stop the previous session's BridgeHost recorded for this Lab root; never a foreign image.

        Only an identity-verified host qualifies: the record must have been proven alive above loopback
        with the token stored in this root's own runtime directory, and its PID must still resolve to
        the exact interpreter image the record names when the terminate handle is opened. Anything
        else is left alone — the user's processes are never guessed at.
        """
        state = self.state()
        if state['state'] != 'EXTERNAL' or not state['pid']:
            return {**state, 'reclaimed': False}
        record = self._record() or {}
        if not terminate_verified(state['pid'], record.get('exe')):
            return {**state, 'reclaimed': False}
        self._unlink_record(record.get('generation'))
        return {**self.state(), 'reclaimed': True}

    # -- internals ---------------------------------------------------------------------------------

    def _json(self, document):
        return (json.dumps(document, indent=2, sort_keys=True) + '\n').encode()

    def _record(self):
        try:
            document = json.loads(self.record_path().read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError, RecursionError):
            return None
        return document if isinstance(document, dict) else None

    def _files(self, name):
        return self.runtime / name

    def _inside_root(self, path):
        try:
            return str(Path(path).resolve()).casefold().startswith(str(self.root.resolve()).casefold() + os.sep)
        except (OSError, ValueError):
            return False

    def _read_text(self, path):
        try:
            return path.read_text(encoding='utf-8').strip()
        except (OSError, ValueError, UnicodeError):
            return ''

    def _token(self, record):
        path = record.get('token_file')
        if not isinstance(path, str) or not self._inside_root(path):
            return None
        return self._read_text(Path(path)) or None

    def _heartbeats(self, record):
        path = record.get('status_file')
        if not isinstance(path, str) or not self._inside_root(path):
            return {}
        try:
            document = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError, RecursionError):
            return {}
        heartbeats = document.get('heartbeats') if isinstance(document, dict) else None
        return heartbeats if isinstance(heartbeats, dict) else {}

    def _is_recorded_process(self, record):
        image = image_of(record.get('pid'))
        exe = record.get('exe')
        return bool(image is not None and isinstance(exe, str)
                    and str(image).casefold() == str(Path(exe)).casefold())

    def _claim(self, generation):
        """Claim the record exclusively so two launcher sessions cannot start two hosts."""
        try:
            handle = os.open(self.record_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            again = self.state()
            code = 'HOST_ALREADY_RUNNING' if again['state'] in ('EXTERNAL', 'UNREACHABLE') else 'HOST_START_IN_PROGRESS'
            raise HostLifecycleError(code, again['detail']) from None
        try:
            os.write(handle, self._json({'state': 'STARTING', 'generation': generation, 'pid': os.getpid(),
                                         'launcher_pid': os.getpid(),
                                         'exe': str(self.python), 'started_unix': time.time()}))
        finally:
            os.close(handle)

    def _spawn(self, generation):
        command = [str(self.python), '-m', HOST_MODULE, '--port', '0',
                   '--token-file', str(self._files('token')), '--status-file', str(self._files('status.json'))]
        with open(self._files('host.log'), 'wb') as log:
            self._child = subprocess.Popen(command, cwd=str(ROOT), env=child_env(self.root), stdout=log,
                                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self._generation = generation
        self._url = None

    def _await_ready(self):
        deadline = time.monotonic() + self.timeout
        log_path = self._files('host.log')
        while True:
            if self._child.poll() is not None:
                raise HostLifecycleError('HOST_EXITED', self._log_tail(log_path))
            token = self._read_text(self._files('token'))
            url = self._url_from_status() or self._url_from_log(log_path)
            if token and url:
                probe = probe_host(url, token, timeout=self.probe_timeout)
                if probe['authorized']:
                    self._url = url
                    return url
            if time.monotonic() >= deadline:
                raise HostLifecycleError('HOST_NOT_READY', self._log_tail(log_path))
            time.sleep(0.1)

    def _url_from_status(self):
        path = self._files('status.json')
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, UnicodeError, RecursionError):
            return None
        url = document.get('url') if isinstance(document, dict) else None
        return url if isinstance(url, str) and url.startswith('http://127.0.0.1:') else None

    def _url_from_log(self, path):
        match = _LOG_URL.search(self._log_bytes(path).decode('utf-8', 'replace'))
        return match.group(0) if match else None

    def _log_bytes(self, path, limit=65536):
        try:
            return path.read_bytes()[-limit:]
        except OSError:
            return b''

    def _log_tail(self, path, limit=2000):
        return self._log_bytes(path, limit).decode('utf-8', 'replace').strip() or 'empty BridgeHost log'

    def _terminate_child(self):
        """Terminate the exact child handle this controller started (never a PID lookup)."""
        child, self._child, self._generation = self._child, None, None
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=STOP_TIMEOUT)

    def _unlink_record(self, generation=None):
        record = self._record()
        if record is None or (generation is not None and record.get('generation') != generation):
            return
        try:
            self.record_path().unlink()
        except OSError:
            pass


def heartbeat_summary(heartbeats):
    """Realm label -> short live-session text, for the launcher's readiness surface."""
    labels = {}
    for realm, entry in sorted((heartbeats or {}).items()):
        if not isinstance(entry, dict):
            continue
        version = entry.get('plugin_version')
        labels[realm] = f'{realm} plugin connected' + (f' ({version})' if isinstance(version, str) else '')
    return labels
