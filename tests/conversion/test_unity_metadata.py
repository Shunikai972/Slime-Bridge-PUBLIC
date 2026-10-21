"""Only synthetic metadata fixtures; no game, model or runtime operations."""
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

from bridge.content.discovery import discover
from bridge.conversion.manifest import ManifestSource
from bridge.conversion.providers import AssetDependencyProvider

WORKTREE = Path(__file__).resolve().parents[2]
A, B, C = 'a' * 32, 'b' * 32, 'c' * 32
HEADER = '%YAML 1.1\n%TAG !u! tag:unity3d.com,2011:\n'


def ref(guid, file_id):
    return f'unity:{guid}:{file_id}'


class UnityMetadataTests(unittest.TestCase):
    def setUp(self):
        parent = WORKTREE / 'Lab' / 'UnityMetadataTests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'Assets'
        self.root.mkdir()

    def asset(self, name, guid, body):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body.encode() if isinstance(body, str) else body)
        path.with_name(path.name + '.meta').write_text(f'fileFormatVersion: 2\nguid: {guid}\n', encoding='utf-8')
        return path

    def read(self, roots=None, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('bridge.conversion.unity_metadata'),
                             'metadata-only adapter is not implemented')
        from bridge.conversion.unity_metadata import read_unity_metadata
        return read_unity_metadata(self.root, realm='SR1', roots=roots or {'Fixture.Root': ref(A, 1)},
                                   **kwargs)

    def test_forward_local_external_transitive_cycle_uses_manifest_boundary(self):
        path = self.asset('root.asset', A, HEADER +
                          '--- !u!114 &1\nMonoBehaviour:\n  links:\n'
                          '  - {fileID: 2}\n  - {fileID: 30, guid: ' + B + ', type: 2}\n'
                          '--- !u!4 &2\nTransform:\n  parent: {fileID: 1}\n')
        self.asset('clip.anim', B, HEADER +
                   '--- !u!74 &30\nAnimationClip:\n  arbitrary: {fileID: 99, guid: ' + C + ', type: 2}\n')
        self.asset('audio.asset', C, HEADER +
                   '--- !u!83 &99\nAudioClip:\n  self: {fileID: 99}\n')
        data = self.read(synthetic=True)
        provider = ManifestSource(data)
        self.assertIsInstance(provider, AssetDependencyProvider)
        self.assertEqual(provider.definition_root({'native_ref': 'Fixture.Root'}), ref(A, 1))
        result = discover(provider, [ref(A, 1)])
        self.assertFalse(result['truncated'])
        self.assertEqual(result['inspected'], 4)
        graph = result['graph'].to_json()
        self.assertEqual(len(graph['edges']), 5)
        self.assertTrue(all(n['resolution'] == 'RESOLVED' for n in graph['nodes']))
        self.assertFalse(any(d['code'] == 'MALFORMED_PROVIDER_OUTPUT' for d in result['diagnostics']))
        identity = provider.inspect(ref(A, 1))['attributes']['unity']
        self.assertEqual((identity['guid'], identity['file_id'], identity['class_id'],
                          identity['serialized_type']), (A, '1', '114', 'MonoBehaviour'))
        self.assertEqual(identity['source_sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(data['data_evidence'], 'SYNTHETIC')
        self.assertEqual(data['extensions']['unity_metadata']['export_completeness'], 'PARTIAL')
        self.assertEqual(data['extensions']['unity_metadata']['build_verification'], 'UNVERIFIED')
        self.assertEqual(data['registry']['traversal']['completeness'], 'PARTIAL')
        self.assertEqual(data['registry']['entries'], [])
        self.assertTrue(provider.build_fingerprint().startswith('UNVERIFIED:'))

    def test_null_is_not_missing_nonnull_references_remain_unresolved(self):
        builtin = '0000000000000000e000000000000000'
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  null: {fileID: 0}\n  missingLocal: {fileID: 55}\n'
                   '  missingGuid: {fileID: 6, guid: ' + B + ', type: 2}\n'
                   '  reserved: {fileID: 10302, guid: ' + builtin + ', type: 0}\n')
        data = self.read()
        self.assertIn('unity_references', data['objects'][0]['attributes'])
        records = data['objects'][0]['attributes']['unity_references']
        self.assertEqual([r['status'] for r in records],
                         ['NULL', 'MISSING_FILEID', 'MISSING_GUID', 'BUILTIN_OR_RESERVED_UNVERIFIED'])
        self.assertIsNone(records[0]['target'])
        self.assertEqual(len(data['objects'][0]['references']), 3)
        self.assertEqual(len(data['extensions']['unity_metadata']['unresolved_references']), 3)
        graph = discover(ManifestSource(data), [ref(A, 1)])['graph'].to_json()
        self.assertEqual(sum(n['resolution'] == 'UNRESOLVED' for n in graph['nodes']), 3)
        self.assertNotIn(ref(A, 0), {n['ref'] for n in graph['nodes']})
        self.assertEqual(data['data_evidence'], 'STATIC')
        self.assertFalse(data['synthetic'])

    def test_duplicate_guids_fail_closed_instead_of_selecting_a_file(self):
        self.asset('root.asset', A, HEADER + '--- !u!114 &1\nMonoBehaviour:\n')
        self.asset('other.asset', A, HEADER + '--- !u!114 &1\nMonoBehaviour:\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate Unity GUID'):
            self.read()

    def test_duplicate_file_ids_fail_closed_instead_of_overwriting_an_object(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n--- !u!4 &1\nTransform:\n')
        with self.assertRaisesRegex(ValueError, 'Duplicate Unity fileID'):
            self.read()

    def test_opaque_targets_preserve_unverified_metadata_without_descriptor_bytes(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  dependency: {fileID: 77, guid: ' + B + ', type: 3}\n')
        binary = self.asset('native.bin', B, b'\x00\xffSYNTHETIC-OPAQUE')
        before = {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.root.rglob('*') if p.is_file()}
        data = self.read()
        self.assertEqual(len(data['objects']), 1)
        self.assertEqual(data['extensions']['unity_metadata']['unresolved_references'][0]['status'],
                         'OPAQUE_ASSET')
        observations = data['extensions']['unity_metadata']['asset_observations']
        self.assertEqual(observations[B]['source_sha256'], hashlib.sha256(binary.read_bytes()).hexdigest())
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                                  for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(data, self.read())
        self.assertIsNone(ManifestSource(data).inspect(ref(B, 77)))
        self.assertTrue(data['extensions']['unity_metadata']['read_only'])

    def test_block_flow_multiline_and_negative_references_preserve_unsupported_tokens(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  block:\n    fileID: -20\n    guid: ' + B + '\n    type: 2\n'
                   '  multiline: {guid: ' + B + ',\n    type: 2, fileID: -20}\n'
                   '  invalid: {fileID: 0002, guid: ' + B + ', type: 2}\n'
                   '  invalidGuid: {fileID: 2, guid: ../escape, type: 2}\n'
                   '  tagged: {fileID: !!python/object/apply:os.system [never-run]}\n')
        self.asset('other.asset', B, HEADER + '--- !u!114 &-20 stripped\nMonoBehaviour:\n')
        data = self.read()
        records = data['objects'][0]['attributes']['unity_references']
        self.assertEqual([r['status'] for r in records],
                         ['RESOLVED', 'RESOLVED', 'UNSUPPORTED_REFERENCE',
                          'UNSUPPORTED_REFERENCE', 'UNSUPPORTED_REFERENCE'])
        self.assertEqual(records[2]['file_id'], '0002')
        self.assertEqual(records[3]['guid'], '../escape')
        self.assertTrue(all(r['target'].startswith('unity-unsupported:') for r in records[2:]))
        graph = discover(ManifestSource(data), [ref(A, 1)])['graph'].to_json()
        self.assertEqual(len(graph['edges']), 5)
        self.assertEqual(sum(n['resolution'] == 'UNRESOLVED' for n in graph['nodes']), 3)
        self.assertTrue(ManifestSource(data).inspect(ref(B, -20))['attributes']['unity']['stripped'])

    def test_quoted_comments_and_block_scalar_content_are_not_reference_edges(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  quoted: "{fileID: 91}"\n  single: \'{fileID: 92}\'\n'
                   '  text: |\n    {fileID: 93}\n    fileID: 94\n'
                   '  folded: >-\n    {fileID: 95}\n'
                   '  # ignored: {fileID: 96}\n'
                   '  actual: {fileID: 2} # {fileID: 97}\n'
                   '--- !u!4 &2\nTransform:\n')
        data = self.read()
        root = ManifestSource(data).inspect(ref(A, 1))
        self.assertEqual([r['target'] for r in root['references']], [ref(A, 2)])
        self.assertEqual(root['attributes']['unity_references'][0]['status'], 'RESOLVED')

    def test_rejects_nonexplicit_or_malformed_roots_without_normalizing_tokens(self):
        from bridge.conversion.unity_metadata import read_unity_metadata
        invalid = [None, {}, {'': ref(A, 1)}, {'root': 'unity:' + A + ':01'},
                   {'root': ref(A.upper(), 1)}, {'root': ref(A, 0)}, {'root': 'auto'}]
        for roots in invalid:
            with self.subTest(roots=roots), self.assertRaises(ValueError):
                read_unity_metadata(self.root, realm='SR1', roots=roots)
        with self.assertRaises(ValueError):
            read_unity_metadata(self.root, realm='OTHER', roots={'root': ref(A, 1)})
        with self.assertRaises(ValueError):
            read_unity_metadata(self.root, realm='SR1', roots={'root': ref(A, 1)}, synthetic='yes')

    def test_bounded_reads_do_not_claim_complete_closure(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n  next: {fileID: 2}\n'
                   '--- !u!4 &2\nTransform:\n')
        self.asset('other.asset', B, HEADER + '--- !u!114 &1\nMonoBehaviour:\n')
        with self.assertRaisesRegex(ValueError, 'metadata index limit'):
            self.read(max_meta_files=1)
        with self.assertRaisesRegex(ValueError, 'metadata node limit'):
            self.read(max_nodes=1)
        data = self.read(max_file_bytes=80)
        self.assertEqual(data['objects'], [])
        self.assertEqual(data['extensions']['unity_metadata']['root_observations']['Fixture.Root']['status'],
                         'FILE_SIZE_LIMIT')
        self.assertIsNone(data['extensions']['unity_metadata']['asset_observations'][A]['source_sha256'])
        self.assertEqual(data['extensions']['unity_metadata']['export_completeness'], 'PARTIAL')
        with self.assertRaises(ValueError):
            self.read(max_nodes=0)

    def test_directory_links_are_not_traversed_or_read_outside_the_explicit_root(self):
        import os
        import subprocess
        from unittest.mock import patch
        outside = Path(self.temp.name) / 'Outside'
        outside.mkdir()
        (outside / 'target.asset').write_text(HEADER + '--- !u!114 &5\nMonoBehaviour:\n', encoding='utf-8')
        (outside / 'target.asset.meta').write_text('guid: ' + B + '\n', encoding='utf-8')
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n  target: {fileID: 5, guid: ' + B + ', type: 2}\n')
        link = self.root / 'linked.asset'
        if os.name == 'nt':
            made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
            self.assertEqual(made.returncode, 0, made.stderr)
        else:
            link.symlink_to(outside, target_is_directory=True)
        link.with_name(link.name + '.meta').write_text('guid: ' + B + '\n', encoding='utf-8')
        original = Path.open
        def guarded_open(path, *args, **kwargs):
            self.assertTrue(path.resolve().is_relative_to(self.root.resolve()), 'read escaped explicit root')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'open', guarded_open):
            data = self.read()
        self.assertEqual(len(data['extensions']['unity_metadata']['guid_index']), 2)
        self.assertEqual(data['extensions']['unity_metadata']['unresolved_references'][0]['status'],
                         'OUTSIDE_ROOT_OR_LINK')
        self.assertTrue(any(d['code'] == 'UNITY_METADATA_LINK_SKIPPED'
                            for d in data['extensions']['unity_metadata']['diagnostics']))

    def test_plain_and_multiline_plain_strings_do_not_become_edges(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  m_Name: Display {fileID: 2}\n'
                   '  description: Long plain text\n    {fileID: 2}\n'
                   '  actual: {fileID: 2}\n--- !u!4 &2\nTransform:\n')
        self.assertEqual(len(ManifestSource(self.read()).inspect(ref(A, 1))['references']), 1)

    def test_block_pointer_mapping_boundaries_cannot_drop_or_choose_tokens(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  external:\n    fileID: 2\n\n    guid: ' + B + '\n    type: 2\n'
                   '  unknown:\n    fileID: 2\n    extra: no\n    guid: ' + B + '\n'
                   '  duplicateGuid:\n    fileID: 2\n    guid: ' + B + '\n    guid: ' + C + '\n'
                   '  duplicateId:\n    fileID: 2\n    fileID: 3\n'
                   '--- !u!4 &2\nTransform:\n')
        self.asset('external.asset', B, HEADER + '--- !u!4 &2\nTransform:\n')
        records = ManifestSource(self.read()).inspect(ref(A, 1))['attributes']['unity_references']
        self.assertEqual([r['status'] for r in records],
                         ['RESOLVED', 'UNSUPPORTED_REFERENCE', 'UNSUPPORTED_REFERENCE', 'UNSUPPORTED_REFERENCE'])
        self.assertEqual(records[0]['target'], ref(B, 2))

    def test_indexed_reserved_guids_never_resolve_into_ordinary_descriptors(self):
        builtin = '0000000000000000e000000000000000'
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n  x: {fileID: 5, guid: ' + builtin + ', type: 0}\n')
        self.asset('builtin.asset', builtin, HEADER + '--- !u!21 &5\nMaterial:\n')
        data = self.read()
        self.assertIsNone(ManifestSource(data).inspect(ref(builtin, 5)))
        graph = discover(ManifestSource(data), [ref(A, 1)])['graph'].to_json()
        self.assertEqual(sum(n['resolution'] == 'UNRESOLVED' for n in graph['nodes']), 1)

    def test_unsupported_quoted_tokens_are_retained_and_have_distinct_identities(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n  a: {fileID: "0002"}\n  b: {fileID: "0003"}\n')
        records = ManifestSource(self.read()).inspect(ref(A, 1))['attributes']['unity_references']
        self.assertEqual([r['file_id'] for r in records], ['"0002"', '"0003"'])
        self.assertTrue(all(r['status'] == 'UNSUPPORTED_REFERENCE' for r in records))
        self.assertNotEqual(records[0]['token_sha256'], records[1]['token_sha256'])

    def test_ambiguous_guid_lines_in_one_sidecar_are_rejected(self):
        path = self.asset('root.asset', A, HEADER + '--- !u!114 &1\nMonoBehaviour:\n')
        path.with_name(path.name + '.meta').write_text('guid: ' + A + '\nguid: ' + B + '\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Ambiguous Unity GUID'):
            self.read()

    def test_object_parse_work_and_source_hashing_are_bounded(self):
        from unittest.mock import patch
        from bridge.conversion import unity_metadata
        body = HEADER + ''.join(f'--- !u!114 &{i}\nMonoBehaviour:\n' for i in range(1, 201))
        path = self.asset('root.asset', A, body)
        with self.assertRaisesRegex(ValueError, 'metadata node limit'):
            self.read(max_nodes=1)
        raw = path.read_bytes()
        original = unity_metadata._sha
        calls = []
        def counted_sha(value):
            if value == raw:
                calls.append(True)
            return original(value)
        with patch.object(unity_metadata, '_sha', counted_sha):
            data = self.read(max_nodes=300)
        self.assertEqual(len(data['objects']), 1)
        self.assertEqual(len(calls), 1, 'whole-file source SHA must be computed once, not once per object')

    def test_flow_sequences_include_every_local_and_explicit_same_guid_reference(self):
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n'
                   '  oneLine: [{fileID: 2}, {fileID: 3}]\n'
                   '  multipleLines: [{fileID: 2},\n    {fileID: 3, guid: ' + A + ', type: 2}]\n'
                   '--- !u!4 &2\nTransform:\n--- !u!4 &3\nTransform:\n')
        data = self.read()
        records = ManifestSource(data).inspect(ref(A, 1))['attributes']['unity_references']
        self.assertEqual([r['target'] for r in records], [ref(A, 2), ref(A, 3), ref(A, 2), ref(A, 3)])
        self.assertTrue(all(r['status'] == 'RESOLVED' for r in records))

    def test_oversized_unsupported_tokens_are_digest_only_not_embedded_payloads(self):
        payload = 'SYNTHETIC_OPAQUE_' * 100
        self.asset('root.asset', A, HEADER +
                   '--- !u!114 &1\nMonoBehaviour:\n  arbitrary: {fileID: ' + payload + '}\n')
        data = self.read()
        record = data['objects'][0]['attributes']['unity_references'][0]
        self.assertIsNone(record['file_id'])
        self.assertEqual(record['status'], 'UNSUPPORTED_REFERENCE')
        self.assertEqual(record['redacted_fields'], ['fileID'])
        self.assertNotIn(payload, str(data))


if __name__ == '__main__':
    unittest.main()
