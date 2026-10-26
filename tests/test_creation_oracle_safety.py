"""Oracle safety regressions; mocked controls and synthetic files only."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.lab import oracle


class OracleStopSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.games = {realm: self.root / realm / name for realm, name in
                      (('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe'))}
        for exe in self.games.values():
            exe.parent.mkdir()
            exe.write_bytes(b'synthetic-never-launched')

    def child(self, pid=101):
        child = Mock(pid=pid)
        child.poll.return_value = None
        return child

    def test_unknown_image_refuses_all_controls_and_records_stop_error(self):
        child = self.child()
        with patch.dict('tools.lab.oracle.GAMES', self.games), \
             patch('tools.lab.oracle.process_image_path', return_value=None):
            errors = oracle._stop_owned({'SR1': child})
        self.assertIn('SR1', errors)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()
        child.wait.assert_not_called()


    def test_escalation_refuses_kill_when_fresh_image_is_unknown_or_mismatched(self):
        import subprocess
        for fresh in (None, self.root / 'outside.exe'):
            with self.subTest(fresh=fresh):
                child = self.child()
                child.wait.side_effect = subprocess.TimeoutExpired('synthetic-child', 20)
                with patch.dict('tools.lab.oracle.GAMES', self.games), \
                     patch('tools.lab.oracle.process_image_path',
                           side_effect=[self.games['SR1'].resolve(), fresh]) as images:
                    errors = oracle._stop_owned({'SR1': child})
                self.assertIn('SR1', errors)
                child.terminate.assert_called_once_with()
                child.kill.assert_not_called()
                self.assertEqual(images.call_count, 2)

    def test_verified_escalation_queries_image_immediately_before_each_control(self):
        import subprocess
        calls = []
        child = self.child()
        child.terminate.side_effect = lambda: calls.append('terminate')
        child.kill.side_effect = lambda: calls.append('kill')
        child.wait.side_effect = [subprocess.TimeoutExpired('synthetic-child', 20), None]
        with patch.dict('tools.lab.oracle.GAMES', self.games), \
             patch('tools.lab.oracle.process_image_path',
                   side_effect=lambda pid: calls.append(('image', pid)) or self.games['SR1'].resolve()):
            errors = oracle._stop_owned({'SR1': child})
        self.assertEqual(errors, {})
        self.assertEqual(calls, [('image', 101), 'terminate', ('image', 101), 'kill'])


class OracleEvidenceSafetyTests(unittest.TestCase):
    def setUp(self):
        # Modern-receipt fixtures do not contain the protected LAB's historical evidence.
        # Dedicated quarantine regressions enforce every real pin, including total disappearance.
        from bridge.counterpart.legacy_quarantine import LegacyQuarantine
        quarantine = LegacyQuarantine({'folders': [], 'retired_creation_ids': []})
        guard = patch('tools.lab.creation_oracle.legacy_quarantine', return_value=quarantine)
        guard.start()
        self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_creation_success_requires_literal_cleanup_confirmation(self):
        import json
        from tools.lab import creation_oracle as creation
        missing = object()
        for marker in (missing, None, False, 0, 1, 'not completed', [], {}):
            with self.subTest(marker=repr(marker)):
                creation.publish_creation_evidence(self.root, {'passed': True, 'receipt': 'synthetic'})
                result: dict[str, object] = {'passed': True}
                if marker is not missing:
                    result['cleanup_completed'] = marker
                creation.finalize_creation_evidence(self.root, result)
                saved = json.loads((self.root / 'creation-result.json').read_text())
                self.assertFalse(saved['passed'])
                self.assertTrue(saved['native_sequence_passed'])
        creation.finalize_creation_evidence(self.root, {'passed': True, 'cleanup_completed': True})
        self.assertTrue(json.loads((self.root / 'creation-result.json').read_text())['passed'])


    def test_direct_publisher_cannot_bypass_cleanup_gate(self):
        import json
        from tools.lab import creation_oracle as creation
        creation.publish_creation_evidence(self.root, {'passed': True}, overall_passed=True)
        saved = json.loads((self.root / 'creation-result.json').read_text())
        self.assertFalse(saved['passed'])
        self.assertTrue(saved['native_sequence_passed'])
        self.assertEqual(saved['validation_scope'], 'awaiting_cleanup')

    def test_generic_success_requires_literal_cleanup_confirmation(self):
        import json
        missing = object()
        for marker in (missing, None, False, 0, 1, 'not completed', [], {}):
            with self.subTest(marker=repr(marker)):
                result: dict[str, object] = {'passed': True}
                if marker is not missing:
                    result['cleanup_completed'] = marker
                oracle._write_evidence(result, self.root)
                path = max(self.root.glob('oracle-*.json'), key=lambda file: file.stat().st_mtime_ns)
                saved = json.loads(path.read_text())
                self.assertFalse(saved['passed'])
                self.assertEqual(saved['validation_scope'], 'awaiting_cleanup')
                self.assertIs(result['passed'], True, 'publication must not erase provisional native status')
        oracle._write_evidence({'passed': True, 'cleanup_completed': True}, self.root)
        path = max(self.root.glob('oracle-*.json'), key=lambda file: file.stat().st_mtime_ns)
        self.assertTrue(json.loads(path.read_text())['passed'])


    def test_prior_cleanup_requires_explicit_lab_and_cleanup_confirmations(self):
        import json
        from tools.lab import creation_oracle as creation
        prior = self.root / 'creation-prior'
        prior.mkdir()
        base: dict[str, object] = {
            'sources_untouched': True, 'saves_match_backup_after_restore': True,
            'registry_changes': {realm: {'removed_or_changed': [], 'added_or_changed': []}
                                 for realm in ('SR1', 'SR2')},
            'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
        }
        missing = object()
        for key in ('cleanup_completed', 'lab_fingerprints_unchanged'):
            for marker in (missing, None, False, 0, 1, 'confirmed', [], {}):
                with self.subTest(key=key, marker=repr(marker)):
                    receipt = dict(base)
                    if marker is missing:
                        receipt.pop(key)
                    else:
                        receipt[key] = marker
                    (prior / 'oracle-result.json').write_text(json.dumps(receipt))
                    with self.assertRaisesRegex(RuntimeError, 'existing-intent recovery'):
                        creation.require_finalized_prior_creations(self.root)
        (prior / 'oracle-result.json').write_text(json.dumps(base))
        creation.require_finalized_prior_creations(self.root)

    def test_legacy_receipt_does_not_silently_confirm_new_cleanup_guards(self):
        import json
        from tools.lab import creation_oracle as creation
        prior = self.root / 'creation-legacy'
        prior.mkdir()
        (prior / 'oracle-result.json').write_text(json.dumps({
            'sources_untouched': True, 'saves_match_backup_after_restore': True,
            'registry_restore': {'SR1': 'reimported', 'SR2': 'reimported'},
        }))
        with self.assertRaisesRegex(RuntimeError, 'Legacy.*existing-intent recovery'):
            creation.require_finalized_prior_creations(self.root)


if __name__ == '__main__':
    unittest.main()
