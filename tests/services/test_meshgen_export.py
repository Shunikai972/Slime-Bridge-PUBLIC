"""SlimeBridge gateway exports: GLB writer + self-check, OBJ/MTL, sidecar, previews, CLI output safety."""

import functools
import hashlib
import io
import json
import os
import re
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stdout
from pathlib import Path

from bridge.meshgen import cli
from bridge.meshgen import preview as R
from bridge.meshgen.export_gltf import (BIN_CHUNK, GLB_MAGIC, JSON_CHUNK, export_glb, read_glb, validate_glb)
from bridge.meshgen.export_obj import export_obj
from bridge.meshgen.palettes import SEMANTIC_MATERIALS
from bridge.meshgen.portal import PortalParams, generate_portal, variant_params
from bridge.meshgen.sidecar import build_sidecar, sidecar_bytes

FAST = {'ring_segments': 32, 'bevel_segments': 2}


@functools.lru_cache(maxsize=None)
def model(variant='meadow'):
    return generate_portal(variant_params(variant, **FAST))


@functools.lru_cache(maxsize=None)
def glb(variant='meadow'):
    return export_glb(model(variant))


def rebuild(doc, binary):
    """GLB container around an edited JSON document (for corruption tests)."""
    j = json.dumps(doc).encode()
    j += b' ' * ((4 - len(j) % 4) % 4)
    total = 12 + 8 + len(j) + 8 + len(binary)
    return (struct.pack('<III', GLB_MAGIC, 2, total) + struct.pack('<II', len(j), JSON_CHUNK) + j +
            struct.pack('<II', len(binary), BIN_CHUNK) + binary)


def codes(diags):
    return {d['code'] for d in diags}


class Glb(unittest.TestCase):
    def test_deterministic_bytes_and_sidecar(self):
        again = export_glb(generate_portal(variant_params('meadow', **FAST)))
        self.assertEqual(again, glb())
        a = sidecar_bytes(model(), {'x.glb': glb()})
        b = sidecar_bytes(generate_portal(variant_params('meadow', **FAST)), {'x.glb': again})
        self.assertEqual(a, b)
        self.assertEqual(json.loads(a)['content_sha256'], json.loads(b)['content_sha256'])
        other = build_sidecar(model('coral'), {'x.glb': glb('coral')})
        self.assertNotEqual(json.loads(a)['content_sha256'], other['content_sha256'])

    def test_self_check_passes_for_full_and_per_lod_exports(self):
        for variant in ('meadow', 'twilight', 'coral'):
            self.assertEqual(validate_glb(glb(variant)), [], variant)
        for level in (0, 1, 2):
            self.assertEqual(validate_glb(export_glb(model(), levels=[level])), [])

    def test_container_layout(self):
        data = glb()
        magic, version, total = struct.unpack_from('<III', data)
        self.assertEqual((magic, version, total), (GLB_MAGIC, 2, len(data)))
        jlen, jtype = struct.unpack_from('<II', data, 12)
        self.assertEqual((jtype, jlen % 4), (JSON_CHUNK, 0))
        self.assertTrue(data[20 + jlen - 1:20 + jlen] in (b' ', b'}'))
        doc, binary = read_glb(data)
        self.assertEqual(len(binary) % 4, 0)
        self.assertTrue(all(v['byteOffset'] % 4 == 0 for v in doc['bufferViews']))
        self.assertEqual(doc['asset']['version'], '2.0')

    def test_scene_content(self):
        m = model()
        doc, _ = read_glb(glb())
        self.assertEqual([mat['name'] for mat in doc['materials']], list(SEMANTIC_MATERIALS))
        names = [n['name'] for n in doc['nodes']]
        for level in (0, 1, 2):
            self.assertIn(f'gateway_LOD{level}', names)
            self.assertIn(f'ring.frame_LOD{level}', names)
        self.assertEqual(len(doc['meshes']), sum(len(lod['parts']) for lod in m.lods))
        self.assertEqual(sum(1 for n in names if n.startswith('COL_')), len(m.collision))
        self.assertIn('TRIGGER_portal', names)
        self.assertTrue(set(m.sockets) <= set(names))
        root = doc['nodes'][doc['scenes'][0]['nodes'][0]]
        meta = root['extras']['slimebridge']
        self.assertEqual(meta['evidence'], 'NEEDS_LOCAL_VISUAL_PROOF')
        self.assertEqual((meta['conventions']['up_axis'], meta['conventions']['forward_axis']), ('+Y', '+Z'))
        self.assertEqual(root['extras']['params'], m.params.to_dict())
        energy = doc['materials'][list(SEMANTIC_MATERIALS).index('portal_energy')]
        self.assertEqual((energy['alphaMode'], energy['doubleSided']), ('BLEND', True))
        self.assertIn('emissiveFactor', doc['materials'][list(SEMANTIC_MATERIALS).index('emissive_trim')])
        for mesh in doc['meshes']:
            pos = doc['accessors'][mesh['primitives'][0]['attributes']['POSITION']]
            self.assertTrue({'min', 'max'} <= set(pos))

    def test_corrupted_glbs_are_rejected(self):
        data = glb()
        doc, binary = read_glb(data)
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb(b'')))
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb(b'XXXX' + data[4:])))
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb(data[:-8])))
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb(data + b'\0\0\0\0')))
        broken = bytearray(data)
        broken[20:24] = b'#!?<'
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb(bytes(broken))))
        self.assertIn('GLB_CONTAINER_INVALID', codes(validate_glb('not bytes')))
        # an index pointing past its vertices
        prim = doc['meshes'][0]['primitives'][0]
        acc = doc['accessors'][prim['indices']]
        view = doc['bufferViews'][acc['bufferView']]
        bad = bytearray(binary)
        fmt = '<H' if acc['componentType'] == 5123 else '<I'
        struct.pack_into(fmt, bad, view['byteOffset'], 60000)
        self.assertIn('GLB_INDEX_RANGE', codes(validate_glb(rebuild(doc, bytes(bad)))))
        # NaN in positions
        pos = doc['accessors'][prim['attributes']['POSITION']]
        bad = bytearray(binary)
        struct.pack_into('<f', bad, doc['bufferViews'][pos['bufferView']]['byteOffset'], float('nan'))
        self.assertIn('GLB_NON_FINITE', codes(validate_glb(rebuild(doc, bytes(bad)))))
        # JSON-level corruption
        edits = {
            'GLB_ACCESSOR_MINMAX': lambda d: d['accessors'][prim['attributes']['POSITION']].update(max=[0.0, 0.0, 0.0]),
            'GLB_BUFFER_VIEW_BOUNDS': lambda d: d['bufferViews'][0].update(byteLength=10 ** 9),
            'GLB_ACCESSOR_BOUNDS': lambda d: d['accessors'][0].update(count=d['accessors'][0]['count'] + 1000),
            'GLB_MATERIAL_INDEX': lambda d: d['meshes'][0]['primitives'][0].update(material=99),
            'GLB_NODE_CHILD': lambda d: d['nodes'][0]['children'].append(10 ** 6),
            'GLB_NODE_PARENTS': lambda d: d['nodes'][0]['children'].append(d['nodes'][0]['children'][0]),
            'GLB_ASSET_VERSION': lambda d: d['asset'].update(version='1.0'),
            'GLB_INDEX_COUNT': lambda d: d['accessors'][prim['indices']].update(count=acc['count'] - 1),
            'GLB_POSITION_ACCESSOR': lambda d: d['accessors'][prim['attributes']['POSITION']].pop('min'),
            'GLB_STRUCTURE_INVALID': lambda d: d.update(meshes=[{'primitives': [{'attributes': 7}]}]),
        }
        for code, edit in edits.items():
            with self.subTest(code=code):
                d = json.loads(json.dumps(doc))
                edit(d)
                self.assertIn(code, codes(validate_glb(rebuild(d, binary))))

    def test_invalid_models_are_not_exported(self):
        broken = generate_portal(PortalParams(ring_segments=-1))
        for fn in (lambda: export_glb(broken), lambda: export_obj(broken), lambda: build_sidecar(broken)):
            with self.assertRaises(ValueError):
                fn()


class Obj(unittest.TestCase):
    def test_obj_references_mtl_and_uses_valid_one_based_indices(self):
        m = model()
        obj, mtl = export_obj(m, level=1, mtl_name='gate.mtl')
        lines = obj.splitlines()
        self.assertIn('mtllib gate.mtl', lines)
        v = sum(1 for line in lines if line.startswith('v '))
        self.assertEqual(v, sum(1 for line in lines if line.startswith('vt ')))
        self.assertEqual(v, sum(1 for line in lines if line.startswith('vn ')))
        self.assertEqual(v, sum(p.vertex_count for p in m.lods[1]['parts']))
        faces = [line for line in lines if line.startswith('f ')]
        self.assertEqual(len(faces), sum(p.triangle_count for p in m.lods[1]['parts']))
        refs = [int(i) for f in faces for corner in f.split()[1:] for i in corner.split('/')]
        self.assertEqual((min(refs), max(refs)), (1, v))
        used = {line.split()[1] for line in lines if line.startswith('usemtl ')}
        defined = set(re.findall(r'^newmtl (\S+)$', mtl, re.M))
        self.assertEqual(used, defined)
        self.assertTrue(defined <= set(SEMANTIC_MATERIALS))
        self.assertNotIn('nan', obj.lower())
        self.assertNotIn('-0.000000', obj)
        with self.assertRaises(ValueError):
            export_obj(m, mtl_name='../evil.mtl')


class Sidecar(unittest.TestCase):
    def test_sidecar_contents(self):
        m = model()
        data = glb()
        doc = build_sidecar(m, {'gate.glb': data})
        self.assertEqual(doc['files']['gate.glb'], {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(doc['collision']['boxes'], m.collision)
        self.assertEqual(doc['trigger'], m.trigger)
        self.assertEqual(doc['sockets'], m.sockets)
        self.assertEqual(len(doc['metadata']['lods']), 3)
        self.assertEqual(doc['params'], m.params.to_dict())
        self.assertEqual(doc['conventions']['up_axis'], '+Y')
        self.assertEqual([x['semantic'] for x in doc['materials']], list(SEMANTIC_MATERIALS))
        self.assertEqual(doc['evidence'], 'NEEDS_LOCAL_VISUAL_PROOF')
        self.assertIn('Unity import', doc['not_tested_here'])


class OutputPaths(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name) / 'repo'
        (self.base / 'Lab' / 'GeneratedContent').mkdir(parents=True)
        self.outside = Path(self._tmp.name) / 'outside'
        self.outside.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_refuses_traversal_absolute_and_foreign_prefixes(self):
        for out in ('../x', 'Lab/GeneratedContent/../../x', '/tmp/x', str(self.outside), 'Lab/Other/x', 'Lab',
                    'Lab/GeneratedContent/./x', 'Lab\\GeneratedContent\\x', '', 'C:/x'):
            with self.subTest(out=out), self.assertRaises(cli.OutputPathError):
                cli.resolve_output(self.base, out)
        root, out = cli.resolve_output(self.base, 'Lab/GeneratedContent/portal3d')
        self.assertEqual(out, root / 'portal3d')

    @unittest.skipUnless(hasattr(os, 'symlink'), 'symlinks unavailable')
    def test_refuses_symlinked_directories(self):
        link = self.base / 'Lab' / 'GeneratedContent' / 'link'
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except OSError:
            self.skipTest('cannot create symlinks here')
        with self.assertRaises(cli.OutputPathError):
            cli.resolve_output(self.base, 'Lab/GeneratedContent/link')
        with self.assertRaises(cli.OutputPathError):
            cli.resolve_output(self.base, 'Lab/GeneratedContent/link/portal')
        # a symlinked ancestor of the output root is refused too
        other = Path(self._tmp.name) / 'repo2'
        other.mkdir()
        (other / 'Lab').symlink_to(self.base / 'Lab', target_is_directory=True)
        with self.assertRaises(cli.OutputPathError):
            cli.resolve_output(other, 'Lab/GeneratedContent/portal3d')
        # a link planted between resolution and writing is refused by the atomic writer
        root, out = cli.resolve_output(self.base, 'Lab/GeneratedContent/late')
        (self.base / 'Lab' / 'GeneratedContent' / 'late').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(cli.OutputPathError):
            cli.write_bundle(root, out, {'a.txt': b'x'})
        self.assertEqual(list(self.outside.iterdir()), [])

    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(['--base', str(self.base), *argv])
        return code, json.loads(buf.getvalue())

    def test_cli_writes_confined_deterministic_bundle(self):
        params = Path(self._tmp.name) / 'params.json'
        params.write_text(json.dumps(FAST), encoding='utf-8')
        code, report = self.run_cli('--variant', 'meadow', '--params', str(params), '--name', 'gate')
        self.assertEqual(code, 0, report)
        out = self.base / 'Lab' / 'GeneratedContent' / 'portal3d'
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ['gate.glb', 'gate.mtl', 'gate.obj', 'gate.portal.json'])
        self.assertEqual(report['glb_self_check'], 'PASSED')
        self.assertEqual(report['evidence'], 'NEEDS_LOCAL_VISUAL_PROOF')
        first = {p.name: p.read_bytes() for p in out.iterdir()}
        self.assertEqual(validate_glb(first['gate.glb']), [])
        side = json.loads(first['gate.portal.json'])
        self.assertEqual(side['files']['gate.glb']['sha256'], hashlib.sha256(first['gate.glb']).hexdigest())
        code, _ = self.run_cli('--variant', 'meadow', '--params', str(params), '--name', 'gate')
        self.assertEqual(code, 0)
        self.assertEqual({p.name: p.read_bytes() for p in out.iterdir()}, first)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_cli_rejects_bad_input_without_writing(self):
        bad = Path(self._tmp.name) / 'bad.json'
        bad.write_text(json.dumps({'ramp_slope_deg': 35.0}), encoding='utf-8')
        code, report = self.run_cli('--params', str(bad))
        self.assertEqual((code, report['status']), (cli.EXIT_INPUT, 'INVALID_PARAMS'))
        self.assertIn('RAMP_TOO_STEEP', {d['code'] for d in report['diagnostics']})
        bad.write_text('{"ring_inner_radius": NaN}', encoding='utf-8')
        code, report = self.run_cli('--params', str(bad))
        self.assertEqual(code, cli.EXIT_INPUT)
        code, report = self.run_cli('--out', '../escape')
        self.assertEqual((code, report['status']), (cli.EXIT_PATH, 'UNSAFE_OUTPUT_PATH'))
        code, report = self.run_cli('--name', '../evil')
        self.assertEqual(code, cli.EXIT_INPUT)
        self.assertEqual(list((self.base / 'Lab' / 'GeneratedContent').iterdir()), [])


def _png_chunks(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, chunks = 8, []
    while pos < len(data):
        length, tag = struct.unpack_from('>I4s', data, pos)
        chunk = data[pos + 8:pos + 8 + length]
        crc, = struct.unpack_from('>I', data, pos + 8 + length)
        assert crc == zlib.crc32(tag + chunk) & 0xFFFFFFFF
        chunks.append((tag, chunk))
        pos += 12 + length
    return chunks


class Previews(unittest.TestCase):
    def test_png_writer(self):
        data = R.png_bytes(2, 1, bytes([255, 0, 0, 0, 0, 255]))
        chunks = _png_chunks(data)
        self.assertEqual([c[0] for c in chunks], [b'IHDR', b'IDAT', b'IEND'])
        self.assertEqual(struct.unpack('>IIBBBBB', chunks[0][1]), (2, 1, 8, 2, 0, 0, 0))
        self.assertEqual(zlib.decompress(chunks[1][1]), b'\x00\xff\x00\x00\x00\x00\xff')

    def test_small_render_is_deterministic_and_shows_the_model(self):
        model = generate_portal(variant_params('meadow', ring_segments=24, bevel_segments=1))
        pngs = R.render_views(model, level=2, width=64, height=48, supersample=1, views=['front', 'three_quarter'])
        again = R.render_views(model, level=2, width=64, height=48, supersample=1, views=['front'])
        self.assertEqual(pngs['front'], again['front'])
        for png in pngs.values():
            chunks = dict(_png_chunks(png))
            self.assertEqual(struct.unpack('>II', chunks[b'IHDR'][:8]), (64, 48))
            self.assertEqual(len(zlib.decompress(chunks[b'IDAT'])), 48 * (1 + 64 * 3))
        parts = model.lods[2]['parts']
        cam = R.view_camera('three_quarter', R.M.bounds(parts), 64, 48)
        with_model, background = R.render(parts, 'meadow', cam, 1)[2], R.render([], 'meadow', cam, 1)[2]
        changed = sum(1 for i in range(0, len(background), 3) if with_model[i:i + 3] != background[i:i + 3])
        self.assertGreater(changed, 0.15 * 64 * 48)  # the gateway covers a good part of the frame


if __name__ == '__main__':
    unittest.main()
