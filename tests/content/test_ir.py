"""Canonical Bridge slime IR: strict, deterministic, migration-friendly, metadata-only."""
import copy
import unittest

from bridge.content import ir
from bridge.content.canonical import sha256_text

H = {name: sha256_text(name) for name in ('def', 'prefab', 'mesh', 'mat', 'tex', 'bone', 'face',
                                          'anim', 'clip', 'icon', 'plort', 'closure', 'vfx')}


def sample_ir(**overrides):
    doc = {
        'schema': ir.IR_SCHEMA, 'schema_version': ir.IR_SCHEMA_VERSION,
        'canonical_id': 'slime.sample', 'content_kind': 'slime', 'role': 'base',
        'identity': {'source_realm': 'SR2', 'source_build_fingerprint': 'fixture-build',
                     'native_definition': {'ref': 'Fixture.Sample', 'numeric_id': None, 'hash': H['def']},
                     'native_prefab': {'ref': 'prefab.sample', 'hash': H['prefab']},
                     'identity_basis': 'DERIVED', 'identity_evidence': ['slug-v1:Fixture.Sample']},
        'visual': {
            'root': 'SR2:prefab.sample',
            'hierarchy': [{'id': 'SR2:prefab.sample', 'parent': None, 'kind': 'prefab'},
                          {'id': 'SR2:bone.root', 'parent': 'SR2:prefab.sample', 'kind': 'transform'}],
            'meshes': [{'id': 'SR2:mesh.body', 'hash': H['mesh'], 'skinned': True, 'attributes': {'submeshes': 2}}],
            'renderers': [{'id': 'SR2:renderer.body', 'kind': 'skinned', 'node': 'SR2:prefab.sample',
                           'mesh': 'SR2:mesh.body', 'materials': ['SR2:mat.b', 'SR2:mat.a'],
                           'bones': ['SR2:bone.root'], 'root_bone': 'SR2:bone.root'}],
            'materials': [{'id': 'SR2:mat.a', 'hash': H['mat'], 'shader': None, 'semantic': None,
                           'status': 'EXPERIMENTAL'},
                          {'id': 'SR2:mat.b', 'hash': H['tex'], 'shader': None, 'semantic': None,
                           'status': 'EXPERIMENTAL'}],
            'textures': [], 'face': None, 'animator': None, 'animation_clips': [],
            'vfx': [], 'audio': [], 'unknown_references': []},
        'gameplay': {'diet': {'food_groups': ['fixture_fruit'], 'favourite_foods': [],
                              'produces': ['plort.sample']},
                     'capabilities': [{'id': 'standard.diet', 'policy': 'REQUIRED',
                                       'sources': [], 'evidence': 'registry diet'}],
                     'native_behaviours': []},
        'associated': {'plorts': [{'canonical_id': 'plort.sample', 'native_ref': 'Fixture.Plort.Sample',
                                   'hash': H['plort']}],
                       'icon': {'id': 'SR2:icon.sample', 'hash': H['icon']},
                       'market': None, 'pedia': None, 'localization': None, 'spawn': None},
        'provenance': {'source_refs': ['SR2:Fixture.Sample'],
                       'asset_hashes': {'SR2:mesh.body': H['mesh'], 'SR2:mat.a': H['mat'],
                                        'SR2:mat.b': H['tex'], 'SR2:icon.sample': H['icon']},
                       'dependency_closure_hash': H['closure'], 'scanner_schema_version': 1,
                       'converter_version': 'test', 'synthetic': True, 'data_evidence': 'SYNTHETIC'},
        'status': 'EXPERIMENTAL', 'status_reasons': ['NEEDS_LOCAL_RUNTIME_PROOF'],
    }
    doc.update(overrides)
    return doc


def codes(diagnostics):
    return {d['code'] for d in diagnostics}


class IRValidationTests(unittest.TestCase):
    def test_sample_is_valid_and_hash_ignores_key_and_set_order(self):
        doc = sample_ir()
        self.assertEqual(ir.validate_ir(doc), [])
        reordered = copy.deepcopy(doc)
        reordered['visual']['materials'].reverse()
        reordered['visual']['hierarchy'].reverse()
        reordered = dict(reversed(list(reordered.items())))
        self.assertEqual(ir.ir_hash(doc), ir.ir_hash(reordered))

    def test_material_slot_order_is_semantic_and_never_sorted(self):
        doc = sample_ir()
        swapped = copy.deepcopy(doc)
        swapped['visual']['renderers'][0]['materials'].reverse()
        self.assertEqual(ir.canonicalize_ir(doc)['visual']['renderers'][0]['materials'],
                         ['SR2:mat.b', 'SR2:mat.a'])
        self.assertNotEqual(ir.ir_hash(doc), ir.ir_hash(swapped))

    def test_unknown_fields_rejected_but_namespaced_extensions_preserved(self):
        self.assertIn('UNKNOWN_FIELD', codes(ir.validate_ir(sample_ir(colour='pink'))))
        nested = sample_ir()
        nested['visual']['lod_groups'] = []
        self.assertIn('UNKNOWN_FIELD', codes(ir.validate_ir(nested)))
        self.assertEqual(ir.validate_ir(sample_ir(extensions={'local.sr1_hint': {'a': 1}})), [])
        self.assertIn('INVALID_EXTENSION_KEY', codes(ir.validate_ir(sample_ir(extensions={'hint': 1}))))

    def test_future_and_foreign_schema_rejected_explicitly(self):
        with self.assertRaises(ir.IRSchemaError) as caught:
            ir.load_ir(sample_ir(schema_version=ir.IR_SCHEMA_VERSION + 1))
        self.assertEqual(caught.exception.code, 'SCHEMA_TOO_NEW')
        with self.assertRaises(ir.IRSchemaError) as caught:
            ir.load_ir(sample_ir(schema='cloud-fixture-v1'))
        self.assertEqual(caught.exception.code, 'WRONG_SCHEMA')
        with self.assertRaises(ir.IRSchemaError) as caught:
            ir.load_ir(sample_ir(schema_version=True))
        self.assertEqual(caught.exception.code, 'WRONG_SCHEMA')

    def test_migration_chain_is_explicit_and_does_not_mutate_input(self):
        old = sample_ir(schema_version=0)
        old['provenance'].pop('scanner_schema_version')
        frozen = copy.deepcopy(old)

        def v0_to_v1(doc):
            doc['provenance']['scanner_schema_version'] = 1
            doc['schema_version'] = 1
            return doc

        loaded = ir.load_ir(old, migrations={0: v0_to_v1})
        self.assertEqual(loaded['schema_version'], 1)
        self.assertEqual(old, frozen)
        with self.assertRaises(ir.IRSchemaError) as caught:
            ir.load_ir(old, migrations={})
        self.assertEqual(caught.exception.code, 'MIGRATION_MISSING')

        def lying_step(doc):
            return doc  # forgets to advance the version
        with self.assertRaises(ir.IRSchemaError) as caught:
            ir.load_ir(old, migrations={0: lying_step})
        self.assertEqual(caught.exception.code, 'MIGRATION_INVALID')

    def test_proprietary_payloads_are_refused(self):
        doc = sample_ir()
        doc['visual']['meshes'][0]['attributes']['vertices'] = [[0, 0, 0]]
        self.assertIn('PAYLOAD_NOT_ALLOWED', codes(ir.validate_ir(doc)))
        doc = sample_ir(extensions={'x.blob': 'A' * 5000})
        self.assertIn('PAYLOAD_NOT_ALLOWED', codes(ir.validate_ir(doc)))

    def test_cross_references_are_checked(self):
        doc = sample_ir()
        doc['visual']['renderers'][0]['mesh'] = 'SR2:mesh.missing'
        self.assertIn('DANGLING_REFERENCE', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['visual']['renderers'][0]['bones'] = ['SR2:bone.elsewhere']
        self.assertIn('DANGLING_REFERENCE', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['visual']['meshes'][0]['skinned'] = False
        self.assertIn('SKINNING_MISMATCH', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['provenance']['asset_hashes']['SR2:mesh.body'] = H['face']
        self.assertIn('HASH_MISMATCH', codes(ir.validate_ir(doc)))

    def test_hierarchy_must_be_a_single_rooted_tree(self):
        doc = sample_ir()
        doc['visual']['hierarchy'].append({'id': 'SR2:loop.a', 'parent': 'SR2:loop.b', 'kind': 'transform'})
        doc['visual']['hierarchy'].append({'id': 'SR2:loop.b', 'parent': 'SR2:loop.a', 'kind': 'transform'})
        self.assertIn('HIERARCHY_NOT_TREE', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['visual']['hierarchy'].append({'id': 'SR2:second', 'parent': None, 'kind': 'transform'})
        self.assertIn('HIERARCHY_NOT_TREE', codes(ir.validate_ir(doc)))

    def test_renderable_status_requires_renderable_visual(self):
        doc = sample_ir()
        doc['visual']['renderers'] = []
        self.assertIn('NOT_RENDERABLE', codes(ir.validate_ir(doc)))
        doc['status'] = 'QUARANTINED'
        doc['status_reasons'] = ['MISSING_CRITICAL_DEPENDENCY']
        self.assertNotIn('NOT_RENDERABLE', codes(ir.validate_ir(doc)))

    def test_identity_and_evidence_consistency(self):
        self.assertIn('INVALID_CANONICAL_ID', codes(ir.validate_ir(sample_ir(canonical_id='plort.sample'))))
        self.assertIn('INVALID_CANONICAL_ID', codes(ir.validate_ir(sample_ir(canonical_id='slime.Pink'))))
        doc = sample_ir()
        doc['provenance']['data_evidence'] = 'RUNTIME_ANCHORED'
        self.assertIn('EVIDENCE_CONTRADICTION', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['identity']['native_definition']['hash'] = 'not-a-hash'
        self.assertIn('INVALID_HASH', codes(ir.validate_ir(doc)))
        self.assertIn('INVALID_ENUM', codes(ir.validate_ir(sample_ir(status='DONE'))))

    def test_available_requires_runtime_anchored_non_synthetic_evidence(self):
        self.assertIn('EVIDENCE_CONTRADICTION', codes(ir.validate_ir(sample_ir(status='AVAILABLE'))))

    def test_validate_never_raises_on_garbage(self):
        for garbage in (None, [], 'x', {'schema': ir.IR_SCHEMA}, sample_ir(visual=[]),
                        sample_ir(identity={'source_realm': 3})):
            self.assertTrue(ir.validate_ir(garbage))


if __name__ == '__main__':
    unittest.main()


class IRDuplicateRowTests(unittest.TestCase):
    def test_duplicate_keyed_rows_are_rejected(self):
        doc = sample_ir()
        doc['gameplay']['capabilities'].append({'id': 'standard.diet', 'policy': 'DEGRADABLE', 'sources': [],
                                                'evidence': 'x'})
        self.assertIn('DUPLICATE_ID', codes(ir.validate_ir(doc)))
        doc = sample_ir()
        doc['associated']['plorts'].append({'canonical_id': 'plort.other', 'native_ref': 'Fixture.Plort.Sample',
                                            'hash': H['plort']})
        self.assertIn('DUPLICATE_ID', codes(ir.validate_ir(doc)))
