"""Adversarial synthetic geometry/metadata reader checks."""
import hashlib
import json
import unittest
from unittest.mock import patch

from bridge.sdk import largo
from bridge.sdk import largo_geometry as geometry
from bridge.sdk import largo_io
from content import test_user_largo_import_cli as fixtures
from content.test_user_largo_import_cli import OBJ, META, snapshot


class GeometryTests(unittest.TestCase):
    setUp = fixtures.ImportCLITests.setUp
    command = fixtures.ImportCLITests.command
    package = fixtures.ImportCLITests.package
    write_manifest = staticmethod(fixtures.ImportCLITests.write_manifest)
    def test_ascii_stl_negative_indices_and_variant_refusal(self):
        path = self.models / 'Alpha-Beta.stl'
        path.write_bytes(b'solid test\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid test\n')
        before = snapshot(self.models)
        self.assertEqual(geometry.inspect_model(self.models, path)['status'], 'GEOMETRY_VALID')
        self.assertEqual(snapshot(self.models), before)
        path = self.models / 'Alpha-Beta.obj'
        path.write_bytes(OBJ.replace(b'f 1 2 3', b'f -3 -2 -1'))
        self.assertEqual(geometry.inspect_model(self.models, path)['status'], 'GEOMETRY_VALID')
        for data, status in [(OBJ.replace(b'f 1 2 3', b'f 1 2 3 1'), 'NEEDS_FORMAT_ADAPTER'),
                             (OBJ + b'vt inf 0\n', 'REJECTED'),
                             (OBJ.replace(b'f 1 2 3', b'f 1 1 1'), 'REJECTED'),
                             (OBJ.replace(b'f 1 2 3', b'f 1/1 2/2 3/3'), 'REJECTED'),
                             (OBJ.replace(b'f 1 2 3', b'f 1// 2// 3//'), 'REJECTED'),
                             (b'v 0 0 0 0\n' + OBJ, 'REJECTED')]:
            with self.subTest(data=data):
                path.write_bytes(data)
                before = snapshot(self.models)
                self.assertEqual(geometry.inspect_model(self.models, path)['status'], status)
                self.assertEqual(snapshot(self.models), before)

    def test_bounds_and_input_limits(self):
        path = self.models / 'Alpha-Beta.obj'
        path.write_bytes(OBJ)
        before = snapshot(self.models)
        with patch.object(geometry, 'MAX_VERTICES', 2):
            self.assertEqual(geometry.inspect_model(self.models, path)['diagnostics'][0]['code'], 'GEOMETRY_LIMIT')
        with patch.object(geometry, 'MAX_LINE_BYTES', 3):
            self.assertEqual(geometry.inspect_model(self.models, path)['diagnostics'][0]['code'], 'GEOMETRY_LIMIT')
        with patch.object(largo_io, 'MAX_ENTRIES', 0):
            with self.assertRaises(largo_io.ImportProblem):
                largo_io.inventory(self.models)
        with patch.object(largo_io, 'MAX_TOTAL_BYTES', 2):
            with self.assertRaises(largo_io.ImportProblem):
                largo_io.inventory(self.models)
        with self.assertRaises(largo_io.ImportProblem):
            largo_io.read_bytes(path, limit=2)
        self.assertEqual(snapshot(self.models), before)

    def test_json_rejects_duplicates_constants_and_overflow(self):
        path = self.base / 'bad.json'
        for value in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}'):
            path.write_text(value)
            before = snapshot(self.base)
            with self.subTest(value=value), self.assertRaises(largo_io.ImportProblem):
                largo_io.read_json(path)
            self.assertEqual(snapshot(self.base), before)

    def test_metadata_invalid_declarations_no_guessing(self):
        package, original = self.package()
        invalid = [{'transform': {'scale': [0, 1, 1]}},
                   {'transform': {'orientation_xyzw': [0, 0, 0, 0]}},
                   {'transform': {'pivot': [float('nan'), 0, 0]}},
                   {'collision': {'mode': 'mesh', 'asset': '../outside.obj'}},
                   {'face_anchors': [{'name': 'x', 'position': [1, 2, 3]}]},
                   {'animation_binding': {'mode': 'skeletal', 'bones': ['a'], 'root_bone': 'b'}},
                   {'source_realms': ['SR3']}, {'intended_realms': []},
                   {'target_parent_availability': {'SR1': ['slime.missing']}}, {'unknown': True}]
        for metadata in invalid:
            original['import_metadata'] = metadata
            self.write_manifest(package, original)
            before = snapshot(self.models)
            result = largo.validate_package(package)
            self.assertEqual(result['status'], 'REJECTED', result)
            self.assertIn('INVALID_IMPORT_METADATA', {d['code'] for d in result['diagnostics']})
            self.assertEqual(snapshot(self.models), before)
        original['import_metadata'] = META
        original['provenance']['user_owned'] = False
        self.write_manifest(package, original)
        self.assertEqual(largo.validate_package(package)['status'], 'REJECTED')

    def test_declared_material_library_is_hashed_not_treated_as_model(self):
        package, manifest = self.package()
        mesh = b'mtllib body.mtl\nusemtl body\n' + OBJ
        material = b'newmtl body\nKd 1 0 0\n'
        (package / 'body.obj').write_bytes(mesh)
        (package / 'body.mtl').write_bytes(material)
        manifest['assets'][0]['sha256'] = hashlib.sha256(mesh).hexdigest()
        manifest['assets'].append({'path': 'body.mtl', 'kind': 'material_library',
                                   'sha256': hashlib.sha256(material).hexdigest()})
        self.write_manifest(package, manifest)
        before = snapshot(self.models)
        code, report = self.command('validate')
        self.assertEqual(code, 0, report)
        self.assertEqual(len(report['entries'][0]['geometry']), 1)
        self.assertEqual(report['entries'][0]['geometry'][0]['external_references'][0]['path'], 'package/body.mtl')
        self.assertEqual(snapshot(self.models), before)

    def test_reserved_output_and_empty_external_dependency_refused(self):
        path = self.models / 'Alpha-Beta.obj'
        path.write_bytes(b'mtllib body.mtl\n' + OBJ)
        (self.models / 'body.mtl').write_bytes(b'')
        result = geometry.inspect_model(self.models, path)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertIn('REFERENCE_EMPTY', {d['code'] for d in result['diagnostics']})
        path.write_bytes(OBJ)
        for name in ('CON.json', 'NUL.txt', 'COM1.report', 'LPT9.json'):
            code, report = self.command('scan', '--out', str(self.base / name))
            self.assertEqual(code, 5, report)
            self.assertIn('UNSAFE_OUTPUT', {d['code'] for d in report['diagnostics']})

    def test_catalog_no_guessing_and_native_refusal(self):
        self.package()
        catalog = json.loads(self.catalog.read_text())
        catalog['species'][0]['canonical_id'] = 'Alpha'  # preserve and reject, never repair to slime.alpha
        self.catalog.write_text(json.dumps(catalog))
        self.assertEqual(self.command()[0], 4)
        self.catalog.write_text(json.dumps(fixtures.CATALOG))
        catalog = json.loads(self.catalog.read_text())
        catalog['target_context'] = {'SR1': {'native': ['largo.alpha.beta'],
                                           'parents': ['slime.alpha', 'slime.beta']}}
        self.catalog.write_text(json.dumps(catalog))
        code, report = self.command('plan', '--target', 'SR1')
        self.assertEqual(code, 4, report)
        self.assertEqual(report['entries'][0]['plans'][0]['final'], 'REFUSED')
        self.assertFalse(report['entries'][0]['plans'][0]['registrable'])
        self.assertEqual(report['registration_receipt']['executed_operations'], [])


if __name__ == '__main__':
    unittest.main()
