import json
import os
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

import numpy as np

from sbconv import protocol
from sbconv.detect import discovery, unity_serialized as us
from sbconv.extract import animation as A
from sbconv.extract.mesh import MeshData, validate_mesh
from sbconv.ir import curves, rig, store
from sbconv.safefs import OutputRoot, UnsafePathError, sha256_bytes


class SafeFs(unittest.TestCase):
    def test_output_root_rejects_overlap_and_escape(self):
        with tempfile.TemporaryDirectory() as d:
            game = Path(d) / 'game'
            game.mkdir()
            with self.assertRaises(UnsafePathError):
                OutputRoot(game / 'out', protected=[game])
            with self.assertRaises(UnsafePathError):
                OutputRoot(Path(d), protected=[game / 'sub'])  # would contain the protected dir only if equal-or-parent
            out = OutputRoot(Path(d) / 'out', protected=[game])
            with self.assertRaises(UnsafePathError):
                out.resolve('..', 'evil.txt')
            with self.assertRaises(UnsafePathError):
                out.write_bytes('a/../../evil.txt', b'x')

    def test_atomic_write_and_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            out = OutputRoot(Path(d) / 'o')
            p = out.write_bytes('x/y.bin', b'abc')
            self.assertEqual(p.read_bytes(), b'abc')
            self.assertEqual([f.name for f in p.parent.iterdir()], ['y.bin'])       # no temp leftovers
            dry = OutputRoot(Path(d) / 'dry', dry_run=True)
            dry.write_bytes('a.bin', b'1')
            self.assertFalse((Path(d) / 'dry').exists())


class Detect(unittest.TestCase):
    def test_header_v21_and_v22(self):
        with tempfile.TemporaryDirectory() as d:
            for ver, unity in ((21, '2019.4.29f1'), (22, '2021.3.28f1')):
                p = Path(d) / f'f{ver}'
                if ver >= 22:
                    head = struct.pack('>IIII', 0, 0, ver, 0) + bytes([0, 0, 0, 0]) + struct.pack('>IqqQ', 100, 4096, 200, 0)
                else:
                    head = struct.pack('>IIII', 100, 4096, ver, 200) + bytes([0, 0, 0, 0])
                p.write_bytes(head + unity.encode() + b'\0' + struct.pack('<i', 19) + bytes([0]) + b'\0' * 64)
                h = us.read_header(p)
                self.assertEqual((h.format_version, h.unity_version, h.platform_name, h.has_type_tree), (ver, unity, 'StandaloneWindows64', False))

    def test_rejects_non_unity(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x'
            p.write_bytes(b'hello world' * 10)
            with self.assertRaises(us.NotSerializedFile):
                us.read_header(p)

    def test_find_game_root_in_wrapper_folders(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'a' / 'b' / 'c'
            (root / 'SlimeRancher2_Data').mkdir(parents=True)
            (root / 'SlimeRancher2.exe').write_bytes(b'MZ')
            self.assertEqual(discovery.find_game_root(Path(d) / 'a', 'SR2'), str(root))
            self.assertIsNone(discovery.find_game_root(Path(d) / 'a', 'SR1'))
            self.assertEqual(discovery.detect_game_at(d)[0], 'SR2')


class StoreAndGeometry(unittest.TestCase):
    def test_sbmesh_roundtrip(self):
        from tests.synth import make_package
        with tempfile.TemporaryDirectory() as d:
            m = make_package(d)
            f = next(iter(m['meshes'].values()))['file']
            hdr, arrs = store.unpack_mesh((Path(d) / f).read_bytes())
            self.assertEqual(hdr['vertex_count'], 4)
            self.assertEqual(arrs['bone_indices'].shape, (4, 4))
            self.assertEqual(arrs['indices0'].tolist(), [0, 1, 2, 0, 2, 3])

    def test_validate_mesh_catches_corruption(self):
        pos = np.zeros((3, 3), np.float32)
        md = MeshData(name='bad', positions=pos)
        md.submeshes = [{'topology': 0, 'indices': np.array([0, 1, 5], np.uint32), 'first_vertex': 0, 'vertex_count': 3}]
        self.assertTrue(any('out of range' in p for p in validate_mesh(md)))
        pos[0, 0] = np.nan
        self.assertTrue(any('non-finite' in p for p in validate_mesh(md)))

    def test_rest_pose_skinning_is_identity(self):
        from tests.synth import make_package
        with tempfile.TemporaryDirectory() as d:
            m = make_package(d)
            f = next(iter(m['meshes'].values()))['file']
            hdr, arrs = store.unpack_mesh((Path(d) / f).read_bytes())
            nodes = m['prefab']['nodes']
            world = rig.world_matrices(nodes)
            inv = np.linalg.inv(world[1])
            bw = np.stack([inv @ world[2], inv @ world[3]])
            sk = rig.skin_vertices(arrs['positions'].astype(float), arrs['bone_indices'].astype(int), arrs['bone_weights'].astype(float), bw,
                                   arrs['bind_poses'].astype(float), np.eye(4))
            self.assertLess(np.abs(sk - arrs['positions']).max(), 1e-6)


class Curves(unittest.TestCase):
    def test_hermite_endpoints_and_flat_tangents(self):
        keys = [{'t': 0.0, 'v': 0.0, 'i': 0.0, 'o': 0.0}, {'t': 1.0, 'v': 1.0, 'i': 0.0, 'o': 0.0}]
        v = curves.eval_scalar(keys, [0.0, 0.5, 1.0, 2.0])
        self.assertTrue(np.allclose(v, [0.0, 0.5, 1.0, 1.0]))

    def test_infinite_slope_is_stepped(self):
        keys = [{'t': 0.0, 'v': 0.0, 'i': 0.0, 'o': float('inf')}, {'t': 1.0, 'v': 5.0, 'i': 0.0, 'o': 0.0}]
        self.assertEqual(curves.eval_scalar(keys, [0.99])[0], 0.0)

    def test_linear_slopes_reproduce_line(self):
        keys = [{'t': 0.0, 'v': 0.0, 'i': 2.0, 'o': 2.0}, {'t': 1.0, 'v': 2.0, 'i': 2.0, 'o': 2.0}]
        self.assertTrue(np.allclose(curves.eval_scalar(keys, [0.25, 0.75]), [0.5, 1.5]))


class Animation(unittest.TestCase):
    CLIP = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!74 &7400000
AnimationClip:
  m_Name: Test
  m_Legacy: 0
  m_SampleRate: 30
  m_WrapMode: 0
  m_RotationCurves: []
  m_EulerCurves: []
  m_PositionCurves:
  - curve:
      m_Curve:
      - {time: 0, value: {x: 0, y: 1, z: 0}, inSlope: {x: 0, y: 0, z: 0}, outSlope: {x: 0, y: 0, z: 0}, tangentMode: 0, weightedMode: 0}
      - {time: 2, value: {x: 0, y: 3, z: 0}, inSlope: {x: 0, y: 0, z: 0}, outSlope: {x: 0, y: 0, z: 0}, tangentMode: 0, weightedMode: 0}
    path: path_0x11111111_abc
  m_ScaleCurves: []
  m_FloatCurves: []
  m_PPtrCurves: []
  m_Events:
  - {time: 1, functionName: Hit, data: '', floatParameter: 0, intParameter: 7, objectReferenceParameter: {fileID: 0}, messageOptions: 0}
  m_AnimationClipSettings: {m_StartTime: 0, m_StopTime: 2, m_LoopTime: 1}
"""

    def test_parse_clip_and_hashed_path_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'Test.anim'
            p.write_text(self.CLIP, encoding='utf-8')
            clip = A.parse_clip(p)
            self.assertEqual((clip['name'], clip['length'], clip['loop']), ('Test', 2.0, True))
            self.assertEqual(clip['events'][0]['function'], 'Hit')
            target = 'bone_root/bone_ear'
            crc = zlib.crc32(target.encode()) & 0xFFFFFFFF
            clip['positions'][0]['path'] = f'path_0x{crc:X}_zzz'
            self.assertEqual(A.resolve_hashed_paths(clip, ['bone_root', target]), 1)
            self.assertEqual(clip['positions'][0]['path'], target)
            clip['positions'][0]['path'] = 'path_0xDEADBEEF_zzz'
            self.assertEqual(A.resolve_hashed_paths(clip, [target]), 0)          # foreign bone stays unresolved

    def test_empty_path_is_normalised(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'T.anim'
            p.write_text(self.CLIP.replace('path: path_0x11111111_abc', 'path:'), encoding='utf-8')
            self.assertEqual(A.parse_clip(p)['positions'][0]['path'], '')


class Protocol(unittest.TestCase):
    def test_unknown_op_and_bad_args(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            protocol.handle({'id': '1', 'op': 'nope'}, 'out')
            protocol.handle({'id': '2', 'op': 'scan', 'args': {'bogus': 1}}, 'out')
        lines = [json.loads(l) for l in buf.getvalue().splitlines()]
        self.assertEqual(lines[0]['event'], 'error')
        self.assertEqual(lines[1]['event'], 'error')
        self.assertIn('bad arguments', lines[1]['error'])


if __name__ == '__main__':
    unittest.main()


class Locking(unittest.TestCase):
    def test_second_holder_is_refused_and_stale_lock_is_taken_over(self):
        from sbconv.safefs import RunLock
        with tempfile.TemporaryDirectory() as d:
            a = RunLock(d)
            with a:
                with self.assertRaises(RuntimeError):
                    RunLock(d).__enter__()
            self.assertFalse((Path(d) / '.sbconv.lock').exists())
            (Path(d) / '.sbconv.lock').write_text('999999')          # PID that does not exist
            with RunLock(d):
                pass
