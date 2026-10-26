import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools import cache_audit


def make_cache(root, **edits):
    folder = Path(root) / 'cache'
    folder.mkdir()
    part = {'role': 'body', 'enabled': True, 'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], 'normals': [[0, 0, 1]] * 3,
            'uv': [[0, 0]] * 3, 'triangles': [[[0, 1, 2]]], 'materials': [{'textures': []}],
            'skin_bones': ['bone_root'], 'bind_poses': [[0] * 16], 'root_bone': 'bone_root',
            'bone_weights': [[1.0, 0, 0, 0, 0, 0, 0, 0]] * 3}
    part.update(edits)
    model = {'schema': 'bridge-native-mesh', 'source_game': 'SR1', 'skeleton': {'bones': [{'name': 'bone_root'}]}, 'skinned_renderers': [part],
             'static_renderers': []}
    (folder / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    digest = hashlib.sha256((folder / 'model.json').read_bytes()).hexdigest()
    (folder / 'cache-manifest.json').write_text(json.dumps({'files': {'model.json': digest}}), encoding='utf-8')
    return folder


class CacheAuditTests(unittest.TestCase):
    def audit(self, **edits):
        with tempfile.TemporaryDirectory() as tmp:
            return cache_audit.audit_cache(make_cache(tmp, **edits))

    def test_a_sound_cache_passes(self):
        self.assertEqual(self.audit(), [])

    def test_each_kind_of_damage_is_named(self):
        cases = {'indexes outside': {'triangles': [[[0, 1, 7]]]},
                 'non-finite': {'vertices': [[float('nan'), 0, 0], [1, 0, 0], [0, 1, 0]]},
                 'bind poses': {'bind_poses': []},
                 'bone index outside': {'bone_weights': [[1.0, 0, 0, 0, 5, 0, 0, 0]] * 3},
                 'material slots': {'materials': []},
                 'root bone': {'root_bone': 'nope'}}
        for needle, edit in cases.items():
            with self.subTest(needle):
                self.assertTrue(any(needle in p for p in self.audit(**edit)), needle)

    def test_a_changed_file_fails_its_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_cache(tmp)
            (folder / 'model.json').write_text('{}', encoding='utf-8')
            self.assertTrue(any('hash mismatch' in p for p in cache_audit.audit_cache(folder)))


if __name__ == '__main__':
    unittest.main()
