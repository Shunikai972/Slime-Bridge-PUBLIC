"""AssetRipper adapter (external process, GPL-3.0: never bundled or linked, only driven over its local
HTTP API on 127.0.0.1). Used for what UnityPy cannot decode: AnimationClip "muscle" data and
AnimatorController state machines, exported as Unity YAML with resolved bone paths.

AssetRipper is located via ``SBCONV_ASSETRIPPER`` (folder or exe), then conventional locations; if it
is missing, ``available()`` is False and callers report a clear 'animation engine missing' diagnostic.
"""

import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .base import EngineCapabilities, EngineError

EXE = 'AssetRipper.GUI.Free.exe'
SEARCH = [r'C:\Tools\AssetRipper', r'C:\Program Files\AssetRipper']


def locate():
    env = os.environ.get('SBCONV_ASSETRIPPER')
    cands = [env] if env else []
    cands += SEARCH
    cands += [os.path.join(p, 'AssetRipper') for p in os.environ.get('PATH', '').split(os.pathsep) if p]
    for c in cands:
        if not c:
            continue
        p = Path(c)
        if p.is_file() and p.name.lower() == EXE.lower():
            return p
        if (p / EXE).is_file():
            return p / EXE
    return None


def available():
    return locate() is not None


def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class AssetRipperServer:
    """Context manager: headless AssetRipper bound to loopback only."""

    def __init__(self, exe=None, log_dir=None, startup_timeout=60):
        self.exe = Path(exe) if exe else locate()
        if not self.exe:
            raise EngineError('AssetRipper not found (set SBCONV_ASSETRIPPER to its folder)')
        self.port = _free_port()
        self.log = Path(log_dir or Path.cwd()) / f'assetripper-{self.port}.log'
        self.proc = None
        self.timeout = startup_timeout

    def __enter__(self):
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([str(self.exe), '--headless', '--port', str(self.port), '--log', '--log-path', str(self.log)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(self.exe.parent),
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        t0 = time.time()
        while time.time() - t0 < self.timeout:
            try:
                self._get('/')
                return self
            except Exception:
                if self.proc.poll() is not None:
                    raise EngineError(f'AssetRipper exited early (code {self.proc.returncode})')
                time.sleep(0.5)
        raise EngineError('AssetRipper did not start in time')

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    # -- HTTP --------------------------------------------------------------------------------
    def _url(self, path):
        return f'http://127.0.0.1:{self.port}{path}'

    def _get(self, path, timeout=30):
        with urllib.request.urlopen(self._url(path), timeout=timeout) as r:
            return r.read()

    def _post(self, path, **form):
        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(self._url(path), data=data, method='POST')

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        try:
            with opener.open(req, timeout=3600) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303):
                return e.code
            raise EngineError(f'AssetRipper {path} failed: HTTP {e.code}')

    def reset(self):
        self._post('/Reset')

    def load(self, paths):
        for p in paths:
            self._post('/LoadFile', Path=str(p))

    def load_folder(self, folder):
        self._post('/LoadFolder', Path=str(folder))

    def export_project(self, out_dir):
        out = Path(out_dir)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        self._post('/Export/UnityProject', Path=str(out))
        return out


def capabilities():
    exe = locate()
    return EngineCapabilities('AssetRipper', '2.x (external process)', 'GPL-3.0 (not linked or redistributed)',
                              'Unity 3.5 - 6000', ['AnimationClip(.anim)', 'AnimatorController(.controller)', 'Prefab(.prefab)'],
                              [f'located at {exe}' if exe else 'NOT FOUND']).as_dict()
