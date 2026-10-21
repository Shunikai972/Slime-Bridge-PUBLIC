import json
import tempfile
import unittest
from pathlib import Path

from tests.synth import make_package
from sbconv.export.gltf import export_glb
from sbconv.verify.glb_roundtrip import verify_glb
from sbconv.verify.offline import verify_package


class OfflineVerification(unittest.TestCase):
    def test_clean_package_passes_including_rest_pose(self):
        with tempfile.TemporaryDirectory() as d:
            make_package(d)
            R, _ = verify_package(d)
            self.assertTrue(R.passed, R.summary()['failed'])
            self.assertTrue(any(c['id'] == 'rig.rest_pose_skinning' and c['ok'] for c in R.checks))

    def test_wrong_bind_pose_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            make_package(d, bind_offset=0.3)
            R, _ = verify_package(d)
            self.assertFalse(R.passed)
            self.assertTrue(any('rest' in c['id'] for c in R.summary()['failed']))

    def test_tampered_file_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            m = make_package(d)
            mesh = Path(d) / next(k for k in m['files'] if k.endswith('.sbmesh'))
            mesh.write_bytes(mesh.read_bytes()[:-4] + b'\xff\xff\xff\xff')
            R, _ = verify_package(d)
            self.assertFalse(R.passed)
            self.assertTrue(any(c['id'] == 'files.integrity' for c in R.summary()['failed']))

    def test_missing_texture_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            m = make_package(d)
            mat = next(iter(m['materials'].values()))
            mat['textures'] = {'_Diffuse': {'texture': 'f#99', 'file': None, 'scale': [1, 1], 'offset': [0, 0]}}
            (Path(d) / 'species.json').write_text(json.dumps(m), encoding='utf-8')
            R, _ = verify_package(d)
            self.assertTrue(any(c['id'] == 'materials.textures_present' for c in R.summary()['failed']))


class GlbExport(unittest.TestCase):
    def test_export_and_reimport_match_source_geometry(self):
        with tempfile.TemporaryDirectory() as d:
            make_package(d)
            out = Path(d) / 'o.glb'
            info = export_glb(d, out)
            self.assertEqual((info['skins'], info['animations']), (1, 1))
            res = verify_glb(d, out)
            self.assertTrue(res and all(r['ok'] for r in res), res)
            self.assertTrue(all(r['winding_ok'] for r in res))


if __name__ == '__main__':
    unittest.main()


class BoneSlotResolution(unittest.TestCase):
    def test_slots_follow_bind_poses_when_declared_list_has_a_gap(self):
        import numpy as np
        from sbconv.extract.appearance import resolve_bone_slots
        # four bones at distinct translations; the mesh uses bones 0,1,3 (bone 2 declared but unused)
        rel = {i: np.array([[1, 0, 0, i], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float) for i in range(4)}
        bind = [np.linalg.inv(rel[i]) for i in (0, 1, 3)]
        slots, method = resolve_bone_slots([0, 1, 2, 3], bind, rel)
        self.assertEqual((slots, method), ([0, 1, 3], 'bindpose_matched'))
        slots, method = resolve_bone_slots([0, 1, 3], bind, rel)
        self.assertEqual((slots, method), ([0, 1, 3], 'declared'))
        self.assertEqual(resolve_bone_slots([0, 1], bind, rel)[0], None)          # a needed bone is not declared at all


class UvRemap(unittest.TestCase):
    def test_uv0_replaced_by_uv1_and_lighting_uv_kept(self):
        import numpy as np
        from sbconv.ir.store import remap_uv0_from_uv1, unpack_mesh
        with tempfile.TemporaryDirectory() as d:
            m = make_package(d)
            f = next(v['file'] for v in m['meshes'].values())
            raw = (Path(d) / f).read_bytes()
            hdr, arrs = unpack_mesh(raw)
            arrs_uv1 = np.array(arrs['uv1'])
            out = remap_uv0_from_uv1(raw)
            h2, a2 = unpack_mesh(out)
            self.assertTrue(np.array_equal(a2['uv0'], arrs_uv1))
            self.assertTrue(np.array_equal(a2['uv3'], arrs['uv0']))
            self.assertEqual(h2['uv_remap']['uv0'], 'from uv1')
            self.assertTrue(np.array_equal(a2['positions'], arrs['positions']))
