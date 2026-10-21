"""Verification framework: impossible receipts rejected; Cloud can never pass runtime requirements."""
import unittest

from bridge.content.canonical import sha256_text
from bridge.core import diagnostics as D
from bridge.verification import plans as V
from bridge.verification.receipts import make_receipt, validate_receipt
from services.test_packages import package_for, receipts_for
from bridge.packages.package import materialize

PROVIDER = {'provider_id': 'local.sr1-runtime', 'provider_version': '0.1.0'}
SESSION = {'pid': 4242, 'session_id': 'lab-session-1', 'process_image_sha256': None}
TRUST = {'bridge.static-verifier': '*', 'local.sr1-runtime': 'SR1', 'local.sr2-runtime': 'SR2'}


def codes(diags):
    return {d['code'] for d in diags}


def runtime_receipt(plan, check_id, status='PASSED', evidence_type='RUNTIME_OBSERVATION', **override):
    fields = dict(realm='SR1', build_fingerprint=plan['binding']['target_build'], provider=PROVIDER,
                  observed_at='2026-10-04T12:00:00Z', session=SESSION)
    fields.update(override)
    return make_receipt(check_id, 'package', plan['subject_id'], status, evidence_type, **fields)


class ReceiptTests(unittest.TestCase):
    def test_impossible_combinations_are_rejected(self):
        ok = make_receipt('runtime.save', 'package', 'p', 'PASSED', 'RUNTIME_OBSERVATION', realm='SR1',
                          build_fingerprint='b', provider=PROVIDER, observed_at='2026-10-04T12:00:00Z', session=SESSION)
        self.assertEqual(validate_receipt(ok), [])
        cases = {
            'RECEIPT_IMPOSSIBLE': make_receipt('c', 'package', 'p', 'PASSED', 'NONE', provider=PROVIDER),
            'RECEIPT_SESSION_MISSING': make_receipt('c', 'package', 'p', 'PASSED', 'RUNTIME_OBSERVATION', realm='SR1',
                                                    build_fingerprint='b', provider=PROVIDER,
                                                    observed_at='2026-10-04T12:00:00Z'),
            'RECEIPT_UNBOUND': make_receipt('c', 'package', 'p', 'FAILED', 'DISK_READBACK', provider=PROVIDER,
                                            observed_at='2026-10-04T12:00:00Z'),
            'RECEIPT_REASON_MISSING': make_receipt('c', 'package', 'p', 'BLOCKED', 'NONE'),
            'RECEIPT_PROVIDER_MISSING': make_receipt('c', 'package', 'p', 'FAILED', 'STATIC_ANALYSIS'),
        }
        for code, receipt in cases.items():
            self.assertIn(code, codes(validate_receipt(receipt)), code)
        not_run = make_receipt('c', 'package', 'p', 'NOT_RUN', 'NONE', artifact_hashes={'log': sha256_text('x')})
        self.assertIn('RECEIPT_IMPOSSIBLE', codes(validate_receipt(not_run)))
        tampered = dict(ok, status='FAILED')
        self.assertIn('RECEIPT_ID_MISMATCH', codes(validate_receipt(tampered)))
        self.assertIn('RECEIPT_SCHEMA_INVALID', codes(validate_receipt({'schema': 'x'})))
        self.assertIn('RECEIPT_SCHEMA_INVALID', codes(validate_receipt(dict(ok, observed_at='yesterday'))))


class PackagePlanTests(unittest.TestCase):
    def setUp(self):
        base = package_for('slime.azure')
        self.package = materialize(base, receipts_for(base))
        self.plan = V.package_plan(self.package)
        self.static = V.evaluate_static(self.plan, self.package)

    def test_plan_sections_and_determinism(self):
        sections = {r['section'] for r in self.plan['requirements']}
        self.assertEqual(sections, {'SOURCE', 'CANONICAL_IR', 'TARGET_PACKAGE', 'RUNTIME'})
        self.assertEqual(self.plan, V.package_plan(self.package))
        self.assertEqual(self.static, V.evaluate_static(self.plan, self.package))
        self.assertTrue(all(validate_receipt(r) == [] for r in self.static))

    def test_cloud_static_evaluation_never_verifies_the_package(self):
        result = V.aggregate(self.plan, self.static, trust=TRUST)
        self.assertNotEqual(result['verdict'], 'VERIFIED')
        self.assertEqual(result['requirements']['source.registry_proof']['status'], 'BLOCKED')   # synthetic source
        self.assertEqual(result['requirements']['package.artifacts']['status'], 'PASSED')
        self.assertTrue(all(v['status'] == 'NOT_RUN' for k, v in result['requirements'].items() if k.startswith('runtime.')))

    def test_synthetic_untrusted_and_misbound_runtime_receipts_are_rejected(self):
        synthetic = runtime_receipt(self.plan, 'runtime.save', evidence_type='SYNTHETIC', session=None)
        wrong_realm = runtime_receipt(self.plan, 'runtime.save', realm='SR2',
                                      provider={'provider_id': 'local.sr2-runtime', 'provider_version': '0.1.0'})
        forged_realm = runtime_receipt(self.plan, 'runtime.save',
                                       provider={'provider_id': 'local.sr2-runtime', 'provider_version': '0.1.0'})
        wrong_build = runtime_receipt(self.plan, 'runtime.save', build_fingerprint='other-build')
        static_claims_runtime = make_receipt('runtime.save', 'package', self.plan['subject_id'], 'PASSED',
                                             'STATIC_ANALYSIS', provider=V.STATIC_VERIFIER)
        stranger = runtime_receipt(self.plan, 'runtime.save',
                                   provider={'provider_id': 'someone.else', 'provider_version': '1.0.0'})
        other_subject = make_receipt('runtime.save', 'package', 'f' * 64, 'PASSED', 'RUNTIME_OBSERVATION', realm='SR1',
                                     build_fingerprint=self.plan['binding']['target_build'], provider=PROVIDER,
                                     observed_at='2026-10-04T12:00:00Z', session=SESSION)
        na = runtime_receipt(self.plan, 'runtime.save', status='NOT_APPLICABLE', evidence_type='NONE', session=None,
                             observed_at=None, diagnostics=[D.make('NA', 'info', 'verification', 'n/a')])
        result = V.aggregate(self.plan, [synthetic, wrong_realm, forged_realm, wrong_build, static_claims_runtime,
                                         stranger, other_subject, na], trust=TRUST)
        reasons = sorted(r['reason'] for r in result['rejected'])
        self.assertEqual(reasons, sorted(['EVIDENCE_NOT_ACCEPTED', 'WRONG_REALM', 'UNTRUSTED_PROVIDER', 'WRONG_BUILD',
                                          'EVIDENCE_NOT_ACCEPTED', 'UNTRUSTED_PROVIDER', 'WRONG_SUBJECT',
                                          'NOT_APPLICABLE_FORBIDDEN']))
        self.assertEqual(result['requirements']['runtime.save']['status'], 'NOT_RUN')

    def test_trusted_runtime_receipts_can_verify_and_conflicts_fail(self):
        receipts = list(self.static)
        # Make the SOURCE proof pass as a runtime-anchored local registry would (simulated local attestation).
        receipts = [r for r in receipts if r['check_id'] != 'source.registry_proof']
        receipts.append(make_receipt('source.registry_proof', 'package', self.plan['subject_id'], 'PASSED',
                                     'STATIC_ANALYSIS', provider=V.STATIC_VERIFIER))
        for req in self.plan['requirements']:
            if req['section'] == 'RUNTIME':
                receipts.append(runtime_receipt(self.plan, req['check_id']))
        result = V.aggregate(self.plan, receipts, trust=TRUST)
        self.assertEqual(result['verdict'], 'VERIFIED', [k for k, v in result['requirements'].items()
                                                          if v['status'] != 'PASSED'])
        receipts.append(runtime_receipt(self.plan, 'runtime.reload', status='FAILED'))
        conflict = V.aggregate(self.plan, receipts, trust=TRUST)
        self.assertEqual(conflict['requirements']['runtime.reload']['status'], 'CONFLICT')
        self.assertEqual(conflict['verdict'], 'FAILED')

    def test_unmaterialized_or_quarantined_packages_fail_static_sections(self):
        planned = package_for('slime.azure')
        result = V.aggregate(V.package_plan(planned), V.evaluate_static(V.package_plan(planned), planned), trust=TRUST)
        self.assertEqual(result['requirements']['package.artifacts']['status'], 'FAILED')
        hollow = package_for('slime.hollow')
        plan = V.package_plan(hollow)
        result = V.aggregate(plan, V.evaluate_static(plan, hollow), trust=TRUST)
        self.assertEqual(result['verdict'], 'FAILED')


if __name__ == '__main__':
    unittest.main()


class SubsystemPlanTests(unittest.TestCase):
    def test_transition_and_multiplayer_plans_need_runtime_evidence(self):
        from bridge.multiplayer.server import ServerConfig
        from bridge.multiplayer.verification import multiplayer_plan
        from bridge.transition.machine import make_intent
        from bridge.transition.verification import transition_plan
        from bridge.verification.plans import aggregate
        endpoint = {r: {'realm': r, 'installation_id': 'i', 'native_save_id': f's{r}', 'native_save_identity': 'x'}
                    for r in ('SR1', 'SR2')}
        intent = make_intent(profile_id='p', source={'realm': 'SR1', 'session': {'pid': 1, 'session_id': 'a'},
                                                     'endpoint': endpoint['SR1']},
                             destination={'realm': 'SR2', 'session': None, 'endpoint': endpoint['SR2']})
        plan = transition_plan(intent, builds={'SR1': 'b1', 'SR2': 'b2'})
        realms = {r['check_id']: r['realm'] for r in plan['requirements']}
        self.assertEqual((realms['transition.source_locked_and_synced'], realms['transition.world_ready_before_visible']),
                         ('SR1', 'SR2'))
        self.assertEqual(aggregate(plan, [], trust={})['verdict'], 'INCOMPLETE')
        hybrid = multiplayer_plan(ServerConfig(name='Hub', mode='HYBRID', required_content={'SR1': 'a' * 64}).to_dict())
        ids = {r['check_id'] for r in hybrid['requirements']}
        self.assertIn('multiplayer.cross_realm_chat', ids)
        self.assertIn('multiplayer.content_gate_sr1', ids)
        self.assertNotIn('multiplayer.content_gate_sr2', ids)
        solo = multiplayer_plan(ServerConfig(name='Solo', mode='SR2_ONLY').to_dict())
        self.assertFalse(any('sr1' in r['check_id'] for r in solo['requirements']))


class ReviewRegressionTests(unittest.TestCase):
    def test_anonymous_or_synthetic_waivers_never_satisfy_runtime_requirements(self):
        """F2: NOT_APPLICABLE/BLOCKED receipts need a trusted provider; SYNTHETIC never counts on runtime sections."""
        from bridge.core import diagnostics as D
        from bridge.verification.plans import aggregate, make_plan, requirement
        from bridge.verification.receipts import make_receipt, validate_receipt
        plan = make_plan('package', 'pkg', [requirement('runtime.diet', 'RUNTIME', 'eats', accepted=('RUNTIME_OBSERVATION',),
                                                        realm='SR2', build='b', not_applicable_allowed=True)])
        reason = [D.make('NOT_RELEVANT', 'info', 'verification', 'no diet')]
        anonymous = make_receipt('runtime.diet', 'package', 'pkg', 'NOT_APPLICABLE', 'SYNTHETIC', realm='SR2',
                                 diagnostics=reason)
        self.assertIn('RECEIPT_PROVIDER_MISSING', {d['code'] for d in validate_receipt(anonymous)})
        self.assertEqual(aggregate(plan, [anonymous], trust={})['verdict'], 'INCOMPLETE')
        trusted = {'local.sr2': 'SR2'}
        synthetic = make_receipt('runtime.diet', 'package', 'pkg', 'NOT_APPLICABLE', 'SYNTHETIC', realm='SR2',
                                 provider={'provider_id': 'local.sr2', 'provider_version': '1'}, diagnostics=reason)
        result = aggregate(plan, [synthetic], trust=trusted)
        self.assertEqual((result['verdict'], result['rejected'][0]['reason']), ('INCOMPLETE', 'SYNTHETIC_ON_RUNTIME'))
        wrong_realm = make_receipt('runtime.diet', 'package', 'pkg', 'NOT_APPLICABLE', 'NONE', realm='SR1',
                                   provider={'provider_id': 'local.sr2', 'provider_version': '1'}, diagnostics=reason)
        self.assertEqual(aggregate(plan, [wrong_realm], trust=trusted)['rejected'][0]['reason'], 'WRONG_REALM')
        waived = make_receipt('runtime.diet', 'package', 'pkg', 'NOT_APPLICABLE', 'NONE', realm='SR2',
                              provider={'provider_id': 'local.sr2', 'provider_version': '1'}, diagnostics=reason)
        self.assertEqual(aggregate(plan, [waived], trust=trusted)['verdict'], 'VERIFIED')
