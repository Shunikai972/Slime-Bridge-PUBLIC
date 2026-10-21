"""Host-side receipt validation only; these fixtures are NOT native gameplay proof."""
import hashlib
import os
import stat
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from portability import directory_link, file_symlink

from bridge.profile import native_proof
from bridge.profile.native_proof import verify_sr1_creation


class NativeCreationProofTests(unittest.TestCase):
    def setUp(self):
        # Modern-receipt fixtures do not contain the protected LAB's historical evidence.
        # Dedicated quarantine regressions enforce every real pin, including total disappearance.
        from bridge.counterpart.legacy_quarantine import LegacyQuarantine
        quarantine = LegacyQuarantine({'folders': [], 'retired_creation_ids': []})
        guard = patch('tools.lab.creation_oracle.legacy_quarantine', return_value=quarantine)
        guard.start()
        self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.creation_id = 'b' * 32
        self.save_id = 'sb1_' + self.creation_id
        self.op_id = self.creation_id + ':create'
        self.file = self.root / (self.save_id + '_0.sav')
        # Transport/hash fixture, intentionally not a fabricated native SRGAME payload.
        self.file.write_bytes(b'unit-test-receipt-fixture')
        self.result = {'realm': 'SR1', 'kind': 'create_save', 'op_id': self.op_id, 'pid': 1234,
                       'creation_id': self.creation_id, 'save_id': self.save_id, 'ok': True,
                       'verified': True, 'save_file': self.file.stem,
                       'sha256': hashlib.sha256(self.file.read_bytes()).hexdigest()}

    def test_readback_matches_exact_native_receipt_and_keeps_bytes(self):
        before = self.file.read_bytes()
        proof = verify_sr1_creation(self.result, self.creation_id, self.op_id, 1234, self.root)
        self.assertEqual(proof['save_id'], self.save_id)
        self.assertEqual(proof['identity'], 'sr1-creation:' + self.creation_id)
        self.assertEqual(proof['sha256'], self.result['sha256'])
        self.assertEqual(self.file.read_bytes(), before)

    def test_wrong_identity_pid_or_hash_cannot_be_linked(self):
        for key, value in [('pid', 999), ('kind', 'query'), ('op_id', 'another:op'),
                           ('creation_id', 'c' * 32), ('verified', False),
                           ('sha256', '0' * 64), ('save_id', 'some-other-save')]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    verify_sr1_creation({**self.result, key: value}, self.creation_id,
                                        self.op_id, 1234, self.root)

    def test_file_traversal_and_colliding_internal_groups_are_refused(self):
        for filename in ('../outside', self.save_id + '_1_0', self.save_id + '_0.sav',
                         self.save_id, self.save_id + '_00', self.save_id + '_01'):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    verify_sr1_creation({**self.result, 'save_file': filename}, self.creation_id,
                                        self.op_id, 1234, self.root)

    def test_present_noncanonical_revisions_are_refused(self):
        for suffix in ('', '_00', '_01'):
            filename = self.save_id + suffix
            (self.root / (filename + '.sav')).write_bytes(self.file.read_bytes())
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    verify_sr1_creation({**self.result, 'save_file': filename}, self.creation_id,
                                        self.op_id, 1234, self.root)

    def test_replay_matches_native_result_but_not_host_receipt_time(self):
        from bridge.profile.native_proof import same_native_result
        original = {**self.result, 'received_unix': 10.0}
        replay = {**self.result, 'received_unix': 11.0}
        self.assertTrue(same_native_result(original, replay))
        self.assertFalse(same_native_result(original, None))
        self.assertFalse(same_native_result(original, {**replay, 'sha256': '0' * 64}))

    def test_native_sequence_evidence_waits_for_cleanup(self):
        import json
        from tools.lab.creation_oracle import publish_creation_evidence
        native = {'passed': True, 'receipt': 'diagnostic-fixture-not-native-proof'}
        publish_creation_evidence(self.root, native)
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertTrue(saved['native_sequence_passed'])
        publish_creation_evidence(self.root, native, overall_passed=False)
        self.assertFalse(json.loads((self.root / 'creation-result.json').read_text())['passed'])
        publish_creation_evidence(self.root, native, overall_passed=True, cleanup_completed=True)
        self.assertTrue(json.loads((self.root / 'creation-result.json').read_text())['passed'])

    def test_cleanup_publication_keeps_failed_scenario_diagnostics(self):
        import json
        from tools.lab.creation_oracle import publish_creation_evidence
        publish_creation_evidence(self.root, {'passed': False, 'error': 'native dispatch became uncertain',
                                              'creation_id': self.creation_id})
        # The generic oracle has no scenario return value when its callback raises.
        publish_creation_evidence(self.root, None, overall_passed=False, cleanup_completed=True)
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['error'], 'native dispatch became uncertain')
        self.assertEqual(saved['creation_id'], self.creation_id)
        self.assertEqual(saved['validation_scope'], 'complete_oracle')

    def test_final_publication_keeps_scenario_evidence_and_oracle_stub(self):
        # Real run creation-20261003-231126: the generic oracle replaced a raised scenario with a
        # {'passed', 'error'} stub, and publishing that stub erased creation_id and the receipt.
        import json
        from tools.lab.creation_oracle import finalize_creation_evidence, publish_creation_evidence
        receipt = {'ok': False, 'reason': 'no op journal; native creation refused'}
        publish_creation_evidence(self.root, {'passed': False, 'creation_id': self.creation_id,
                                              'native_creation_result': receipt, 'error': 'ValueError: x'})
        stub = {'passed': False, 'error': 'ValueError: x'}
        finalize_creation_evidence(self.root, {'passed': False, 'scenario': stub, 'cleanup_completed': True})
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertEqual(saved['creation_id'], self.creation_id)
        self.assertEqual(saved['native_creation_result'], receipt)
        self.assertEqual(saved['oracle_scenario_status'], stub)
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['validation_scope'], 'complete_oracle')

    def test_final_publication_without_scenario_file_records_oracle_stub(self):
        import json
        from tools.lab.creation_oracle import finalize_creation_evidence
        stub = {'passed': False, 'error': 'games did not reach loaded status'}
        finalize_creation_evidence(self.root, {'passed': False, 'scenario': stub, 'cleanup_completed': True})
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertFalse(saved['native_sequence_passed'])
        self.assertEqual(saved['oracle_scenario_status'], stub)

    def test_unfinished_prior_run_blocks_another_fresh_creation(self):
        import json
        from tools.lab.creation_oracle import require_finalized_prior_creations
        prior = self.root / 'creation-prior'
        prior.mkdir()
        with self.assertRaises(RuntimeError):
            require_finalized_prior_creations(self.root)
        (prior / 'oracle-result.json').write_text(json.dumps({'passed': False,
            'sources_untouched': True, 'saves_match_backup_after_restore': True,
            'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
            'registry_restore': {'SR1': 'reimported', 'SR2': 'reimported'}}))
        require_finalized_prior_creations(self.root)
        (prior / 'oracle-result.json').write_text(json.dumps({'passed': True}))
        with self.assertRaises(RuntimeError):
            require_finalized_prior_creations(self.root)

    def test_unchanged_registry_does_not_require_an_import_receipt(self):
        import json
        from tools.lab.creation_oracle import require_finalized_prior_creations
        prior = self.root / 'creation-clean'
        prior.mkdir()
        receipt = {'passed': False, 'sources_untouched': True,
                   'saves_match_backup_after_restore': True,
                   'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
                   'registry_changes': {realm: {'removed_or_changed': [], 'added_or_changed': []}
                                        for realm in ('SR1', 'SR2')},
                   'registry_restore': {}}
        (prior / 'oracle-result.json').write_text(json.dumps(receipt))
        require_finalized_prior_creations(self.root)
        receipt['registry_changes']['SR2']['added_or_changed'] = ['test-fixture']
        (prior / 'oracle-result.json').write_text(json.dumps(receipt))
        with self.assertRaises(RuntimeError):
            require_finalized_prior_creations(self.root)
        receipt['registry_restore']['SR2'] = 'reimported'
        (prior / 'oracle-result.json').write_text(json.dumps(receipt))
        require_finalized_prior_creations(self.root)

    def test_incomplete_cleanup_blocks_another_fresh_creation(self):
        import json
        from tools.lab.creation_oracle import require_finalized_prior_creations
        prior = self.root / 'creation-incomplete-cleanup'
        prior.mkdir()
        base = {'passed': False, 'sources_untouched': True, 'saves_match_backup_after_restore': True,
                'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
                'registry_restore': {'SR1': 'reimported', 'SR2': 'reimported'}}
        for key, value in [('save_restore_errors', ['SR1/a.sav: OSError']), ('stop_errors', {'SR1': 'OSError'})]:
            with self.subTest(key=key):
                (prior / 'oracle-result.json').write_text(json.dumps({**base, key: value}))
                with self.assertRaises(RuntimeError):
                    require_finalized_prior_creations(self.root)

    def test_failed_creation_oracle_preserves_partial_evidence(self):
        import json
        from tools.lab.creation_oracle import make_scenario
        scenario = make_scenario(self.root, 1, 1)
        with self.assertRaises(RuntimeError):
            scenario(None, {}, {}, procs={})
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertIn('initially own exactly', saved['error'])

    def test_reconcile_can_validate_the_same_native_identity_without_recreation(self):
        op_id = self.creation_id + ':reconcile'
        result = {**self.result, 'kind': 'reconcile_save', 'op_id': op_id}
        proof = verify_sr1_creation(result, self.creation_id, op_id, 1234, self.root,
                                    expected_kind='reconcile_save')
        self.assertEqual(proof['identity'], 'sr1-creation:' + self.creation_id)


class RealmCreationProofTests(unittest.TestCase):
    """Synthetic bytes test disk identity/flush only, never a native parser or game."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cid = 'b' * 32
        self.op = self.cid + ':create'
        self.payload = b'synthetic-disk-readback-fixture\x00\xff'

    def fixture(self, realm, suffix='0'):
        save_id = ('sb1_' if realm == 'SR1' else 'sb2_') + self.cid
        target = self.root / (save_id + '_' + suffix + '.sav')
        target.write_bytes(self.payload)
        result = {'realm': realm, 'kind': 'create_save', 'op_id': self.op, 'pid': 1234,
                  'creation_id': self.cid, 'save_id': save_id, 'ok': True, 'verified': True,
                  'save_file': target.stem, 'sha256': hashlib.sha256(self.payload).hexdigest()}
        return target, result

    def test_marker_helpers_and_realm_specific_canonical_filename_bounds(self):
        self.assertTrue(callable(getattr(native_proof, 'creation_save_id', None)),
                        'creation marker helper is required')
        self.assertTrue(callable(getattr(native_proof, 'creation_filename_ok', None)),
                        'creation filename helper is required')
        for realm, maximum in (('SR1', '999999'), ('SR2', '9999999999')):
            with self.subTest(realm=realm):
                save_id = native_proof.creation_save_id(self.cid, realm=realm)
                self.assertEqual(save_id, ('sb1_' if realm == 'SR1' else 'sb2_') + self.cid)
                for suffix in ('0', '1', maximum):
                    target, result = self.fixture(realm, suffix)
                    self.assertTrue(native_proof.creation_filename_ok(self.cid, target.stem, realm=realm))
                    proof = native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                    self.assertEqual(proof['save_file'], str(target))
                for filename in (None, 12, True, b'name', [], {}, '', save_id, save_id + '_00',
                                 save_id + '_01', save_id + '_-1', save_id + '_+1', save_id + '_1_0',
                                 save_id + '_1' + '0' * len(maximum), save_id + '_0.sav',
                                 save_id + '_0.tmp', save_id + '_0\n', save_id + '_0:stream',
                                 save_id + '_\u0661', '../' + save_id + '_0', '..\\' + save_id + '_0',
                                 '/' + save_id + '_0', 'C:\\' + save_id + '_0',
                                 save_id.upper() + '_0', 'sb1_' + 'c' * 32 + '_0',
                                 ('sb2_' if realm == 'SR1' else 'sb1_') + self.cid + '_0'):
                    with self.subTest(filename=filename):
                        self.assertFalse(native_proof.creation_filename_ok(self.cid, filename, realm=realm))
                        with self.assertRaises(ValueError):
                            native_proof.verify_creation({**result, 'save_file': filename}, self.cid,
                                                         self.op, 1234, self.root, realm=realm)

    def test_callers_cannot_bind_invalid_pid_operation_or_command_kind(self):
        for realm in ('SR1', 'SR2'):
            _, result = self.fixture(realm)
            for pid in (None, True, False, 0, -1, 1234.0, '1234', [], {}):
                with self.subTest(realm=realm, pid=pid):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation({**result, 'pid': pid}, self.cid, self.op,
                                                     pid, self.root, realm=realm)
            for op in (None, True, 1234, [], {}, '', ' ', 'op\n', 'op/path', 'x' * 129):
                with self.subTest(realm=realm, op=op):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation({**result, 'op_id': op}, self.cid, op,
                                                     1234, self.root, realm=realm)
            for kind in (None, True, [], {}, '', 'query', 'create', 'create_save\n'):
                with self.subTest(realm=realm, kind=kind):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation({**result, 'kind': kind}, self.cid, self.op,
                                                     1234, self.root, realm=realm, expected_kind=kind)

    def test_receipt_fields_must_bind_exactly_to_the_command(self):
        for realm in ('SR1', 'SR2'):
            _, result = self.fixture(realm)
            for key, value in (('realm', 'SR2' if realm == 'SR1' else 'SR1'), ('kind', 'reconcile_save'),
                               ('op_id', 'other:operation'), ('creation_id', 'c' * 32), ('save_id', 'foreign'),
                               ('pid', 999), ('pid', True), ('pid', 1234.0), ('ok', False), ('ok', 1),
                               ('verified', False), ('verified', 1)):
                with self.subTest(realm=realm, key=key, value=value):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation({**result, key: value}, self.cid, self.op,
                                                     1234, self.root, realm=realm)
            for malformed in (None, [], {}, True, 'receipt'):
                with self.subTest(realm=realm, malformed=malformed):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation(malformed, self.cid, self.op, 1234, self.root, realm=realm)
            # SR1's historical verified read-only receipts explicitly have persisted=false.
            for flags in ({'persisted': False, 'applied': False}, {'persisted': True, 'applied': True}):
                proof = native_proof.verify_creation({**result, **flags}, self.cid, self.op,
                                                     1234, self.root, realm=realm)
                self.assertEqual(proof['save_id'], result['save_id'])
            op = self.cid + ':reconcile'
            reconcile = {**result, 'kind': 'reconcile_save', 'op_id': op, 'persisted': False}
            verifier = native_proof.verify_sr1_creation if realm == 'SR1' else native_proof.verify_sr2_creation
            self.assertEqual(verifier(reconcile, self.cid, op, 1234, self.root, expected_kind='reconcile_save'),
                             native_proof.verify_creation(reconcile, self.cid, op, 1234, self.root,
                                                          realm=realm, expected_kind='reconcile_save'))

    def test_invalid_creation_or_unknown_realm_is_rejected_before_disk_access(self):
        _, result = self.fixture('SR1')
        for realm in (None, True, 1, [], {}, '', 'SR3', 'sr1', 'SR1\n'):
            with self.subTest(realm=realm):
                with self.assertRaises(ValueError):
                    native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                with self.assertRaises(ValueError):
                    native_proof.creation_save_id(self.cid, realm=realm)
        for realm in ('SR1', 'SR2'):
            for cid in (None, True, 32, [], {}, b'b' * 32, '', 'b' * 31, 'b' * 33,
                        'B' * 32, 'g' * 32, 'b' * 32 + '\n', '../' + 'b' * 29):
                with self.subTest(realm=realm, cid=cid):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation(result, cid, self.op, 1234, self.root, realm=realm)
                    with self.assertRaises(ValueError):
                        native_proof.creation_save_id(cid, realm=realm)
                    with self.assertRaises(ValueError):
                        native_proof.creation_filename_ok(cid, result['save_file'], realm=realm)

    def test_hash_must_be_exact_lowercase_sha256_and_match_the_file(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            for digest in (None, True, 64, [], {}, b'0' * 64, '', '0' * 63, '0' * 65,
                           'A' * 64, 'g' * 64, result['sha256'] + '\n', '0' * 64):
                with self.subTest(realm=realm, digest=digest):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation({**result, 'sha256': digest}, self.cid, self.op,
                                                     1234, self.root, realm=realm)
            self.assertEqual(target.read_bytes(), self.payload)

    def test_save_root_must_be_an_explicit_absolute_path_without_parent_traversal(self):
        nested = self.root / 'nested'
        nested.mkdir()
        for realm in ('SR1', 'SR2'):
            _, result = self.fixture(realm)
            for root in (None, True, 12, [], {}, b'root', '', '.',
                         self.root.name, nested / '..'):
                with self.subTest(realm=realm, root=root):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, root, realm=realm)

    def test_lexical_reparse_ancestors_are_checked_before_any_native_file_open(self):
        self.assertTrue(self.root.is_absolute())
        real_lstat = Path.lstat
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            for unsafe in (target, self.root, self.root.parent):
                def lstat(path, unsafe=unsafe):
                    if path == unsafe:
                        return SimpleNamespace(st_mode=stat.S_IFDIR if path != target else stat.S_IFREG,
                                               st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                    return real_lstat(path)
                with self.subTest(realm=realm, unsafe=unsafe):
                    # Simulate a Windows non-junction, non-symlink reparse attribute.
                    with patch.object(Path, 'lstat', lstat), \
                            patch.object(Path, 'open', autospec=True, side_effect=Path.open) as opening:
                        with self.assertRaisesRegex(ValueError, 'reparse'):
                            native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                        opening.assert_not_called()

    def test_real_directory_reparse_ancestor_is_refused_even_with_parent_collapse(self):
        nested = self.root / 'nested'
        nested.mkdir()
        with directory_link(self.root / 'link', self.root) as link:
            for realm in ('SR1', 'SR2'):
                target, result = self.fixture(realm)
                for root in (link, link / 'nested' / '..'):
                    with self.subTest(realm=realm, root=root):
                        with self.assertRaisesRegex(ValueError, 'reparse|path|traversal'):
                            native_proof.verify_creation(result, self.cid, self.op, 1234, root, realm=realm)
                self.assertEqual(target.read_bytes(), self.payload)

    def test_real_file_symlink_at_the_owned_filename_is_refused(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            outside = self.root / 'unowned-source.sav'
            target.replace(outside)
            file_symlink(target, outside)  # Only this test may skip unavailable Windows privilege.
            with self.subTest(realm=realm):
                with self.assertRaisesRegex(ValueError, 'reparse'):
                    native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            target.unlink()
            self.assertEqual(outside.read_bytes(), self.payload)

    def test_missing_file_or_directory_at_the_owned_filename_is_refused(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            target.unlink()
            with self.subTest(realm=realm, kind='missing'):
                with self.assertRaises((OSError, ValueError)):
                    native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            target.mkdir()
            with self.subTest(realm=realm, kind='directory'):
                with self.assertRaises(ValueError):
                    native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)

    def test_hashing_again_after_fsync_rejects_changed_bytes_and_flush_failures(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            def change_on_flush(fd):
                target.write_bytes(b'changed fixture bytes')
            with self.subTest(realm=realm, kind='changed'):
                with patch.object(native_proof.os, 'fsync', side_effect=change_on_flush):
                    with self.assertRaises(ValueError):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            target.write_bytes(self.payload)
            with self.subTest(realm=realm, kind='fsync-error'):
                with patch.object(native_proof.os, 'fsync', side_effect=OSError('injected flush failure')):
                    with self.assertRaises(OSError):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            self.assertEqual(target.read_bytes(), self.payload)

    def test_same_bytes_with_a_changed_file_timestamp_are_not_stable_proof(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            before = target.stat()
            def change_on_flush(fd):
                os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
            with self.subTest(realm=realm):
                with patch.object(native_proof.os, 'fsync', side_effect=change_on_flush):
                    with self.assertRaisesRegex(ValueError, 'changed|stable|bound'):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            self.assertEqual(target.read_bytes(), self.payload)

    def test_identical_byte_replacement_between_path_check_and_open_is_refused(self):
        real_open = Path.open
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            replacement = self.root / 'replacement.sav'
            replacement.write_bytes(self.payload)
            before = target.stat()
            os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
            def replacing_open(path, *args, **kwargs):
                if path == target:
                    os.replace(replacement, target)
                return real_open(path, *args, **kwargs)
            with self.subTest(realm=realm):
                with patch.object(Path, 'open', replacing_open), patch.object(native_proof.os, 'fsync') as flush:
                    with self.assertRaisesRegex(ValueError, 'changed|stable|bound'):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                    flush.assert_not_called()
            self.assertEqual(target.read_bytes(), self.payload)

    def test_files_disappearing_or_replaced_on_close_cannot_receive_a_proof(self):
        real_open = Path.open
        for realm in ('SR1', 'SR2'):
            for effect in ('disappear', 'replace'):
                target, result = self.fixture(realm)
                replacement = self.root / 'replacement.sav'
                replacement.write_bytes(self.payload)
                @contextmanager
                def changing_open(path, *args, **kwargs):
                    with real_open(path, *args, **kwargs) as native:
                        yield native
                    if path == target:
                        if effect == 'disappear':
                            target.unlink()
                        else:
                            os.replace(replacement, target)
                with self.subTest(realm=realm, effect=effect):
                    with patch.object(Path, 'open', changing_open):
                        with self.assertRaises((ValueError, OSError)):
                            native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)

    def test_reparse_introduced_during_flush_is_refused_on_final_path_check(self):
        real_lstat = Path.lstat
        for realm in ('SR1', 'SR2'):
            _, result = self.fixture(realm)
            flushed = False
            def flush(fd):
                nonlocal flushed
                flushed = True
            def lstat(path):
                if path == self.root and flushed:
                    return SimpleNamespace(st_mode=stat.S_IFDIR,
                                           st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                return real_lstat(path)
            with self.subTest(realm=realm):
                with patch.object(Path, 'lstat', lstat), patch.object(native_proof.os, 'fsync', side_effect=flush):
                    with self.assertRaisesRegex(ValueError, 'reparse'):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)

    def test_native_filename_hardlinked_to_an_unowned_file_is_refused(self):
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            other = self.root / 'unowned.sav'
            other.write_bytes(self.payload)
            target.unlink()
            target.hardlink_to(other)
            with self.subTest(realm=realm):
                with self.assertRaisesRegex(ValueError, 'link|bound'):
                    native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
            target.unlink()
            self.assertEqual(other.read_bytes(), self.payload)

    def test_windows_birthtime_binds_both_stat_views_without_assuming_ctime_semantics(self):
        real_lstat, real_fstat = Path.lstat, os.fstat
        for realm in ('SR1', 'SR2'):
            target, result = self.fixture(realm)
            def state_view(state, change_time):
                values = {key: getattr(state, key, 0) for key in
                          ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size',
                           'st_mtime_ns', 'st_file_attributes')}
                return SimpleNamespace(**values, st_birthtime_ns=100, st_ctime_ns=change_time)
            def lstat(path):
                state = real_lstat(path)
                return state_view(state, 100) if path == target else state
            def fstat(fd):
                return state_view(real_fstat(fd), 200)
            with self.subTest(realm=realm):
                # Real Windows can report birthtime through lstat's deprecated ctime
                # while fstat's ctime reports a later change; this is not a mutation.
                with patch.object(Path, 'lstat', lstat), patch.object(native_proof.os, 'fstat', side_effect=fstat):
                    proof = native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                self.assertEqual(proof['sha256'], result['sha256'])

    def test_handle_change_time_is_checked_even_when_birthtime_does_not_change(self):
        real_fstat = os.fstat
        for realm in ('SR1', 'SR2'):
            _, result = self.fixture(realm)
            flushed = False
            def flush(fd):
                nonlocal flushed
                flushed = True
            def fstat(fd):
                state = real_fstat(fd)
                values = {key: getattr(state, key, 0) for key in
                          ('st_dev', 'st_ino', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns')}
                # Preserve the actual birthtime, but inject a same-handle change-time mutation.
                values['st_birthtime_ns'] = getattr(state, 'st_birthtime_ns', state.st_ctime_ns)
                values['st_ctime_ns'] = state.st_ctime_ns + (1 if flushed else 0)
                return SimpleNamespace(**values)
            with self.subTest(realm=realm):
                with patch.object(native_proof.os, 'fstat', side_effect=fstat), \
                        patch.object(native_proof.os, 'fsync', side_effect=flush):
                    with self.assertRaisesRegex(ValueError, 'changed|stable|bound'):
                        native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)

    def test_generic_readback_and_realm_wrappers_preserve_identity_and_bytes(self):
        self.assertTrue(callable(getattr(native_proof, 'verify_creation', None)),
                        'realm-generic disk verifier is required')
        self.assertTrue(callable(getattr(native_proof, 'verify_sr2_creation', None)),
                        'SR2 compatibility wrapper is required')
        for realm, verifier in (('SR1', native_proof.verify_sr1_creation),
                                ('SR2', native_proof.verify_sr2_creation)):
            with self.subTest(realm=realm):
                target, result = self.fixture(realm)
                proof = native_proof.verify_creation(result, self.cid, self.op, 1234, self.root, realm=realm)
                self.assertEqual(proof, verifier(result, self.cid, self.op, 1234, self.root))
                self.assertEqual(proof['save_id'], result['save_id'])
                self.assertEqual(proof['save_file'], str(target))
                self.assertEqual(proof['identity'], realm.lower() + '-creation:' + self.cid)
                self.assertEqual(proof['sha256'], result['sha256'])
                self.assertIs(proof['os_flush_completed'], True)
                self.assertEqual(target.read_bytes(), self.payload)


if __name__ == '__main__':
    unittest.main()
