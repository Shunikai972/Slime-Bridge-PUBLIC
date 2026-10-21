"""Synthetic orchestration tests, NOT native runtime or proprietary-save proof.

Game/process/Windows guards are explicit test doubles. Native parsing is not exercised.
SQLite linkage, JSON evidence and disk hash/OS-flush readback use real synthetic temporary files.
"""
import base64
import contextlib
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from tools.lab import creation_oracle as creation
from tools.lab import oracle


class CreationRealmTests(unittest.TestCase):
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

    def saved(self):
        return json.loads((self.root / 'creation-result.json').read_text())

    def test_default_direction_remains_sr2_to_sr1(self):
        with self.assertRaisesRegex(RuntimeError, 'initially own exactly'):
            creation.make_scenario(self.root, 1, 1)(None, {}, {}, procs={})
        saved = self.saved()
        self.assertEqual(saved['initially_launched_realms'], ['SR2'])
        self.assertEqual(saved['source_realm'], 'SR2')
        self.assertEqual(saved['target_realm'], 'SR1')
        self.assertFalse(saved['passed'])

    def test_unknown_or_same_realms_are_rejected_before_evidence(self):
        for source, target in [('SR3', 'SR1'), ('SR2', 'other'), ('SR1', 'SR1'), ('SR2', 'SR2')]:
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                creation.make_scenario(self.root, 1, 1, source_realm=source, target_realm=target)
        self.assertFalse((self.root / 'creation-result.json').exists())

    def exercise(self, target='SR2', timeout=False, reconcile_bad=False, replay_change=None,
                 loaded_identity_missing=False, native_subdirectory=False, native_binding=True):
        source = 'SR1' if target == 'SR2' else 'SR2'
        source_proc, target_proc = Mock(pid=101), Mock(pid=202)
        source_proc.poll.return_value = target_proc.poll.return_value = None
        procs = {source: source_proc}
        host = Mock(url='http://127.0.0.1:0')
        host.realm_token.return_value = 'test-only'
        issued = {}
        creates = []
        receipts = {}
        proof_calls = []
        payload = b'synthetic-bytes-not-a-native-save'
        digest = hashlib.sha256(payload).hexdigest()
        save_root = self.root / 'home' / 'AppData' / 'LocalLow' / oracle.save_guard.SAVE_DIRS[target]
        if native_subdirectory:
            save_root = save_root / 'Steam' / 'synthetic-native-account'
        lab = self.root / 'Lab' / 'Games'
        games = {realm: lab / realm / name for realm, name in
                 (('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe'))}
        for exe in games.values():
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_bytes(b'synthetic-never-launched')
        from bridge.profile import native_proof
        real_verify_creation = native_proof.verify_creation

        def enqueue(realm, kind, cid, op_id=None, expected_pid=None):
            self.assertEqual(realm, target)
            self.assertEqual(expected_pid, 202)
            op = op_id or cid + ':create'
            issued[op] = (kind, cid)
            if kind == 'create_save':
                creates.append(op)
            return op

        def wait(op, _timeout):
            kind, cid = issued[op]
            sid = ('sb1_' if target == 'SR1' else 'sb2_') + cid
            result = {'realm': target, 'pid': 202, 'op_id': op, 'kind': kind,
                      'creation_id': cid, 'save_id': sid, 'ok': True, 'verified': True,
                      'save_file': sid + '_0',
                      'sha256': digest, 'before': 1, 'after': 1, 'applied': False,
                      'persisted': target == 'SR2', 'uncertain': False, 'received_unix': len(receipts)}
            if ':ready:' in op:
                # Native provider initialization creates its exact directory before CREATE.
                # This signed diagnostic is synthetic; it does not prove a real native API.
                save_root.mkdir(parents=True, exist_ok=True)
                log = games[target].parent / 'MelonLoader' / 'Latest.log'
                log.parent.mkdir(parents=True, exist_ok=True)
                if native_binding:
                    descriptor = {'version': 1, 'realm': target, 'pid': 202,
                                  'executable': str(games[target].resolve()),
                                  'journal': str((self.root / (target + '-ops.journal')).resolve()),
                                  'director_pointer': '0000000000000011',
                                  'storage_pointer': '0000000000000022',
                                  'provider_type': 'FileStorageProvider', 'initialized': True,
                                  'native_root': str(save_root.resolve()), 'binding_valid': True,
                                  'sampled_utc_ms': time.time_ns() // 1_000_000,
                                  'sample_id': 1}
                    raw = json.dumps(descriptor, separators=(',', ':')).encode('utf-8')
                    signature = hmac.new(b'test-only', raw, hashlib.sha256).hexdigest()
                    log.write_text('[synthetic] SLIMEBRIDGE_STORAGE_BINDING_V1 ' +
                                   base64.b64encode(raw).decode('ascii') + ' ' + signature + '\n')
                else:
                    log.write_text('[synthetic] no native storage binding\n')
                result.update(verified=False, can_create=True, before=0, after=0,
                              save_file='', sha256='', persisted=False)
            elif kind == 'create_save':
                if timeout and len(creates) == 1:
                    return None
                if len(creates) == 1:
                    result.update(applied=True, persisted=True)
                    save_root.mkdir(parents=True, exist_ok=True)
                    (save_root / (sid + '_0.sav')).write_bytes(payload)
                elif target == 'SR1':
                    result = {**receipts[op], 'received_unix': 999}
                else:
                    result.update(reason='ALREADY_EXISTS', applied=False)
            elif reconcile_bad:
                result.update(ok=False, verified=False, uncertain=True)
            elif timeout:
                # The lost ACK may conceal a completed creation, never a second dispatch.
                save_root.mkdir(parents=True, exist_ok=True)
                (save_root / (sid + '_0.sav')).write_bytes(payload)
            if ':reconcile:after' in op and replay_change:
                result.update(replay_change)
            receipts[op] = dict(result)
            return result

        def proof(result, cid, op, pid, save_root, *, realm, expected_kind='create_save'):
            proof_calls.append((realm, save_root, expected_kind, result))
            return real_verify_creation(result, cid, op, pid, save_root, realm=realm,
                                        expected_kind=expected_kind)

        def beats():
            cid = next(iter(issued.values()))[1]
            return {source: {'pid': 101, 'status': {'save_id': 'source', 'player_alive': True,
                                                   'pink_slime_count': 0, 'pink_plort_count': 0}},
                    target: {'pid': 202, 'status': {'save_id': ('sb1_' if target == 'SR1' else 'sb2_') + cid,
                                                   'player_alive': True, 'pink_slime_count': 0,
                                                   'pink_plort_count': 0}}}

        def query(_host, realm, kind, item, count, op, sid, _timeout):
            return {'realm': realm, 'pid': 101 if realm == source else 202, 'kind': kind, 'item': item,
                    'op_id': op, 'save_id': '' if loaded_identity_missing else sid, 'ok': True, 'before': 0}

        host.enqueue_creation_command.side_effect = enqueue
        host.wait_result.side_effect = wait
        host.heartbeats.side_effect = beats
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict('tools.lab.oracle.GAMES', games))
            stack.enter_context(patch('tools.lab.oracle.LAB', lab))
            stack.enter_context(patch('tools.lab.creation_oracle.Path.home', return_value=self.root / 'home'))
            stack.enter_context(patch('tools.lab.creation_oracle.assert_no_other_game_processes'))
            stack.enter_context(patch('tools.lab.creation_oracle.subprocess.Popen', return_value=target_proc))
            stack.enter_context(patch('tools.lab.creation_oracle.validate_lab_destination', side_effect=lambda path, _lab: path))
            stack.enter_context(patch('tools.lab.creation_oracle.process_image_path',
                                      side_effect=lambda pid: oracle.GAMES[source if pid == 101 else target].resolve()))
            stack.enter_context(patch('tools.lab.oracle.same_image', return_value=True))
            stack.enter_context(patch('tools.lab.creation_oracle.run_command', side_effect=query))
            # Transparent spy invokes the real reader; only save bytes are synthetic.
            stack.enter_context(patch.object(native_proof, 'verify_creation', side_effect=proof))
            scenario = creation.make_scenario(self.root, 1, 1, source_realm=source, target_realm=target)
            try:
                result = scenario(host, {source: {'pid': 101, 'status': {'save_id': 'source'}}},
                                  {source: 101}, procs=procs)
            except (ValueError, RuntimeError, FileNotFoundError):
                result = self.saved()
        return result, host, creates, proof_calls, procs

    def test_native_account_storage_root_is_bound_before_create(self):
        result, _host, creates, proofs, _procs = self.exercise('SR2', native_subdirectory=True)
        self.assertTrue(result['passed'], result.get('error'))
        self.assertEqual(len(creates), 1)
        expected = self.root / 'home' / 'AppData' / 'LocalLow' / oracle.save_guard.SAVE_DIRS['SR2'] / 'Steam' / 'synthetic-native-account'
        self.assertTrue(proofs)
        self.assertTrue(all(root == expected for _realm, root, _kind, _receipt in proofs))
        self.assertEqual(Path(result['native_storage_binding']['native_root']), expected)
        self.assertEqual(result['native_storage_binding']['pid'], 202)
        self.assertEqual(Path(result['independent_disk_readback']['save_file']).parent, expected)

    def test_observed_signed_sample_regression_fails_before_linking(self):
        # Replay actual signed synthetic bytes after a newer sample was observed.
        # Matching root/owner is insufficient if the live sample cursor regresses.
        real_reader = creation.read_binding_log
        observed = 0
        original_bytes = None

        def replay_old_sample(log_path, **context):
            nonlocal observed, original_bytes
            observed += 1
            if observed == 1:
                original_bytes = log_path.read_bytes()
            elif observed == 2:
                assert original_bytes is not None
                encoded = original_bytes.split(b'SLIMEBRIDGE_STORAGE_BINDING_V1 ', 1)[1].split()[0]
                descriptor = json.loads(base64.b64decode(encoded))
                descriptor['sample_id'] = 2
                raw = json.dumps(descriptor, separators=(',', ':')).encode('utf-8')
                signature = hmac.new(b'test-only', raw, hashlib.sha256).hexdigest()
                log_path.write_text('[synthetic] SLIMEBRIDGE_STORAGE_BINDING_V1 ' +
                                    base64.b64encode(raw).decode('ascii') + ' ' + signature + '\n')
            elif observed == 3:
                log_path.write_bytes(original_bytes)
            return real_reader(log_path, **context)

        with patch.object(creation, 'read_binding_log', side_effect=replay_old_sample):
            result, _host, creates, _proofs, _procs = self.exercise('SR2', native_subdirectory=True)
        self.assertFalse(result['passed'], 'a regressed signed sample must not produce LINKED')
        self.assertEqual(len(creates), 1)
        self.assertNotIn('profile_after_link', result)
        self.assertIn('binding', result['error'].lower())
        from bridge.profile.store import ProfileStore
        with ProfileStore(self.root / 'profiles.sqlite') as store:
            profile = store.get(result['bridge_profile_id'])
        self.assertEqual(profile['state'], 'CREATING')
        self.assertEqual(profile['creation_issued'], 1)

    def test_invalid_append_during_preclaim_read_denies_before_issuing_create(self):
        from test_native_storage_binding import append_after_binding_read, signed_storage_frame
        log = self.root / 'Lab/Games/SR2/MelonLoader/Latest.log'

        def invalidate(data):
            encoded = data.split(b'SLIMEBRIDGE_STORAGE_BINDING_V1 ', 1)[1].split()[0]
            record = json.loads(base64.b64decode(encoded))
            return signed_storage_frame({**record, 'sample_id': 2, 'binding_valid': False})

        with append_after_binding_read(log, invalidate) as observations:
            result, _host, creates, _proofs, _procs = self.exercise('SR2', native_subdirectory=True)
        self.assertEqual(len(observations), 1)
        self.assertFalse(result['passed'])
        self.assertEqual(creates, [], 'known invalid appended sample must refuse before claim/enqueue')
        self.assertNotIn('creation_claimed', result)
        from bridge.profile.store import ProfileStore
        with ProfileStore(self.root / 'profiles.sqlite') as store:
            profile = store.get(result['bridge_profile_id'])
        self.assertEqual(profile['state'], 'CREATING')
        self.assertEqual(profile['creation_issued'], 0)

    def test_owner_change_during_final_native_queries_never_reports_success(self):
        from test_native_storage_binding import signed_storage_frame
        # Earlier native linkage was verified, so a late sequence failure must not rewrite
        # the historical proof/profile. Only refuse fresh overall sequence success.
        for boundary in (':target-ready', ':source-still-ready'):
            extra = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
            self.addCleanup(extra.cleanup)
            self.root = Path(extra.name)
            with self.subTest(boundary=boundary):
                calls = []
                real_query = creation._live_query

                def changed_owner(*args, **kwargs):
                    result = real_query(*args, **kwargs)
                    if args[4].endswith(boundary):
                        log = self.root / 'Lab/Games/SR2/MelonLoader/Latest.log'
                        encoded = log.read_bytes().split(b'SLIMEBRIDGE_STORAGE_BINDING_V1 ', 1)[1].split()[0]
                        record = json.loads(base64.b64decode(encoded))
                        with log.open('ab') as writer:
                            writer.write(signed_storage_frame({**record, 'sample_id': 2,
                                                               'storage_pointer': '0000000000000033'}))
                        calls.append(args[4])
                    return result

                with patch.object(creation, '_live_query', side_effect=changed_owner):
                    result, _host, creates, _proofs, _procs = self.exercise('SR2', native_subdirectory=True)
                self.assertEqual(len(calls), 1)
                self.assertFalse(result['passed'], 'a terminal observed owner replacement is not sequence success')
                self.assertEqual(len(creates), 1)
                self.assertIn('storage binding', result['error'].lower())
                from bridge.profile.store import ProfileStore
                with ProfileStore(self.root / 'profiles.sqlite') as store:
                    profile = store.get(result['bridge_profile_id'])
                self.assertEqual(profile['state'], 'LINKED')
                self.assertEqual(profile['creation_issued'], 1)

    def test_sr2_missing_native_storage_binding_denies_create_before_claim(self):
        result, _host, creates, _proofs, _procs = self.exercise('SR2', native_binding=False)
        self.assertFalse(result['passed'])
        self.assertEqual(creates, [], 'no claimed/enqueued CREATE without exact native root proof')
        self.assertNotIn('creation_claimed', result)
        self.assertIn('storage binding', result['error'].lower())
        from bridge.profile.store import ProfileStore
        with ProfileStore(self.root / 'profiles.sqlite') as store:
            profile = store.get(result['bridge_profile_id'])
        self.assertEqual(profile['creation_issued'], 0)

    def test_orchestration_receipts_satisfy_actual_host_contract(self):
        from bridge.host.server import _matches_creation_result
        for target in ('SR1', 'SR2'):
            with self.subTest(target=target):
                result, _host, _creates, _proofs, _procs = self.exercise(target)
                self.assertTrue(result['passed'])
                for key in ('menu_ready', 'native_creation_result',
                            'native_reconcile_before_replay', 'native_reconcile_after_replay'):
                    receipt = result[key]
                    command = {'kind': receipt['kind'], 'creation_id': result['creation_id']}
                    with self.subTest(receipt=key):
                        self.assertTrue(_matches_creation_result(receipt, command, 202, realm=target))
                (self.root / 'profiles.sqlite').unlink()

    def test_both_directions_link_reopen_and_wait_for_cleanup(self):
        for target in ('SR1', 'SR2'):
            with self.subTest(target=target):
                result, _host, creates, proofs, procs = self.exercise(target)
                self.assertTrue(result['passed'])
                self.assertEqual(len(creates), 1)  # one CREATE; durable decision/reconciliation only
                self.assertEqual(result['replay_decision']['decision'], 'USE_EXISTING_LINK')
                self.assertEqual(set(procs), {'SR1', 'SR2'})
                endpoint = result['profile_after_db_reopen'][target.lower()]
                self.assertEqual(endpoint['native_save_id'], result['independent_disk_readback']['save_id'])
                self.assertTrue(all(realm == target and root.parts[-2:] == oracle.save_guard.SAVE_DIRS[target].parts
                                    for realm, root, _kind, _receipt in proofs))
                self.assertFalse(self.saved()['passed'])
                creation.finalize_creation_evidence(self.root, {'passed': False, 'scenario': result})
                self.assertFalse(self.saved()['passed'])
                creation.finalize_creation_evidence(self.root, {'passed': True, 'cleanup_completed': False,
                                                               'scenario': result})
                self.assertFalse(self.saved()['passed'])
                creation.finalize_creation_evidence(self.root, {'passed': True, 'cleanup_completed': True,
                                                               'scenario': result})
                self.assertTrue(self.saved()['passed'])
                # Independent cases need independent creation SQLite DBs.
                (self.root / 'profiles.sqlite').unlink()

    def test_success_never_reenqueues_an_issued_create_in_either_direction(self):
        for target in ('SR1', 'SR2'):
            with self.subTest(target=target):
                result, host, creates, _proofs, _procs = self.exercise(target)
                self.assertTrue(result['passed'])
                self.assertEqual(len(creates), 1, 'strict one CREATE enqueue; replay must use decision + reconciliation')
                self.assertEqual(result['replay_decision']['decision'], 'USE_EXISTING_LINK')
                self.assertNotIn('plugin_journal_replay', result)
                self.assertEqual(result['independent_replay_readback'], result['independent_disk_readback'])
                (self.root / 'profiles.sqlite').unlink()

    def test_timeout_reconciles_same_intent_and_never_replays_create(self):
        result, _host, creates, proofs, _procs = self.exercise(timeout=True)
        self.assertTrue(result['passed'])
        self.assertEqual(len(creates), 1)
        self.assertIsNone(result['native_creation_result'])
        self.assertTrue(result['creation_uncertain'])
        self.assertIn('native_timeout_reconciliation', result)
        self.assertEqual(proofs[0][2], 'reconcile_save')
        _host.cancel_command.assert_called_once_with('SR2', creates[0])

    def test_uncertain_reconciliation_keeps_partial_evidence_without_link(self):
        result, _host, creates, _proofs, _procs = self.exercise(timeout=True, reconcile_bad=True)
        self.assertFalse(result['passed'])
        self.assertEqual(len(creates), 1)
        self.assertIn('creation_id', result)
        self.assertIn('native_timeout_reconciliation', result)
        self.assertNotIn('profile_after_link', result)

    def test_existing_link_decision_and_readonly_replay_keep_identity_file_hash_and_counts(self):
        result, _host, creates, _proofs, _procs = self.exercise()
        replay = result['native_reconcile_after_replay']
        self.assertFalse(replay['applied'])
        self.assertEqual(result['replay_decision']['decision'], 'USE_EXISTING_LINK')
        self.assertNotIn('plugin_journal_replay', result)
        self.assertEqual(len(creates), 1)
        self.assertTrue(result['passed'])

    def test_changed_readonly_replay_proof_or_group_count_is_refused(self):
        for change in ({'save_id': 'different'}, {'save_file': 'different'}, {'sha256': 'b' * 64},
                       {'before': 2, 'after': 2}, {'applied': True}, {'verified': False}):
            with self.subTest(change=change):
                result, _host, creates, _proofs, _procs = self.exercise(replay_change=change)
                self.assertFalse(result['passed'])
                self.assertEqual(len(creates), 1)
                self.assertIn('native_reconcile_after_replay', result)
                self.assertNotIn('plugin_journal_replay', result)
                (self.root / 'profiles.sqlite').unlink()

    def test_replayed_decision_other_than_existing_link_fails_closed_without_create(self):
        with patch('tools.lab.creation_oracle.decide_counterpart', return_value={'decision': 'CREATE_COUNTERPART'}):
            result, _host, creates, _proofs, _procs = self.exercise()
        self.assertFalse(result['passed'])
        self.assertEqual(len(creates), 1)
        self.assertIn('would not use the existing', result['error'])
        self.assertNotIn('native_reconcile_after_replay', result)

    def test_missing_loaded_native_identity_fails_closed_before_dispatch(self):
        result, _host, creates, _proofs, procs = self.exercise(loaded_identity_missing=True)
        self.assertFalse(result['passed'])
        self.assertEqual(creates, [])
        self.assertEqual(set(procs), {'SR1'})

    def test_prior_run_with_lab_mutation_or_unfinished_cleanup_blocks_new_intent(self):
        prior = self.root / 'creation-prior'
        prior.mkdir()
        finalized = {'sources_untouched': True, 'saves_match_backup_after_restore': True,
                     'cleanup_completed': True, 'lab_fingerprints_unchanged': True,
                     'registry_changes': {realm: {'removed_or_changed': [], 'added_or_changed': []}
                                          for realm in ('SR1', 'SR2')}}
        for failed_guard in ({'lab_fingerprints_unchanged': False}, {'cleanup_completed': False},
                             {'cleanup_error': 'synthetic cleanup failure'}):
            with self.subTest(failed_guard=failed_guard):
                (prior / 'oracle-result.json').write_text(json.dumps({**finalized, **failed_guard}))
                with self.assertRaisesRegex(RuntimeError, 'existing-intent recovery'):
                    creation.require_finalized_prior_creations(self.root)

    def test_target_child_registered_even_when_image_validation_fails(self):
        child = Mock(pid=202)
        with patch('tools.lab.creation_oracle.assert_no_other_game_processes'), \
             patch('tools.lab.creation_oracle.validate_lab_destination', side_effect=lambda path, _lab: path), \
             patch('tools.lab.creation_oracle.subprocess.Popen', return_value=child), \
             patch('tools.lab.creation_oracle.process_image_path', return_value=None):
            owned = {}
            with self.assertRaisesRegex(RuntimeError, 'verified SR2'):
                creation._launch_target(Mock(), self.root, owned, 'SR2')
            self.assertIs(owned['SR2'], child)


class CreationGuardAndCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab = self.root / 'Lab' / 'Games'
        self.games = {}
        for realm, name in [('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe')]:
            exe = self.lab / realm / name
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'synthetic-executable-never-launched')
            source = self.root / 'original' / realm
            source.mkdir(parents=True)
            (source / name).write_bytes(exe.read_bytes())
            import hashlib
            digest = hashlib.sha256(exe.read_bytes()).hexdigest()
            (exe.parent / 'slimebridge-lab.json').write_text(json.dumps(
                {'source': str(source), 'sha256': {name: digest}}))
            self.games[realm] = exe

    def test_sr2_opt_in_requires_both_guards_before_any_launch(self):
        with patch('tools.lab.oracle.subprocess.Popen') as popen, patch.dict('tools.lab.oracle.GAMES', self.games), \
             patch('tools.lab.oracle.LAB', self.lab):
            for realms in (None, ['SR1'], ['SR2']):
                with self.subTest(guards=realms), self.assertRaises(ValueError):
                    oracle.run(['SR1'], 0, False, self.root / 'e', creation_realms=('SR2',),
                               guard_realms=realms)
            popen.assert_not_called()

    def test_sr2_opt_in_requires_profile_gate_and_fingerprints(self):
        for current_user in (False, True):
            with self.subTest(current_user=current_user), contextlib.ExitStack() as stack:
                stack.enter_context(patch.dict('tools.lab.oracle.GAMES', self.games))
                stack.enter_context(patch('tools.lab.oracle.LAB', self.lab))
                isolated = stack.enter_context(patch('tools.lab.oracle.require_isolated_profile'))
                stack.enter_context(patch('tools.lab.oracle.assert_no_other_game_processes'))
                snapshot = stack.enter_context(patch('tools.lab.oracle.save_guard.snapshot', return_value=self.root / 'snap'))
                finalized = stack.enter_context(patch('tools.lab.oracle._finalize_current_user'))
                launched = stack.enter_context(patch('tools.lab.oracle._run_games', return_value={'passed': False}))
                oracle.run(['SR1'], 0, False, self.root / 'e', current_user=current_user,
                           creation_realms=('SR1', 'SR2'), guard_realms=['SR1', 'SR2'])
                if current_user:
                    snapshot.assert_called_once_with(['SR1', 'SR2'], 'oracle-pre')
                    self.assertEqual(finalized.call_args.args[2], ['SR1', 'SR2'])
                    isolated.assert_not_called()
                else:
                    isolated.assert_called_once_with(['SR1', 'SR2'])
                self.assertEqual(launched.call_args.kwargs['creation_realms'], ('SR1', 'SR2'))
                result = launched.call_args.args[6]
                self.assertEqual(set(result['lab_fingerprints_before']), {'SR1', 'SR2'})
                self.assertEqual(set(result['sources_before']), {'SR1', 'SR2'})

    def test_creation_realms_reaches_bridge_host_default_and_opt_in(self):
        host = MagicMock(url='http://127.0.0.1:0')
        host.__enter__.return_value = host
        child = Mock(pid=101)
        child.poll.return_value = None
        with patch('tools.lab.oracle.BridgeHost', return_value=host) as ctor, \
             patch('tools.lab.oracle.subprocess.Popen', return_value=child), \
             patch.dict('tools.lab.oracle.GAMES', self.games), patch('tools.lab.oracle.LAB', self.lab), \
             patch('tools.lab.oracle.process_image_path', return_value=self.games['SR1']), \
             patch('tools.lab.oracle._stop_owned', return_value={}):
            for enabled in (('SR1',), ('SR1', 'SR2')):
                oracle._run_games(['SR1'], 0, False, self.root, False, False,
                                  {'realms': {}, 'passed': False}, creation_realms=enabled)
                ctor.assert_called_with(creation_realms=enabled)

    def test_lab_mutation_fails_overall_after_cleanup(self):
        def run_games(*args, **kwargs):
            self.games['SR2'].write_bytes(b'changed-synthetic-lab')
            result = args[6]
            result['passed'] = True
            return result
        with patch.dict('tools.lab.oracle.GAMES', self.games), patch('tools.lab.oracle.LAB', self.lab), \
             patch('tools.lab.oracle.require_isolated_profile'), \
             patch('tools.lab.oracle._run_games', side_effect=run_games):
            result = oracle.run(['SR1'], 0, False, self.root / 'e',
                                creation_realms=('SR2',), guard_realms=['SR1', 'SR2'])
        self.assertFalse(result['passed'])
        self.assertFalse(result['lab_fingerprints_unchanged'])

    def test_process_identity_mismatch_is_never_controlled(self):
        child = Mock(pid=101)
        child.poll.return_value = None
        wrong = self.root / 'outside.exe'
        with patch('tools.lab.oracle.process_image_path', return_value=wrong), \
             patch.dict('tools.lab.oracle.GAMES', self.games):
            errors = oracle._stop_owned({'SR1': child})
        self.assertIn('SR1', errors)
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_cleanup_queries_pid_image_before_controlling_owned_handle(self):
        calls = []
        child = Mock(pid=101)
        child.poll.return_value = None
        child.terminate.side_effect = lambda: calls.append('terminate')
        with patch('tools.lab.oracle.process_image_path', side_effect=lambda pid: calls.append(('image', pid)) or self.games['SR1']), \
             patch.dict('tools.lab.oracle.GAMES', self.games):
            self.assertEqual(oracle._stop_owned({'SR1': child}), {})
        self.assertEqual(calls[0], ('image', 101))
        self.assertEqual(calls[1], 'terminate')

    def test_snapshot_race_is_inside_restore_finally(self):
        with patch.dict('tools.lab.oracle.GAMES', self.games), patch('tools.lab.oracle.LAB', self.lab), \
             patch('tools.lab.oracle.assert_no_other_game_processes', side_effect=[None, RuntimeError('race')]), \
             patch('tools.lab.oracle.save_guard.snapshot', return_value=self.root / 'snap'), \
             patch('tools.lab.oracle._finalize_current_user') as finalizer, \
             patch('tools.lab.oracle._run_games') as launch:
            with self.assertRaisesRegex(RuntimeError, 'race'):
                oracle.run(['SR1'], 0, False, self.root / 'e', current_user=True,
                           creation_realms=('SR2',), guard_realms=['SR1', 'SR2'])
            finalizer.assert_called_once()
            launch.assert_not_called()

    def test_generic_evidence_cannot_pass_before_cleanup_or_after_cleanup_error(self):
        result = {'passed': True, 'cleanup_completed': False}
        oracle._write_evidence(result, self.root)
        saved = json.loads(next(self.root.glob('oracle-*.json')).read_text())
        self.assertFalse(saved['passed'])
        self.assertEqual(saved['validation_scope'], 'awaiting_cleanup')
        with patch.dict('tools.lab.oracle.GAMES', self.games), patch('tools.lab.oracle.LAB', self.lab), \
             patch('tools.lab.oracle.require_isolated_profile'), \
             patch('tools.lab.oracle._run_games', side_effect=lambda *args, **kw: args[6].update(passed=True) or args[6]), \
             patch('tools.lab.oracle.lab_fingerprints', side_effect=[{}, RuntimeError('readback error')]):
            with self.assertRaisesRegex(RuntimeError, 'readback error'):
                oracle.run(['SR1'], 0, False, self.root / 'failure',
                           creation_realms=('SR2',), guard_realms=['SR1', 'SR2'])
        saved = json.loads(next((self.root / 'failure').glob('oracle-*.json')).read_text())
        self.assertFalse(saved['passed'])
        self.assertIn('cleanup_error', saved)

    def invoke_cli(self, argv):
        with patch('sys.argv', ['creation_oracle', *argv]), \
             patch('tools.lab.creation_oracle.oracle.REPO_ROOT', self.root), \
             patch('tools.lab.creation_oracle.require_finalized_prior_creations'), \
             patch('tools.lab.creation_oracle.oracle.run', return_value={'passed': False}) as run:
            with self.assertRaises(SystemExit) as exit:
                creation.main()
        return run, exit.exception.code

    def test_cli_default_and_reverse_source_save_selection(self):
        run, code = self.invoke_cli(['--current-user-authorized', '--sr2-save-id', 'source2'])
        self.assertEqual(code, 1)
        self.assertEqual(run.call_args.args[0], ['SR2'])
        self.assertEqual(run.call_args.kwargs['sr2_save_id'], 'source2')
        self.assertEqual(run.call_args.kwargs['creation_realms'], ('SR1',))
        run, code = self.invoke_cli(['--current-user-authorized', '--source-realm', 'SR1',
                                    '--target-realm', 'SR2', '--sr1-save-id', 'source1'])
        self.assertEqual(code, 1)
        self.assertEqual(run.call_args.args[0], ['SR1'])
        self.assertEqual(run.call_args.kwargs['sr1_save_id'], 'source1')
        self.assertIsNone(run.call_args.kwargs['sr2_save_id'])
        self.assertEqual(run.call_args.kwargs['creation_realms'], ('SR2',))
        self.assertEqual(run.call_args.kwargs['guard_realms'], ['SR1', 'SR2'])
        self.assertEqual(set(run.call_args.kwargs['extra_env']), {'SR1'})

    def test_cli_invalid_pair_or_target_save_id_fails_before_run(self):
        for argv in (['--source-realm', 'SR1', '--target-realm', 'SR1'],
                     ['--source-realm', 'SR3'], ['--sr1-save-id', 'target'],
                     ['--sr2-save-id', '../bad']):
            with self.subTest(argv=argv):
                run, code = self.invoke_cli(['--current-user-authorized', *argv])
                self.assertEqual(code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
