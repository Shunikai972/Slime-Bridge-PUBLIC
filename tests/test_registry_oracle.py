"""Protected registry runner: real readers over synthetic captures, game boundary mocked."""

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from test_native_registry_collectors import SR1_ROWS, write_capture

from tools.lab import registry_oracle


def write_run_capture(directory, realm, rows, run_id):
    def bind_run(snapshot, receipt):
        snapshot['collector']['run_id'] = receipt['run_id'] = run_id
    return write_capture(directory, realm, rows, tamper=bind_run)


@contextmanager
def synthetic_runner(realm, after_scenario=None):
    """Real evidence/reader/scenario; autospec replaces only the protected game-run boundary."""
    with tempfile.TemporaryDirectory(prefix='registry-synthetic-run-') as tmp:
        root = Path(tmp)
        game = root / 'Lab/Games' / realm
        exe = game / ('SlimeRancher.exe' if realm == 'SR1' else 'SlimeRancher2.exe')
        anchor = (game / 'SlimeRancher_Data/Managed/Assembly-CSharp.dll' if realm == 'SR1'
                  else game / 'GameAssembly.dll')
        built = (root / 'mods/sr1-bridge/bin/Release/net472/SlimeBridge.SR1.dll' if realm == 'SR1'
                 else root / 'mods/sr2-bridge/bin/Release/net6.0/SlimeBridge.SR2.dll')
        deployed = (game / 'SRML/Mods/SlimeBridge/SlimeBridge.SR1.dll' if realm == 'SR1'
                    else game / 'Mods/SlimeBridge.SR2.dll')
        for path, content in ((exe, b'never-launched fixture'), (anchor, b'fixture-build'),
                              (built, b'synthetic plugin'), (deployed, b'synthetic plugin')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        state = {'root': root, 'exe': exe, 'anchor': anchor, 'built': built, 'deployed': deployed}

        def protected_run(realms, timeout, keep_running, evidence, **kwargs):
            run_id = kwargs['extra_env'][realm]['SLIMEBRIDGE_LAB_REGISTRY_RUN']
            write_run_capture(evidence, realm, SR1_ROWS, run_id)
            child = Mock(pid=101)
            child.poll.return_value = None
            host = Mock()
            host.heartbeats.return_value = {realm: {'pid': 101}}
            with patch('tools.lab.registry_oracle.oracle.same_image', return_value=True):
                scenario = kwargs['scenario'](host, host.heartbeats(), {realm: child.pid}, procs={realm: child})
            state.update(scenario=scenario, evidence=evidence, run_id=run_id, host=host)
            if after_scenario:
                after_scenario(state)
            return {'passed': True, 'scenario': scenario, 'cleanup_completed': True,
                    'sources_untouched': True, 'lab_fingerprints_unchanged': True,
                    'saves_match_backup_after_restore': True}

        with patch('tools.lab.registry_oracle.oracle.REPO_ROOT', root), \
             patch('tools.lab.registry_oracle.oracle.LAB', root / 'Lab/Games'), \
             patch.dict('tools.lab.registry_oracle.oracle.GAMES', {realm: exe}), \
             patch('tools.lab.registry_oracle.oracle.run', autospec=True, side_effect=protected_run) as run:
            state['run'] = run
            yield state


@contextmanager
def exclusive_publication_handle(path):
    """Synthetic Win32 writer keeps an already-visible file unshareable until release."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x40000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def release():
        nonlocal handle
        if handle is not None:
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = None

    try:
        yield release
    finally:
        release()


class RegistryOracleTests(unittest.TestCase):
    def test_summary_uses_actual_independent_capture_reader(self):
        self.assertIsNotNone(registry_oracle, 'protected registry capture runner is missing')
        with tempfile.TemporaryDirectory(prefix='registry-runner-') as tmp:
            directory = Path(tmp)
            build = write_capture(directory, 'SR1', SR1_ROWS)
            before = {p.name: p.read_bytes() for p in directory.iterdir()}
            result = registry_oracle.capture_summary(directory, 'SR1', build, 'run1')
            self.assertTrue(result['passed'], result)
            self.assertEqual(result['completeness'], 'COMPLETE')
            self.assertEqual(result['counts'], {'slimes_array': 3, 'by_identifiable': 3})
            self.assertEqual(result['definition_count'], 3)
            self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)

    def test_runner_requires_authorization_before_any_launch_or_output(self):
        with patch('tools.lab.registry_oracle.oracle.run') as launch:
            with self.assertRaisesRegex(ValueError, 'authorization'):
                registry_oracle.run_capture('SR1', current_user_authorized=False)
            launch.assert_not_called()

    def test_runner_reuses_protected_oracle_without_autoload_or_creation(self):
        with tempfile.TemporaryDirectory(prefix='registry-protected-run-') as tmp:
            root = Path(tmp)
            realm = 'SR1'
            game = root / 'Lab/Games/SR1'
            game.mkdir(parents=True)
            exe = game / 'SlimeRancher.exe'
            exe.write_bytes(b'fixture exe')
            anchor = game / 'SlimeRancher_Data/Managed/Assembly-CSharp.dll'
            anchor.parent.mkdir(parents=True)
            anchor.write_bytes(b'fixture-build')
            built = root / 'mods/sr1-bridge/bin/Release/net472/SlimeBridge.SR1.dll'
            deployed = game / 'SRML/Mods/SlimeBridge/SlimeBridge.SR1.dll'
            for path in (built, deployed):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'synthetic reviewed plugin')

            result_overrides = {}

            def protected_run(realms, timeout, keep_running, evidence, **kwargs):
                self.assertEqual(realms, ['SR1'])
                self.assertFalse(keep_running)
                self.assertFalse(kwargs['require_status'])
                self.assertTrue(kwargs['current_user'])
                self.assertNotIn('protected_creation', kwargs)  # derived by the real oracle from scenario + guards
                self.assertEqual(kwargs['guard_realms'], ['SR1', 'SR2'])
                self.assertEqual(kwargs['creation_realms'], ())
                env = kwargs['extra_env'][realm]
                self.assertEqual(set(env), {'SLIMEBRIDGE_LAB_REGISTRY_OUT',
                                           'SLIMEBRIDGE_LAB_REGISTRY_RUN', 'SLIMEBRIDGE_LAB_EXE'})
                self.assertEqual(Path(env['SLIMEBRIDGE_LAB_EXE']), exe)
                self.assertEqual(Path(env['SLIMEBRIDGE_LAB_REGISTRY_OUT']), evidence)
                write_capture(evidence, realm, SR1_ROWS)
                for name, field in [('registry-snapshot.json', 'collector'), ('traversal-receipt.json', None)]:
                    path = evidence / name
                    doc = json.loads(path.read_text())
                    target = doc[field] if field else doc
                    target['run_id'] = env['SLIMEBRIDGE_LAB_REGISTRY_RUN']
                    path.write_text(json.dumps(doc))
                child = Mock(pid=101)
                child.poll.return_value = None
                host = Mock()
                host.heartbeats.return_value = {realm: {'pid': 101}}
                with patch('tools.lab.registry_oracle.oracle.heartbeat_matches_launched', return_value=True):
                    scenario = kwargs['scenario'](host, host.heartbeats(), {realm: 101}, procs={realm: child})
                result = {'passed': True, 'scenario': scenario, 'cleanup_completed': True,
                          'sources_untouched': True, 'lab_fingerprints_unchanged': True,
                          'saves_match_backup_after_restore': True,
                          'save_changes': {'SR1': {'added': [], 'removed': [], 'modified': []}}}
                result.update(result_overrides)
                return result

            with patch('tools.lab.registry_oracle.oracle.REPO_ROOT', root), \
                 patch('tools.lab.registry_oracle.oracle.LAB', root / 'Lab/Games'), \
                 patch.dict('tools.lab.registry_oracle.oracle.GAMES', {'SR1': exe}), \
                 patch('tools.lab.registry_oracle.oracle.run', autospec=True, side_effect=protected_run):
                result = registry_oracle.run_capture(realm, current_user_authorized=True)
                for field in ('cleanup_completed', 'sources_untouched', 'lab_fingerprints_unchanged',
                              'saves_match_backup_after_restore'):
                    with self.subTest(missing_proof=field):
                        result_overrides[field] = None
                        refused = registry_oracle.run_capture(realm, current_user_authorized=True)
                        self.assertFalse(refused['passed'])
                        result_overrides.clear()
                result_overrides['scenario'] = {'passed': False, 'owned_process_verified': True,
                                                'capture': {'passed': False}}
                self.assertFalse(registry_oracle.run_capture(realm, current_user_authorized=True)['passed'],
                                 'scenario failure cannot be overridden by outer success')
                result_overrides.clear()
            self.assertTrue(result['passed'], result)
            self.assertEqual(result['validation_scope'], 'protected_live_registry_capture')
            self.assertEqual(result['scenario']['capture']['counts']['slimes_array'], 3)
            self.assertEqual(built.read_bytes(), deployed.read_bytes())
            self.assertTrue((Path(result['run_directory']) / 'registry-runtime-receipt.json').is_file())

    def test_cleanup_replacement_cannot_be_promoted_to_live_capture(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm):
                def swap(state):
                    evidence, run_id = state['evidence'], state['run_id']
                    self.assertTrue(state['scenario']['passed'], state['scenario'])
                    names = (registry_oracle.SNAPSHOT_FILE, registry_oracle.RECEIPT_FILE)
                    state['live_bytes'] = {name: (evidence / name).read_bytes() for name in names}
                    state['live_summary'] = dict(state['scenario']['capture'])
                    # Simulate cleanup replacing BOTH files with another valid capture of this same run/build.
                    write_run_capture(evidence, realm, SR1_ROWS[:2], run_id)
                    state['replacement_bytes'] = {name: (evidence / name).read_bytes() for name in names}
                    state['replacement_summary'] = registry_oracle.capture_summary(
                        evidence, realm, state['anchor'], run_id)
                    self.assertTrue(state['replacement_summary']['passed'])
                    for field in ('realm', 'run_id', 'active_build', 'active_sha256', 'completeness'):
                        self.assertEqual(state['live_summary'][field], state['replacement_summary'][field])
                    self.assertNotEqual(state['live_summary']['artifact_sha256'],
                                        state['replacement_summary']['artifact_sha256'])

                with synthetic_runner(realm, swap) as state:
                    result = registry_oracle.run_capture(realm, current_user_authorized=True)
                    published = json.loads((state['evidence'] / 'registry-runtime-receipt.json').read_text())
                    self.assertFalse(result['passed'], 'P1_EVIDENCE_SWAP: valid replacement is not the live capture')
                    self.assertFalse(published['passed'])
                    self.assertNotEqual(published['validation_scope'], 'protected_live_registry_capture')
                    self.assertFalse(published['capture_bound_to_live_scenario'])
                    self.assertEqual(published['scenario']['capture'], state['live_summary'])
                    self.assertEqual(published['post_cleanup_capture'], state['replacement_summary'])
                    self.assertEqual(published['post_cleanup_capture']['validation_scope'], 'recorded_consistency_only')
                    for label in ('live', 'replacement'):
                        hashes = {name: hashlib.sha256(data).hexdigest()
                                  for name, data in state[label + '_bytes'].items()}
                        self.assertEqual(state[label + '_summary']['artifact_sha256'], hashes)
                    for name, data in state['replacement_bytes'].items():
                        self.assertEqual((state['evidence'] / name).read_bytes(), data)
                    state['host'].enqueue_creation_command.assert_not_called()

    def test_unreadable_cleanup_capture_publishes_failure(self):
        for changed_file, replacement in ((registry_oracle.SNAPSHOT_FILE, None),
                                          (registry_oracle.RECEIPT_FILE, '{')):
            with self.subTest(file=changed_file):
                def invalidate(state):
                    self.assertTrue(state['scenario']['passed'])
                    path = state['evidence'] / changed_file
                    if replacement is None:
                        path.unlink()
                    else:
                        path.write_text(replacement, encoding='utf-8')

                with synthetic_runner('SR1', invalidate) as state:
                    result = registry_oracle.run_capture('SR1', current_user_authorized=True)
                    published = json.loads((state['evidence'] / 'registry-runtime-receipt.json').read_text())
                    self.assertFalse(result['passed'])
                    self.assertFalse(published['passed'])
                    self.assertFalse(published['capture_bound_to_live_scenario'])
                    self.assertIn('capture_validation_error', published)
                    self.assertNotEqual(published['validation_scope'], 'protected_live_registry_capture')
                    self.assertEqual(published['scenario']['capture'], state['scenario']['capture'])

    def test_both_realms_full_run_preserves_live_capture_binding(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), synthetic_runner(realm) as state:
                result = registry_oracle.run_capture(realm, current_user_authorized=True)
                published = json.loads((state['evidence'] / 'registry-runtime-receipt.json').read_text())
                self.assertTrue(result['passed'], result)
                self.assertTrue(published['passed'])
                self.assertTrue(published['capture_bound_to_live_scenario'])
                self.assertEqual(published['scenario']['capture'], published['post_cleanup_capture'])
                self.assertEqual(published['post_cleanup_capture']['active_build'], str(state['anchor']))
                self.assertEqual(published['validation_scope'], 'protected_live_registry_capture')
                self.assertFalse(published['native_creation_enabled'])
                args, kwargs = state['run'].call_args
                self.assertEqual(args[:3], ([realm], 600, False))
                self.assertFalse(kwargs['require_status'])
                self.assertTrue(kwargs['current_user'])
                self.assertEqual(kwargs['guard_realms'], ['SR1', 'SR2'])
                self.assertEqual(kwargs['creation_realms'], ())
                self.assertNotIn('protected_creation', kwargs)
                self.assertEqual(set(kwargs['extra_env'][realm]), {'SLIMEBRIDGE_LAB_REGISTRY_OUT',
                                 'SLIMEBRIDGE_LAB_REGISTRY_RUN', 'SLIMEBRIDGE_LAB_EXE'})
                state['host'].enqueue_creation_command.assert_not_called()

    def test_prelaunch_plugin_mismatch_refuses_before_output(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), synthetic_runner(realm) as state:
                state['deployed'].write_bytes(b'different deployed plugin')
                with self.assertRaisesRegex(ValueError, 'deployed plugin does not match'):
                    registry_oracle.run_capture(realm, current_user_authorized=True)
                state['run'].assert_not_called()
                self.assertFalse((state['root'] / 'Lab/RuntimeArtifacts').exists())

    def test_post_run_plugin_and_native_build_changes_cannot_pass(self):
        for realm in ('SR1', 'SR2'):
            for target in ('built', 'deployed', 'anchor'):
                with self.subTest(realm=realm, changed=target):
                    def change(state):
                        self.assertTrue(state['scenario']['passed'])
                        state[target].write_bytes(b'changed after live capture')

                    with synthetic_runner(realm, change) as state:
                        result = registry_oracle.run_capture(realm, current_user_authorized=True)
                        published = json.loads((state['evidence'] / 'registry-runtime-receipt.json').read_text())
                        self.assertFalse(result['passed'])
                        self.assertFalse(published['passed'])
                        self.assertFalse(published['plugin_and_build_unchanged'])
                        self.assertFalse(published['capture_bound_to_live_scenario'])
                        self.assertNotEqual(published['validation_scope'], 'protected_live_registry_capture')
                        self.assertEqual(published['scenario']['capture'], state['scenario']['capture'])

    def test_each_raw_artifact_hash_is_bound_even_when_counts_are_unchanged(self):
        for name in (registry_oracle.SNAPSHOT_FILE, registry_oracle.RECEIPT_FILE):
            with self.subTest(file=name):
                def replace_bytes(state):
                    path = state['evidence'] / name
                    path.write_bytes(path.read_bytes() + b' \n')

                with synthetic_runner('SR1', replace_bytes) as state:
                    result = registry_oracle.run_capture('SR1', current_user_authorized=True)
                    published = json.loads((state['evidence'] / 'registry-runtime-receipt.json').read_text())
                    self.assertTrue(published['post_cleanup_capture']['passed'])  # still valid recorded evidence
                    self.assertFalse(published['passed'])
                    self.assertFalse(published['capture_bound_to_live_scenario'])
                    before = published['scenario']['capture']
                    after = published['post_cleanup_capture']
                    self.assertEqual({k: v for k, v in before.items() if k != 'artifact_sha256'},
                                     {k: v for k, v in after.items() if k != 'artifact_sha256'})
                    self.assertNotEqual(before['artifact_sha256'][name], after['artifact_sha256'][name])

    def test_actual_oracle_derives_protection_without_unsupported_keyword(self):
        """Exercise real oracle.run; only game/process and account-profile boundaries are doubles."""
        actual_run = registry_oracle.oracle.run
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), synthetic_runner(realm) as state:
                lab = state['root'] / 'Lab/Games'
                games = {}
                for guarded, filename in (('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe')):
                    exe = lab / guarded / filename
                    exe.parent.mkdir(parents=True, exist_ok=True)
                    if not exe.exists():
                        exe.write_bytes(b'never-launched guard fixture')
                    manifest = {'source': str(state['root'] / 'read-only-original' / guarded),
                                'sha256': {filename: hashlib.sha256(exe.read_bytes()).hexdigest()}}
                    (exe.parent / 'slimebridge-lab.json').write_text(json.dumps(manifest), encoding='utf-8')
                    games[guarded] = exe

                def run_games(realms, timeout_s, keep_running, evidence_dir, require_status, current_user,
                              result, diag_saves, sr2_save_id, sr1_save_id, scenario, extra_env, *,
                              creation_realms, transfer_authority=None):
                    self.assertEqual(realms, [realm])
                    self.assertFalse(keep_running)
                    self.assertFalse(require_status)
                    self.assertEqual(creation_realms, ())
                    self.assertIsNone(transfer_authority)
                    run_id = extra_env[realm]['SLIMEBRIDGE_LAB_REGISTRY_RUN']
                    write_run_capture(evidence_dir, realm, SR1_ROWS, run_id)
                    child = Mock(pid=101)
                    child.poll.return_value = None
                    host = Mock()
                    host.heartbeats.return_value = {realm: {'pid': child.pid}}
                    with patch('tools.lab.registry_oracle.oracle.same_image', return_value=True):
                        captured = scenario(host, host.heartbeats(), {realm: child.pid}, procs={realm: child})
                    result.update(passed=captured['passed'], scenario=captured)
                    return result

                def finish(result, snap, realms, evidence):
                    self.assertEqual(realms, ['SR1', 'SR2'])
                    result.update(sources_untouched=True, saves_match_backup_after_restore=True)

                state['run'].side_effect = actual_run
                with patch.dict('tools.lab.registry_oracle.oracle.GAMES', games), \
                     patch('tools.lab.registry_oracle.oracle.assert_no_other_game_processes'), \
                     patch('tools.lab.registry_oracle.oracle.source_fingerprints', return_value={}), \
                     patch('tools.lab.registry_oracle.oracle.lab_fingerprints',
                           wraps=registry_oracle.oracle.lab_fingerprints) as fingerprints, \
                     patch('tools.lab.registry_oracle.oracle.save_guard.snapshot',
                           return_value=state['root'] / 'synthetic-backup') as backup, \
                     patch('tools.lab.registry_oracle.oracle._finalize_current_user', side_effect=finish) as restore, \
                     patch('tools.lab.registry_oracle.oracle._run_games', autospec=True, side_effect=run_games):
                    result = registry_oracle.run_capture(realm, current_user_authorized=True)
                self.assertTrue(result['passed'], result)
                self.assertTrue(result['cleanup_completed'])
                self.assertTrue(result['lab_fingerprints_unchanged'])
                self.assertEqual(result['creation_realms'], [])
                self.assertEqual(result['guarded_realms'], ['SR1', 'SR2'])
                self.assertEqual(fingerprints.call_count, 2)
                for invocation in fingerprints.call_args_list:
                    self.assertEqual(invocation.args, (['SR1', 'SR2'],))
                backup.assert_called_once_with(['SR1', 'SR2'], 'oracle-pre')
                restore.assert_called_once()

    def test_live_scenario_binds_child_pid_and_image(self):
        self.assertIsNotNone(registry_oracle)
        with tempfile.TemporaryDirectory(prefix='registry-scenario-') as tmp:
            directory = Path(tmp)
            build = write_capture(directory, 'SR1', SR1_ROWS)
            child = Mock(pid=101)
            child.poll.return_value = None
            host = Mock()
            host.heartbeats.return_value = {'SR1': {'pid': 101}}
            exe = directory / 'SlimeRancher.exe'
            scenario = registry_oracle.make_scenario('SR1', directory, build, 'run1', exe, 0.1)
            with patch('tools.lab.registry_oracle.oracle.heartbeat_matches_launched', return_value=True) as verified:
                result = scenario(host, host.heartbeats(), {'SR1': 101}, procs={'SR1': child})
            self.assertTrue(result['passed'], result)
            self.assertTrue(result['owned_process_verified'])
            self.assertEqual(result['pid'], 101)
            self.assertEqual(result['capture']['validation_scope'], 'recorded_consistency_only')
            verified.assert_called_with({'pid': 101}, child, exe)
            host.enqueue_creation_command.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'real exclusive-publication handle requires Windows')
    def test_visible_exclusive_publication_waits_for_writer_release(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), tempfile.TemporaryDirectory(prefix='registry-publishing-') as tmp:
                directory = Path(tmp)
                build = write_capture(directory, realm, SR1_ROWS)
                before = {name: (directory / name).read_bytes() for name in
                          (registry_oracle.SNAPSHOT_FILE, registry_oracle.RECEIPT_FILE)}
                child = Mock(pid=101)
                child.poll.return_value = None
                host = Mock()
                host.heartbeats.return_value = {realm: {'pid': child.pid}}
                scenario = registry_oracle.make_scenario(realm, directory, build, 'run1', directory, 1)
                with exclusive_publication_handle(directory / registry_oracle.RECEIPT_FILE) as release:
                    self.assertTrue((directory / registry_oracle.RECEIPT_FILE).is_file())
                    with self.assertRaises(PermissionError) as blocked:
                        registry_oracle.capture_summary(directory, realm, build, 'run1')
                    self.assertEqual(blocked.exception.errno, 13)  # Python's CRT open maps sharing refusal to EACCES.
                    with patch('tools.lab.registry_oracle.oracle.same_image', return_value=True), \
                         patch('tools.lab.registry_oracle.time.sleep', side_effect=lambda seconds: release()) as slept, \
                         patch('tools.lab.registry_oracle.capture_summary',
                               wraps=registry_oracle.capture_summary) as reader:
                        try:
                            result = scenario(host, {}, {realm: child.pid}, procs={realm: child})
                        except PermissionError as error:
                            self.fail(f'visible publication is not ready until the owned writer closes: {error}')
                    self.assertTrue(result['passed'], result)
                    self.assertTrue(result['owned_process_verified'])
                    self.assertEqual(reader.call_count, 2)
                    slept.assert_called_once()
                self.assertEqual({name: (directory / name).read_bytes() for name in before}, before)
                host.enqueue_creation_command.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'real exclusive-publication handle requires Windows')
    def test_exclusive_publication_deadline_refuses_without_live_capture(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), tempfile.TemporaryDirectory(prefix='registry-lock-timeout-') as tmp:
                directory = Path(tmp)
                build = write_capture(directory, realm, SR1_ROWS)
                child = Mock(pid=101)
                child.poll.return_value = None
                host = Mock()
                host.heartbeats.return_value = {realm: {'pid': child.pid}}
                scenario = registry_oracle.make_scenario(realm, directory, build, 'run1', directory, 1)
                with exclusive_publication_handle(directory / registry_oracle.RECEIPT_FILE), \
                     patch('tools.lab.registry_oracle.oracle.same_image', return_value=True), \
                     patch('tools.lab.registry_oracle.time.monotonic', side_effect=[0, 1]), \
                     patch('tools.lab.registry_oracle.time.sleep') as slept:
                    result = scenario(host, {}, {realm: child.pid}, procs={realm: child})
                self.assertFalse(result['passed'])
                self.assertIn('deadline', result['error'])
                self.assertNotIn('capture', result)
                slept.assert_not_called()
                host.enqueue_creation_command.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'real exclusive-publication handle requires Windows')
    def test_child_exit_during_publication_wait_refuses_before_retry_read(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), tempfile.TemporaryDirectory(prefix='registry-lock-child-') as tmp:
                directory = Path(tmp)
                build = write_capture(directory, realm, SR1_ROWS)
                child = Mock(pid=101)
                child.poll.return_value = None
                host = Mock()
                host.heartbeats.return_value = {realm: {'pid': child.pid}}
                scenario = registry_oracle.make_scenario(realm, directory, build, 'run1', directory, 1)
                with exclusive_publication_handle(directory / registry_oracle.RECEIPT_FILE) as release:
                    def lose_child(seconds):
                        release()
                        child.poll.return_value = 0

                    with patch('tools.lab.registry_oracle.oracle.same_image', return_value=True), \
                         patch('tools.lab.registry_oracle.time.sleep', side_effect=lose_child), \
                         patch('tools.lab.registry_oracle.capture_summary',
                               wraps=registry_oracle.capture_summary) as reader:
                        result = scenario(host, {}, {realm: child.pid}, procs={realm: child})
                self.assertFalse(result['passed'])
                self.assertIn('owned child', result['error'])
                self.assertNotIn('capture', result)
                self.assertEqual(reader.call_count, 1)
                host.enqueue_creation_command.assert_not_called()

    def test_malformed_visible_capture_is_not_a_publication_retry(self):
        for realm in ('SR1', 'SR2'):
            with self.subTest(realm=realm), tempfile.TemporaryDirectory(prefix='registry-malformed-ready-') as tmp:
                directory = Path(tmp)
                build = write_capture(directory, realm, SR1_ROWS)
                (directory / registry_oracle.SNAPSHOT_FILE).write_text('{', encoding='utf-8')
                child = Mock(pid=101)
                child.poll.return_value = None
                host = Mock()
                host.heartbeats.return_value = {realm: {'pid': child.pid}}
                scenario = registry_oracle.make_scenario(realm, directory, build, 'run1', directory, 1)
                with patch('tools.lab.registry_oracle.oracle.same_image', return_value=True), \
                     patch('tools.lab.registry_oracle.time.sleep') as slept, self.assertRaises(ValueError):
                    scenario(host, {}, {realm: child.pid}, procs={realm: child})
                slept.assert_not_called()
                host.enqueue_creation_command.assert_not_called()

    def test_mismatched_child_is_refused_before_capture_read(self):
        child = Mock(pid=101)
        child.poll.return_value = None
        host = Mock()
        host.heartbeats.return_value = {'SR1': {'pid': 202}}
        scenario = registry_oracle.make_scenario('SR1', Path('unused'), Path('unused'), 'run1', Path('unused'), 0)
        with patch('tools.lab.registry_oracle.oracle.heartbeat_matches_launched', return_value=False), \
             patch('tools.lab.registry_oracle.capture_summary') as read:
            result = scenario(host, {}, {'SR1': 101}, procs={'SR1': child})
        self.assertFalse(result['passed'])
        read.assert_not_called()

    def test_missing_files_reach_bounded_timeout(self):
        with tempfile.TemporaryDirectory(prefix='registry-no-capture-') as tmp:
            child = Mock(pid=101)
            host = Mock()
            host.heartbeats.return_value = {'SR1': {'pid': 101}}
            scenario = registry_oracle.make_scenario('SR1', Path(tmp), Path(tmp) / 'unused', 'run1', Path(tmp), 0)
            with patch('tools.lab.registry_oracle.oracle.heartbeat_matches_launched', return_value=True), \
                 patch('tools.lab.registry_oracle.capture_summary') as read:
                result = scenario(host, {}, {'SR1': 101}, procs={'SR1': child})
            self.assertFalse(result['passed'])
            self.assertIn('deadline', result['error'])
            read.assert_not_called()

    def test_child_image_is_rechecked_after_reader(self):
        with tempfile.TemporaryDirectory(prefix='registry-child-changed-') as tmp:
            directory = Path(tmp)
            build = write_capture(directory, 'SR1', SR1_ROWS)
            child = Mock(pid=101)
            host = Mock()
            host.heartbeats.return_value = {'SR1': {'pid': 101}}
            scenario = registry_oracle.make_scenario('SR1', directory, build, 'run1', directory, 0)
            with patch('tools.lab.registry_oracle.oracle.heartbeat_matches_launched', side_effect=[True, False]):
                result = scenario(host, {}, {'SR1': 101}, procs={'SR1': child})
            self.assertFalse(result['passed'])
            self.assertIn('during capture validation', result['error'])

    def test_no_owned_child_never_reads(self):
        scenario = registry_oracle.make_scenario('SR1', Path('unused'), Path('unused'), 'run1', Path('unused'), 0)
        with patch('tools.lab.registry_oracle.capture_summary') as read:
            result = scenario(Mock(), {}, {'SR1': 101}, procs={})
        self.assertFalse(result['passed'])
        read.assert_not_called()

    def test_invalid_inputs_fail_before_launch(self):
        with patch('tools.lab.registry_oracle.oracle.run') as launch:
            for timeout in (float('nan'), float('inf'), -1, 0, 1801):
                with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                    registry_oracle.run_capture('SR1', current_user_authorized=True, timeout_s=timeout)
            with self.assertRaises(ValueError):
                registry_oracle.run_capture('SR3', current_user_authorized=True)
            launch.assert_not_called()

    def test_partial_capture_is_not_runtime_complete(self):
        self.assertIsNotNone(registry_oracle)
        with tempfile.TemporaryDirectory(prefix='registry-partial-') as tmp:
            directory = Path(tmp)
            build = write_capture(directory, 'SR1', SR1_ROWS, expected=4)
            result = registry_oracle.capture_summary(directory, 'SR1', build, 'run1')
            self.assertFalse(result['passed'])
            self.assertEqual(result['completeness'], 'PARTIAL')

    def test_wrong_run_is_refused(self):
        self.assertIsNotNone(registry_oracle)
        with tempfile.TemporaryDirectory(prefix='registry-wrong-run-') as tmp:
            directory = Path(tmp)
            build = write_capture(directory, 'SR1', SR1_ROWS)
            with self.assertRaisesRegex(ValueError, 'expected runtime invocation'):
                registry_oracle.capture_summary(directory, 'SR1', build, 'wrong-run')


if __name__ == '__main__':
    unittest.main()
