"""Generated-content packages: identity, validation, materialization, comparison, compatibility."""
import copy
import unittest
from functools import lru_cache

from bridge.content.canonical import sha256_text
from bridge.conversion.extraction import SyntheticExtractionProvider
from bridge.core import diagnostics as D
from bridge.packages import package as P
from conversion.test_batch import run
from conversion.test_cache import scratch


@lru_cache(maxsize=1)
def batch():
    return run()


def package_for(cid):
    out = batch()
    result = out['results'][cid]
    root = f"SR2:{result['products']['ir']['identity']['native_definition']['ref']}" \
        if result['products']['ir'] else None
    deps = out['graph'].dependency_hashes(root) if root else []
    return P.build_generated_package(result, dependency_hashes=deps)


def receipts_for(pkg):
    provider = SyntheticExtractionProvider('SR2')
    plan_items = pkg['target_package']['artifacts']
    return [provider.extract({'node': a['source_node'], 'source_hash': a['source_hash'], 'artifact': a['artifact']})
            for a in plan_items]


def codes(diags):
    return {d['code'] for d in diags}


class GeneratedPackageTests(unittest.TestCase):
    def test_deterministic_identity_and_valid_manifest(self):
        first, second = package_for('slime.azure'), package_for('slime.azure')
        self.assertEqual(first, second)
        self.assertEqual(P.validate_package(first), [])
        self.assertEqual(first['lineage'], {'canonical_id': 'slime.azure', 'source_realm': 'SR2',
                                            'target_realm': 'SR1', 'direction': 'SR2->SR1'})
        self.assertEqual(first['canonical_ids']['plorts'], ['plort.azure'])
        self.assertTrue(first['source']['dependency_hashes'])
        self.assertTrue(all(a['path'].startswith('artifacts/') and a['sha256'] is None for a in first['artifacts']))
        self.assertFalse(P.installable(first))  # planned, not materialized

    def test_tampering_is_detected(self):
        pkg = package_for('slime.azure')
        tampered = copy.deepcopy(pkg)
        tampered['status'] = 'EXPERIMENTAL' if pkg['status'] != 'EXPERIMENTAL' else 'PENDING_ADAPTER'
        self.assertIn('PACKAGE_ID_MISMATCH', codes(P.validate_package(tampered)))
        escaped = copy.deepcopy(pkg)
        escaped['artifacts'][0]['path'] = 'artifacts/../../evil'
        P._seal(escaped)
        self.assertIn('ARTIFACT_PATH_UNSAFE', codes(P.validate_package(escaped)))
        foreign = copy.deepcopy(pkg)
        foreign['registration_plan']['operations'][0]['canonical_id'] = 'slime.pink'
        P._seal(foreign)
        self.assertIn('FOREIGN_REGISTRATION', codes(P.validate_package(foreign)))
        lying = copy.deepcopy(pkg)
        lying['synthetic'] = False
        P._seal(lying)
        self.assertIn('EVIDENCE_CONTRADICTION', codes(P.validate_package(lying)))
        self.assertIn('PACKAGE_SCHEMA_INVALID', codes(P.validate_package({'schema': 'nope'})))
        self.assertIn('PACKAGE_SCHEMA_INVALID', codes(P.validate_package([])))

    def test_quarantined_conversion_yields_quarantined_package(self):
        pkg = package_for('slime.hollow')
        self.assertEqual(pkg['status'], 'QUARANTINED')
        self.assertEqual(P.validate_package(pkg), [])
        self.assertFalse(P.installable(pkg))
        record = P.quarantine_record(pkg, ['MISSING_CRITICAL_DEPENDENCY'])
        self.assertEqual(record['diagnostics'][0]['code'], 'PACKAGE_QUARANTINED')
        self.assertEqual(D.validate(record['diagnostics'][0]), [])

    def test_materialization_binds_receipts_and_store_digests(self):
        pkg = package_for('slime.azure')
        materialized = P.materialize(pkg, receipts_for(pkg))
        self.assertTrue(materialized['materialized'])
        self.assertEqual(P.validate_package(materialized), [])
        self.assertTrue(P.installable(materialized))
        self.assertNotEqual(materialized['package_id'], pkg['package_id'])
        bad = receipts_for(pkg)
        bad[0] = dict(bad[0], source_hash=sha256_text('other source'))
        rejected = P.materialize(pkg, bad)
        self.assertIn('ARTIFACT_RECEIPT_REJECTED', codes(rejected['diagnostics']))
        if any(a['required'] and a['missing'] for a in rejected['artifacts']):
            self.assertFalse(P.installable(rejected))
        with scratch() as root:
            # Synthetic bytes only: write files whose digests we then put in the manifest.
            receipts = []
            for item in pkg['target_package']['artifacts']:
                data = f'synthetic bytes for {item["artifact"]}'.encode()
                path = root / 'artifacts' / item['artifact']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                receipts.append({'node': item['source_node'], 'status': 'EXTRACTED', 'source_hash': item['source_hash'],
                                 'synthetic': True, 'location': item['artifact'],
                                 'sha256': __import__('hashlib').sha256(data).hexdigest()})
            stored = P.materialize(pkg, receipts)
            self.assertEqual(P.verify_artifact_store(stored, root), [])
            (root / 'artifacts' / pkg['target_package']['artifacts'][0]['artifact']).write_bytes(b'changed')
            self.assertIn('ARTIFACT_DIGEST_MISMATCH', codes(P.verify_artifact_store(stored, root)))

    def test_compare_diff_and_compatibility(self):
        pkg = P.materialize(package_for('slime.azure'), receipts_for(package_for('slime.azure')))
        self.assertEqual(P.compare(pkg, pkg), 'IDENTICAL')
        newer = copy.deepcopy(pkg)
        newer['converter'] = dict(newer['converter'], converter_version='1.1.0')
        P._seal(newer)
        self.assertEqual(P.compare(pkg, newer), 'UPGRADE')
        self.assertEqual(P.compare(newer, pkg), 'DOWNGRADE')
        rebuilt = copy.deepcopy(pkg)
        rebuilt['source']['closure_hash'] = sha256_text('new closure')
        P._seal(rebuilt)
        self.assertEqual(P.compare(pkg, rebuilt), 'REBUILD')
        self.assertEqual(P.compare(pkg, package_for('slime.prism')), 'INCOMPATIBLE')
        delta = P.diff(pkg, rebuilt)
        self.assertEqual(delta['fields_changed'], ['source'])
        self.assertEqual(delta['artifacts'], {'added': [], 'removed': [], 'changed': []})
        build = pkg['target']['build_compatibility']['fingerprints'][0]
        self.assertEqual(P.target_compatibility(pkg, 'SR1', build), 'COMPATIBLE')
        self.assertEqual(P.target_compatibility(pkg, 'SR1', 'updated-game-build'), 'REVERIFY_REQUIRED')
        self.assertEqual(P.target_compatibility(pkg, 'SR2', build), 'INCOMPATIBLE')


class DiagnosticsTests(unittest.TestCase):
    def test_unified_diagnostics_are_strict_and_serializable(self):
        item = D.make('SLOT_CONFLICT', 'error', 'counterpart', 'slot taken', realm='SR2', details={'slot': 3},
                      action='reconcile', evidence='NEEDS_LOCAL_RUNTIME_PROOF')
        self.assertEqual(D.validate(item), [])
        for bad in (lambda: D.make('lower', 'error', 'core', 'x'), lambda: D.make('X', 'fatal', 'core', 'x'),
                    lambda: D.make('X', 'error', 'nowhere', 'x'),
                    lambda: D.make('X', 'error', 'core', 'x', details={'v': float('nan')})):
            with self.assertRaises((ValueError, TypeError)):
                bad()
        self.assertTrue(D.validate({**item, 'extra': 1}))
        self.assertEqual(D.sort([item, item]), [item])


if __name__ == '__main__':
    unittest.main()


class SchemaStrictnessTests(unittest.TestCase):
    def test_enum_is_type_strict(self):
        from bridge.core import schema as S
        self.assertTrue(S.check(S.enum(1), True))
        self.assertTrue(S.check(S.enum(1), 1.0))
        self.assertEqual(S.check(S.enum(1), 1), [])


class EvidenceVocabularyTests(unittest.TestCase):
    def test_visual_proof_is_a_first_class_evidence_level(self):
        from bridge.content.canonical import Evidence
        diag = D.make('PORTAL_MODEL_UNREVIEWED', 'info', 'meshgen', 'generated, not yet seen in a game',
                      evidence='NEEDS_LOCAL_VISUAL_PROOF')
        self.assertEqual(diag['evidence'], Evidence.NEEDS_LOCAL_VISUAL_PROOF)
        with self.assertRaises(ValueError):
            D.make('X', 'info', 'meshgen', 'x', evidence='RUNTIME_PROVEN')  # never a Cloud evidence level
