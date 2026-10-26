"""Bounded GLB2 reader checks; synthetic in-memory fixtures only, never user models."""
import copy
import json
import struct
import unittest
from unittest.mock import patch

from bridge.sdk import largo_geometry as geometry
from content import test_user_largo_import_cli as fixtures
from content.test_user_largo_import_cli import snapshot

JSON_CHUNK, BIN_CHUNK = 0x4E4F534A, 0x004E4942
POSITIONS = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
INDICES = struct.pack('<3H', 0, 1, 2)
DOC = {
    'asset': {'version': '2.0'},
    'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
    'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
    'materials': [{'name': 'synthetic-body'}],
    'buffers': [{'byteLength': 42}],
    'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': 36, 'target': 34962},
                    {'buffer': 0, 'byteOffset': 36, 'byteLength': 6, 'target': 34963}],
    'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
                   'min': [0, 0, 0], 'max': [1, 1, 0]},
                  {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
}


def build(doc=DOC, data=POSITIONS + INDICES, *, raw_json=None, extra=(), magic=b'glTF',
          version=2, length_delta=0, json_type=JSON_CHUNK):
    text = raw_json if raw_json is not None else json.dumps(doc, separators=(',', ':')).encode()
    text += b' ' * (-len(text) % 4)
    body = struct.pack('<II', len(text), json_type) + text
    if data is not None:
        padded = data + b'\0' * (-len(data) % 4)
        body += struct.pack('<II', len(padded), BIN_CHUNK) + padded
    for kind, payload in extra:
        body += struct.pack('<II', len(payload), kind) + payload
    return magic + struct.pack('<II', version, 12 + len(body) + length_delta) + body


def changed(edit):
    doc = copy.deepcopy(DOC)
    edit(doc)
    return doc


class GLBReaderTests(unittest.TestCase):
    setUp = fixtures.ImportCLITests.setUp
    command = fixtures.ImportCLITests.command

    def inspect(self, raw):
        path = self.models / 'Alpha-Beta.glb'
        if path.exists():
            path.unlink()
        path.write_bytes(raw)
        before = snapshot(self.models)
        report = geometry.inspect_model(self.models, path)
        self.assertEqual(snapshot(self.models), before, 'GLB inspection must never write sources')
        return report

    def assertStatus(self, raw, status, code=None):
        report = self.inspect(raw)
        self.assertEqual(report['status'], status, report)
        if status != 'GEOMETRY_VALID':
            self.assertFalse(report['geometry_checked'])
            self.assertIsNone(report['bounds'])
            if code:
                self.assertEqual(report['diagnostics'][0]['code'], code, report)
        self.assertFalse(report['runtime_checked'])
        return report

    def test_indexed_triangle_is_decoded_with_exact_bounds_and_material_names(self):
        report = self.assertStatus(build(), 'GEOMETRY_VALID')
        self.assertTrue(report['geometry_checked'])
        self.assertEqual((report['format'], report['vertices'], report['triangles']), ('glb', 3, 1))
        self.assertEqual(report['bounds'], {'min': [0.0, 0.0, 0.0], 'max': [1.0, 1.0, 0.0]})
        self.assertEqual(report['materials'], ['synthetic-body'])
        self.assertEqual(report['external_references'], [])
        self.assertEqual(report['diagnostics'], [])

    def test_nonindexed_strided_and_uint8_uint32_variants(self):
        def no_indices(doc):
            del doc['meshes'][0]['primitives'][0]['indices']
            doc['accessors'].pop()
            doc['bufferViews'].pop()
            doc['buffers'][0]['byteLength'] = 36
        report = self.assertStatus(build(changed(no_indices), POSITIONS), 'GEOMETRY_VALID')
        self.assertEqual((report['vertices'], report['triangles']), (3, 1))

        strided = b''.join(struct.pack('<3f4x', *POSITIONS_TUPLE[i:i + 3]) for i in (0, 3, 6))
        def stride(doc):
            doc['bufferViews'][0].update(byteLength=48, byteStride=16)
            doc['bufferViews'][1]['byteOffset'] = 48
            doc['buffers'][0]['byteLength'] = 54
        self.assertStatus(build(changed(stride), strided + INDICES), 'GEOMETRY_VALID')

        for component, packed in ((5121, struct.pack('<3B', 0, 1, 2)), (5125, struct.pack('<3I', 0, 1, 2))):
            def width(doc, component=component, packed=packed):
                doc['accessors'][1]['componentType'] = component
                doc['bufferViews'][1]['byteLength'] = len(packed)
                doc['buffers'][0]['byteLength'] = 36 + len(packed)
            with self.subTest(component=component):
                self.assertStatus(build(changed(width), POSITIONS + packed), 'GEOMETRY_VALID')

    def test_decimal_min_max_compared_after_float32_rounding(self):
        positions = struct.pack('<9f', 0.1, 0, 0, 1.2, 0, 0, 0.1, 1, 0)
        def decimal(doc):
            doc['accessors'][0].update(min=[0.1, 0, 0], max=[1.2, 1, 0])
        self.assertStatus(build(changed(decimal), positions + INDICES), 'GEOMETRY_VALID')
        def wrong(doc):
            doc['accessors'][0].update(min=[0.1, 0, 0], max=[1.3, 1, 0])
        self.assertStatus(build(changed(wrong), positions + INDICES), 'REJECTED', 'MALFORMED_GEOMETRY')

    def test_container_violations_are_rejected(self):
        whole = build()
        cases = {
            'magic': build(magic=b'gltf'),
            'declared length': build(length_delta=4),
            'trailing bytes': whole + b'\0\0\0\0',
            'truncated': whole[:-4],
            'header only': whole[:12],
            'json not first': build(json_type=BIN_CHUNK),
            'misaligned chunk': whole[:12] + struct.pack('<II', 3, JSON_CHUNK) + b'{} ',
            'duplicate json': build(extra=[(JSON_CHUNK, b'{}  ')]),
            'bin not second': build(data=None, extra=[(0x12345678, b'abcd'), (BIN_CHUNK, POSITIONS + INDICES + b'\0\0')]),
            'missing bin': build(data=None),
            'bin longer than padding': build(changed(lambda d: d['buffers'][0].update(byteLength=36))),
            'bin shorter than buffer': build(changed(lambda d: d['buffers'][0].update(byteLength=48))),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                self.assertStatus(raw, 'REJECTED')

        def consistent(body):  # header length matches, so only the chunk-level check can fail
            return b'glTF' + struct.pack('<II', 2, 12 + len(body)) + body
        json_chunk = whole[12:20 + struct.unpack_from('<I', whole, 12)[0]]
        chunk_level = {
            'misaligned chunk length': (consistent(struct.pack('<II', 3, JSON_CHUNK) + b'{} '),
                                        'misaligned or truncated GLB chunk'),
            'chunk overruns file': (consistent(json_chunk + struct.pack('<II', 64, BIN_CHUNK) + b'\0' * 8),
                                    'misaligned or truncated GLB chunk'),
            'partial chunk header': (consistent(json_chunk + b'\0\0\0\0'), 'truncated GLB chunk header'),
            'no chunks': (consistent(b''), 'GLB JSON chunk must be first'),
        }
        for name, (raw, detail) in chunk_level.items():
            with self.subTest(name=name):
                report = self.assertStatus(raw, 'REJECTED', 'MALFORMED_GEOMETRY')
                self.assertEqual(report['diagnostics'][0]['detail'], detail)

    def test_json_policy_rejects_duplicates_nonfinite_types_and_depth(self):
        text = json.dumps(DOC, separators=(',', ':')).encode()
        cases = {
            'duplicate key': text.replace(b'{"asset":', b'{"scene":0,"asset":', 1),
            'NaN': text.replace(b'"min":[0,0,0]', b'"min":[NaN,0,0]'),
            'overflow': text.replace(b'"min":[0,0,0]', b'"min":[1e999,0,0]'),
            'invalid utf8': text.replace(b'synthetic-body', b'synthetic-\xff'),
            'bom': b'\xef\xbb\xbf' + text,
            'deep nesting': text[:-1] + b',"extras":' + b'[' * 4000 + b']' * 4000 + b'}',
            'root array': b'[]',
        }
        for name, raw_json in cases.items():
            with self.subTest(name=name):
                self.assertStatus(build(raw_json=raw_json), 'REJECTED')
        bool_index = changed(lambda d: d['meshes'][0]['primitives'][0].update(indices=True))
        self.assertStatus(build(bool_index), 'REJECTED')

    def test_accessor_and_geometry_violations_are_rejected(self):
        degenerate = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 2, 0, 0)
        nonfinite = struct.pack('<9f', 0, 0, 0, float('inf'), 0, 0, 0, 1, 0)
        cases = {
            'view outside buffer': (changed(lambda d: d['bufferViews'][1].update(byteLength=8)), None),
            'accessor overruns view': (changed(lambda d: d['accessors'][0].update(count=4)), None),
            'misaligned accessor': (changed(lambda d: d['accessors'][0].update(byteOffset=2)), None),
            'stride too small': (changed(lambda d: d['bufferViews'][0].update(byteStride=8)), None),
            'stride not multiple of four': (changed(lambda d: d['bufferViews'][0].update(byteStride=14)), None),
            'stride on index view': (changed(lambda d: d['bufferViews'][1].update(byteStride=4)), None),
            'index out of range': (None, POSITIONS + struct.pack('<3H', 0, 1, 3)),
            'repeated index': (None, POSITIONS + struct.pack('<3H', 0, 1, 1)),
            'index count not triangles': (changed(lambda d: (d['accessors'][1].update(count=2),
                                                             d['bufferViews'][1].update(byteLength=4))), None),
            'float indices': (changed(lambda d: d['accessors'][1].update(componentType=5126)), None),
            'normalized float positions': (changed(lambda d: d['accessors'][0].update(normalized=True)), None),
            'primitive restart index': (changed(lambda d: (d['accessors'][1].update(componentType=5121),
                                                           d['bufferViews'][1].update(byteLength=3),
                                                           d['buffers'][0].update(byteLength=39))),
                                        POSITIONS + struct.pack('<3B', 0, 1, 255)),
            'position min missing': (changed(lambda d: d['accessors'][0].pop('min')), None),
            'position min wrong length': (changed(lambda d: d['accessors'][0].update(min=[0, 0])), None),
            'degenerate': (changed(lambda d: d['accessors'][0].update(max=[2, 0, 0])), degenerate + INDICES),
            'missing accessor': (changed(lambda d: d['meshes'][0]['primitives'][0].update(indices=9)), None),
            'count zero': (changed(lambda d: d['accessors'][1].update(count=0)), None),
            'unknown material': (changed(lambda d: d['meshes'][0]['primitives'][0].update(material=5)), None),
        }
        for name, (doc, data) in cases.items():
            with self.subTest(name=name):
                self.assertStatus(build(doc or DOC, data or POSITIONS + INDICES), 'REJECTED')
        nonfinite_doc = changed(lambda d: d['accessors'][0].update(max=[1e20, 1, 0]))
        self.assertStatus(build(nonfinite_doc, nonfinite + INDICES), 'REJECTED', 'NONFINITE_GEOMETRY')
        with patch.object(geometry, 'MAX_VERTICES', 2):
            self.assertStatus(build(), 'REJECTED', 'GEOMETRY_LIMIT')

    def test_valid_but_unsupported_features_need_an_adapter(self):
        primitive = lambda **kv: (lambda d: d['meshes'][0]['primitives'][0].update(**kv))
        cases = {
            'glTF 1 binary': build(version=1),
            'unknown extension chunk': build(extra=[(0x12345678, b'abcd')]),
            'extensions used': build(changed(lambda d: d.update(extensionsUsed=['KHR_draco_mesh_compression']))),
            'skins': build(changed(lambda d: d.update(skins=[{'joints': [0]}]))),
            'animations': build(changed(lambda d: d.update(animations=[]))),
            'textures': build(changed(lambda d: d.update(textures=[{}]))),
            'lines mode': build(changed(primitive(mode=1))),
            'morph targets': build(changed(primitive(targets=[{'POSITION': 0}]))),
            'extra attribute': build(changed(lambda d: d['meshes'][0]['primitives'][0]['attributes'].update(NORMAL=0))),
            'second primitive': build(changed(lambda d: d['meshes'][0]['primitives'].append(
                copy.deepcopy(d['meshes'][0]['primitives'][0])))),
            'node transform': build(changed(lambda d: d['nodes'][0].update(translation=[1, 0, 0]))),
            'child node': build(changed(lambda d: (d['nodes'][0].update(children=[1]), d['nodes'].append({})))),
            'sparse accessor': build(changed(lambda d: d['accessors'][0].update(
                sparse={'count': 1, 'indices': {}, 'values': {}}))),
            'quantized positions': build(changed(lambda d: d['accessors'][0].update(componentType=5123))),
            'implicit zero accessor': build(changed(lambda d: d['accessors'][0].pop('bufferView'))),
            'textured material': build(changed(lambda d: d['materials'][0].update(
                pbrMetallicRoughness={'baseColorTexture': {'index': 0}}))),
            'asset version 2.1': build(changed(lambda d: d['asset'].update(version='2.1'))),
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                self.assertStatus(raw, 'NEEDS_FORMAT_ADAPTER', 'NEEDS_FORMAT_ADAPTER')

    def test_external_buffer_uri_is_never_opened(self):
        (self.base / 'outside.bin').write_bytes(POSITIONS + INDICES)
        for uri in ('../outside.bin', 'data:application/octet-stream;base64,AAAA',
                    'https://invalid.example/b.bin', '%2E%2E/outside.bin'):
            doc = changed(lambda d, uri=uri: d['buffers'][0].update(uri=uri))
            with self.subTest(uri=uri), patch.object(geometry, 'read_bytes', wraps=geometry.read_bytes) as reader:
                self.assertStatus(build(doc, None), 'NEEDS_FORMAT_ADAPTER', 'NEEDS_FORMAT_ADAPTER')
                self.assertEqual(reader.call_count, 1, 'only the model itself may be read')

    def test_cli_exit_codes_for_supported_unsupported_and_malformed_glb(self):
        path = self.models / 'Alpha-Beta.glb'
        for raw, exit_code, status in ((build(), 0, 'GEOMETRY_VALID'),
                                       (build(changed(lambda d: d.update(skins=[{'joints': [0]}]))), 3,
                                        'NEEDS_FORMAT_ADAPTER'),
                                       (b'opaque synthetic input', 4, 'REJECTED')):
            path.write_bytes(raw)
            with self.subTest(status=status):
                code, report = self.command('validate')
                self.assertEqual(code, exit_code, report)
                self.assertEqual(report['entries'][0]['geometry'][0]['status'], status)
                self.assertEqual(report['registration_receipt']['executed_operations'], [])


POSITIONS_TUPLE = struct.unpack('<9f', POSITIONS)

if __name__ == '__main__':
    unittest.main()
