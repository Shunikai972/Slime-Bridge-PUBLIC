"""Read-only, per-run SR2 native storage-root binding for the protected LAB.

This is a signed diagnostic, not a creation-protocol extension or native save parser.
The producer proves the concrete native provider and guards its ownership through
CREATE. The consumer admits its exact directory beneath the already guarded realm
root. Filesystem checks detect observed changes; they are not an atomic NTFS sandbox.
No filename search, account guess, pathname fallback, or historical receipt mutation.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import stat
import time
from pathlib import Path

PREFIX = b'SLIMEBRIDGE_STORAGE_BINDING_V1 '
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_PAYLOAD_BYTES = 8192
FIELDS = frozenset(('version', 'realm', 'pid', 'executable', 'journal',
                    'director_pointer', 'storage_pointer', 'provider_type',
                    'initialized', 'native_root', 'sampled_utc_ms', 'sample_id',
                    'binding_valid'))
IDENTITY_FIELDS = FIELDS - {'sampled_utc_ms', 'sample_id'}


def _fail(reason: str) -> ValueError:
    # Never include the token, raw log, or untrusted payload in an exception.
    return ValueError('native storage binding: ' + reason)


def _absolute_path(value: str) -> Path:
    if (type(value) is not str or not value or len(value) > 2048 or
            any(ord(char) < 32 for char in value) or value.startswith(('\\\\', '//'))):
        raise _fail('invalid absolute path')
    path = Path(value)
    if not path.is_absolute():
        raise _fail('relative path')
    # Check the original string before Path can discard dot components.
    for part in re.split(r'[/\\]', value):
        if not part or part == path.drive:
            continue
        if part in ('.', '..') or ':' in part or part.rstrip(' .') != part:
            raise _fail('unsafe path component')
    return path


def _directory_identity(root: Path) -> list:
    identities = []
    for component in reversed((root, *root.parents)):
        state = component.lstat()
        attrs = getattr(state, 'st_file_attributes', 0)
        if stat.S_ISLNK(state.st_mode) or attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise _fail('reparse directory or ancestor')
        if not stat.S_ISDIR(state.st_mode) or not state.st_ino:
            raise _fail('not an identifiable directory')
        # Directory mtime/size can legitimately change when the native save appears.
        birth = getattr(state, 'st_birthtime_ns', None)
        identities.append([str(component), state.st_dev, state.st_ino, state.st_mode, birth])
    return identities


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _fail('duplicate JSON field')
        result[key] = value
    return result


def read_storage_binding(log_data: bytes, *, token: str, pid: int,
                         executable: Path, journal: Path, guarded_root: Path,
                         now_ms: int | None = None) -> dict:
    """Authenticate ordered native samples and admit their exact current directory."""
    if (type(log_data) is not bytes or len(log_data) > MAX_LOG_BYTES or
            type(token) is not str or not token or type(pid) is not int or pid <= 0):
        raise _fail('invalid consumer context')
    expected_exe = _absolute_path(str(executable))
    expected_journal = _absolute_path(str(journal))
    allowed = _absolute_path(str(guarded_root))
    latest = None
    latest_raw = None
    previous_id = 0
    for line in log_data.splitlines():
        if PREFIX not in line:
            continue
        frame = line.split(PREFIX, 1)[1].split()
        if len(frame) != 2 or len(frame[0]) > 4 * ((MAX_PAYLOAD_BYTES + 2) // 3):
            raise _fail('malformed signed diagnostic')
        encoded, signature = frame
        if re.fullmatch(rb'[0-9a-f]{64}', signature) is None:
            raise _fail('invalid signature shape')
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise _fail('invalid payload encoding') from exc
        if not raw or len(raw) > MAX_PAYLOAD_BYTES:
            raise _fail('oversized or empty payload')
        expected = hmac.new(token.encode('utf-8'), raw, hashlib.sha256).hexdigest().encode('ascii')
        if not hmac.compare_digest(expected, signature):
            raise _fail('signature mismatch')
        try:
            record = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise _fail('invalid signed JSON') from exc
        if type(record) is not dict or set(record) != FIELDS:
            raise _fail('unexpected diagnostic schema')
        if (type(record['version']) is not int or record['version'] != 1 or
                record['realm'] != 'SR2' or type(record['pid']) is not int or record['pid'] != pid or
                record['provider_type'] != 'FileStorageProvider' or
                record['initialized'] is not True or record['binding_valid'] is not True):
            raise _fail('invalid native provider binding')
        for name in ('director_pointer', 'storage_pointer'):
            value = record[name]
            if (type(value) is not str or re.fullmatch(r'[0-9a-f]{16}', value) is None or
                    int(value, 16) == 0):
                raise _fail('invalid native pointer identity')
        if (_absolute_path(record['executable']) != expected_exe or
                _absolute_path(record['journal']) != expected_journal):
            raise _fail('process or run descriptor mismatch')
        sample = record['sample_id']
        sampled = record['sampled_utc_ms']
        if (type(sample) is not int or not 0 < sample <= 2**53 or sample <= previous_id or
                type(sampled) is not int or not 0 < sampled <= 2**53):
            raise _fail('invalid or unordered sample')
        if latest is not None and any(record[key] != latest[key] for key in IDENTITY_FIELDS):
            raise _fail('owner, provider or root changed within this run')
        previous_id, latest, latest_raw = sample, record, raw
    if latest is None or latest_raw is None:
        raise _fail('missing signed diagnostic')
    current_ms = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if type(current_ms) is not int or not -1000 <= current_ms - latest['sampled_utc_ms'] <= 5000:
        raise _fail('stale or future diagnostic')
    root = _absolute_path(latest['native_root'])
    if not root.is_relative_to(allowed):
        raise _fail('native root is outside guarded realm directory')
    before = _directory_identity(root)
    if _directory_identity(root) != before:
        raise _fail('directory identity changed during binding')
    return {**latest, 'payload_sha256': hashlib.sha256(latest_raw).hexdigest(),
            'root_directory_identity': before}


def assert_same_binding(original: dict, current: dict) -> None:
    """Revalidate ownership/directory identity without freezing advancing timestamps."""
    if (any(original.get(key) != current.get(key) for key in IDENTITY_FIELDS) or
            original.get('root_directory_identity') != current.get('root_directory_identity') or
            current['sample_id'] < original['sample_id']):
        raise _fail('previously bound ownership or directory identity changed')


def read_binding_log(log_path: Path, **context) -> dict:
    """Read only the known LAB logger file, checking its identity while it appends."""
    log = _absolute_path(str(log_path))
    parent_before = _directory_identity(log.parent)
    before = log.lstat()

    def file_identity(state):
        if (not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or
                getattr(state, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise _fail('logger is not an ordinary owned file')
        return (state.st_dev, state.st_ino, getattr(state, 'st_birthtime_ns', None))

    identity = file_identity(before)
    with log.open('rb') as stream:
        snapshot = os.fstat(stream.fileno())
        if file_identity(snapshot) != identity:
            raise _fail('logger was replaced before read')
        data = b''
        # A logger can append between read and fstat. Consume the observed tail,
        # including invalidations, rather than authorize from an older prefix.
        # Bound catch-up; continuous growth or truncation is not a valid snapshot.
        for _attempt in range(3):
            data += stream.read(MAX_LOG_BYTES + 1 - len(data))
            observed = os.fstat(stream.fileno())
            if file_identity(observed) != identity:
                raise _fail('logger identity changed during read')
            if len(data) > MAX_LOG_BYTES or observed.st_size > MAX_LOG_BYTES:
                raise _fail('oversized logger')
            if observed.st_size < len(data):
                raise _fail('logger truncated during read')
            if observed.st_size == len(data):
                if (snapshot.st_size == observed.st_size and
                        snapshot.st_mtime_ns != observed.st_mtime_ns):
                    raise _fail('logger content changed without append')
                break
            snapshot = observed
        else:
            raise _fail('logger did not stabilize during bounded read')
    after = log.lstat()
    if (file_identity(after) != identity or after.st_size != len(data) or
            after.st_mtime_ns != observed.st_mtime_ns or
            _directory_identity(log.parent) != parent_before):
        raise _fail('logger or its parent changed after read')
    return read_storage_binding(data, **context)
