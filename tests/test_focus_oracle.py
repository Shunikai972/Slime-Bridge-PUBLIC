"""Pure focus evidence/ownership checks. Synthetic rows are NOT native proof."""
import importlib
import inspect
import json
from pathlib import Path
import unittest
from unittest import mock

from tools.lab import oracle

try:
    focus = importlib.import_module('tools.lab.focus_oracle')
except ModuleNotFoundError as error:
    if error.name != 'tools.lab.focus_oracle':
        raise
    focus = None


def row(sample=1):
    return {'sample': sample, 'time_seconds': 100 + sample / 4, 'utc_time': '2026-10-07T10:00:00Z',
            'own_pid': 17, 'unity_platform': 'WindowsPlayer:2', 'unity_focus_before': False,
            'run_in_background': True, 'hwnd_before': '0x7', 'foreground_thread': 29,
            'foreground_pid': 23, 'win32_error': 0, 'hwnd_after': '0x7', 'hwnd_stable': True,
            'unity_focus_after': False, 'error_stage': None, 'error_type': None, 'error_message': None}


class FocusOracleTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(focus, 'missing read-only focus oracle feature')

    def test_valid_rows_and_exception_fields_remain_raw(self):
        rows = [row(), row(2)]
        rows[1].update(unity_focus_after=None, error_stage='unity_focus_after',
                       error_type='InvalidOperationException', error_message='native getter failure')
        text = '\n'.join('[loader] lab-focus ' + json.dumps(x) for x in rows)
        self.assertEqual(focus.parse_rows(text, 17), rows)
        self.assertIn('capture-error:unity_focus_after:InvalidOperationException', focus.blockers(rows[1]))
        self.assertEqual(focus.blockers(rows[0]), [])  # Not an authorization result.

    def test_malformed_foreign_and_noncontiguous_rows_rejected(self):
        for changed in ({'own_pid': 18}, {'sample': True}, {'sample': 3}, {'unity_focus_before': 0},
                        {'foreground_pid': -1}, {'error_message': 'x' * 161}, {'time_seconds': float('inf')}):
            value = row(); value.update(changed)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                focus.parse_rows('lab-focus ' + json.dumps(value), 17)
        with self.assertRaises(ValueError):
            focus.parse_rows('lab-focus {broken', 17)

    def test_corrupt_or_unbounded_stream_rejected(self):
        with self.assertRaises(ValueError):
            focus.parse_rows('\n'.join('lab-focus ' + json.dumps(row(n)) for n in range(1, 122)), 17)
        value = row(); value.pop('foreground_pid')
        with self.assertRaises(ValueError):
            focus.parse_rows('lab-focus ' + json.dumps(value), 17)

    def test_focus_platform_and_unknown_owner_blockers_separate(self):
        value = row(); value.update(unity_platform='LinuxPlayer:13', unity_focus_before=True,
                                   foreground_pid=0, hwnd_stable=False)
        self.assertEqual(set(focus.blockers(value)), {'unsupported-unity-platform', 'unity-focus-true',
                                                     'unknown-foreground-owner', 'foreground-window-changed'})

    def test_live_to_post_cleanup_payload_replacement_rejected(self):
        first = [row(), row(2)]
        changed = [dict(x) for x in first]; changed[1]['foreground_pid'] = 24
        self.assertEqual(focus.rows_digest(first), focus.rows_digest(first))
        with self.assertRaises(ValueError):
            focus.verify_post_cleanup(changed, focus.rows_digest(first))

    def test_every_owned_child_verified_without_fabricated_processes(self):
        processes = {r: mock.Mock(spec=['pid', 'poll']) for r in ('SR1', 'SR2')}
        for number, process in enumerate(processes.values(), 17):
            process.pid = number; process.poll.return_value = None
        with mock.patch.object(focus, 'same_image', return_value=True) as images:
            focus.require_owned(processes, {'SR1': 17, 'SR2': 18})
            self.assertEqual(images.call_count, 2)
        processes['SR2'].poll.return_value = 0
        with mock.patch.object(focus, 'same_image', return_value=True), self.assertRaises(RuntimeError):
            focus.require_owned(processes, {'SR1': 17, 'SR2': 18})
        processes['SR2'].poll.return_value = None
        with mock.patch.object(focus, 'same_image', return_value=False), self.assertRaises(RuntimeError):
            focus.require_owned(processes, {'SR1': 17, 'SR2': 18})

    def test_conflicting_inherited_registry_capture_refused_before_launch(self):
        with mock.patch.dict(focus.os.environ, {'SLIMEBRIDGE_LAB_REGISTRY_OUT': 'synthetic-conflict'}), \
                mock.patch.object(focus.oracle, 'run', autospec=True) as run:
            with self.assertRaises(ValueError):
                focus.run_diagnostic(Path('synthetic-only'), 'source_1', 'target_1', 420)
            run.assert_not_called()

    def test_real_oracle_signature_bound_and_no_creation_realms(self):
        with mock.patch.object(focus.oracle, 'run', autospec=True, return_value={'passed': False}) as run:
            focus.run_diagnostic(Path('synthetic-only'), 'source_1', 'target_1', 420)
            call = run.call_args
            inspect.signature(oracle.run).bind(*call.args, **call.kwargs)
            self.assertEqual(call.kwargs['creation_realms'], ())
            self.assertEqual(call.kwargs['sr1_save_id'], 'source_1')
            self.assertEqual(call.kwargs['sr2_save_id'], 'target_1')
            self.assertTrue(call.kwargs['current_user'])
            self.assertEqual(call.kwargs['extra_env']['SR2']['SLIMEBRIDGE_LAB_CREATE'], '0')
            self.assertEqual(call.kwargs['extra_env']['SR2']['SLIMEBRIDGE_LAB_GIVE'], '0')


if __name__ == '__main__':
    unittest.main()
