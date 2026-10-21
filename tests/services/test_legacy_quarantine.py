"""Exact legacy creation quarantine + retired creation IDs (synthetic copies of pinned shapes only)."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.counterpart import legacy_quarantine as LQ
from bridge.counterpart.hosttransport import BridgeHostTransport
from bridge.counterpart.journal import CounterpartJournal, JournalError
from bridge.counterpart.links import ProfileStoreLinks
from bridge.counterpart.realms import wire_adapter
from bridge.counterpart.service import CounterpartService
from bridge.host.server import BridgeHost
from bridge.profile.store import ProfileStore
from test_commands import TOKEN

ROOT = Path(__file__).resolve().parents[2]
RETIRED = sorted(LQ.RETIRED_CREATION_IDS)


def synthetic_pins(folders):
    """Pins for synthetic folders, built exactly like the reviewed document."""
    entries = []
    for name, files, ids in folders:
        entries.append({'folder_name': name, 'creation_ids': sorted(ids), 'audit_verdict': 'UNRESOLVED_LEGACY_INTENT',
                        'creation_ids_by_source': {'journals': sorted(ids)},
                        'files': [{'path': p, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
                                  for p, b in sorted(files.items())]})
    ids = sorted({i for _, _, folder_ids in folders for i in folder_ids})
    digest = LQ._digest({'folders': entries, 'retired_creation_ids': ids})
    return {'schema': 'slimebridge-legacy-creation-quarantine', 'version': 1, 'status': LQ.STATUS,
            'folders': entries, 'retired_creation_ids': ids, 'pins_sha256': digest}, digest


class PinDocumentTests(unittest.TestCase):
    def test_reviewed_pins_cover_exactly_13_folders_66_files_and_9_ids(self):
        doc = LQ.load_pins()
        self.assertEqual(len(doc['folders']), 13)
        self.assertEqual(sum(len(f['files']) for f in doc['folders']), 66)
        self.assertEqual(frozenset(doc['retired_creation_ids']), LQ.RETIRED_CREATION_IDS)
        self.assertEqual(len(LQ.RETIRED_CREATION_IDS), 9)
        self.assertEqual(doc['status'], 'UNRESOLVED_LEGACY_INTENT_QUARANTINED')
        self.assertEqual(doc['audit_report_sha256'],
                         '42b911761714278eef833ea6d8d27ff0cd7094593ef5cd9a36eec17bf25b6e19')

    def test_foreign_historical_id_relationships_are_preserved(self):
        doc = {f['folder_name']: f for f in LQ.load_pins()['folders']}
        self.assertEqual(doc['creation-20261003-231126-364258']['creation_ids_by_source'],
                         {'journals': ['07afb0b92e1b4461be1560b7da6b995d'], 'readonly_sqlite': ['9f1fea2d47ef4594a540a5eb83805802'],
                          'receipts': []})
        self.assertIn('07afb0b92e1b4461be1560b7da6b995d', doc['creation-20261003-232257-605213']['creation_ids'])

    def test_native_shared_command_boundary_retires_the_same_exact_ids(self):
        import re
        source = (ROOT / 'mods' / 'shared' / 'BridgeCommands.cs').read_text(encoding='utf-8')
        block = re.search(r'RetiredCreationIds = new HashSet<string>\(StringComparer\.Ordinal\)\s*\{(.*?)\};', source, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(frozenset(re.findall(r'"([0-9a-f]{32})"', block.group(1))), LQ.RETIRED_CREATION_IDS)

    def test_tampered_pin_document_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = json.loads(LQ.PINS_PATH.read_text(encoding='utf-8'))
            doc['folders'][0]['files'][0]['sha256'] = '0' * 64
            path = Path(tmp) / 'pins.json'
            path.write_text(json.dumps(doc), encoding='utf-8')
            with self.assertRaisesRegex(LQ.QuarantineError, 'reviewed digest'):
                LQ.load_pins(path)

    def test_matching_is_exact_not_prefix_or_case_folded(self):
        cid = RETIRED[0]
        self.assertTrue(LQ.is_retired(cid))
        for other in (cid[:-1], cid.upper(), cid + '0', ' ' + cid, cid[:-1] + ('0' if cid[-1] != '0' else '1'), None, 7):
            self.assertFalse(LQ.is_retired(other), other)


@unittest.skipUnless(os.name == 'nt', 'NTFS junction regression')
class FolderVerificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / 'RuntimeArtifacts'
        self.root.mkdir()
        self.files = {'oracle-result.json': b'{"legacy": true}', 'profiles.sqlite': b'sqlite-bytes'}
        self.layout = [('creation-a', self.files, ['a' * 32]), ('creation-b', {'out.txt': b'x'}, [])]
        for name, files, _ in self.layout:
            (self.root / name).mkdir()
            for rel, data in files.items():
                (self.root / name / rel).write_bytes(data)
        pins, _ = synthetic_pins(self.layout)
        self.q = LQ.LegacyQuarantine(pins)

    def refused(self, pattern='', *, require_all=True):
        with self.assertRaisesRegex(LQ.QuarantineError, pattern):
            self.q.verify_root(self.root, require_all=require_all)

    def test_exact_pins_accept_and_report_quarantine_not_success(self):
        verdicts = self.q.verify_root(self.root, require_all=True)
        self.assertEqual([v['status'] for v in verdicts], [LQ.STATUS] * 2)
        self.assertNotIn('passed', json.dumps(verdicts))

    def test_tampered_byte_rejected(self):
        (self.root / 'creation-a' / 'profiles.sqlite').write_bytes(b'sqlite-byteZ')
        self.refused('bytes changed')

    def test_wrong_size_rejected(self):
        (self.root / 'creation-a' / 'profiles.sqlite').write_bytes(b'sqlite-bytes+')
        self.refused('bytes changed')

    def test_missing_file_rejected(self):
        (self.root / 'creation-a' / 'profiles.sqlite').unlink()
        self.refused('inventory drift')

    def test_extra_file_rejected(self):
        (self.root / 'creation-a' / 'oracle-result.json.bak').write_bytes(b'')
        self.refused('inventory drift')

    def test_nested_directory_rejected(self):
        (self.root / 'creation-a' / 'nested').mkdir()
        self.refused('non-file entry')

    def test_renamed_or_moved_folder_rejected(self):
        (self.root / 'creation-a').rename(self.root / 'creation-a-moved')
        self.refused('missing')
        self.refused('missing', require_all=False)
        with self.assertRaisesRegex(LQ.QuarantineError, 'unknown legacy'):
            self.q.verify_folder(self.root / 'creation-a-moved')

    def test_all_pinned_folders_missing_fails_closed(self):
        archive = Path(self._tmp.name) / 'archive'
        archive.mkdir()
        for name, _, _ in self.layout:
            (self.root / name).rename(archive / name)
        self.refused('missing')
        self.refused('missing', require_all=False)

    def test_reparse_folder_rejected(self):
        outside = Path(self._tmp.name) / 'outside'
        shutil.move(str(self.root / 'creation-a'), str(outside))
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(self.root / 'creation-a'), str(outside)],
                       check=True, capture_output=True)
        try:
            self.refused('not a plain directory')
        finally:
            os.rmdir(self.root / 'creation-a')

    def test_unknown_folder_rejected(self):
        with self.assertRaisesRegex(LQ.QuarantineError, 'unknown legacy'):
            self.q.verify_folder(self.root / 'creation-new')

    def test_verification_never_writes_historical_bytes(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        self.q.verify_root(self.root, require_all=True)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()})


class CreationOraclePreflightTests(unittest.TestCase):
    """The real oracle preflight: pinned folders are quarantined, anything else stays strict."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        legacy = self.root / 'creation-legacy'
        legacy.mkdir()
        self.legacy_bytes = json.dumps({'sources_untouched': True}).encode()
        (legacy / 'oracle-result.json').write_bytes(self.legacy_bytes)
        pins, _ = synthetic_pins([('creation-legacy', {'oracle-result.json': self.legacy_bytes}, ['a' * 32])])
        self.quarantine = LQ.LegacyQuarantine(pins)

    def preflight(self):
        from tools.lab import creation_oracle as creation
        with patch.object(creation, 'legacy_quarantine', return_value=self.quarantine):
            creation.require_finalized_prior_creations(self.root)

    def test_pinned_legacy_receipt_is_quarantined_and_unchanged(self):
        self.preflight()
        self.assertEqual((self.root / 'creation-legacy' / 'oracle-result.json').read_bytes(), self.legacy_bytes)

    def test_tampered_pinned_folder_fails_closed(self):
        (self.root / 'creation-legacy' / 'oracle-result.json').write_bytes(self.legacy_bytes + b' ')
        with self.assertRaisesRegex(Exception, 'bytes changed'):
            self.preflight()

    def test_unpinned_legacy_receipt_still_requires_recovery(self):
        other = self.root / 'creation-unknown'
        other.mkdir()
        (other / 'oracle-result.json').write_bytes(self.legacy_bytes)
        with self.assertRaisesRegex(RuntimeError, 'existing-intent recovery'):
            self.preflight()

    def test_all_historical_folders_moved_blocks_preflight(self):
        archive = self.root.parent / (self.root.name + '-archived')
        archive.mkdir()
        self.addCleanup(shutil.rmtree, archive)
        (self.root / 'creation-legacy').rename(archive / 'creation-legacy')
        with self.assertRaisesRegex(LQ.QuarantineError, 'missing'):
            self.preflight()

    def test_missing_artifact_root_blocks_preflight(self):
        shutil.rmtree(self.root)
        with self.assertRaisesRegex(LQ.QuarantineError, 'missing'):
            self.preflight()

    def test_modern_finalized_receipt_still_allowed(self):
        modern = self.root / 'creation-modern'
        modern.mkdir()
        (modern / 'oracle-result.json').write_text(json.dumps({
            'sources_untouched': True, 'saves_match_backup_after_restore': True,
            'registry_changes': {realm: {'removed_or_changed': [], 'added_or_changed': []} for realm in ('SR1', 'SR2')},
            'cleanup_completed': True, 'lab_fingerprints_unchanged': True}))
        self.preflight()

    def test_real_repository_pins_match_disk_when_lab_evidence_present(self):
        from tools.lab import creation_oracle as creation
        root = ROOT / 'Lab' / 'RuntimeArtifacts'
        if not any(os.path.lexists(root / name) for name in LQ.LegacyQuarantine().by_name):
            self.skipTest('local LAB evidence absent (e.g. clean clone)')
        self.assertEqual(len(creation.legacy_quarantine().verify_root(root, require_all=True)), 13)


class RetiredEndpointAdoptionTests(unittest.TestCase):
    """Known old profile provenance must survive absent or NULL journal metadata."""

    def linked_fixture(self, cid, target, origin=None, *, target_unknown=False, with_intent=False):
        from contextlib import contextmanager
        from services.test_counterpart_core import Harness

        @contextmanager
        def fixture():
            with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
                h = Harness(tmp)
                try:
                    source = 'SR2' if target == 'SR1' else 'SR1'
                    pid = h.profile(source)
                    endpoint = {'realm': target, 'installation_id': target.lower() + '-install',
                                'native_save_id': 'synthetic-' + cid,
                                'native_save_identity': 'synthetic-identity-' + cid}
                    h.store._db.execute('UPDATE profiles SET state=?, creation_id=?, creation_target_realm=?, '
                                         'creation_issued=1 WHERE bridge_profile_id=?',
                                         ('LINKED', cid, None if target_unknown else target, pid))
                    h.store._db.execute('INSERT INTO endpoints VALUES (?,?,?,?,?)',
                                         (pid, target, endpoint['installation_id'], endpoint['native_save_id'],
                                          endpoint['native_save_identity']))
                    h.store._db.commit()
                    if not target_unknown:
                        self.assertEqual(h.svc.observe_live(pid, source, build_fingerprint='build'), 'VERIFIED')
                    if origin is not None:
                        h.journal._db.execute('INSERT INTO endpoint_meta VALUES (?,?,?,?,?,?,?,?)',
                                               (pid, target, origin, None, json.dumps(endpoint), 'build', None, 'VERIFIED'))
                    if with_intent:
                        from bridge.counterpart.model import make_request
                        request = make_request(creation_id=cid, operation_id=cid + ':create', profile_id=pid,
                                               source={'realm': source, **h.store.get(pid)[source.lower()]},
                                               target_realm=target, target_installation_id=endpoint['installation_id'])
                        h.journal._db.execute('INSERT INTO intents(creation_id, operation_id, profile_id, target_realm, '
                                               'target_installation_id, request, phase) VALUES (?,?,?,?,?,?,?)',
                                               (cid, cid + ':create', pid, target, endpoint['installation_id'],
                                                json.dumps(request), 'LINKED'))
                    yield h, pid, endpoint
                finally:
                    h.close()
        return fixture()

    def trusted_receipts(self, pid, endpoint):
        from services.test_counterpart_core import NOW, READBACK
        from bridge.verification.receipts import make_receipt
        target = endpoint['realm']
        return [make_receipt(check, 'counterpart', f'endpoint:{pid}:{target}', 'PASSED', 'NATIVE_READBACK',
                             realm=target, build_fingerprint='build', provider=READBACK[target], observed_at=NOW,
                             details={'native_save_id': endpoint['native_save_id']})
                for check in ('endpoint.native_save_exists', 'endpoint.identity_matches', 'endpoint.full_readback')]

    def assert_refused_unchanged(self, h, pid, callback):
        before_store = h.store.get(pid)
        before_journal = list(h.journal._db.iterdump())
        with self.assertRaises(JournalError) as refused:
            callback()
        self.assertEqual(refused.exception.code, 'RETIRED_CREATION_ID')
        self.assertEqual(h.store.get(pid), before_store)
        self.assertEqual(list(h.journal._db.iterdump()), before_journal)
        self.assertEqual(h.transport.sent, [])

    def test_retired_profile_provenance_blocks_live_adoption(self):
        for cid in RETIRED:
            for target in ('SR1', 'SR2'):
                for origin in (None, 'LEGACY', 'SOURCE'):
                    with self.subTest(cid=cid, target=target, origin=origin), self.linked_fixture(cid, target, origin) as (h, pid, endpoint):
                        self.assert_refused_unchanged(h, pid, lambda: h.svc.observe_live(pid, target, build_fingerprint='build'))

    def test_retired_profile_provenance_blocks_trusted_endpoint_reverification(self):
        for cid in RETIRED:
            for target in ('SR1', 'SR2'):
                for origin in (None, 'LEGACY', 'SOURCE'):
                    with self.subTest(cid=cid, target=target, origin=origin), self.linked_fixture(cid, target, origin) as (h, pid, endpoint):
                        self.assert_refused_unchanged(h, pid, lambda: h.svc.reverify_endpoint(
                            pid, target, self.trusted_receipts(pid, endpoint), build_fingerprint='build'))

    def test_retired_verified_null_metadata_cannot_be_used_by_pair_or_policy(self):
        from bridge.counterpart.pair import save_pair
        from bridge.counterpart.policy import decide
        from bridge.counterpart.orchestrator import CounterpartOrchestrator
        for cid in RETIRED:
            for target in ('SR1', 'SR2'):
                with self.subTest(cid=cid, target=target), self.linked_fixture(cid, target, 'SOURCE') as (h, pid, endpoint):
                    profile, metadata = h.store.get(pid), h.journal.endpoint_meta(pid)
                    pair = save_pair(profile, [], metadata, current_builds={'SR1': 'build', 'SR2': 'build'})
                    self.assertNotEqual(pair['status'], 'LINKED')
                    self.assertIn('RETIRED_CREATION_ID', pair['reasons'])
                    decision = decide(profile=profile, intents=[], target_realm=target, provider_health='HEALTHY',
                                      slot_status='FREE', runtime_proven=True, endpoint_compatibility='VALID')
                    self.assertEqual(decision['decision'], 'BLOCK_CONFLICT')
                    self.assertIn('RETIRED_CREATION_ID', decision['reasons'])
                    self.assertEqual(h.svc.decide(pid, target, provider_health='HEALTHY', runtime_proven=True,
                                                 current_build='build')['decision'], 'BLOCK_CONFLICT')
                    evidence = type('Evidence', (), {'receipts': lambda *args: self.fail('retired endpoint requested receipts')})()
                    orchestrator = CounterpartOrchestrator(h.svc, sessions=lambda realm: {}, health=lambda realm: 'HEALTHY',
                                                          slots=lambda *args: None, evidence=evidence,
                                                          runtime_proven=lambda realm: True, builds=lambda realm: 'build')
                    trace = orchestrator.run(pid, target, endpoint['installation_id'])
                    self.assertEqual([(e['decision'], e['action']) for e in trace], [('BLOCK_CONFLICT', 'NONE')])
                    self.assertEqual(h.transport.sent, [])

    def test_lower_journal_adoption_refuses_known_retired_intents(self):
        for cid in RETIRED:
            for target in ('SR1', 'SR2'):
                for origin in (None, 'LEGACY', 'SOURCE'):
                    with self.subTest(cid=cid, target=target, origin=origin), self.linked_fixture(cid, target, origin, with_intent=True) as (h, pid, endpoint):
                        self.assert_refused_unchanged(h, pid, lambda: h.journal.observe_live_endpoint(pid, endpoint, 'build'))
                        self.assert_refused_unchanged(h, pid, lambda: h.journal.record_endpoint_verification(
                            pid, target, verdict='VERIFIED', build_fingerprint='build', endpoint=endpoint))

    def test_retired_profile_with_unknown_target_cannot_be_adopted(self):
        with self.linked_fixture(RETIRED[0], 'SR2', target_unknown=True) as (h, pid, endpoint):
            for realm in ('SR1', 'SR2'):
                self.assert_refused_unchanged(h, pid, lambda: h.svc.observe_live(pid, realm, build_fingerprint='build'))

    def test_retired_endpoint_metadata_blocks_policy_without_profile_creation_id(self):
        for target in ('SR1', 'SR2'):
            with self.linked_fixture('f' * 32, target, 'SOURCE') as (h, pid, endpoint):
                h.store._db.execute('UPDATE profiles SET creation_id=NULL WHERE bridge_profile_id=?', (pid,))
                h.store._db.commit()
                h.journal._db.execute('UPDATE endpoint_meta SET creation_id=? WHERE profile_id=? AND realm=?',
                                       (RETIRED[0], pid, target))
                before = list(h.journal._db.iterdump())
                decision = h.svc.decide(pid, target, provider_health='HEALTHY', runtime_proven=True,
                                        current_build='build')
                self.assertEqual(decision['decision'], 'BLOCK_CONFLICT')
                self.assertIn('RETIRED_CREATION_ID', decision['reasons'])
                self.assertEqual(list(h.journal._db.iterdump()), before)
                self.assertEqual(h.transport.sent, [])

    def test_fresh_nonretired_legacy_profile_still_verifies(self):
        import uuid
        for target in ('SR1', 'SR2'):
            with self.linked_fixture(uuid.uuid4().hex, target) as (h, pid, endpoint):
                result = h.svc.reverify_endpoint(pid, target, self.trusted_receipts(pid, endpoint), build_fingerprint='build')
                self.assertEqual(result['verdict'], 'VERIFIED')
                self.assertEqual(h.svc.pair(pid, current_builds={'SR1': 'build', 'SR2': 'build'})['status'], 'LINKED')
                self.assertEqual(h.transport.sent, [])


class RetiredIdBoundaryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.host = BridgeHost(token=TOKEN).__enter__()
        self.store = ProfileStore(tmp / 'profiles.sqlite3')
        self.journal = CounterpartJournal(tmp / 'journal.sqlite3')
        self.transport = BridgeHostTransport(self.host)
        self.svc = CounterpartService(self.journal, ProfileStoreLinks(self.store),
                                      {'SR1': wire_adapter('SR1'), 'SR2': wire_adapter('SR2')}, self.transport, trust={})
        self.pid = self.store.find_or_create('SR2', 'sr2-install', 'native-sr2', 'sr2-identity')['bridge_profile_id']
        self.session = {'pid': os.getpid(), 'session_id': 'host-1'}

    def tearDown(self):
        self.journal.close()
        self.store.__exit__(None, None, None)
        self.host.__exit__(None, None, None)
        self._tmp.cleanup()

    def force_reserved(self, cid):
        """Simulate a stale store already holding a retired id (e.g. restored from an old backup)."""
        self.store._db.execute('UPDATE profiles SET state=?, creation_id=?, creation_target_realm=? '
                               'WHERE bridge_profile_id=?', ('CREATING', cid, 'SR1', self.pid))
        self.store._db.commit()

    def test_every_retired_id_is_denied_by_the_host_enqueue(self):
        for cid in RETIRED:
            for kind in ('create_save', 'reconcile_save'):
                with self.subTest(cid=cid, kind=kind), self.assertRaises(LQ.RetiredCreationError):
                    self.host.enqueue_creation_command('SR1', kind, cid, expected_pid=os.getpid())

    def test_random_new_creation_id_still_allowed(self):
        cid = self.svc.request(self.pid, 'SR1', 'sr1-install')
        self.assertFalse(LQ.is_retired(cid))
        self.assertEqual(self.journal.get(cid)['phase'], 'INTENT_PERSISTED')
        self.assertTrue(self.store.claim_creation(self.pid, cid))
        op = self.host.enqueue_creation_command('SR1', 'create_save', cid, expected_pid=os.getpid())
        self.assertEqual(op, cid + ':create')

    def test_store_never_reserves_or_claims_a_retired_id(self):
        self.force_reserved(RETIRED[0])
        with self.assertRaises(LQ.RetiredCreationError):
            self.store.reserve_counterpart(self.pid, 'SR1')
        with self.assertRaises(LQ.RetiredCreationError):
            self.store.claim_creation(self.pid, RETIRED[0])
        self.assertEqual(self.store.get(self.pid)['creation_issued'], 0)

    def test_service_request_with_retired_reservation_denied(self):
        self.force_reserved(RETIRED[1])
        with self.assertRaises(LQ.RetiredCreationError):
            self.svc.request(self.pid, 'SR1', 'sr1-install')
        self.assertEqual(self.journal.intents_for(self.pid), [])

    def journal_retired_intent(self, cid):
        from bridge.counterpart.model import make_request
        request = make_request(creation_id=cid, operation_id=f'{cid}:create', profile_id=self.pid,
                               source={'realm': 'SR2', 'installation_id': 'sr2-install',
                                       'native_save_id': 'native-sr2', 'native_save_identity': 'sr2-identity'},
                               target_realm='SR1', target_installation_id='sr1-install')
        with self.assertRaises(JournalError) as refused:
            self.journal.open_intent(request)
        self.assertEqual(refused.exception.code, 'RETIRED_CREATION_ID')
        # Bypass the guarded API to model a pre-existing durable row (an old journal file).
        self.journal._db.execute('INSERT INTO intents(creation_id, operation_id, profile_id, target_realm, '
                                 'target_installation_id, request, phase) VALUES (?,?,?,?,?,?,?)',
                                 (cid, f'{cid}:create', self.pid, 'SR1', 'sr1-install', json.dumps(request),
                                  'INTENT_PERSISTED'))
        return request

    def test_durable_intent_reuse_and_dispatch_denied(self):
        cid = RETIRED[2]
        self.journal_retired_intent(cid)
        self.assertFalse(self.journal.begin_dispatch(cid, self.session))
        verdict = self.svc.dispatch(cid, self.session)
        self.assertFalse(verdict['dispatched'])
        self.assertIn('retired', verdict['reason'])
        self.assertEqual(self.host._pending['SR1'], [])

    def test_reconcile_mutation_denied(self):
        cid = RETIRED[3]
        self.journal_retired_intent(cid)
        self.journal._db.execute("UPDATE intents SET phase='UNCERTAIN' WHERE creation_id=?", (cid,))
        self.journal._db.commit()
        with self.assertRaises(JournalError) as refused:
            self.journal.begin_reconcile(cid, self.session)
        self.assertEqual(refused.exception.code, 'RETIRED_CREATION_ID')
        verdict = self.svc.reconcile(cid, self.session)
        self.assertFalse(verdict['sent'])
        self.assertEqual(self.host._pending['SR1'], [])

    def test_all_retired_confirmation_attempts_preserve_store(self):
        for cid in RETIRED:
            with self.subTest(cid=cid):
                self.force_reserved(cid)
                self.store._db.execute('UPDATE profiles SET creation_issued=1 WHERE bridge_profile_id=?', (self.pid,))
                self.store._db.commit()
                before = self.store.get(self.pid)
                with self.assertRaises(LQ.RetiredCreationError):
                    self.store.confirm_counterpart(self.pid, cid, 'sr1-install', 'sb1_' + cid, 'identity-' + cid)
                self.assertEqual(self.store.get(self.pid), before)

    def test_retired_verification_and_link_boundaries_preserve_journal(self):
        for cid in RETIRED:
            self.journal_retired_intent(cid)
            observed = {'realm': 'SR1', 'installation_id': 'sr1-install', 'native_save_id': 'sb1_' + cid,
                        'native_save_identity': 'identity-' + cid, 'native_filename': 'sb1_' + cid + '_0',
                        'save_hash': hashlib.sha256(cid.encode()).hexdigest(), 'slot': None}
            plan = {'plan_id': 'synthetic-plan'}
            callbacks = [
                ('NATIVE_OBSERVED', lambda: self.svc.plan_verification(cid)),
                ('NATIVE_OBSERVED', lambda: self.journal.attach_verification_plan(cid, plan)),
                ('READBACK_PENDING', lambda: self.journal.record_verification(cid, {'plan_id': 'synthetic-plan', 'verdict': 'VERIFIED'})),
                ('VERIFIED', lambda: self.journal.begin_link(cid)),
                ('LINK_PENDING', lambda: self.journal.finish_link(cid, linked=True)),
                ('LINK_PENDING', lambda: self.svc.link(cid)),
                ('LINK_PENDING', lambda: self.svc.finish_link(cid)),
            ]
            for phase, callback in callbacks:
                with self.subTest(cid=cid, phase=phase, callback=callback):
                    self.journal._db.execute('UPDATE intents SET phase=?, observed=?, verification_plan=? WHERE creation_id=?',
                                             (phase, json.dumps(observed), json.dumps(plan), cid))
                    self.journal._db.commit()
                    before = self.journal.get(cid)
                    with self.assertRaises(JournalError) as refused:
                        callback()
                    self.assertEqual(refused.exception.code, 'RETIRED_CREATION_ID')
                    self.assertEqual(self.journal.get(cid), before)
                    self.assertEqual(self.journal.endpoint_meta(self.pid), {})

    def test_restart_recovery_never_promotes_retired_pending_links(self):
        for cid in RETIRED:
            self.force_reserved(cid)
            self.store._db.execute('UPDATE profiles SET creation_issued=1 WHERE bridge_profile_id=?', (self.pid,))
            self.store._db.commit()
            self.journal_retired_intent(cid)
            observed = {'realm': 'SR1', 'installation_id': 'sr1-install', 'native_save_id': 'sb1_' + cid,
                        'native_save_identity': 'identity-' + cid, 'native_filename': 'sb1_' + cid + '_0',
                        'save_hash': hashlib.sha256(cid.encode()).hexdigest(), 'slot': None}
            self.journal._db.execute('UPDATE intents SET phase=?, observed=? WHERE creation_id=?',
                                     ('LINK_PENDING', json.dumps(observed), cid))
            self.journal._db.commit()
        before_store = self.store.get(self.pid)
        before_intents = {cid: self.journal.get(cid) for cid in RETIRED}
        self.journal.close()
        self.journal = CounterpartJournal(Path(self._tmp.name) / 'journal.sqlite3')
        self.svc = CounterpartService(self.journal, ProfileStoreLinks(self.store),
                                      {'SR1': wire_adapter('SR1'), 'SR2': wire_adapter('SR2')}, self.transport, trust={})
        report = self.svc.recover()
        self.assertEqual(report['link_pending'], [])
        self.assertEqual(self.store.get(self.pid), before_store)
        self.assertEqual({cid: self.journal.get(cid) for cid in RETIRED}, before_intents)
        self.assertEqual(self.journal.endpoint_meta(self.pid), {})
        self.assertEqual(self.host._pending['SR1'], [])

    def test_retired_endpoint_metadata_cannot_be_verified_again(self):
        for cid in RETIRED:
            endpoint = {'realm': 'SR1', 'installation_id': 'sr1-install',
                        'native_save_id': 'sb1_' + cid, 'native_save_identity': 'identity-' + cid}
            self.journal._db.execute('INSERT OR REPLACE INTO endpoint_meta VALUES (?,?,?,?,?,?,?,?)',
                                     (self.pid, 'SR1', 'COUNTERPART', cid, json.dumps(endpoint),
                                      'old-build', 'old-plan', 'REVERIFY_REQUIRED'))
            self.journal._db.commit()
            before = self.journal.endpoint_meta(self.pid)
            with self.subTest(cid=cid):
                with self.assertRaises(JournalError) as refused:
                    self.journal.record_endpoint_verification(self.pid, 'SR1', verdict='VERIFIED',
                                                              build_fingerprint='new-build', endpoint=endpoint)
                self.assertEqual(refused.exception.code, 'RETIRED_CREATION_ID')
                self.assertEqual(self.journal.endpoint_meta(self.pid), before)

    def test_cached_or_wire_success_for_retired_id_cannot_be_accepted(self):
        cid = RETIRED[4]
        self.journal_retired_intent(cid)
        reply = {'realm': 'SR1', 'op_id': f'{cid}:create', 'kind': 'create_save', 'creation_id': cid,
                 'pid': self.session['pid'], 'ok': True, 'applied': True, 'verified': True, 'uncertain': False,
                 'persisted': True, 'save_id': 'sb1_' + cid, 'save_file': f'sb1_{cid}_0', 'sha256': 'a' * 64}
        self.assertEqual(self.svc.on_wire_result('SR1', reply, session=self.session),
                         {'verdict': 'REJECTED', 'code': 'RETIRED_CREATION_ID'})
        self.assertEqual(self.journal.get(cid)['phase'], 'INTENT_PERSISTED')


if __name__ == '__main__':
    unittest.main()
