"""Synthetic SR1/SR2 existence receipts, not native parser or runtime proof."""
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.counterpart import realms
from bridge.profile import native_proof
from bridge.verification.receipts import validate_receipt


class DiskReadbackReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cid = 'a' * 32
        self.payload = b'synthetic receipt fixture; not a game save'
        self.options = {'save_root': self.root,
                        'provider': {'provider_id': 'synthetic.disk-reader', 'provider_version': '1.0.0'},
                        'observed_at': '2026-10-05T00:00:00Z', 'build_fingerprint': 'synthetic-build'}

    def fixture(self, realm):
        marker = ('sb1_' if realm == 'SR1' else 'sb2_') + self.cid
        filename = marker + ('_0' if realm == 'SR1' else '_9999999999')
        target = self.root / (filename + '.sav')
        target.write_bytes(self.payload)
        result = {'creation_id': self.cid, 'operation_id': self.cid + ':create', 'target_realm': realm,
                  'native_save_id': marker, 'native_filename': filename,
                  'session': {'pid': 1234, 'session_id': 'synthetic-session'},
                  'save_hash': hashlib.sha256(self.payload).hexdigest()}
        return target, result

    def wrappers(self):
        self.assertTrue(callable(getattr(realms, 'sr2_disk_readback_receipt', None)),
                        'SR2 existence disk receipt wrapper is required')
        return (('SR1', realms.sr1_disk_readback_receipt), ('SR2', realms.sr2_disk_readback_receipt))

    def test_sr2_and_sr1_issue_only_the_exact_existence_disk_receipt(self):
        for realm, wrapper in self.wrappers():
            with self.subTest(realm=realm):
                target, result = self.fixture(realm)
                receipt = wrapper(result, **self.options)
                self.assertEqual(receipt['check_id'], 'counterpart.native_save_exists')
                self.assertEqual(receipt['subject_id'], self.cid)
                self.assertEqual(receipt['realm'], realm)
                self.assertEqual((receipt['status'], receipt['evidence_type']), ('PASSED', 'DISK_READBACK'))
                self.assertEqual(receipt['artifact_hashes'], {'save': result['save_hash']})
                self.assertEqual(receipt['details'], {'native_save_id': result['native_save_id'],
                                 'identity': realm.lower() + '-creation:' + self.cid, 'os_flush_completed': True})
                self.assertEqual(validate_receipt(receipt), [])
                self.assertEqual(target.read_bytes(), self.payload)

    def test_supplied_target_realm_must_match_the_wrapper_without_flushing(self):
        for realm, wrapper in self.wrappers():
            _, result = self.fixture(realm)
            for target_realm in (None, '', 'sr1', 'SR3', True, [], {}, 'SR2' if realm == 'SR1' else 'SR1'):
                with self.subTest(realm=realm, target_realm=target_realm):
                    with patch.object(native_proof.os, 'fsync') as flushing:
                        receipt = wrapper({**result, 'target_realm': target_realm}, **self.options)
                    self.assertEqual(receipt['status'], 'FAILED')
                    self.assertEqual(receipt['realm'], realm)
                    self.assertEqual(receipt['artifact_hashes'], {})
                    self.assertEqual(validate_receipt(receipt), [])
                    flushing.assert_not_called()
            # SR1 callers predating target_realm keep their normalized-result API.
            legacy = dict(result)
            del legacy['target_realm']
            self.assertEqual(wrapper(legacy, **self.options)['status'], 'PASSED')

    def test_invalid_normalized_fields_fail_closed_without_forging_artifacts(self):
        for realm, wrapper in self.wrappers():
            _, result = self.fixture(realm)
            for field, value in (('native_filename', None), ('native_filename', ['bad']),
                                 ('native_save_id', 'foreign'), ('native_save_id', None),
                                 ('save_hash', 'A' * 64), ('save_hash', None), ('save_hash', ['bad']),
                                 ('operation_id', None), ('operation_id', ''),
                                 ('session', None), ('session', []), ('session', {}),
                                 ('session', {'pid': True}), ('session', {'pid': 0}),
                                 ('session', {'pid': 1234.0})):
                with self.subTest(realm=realm, field=field, value=value):
                    receipt = wrapper({**result, field: value}, **self.options)
                    self.assertEqual((receipt['status'], receipt['evidence_type']), ('FAILED', 'DISK_READBACK'))
                    self.assertEqual(receipt['artifact_hashes'], {})
                    self.assertNotIn('os_flush_completed', receipt['details'])
                    self.assertEqual(receipt['diagnostics'][0]['code'], realm + '_DISK_READBACK_FAILED')
                    self.assertEqual(validate_receipt(receipt), [])
            for field in ('native_filename', 'operation_id', 'session', 'save_hash', 'native_save_id'):
                with self.subTest(realm=realm, missing=field):
                    malformed = dict(result)
                    del malformed[field]
                    self.assertEqual(wrapper(malformed, **self.options)['status'], 'FAILED')

    def test_outer_result_cannot_fabricate_an_unbound_creation_subject(self):
        for realm, wrapper in self.wrappers():
            _, result = self.fixture(realm)
            for malformed in (None, [], True, 'result', {}, {**result, 'creation_id': None},
                              {**result, 'creation_id': 'A' * 32}):
                with self.subTest(realm=realm, malformed=malformed):
                    with patch.object(native_proof.os, 'fsync') as flushing:
                        with self.assertRaises(ValueError):
                            wrapper(malformed, **self.options)
                    flushing.assert_not_called()

    def test_disk_absence_bad_hash_and_flush_failure_never_pass(self):
        for realm, wrapper in self.wrappers():
            target, result = self.fixture(realm)
            target.unlink()
            self.assertEqual(wrapper(result, **self.options)['status'], 'FAILED')
            target.write_bytes(b'not the reported bytes')
            self.assertEqual(wrapper(result, **self.options)['status'], 'FAILED')
            target.write_bytes(self.payload)
            with patch.object(native_proof.os, 'fsync', side_effect=OSError('synthetic fsync failure')):
                self.assertEqual(wrapper(result, **self.options)['status'], 'FAILED')
            self.assertEqual(target.read_bytes(), self.payload)

    def test_reconcile_and_legacy_readonly_result_remain_disk_only(self):
        for realm, wrapper in self.wrappers():
            _, result = self.fixture(realm)
            reconcile = {**result, 'operation_id': self.cid + ':reconcile', 'operation_kind': 'reconcile',
                         'persisted': False, 'applied': False}
            receipt = wrapper(reconcile, **self.options)
            self.assertEqual((receipt['status'], receipt['check_id']), ('PASSED', 'counterpart.native_save_exists'))
            self.assertEqual(receipt['evidence_type'], 'DISK_READBACK')
            for operation_kind in ('query', '', None, [], {}):
                with self.subTest(realm=realm, operation_kind=operation_kind):
                    self.assertEqual(wrapper({**result, 'operation_kind': operation_kind}, **self.options)['status'],
                                     'FAILED')

    def test_sr2_convention_matches_only_the_local_static_creation_filename_contract(self):
        convention = realms.CONVENTIONS['SR2']
        marker = convention.native_save_id(self.cid)
        self.assertEqual(marker, 'sb2_' + self.cid)
        for suffix in ('0', '1', '9999999999'):
            self.assertTrue(convention.filename_ok(self.cid, marker + '_' + suffix))
        for filename in ('foreign_1', 'sb1_' + self.cid + '_0', marker + '_10000000000', marker + '_01',
                         marker + '_00', marker + '_-1', marker + '_1.sav', marker + '_1:stream',
                         marker + '_1\n', marker.upper() + '_1', 'sb2_' + 'c' * 32 + '_0'):
            with self.subTest(filename=filename):
                self.assertFalse(convention.filename_ok(self.cid, filename))
        self.assertEqual(convention.local_queries, ('Q019', 'Q020'))
        self.assertEqual(convention.evidence, realms.Evidence.BLOCKED_ON_LOCAL_QUERY)


if __name__ == '__main__':
    unittest.main()
