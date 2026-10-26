"""BridgeHost lifecycle in the local launcher: a real owned child, real HTTP readiness, scoped stop.

No game, save or original install is touched here: the child is the BridgeHost dev entry point bound
to loopback, started inside a private scratch root, and the "plugin" is a stand-in that speaks the
real heartbeat protocol over real HTTP.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request, urlopen

from bridge.app.hostlife import HostController, HostLifecycleError, child_env

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = sys.platform == 'win32'


def process_image_path(pid):
    """Windows-only identity lookup, imported on demand so this module imports anywhere."""
    from tools.lab.process_guard import process_image_path as impl
    return impl(pid)


def _cleanup_tmp(tmp, timeout=5.0):
    """Remove a test's temporary root. On Windows a venv python.exe is a launcher whose real interpreter child exits a
    few milliseconds after the launcher: its host.log can still be open right after stop(), so retry briefly."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            tmp.cleanup()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _await(predicate, timeout=10.0, interval=0.05):
    """Poll a read-only predicate until it is true; returns the truthy value or None."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


@unittest.skipUnless(WINDOWS, 'process identity checks are Windows-only')
class HostLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(_cleanup_tmp, self.tmp)
        self.root = Path(self.tmp.name) / 'Launcher'
        self.host = HostController(self.root)
        self.addCleanup(self.host.close)

    def test_start_is_a_real_owned_host_ready_over_http(self):
        self.assertEqual(self.host.state()['state'], 'STOPPED')
        state = self.host.start()
        self.assertEqual(state['state'], 'RUNNING')
        self.assertTrue(state['owned'])
        self.assertTrue(state['ready'], state['detail'])
        self.assertRegex(state['url'], r'^http://127\.0\.0\.1:\d+$')
        self.assertEqual(process_image_path(state['pid']), Path(sys.executable).resolve())

    def test_readiness_comes_from_the_host_token_and_live_heartbeats(self):
        state = self.host.start()
        token_file = Path(state['token_file'])
        self.assertTrue(token_file.is_file())
        token = token_file.read_text(encoding='utf-8').strip()
        self.assertGreaterEqual(len(token), 16)
        record = (self.root / 'host' / 'host.json').read_text(encoding='utf-8')
        self.assertNotIn(token, record)  # the record points at the token file, never the secret itself
        heartbeat = {'protocol_version': 1, 'message_type': 'heartbeat', 'realm': 'SR1',
                     'request_id': 'launcher-lifecycle-test', 'pid': os.getpid(), 'plugin_version': 'test'}
        request = Request(state['url'] + '/v1/heartbeat', json.dumps(heartbeat).encode(),
                          {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=5) as response:
            ack = json.loads(response.read())
        self.assertEqual((response.status, ack['message_type']), (200, 'heartbeat_ack'))
        live = _await(lambda: self.host.state()['heartbeats'].get('SR1'), timeout=6)
        self.assertIsNotNone(live, 'the host did not surface the live plugin heartbeat')
        self.assertEqual(live['request_id'], 'launcher-lifecycle-test')

    def test_start_is_idempotent_and_stop_leaves_no_process(self):
        first = self.host.start()
        second = self.host.start()
        self.assertEqual(second['pid'], first['pid'])
        stopped = self.host.stop()
        self.assertEqual(stopped['state'], 'STOPPED')
        self.assertTrue(stopped['stopped'])
        self.assertTrue(_await(lambda: process_image_path(first['pid']) is None, timeout=8))
        again = self.host.stop()
        self.assertFalse(again['stopped'])  # idempotent: nothing left to stop
        self.assertEqual(again['state'], 'STOPPED')


class SiblingAndReclaimTests(unittest.TestCase):
    """A second launcher session must never start a second host, and never kill a sibling's host."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(_cleanup_tmp, self.tmp)
        self.root = Path(self.tmp.name) / 'Launcher'
        self.host = HostController(self.root)
        self.addCleanup(self.host.close)
        self.addCleanup(self._cleanup_external)

    def _cleanup_external(self):
        if self.host.state()['state'] == 'EXTERNAL':
            self.host.reclaim()

    def test_sibling_session_is_refused_and_never_killed(self):
        first = self.host.start()
        sibling = HostController(self.root)
        self.addCleanup(sibling.close)
        seen = sibling.state()
        self.assertEqual(seen['state'], 'EXTERNAL')
        self.assertTrue(seen['ready'], seen['detail'])
        self.assertFalse(seen['owned'])
        with self.assertRaises(HostLifecycleError) as refused:
            sibling.start()
        self.assertEqual(refused.exception.code, 'HOST_ALREADY_RUNNING')
        self.assertFalse(sibling.stop()['stopped'])
        self.assertIsNotNone(process_image_path(first['pid']))  # untouched by the refused sibling

    def test_reclaim_stops_only_an_identity_verified_previous_session_host(self):
        # A launcher that dies without stopping its host (real crash simulation in a helper process).
        script = ('import sys\n'
                  'sys.path.insert(0, %r)\n'
                  'from bridge.app.hostlife import HostController\n'
                  'print(HostController(%r).start()["url"], flush=True)\n') % (str(ROOT), str(self.root))
        helper = subprocess.run([sys.executable, '-c', script], capture_output=True, timeout=120, text=True,
                                env={**os.environ, 'PYTHONPATH': str(ROOT)})
        self.assertEqual(helper.returncode, 0, helper.stderr)
        orphan_url = helper.stdout.strip().splitlines()[-1]
        self.assertTrue(orphan_url.startswith('http://127.0.0.1:'), helper.stdout)
        seen = self.host.state()
        self.assertEqual(seen['state'], 'EXTERNAL')
        self.assertEqual(seen['url'], orphan_url)
        with self.assertRaises(HostLifecycleError):
            self.host.start()
        reclaimed = self.host.reclaim()
        self.assertTrue(reclaimed['reclaimed'])
        self.assertEqual(reclaimed['state'], 'STOPPED')
        self.assertTrue(_await(lambda: process_image_path(seen['pid']) is None, timeout=8))
        self.assertEqual(self.host.state()['state'], 'STOPPED')
        self.assertEqual(self.host.start()['state'], 'RUNNING')  # the Lab is usable again

    def test_unhonourable_records_are_replaced_without_killing_anything(self):
        runtime = self.root / 'host'
        runtime.mkdir(parents=True)
        record = {'state': 'RUNNING', 'generation': 'dead-generation', 'pid': 999_999_999,
                  'exe': str(Path(sys.executable).resolve()), 'url': 'http://127.0.0.1:1',
                  'token_file': str(runtime / 'token'), 'status_file': str(runtime / 'status.json'),
                  'started_unix': time.time()}
        (runtime / 'host.json').write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(self.host.state()['state'], 'STALE')
        self.assertFalse(self.host.reclaim()['reclaimed'])

        # A live PID whose image is not the recorded interpreter (PID reuse / foreign process).
        decoy = subprocess.Popen([os.environ.get('ComSpec', 'cmd.exe'), '/k', 'rem'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.PIPE)
        self.addCleanup(decoy.kill)
        (runtime / 'host.json').write_text(json.dumps({**record, 'pid': decoy.pid}), encoding='utf-8')
        self.assertEqual(self.host.state()['state'], 'STALE')  # the PID now belongs to another image
        self.assertFalse(self.host.reclaim()['reclaimed'])  # never terminate a foreign image
        self.assertIsNotNone(process_image_path(decoy.pid))
        self.assertEqual(self.host.start()['state'], 'RUNNING')  # stale record replaced, not honoured
        self.assertIsNotNone(process_image_path(decoy.pid))  # the decoy survived the whole lifecycle


class ChildEnvironmentTests(unittest.TestCase):
    def test_lab_children_get_private_temp_dirs_without_touching_the_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Launcher'
            before = dict(os.environ)
            env = child_env(root)
            scratch = str((root / 'scratch').resolve()).casefold()
            for key in ('TEMP', 'TMP', 'TMPDIR', 'DOTNET_CLI_HOME'):
                self.assertTrue(Path(env[key]).is_absolute(), key)
                self.assertTrue(str(Path(env[key]).resolve()).casefold().startswith(scratch), key)
            probe = root / 'env-probe.py'
            probe.write_text('import json, os, tempfile\n'
                             'print(json.dumps({"temp": tempfile.gettempdir(), "tmpdir": os.environ["TMPDIR"],'
                             ' "dotnet": os.environ["DOTNET_CLI_HOME"]}))\n', encoding='utf-8')
            result = subprocess.run([sys.executable, str(probe)], capture_output=True, timeout=60, env=env,
                                    cwd=str(ROOT), text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            reported = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertTrue(reported['temp'].casefold().startswith(scratch), reported)
            self.assertTrue(reported['tmpdir'].casefold().startswith(scratch), reported)
            self.assertTrue(reported['dotnet'].casefold().startswith(scratch), reported)
            self.assertEqual(dict(os.environ), before)  # per-process environment only, never global
