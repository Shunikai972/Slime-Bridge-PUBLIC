"""Contract tests for runtime registry captures written by the SR1/SR2 NativeRegistryCollector.

The fixture text mirrors mods/shared/RegistrySnapshotFormat.cs; the C# serializer itself is pinned
by the RegistryCaptureHarness against the same canonical hash. Synthetic data only.
"""

import hashlib
import json
import subprocess
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from bridge.content.registry_capture import CaptureError, canonical_registry_sha256, load_capture
from bridge.registry.snapshot import snapshot_from_scan
from bridge.registry.reconcile import classify_species


@contextmanager
def fixture_dir():
    with tempfile.TemporaryDirectory(prefix='registry-capture-') as name:
        yield Path(name)


def _row(ref, native_id, reference, largo=False, bases=(), plorts=()):
    return {'object_ref': ref, 'native_id': native_id, 'reference_id': reference, 'name': reference,
            'is_largo': largo, 'can_largofy': not largo, 'base_slimes': list(bases),
            'produced_plorts': list(plorts), 'prefab_refs': [reference + 'Prefab'],
            'evidence': ['runtime registry traversal']}


def write_capture(root, realm, rows, build_bytes=b'fixture-build', expected=None, tamper=None):
    build = root / 'active-build.bin'
    build.write_bytes(build_bytes)
    digest = hashlib.sha256(build_bytes).hexdigest()
    rows = sorted(rows, key=lambda r: r['object_ref'])
    registry = canonical_registry_sha256(rows)
    expected = len(rows) if expected is None else expected
    identity = 'GameContext.SlimeDefinitions.Slimes'
    snapshot = {'schema_version': 1, 'realm': realm, 'game_build': digest, 'build_match': 'VERIFIED',
                'registry_traversed': True, 'completeness': 'COMPLETE' if expected == len(rows) else 'PARTIAL',
                'collector': {'version': '2', 'run_id': 'run1', 'counts': {'slimes_array': expected, 'by_identifiable': expected}},
                'build_proof': {'active_sha256': digest, 'scanned_sha256': digest,
                                'active_path': str(build), 'provenance': 'plugin hashed loaded file'},
                'traversal_proof': {'registry_identity': identity, 'registry_sha256': registry,
                                    'expected_count': expected, 'observed_count': len(rows)},
                'definitions': rows, 'errors': []}
    receipt = {'schema': 'bridge-traversal-receipt', 'schema_version': 1, 'realm': realm,
               'collector_version': '2', 'run_id': 'run1', 'active_sha256': digest,
               'registry_identity': identity, 'registry_sha256': registry, 'expected_count': expected,
               'observed_count': len(rows), 'counts': {'slimes_array': expected, 'by_identifiable': expected},
               'object_refs': [r['object_ref'] for r in rows]}
    if tamper:
        tamper(snapshot, receipt)
    (root / 'registry-snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
    (root / 'traversal-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    return build


SR1_ROWS = [_row('SR1:def:1', 1, 'PINK_SLIME', plorts=['PINK_PLORT']),
            _row('SR1:def:2', 2, 'TABBY_SLIME', plorts=['TABBY_PLORT']),
            _row('SR1:def:3', 3, 'PINK_TABBY_LARGO', largo=True, bases=['PINK_SLIME', 'TABBY_SLIME'],
                 plorts=['PINK_PLORT', 'TABBY_PLORT'])]


class RegistryCaptureTests(unittest.TestCase):
    def test_canonical_hash_is_pinned(self):
        rows = [_row('R:a', 7, 'A\u00e9"', plorts=['P'])]
        line = json.dumps(['R:a', 7, 'A\u00e9"', 'A\u00e9"', False, True, [], ['P'], ['A\u00e9"Prefab']],
                          separators=(',', ':'), ensure_ascii=True) + '\n'
        self.assertEqual(canonical_registry_sha256(rows), hashlib.sha256(line.encode()).hexdigest())
        # Literal shared with tests/RegistryCaptureHarness (C# RegistrySnapshotFormat.RegistrySha256).
        self.assertEqual(canonical_registry_sha256(rows), CANONICAL_PIN)

    def test_complete_capture_passes_generic_reader_and_reconcile(self):
        with fixture_dir() as root, fixture_dir() as root2:
            build = write_capture(root, 'SR1', SR1_ROWS)
            scan = load_capture(root, active_build=build, realm='SR1')
            self.assertEqual(scan['completeness'], 'COMPLETE')
            self.assertEqual(len(scan['definitions']), 3)
            snap = snapshot_from_scan(scan)
            self.assertEqual(snap['traversal']['completeness'], 'COMPLETE')
            b2 = write_capture(root2, 'SR2', [_row('SR2:def:9', None, 'SlimeDefinition.Pink',
                                                   plorts=['IdentifiableType.PinkPlort'])], b'sr2-build')
            snap2 = snapshot_from_scan(load_capture(root2, active_build=b2, realm='SR2'))
            self.assertEqual(snap2['traversal']['completeness'], 'COMPLETE')
            self.assertIsInstance(classify_species(snap, snap2), dict)

    def test_actual_csharp_serializer_passes_real_reader(self):
        with fixture_dir() as root:
            build = root / 'active-build.bin'
            build.write_bytes(b'synthetic-csharp-build')
            project = Path(__file__).resolve().parent / 'RegistryCaptureHarness/RegistryCaptureHarness.csproj'
            result = subprocess.run(['dotnet', 'run', '--project', str(project), '-c', 'Release',
                                     '--', str(root), str(build)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            scan = load_capture(root, active_build=build, realm='SR1')
            self.assertEqual(scan['completeness'], 'COMPLETE')
            self.assertEqual(scan['traversal_proof']['registry_sha256'], CANONICAL_PIN)

    def test_actual_unicode_csharp_capture_passes_reader(self):
        with fixture_dir() as root:
            build = root / "active-build.bin"
            build.write_bytes(b"synthetic-unicode-build")
            project = Path(__file__).resolve().parent / "RegistryCaptureHarness/RegistryCaptureHarness.csproj"
            result = subprocess.run(["dotnet", "run", "--project", str(project), "-c", "Release",
                                     "--", str(root), str(build), "unicode"],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            scan = load_capture(root, active_build=build, realm="SR1")
            self.assertEqual(scan["completeness"], "COMPLETE")
            self.assertEqual(len(scan["definitions"]), 2)

    def test_counts_require_both_real_native_channels(self):
        for counts in ({'slimes_array': 3}, {'invented': 3}):
            with self.subTest(counts=counts), fixture_dir() as root:
                def tamper(snapshot, receipt):
                    snapshot['collector']['counts'] = dict(counts)
                    receipt['counts'] = dict(counts)
                build = write_capture(root, 'SR1', SR1_ROWS, tamper=tamper)
                with self.assertRaises(CaptureError):
                    load_capture(root, active_build=build, realm='SR1')

    def test_largo_cannot_parent_itself_or_another_largo(self):
        variants = [
            [_row('R:a', 1, 'A'), _row('R:l', 2, 'L', largo=True, bases=['A', 'L'])],
            [_row('R:a', 1, 'A'), _row('R:b', 2, 'B'),
             _row('R:l', 3, 'L', largo=True, bases=['A', 'B']),
             _row('R:x', 4, 'X', largo=True, bases=['A', 'L'])]]
        for rows in variants:
            with self.subTest(rows=rows), fixture_dir() as root:
                build = write_capture(root, 'SR1', rows)
                with self.assertRaises(CaptureError):
                    load_capture(root, active_build=build, realm='SR1')

    def test_canonical_order_uses_utf16_ordinal_units(self):
        bmp = _row('R:\ue000', 1, 'A')
        supplementary = _row('R:😀', 2, 'B')
        # UTF-16 surrogate D83D precedes E000, opposite Python code-point sorting.
        lines = [json.dumps([r[k] for k in ('object_ref', 'native_id', 'reference_id', 'name',
                                            'is_largo', 'can_largofy', 'base_slimes',
                                            'produced_plorts', 'prefab_refs')],
                            separators=(',', ':'), ensure_ascii=True) + '\n'
                 for r in (supplementary, bmp)]
        self.assertEqual(canonical_registry_sha256([bmp, supplementary]),
                         hashlib.sha256(''.join(lines).encode()).hexdigest())

    def test_old_run_cannot_be_reused(self):
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1', expected_run='new-run')

    def test_independent_count_cannot_disagree_with_complete_array(self):
        def tamper(snapshot, receipt):
            snapshot['collector']['counts']['by_identifiable'] = 9
            receipt['counts']['by_identifiable'] = 9
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, tamper=tamper)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_wrong_build_rejected(self):
        with fixture_dir() as root:
            write_capture(root, 'SR1', SR1_ROWS)
            other = root / 'other.bin'
            other.write_bytes(b'different')
            with self.assertRaises((CaptureError, ValueError)):
                load_capture(root, active_build=other, realm='SR1')

    def test_wrong_realm_rejected(self):
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR2')

    def test_registry_hash_must_match_rows(self):
        def tamper(snapshot, receipt):
            snapshot['definitions'][0]['produced_plorts'] = ['FORGED']
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, tamper=tamper)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_receipt_object_refs_must_match(self):
        def tamper(snapshot, receipt):
            receipt['object_refs'] = receipt['object_refs'][:-1] + ['SR1:def:forged']
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, tamper=tamper)
            with self.assertRaises((CaptureError, ValueError)):
                load_capture(root, active_build=build, realm='SR1')

    def test_receipt_run_id_must_match(self):
        def tamper(snapshot, receipt):
            receipt['run_id'] = 'other-run'
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, tamper=tamper)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_count_mismatch_is_not_complete(self):
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, expected=4)
            scan = load_capture(root, active_build=build, realm='SR1')
            self.assertNotEqual(scan['completeness'], 'COMPLETE')

    def test_self_reported_complete_with_count_mismatch_rejected(self):
        def tamper(snapshot, receipt):
            snapshot['completeness'] = 'COMPLETE'
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', SR1_ROWS, expected=4, tamper=tamper)
            with self.assertRaises((CaptureError, ValueError)):
                load_capture(root, active_build=build, realm='SR1')

    def test_duplicate_native_id_rejected(self):
        rows = [_row('SR1:def:1', 1, 'A'), _row('SR1:def:2', 1, 'B')]
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', rows)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_duplicate_reference_id_rejected(self):
        rows = [_row('SR1:def:1', 1, 'A'), _row('SR1:def:2', 2, 'A')]
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', rows)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_largo_base_must_resolve_in_registry(self):
        rows = [_row('SR1:def:1', 1, 'A'),
                _row('SR1:def:3', 3, 'L', largo=True, bases=['A', 'MISSING'])]
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', rows)
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')

    def test_empty_plort_reference_rejected(self):
        rows = [_row('SR1:def:1', 1, 'A', plorts=[''])]
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', rows)
            with self.assertRaises((CaptureError, ValueError)):
                load_capture(root, active_build=build, realm='SR1')

    def test_empty_registry_never_complete(self):
        with fixture_dir() as root:
            build = write_capture(root, 'SR1', [])
            with self.assertRaises(CaptureError):
                load_capture(root, active_build=build, realm='SR1')


CANONICAL_PIN = 'b4bedd8730b600277286c349d713adc0b3dbae8f78a6187d9e4fc8dc271299b1'


if __name__ == '__main__':
    unittest.main()
