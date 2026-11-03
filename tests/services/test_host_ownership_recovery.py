"""Ownership/cleanup regressions: Python-only, no .NET, game or native save operations."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from apps.launcher.runtime import HostProcess
from bridge.app.hostlife import HostController
from services.test_host_lifecycle import _cleanup_tmp

ROOT=Path(__file__).resolve().parents[2]


class HostOwnershipRecoveryTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'Lab/T'
        base.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='host-',dir=base)
        self.addCleanup(_cleanup_tmp,self.tmp)
        self.root=Path(self.tmp.name)

    def controller(self):
        host=HostController(self.root/'legacy')
        child=mock.Mock()
        child.poll.return_value=None
        child.wait.return_value=0
        host._child=child
        host._generation='owned-generation'
        return host,child

    def test_owned_controller_stops_even_when_record_is_missing(self):
        host,child=self.controller()
        self.assertFalse(host.record_path().exists())
        result=host.stop()
        self.assertTrue(result['stopped'])
        child.terminate.assert_called_once_with()
        child.wait.assert_called_once()
        self.assertIsNone(host._child)

    def test_controller_keeps_owned_handle_if_termination_fails(self):
        host,child=self.controller()
        child.terminate.side_effect=OSError('synthetic owned-child termination denied')
        with self.assertRaises(OSError):host._terminate_child()
        self.assertIs(host._child,child)
        self.assertEqual(host._generation,'owned-generation')

    def test_controller_preserves_other_generation_record_while_stopping_own_child(self):
        host,child=self.controller()
        host.runtime.mkdir(parents=True)
        record={'state':'STARTING','generation':'other-generation','pid':999999999,'exe':'nonexistent-image'}
        host.record_path().write_text(json.dumps(record))
        result=host.stop()
        self.assertTrue(result['stopped'])
        child.terminate.assert_called_once()
        self.assertEqual(json.loads(host.record_path().read_text()),record)

    def desktop_host(self):
        host=HostProcess(self.root/'desktop')
        host.root.mkdir()
        host.token_file.write_text('synthetic-test-only-token')
        host.status_file.write_text('{}')
        child=mock.Mock()
        child.poll.return_value=None
        child.wait.return_value=0
        host._child=child
        host._token='synthetic-test-only-token'
        return host,child

    def test_desktop_waits_after_force_kill_before_removing_runtime_files(self):
        host,child=self.desktop_host()
        def wait(*args,**kwargs):
            self.assertTrue(host.token_file.exists())
            if child.wait.call_count==1:raise subprocess.TimeoutExpired('synthetic owned host',5)
            return 1
        child.wait.side_effect=wait
        result=host.stop()
        self.assertTrue(result['stopped'])
        self.assertEqual(child.wait.call_count,2)
        child.kill.assert_called_once_with()
        self.assertIsNone(host._child)
        self.assertFalse(host.token_file.exists())

    def test_desktop_preserves_ownership_and_runtime_files_on_terminate_error(self):
        host,child=self.desktop_host()
        child.terminate.side_effect=OSError('synthetic termination denied')
        with self.assertRaises(OSError):host.stop()
        self.assertIs(host._child,child)
        self.assertEqual(host._token,'synthetic-test-only-token')
        self.assertTrue(host.token_file.exists())

    def test_desktop_can_retry_a_failed_termination(self):
        host,child=self.desktop_host()
        child.terminate.side_effect=[OSError('synthetic first attempt denied'),None]
        with self.assertRaises(OSError):host.stop()
        self.assertTrue(host.stop()['stopped'])
        self.assertEqual(child.terminate.call_count,2)
        self.assertIsNone(host._child)

    def test_desktop_keeps_handle_if_wait_after_kill_times_out(self):
        host,child=self.desktop_host()
        child.wait.side_effect=subprocess.TimeoutExpired('synthetic host wait',5)
        with self.assertRaises(subprocess.TimeoutExpired):host.stop()
        self.assertIs(host._child,child)
        self.assertTrue(host.token_file.exists())

    def test_real_python_host_stops_after_its_record_is_removed(self):
        host=HostController(self.root/'real')
        child=None
        try:
            host.start()
            child=host._child
            self.assertIsNotNone(child)
            assert child is not None
            host.record_path().unlink()
            result=host.stop()
            self.assertTrue(result['stopped'])
            self.assertIsNotNone(child.poll())
        finally:
            # An original-bug RED must never abandon the real helper it created.
            host._terminate_child()

    def test_log_tail_does_not_read_the_entire_log(self):
        host=HostController(self.root/'logs')
        stream=io.BytesIO(b'prefix-'+b'x'*4096+b'-tail')
        with mock.patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded log read')), \
             mock.patch.object(Path,'open',return_value=stream):
            self.assertEqual(host._log_bytes(Path('synthetic.log'),4),b'tail')

    def test_nonpositive_log_limit_reads_nothing(self):
        host=HostController(self.root/'logs')
        with mock.patch.object(Path,'read_bytes',side_effect=AssertionError('unbounded log read')), \
             mock.patch.object(Path,'open') as open_file:
            self.assertEqual(host._log_bytes(Path('synthetic.log'),0),b'')
            open_file.assert_not_called()


if __name__=='__main__':unittest.main()
