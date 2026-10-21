"""Synthetic signed diagnostics and scratch filesystem tests; no native/game/save proof."""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.lab import native_storage_binding as binding


def signed_storage_frame(record, token='test-only'):
    raw = json.dumps(record, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    signature = hmac.new(token.encode('utf-8'), raw, hashlib.sha256).hexdigest().encode('ascii')
    return b'[synthetic] ' + binding.PREFIX + base64.b64encode(raw) + b' ' + signature + b'\n'


@contextlib.contextmanager
def append_after_binding_read(log_path, make_append):
    """Real read + same-file append before fstat, scoped to the scratch logger only."""
    original_open = Path.open
    observations = []

    class StreamBoundary:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size=-1):
            data = self.stream.read(size)
            if not observations:
                with original_open(log_path, 'ab') as writer:
                    writer.write(make_append(data))
                    writer.flush()
                    os.fsync(writer.fileno())
                observations.append({'bytes_read': len(data),
                                     'size_after_append': os.fstat(self.stream.fileno()).st_size})
            return data

    def racing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == log_path and args and args[0] == 'rb' and not observations:
            return StreamBoundary(stream)
        return stream

    with patch.object(Path, 'open', racing_open):
        yield observations


class NativeStorageBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.allowed = self.root / 'guarded-realm'
        self.native = self.allowed / 'Steam' / 'synthetic-native-account'
        self.native.mkdir(parents=True)
        self.exe = self.root / 'Lab' / 'SlimeRancher2.exe'
        self.exe.parent.mkdir()
        self.exe.write_bytes(b'synthetic-never-executed')
        self.journal = self.root / 'per-run-ops.journal'
        self.now = time.time_ns() // 1_000_000
        self.context = dict(token='test-only', pid=202, executable=self.exe,
                            journal=self.journal, guarded_root=self.allowed, now_ms=self.now)
        self.record = {'version': 1, 'realm': 'SR2', 'pid': 202,
                       'executable': str(self.exe), 'journal': str(self.journal),
                       'director_pointer': '0000000000000011',
                       'storage_pointer': '0000000000000022',
                       'provider_type': 'FileStorageProvider', 'initialized': True,
                       'native_root': str(self.native), 'sampled_utc_ms': self.now,
                       'sample_id': 1, 'binding_valid': True}

    def frame(self, record=None, *, raw=None, token='test-only'):
        if raw is None:
            raw = json.dumps(self.record if record is None else record,
                             separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        signature = hmac.new(token.encode('utf-8'), raw, hashlib.sha256).hexdigest().encode('ascii')
        return b'[synthetic] ' + binding.PREFIX + base64.b64encode(raw) + b' ' + signature + b'\n'

    def read(self, data=None, **context):
        return binding.read_storage_binding(self.frame() if data is None else data,
                                            **{**self.context, **context})

    def test_exact_native_root_flat_or_nested_and_unicode_are_supported(self):
        unicode_root = self.allowed / 'Compte-échantillon'
        unicode_root.mkdir()
        for root in (self.allowed, self.native, unicode_root):
            with self.subTest(root=root):
                data = self.frame({**self.record, 'native_root': str(root)})
                result = self.read(data)
                self.assertEqual(Path(result['native_root']), root)
                self.assertEqual(result['provider_type'], 'FileStorageProvider')
                self.assertEqual(result['sample_id'], 1)
                self.assertTrue(result['root_directory_identity'])
                self.assertEqual(len(result['payload_sha256']), 64)

    def test_authentication_never_accepts_wrong_token_or_modified_bytes(self):
        data = self.frame()
        with self.assertRaisesRegex(ValueError, 'signature mismatch'):
            self.read(data, token='different-test-only')
        changed = self.frame({**self.record, 'pid': 303})
        original_signature = data.split()[-1]
        forged = changed.rsplit(b' ', 1)[0] + b' ' + original_signature + b'\n'
        with self.assertRaisesRegex(ValueError, 'signature mismatch'):
            self.read(forged)

    def test_binding_identity_types_and_freshness_are_fail_closed(self):
        changes = [
            {'pid': 303}, {'pid': True}, {'realm': 'SR1'}, {'version': True}, {'version': 2},
            {'provider_type': 'DerivedFileStorageProvider'}, {'provider_type': 'StorageProvider'},
            {'initialized': False}, {'initialized': 1}, {'binding_valid': False},
            {'director_pointer': '0000000000000000'}, {'storage_pointer': '0000000000000000'},
            {'director_pointer': '11'}, {'storage_pointer': 22},
            {'sample_id': True}, {'sample_id': 0}, {'sample_id': 2**53 + 1},
            {'sampled_utc_ms': True}, {'sampled_utc_ms': self.now - 5001},
            {'sampled_utc_ms': self.now + 1001},
            {'executable': str(self.root / 'other.exe')},
            {'journal': str(self.root / 'other-run-ops.journal')},
            {'native_root': None}, {'native_root': 'relative-root'},
            {'native_root': str(self.allowed) + '/Steam/../elsewhere'},
            {'native_root': str(self.allowed) + '/./Steam'},
            {'native_root': str(self.allowed) + '/account:stream'},
            {'native_root': str(self.allowed) + '/account.'},
            {'native_root': str(self.allowed) + '/account '},
            {'native_root': str(self.allowed) + '/account\x00'},
            {'native_root': str(self.allowed) + '-outside'},
            {'native_root': '\\\\server\\share'},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read(self.frame({**self.record, **change}))
        for boundary in (-1000, 5000):
            with self.subTest(boundary=boundary):
                self.read(self.frame({**self.record, 'sampled_utc_ms': self.now - boundary}))

    def test_schema_duplicates_encoding_and_size_are_rejected(self):
        raw = json.dumps(self.record, separators=(',', ':')).encode('utf-8')
        variants = [self.frame({**self.record, 'extra': True}),
                    self.frame({k: v for k, v in self.record.items() if k != 'journal'}),
                    self.frame(raw=raw[:-1] + b',"pid":202}'),
                    self.frame(raw=b'null'), self.frame(raw=b'[]'), self.frame(raw=b'\xff'),
                    self.frame(raw=b' ' * (binding.MAX_PAYLOAD_BYTES + 1)),
                    binding.PREFIX + b'@invalid-base64 ' + b'0' * 64 + b'\n',
                    binding.PREFIX + b'AAAA signature extra-token\n',
                    b'x' * (binding.MAX_LOG_BYTES + 1), b'ordinary logger line without binding']
        for index, data in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.read(data)

    def test_ordered_samples_do_not_accept_replacement_or_regression(self):
        next_record = {**self.record, 'sample_id': 2, 'sampled_utc_ms': self.now + 1}
        ordered = self.frame() + self.frame(next_record)
        first, current = self.read(), self.read(ordered)
        self.assertEqual(current['sample_id'], 2)
        binding.assert_same_binding(first, current)
        with self.assertRaises(ValueError):
            binding.assert_same_binding(current, first)
        for change in ({'sample_id': 1}, {'director_pointer': '0000000000000012'},
                       {'storage_pointer': '0000000000000023'},
                       {'native_root': str(self.allowed)}, {'binding_valid': False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.read(self.frame() + self.frame({**next_record, **change}))

    def test_directory_and_opaque_reparse_ancestors_are_refused(self):
        plain_file = self.allowed / 'not-a-directory'
        plain_file.write_bytes(b'synthetic')
        with self.assertRaises(ValueError):
            self.read(self.frame({**self.record, 'native_root': str(plain_file)}))
        original_lstat = Path.lstat
        def opaque_reparse(path, *args, **kwargs):
            state = original_lstat(path, *args, **kwargs)
            if path == self.native.parent:
                return SimpleNamespace(st_mode=state.st_mode,
                                       st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return state
        with patch.object(Path, 'lstat', opaque_reparse), self.assertRaisesRegex(ValueError, 'reparse'):
            self.read()

    def test_replaced_directory_identity_is_not_a_successful_rebinding(self):
        original = self.read()
        held = self.native.with_name('previous-owned-synthetic-directory')
        self.native.rename(held)
        self.native.mkdir()
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            binding.assert_same_binding(original, self.read())

    def test_real_scratch_junction_is_rejected_without_reading_target(self):
        if os.name != 'nt':
            self.skipTest('NTFS junction probe requires Windows')
        outside = self.root / 'outside-guarded-realm'
        outside.mkdir()
        sentinel = outside / 'sentinel.txt'
        sentinel.write_bytes(b'unchanged-synthetic-sentinel')
        alias = self.allowed / 'linked-account'
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(alias), str(outside)],
                                capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        try:
            self.assertTrue(alias.is_junction())
            self.assertEqual(alias.resolve(), outside.resolve())
            with self.assertRaisesRegex(ValueError, 'reparse'):
                self.read(self.frame({**self.record, 'native_root': str(alias)}))
            self.assertEqual(sentinel.read_bytes(), b'unchanged-synthetic-sentinel')
        finally:
            alias.rmdir()  # Exact scratch junction owned by this test, not a game capture.

    def test_invalid_signed_append_observed_during_read_is_not_ignored(self):
        log = self.exe.parent / 'Latest.log'
        log.write_bytes(self.frame())
        invalid = self.frame({**self.record, 'sample_id': 2, 'binding_valid': False})
        with append_after_binding_read(log, lambda _data: invalid) as observations:
            with self.assertRaises(ValueError):
                binding.read_binding_log(log, **self.context)
        self.assertEqual(len(observations), 1)
        self.assertGreater(observations[0]['size_after_append'], observations[0]['bytes_read'])
        with self.assertRaises(ValueError):
            self.read(log.read_bytes())

    def test_healthy_signed_append_observed_during_read_is_consumed(self):
        log = self.exe.parent / 'Latest.log'
        log.write_bytes(self.frame())
        next_frame = self.frame({**self.record, 'sample_id': 2})
        with append_after_binding_read(log, lambda _data: next_frame) as observations:
            result = binding.read_binding_log(log, **self.context)
        self.assertEqual(len(observations), 1)
        self.assertEqual(result['sample_id'], 2, 'an observed complete newer sample must not be discarded')

    def test_known_logger_file_is_read_only_and_ordinary(self):
        log = self.exe.parent / 'Latest.log'
        data = b'ordinary synthetic log\n' + self.frame()
        log.write_bytes(data)
        result = binding.read_binding_log(log, **self.context)
        self.assertEqual(Path(result['native_root']), self.native)
        self.assertEqual(log.read_bytes(), data)
        duplicate = self.exe.parent / 'hardlinked-log'
        os.link(log, duplicate)
        with self.assertRaisesRegex(ValueError, 'ordinary'):
            binding.read_binding_log(log, **self.context)


if __name__ == '__main__':
    unittest.main()
