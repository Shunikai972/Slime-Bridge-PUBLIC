"""Bounded no-link input reads and exclusive report outputs; no model writes.

An offline reader, not a sandbox for concurrently mutating directories: keep input
and report directories quiescent. Paths are rechecked before each open/create.
"""
import json
import math
import os
import re
import stat
from pathlib import Path

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_DEPTH = 6
MAX_TOTAL_BYTES = 256 * 1024 * 1024


class ImportProblem(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def checked_path(value, code, *, directory=False, missing_final=False):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path == Path(path.anchor):
        raise ImportProblem(code, 'explicit absolute non-volume-root path without .. required')
    reserved = re.compile(r'(?i)^(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?$')
    if str(path).startswith(('\\\\', '//')) or any(
            any(ord(c) < 32 or c in ':<>|?*' for c in part) or part.endswith((' ', '.')) or reserved.fullmatch(part)
            for part in path.parts[1:]):
        raise ImportProblem(code, 'UNC/device/alternate-stream/spelling-alias path unsupported')
    for current in [*reversed(path.parents), path]:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if missing_final and current == path:
                return path
            raise ImportProblem(code, f'path does not exist: {current}') from None
        if linked(info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ImportProblem(code, f'links/reparse/hardlinks forbidden: {current}')
    if directory and not path.is_dir():
        raise ImportProblem(code, 'root must be an existing directory')
    return path


def read_bytes(path, limit=MAX_INPUT_BYTES):
    path = checked_path(path, 'UNSAFE_SOURCE')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ImportProblem('INPUT_LIMIT', f'not regular or larger than {limit} bytes: {path.name}')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or linked(opened) or \
                (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ImportProblem('UNSAFE_SOURCE', 'source changed or linked')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ImportProblem('INPUT_LIMIT', 'bounded read exceeded')
        after = os.fstat(fd)
        if (after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
            raise ImportProblem('SOURCE_CHANGED', 'source changed during read')
        return data
    finally:
        os.close(fd)


def read_json(path):
    return parse_json(read_bytes(path, MAX_JSON_BYTES))


def parse_json(raw):
    """Strict UTF-8 JSON: duplicate keys and nonfinite/overflowing numbers are refused."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ImportProblem('INVALID_JSON', 'duplicate JSON key')
            result[key] = value
        return result

    def finite(value):
        raise ImportProblem('INVALID_JSON', f'nonfinite JSON number: {value}')

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            finite(value)
        return number

    return json.loads(raw.decode('utf-8'),
                      object_pairs_hook=unique, parse_constant=finite, parse_float=finite_float)


def inventory(root):
    files, total, count = [], 0, 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as listing:
            for child in sorted(listing, key=lambda item: item.name):
                count += 1
                if count > MAX_ENTRIES:
                    raise ImportProblem('SCAN_LIMIT_EXCEEDED', f'more than {MAX_ENTRIES} entries')
                info = Path(child.path).lstat()  # Windows DirEntry.stat omits inode/link count
                if linked(info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                    raise ImportProblem('UNSAFE_SOURCE', f'linked source entry: {child.name}')
                if stat.S_ISDIR(info.st_mode):
                    if depth >= MAX_DEPTH:
                        raise ImportProblem('SCAN_LIMIT_EXCEEDED', f'depth exceeds {MAX_DEPTH}')
                    stack.append((Path(child.path), depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    if total > MAX_TOTAL_BYTES:
                        raise ImportProblem('SCAN_LIMIT_EXCEEDED', f'aggregate bytes exceed {MAX_TOTAL_BYTES}')
                    files.append(Path(child.path))
                else:
                    raise ImportProblem('UNSAFE_SOURCE', 'nonregular source entry')
    return sorted(files)


def output_path(value, root):
    path = checked_path(value, 'UNSAFE_OUTPUT', missing_final=True)
    if path == root or path.is_relative_to(root) or root.is_relative_to(path) or path.exists():
        raise ImportProblem('UNSAFE_OUTPUT', 'new exclusive file outside root required; ancestors/overlap rejected')
    return path


def write_report(path, root, raw):
    path = output_path(path, root)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    try:
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw.encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ImportProblem('UNSAFE_OUTPUT', str(exc)[:160]) from exc
