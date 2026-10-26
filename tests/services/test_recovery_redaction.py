"""Bundle privacy regressions discovered by independent review; all credentials are synthetic."""

import base64
import json
import tempfile
import unittest

from bridge.app import recovery as R
from bridge.core import diagnostics as D


class RecoveryPrivacyTests(unittest.TestCase):
    def test_tuples_are_redacted_before_json_serialization(self):
        diag = D.make('PROBE_FAILED', 'warning', 'core', 'synthetic fixture',
                      details={'notes': ('Bearer synthetic-tuple', '/data/private/file',
                                         {'nested': ('~/private/file',)})})
        with tempfile.TemporaryDirectory() as tmp:
            path = R.export_bundle(R.issues([diag]), tmp, name='tuple-fixture')
            exported = json.loads(path.read_text())
            self.assertEqual(exported['issues'][0]['details']['details']['notes'],
                             ['<redacted>', '<path>', {'nested': ['<path>']}])

    def test_jwt_json_whitespace_does_not_bypass_redaction(self):
        def encode(data):
            return base64.urlsafe_b64encode(data).rstrip(b'=').decode()
        payload = encode(b'{"sub":"synthetic-fixture"}')
        signature = encode(b'synthetic-signature')
        for header in (b'{"alg":"HS256"}', b'{ "alg":"HS256"}', b'{\n"alg":"HS256"}',
                       b' \n{"alg":"HS256"}'):
            with self.subTest(header=header):
                jwt = '.'.join((encode(header), payload, signature))
                self.assertEqual(R.redact('before ' + jwt + ' after'), 'before <redacted> after')
                self.assertEqual(R.redact(jwt.rsplit('.', 1)[0] + '.'), '<redacted>')
        self.assertEqual(R.redact('ranch.example.com'), 'ranch.example.com')

    def test_colon_prefixed_and_space_containing_paths_are_fully_removed(self):
        for path in (r'C:\Users\private\file', 'C://Users/private/file', '/etc/private/file',
                     r'\\server\share\private.log', '~/private/file'):
            with self.subTest(path=path):
                self.assertEqual(R.redact('root:' + path), 'root:<path>')
        for path in ('/data/Private User/config.json', r'C:\Private User\config.json',
                     r'\\server\Private Share\config.json', '~/Private User/config.json'):
            with self.subTest(path=path):
                self.assertEqual(R.redact('failed at ' + path), 'failed at <path>')
                self.assertEqual(R.redact('failed at "' + path + '" after'), 'failed at "<path>" after')
        self.assertEqual(R.redact('https://example.com/path'), 'https://example.com/path')
        self.assertEqual(R.redact('file:///Users/SyntheticOwner/private.conf'), '<path>')
        self.assertEqual(R.redact('location: file:///Users/SyntheticOwner/private.conf'), 'location: <path>')

    def test_file_uri_scheme_is_redacted_case_insensitively(self):
        for scheme in ('file', 'FILE', 'FiLe', 'fIlE'):
            with self.subTest(scheme=scheme):
                self.assertEqual(R.redact(scheme + ':///Users/SyntheticOwner/private.conf'), '<path>')
                diag = D.make('PROBE_FAILED', 'warning', 'core', 'synthetic fixture',
                              details={'location': scheme + ':///Users/SyntheticOwner/private.conf'})
                with tempfile.TemporaryDirectory() as tmp:
                    exported = R.export_bundle(R.issues([diag]), tmp, name='scheme-export').read_text()
                    self.assertNotIn('private.conf', exported)

    def test_file_uri_paths_are_not_exported_verbatim(self):
        diag = D.make('PROBE_FAILED', 'warning', 'core', 'synthetic fixture',
                      details={'location': 'file:///Users/SyntheticOwner/private.conf',
                               'endpoint': 'https://example.com/api?user=synthetic'})
        with tempfile.TemporaryDirectory() as tmp:
            path = R.export_bundle(R.issues([diag]), tmp, name='file-uri-export')
            text = path.read_text()
            self.assertNotIn('/Users/SyntheticOwner/private.conf', text)
            exported = json.loads(text)['issues'][0]['details']['details']
            self.assertEqual(exported['location'], '<path>')
            self.assertEqual(exported['endpoint'], 'https://example.com/api?user=synthetic')

    def test_sensitive_mapping_keys_are_not_exported_verbatim(self):
        diag = D.make('PROBE_FAILED', 'warning', 'core', 'synthetic fixture',
                      details={'/data/private/config.json': 'value', 'Bearer synthetic-key-field': 'value'})
        with tempfile.TemporaryDirectory() as tmp:
            path = R.export_bundle(R.issues([diag]), tmp, name='mapping-keys')
            text = path.read_text()
            self.assertNotIn('/data/private/config.json', text)
            self.assertNotIn('synthetic-key-field', text)
            self.assertEqual(json.loads(text)['issues'][0]['details']['details'],
                             {'<path>': 'value', '<redacted>': '<redacted>'})


if __name__ == '__main__':
    unittest.main()
