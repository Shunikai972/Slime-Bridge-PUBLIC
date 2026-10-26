import json
import hashlib
import os
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from bridge.content.scan import read_runtime_snapshot, scan_sr1_export, scan_sr2_catalog, build_ir
from bridge.content.coverage import species_coverage
from tools.content.scan import _write


@contextmanager
def worktree_fixture():
    root = Path.cwd() / 'Lab' / 'GeneratedContent' / ('scan-test-' + uuid.uuid4().hex)
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root)


class ContentScanTests(unittest.TestCase):
    def test_runtime_snapshot_preserves_native_relationships_and_edges(self):
        with worktree_fixture() as root:
            path = root / 'snapshot.json'
            build = root / 'active-build.dll'
            build.write_bytes(b'fixture-build')
            digest = hashlib.sha256(build.read_bytes()).hexdigest()
            path.write_text(json.dumps({
                'schema_version': 1, 'realm': 'SR2', 'game_build': 'test',
                'completeness': 'COMPLETE', 'registry_traversed': True,
                'build_match': 'VERIFIED',
                'build_proof': {'active_sha256': digest, 'scanned_sha256': digest,
                                'active_path': str(build), 'provenance': 'fixture'},
                'traversal_proof': {'registry_identity': 'LookupDirector.slimes',
                    'registry_sha256': 'b' * 64, 'expected_count': 1, 'observed_count': 1},
                'definitions': [{'canonical_id': 'slime.pink', 'canonical_evidence': ['native mapping'],
                    'native_id': 3, 'reference_id': 'SlimeDefinition.Pink', 'object_ref': 'pink-def',
                    'name': 'Pink', 'is_largo': False, 'can_largofy': True,
                    'base_slimes': [], 'produced_plorts': ['IdentifiableType.PinkPlort'],
                    'prefab_refs': ['pink-prefab'], 'evidence': ['registry traversal'],
                    'materials': ['body-material'], 'asset_hashes': {'body-material': 'sha256:test'},
                    'asset_dependencies': [{'source_field': 'appearances[0].material',
                        'target_ref': 'body-material', 'target_kind': 'material',
                        'provenance': 'native serialized field'}]}],
                'assets': [{'object_ref': 'body-material', 'kind': 'material', 'sha256': 'c' * 64,
                    'dependencies': [{'source_field': 'shader', 'target_ref': 'body-shader',
                        'target_kind': 'shader', 'provenance': 'native serialized field'},
                        {'source_field': 'textures[0]', 'target_ref': 'body-texture',
                        'target_kind': 'texture', 'provenance': 'native serialized field'}]}],
            }), encoding='utf-8')
            scan = read_runtime_snapshot(path, active_build=build, traversal_anchor={
                'realm': 'SR2', 'active_sha256': digest, 'registry_identity': 'LookupDirector.slimes',
                'registry_sha256': 'b' * 64, 'object_refs': ['pink-def']})
            row = scan['definitions'][0]
            self.assertEqual((row['native_id'], row['reference_id']), (3, 'SlimeDefinition.Pink'))
            self.assertEqual(row['produced_plorts'], ['IdentifiableType.PinkPlort'])
            self.assertEqual(row['content']['materials'], ['body-material'])
            self.assertEqual(scan['completeness'], 'COMPLETE')
            ir = build_ir([scan])
            self.assertIn({'from': 'SR2:pink-def', 'relation': 'produces_plort',
                           'to': 'SR2:IdentifiableType.PinkPlort', 'resolved': False}, ir['edges'])
            self.assertIn({'from': 'SR2:pink-def', 'relation': 'appearances[0].material',
                           'to': 'SR2:body-material', 'resolved': True,
                           'target_kind': 'material', 'provenance': 'native serialized field'}, ir['edges'])
            self.assertTrue(any(e['from'] == 'SR2:body-material' and e['to'] == 'SR2:body-shader'
                                and not e['resolved'] for e in ir['edges']))
            self.assertTrue(any(n['id'] == 'SR2:body-shader' and n['kind'] == 'shader'
                                and n['unresolved'] for n in ir['nodes']))

    def test_complete_requires_explicit_registry_traversal_and_resolved_fields(self):
        with worktree_fixture() as root:
            path = root / 'snapshot.json'
            path.write_text(json.dumps({'schema_version': 1, 'realm': 'SR1',
                'completeness': 'COMPLETE', 'definitions': []}), encoding='utf-8')
            with self.assertRaises(ValueError):
                read_runtime_snapshot(path)

    def test_complete_rejects_unchecked_build_and_registry_count(self):
        with worktree_fixture() as root:
            path = root / 'snapshot.json'
            data = {'schema_version': 1, 'realm': 'SR1', 'game_build': 'label',
                    'build_match': 'VERIFIED', 'registry_traversed': True,
                    'completeness': 'COMPLETE', 'definitions': [],
                    'build_proof': {'active_sha256': 'a' * 64, 'scanned_sha256': 'b' * 64,
                                    'active_path': 'Lab/Games/SR1', 'provenance': 'fixture'},
                    'traversal_proof': {'registry_identity': 'registry',
                        'registry_sha256': 'c' * 64, 'expected_count': 0, 'observed_count': 0}}
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                read_runtime_snapshot(path)
            data['build_proof']['scanned_sha256'] = 'a' * 64
            data['traversal_proof']['expected_count'] = 1
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.assertRaises(ValueError):
                read_runtime_snapshot(path)

    def test_complete_requires_independent_active_build(self):
        with worktree_fixture() as root:
            path = root / 'snapshot.json'
            digest = hashlib.sha256(b'fake').hexdigest()
            data = {'schema_version': 1, 'realm': 'SR1', 'game_build': 'label',
                    'build_match': 'VERIFIED', 'registry_traversed': True,
                    'completeness': 'COMPLETE', 'definitions': [],
                    'build_proof': {'active_sha256': digest, 'scanned_sha256': digest,
                                    'active_path': 'untrusted path', 'provenance': 'self-report'},
                    'traversal_proof': {'registry_identity': 'registry', 'registry_sha256': digest,
                                       'expected_count': 0, 'observed_count': 0}}
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                read_runtime_snapshot(path)

    def test_exclusivity_rejects_self_reported_complete_without_build_proof(self):
        base = {'canonical_id': 'slime.a', 'canonical_proven': True, 'is_largo': False}
        one = {'realm': 'SR1', 'completeness': 'COMPLETE', 'registry_traversed': True,
               'build_match': 'UNVERIFIED', 'definitions': [base]}
        two = {**one, 'realm': 'SR2', 'definitions': []}
        result = species_coverage(one, two)
        self.assertFalse(result['exclusive_classification_complete'])
        self.assertEqual(result['confirmed_sr1_only'], [])

    def test_content_reference_is_not_lost_when_explicit_edges_are_missing(self):
        from bridge.content.scan import _definition
        scan = {'realm': 'SR1', 'errors': [], 'definitions': [
            _definition(name='A', object_ref='a', is_largo=False,
                        content={'materials': ['body'], 'meshes': ['mesh']})],
            'assets': [{'object_ref': 'body', 'kind': 'material', 'sha256': 'a' * 64,
                        'dependencies': [{'source_field': 'textures[0]', 'target_ref': 'texture',
                            'target_kind': 'texture', 'provenance': 'serialized field'}]}]}
        ir = build_ir([scan])
        self.assertTrue(any(e['from'] == 'SR1:a' and e['to'] == 'SR1:body' for e in ir['edges']))
        self.assertTrue(any(e['from'] == 'SR1:a' and e['to'] == 'SR1:mesh'
                            and not e['resolved'] for e in ir['edges']))
        self.assertTrue(any(n['id'] == 'SR1:mesh' and n['unresolved'] for n in ir['nodes']))

    def test_native_prefab_edge_resolves_and_asset_cycle_terminates(self):
        from bridge.content.scan import _definition
        scan = {'realm': 'SR1', 'errors': [], 'definitions': [
            _definition(name='A', object_ref='a', is_largo=False, prefab_refs=['actor'])],
            'assets': [{'object_ref': 'actor', 'kind': 'prefab', 'sha256': 'a' * 64,
                        'dependencies': [{'source_field': 'self', 'target_ref': 'actor',
                            'target_kind': 'prefab', 'provenance': 'serialized field'}]}]}
        ir = build_ir([scan])
        self.assertEqual(len({n['id'] for n in ir['nodes']}), len(ir['nodes']))
        self.assertTrue(any(e['relation'] == 'has_prefab' and e['resolved'] for e in ir['edges']))

    def test_failed_publish_cleans_its_exclusive_temp_file(self):
        from unittest.mock import patch
        with worktree_fixture() as root:
            output = root / 'output.json'
            with patch('tools.content.scan.os.replace', side_effect=OSError('injected publish failure')):
                with self.assertRaises(OSError):
                    _write(output, {'value': 1})
            self.assertEqual(list(root.iterdir()), [])

    def test_scanner_does_not_emit_largo_modeling_backlog(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from tools.content.scan import main
        with worktree_fixture() as root:
            first = root / 'sr1.json'
            second = root / 'sr2.json'
            for path, realm in ((first, 'SR1'), (second, 'SR2')):
                path.write_text(json.dumps({'schema_version': 1, 'realm': realm,
                    'completeness': 'PARTIAL', 'definitions': [], 'errors': []}))
            from unittest.mock import patch
            from bridge.content.scan import read_runtime_snapshot
            anchor = root / 'fixture-build.bin'
            anchor.write_bytes(b'unit fixture only; not a game build')
            def read_fixture(path, **kwargs):
                return read_runtime_snapshot(path, active_build=anchor)
            with redirect_stdout(StringIO()), patch('tools.content.scan.read_runtime_snapshot',
                                                    side_effect=read_fixture):
                main(['--sr1-runtime', str(first), '--sr2-runtime', str(second),
                      '--out-dir', str(root / 'out')])
            self.assertEqual({p.name for p in (root / 'out').iterdir()},
                             {'sr1-content-manifest.json', 'sr2-content-manifest.json',
                              'bridge-content-manifest.json'})

    def test_unmapped_base_blocks_exclusive_claim(self):
        from test_largo_coverage import snapshot, definition
        one = snapshot('SR1', [definition('a', 'SR1')])
        two = snapshot('SR2', [definition('unknown', 'SR2')])
        two['definitions'][0].update(canonical_id=None, canonical_proven=True)
        result = species_coverage(one, two)
        self.assertFalse(result['exclusive_classification_complete'])
        self.assertEqual(result['confirmed_sr1_only'], [])

    def test_truncated_registry_requires_independent_traversal_receipt(self):
        from test_largo_coverage import snapshot, definition
        from bridge.content.scan import checked_runtime_proof
        scan = snapshot('SR1', [definition('a', 'SR1')])
        scan['definitions'] = []
        scan['traversal_proof'].update(expected_count=0, observed_count=0)
        self.assertFalse(checked_runtime_proof(scan))

    def test_nested_content_and_wrong_typed_target_are_not_hidden(self):
        from bridge.content.scan import _definition
        scan = {'realm': 'SR1', 'errors': [], 'definitions': [
            _definition(name='A', object_ref='a', is_largo=False,
                content={'model_manifest': {'mesh': {'details': {'object_ref': 'mesh'}}},
                         'materials': ['shader']}, prefab_refs=['shader'])],
                'assets': [{'object_ref': 'shader', 'kind': 'shader', 'sha256': 'a' * 64}]}
        ir = build_ir([scan])
        self.assertTrue(any(e['to'] == 'SR1:mesh' and e['target_kind'] == 'mesh' for e in ir['edges']))
        self.assertTrue(any(e['relation'] == 'has_prefab' and not e['resolved'] for e in ir['edges']))
        self.assertTrue(any(e['to'] == 'SR1:shader' and e.get('target_kind') == 'material'
                            and not e['resolved'] for e in ir['edges']))

    def test_atomic_write_refuses_existing_hardlink(self):
        with worktree_fixture() as root:
            original = root / 'original.json'
            output = root / 'output.json'
            original.write_bytes(b'original bytes')
            os.link(original, output)
            with self.assertRaises(ValueError):
                _write(output, {'new': 1})
            self.assertEqual(original.read_bytes(), b'original bytes')
            self.assertEqual(output.read_text(), original.read_text())

    def test_asset_readers_are_partial_and_do_not_infer_largo_parents_from_names(self):
        with worktree_fixture() as root:
            mono = root / 'MonoBehaviour'
            mono.mkdir()
            (mono / 'MainSlimeDefinitions.asset').write_text('  Slimes:\n  - {fileID: 11400000, guid: aaaa, type: 2}\n  - {fileID: 11400000, guid: bbbb, type: 2}\n')
            (mono / 'Pink.asset.meta').write_text('guid: aaaa\n')
            (mono / 'Pink.asset').write_text('  m_Name: Pink\n  IdentifiableId: 3\n  BaseSlimes: []\n  IsLargo: 0\n  CanLargofy: 1\n  AppearancesDefault:\n  - {fileID: 11400000, guid: cccc, type: 2}\n  Diet:\n    Produces: 06000000\n')
            (mono / 'PinkRock.asset.meta').write_text('guid: bbbb\n')
            (mono / 'PinkRock.asset').write_text('  m_Name: PinkRock\n  IdentifiableId: 9\n  BaseSlimes:\n  - {fileID: 11400000, guid: aaaa, type: 2}\n  - {fileID: 11400000, guid: dddd, type: 2}\n  IsLargo: 1\n  CanLargofy: 0\n')
            (mono / 'PinkAppearance.asset.meta').write_text('guid: cccc\n')
            (mono / 'PinkAppearance.asset').write_text('  m_Name: PinkAppearance\n')
            sr1 = scan_sr1_export(root, game_build='export-build')
            self.assertEqual(sr1['completeness'], 'PARTIAL')
            self.assertEqual(len(sr1['definitions']), 2)
            self.assertEqual(sr1['definitions'][0]['produced_plorts'], [6])
            self.assertTrue(any(e['code'] == 'UNRESOLVED_BASE' for e in sr1['errors']))
            self.assertFalse(sr1['definitions'][1]['canonical_proven'])
            self.assertTrue(any(e['relation'] == 'has_appearance' and e['resolved']
                                for e in build_ir([sr1])['edges']))
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'m_BuildResultHash': 'hash', 'm_InternalIds': [
                'Assets/Actor/Slime/Base/Definition/Cotton.asset',
                'Assets/Actor/Slime/Largo/Definition/CottonPink.asset']}))
            sr2 = scan_sr2_catalog(catalog)
            self.assertEqual(sr2['completeness'], 'PARTIAL')
            self.assertIsNone(sr2['definitions'][0]['can_largofy'])
            self.assertEqual(sr2['definitions'][1]['base_slimes'], [])

    def test_exclusive_classification_waits_for_complete_proven_mappings(self):
        from test_largo_coverage import snapshot, definition
        base = definition('pink', 'SR1')
        sr1 = snapshot('SR1', [base])
        sr2 = snapshot('SR2', [], complete=False)
        self.assertEqual(species_coverage(sr1, sr2)['confirmed_sr1_only'], [])
        sr2.update(completeness='COMPLETE', registry_traversed=True)
        self.assertEqual(species_coverage(sr1, sr2)['confirmed_sr1_only'], ['slime.pink'])
        sr2['definitions'] = [{**base, 'canonical_proven': False}]
        self.assertEqual(species_coverage(sr1, sr2)['confirmed_sr1_only'], [])


if __name__ == '__main__':
    unittest.main()
