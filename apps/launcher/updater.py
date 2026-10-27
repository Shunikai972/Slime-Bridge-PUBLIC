"""Self-update of the standalone launcher from the latest GitHub release.

check()   -> {'current', 'latest', 'update'} from the public repository's latest release.
prepare() -> downloads the standalone archive for this platform, verifies it against the release's SHA256SUMS.txt and
             unpacks it under Lab/Cache/update/<version>/.
restart_into(new_dir) -> starts a small helper that waits for this launcher to exit, copies the new files over the
             install folder (the player's data in Lab/ is never deleted: files are only added or replaced) and starts the
             launcher again. A running executable cannot replace itself, hence the helper.

Only the standalone build updates itself; a source checkout updates with git or a new source zip.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = 'Shunikai972/Slime-Bridge-PUBLIC'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE = REPO_ROOT / 'Lab' / 'Cache' / 'update'
WINDOWS = sys.platform == 'win32'


class UpdateError(RuntimeError):
    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code, self.detail = code, detail


def current_version():
    try:
        return (REPO_ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    except OSError:
        return '0.0.0'


def _key(version):
    parts = []
    for p in str(version).lstrip('v').split('.'):
        digits = ''.join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def _get(url, timeout=30, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, headers={'User-Agent': 'SlimeBridge-updater', 'Accept': 'application/vnd.github+json'})
    with opener(req, timeout=timeout) as r:
        return r.read()


def latest(opener=urllib.request.urlopen):
    try:
        rel = json.loads(_get(API, opener=opener))
    except (OSError, ValueError) as exc:
        raise UpdateError('NO_NETWORK', str(exc)) from exc
    tag = rel.get('tag_name') or ''
    return {'tag': tag, 'version': tag.lstrip('v'), 'notes': (rel.get('body') or '')[:4000],
            'assets': {a['name']: a['browser_download_url'] for a in rel.get('assets', [])}}


def check(opener=urllib.request.urlopen):
    info = latest(opener)
    cur = current_version()
    return {'current': cur, 'latest': info['version'], 'update': _key(info['version']) > _key(cur),
            'frozen': bool(getattr(sys, 'frozen', False)), 'notes': info['notes']}


def asset_name(version):
    return (f'SlimeBridge-v{version}-win64-standalone.zip' if WINDOWS
            else f'SlimeBridge-v{version}-linux-x64-standalone.tar.gz')


def prepare(opener=urllib.request.urlopen, cache=CACHE):
    """Download + verify + unpack the latest standalone build; returns the folder holding the new SlimeBridge/."""
    info = latest(opener)
    name = asset_name(info['version'])
    if name not in info['assets']:
        raise UpdateError('NO_BUILD_FOR_THIS_PLATFORM', name)
    work = Path(cache) / info['version']
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    archive = work / name
    try:
        archive.write_bytes(_get(info['assets'][name], timeout=600, opener=opener))
    except OSError as exc:
        raise UpdateError('DOWNLOAD_FAILED', str(exc)) from exc
    sums = info['assets'].get('SHA256SUMS.txt')
    if sums:
        expected = {}
        for line in _get(sums, opener=opener).decode('utf-8', 'replace').splitlines():
            parts = line.split()
            if len(parts) == 2:
                expected[parts[1].lstrip('*')] = parts[0].lower()
        if name in expected and hashlib.sha256(archive.read_bytes()).hexdigest() != expected[name]:
            raise UpdateError('VERIFY_FAILED', name)
    out = work / 'new'
    if name.endswith('.zip'):
        with zipfile.ZipFile(archive) as zf:
            for n in zf.namelist():
                if n.startswith('/') or '..' in n.replace('\\', '/').split('/'):
                    raise UpdateError('BAD_ARCHIVE', n)
            zf.extractall(out)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter='data')
    folder = out / 'SlimeBridge'
    if not (folder / ('SlimeBridge.exe' if WINDOWS else 'SlimeBridge')).is_file():
        raise UpdateError('BAD_ARCHIVE', 'no launcher inside')
    return {'version': info['version'], 'folder': str(folder)}


def install_dir():
    return Path(sys.executable).resolve().parent


def restart_into(new_folder, pid=None, popen=subprocess.Popen):
    """Start the helper that swaps the files once this process exits, then relaunches. The caller exits right after."""
    if not getattr(sys, 'frozen', False):
        raise UpdateError('SOURCE_INSTALL', 'update a source checkout with git or a new source zip')
    pid = pid or os.getpid()
    target = install_dir()
    script_dir = Path(tempfile.mkdtemp(prefix='slimebridge-update-'))
    if WINDOWS:
        script = script_dir / 'update.cmd'
        # full System32 paths (PATH may not have them) and ping as the delay (timeout needs a console)
        script.write_text('\r\n'.join([
            '@echo off',
            'set "S=%SystemRoot%\\System32"',
            ':wait',
            f'"%S%\\tasklist.exe" /FI "PID eq {pid}" /NH 2>nul | "%S%\\find.exe" "{pid}" >nul && ("%S%\\PING.EXE" -n 2 127.0.0.1 >nul & goto wait)',
            # /E adds and replaces files, never deletes: the player's Lab data stays; /IS /IT also replace files that
            # look identical by size and time (VERSION 0.1.4 -> 0.1.5 has the same size)
            f'"%S%\\Robocopy.exe" "{new_folder}" "{target}" /E /IS /IT /R:5 /W:1 /NFL /NDL /NJH /NJS /NP >nul',
            f'start "" "{target / "SlimeBridge.exe"}"',
        ]) + '\r\n', encoding='utf-8')
        # no DETACHED_PROCESS: cmd pipes do not run without a console; CREATE_NO_WINDOW gives it a hidden one
        popen([str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'cmd.exe'), '/c', str(script)],
              cwd=str(script_dir), close_fds=True, stdin=subprocess.DEVNULL,
              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
    else:
        script = script_dir / 'update.sh'
        script.write_text('\n'.join([
            '#!/bin/sh',
            f'while kill -0 {pid} 2>/dev/null; do sleep 1; done',
            f'cp -a "{new_folder}/." "{target}/"',
            f'nohup "{target / "SlimeBridge"}" >/dev/null 2>&1 &',
        ]) + '\n', encoding='utf-8')
        script.chmod(0o755)
        popen(['/bin/sh', str(script)], cwd=str(script_dir), start_new_session=True, close_fds=True)
    return {'helper': str(script), 'target': str(target)}
