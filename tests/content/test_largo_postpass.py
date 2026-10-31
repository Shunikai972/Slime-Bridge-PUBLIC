import unittest

import numpy as np

from tools.largo_models import postpass


def _model():
    bones = [{'name': 'ROOT', 'parent': -1, 'p': [0.0, 0.0, 0.0], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]},
             {'name': 'BODY', 'parent': 0, 'p': [0.0, 0.2, 0.0], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]},
             {'name': 'EAR', 'parent': 1, 'p': [0.0, 0.6, 0.0], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]}]
    rng = np.random.default_rng(1)
    verts = rng.uniform([-0.25, 0.0, -0.25], [0.25, 0.5, 0.25], size=(200, 3)).round(4).tolist()
    ear = [[0.0, 0.6 + 0.01 * i, 0.0] for i in range(20)]
    world = postpass.worlds(bones)
    binds = [[float(v) for v in np.linalg.inv(w).reshape(-1)] for w in world]
    weights = [[1.0, 0, 0, 0, 1, 1, 1, 1] for _ in verts] + [[1.0, 0, 0, 0, 2, 2, 2, 2] for _ in ear]
    clip = {'name': 'Idle v4', 'duration': 1.0, 'loop': True, 'sample_rate': 2.0, 'times': [0.0, 0.5], 'channels': [
        {'path': 'ROOT/BODY', 'property': 'local_position', 'values': [[0.0, 0.2, 0.0], [0.0, 0.3, 0.0]]},
        {'path': 'ROOT/BODY/EAR', 'property': 'local_rotation', 'values': [[0.0, 0.0, 0.0, 1.0], [0.0, 0.0, 0.2588, 0.9659]]}]}
    return {'skeleton': {'bone_count': 3, 'bones': bones}, 'source_animation': {'default_clip': 'Idle v4', 'clips': [clip]},
            'skinned_renderers': [{'role': 'attachment', 'path': 'x/ring', 'mesh_name': 'ring', 'vertices': [[0, 0, 0]],
                                   'bone_weights': [[1, 0, 0, 0, 0, 0, 0, 0]], 'skin_bones': ['ROOT', 'BODY', 'EAR'], 'bind_poses': binds},
                                  {'role': 'body', 'path': 'x/Mesh', 'mesh_name': 'Mesh', 'vertices': verts + ear,
                                   'bone_weights': weights, 'skin_bones': ['ROOT', 'BODY', 'EAR'], 'bind_poses': binds}]}


def _skin(model, sample=None):
    bones = [dict(b) for b in model['skeleton']['bones']]
    if sample is not None:
        paths = postpass._paths(bones)
        for ch in model['source_animation']['clips'][0]['channels']:
            b = bones[paths.index(ch['path'])]
            b[{'local_position': 'p', 'local_rotation': 'q', 'local_scale': 's'}[ch['property']]] = ch['values'][sample]
    world = postpass.worlds(bones)
    index = {b['name']: i for i, b in enumerate(bones)}
    part = next(p for p in model['skinned_renderers'] if p['role'] == 'body')
    mats = [world[index[n]] @ np.array(bp).reshape(4, 4) for n, bp in zip(part['skin_bones'], part['bind_poses'])]
    return np.array([sum(row[k] * (mats[int(row[4 + k])] @ np.array(list(v) + [1.0])) for k in range(4))[:3]
                     for v, row in zip(part['vertices'], part['bone_weights'])])


class LargoPostpassTests(unittest.TestCase):
    def test_body_gets_the_games_size_and_physics_rig_and_the_rest_keeps_its_animation(self):
        model = _model()
        size = postpass.normalise_to_body(model)
        self.assertEqual(size['body_bone'], 'BODY')
        body = next(p for p in model['skinned_renderers'] if p['role'] == 'body')
        verts = np.array(body['vertices'])[:200]
        width = np.percentile(verts[:, 0], 98) - np.percentile(verts[:, 0], 2)
        self.assertAlmostEqual(width, postpass.BODY_WIDTH, places=2)
        rest = _skin(model)
        np.testing.assert_allclose(rest, body['vertices'], atol=1e-4)      # bind poses follow the rescale
        bounced = _skin(model, 1)                                         # BODY rises with the authored idle
        result = postpass.add_jiggle(model)
        self.assertEqual(result['clips_driving_core'], 1)
        np.testing.assert_allclose(_skin(model), rest, atol=1e-4)
        np.testing.assert_allclose(_skin(model, 1), bounced, atol=1e-4)  # ...and the physics body rises with it
        names = [b['name'] for b in model['skeleton']['bones']]
        core = model['skeleton']['bones'][names.index('bone_core')]
        self.assertEqual(core['parent'], -1, 'the spring runs in the root frame: bone_core must not rotate with the animation')
        for side in ('bac', 'bot', 'fro', 'lef', 'rig', 'top'):
            self.assertIn(f'bone_jiggle_{side}', names)
        channels = model['source_animation']['clips'][0]['channels']
        self.assertEqual(sorted({c['path'] for c in channels}), ['ROOT/BODY', 'ROOT/BODY/EAR', 'bone_core'])
        self.assertEqual(model['skinned_renderers'][0]['mesh_name'], 'body')
        for part in model['skinned_renderers']:
            self.assertEqual(len(part['skin_bones']), len(part['bind_poses']))
        for row in model['skinned_renderers'][0]['bone_weights']:
            self.assertAlmostEqual(sum(row[:4]), 1.0, places=5)
        self.assertEqual(postpass.add_jiggle(model)['jiggle'], 'present')


if __name__ == '__main__':
    unittest.main()
