"""End-to-end content release on synthetic data: package -> deploy unit -> journaled install -> proof plan.

The installed files are the synthetic extraction bytes; runtime requirements stay NOT_RUN, so the
release is never reported as verified in Cloud.
"""

import tempfile
import unittest
from pathlib import Path

from bridge.app.content import catalogue
from bridge.content.canonical import canonical_json
from bridge.deploy.executor import DeployExecutor, DeployJournal, MemoryFS
from bridge.deploy.planner import plan_deploy
from bridge.deploy.units import unit_from_generated_package
from bridge.packages import package as P
from bridge.verification.plans import STATIC_VERIFIER, aggregate, evaluate_static, package_plan
from services.test_packages import batch, package_for, receipts_for


def synthetic_bytes(pkg):
    """Bytes whose sha256 equals the synthetic extraction receipt digest for each artifact."""
    out = {}
    for artifact in pkg['artifacts']:
        if artifact['sha256'] is not None:
            out[artifact['sha256']] = canonical_json({'synthetic-extract': artifact['source_hash']}).encode()
    return out


class ContentReleaseTests(unittest.TestCase):
    def test_converted_species_installs_atomically_and_stays_unverified(self):
        pkg = package_for('slime.azure')
        pkg = P.materialize(pkg, receipts_for(pkg))
        self.assertTrue(P.installable(pkg), P.validate_package(pkg))
        unit = unit_from_generated_package(pkg)
        blobs = synthetic_bytes(pkg)
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeployJournal(Path(tmp) / 'deploy.sqlite3')
            try:
                fs = MemoryFS()
                plan = plan_deploy(journal.inventory('SR1'), [{'key': unit['key'], 'state': 'ENABLED', 'unit': unit}],
                                   build='synthetic-sr1-target-build-1')
                self.assertTrue(plan['executable'], plan['blockers'])
                outcome = DeployExecutor(journal, fs, blobs.get).apply(plan)
                self.assertEqual(outcome['state'], 'COMMITTED')
                live = {k for k in fs.files if k.startswith('live/')}
                self.assertEqual(live, {f"live/{f['path']}" for f in unit['files']})
                rows = {r['canonical_id']: r for r in catalogue({'b': batch()}, inventories={
                    'SR1': journal.inventory('SR1'), 'SR2': journal.inventory('SR2')})['rows']}
                azure = rows['slime.azure']['directions']['SR2->SR1']
                self.assertEqual((azure['installed']['state'], azure['verification']), ('ENABLED', 'NOT_RUN'))
            finally:
                journal.close()
        proof = package_plan(pkg)
        result = aggregate(proof, evaluate_static(proof, pkg), trust={STATIC_VERIFIER['provider_id']: '*'})
        self.assertEqual(result['verdict'], 'INCOMPLETE')
        self.assertEqual(result['sections']['RUNTIME'], ['NOT_RUN'])

    def test_quarantined_species_cannot_be_enabled(self):
        pkg = package_for('slime.hollow')  # missing critical mesh: quarantined, nothing to materialize
        self.assertFalse(P.installable(pkg))
        unit = unit_from_generated_package(pkg)
        self.assertEqual(unit['status'], 'QUARANTINED')
        with tempfile.TemporaryDirectory() as tmp:
            journal = DeployJournal(Path(tmp) / 'deploy.sqlite3')
            try:
                plan = plan_deploy(journal.inventory('SR1'), [{'key': unit['key'], 'state': 'ENABLED', 'unit': unit}])
                self.assertFalse(plan['executable'])
                self.assertIn('UNIT_NOT_DEPLOYABLE', {b['code'] for b in plan['blockers']})
            finally:
                journal.close()


if __name__ == '__main__':
    unittest.main()
