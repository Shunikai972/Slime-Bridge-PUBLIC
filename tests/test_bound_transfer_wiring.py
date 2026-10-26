"""Wiring coverage for the bound transfer oracle plumbing (no games, no LAB).

Covers the new oracle entry points without launching anything: transfer-authority and implant
validation, the pinned implant writer (against a temp LocalLow root), the linked-profile reader,
and the exact scenario adoption flow (fresh journal -> observe both endpoints -> resolver reports
a resolvable linked binding under the run build).
"""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.counterpart.journal import CounterpartJournal
from bridge.profile.store import ProfileStore
from bridge.transfer.binding import TransferBindingResolver
from tools.lab import oracle, save_guard
from tools.lab.transfer_oracle import read_linked_profile
from transfer_binding_fixtures import PairFixture


class TransferAuthorityValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_incomplete_transfer_authority_is_rejected(self):
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run', transfer_authority={'profiles_path': 'x'})

    def test_non_hex_profile_id_is_rejected(self):
        authority = {'profiles_path': 'x', 'journal_path': 'y', 'store_path': 'z',
                     'profile_id': 'not-a-profile-id', 'trust': {}}
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run', transfer_authority=authority)

    def test_implant_requires_current_user_and_valid_specs(self):
        spec = {'source': str(self.root / 's.sav'), 'realm': 'SR1', 'rel': 'Dir/s.sav',
                'sha256': 'a' * 64}
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run-a', implant=[spec])
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run-b', current_user=True,
                       implant=[{**spec, 'sha256': 'nope'}])
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run-c', current_user=True,
                       implant=[{**spec, 'rel': '../escape.sav'}])
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run-d', current_user=True, implant=[])

    def test_implant_specs_reject_rooted_absolute_and_drive_relative_rels(self):
        # R-001: a rooted/absolute rel resets the join outside the realm save root and would
        # escape both the lexical containment and the restore pass. All forms must be rejected
        # before any snapshot/launch side effect.
        spec = {'source': str(self.root / 's.sav'), 'realm': 'SR1', 'rel': 'Dir/s.sav',
                'sha256': 'a' * 64}
        for rel in ('C:/escape.sav', '/escape.sav', '\\\\srv\\share\\e.sav', 'C:escape.sav'):
            with self.assertRaises(ValueError):
                oracle.run(['SR1'], 1, False, self.root / f'run-{rel.replace(":", "_")}',
                           current_user=True, implant=[{**spec, 'rel': rel}])

    def test_implant_specs_reject_alternate_data_stream_rels(self):
        # R-001 (v2): ':' opens an NTFS alternate data stream that the default-stream hash and
        # restore inventory would neither see nor remove; it must be refused everywhere.
        spec = {'source': str(self.root / 's.sav'), 'realm': 'SR1', 'rel': 'existing.sav:bridge',
                'sha256': 'a' * 64}
        with self.assertRaises(ValueError):
            oracle.run(['SR1'], 1, False, self.root / 'run-ads', current_user=True, implant=[spec])

    def test_implant_writer_rejects_alternate_data_stream_rel(self):
        source = self.root / 'w.sav'
        source.write_bytes(b'x')
        import hashlib as _hashlib
        spec = {'source': str(source), 'realm': 'SR1', 'rel': 'existing.sav:bridge',
                'sha256': _hashlib.sha256(b'x').hexdigest()}
        with mock.patch('tools.lab.oracle.save_guard.local_low', return_value=self.root / 'low'):
            with self.assertRaises(RuntimeError):
                oracle._implant_saves([spec])
            self.assertFalse((self.root / 'low').exists())

    def test_authority_rejects_junctioned_write_paths(self):
        # R-002 (v2): a junction inside the evidence dir must not redirect store/journal files
        # outside it. Preflight refuses reparse points on the write paths.
        import subprocess
        fixture = PairFixture(self.root / 'junction-fixture')
        pid = fixture.linked(suffix='junction')
        evidence = self.root / 'junction-evidence'
        evidence.mkdir()
        outside = self.root / 'junction-outside'
        outside.mkdir()
        link = evidence / 'link'
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                              capture_output=True)
        if made.returncode != 0:
            self.skipTest('junction creation unavailable on this host')
        authority = {'profiles_path': str(fixture.profiles_path),
                     'journal_path': str(link / 'j.sqlite'), 'store_path': str(link / 't.db'),
                     'profile_id': pid,
                     'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'}}
        with mock.patch('tools.lab.oracle._run_games', return_value={'passed': False}), \
                mock.patch('tools.lab.oracle.require_isolated_profile'):
            with self.assertRaises(ValueError):
                oracle.run(['SR1'], 0, False, evidence, transfer_authority=authority)
        self.assertFalse((outside / 't.db').exists())

    def test_authority_rejects_hardlinked_write_paths(self):
        # R-003 (v3): a hard link is not a reparse point but still redirects writes to outside
        # content; an existing multi-link target is refused before anything opens it.
        fixture = PairFixture(self.root / 'hardlink-fixture')
        pid = fixture.linked(suffix='hardlink')
        evidence = self.root / 'hardlink-evidence'
        evidence.mkdir()
        outside = self.root / 'hardlink-outside.db'
        outside.write_bytes(b'outside-content')
        link = evidence / 'transfers.db'
        try:
            os.link(outside, link)
        except OSError:
            self.skipTest('hardlink creation unavailable on this host')
        authority = {'profiles_path': str(fixture.profiles_path),
                     'journal_path': str(evidence / 'j.sqlite'), 'store_path': str(link),
                     'profile_id': pid,
                     'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'}}
        with mock.patch('tools.lab.oracle._run_games', return_value={'passed': False}), \
                mock.patch('tools.lab.oracle.require_isolated_profile'):
            with self.assertRaises(ValueError):
                oracle.run(['SR1'], 0, False, evidence, transfer_authority=authority)
        self.assertEqual(outside.read_bytes(), b'outside-content')

    def test_authority_rejects_hardlinked_sqlite_sidecars(self):
        # R-004 (v4): SQLite derives -wal/-shm/-journal sidecars from a database path; a
        # pre-existing link at a sidecar name must be refused even when the base name is clean.
        fixture = PairFixture(self.root / 'sidecar-fixture')
        pid = fixture.linked(suffix='sidecar')
        evidence = self.root / 'sidecar-evidence'
        evidence.mkdir()
        outside = self.root / 'sidecar-outside'
        outside.write_bytes(b'outside')
        try:
            os.link(outside, evidence / 'transfers.db-wal')
        except OSError:
            self.skipTest('hardlink creation unavailable on this host')
        authority = {'profiles_path': str(fixture.profiles_path),
                     'journal_path': str(evidence / 'j.sqlite'),
                     'store_path': str(evidence / 'transfers.db'), 'profile_id': pid,
                     'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'}}
        with mock.patch('tools.lab.oracle._run_games', return_value={'passed': False}), \
                mock.patch('tools.lab.oracle.require_isolated_profile'):
            with self.assertRaises(ValueError):
                oracle.run(['SR1'], 0, False, evidence, transfer_authority=authority)
        self.assertEqual(outside.read_bytes(), b'outside')

    def test_scenario_rechecks_journal_sidecars_before_open(self):
        # R-005 (v4): the journal opens long after arm-time validation; the scenario re-checks
        # the run-scoped write targets (including sqlite sidecars) immediately before the open.
        fixture = PairFixture(self.root / 'scenario-fixture')
        pid = fixture.linked(suffix='scenario')
        run = self.root / 'scenario-run'
        run.mkdir()
        outside = self.root / 'scenario-outside'
        outside.write_bytes(b'outside')
        try:
            os.link(outside, run / 'counterpart-journal.sqlite-wal')
        except OSError:
            self.skipTest('hardlink creation unavailable on this host')
        with ProfileStore(fixture.profiles_path, read_only=True) as store:
            profile = store.get(pid)
        endpoints = {realm: {'realm': realm, **{key: profile[realm.lower()][key] for key in
                                                ('installation_id', 'native_save_id',
                                                 'native_save_identity')}}
                     for realm in ('SR1', 'SR2')}
        authority = {'profiles_path': str(fixture.profiles_path),
                     'journal_path': str(run / 'counterpart-journal.sqlite'),
                     'store_path': str(run / 'transfers.db'), 'profile_id': pid,
                     'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'},
                     '_runtime': {'profile_id': pid,
                                  'journal_path': run / 'counterpart-journal.sqlite',
                                  'store_path': run / 'transfers.db', 'builds': fixture.builds,
                                  'endpoints': endpoints, 'store': object()}}
        from tools.lab.transfer_oracle import make_scenario
        scenario = make_scenario(pid, endpoints, [{'sender': 'SR1', 'item': 'slime.pink'}], False,
                                 1.0, authority)
        beats = {'SR1': {'status': {'save_id': 'a'}}, 'SR2': {'status': {'save_id': 'b'}}}
        evidence = scenario(host=object(), beats=beats, pids={'SR1': 1, 'SR2': 2}, procs={})
        self.assertIn('journal open refused', evidence.get('error', ''))
        self.assertIn('hardlinked', evidence.get('error', ''))
        self.assertFalse((run / 'counterpart-journal.sqlite').exists())

    def test_authority_rejects_malformed_trust_paths_and_flags(self):
        fixture = PairFixture(self.root / 'auth-fixture')
        pid = fixture.linked(suffix='auth')
        evidence = self.root / 'auth-evidence'
        evidence.mkdir()
        base = {'profiles_path': str(fixture.profiles_path),
                'journal_path': str(evidence / 'j.sqlite'), 'store_path': str(evidence / 't.db'),
                'profile_id': pid,
                'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'}}
        bad_cases = ({'trust': None}, {'trust': {}}, {'trust': {'x': 'SR3'}}, {'trust': 'nope'},
                     {'allow_lab_give': 'yes'}, {'profiles_path': None},
                     {'profiles_path': str(self.root / 'missing.sqlite')},
                     {'journal_path': str(self.root / 'escape-j.sqlite')},
                     {'store_path': str(self.root / 'escape-t.db')}, {'journal_path': 17})
        with mock.patch('tools.lab.oracle._run_games', return_value={'passed': False}), \
                mock.patch('tools.lab.oracle.require_isolated_profile'):
            for changes in bad_cases:
                with self.assertRaises(ValueError):
                    oracle.run(['SR1'], 0, False, evidence, transfer_authority={**base, **changes})

    def test_authority_preflight_caches_endpoints_before_launch(self):
        fixture = PairFixture(self.root / 'preflight-fixture')
        pid = fixture.linked(suffix='pre')
        evidence = self.root / 'preflight-evidence'
        evidence.mkdir()
        authority = {'profiles_path': str(fixture.profiles_path),
                     'journal_path': str(evidence / 'j.sqlite'),
                     'store_path': str(evidence / 't.db'), 'profile_id': pid,
                     'trust': {'slimebridge.lab-live.SR1': 'SR1', 'slimebridge.lab-live.SR2': 'SR2'}}
        lab = self.root / 'Lab' / 'Games'
        exe = lab / 'SR1' / 'SlimeRancher.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'synthetic-test-boundary-not-an-executable')
        with mock.patch('tools.lab.oracle._run_games', return_value={'passed': False}) as run_games, \
                mock.patch('tools.lab.oracle.require_isolated_profile') as isolated, \
                mock.patch.object(oracle, 'LAB', lab), \
                mock.patch.dict(oracle.GAMES, {'SR1': exe}):
            oracle.run(['SR1'], 0, False, evidence, transfer_authority=authority)
        isolated.assert_called_once_with(['SR1'])
        run_games.assert_called_once()
        self.assertEqual(authority['_endpoints']['SR1']['native_save_id'], 'a')
        self.assertEqual(authority['_endpoints']['SR2']['native_save_id'], 'b')
        self.assertIs(run_games.call_args.kwargs['transfer_authority'], authority)


class ImplantWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _spec(self, name='s.sav', rel='Dir/s.sav'):
        source = self.root / name
        payload = b'bridge-owned-test-save'
        source.write_bytes(payload)
        return {'source': str(source), 'realm': 'SR1', 'rel': rel,
                'sha256': hashlib.sha256(payload).hexdigest()}, payload

    def test_implant_writes_verifies_and_refuses_overwrite(self):
        spec, payload = self._spec()
        with mock.patch('tools.lab.oracle.save_guard.local_low', return_value=self.root / 'low'):
            done = oracle._implant_saves([spec])
            target = self.root / 'low' / save_guard.SAVE_DIRS['SR1'] / 'Dir' / 's.sav'
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(done[0]['sha256'], spec['sha256'])
            with self.assertRaises(RuntimeError):
                oracle._implant_saves([spec])

    def test_implant_refuses_wrong_digest_before_writing(self):
        spec, _ = self._spec()
        bad = {**spec, 'sha256': '0' * 64}
        with mock.patch('tools.lab.oracle.save_guard.local_low', return_value=self.root / 'low'):
            with self.assertRaises(RuntimeError):
                oracle._implant_saves([bad])
            self.assertFalse((self.root / 'low').exists())


class LinkedProfileAndAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_linked_profile_requires_a_linked_pair(self):
        fixture = PairFixture(self.root / 'fixture')
        pid = fixture.linked(suffix='wire')
        profile = read_linked_profile(fixture.profiles_path, pid)
        self.assertEqual(profile['state'], 'LINKED')
        for realm in ('SR1', 'SR2'):
            self.assertEqual(profile[realm.lower()]['native_save_id'],
                             'a' if realm == 'SR1' else 'b')

    def test_read_linked_profile_rejects_unlinked_or_missing(self):
        fixture = PairFixture(self.root / 'fixture')
        with ProfileStore(fixture.profiles_path) as store:
            profile = store.find_or_create('SR1', 'solo-install', 'solo-save', 'solo-native')
        with self.assertRaises(SystemExit):
            read_linked_profile(fixture.profiles_path, profile['bridge_profile_id'])
        with self.assertRaises(SystemExit):
            read_linked_profile(fixture.profiles_path, '0' * 32)

    def test_fresh_journal_adoption_resolves_a_linked_binding(self):
        fixture = PairFixture(self.root / 'fixture')
        pid = fixture.linked(suffix='adopt')
        journal_path = self.root / 'run' / 'counterpart-journal.sqlite3'
        journal_path.parent.mkdir(parents=True)
        # The exact scenario flow: a fresh run journal records both live saves under the run build.
        with CounterpartJournal(journal_path) as journal:
            with ProfileStore(fixture.profiles_path, read_only=True) as store:
                profile = store.get(pid)
            for realm in ('SR1', 'SR2'):
                verdict = journal.observe_live_endpoint(
                    pid,
                    {'realm': realm, **{key: profile[realm.lower()][key] for key in
                                        ('installation_id', 'native_save_id', 'native_save_identity')}},
                    fixture.builds[realm])
                self.assertEqual(verdict, 'VERIFIED')
        resolver = TransferBindingResolver(fixture.profiles_path, journal_path,
                                           current_builds=lambda: dict(fixture.builds))
        binding = resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b')
        self.assertEqual(binding.bridge_profile_id, pid)
        self.assertEqual(binding.sender_realm, 'SR1')
        self.assertEqual(binding.receiver_realm, 'SR2')

    def test_adoption_under_a_changed_build_needs_reverification(self):
        fixture = PairFixture(self.root / 'fixture')
        pid = fixture.linked(suffix='drift')
        journal_path = self.root / 'drift' / 'counterpart-journal.sqlite3'
        journal_path.parent.mkdir(parents=True)
        with CounterpartJournal(journal_path) as journal:
            with ProfileStore(fixture.profiles_path, read_only=True) as store:
                profile = store.get(pid)
            for realm in ('SR1', 'SR2'):
                journal.observe_live_endpoint(
                    pid,
                    {'realm': realm, **{key: profile[realm.lower()][key] for key in
                                        ('installation_id', 'native_save_id', 'native_save_identity')}},
                    fixture.builds[realm])
        # A different current build makes the pair REVERIFY_REQUIRED; the resolver must refuse it.
        drifted = {'SR1': 'lab-files-sha256:new', 'SR2': fixture.builds['SR2']}
        resolver = TransferBindingResolver(fixture.profiles_path, journal_path,
                                           current_builds=lambda: dict(drifted))
        from bridge.transfer.binding import TransferBindingError
        with self.assertRaises(TransferBindingError):
            resolver.resolve(pid, 'SR1', 'a', 'SR2', 'b')


if __name__ == '__main__':
    unittest.main()
