"""Filesystem safety: game installs are read-only inputs, writes are confined to one output root."""

import hashlib
import json
import os
import tempfile
from pathlib import Path


class UnsafePathError(ValueError):
    pass


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(chunk)
            if not block:
                return h.hexdigest()
            h.update(block)


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def sha256_json(obj):
    return sha256_bytes(canonical_json(obj).encode('utf-8'))


def _norm(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(str(p))))


def is_within(base, path):
    b, p = _norm(base), _norm(path)
    return p == b or p.startswith(b + os.sep)


class OutputRoot:
    """The only place the converter may write. Every write path is validated against it and
    against the registered read-only game roots (so an output dir can never sit inside an install)."""

    def __init__(self, root, protected=(), dry_run=False):
        self.dry_run = dry_run
        self.root = Path(os.path.abspath(str(root)))
        for prot in protected:
            if is_within(prot, self.root) or is_within(self.root, prot):
                raise UnsafePathError(f'output root {self.root} overlaps protected game directory {prot}')
        if not dry_run:
            self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, *parts):
        target = self.root.joinpath(*parts)
        if any(part in ('..',) for part in Path(*parts).parts) or not is_within(self.root, target):
            raise UnsafePathError(f'path escapes output root: {target}')
        return target

    def write_bytes(self, rel, data):
        """Atomic write: temp file in the same directory, fsync, then os.replace."""
        target = self.resolve(rel)
        if self.dry_run:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix='.tmp-', suffix='.part')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return target

    def write_json(self, rel, obj):
        return self.write_bytes(rel, (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8'))

    def read_json(self, rel, default=None):
        try:
            return json.loads(self.resolve(rel).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default


def open_game_file(path):
    """Read-only open of a file inside a game install (never any other mode)."""
    return open(path, 'rb')


class RunLock:
    """Single-writer lock for an output directory (a stale lock whose PID is gone is taken over)."""

    def __init__(self, root):
        self.path = Path(root) / '.sbconv.lock'

    @staticmethod
    def _alive(pid):
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            try:
                os.kill(int(pid), 0)
                return True
            except OSError:
                return False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                pid = int(self.path.read_text().strip() or 0)
            except (OSError, ValueError):
                pid = 0
            if pid and self._alive(pid):
                raise RuntimeError(f'another sbconv run (PID {pid}) is using {self.path.parent}; wait for it or use another --output')
            self.path.unlink(missing_ok=True)
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)
