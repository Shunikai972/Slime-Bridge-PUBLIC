"""Synthetic wire-contract checks; no native parser or game runtime evidence."""
import unittest

from bridge.counterpart.realms import wire_adapter


class WireProofTests(unittest.TestCase):
    def fixtures(self, realm, *, pid=4100, kind='create'):
        cid = 'd' * 32
        prefix = 'sb1_' if realm == 'SR1' else 'sb2_'
        op = cid + ':' + kind
        request = {'creation_id': cid, 'profile_id': 'unit-profile',
                   'target_realm': realm, 'target_installation_id': realm.lower() + '-unit'}
        session = {'pid': pid, 'session_id': 'unit-session'}
        wire = {'realm': realm, 'op_id': op, 'creation_id': cid,
                'kind': 'create_save' if kind == 'create' else 'reconcile_save',
                'pid': pid, 'ok': True, 'verified': True, 'applied': kind == 'create',
                'persisted': True, 'uncertain': False, 'save_id': prefix + cid,
                'save_file': prefix + cid + '_0', 'sha256': 'a' * 64}
        return wire_adapter(realm), request, session, wire, op

    def test_verified_success_requires_complete_noncontradictory_proof(self):
        for realm in ('SR1', 'SR2'):
            adapter, request, session, wire, op = self.fixtures(realm)
            self.assertEqual(adapter.interpret_result(request, 'create', op, wire, session=session)['outcome'], 'CREATED')
            for changed in ({'save_file': None}, {'save_file': ''}, {'sha256': None}, {'sha256': ''},
                            {'sha256': 'A' * 64}, {'uncertain': True}, {'uncertain': None},
                            {'applied': None}, {'persisted': None}, {'persisted': False}):
                with self.subTest(realm=realm, changed=changed):
                    with self.assertRaises(ValueError):
                        adapter.interpret_result(request, 'create', op, {**wire, **changed}, session=session)

    def test_boolean_pid_cannot_bind_to_integer_pid_one(self):
        adapter, request, session, wire, op = self.fixtures('SR1', pid=1)
        with self.assertRaises(ValueError):
            adapter.interpret_result(request, 'create', op, {**wire, 'pid': True}, session=session)

    def test_sr1_read_only_existing_proof_does_not_require_a_new_flush(self):
        adapter, request, session, wire, op = self.fixtures('SR1', kind='reconcile')
        observed = adapter.interpret_result(request, 'reconcile', op,
                                            {**wire, 'persisted': False}, session=session)
        self.assertEqual(observed['outcome'], 'ALREADY_EXISTS')
        self.assertEqual(observed['native_filename'], wire['save_file'])

    def test_reconciliation_cannot_claim_an_applied_creation(self):
        adapter, request, session, wire, op = self.fixtures('SR2', kind='reconcile')
        with self.assertRaises(ValueError):
            adapter.interpret_result(request, 'reconcile', op, {**wire, 'applied': True}, session=session)


if __name__ == '__main__':
    unittest.main()
