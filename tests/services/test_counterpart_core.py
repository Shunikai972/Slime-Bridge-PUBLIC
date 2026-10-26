"""Generic counterpart core: request/result binding, durable journal fences, verification, linking."""

import re
import tempfile
import unittest
from pathlib import Path

from bridge.content.canonical import sha256_text
from bridge.core import diagnostics as D
from bridge.counterpart import model
from bridge.counterpart.journal import CounterpartJournal, JournalError
from bridge.counterpart.links import ProfileStoreLinks
from bridge.counterpart.pair import endpoint_compatibility
from bridge.counterpart.policy import decide
from bridge.counterpart.realms import CONVENTIONS, sr1_disk_readback_receipt, wire_adapter
from bridge.counterpart.service import CounterpartService
from bridge.counterpart.startup import plan_startup
from bridge.counterpart.world_ready import evaluate_world_ready
from bridge.profile.store import ProfileStore
from bridge.verification.receipts import make_receipt

ROOT = Path(__file__).resolve().parents[2]
NOW = '2026-10-04T12:00:00Z'
SESSION_A = {'pid': 4100, 'session_id': 'host-1'}
SESSION_B = {'pid': 4200, 'session_id': 'host-2'}
READBACK = {'SR1': {'provider_id': 'local.sr1-readback', 'provider_version': '1'},
            'SR2': {'provider_id': 'local.sr2-readback', 'provider_version': '1'}}
TRUST = {READBACK['SR1']['provider_id']: 'SR1', READBACK['SR2']['provider_id']: 'SR2'}
SOURCE = {'SR1': ('sr1-install', 'native-sr1-save', 'sr1-identity'),
          'SR2': ('sr2-install', 'native-sr2-save', 'sr2-identity')}


class Transport:
    def __init__(self):
        self.sent, self.live, self.fail_send = [], True, False

    def preflight(self, realm, session):
        return self.live

    def send(self, realm, command, session):
        if self.fail_send:
            raise ConnectionError('pipe closed')
        self.sent.append((realm, dict(command), dict(session)))


def wire(realm, cid, op, kind='create_save', session=SESSION_A, **extra):
    prefix = CONVENTIONS[realm].marker_prefix
    reply = {'realm': realm, 'op_id': op, 'kind': kind, 'creation_id': cid, 'pid': session['pid'], 'ok': True,
             'applied': kind == 'create_save', 'verified': True, 'uncertain': False, 'persisted': True,
             'save_id': prefix + cid, 'save_file': f'{prefix}{cid}_0', 'sha256': sha256_text(cid)}
    reply.update(extra)
    return reply


def absent(realm, cid, op, session):
    return {'realm': realm, 'op_id': op, 'kind': 'reconcile_save', 'creation_id': cid, 'pid': session['pid'],
            'ok': True, 'can_create': True, 'verified': False, 'uncertain': False,
            'applied': False, 'persisted': False, 'before': 0, 'after': 0}


def receipts_for(svc, cid, *, realm, provider=None, save_id=None, evidence='NATIVE_READBACK', status='PASSED'):
    intent = svc.journal.get(cid)
    observed = intent['observed']
    out = []
    for check in ('counterpart.native_save_exists', 'counterpart.no_conflicting_candidate',
                  'counterpart.full_readback', 'counterpart.save_hash'):
        out.append(make_receipt(check, 'counterpart', cid, status, evidence, realm=realm, build_fingerprint='build-1',
                                provider=provider or READBACK[realm], observed_at=NOW,
                                artifact_hashes={'save': observed['save_hash']},
                                details={'native_save_id': save_id or observed['native_save_id']}))
    out.append(make_receipt('counterpart.slot_matches', 'counterpart', cid, 'NOT_APPLICABLE', 'NONE', realm=realm,
                            provider=provider or READBACK[realm],
                            diagnostics=[D.make('NO_SLOT', 'info', 'counterpart', 'realm has no slot')]))
    return out


class Harness:
    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.store = ProfileStore(self.tmp / 'profiles.sqlite3')
        self.journal = CounterpartJournal(self.tmp / 'journal.sqlite3')
        self.transport = Transport()
        self.svc = CounterpartService(self.journal, ProfileStoreLinks(self.store),
                                      {'SR1': wire_adapter('SR1'), 'SR2': wire_adapter('SR2')}, self.transport,
                                      trust=TRUST)

    def close(self):
        self.journal.close()
        self.store.__exit__(None, None, None)

    def profile(self, realm):
        return self.store.find_or_create(realm, *SOURCE[realm])['bridge_profile_id']

    def created(self, source, target):
        pid = self.profile(source)
        cid = self.svc.request(pid, target, f'{target.lower()}-install')
        assert self.svc.dispatch(cid, SESSION_A)['dispatched']
        return pid, cid

    def through_link(self, source, target):
        pid, cid = self.created(source, target)
        verdict = self.svc.on_wire_result(target, wire(target, cid, f'{cid}:create'), session=SESSION_A)
        assert verdict == {'verdict': 'ACCEPTED', 'phase': 'NATIVE_OBSERVED'}, verdict
        self.svc.plan_verification(cid, build_fingerprint='build-1')
        assert self.svc.verify(cid, receipts_for(self.svc, cid, realm=target))['phase'] == 'VERIFIED'
        assert self.svc.link(cid, build_fingerprint='build-1')['phase'] == 'LINKED'
        return pid, cid


class CounterpartCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.h = Harness(self._tmp.name)
        self.svc, self.journal, self.store = self.h.svc, self.h.journal, self.h.store

    def tearDown(self):
        self.h.close()
        self._tmp.cleanup()


class ModelTests(unittest.TestCase):
    def request(self, **over):
        fields = dict(creation_id='a' * 32, operation_id='a' * 32 + ':create', profile_id='p',
                      source={'realm': 'SR1', 'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x'},
                      target_realm='SR2', target_installation_id='t')
        fields.update(over)
        return model.make_request(**fields)

    def test_request_is_realm_neutral_and_provenance_bound(self):
        request = self.request()
        self.assertEqual(model.validate_request(request), [])
        self.assertNotIn('sb1_', repr(request) + repr(model.make_request.__defaults__))
        tampered = dict(request, target_installation_id='other')
        self.assertTrue(any('provenance_ref' in p for p in model.validate_request(tampered)))
        with self.assertRaises(ValueError):
            self.request(target_realm='SR1')
        with self.assertRaises(ValueError):
            self.request(slot_policy={'mode': 'PREFERRED', 'preferred_slot': None})

    def test_result_invariants(self):
        base = dict(creation_id='a' * 32, operation_id='op', operation_kind='create', profile_id='p', target_realm='SR2',
                    installation_id='t', provider={'provider_id': 'x', 'provider_version': '1'},
                    session=SESSION_A, evidence='NATIVE_READBACK')
        self.assertTrue(model.validate_result(model.make_result(outcome='CREATED', native_effect='CONFIRMED', **base)))
        self.assertTrue(model.validate_result(model.make_result(outcome='ABSENT', native_effect='NONE', **base)))
        self.assertTrue(model.validate_result(model.make_result(outcome='UNCERTAIN', native_effect='NONE', **base)))
        self.assertTrue(model.validate_result(model.make_result(
            outcome='NO_CAPACITY', native_effect='NONE', native_save_id='s', native_save_identity='i', **base)))
        ok = model.make_result(outcome='CREATED', native_effect='CONFIRMED', native_save_id='s',
                               native_save_identity='i', **base)
        self.assertEqual(model.validate_result(ok), [])
        # F5: FAILED is a definitive no-effect answer; anything weaker must be UNCERTAIN
        self.assertTrue(model.validate_result(model.make_result(outcome='FAILED', native_effect='POSSIBLE', **base)))
        self.assertTrue(model.validate_result(model.make_result(outcome='FAILED', native_effect='NONE',
                                                                native_save_id='s', **base)))
        self.assertEqual(model.validate_result(model.make_result(outcome='FAILED', native_effect='NONE', **base)), [])

    def test_save_identity_needs_core_facts(self):
        a = {'realm': 'SR2', 'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x',
             'display_name': 'Ranch'}
        self.assertTrue(model.same_save(a, dict(a, display_name='Other')))
        self.assertFalse(model.same_save(a, dict(a, native_save_identity=None)))
        self.assertFalse(model.same_save(a, dict(a, save_hash='1' * 64) | {'native_save_id': 't'}))
        self.assertFalse(model.same_save({'display_name': 'Ranch'}, {'display_name': 'Ranch'}))
        self.assertGreater(model.identity_strength(dict(a, save_hash='1' * 64)), model.identity_strength(a))

    def test_generic_modules_contain_no_realm_markers(self):
        generic = [p for p in (ROOT / 'bridge' / 'counterpart').glob('*.py') if p.name not in ('realms.py', 'simulator.py')]
        for path in generic:
            text = path.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r"sb[12]_|sr[12]-creation", text), path.name)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.request = model.make_request(
            creation_id='b' * 32, operation_id='b' * 32 + ':create', profile_id='p',
            source={'realm': 'SR2', 'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x'},
            target_realm='SR1', target_installation_id='t')
        self.adapter = wire_adapter('SR1')
        self.cid, self.op = 'b' * 32, 'b' * 32 + ':create'

    def interpret(self, reply, kind='create', op=None):
        return self.adapter.interpret_result(self.request, kind, op or self.op, reply, session=SESSION_A)

    def test_verified_create_maps_to_created_with_realm_identity(self):
        result = self.interpret(wire('SR1', self.cid, self.op))
        self.assertEqual((result['outcome'], result['native_effect'], result['evidence']),
                         ('CREATED', 'CONFIRMED', 'NATIVE_READBACK'))
        self.assertEqual(result['native_save_id'], 'sb1_' + self.cid)
        self.assertEqual(result['native_save_identity'], 'sr1-creation:' + self.cid)

    def test_unbindable_replies_raise(self):
        for change in ({'realm': 'SR2'}, {'op_id': 'x'}, {'kind': 'reconcile_save'}, {'creation_id': 'c' * 32},
                       {'pid': 1}, {'ok': 'yes'}, {'save_id': 'sb1_' + 'c' * 32}, {'verified': 'true'},
                       {'save_file': 'sb1_' + 'c' * 32 + '_0'}, {'save_file': '../escape'}, {'save_file': 7},
                       {'sha256': ['x']}, {'applied': 'yes'}, {'reason': {'nested': 1}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.interpret(dict(wire('SR1', self.cid, self.op), **change))

    def test_ambiguous_replies_are_uncertain_never_success_or_absence(self):
        for change in ({'verified': None}, {'ok': True, 'verified': False}, {'ok': False, 'uncertain': True},
                       {'ok': False, 'uncertain': False, 'applied': True}, {'save_id': ''}):
            with self.subTest(change=change):
                result = self.interpret(dict(wire('SR1', self.cid, self.op), **change))
                self.assertEqual((result['outcome'], result['native_effect']), ('UNCERTAIN', 'POSSIBLE'))

    def test_reconcile_absence_and_refusal(self):
        op = self.cid + ':reconcile:1'
        result = self.interpret(absent('SR1', self.cid, op, SESSION_A), kind='reconcile', op=op)
        self.assertEqual((result['outcome'], result['native_effect']), ('ABSENT', 'NONE'))
        refused = dict(wire('SR1', self.cid, self.op), ok=False, applied=False, persisted=False, verified=False,
                       save_id='', reason='not at the main menu')
        result = self.interpret(refused)
        self.assertEqual((result['outcome'], result['native_effect']), ('FAILED', 'NONE'))
        self.assertEqual(result['diagnostics'][0]['code'], 'NATIVE_REASON')


class FlowTests(CounterpartCase):
    def test_both_directions_link_through_the_profile_store(self):
        for source, target in (('SR1', 'SR2'), ('SR2', 'SR1')):
            with self.subTest(direction=f'{source}->{target}'):
                pid, cid = self.h.through_link(source, target)
                snap = self.store.get(pid)
                self.assertEqual(snap['state'], 'LINKED')
                self.assertEqual(snap[target.lower()]['native_save_id'],
                                 CONVENTIONS[target].marker_prefix + cid)
                self.assertEqual(self.journal.endpoint_meta(pid)[target]['verdict'], 'VERIFIED')
                pair = self.svc.pair(pid, current_builds={'SR1': 'build-1', 'SR2': 'build-1'})
                self.assertEqual(pair['status'], 'REVERIFY_REQUIRED')  # source endpoint never observed live
                self.svc.observe_live(pid, source, build_fingerprint='build-1')
                pair = self.svc.pair(pid, current_builds={'SR1': 'build-1', 'SR2': 'build-1'})
                self.assertEqual(pair['status'], 'LINKED')
                commands = [c for r, c, s in self.h.transport.sent if c['creation_id'] == cid]
                self.assertEqual([c['kind'] for c in commands], ['create_save'])

    def test_dispatch_fence_is_single_use(self):
        pid, cid = self.h.created('SR1', 'SR2')
        again = self.svc.dispatch(cid, SESSION_A)
        self.assertFalse(again['dispatched'])
        self.assertEqual(len(self.h.transport.sent), 1)
        self.assertFalse(self.journal.begin_dispatch(cid, SESSION_B))

    def test_failed_send_is_uncertain_and_never_resent_as_create(self):
        pid = self.h.profile('SR2')
        cid = self.svc.request(pid, 'SR1', 'sr1-install')
        self.h.transport.fail_send = True
        self.assertFalse(self.svc.dispatch(cid, SESSION_A)['dispatched'])
        self.assertEqual(self.journal.get(cid)['phase'], 'UNCERTAIN')
        self.h.transport.fail_send = False
        self.assertFalse(self.svc.dispatch(cid, SESSION_A)['dispatched'])
        sent = self.svc.reconcile(cid, SESSION_A)
        self.assertTrue(sent['sent'])
        self.assertEqual([c['kind'] for r, c, s in self.h.transport.sent], ['reconcile_save'])

    def test_refusal_after_a_possible_effect_never_closes_the_creation(self):
        """F5: create answered 'maybe' then 'refused, nothing written' stays UNCERTAIN until reconciled."""
        pid, cid = self.h.created('SR1', 'SR2')
        op = f'{cid}:create'
        maybe = wire('SR2', cid, op, ok=False, uncertain=True, verified=False, save_id=None)
        self.assertEqual(self.svc.on_wire_result('SR2', maybe, session=SESSION_A)['phase'], 'UNCERTAIN')
        refused = wire('SR2', cid, op, ok=False, uncertain=False, applied=False, persisted=False, verified=False,
                       save_id=None)
        self.assertEqual(self.svc.on_wire_result('SR2', refused, session=SESSION_A)['phase'], 'UNCERTAIN')
        self.assertEqual(self.journal.get(cid)['phase'], 'UNCERTAIN')
        # a clean refusal with no earlier possible effect still fails the creation
        pid2, cid2 = self.h.created('SR2', 'SR1')
        clean = wire('SR1', cid2, f'{cid2}:create', ok=False, uncertain=False, applied=False, persisted=False,
                     verified=False, save_id=None)
        self.assertEqual(self.svc.on_wire_result('SR1', clean, session=SESSION_A)['phase'], 'FAILED')

    def test_preflight_failure_fences_nothing(self):
        pid = self.h.profile('SR1')
        cid = self.svc.request(pid, 'SR2', 'sr2-install')
        self.h.transport.live = False
        self.assertFalse(self.svc.dispatch(cid, SESSION_A)['dispatched'])
        self.assertEqual(self.journal.get(cid)['phase'], 'INTENT_PERSISTED')
        self.assertEqual(self.store.get(pid)['creation_issued'], 0)

    def test_request_is_idempotent_and_one_active_intent_per_realm(self):
        pid = self.h.profile('SR1')
        cid = self.svc.request(pid, 'SR2', 'sr2-install')
        self.assertEqual(self.svc.request(pid, 'SR2', 'sr2-install'), cid)
        with self.assertRaises(JournalError) as ctx:
            self.svc.request(pid, 'SR2', 'another-install')
        self.assertEqual(ctx.exception.code, 'DUPLICATE_INTENT_CONFLICT')

    def test_replayed_result_is_duplicate_and_conflicting_reply_rejected(self):
        pid, cid = self.h.created('SR1', 'SR2')
        reply = wire('SR2', cid, f'{cid}:create')
        self.assertEqual(self.svc.on_wire_result('SR2', reply, session=SESSION_A)['verdict'], 'ACCEPTED')
        self.assertEqual(self.svc.on_wire_result('SR2', reply, session=SESSION_A)['verdict'], 'DUPLICATE')
        changed = dict(reply, sha256=sha256_text('other'))
        self.assertEqual(self.svc.on_wire_result('SR2', changed, session=SESSION_A),
                         {'verdict': 'REJECTED', 'code': 'CONFLICTING_REPLY'})

    def test_strict_binding_rejections(self):
        pid, cid = self.h.created('SR1', 'SR2')
        op = f'{cid}:create'
        cases = {'UNBINDABLE_REPLY': ('SR2', dict(wire('SR2', cid, op), pid=999), SESSION_A),
                 'WRONG_REALM': ('SR1', wire('SR1', cid, op), SESSION_A),
                 'NO_DURABLE_INTENT': ('SR2', wire('SR2', 'f' * 32, 'f' * 32 + ':create'), SESSION_A),
                 'STALE_OR_UNKNOWN_OPERATION': ('SR2', wire('SR2', cid, f'{cid}:reconcile:9', kind='reconcile_save'),
                                                SESSION_A),
                 'WRONG_SESSION': ('SR2', wire('SR2', cid, op, session=SESSION_B), SESSION_B)}
        for code, (realm, reply, session) in cases.items():
            with self.subTest(code=code):
                verdict = self.svc.on_wire_result(realm, reply, session=session)
                self.assertEqual((verdict['verdict'], verdict['code']), ('REJECTED', code))
        for junk in ([cid], None, 'not-hex', 7):
            self.assertEqual(self.svc.on_wire_result('SR2', dict(wire('SR2', cid, op), creation_id=junk), session=SESSION_A),
                             {'verdict': 'REJECTED', 'code': 'MALFORMED_REPLY'})
        self.assertEqual(self.svc.on_wire_result('SR2', 'not a dict', session=SESSION_A)['code'], 'MALFORMED_REPLY')
        self.assertEqual(self.journal.get(cid)['phase'], 'DISPATCHED')
        adapter = self.svc.adapters['SR2']
        good = adapter.interpret_result(self.journal.get(cid)['request'], 'create', op, wire('SR2', cid, op),
                                        session=SESSION_A)
        for field, value, code in (('profile_id', 'other', 'WRONG_PROFILE'),
                                   ('installation_id', 'other', 'WRONG_INSTALLATION'),
                                   ('operation_kind', 'reconcile', 'WRONG_OPERATION_KIND'),
                                   ('native_save_id', 'sb2_' + 'e' * 32, 'PROVENANCE_MISMATCH')):
            with self.subTest(code=code), self.assertRaises(JournalError) as ctx:
                self.journal.accept_result(dict(good, **{field: value}), adapter=adapter)
            self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(JournalError) as ctx:
            self.journal.accept_result(good, adapter=adapter, expected_source={'realm': 'SR1'})
        self.assertEqual(ctx.exception.code, 'SOURCE_ENDPOINT_MISMATCH')

    def test_absence_proves_nothing_from_the_dispatch_session(self):
        pid, cid = self.h.created('SR1', 'SR2')
        self.svc.timeout(cid)
        op = self.svc.reconcile(cid, SESSION_A)['operation_id']
        self.assertEqual(self.svc.on_wire_result('SR2', absent('SR2', cid, op, SESSION_A), session=SESSION_A),
                         {'verdict': 'ACCEPTED', 'phase': 'UNCERTAIN'})
        op = self.svc.reconcile(cid, SESSION_B)['operation_id']
        self.assertEqual(self.svc.on_wire_result('SR2', absent('SR2', cid, op, SESSION_B), session=SESSION_B),
                         {'verdict': 'ACCEPTED', 'phase': 'FAILED_ABSENT'})
        late = self.svc.on_wire_result('SR2', wire('SR2', cid, f'{cid}:create'), session=SESSION_A)
        self.assertEqual((late['verdict'], late['code']), ('REJECTED', 'LATE_RESULT'))

    def test_restart_makes_in_flight_uncertain_and_reconcile_finds_the_save(self):
        pid, cid = self.h.created('SR2', 'SR1')
        self.journal.close()
        self.h.journal = journal = CounterpartJournal(self.h.tmp / 'journal.sqlite3')
        self.svc.journal = journal
        report = self.svc.recover()
        self.assertEqual(report['uncertain'], [cid])
        self.assertEqual(report['diagnostics'][0]['code'], 'COUNTERPART_RECOVERED_UNCERTAIN')
        self.assertFalse(self.svc.dispatch(cid, SESSION_B)['dispatched'])
        op = self.svc.reconcile(cid, SESSION_B)['operation_id']
        reply = wire('SR1', cid, op, kind='reconcile_save', session=SESSION_B, applied=False)
        self.assertEqual(self.svc.on_wire_result('SR1', reply, session=SESSION_B),
                         {'verdict': 'ACCEPTED', 'phase': 'NATIVE_OBSERVED'})
        self.assertEqual(journal.last_outcomes(pid), {cid: 'ALREADY_EXISTS'})

    def test_different_save_after_observation_is_a_conflict(self):
        pid, cid = self.h.created('SR1', 'SR2')
        self.svc.on_wire_result('SR2', wire('SR2', cid, f'{cid}:create'), session=SESSION_A)
        intent = self.journal.get(cid)
        adapter = self.svc.adapters['SR2']
        self.journal.mark_uncertain(cid, 'n/a')  # no-op outside dispatch phases
        other = adapter.interpret_result(intent['request'], 'create', f'{cid}:create',
                                         wire('SR2', cid, f'{cid}:create'), session=SESSION_A)
        other = dict(other, native_save_identity='forged-identity')
        self.journal._db.execute("UPDATE operations SET answer=NULL WHERE operation_id=?", (f'{cid}:create',))
        self.journal.accept_result(other, adapter=adapter)
        self.assertEqual(self.journal.get(cid)['phase'], 'CONFLICT')


class VerificationTests(CounterpartCase):
    def observed(self, source='SR1', target='SR2'):
        pid, cid = self.h.created(source, target)
        self.svc.on_wire_result(target, wire(target, cid, f'{cid}:create'), session=SESSION_A)
        self.svc.plan_verification(cid, build_fingerprint='build-1')
        return pid, cid

    def test_plugin_success_alone_never_links(self):
        pid, cid = self.observed()
        result = self.svc.verify(cid, [])
        self.assertEqual((result['phase'], result['verdict']), ('READBACK_PENDING', 'INCOMPLETE'))
        with self.assertRaises(JournalError) as ctx:
            self.svc.link(cid)
        self.assertEqual(ctx.exception.code, 'NOT_VERIFIED')
        self.assertEqual(self.store.get(pid)['state'], 'CREATING')

    def test_untrusted_cross_realm_synthetic_and_unbound_receipts_are_rejected(self):
        pid, cid = self.observed()
        bad = (receipts_for(self.svc, cid, realm='SR2', provider=READBACK['SR1']) +
               receipts_for(self.svc, cid, realm='SR2', evidence='SYNTHETIC') +
               receipts_for(self.svc, cid, realm='SR2', save_id='sb2_' + 'e' * 32))
        result = self.svc.verify(cid, bad)
        self.assertEqual(result['verdict'], 'INCOMPLETE')
        reasons = {r['reason'] for r in result['rejected']}
        self.assertTrue({'UNTRUSTED_PROVIDER', 'EVIDENCE_NOT_ACCEPTED', 'UNBOUND_SAVE'} <= reasons, reasons)

    def test_failed_readback_is_a_conflict(self):
        pid, cid = self.observed()
        result = self.svc.verify(cid, receipts_for(self.svc, cid, realm='SR2', status='FAILED'))
        self.assertEqual(result['phase'], 'CONFLICT')

    def test_link_refused_by_store_is_a_conflict(self):
        pid, cid = self.observed()
        self.svc.verify(cid, receipts_for(self.svc, cid, realm='SR2'))
        # Another profile already owns this native save: ProfileStore refuses the cross-link.
        self.store.find_or_create('SR2', 'sr2-install', 'sb2_' + cid, 'someone-else')
        result = self.svc.link(cid)
        self.assertEqual(result['phase'], 'CONFLICT')
        self.assertEqual(result['diagnostics'][0]['code'], 'COUNTERPART_LINK_REFUSED')
        self.assertEqual(self.store.get(pid)['state'], 'CREATING')

    def test_recover_finishes_a_half_done_link(self):
        pid, cid = self.observed()
        self.svc.verify(cid, receipts_for(self.svc, cid, realm='SR2'))
        self.journal.begin_link(cid)  # crash before ProfileStore.confirm
        report = self.svc.recover(build_fingerprints={'SR2': 'build-1'})
        self.assertEqual(report['link_pending'], [cid])
        self.assertEqual(self.journal.get(cid)['phase'], 'LINKED')
        self.assertEqual(self.store.get(pid)['state'], 'LINKED')

    def test_finishing_one_link_leaves_other_in_flight_creations_alone(self):
        """F7: FINISH_LINK completes its own creation; it is not a host restart for everyone else."""
        from bridge.counterpart.orchestrator import CounterpartOrchestrator
        busy_pid, busy_cid = self.h.created('SR1', 'SR2')            # another profile, dispatched, in flight
        pid, cid = self.observed('SR2', 'SR1')
        self.svc.verify(cid, receipts_for(self.svc, cid, realm='SR1'))
        self.journal.begin_link(cid)                                  # crash before ProfileStore.confirm
        orchestrator = CounterpartOrchestrator(self.svc, sessions=lambda realm: SESSION_A,
                                               health=lambda realm: 'HEALTHY', slots=lambda realm, inst: None,
                                               evidence=None, runtime_proven=lambda realm: True,
                                               builds=lambda realm: 'build-1')
        entry = orchestrator.step(pid, 'SR1', 'sr1-install')
        self.assertEqual((entry['action'], entry['result']['phase']), ('FINISH_LINK', 'LINKED'))
        self.assertEqual(self.store.get(pid)['state'], 'LINKED')
        self.assertEqual(self.journal.get(busy_cid)['phase'], 'DISPATCHED')
        self.assertEqual(self.svc.finish_link(cid), {'phase': 'LINKED', 'diagnostics': []})  # idempotent

    def test_sr1_disk_readback_receipt_binds_the_exact_file(self):
        pid, cid = self.observed('SR2', 'SR1')
        intent = self.journal.get(cid)
        save_root = self.h.tmp / 'saves'
        save_root.mkdir()
        (save_root / (intent['observed']['native_filename'] + '.sav')).write_bytes(b'synthetic')
        result = {'operation_id': f'{cid}:create', 'creation_id': cid, 'native_save_id': 'sb1_' + cid,
                  'session': SESSION_A, 'native_filename': intent['observed']['native_filename'],
                  'save_hash': intent['observed']['save_hash']}
        receipt = sr1_disk_readback_receipt(result, save_root=save_root, provider=READBACK['SR1'], observed_at=NOW,
                                            build_fingerprint='build-1')
        self.assertEqual(receipt['status'], 'FAILED')  # synthetic bytes do not match the reported hash
        (save_root / (intent['observed']['native_filename'] + '.sav')).write_bytes(cid.encode())
        receipt = sr1_disk_readback_receipt(result, save_root=save_root, provider=READBACK['SR1'], observed_at=NOW,
                                            build_fingerprint='build-1')
        self.assertEqual((receipt['status'], receipt['details']['native_save_id']), ('PASSED', 'sb1_' + cid))


class PolicyTests(CounterpartCase):
    def decide(self, pid, target, **over):
        args = dict(provider_health='HEALTHY', slot_probe={'complete': True, 'free_slots': [3]}, runtime_proven=True,
                    current_build='build-1')
        args.update(over)
        return self.svc.decide(pid, target, **args)['decision']

    def test_decisions_follow_durable_state(self):
        pid = self.h.profile('SR1')
        self.assertEqual(self.decide(pid, 'SR2'), 'CREATE_COUNTERPART')
        self.assertEqual(self.decide(pid, 'SR2', runtime_proven=False), 'NEEDS_LOCAL_RUNTIME_PROOF')
        self.assertEqual(self.decide(pid, 'SR2', provider_health='UNAVAILABLE'), 'BLOCK_PROVIDER_UNAVAILABLE')
        self.assertEqual(self.decide(pid, 'SR2', slot_probe={'complete': True, 'free_slots': []}), 'BLOCK_NO_CAPACITY')
        self.assertEqual(self.decide(pid, 'SR2', slot_probe={'complete': False}), 'BLOCK_PROVIDER_UNAVAILABLE')
        self.assertEqual(self.decide(pid, 'SR1'), 'BLOCK_CONFLICT')
        cid = self.svc.request(pid, 'SR2', 'sr2-install')
        self.assertEqual(self.decide(pid, 'SR2'), 'CREATE_COUNTERPART')
        self.svc.dispatch(cid, SESSION_A)
        self.assertEqual(self.decide(pid, 'SR2'), 'WAIT_FOR_NATIVE_RESULT')
        self.svc.timeout(cid)
        self.assertEqual(self.decide(pid, 'SR2', runtime_proven=False), 'RECONCILE_PENDING_CREATION')
        self.assertEqual(self.decide(pid, 'SR2', provider_health='UNKNOWN'), 'BLOCK_PROVIDER_UNAVAILABLE')
        op = self.svc.reconcile(cid, SESSION_A)['operation_id']
        self.svc.on_wire_result('SR2', wire('SR2', cid, op, kind='reconcile_save', applied=False), session=SESSION_A)
        self.assertEqual(self.decide(pid, 'SR2'), 'VERIFY_EXISTING_CANDIDATE')

    def test_linked_pairs_and_build_changes(self):
        pid, cid = self.h.through_link('SR2', 'SR1')
        self.assertEqual(self.decide(pid, 'SR1'), 'USE_EXISTING_LINK')
        self.assertEqual(self.decide(pid, 'SR1', current_build='build-2'), 'VERIFY_EXISTING_CANDIDATE')
        self.assertEqual(self.decide(pid, 'SR1', current_build=None), 'NEEDS_LOCAL_RUNTIME_PROOF')
        self.assertEqual(self.decide(pid, 'SR1', supported_builds=('build-0',)), 'BLOCK_CONFLICT')

    def test_failed_creation_is_never_retried_automatically(self):
        pid = self.h.profile('SR1')
        cid = self.svc.request(pid, 'SR2', 'sr2-install')
        self.svc.dispatch(cid, SESSION_A)
        refused = dict(wire('SR2', cid, f'{cid}:create'), ok=False, applied=False, persisted=False, verified=False,
                       save_id='')
        self.assertEqual(self.svc.on_wire_result('SR2', refused, session=SESSION_A)['phase'], 'FAILED')
        decision = self.svc.decide(pid, 'SR2', provider_health='HEALTHY', runtime_proven=True,
                                   slot_probe={'complete': True, 'free_slots': [1]})
        self.assertEqual((decision['decision'], decision['reasons']), ('BLOCK_CONFLICT', ['CREATION_NOT_ABANDONABLE']))

    def test_store_refusing_the_fence_is_a_conflict_and_sends_nothing(self):
        pid = self.h.profile('SR1')
        cid = self.svc.request(pid, 'SR2', 'sr2-install')
        self.store._db.execute("UPDATE profiles SET creation_id='other' WHERE bridge_profile_id=?", (pid,))
        self.store._db.commit()
        self.assertFalse(self.svc.dispatch(cid, SESSION_A)['dispatched'])
        self.assertEqual(self.journal.get(cid)['phase'], 'CONFLICT')
        self.assertEqual(self.h.transport.sent, [])

    def test_legacy_issued_creation_is_reconciled_not_recreated(self):
        profile = {'bridge_profile_id': 'p', 'state': 'CREATING', 'creation_id': 'c' * 32,
                   'creation_target_realm': 'SR2', 'creation_issued': 1,
                   'sr1': {'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x'}, 'sr2': None}
        result = decide(profile=profile, intents=[], target_realm='SR2', provider_health='HEALTHY',
                        slot_status='FREE', runtime_proven=True)
        self.assertEqual((result['decision'], result['next_step']), ('RECONCILE_PENDING_CREATION', 'adopt_and_reconcile'))


class PairAndStartupTests(CounterpartCase):
    def test_endpoint_compatibility(self):
        self.assertEqual(endpoint_compatibility('b1', 'b1'), 'VALID')
        self.assertEqual(endpoint_compatibility('b1', 'b2'), 'REVERIFY_REQUIRED')
        self.assertEqual(endpoint_compatibility(None, 'b2'), 'REVERIFY_REQUIRED')
        self.assertEqual(endpoint_compatibility('b1', None), 'UNKNOWN')
        self.assertEqual(endpoint_compatibility('b1', 'b9', supported_builds=('b1',)), 'INCOMPATIBLE')
        self.assertEqual(endpoint_compatibility('b1', 'b1', verdict='FAILED'), 'INCOMPATIBLE')

    def test_pair_statuses_and_reverification_never_relinks(self):
        pid = self.h.profile('SR2')
        self.assertEqual(self.svc.pair(pid)['status'], 'SR2_ONLY')
        cid = self.svc.request(pid, 'SR1', 'sr1-install')
        self.assertEqual(self.svc.pair(pid)['pending']['creation_id'], cid)
        self.assertEqual(self.svc.pair(pid)['status'], 'PENDING_COUNTERPART')
        self.svc.dispatch(cid, SESSION_A)
        self.svc.on_wire_result('SR1', wire('SR1', cid, f'{cid}:create'), session=SESSION_A)
        self.svc.plan_verification(cid, build_fingerprint='build-1')
        self.svc.verify(cid, receipts_for(self.svc, cid, realm='SR1'))
        self.svc.link(cid, build_fingerprint='build-1')
        self.svc.observe_live(pid, 'SR2', build_fingerprint='b-sr2')
        builds = {'SR1': 'build-2', 'SR2': 'b-sr2'}
        self.assertEqual(self.svc.pair(pid, current_builds=builds)['status'], 'REVERIFY_REQUIRED')
        before = self.store.get(pid)
        incomplete = self.svc.reverify_endpoint(pid, 'SR1', [], build_fingerprint='build-2')
        self.assertEqual(incomplete['verdict'], 'REVERIFY_REQUIRED')
        endpoint = before['sr1']
        receipts = [make_receipt(check, 'counterpart', f'endpoint:{pid}:SR1', 'PASSED', 'NATIVE_READBACK', realm='SR1',
                                 build_fingerprint='build-2', provider=READBACK['SR1'], observed_at=NOW,
                                 details={'native_save_id': endpoint['native_save_id']})
                    for check in ('endpoint.native_save_exists', 'endpoint.identity_matches', 'endpoint.full_readback')]
        self.assertEqual(self.svc.reverify_endpoint(pid, 'SR1', receipts, build_fingerprint='build-2')['verdict'],
                         'VERIFIED')
        self.assertEqual(self.svc.pair(pid, current_builds=builds)['status'], 'LINKED')
        self.assertEqual(self.store.get(pid), before)

    def test_live_observation_never_upgrades_a_verdict_or_moves_the_build(self):
        """F3: a live identification is weak evidence: first sight only; a new build forces re-verification."""
        pid, cid = self.h.through_link('SR1', 'SR2')
        meta = lambda realm: {k: self.journal.endpoint_meta(pid)[realm][k] for k in ('origin', 'verdict',
                                                                                     'build_fingerprint')}
        self.assertEqual(self.svc.observe_live(pid, 'SR1', build_fingerprint='build-1'), 'VERIFIED')
        self.assertEqual(meta('SR1'), {'origin': 'SOURCE', 'verdict': 'VERIFIED', 'build_fingerprint': 'build-1'})
        self.assertEqual(self.svc.observe_live(pid, 'SR1', build_fingerprint='build-1'), 'VERIFIED')  # no-op
        self.assertEqual(self.svc.observe_live(pid, 'SR1', build_fingerprint='build-2'), 'REVERIFY_REQUIRED')
        self.assertEqual(meta('SR1'), {'origin': 'SOURCE', 'verdict': 'REVERIFY_REQUIRED',
                                       'build_fingerprint': 'build-1'})
        # observing again under the new build must not launder the verdict back to VERIFIED
        self.assertEqual(self.svc.observe_live(pid, 'SR1', build_fingerprint='build-2'), 'REVERIFY_REQUIRED')
        self.assertEqual(self.svc.pair(pid, current_builds={'SR1': 'build-2', 'SR2': 'build-1'})['status'],
                         'REVERIFY_REQUIRED')
        # a counterpart endpoint is unchanged by a same-build sighting and never upgraded after a failure
        self.assertEqual(self.svc.observe_live(pid, 'SR2', build_fingerprint='build-1'), 'VERIFIED')
        self.assertEqual(meta('SR2'), {'origin': 'COUNTERPART', 'verdict': 'VERIFIED', 'build_fingerprint': 'build-1'})
        self.journal.record_endpoint_verification(pid, 'SR2', verdict='FAILED', build_fingerprint='build-1')
        for build in ('build-1', 'build-3'):
            self.assertEqual(self.svc.observe_live(pid, 'SR2', build_fingerprint=build), 'FAILED')
        self.assertEqual(meta('SR2'), {'origin': 'COUNTERPART', 'verdict': 'FAILED', 'build_fingerprint': 'build-1'})

    def test_startup_plan_is_fact_driven(self):
        plan = plan_startup()
        self.assertEqual((plan['current'], plan['transition_available']), ('ATTACH_SOURCE', False))
        facts = dict(source_session=SESSION_A, source_save={'realm': 'SR1'}, profile={'bridge_profile_id': 'p'})
        self.assertEqual(plan_startup(**facts)['current'], 'RECONCILE_COUNTERPART')
        create = {'decision': 'CREATE_COUNTERPART', 'reasons': ['NO_EXISTING_COUNTERPART']}
        plan = plan_startup(**facts, decision=create)
        self.assertEqual(plan['current'], 'CREATE_COUNTERPART')
        self.assertEqual(plan['steps'][3]['state'], 'SKIPPED')
        blocked = plan_startup(**facts, decision={'decision': 'BLOCK_NO_CAPACITY', 'reasons': ['NO_FREE_SLOT']})
        self.assertEqual(blocked['blocked']['step'], 'CREATE_COUNTERPART')
        linked = dict(facts, decision={'decision': 'USE_EXISTING_LINK', 'reasons': []},
                      destination_endpoint={'realm': 'SR2'})
        self.assertEqual(plan_startup(**linked)['current'], 'PREPARE_DESTINATION')
        waiting = plan_startup(**linked, destination_session=SESSION_B, destination_save_confirmed=True,
                               world_ready=(False, []))
        self.assertEqual(waiting['current'], 'AWAIT_READINESS')
        ready = plan_startup(**linked, destination_session=SESSION_B, destination_save_confirmed=True,
                             world_ready=(True, []))
        self.assertTrue(ready['transition_available'])
        cold = plan_startup(**linked, warm=False)
        self.assertTrue(cold['transition_available'])
        self.assertEqual(cold['mode'], 'COLD')


class WorldReadyTests(unittest.TestCase):
    EXPECTED = {'realm': 'SR2', 'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x',
                'build_fingerprint': 'b', 'session': SESSION_B}

    def report(self, **over):
        report = {'schema': 'bridge-world-ready', 'schema_version': 1, 'realm': 'SR2', 'session': SESSION_B,
                  'installation_id': 'i', 'native_save_id': 's', 'native_save_identity': 'x', 'build_fingerprint': 'b',
                  'process_running': True, 'load_callback_returned': True, 'scene_settled': True,
                  'player_usable': True, 'bridge_synchronized': True, 'observed_at': NOW,
                  'provider': READBACK['SR2'], 'evidence': 'RUNTIME_OBSERVATION'}
        report.update(over)
        return report

    def test_only_a_settled_bound_trusted_runtime_report_is_ready(self):
        self.assertEqual(evaluate_world_ready(self.report(), expected=self.EXPECTED, trust=TRUST), (True, []))
        cases = {'WORLD_READY_SCENE_SETTLED_FALSE': {'scene_settled': False},
                 'WORLD_READY_WRONG_NATIVE_SAVE_ID': {'native_save_id': 'other'},
                 'WORLD_READY_WRONG_SESSION': {'session': SESSION_A},
                 'WORLD_READY_NOT_RUNTIME': {'evidence': 'SYNTHETIC'},
                 'WORLD_READY_UNTRUSTED': {'provider': READBACK['SR1']},
                 'WORLD_READY_MALFORMED': {'player_usable': 'yes'}}
        for code, change in cases.items():
            with self.subTest(code=code):
                ready, diags = evaluate_world_ready(self.report(**change), expected=self.EXPECTED, trust=TRUST)
                self.assertFalse(ready)
                self.assertIn(code, [d['code'] for d in diags])
        process_only = self.report(scene_settled=False, player_usable=False, bridge_synchronized=False)
        self.assertFalse(evaluate_world_ready(process_only, expected=self.EXPECTED, trust=TRUST)[0])


if __name__ == '__main__':
    unittest.main()
