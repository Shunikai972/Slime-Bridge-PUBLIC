"""Process side of the launcher: the user's own BridgeHost and the games it starts.

* ``HostProcess`` runs ``python -m bridge.host.server`` (loopback only) with a private token file, and stops only
  the child it started itself.
* ``GameLauncher`` starts the game executable that discovery already verified (never a path typed into the UI),
  with ``SLIMEBRIDGE_URL`` / ``SLIMEBRIDGE_TOKEN`` in its environment when the host is up. It never touches saves,
  never injects anything and never bypasses a store: the game starts exactly as if the player double-clicked it.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXECUTABLES = {'SR1': 'SlimeRancher.exe', 'SR2': 'SlimeRancher2.exe'}


class RuntimeErrorWithCode(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


class HostProcess:
    def __init__(self, root, popen=subprocess.Popen, python=None, wait_seconds=8.0, clock=time.monotonic, sleep=time.sleep):
        self.root = Path(root)
        self._popen, self._python = popen, python or sys.executable
        self._wait, self._clock, self._sleep = wait_seconds, clock, sleep
        self._child = None
        self._token = None

    @property
    def token_file(self):
        return self.root / 'host.token'

    @property
    def status_file(self):
        return self.root / 'host.status.json'

    def _read_status(self):
        try:
            return json.loads(self.status_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def running(self):
        return self._child is not None and self._child.poll() is None

    def state(self):
        if not self.running():
            return {'state': 'STOPPED', 'url': None, 'pid': None, 'heartbeats': {}}
        status = self._read_status()
        return {'state': 'RUNNING' if status.get('url') else 'STARTING', 'url': status.get('url'),
                'pid': self._child.pid, 'heartbeats': status.get('heartbeats') or {}}

    def admin_token(self):
        """The host's admin token while it runs (used by this launcher only, never shown or logged)."""
        return self._token if self.running() else None

    def env(self):
        """Environment the games need to find this host (empty when it is not running)."""
        status = self.state()
        if status['state'] != 'RUNNING' or not self._token:
            return {}
        return {'SLIMEBRIDGE_URL': status['url'], 'SLIMEBRIDGE_TOKEN': self._token}

    def realm_env(self, realm):
        """Environment for one game: the host address and that realm's own credential (never the admin token)."""
        status = self.state()
        if status['state'] != 'RUNNING' or not self._token or realm not in ('SR1', 'SR2'):
            return {}
        request = urllib.request.Request(status['url'].rstrip('/') + '/v1/realm-token?realm=' + realm,
                                         headers={'Authorization': 'Bearer ' + self._token})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                token = json.loads(response.read()).get('token')
        except (OSError, ValueError):
            return {}
        return {'SLIMEBRIDGE_URL': status['url'], 'SLIMEBRIDGE_TOKEN': token} if isinstance(token, str) and token else {}

    def start(self):
        if self.running():
            return self.state()
        self.root.mkdir(parents=True, exist_ok=True)
        for stale in (self.status_file, self.token_file):
            stale.unlink(missing_ok=True)
        command = [self._python, '-m', 'bridge.host.server', '--token-file', str(self.token_file),
                   '--status-file', str(self.status_file)]
        kwargs = {'cwd': str(REPO_ROOT), 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        try:
            self._child = self._popen(command, **kwargs)
        except OSError as exc:
            raise RuntimeErrorWithCode('HOST_START_FAILED', str(exc)) from exc
        deadline = self._clock() + self._wait
        while self._clock() < deadline:
            if self._child.poll() is not None:
                self._child = None
                raise RuntimeErrorWithCode('HOST_EXITED', 'BridgeHost stopped right after starting')
            if self._read_status().get('url') and self.token_file.is_file():
                self._token = self.token_file.read_text(encoding='utf-8').strip()
                return self.state()
            self._sleep(0.1)
        self.stop()
        raise RuntimeErrorWithCode('HOST_TIMEOUT', 'BridgeHost did not report an address in time')

    def stop(self):
        child, self._child, self._token = self._child, None, None
        if child is None:
            return {'stopped': False}
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        for leftover in (self.token_file, self.status_file):
            leftover.unlink(missing_ok=True)
        return {'stopped': True}


class GameLauncher:
    """Starts verified game executables; one process per realm at a time."""

    def __init__(self, popen=subprocess.Popen):
        self._popen = popen
        self._children = {}

    def running(self, realm):
        child = self._children.get(realm)
        return child is not None and child.poll() is None

    def launch(self, installation, extra_env=None):
        realm = installation['game']
        exe = Path(installation['root']) / EXECUTABLES[realm]
        if not exe.is_file():
            raise RuntimeErrorWithCode('EXECUTABLE_MISSING', str(exe))
        if self.running(realm):
            return {'realm': realm, 'pid': self._children[realm].pid, 'already_running': True, 'exe': str(exe)}
        env = dict(os.environ)
        env.update(extra_env or {})
        kwargs = {'cwd': str(exe.parent), 'env': env, 'close_fds': True}
        if os.name == 'nt':
            kwargs['creationflags'] = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        try:
            child = self._popen([str(exe)], **kwargs)
        except OSError as exc:
            raise RuntimeErrorWithCode('LAUNCH_FAILED', str(exc)) from exc
        self._children[realm] = child
        return {'realm': realm, 'pid': child.pid, 'already_running': False, 'exe': str(exe)}

    def status(self):
        return {realm: self.running(realm) for realm in EXECUTABLES}
