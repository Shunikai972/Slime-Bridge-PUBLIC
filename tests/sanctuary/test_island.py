import math
import random
import unittest

from bridge.sanctuary import island as I


class QuaternionTests(unittest.TestCase):
    def test_yaw_rotation_matches_unity_convention(self):
        # a Unity yaw of 90 degrees turns +Z toward +X
        x, y, z = I.q_rotate(I.yaw_q(90.0), [0.0, 0.0, 1.0])
        self.assertAlmostEqual(x, 1.0, places=6)
        self.assertAlmostEqual(z, 0.0, places=6)

    def test_composition_adds_yaws(self):
        q = I.q_mul(I.yaw_q(30.0), I.yaw_q(60.0))
        x, _, z = I.q_rotate(q, [0.0, 0.0, 1.0])
        self.assertAlmostEqual(x, 1.0, places=6)
        self.assertAlmostEqual(z, 0.0, places=6)


class PlacementTests(unittest.TestCase):
    def test_statue_slots_are_spread_apart_and_keep_the_entrance_open(self):
        points = I.statue_slots(17, 13.5, 23.0, 0.82, random.Random(1), gap=0)
        self.assertEqual(len(points), 17)
        for du, dv in points:
            self.assertGreaterEqual(math.hypot(du, dv), 13.5 - 1e-6)
            self.assertLessEqual(math.hypot(du / 0.82, dv), 23.0 + 1e-6)
            # angle 0 = away from the bridge: the bridge side (toward -u) is never used
            self.assertGreater(du, -23.0 * math.cos(math.radians(38.0)) * 0.82 - 1e-6)

    def test_best_candidate_sampling_is_deterministic(self):
        a = I.statue_slots(10, 13.5, 23.0, 0.82, random.Random(5))
        b = I.statue_slots(10, 13.5, 23.0, 0.82, random.Random(5))
        self.assertEqual(a, b)

    def test_footprints_detect_overlap_and_ignore_listed_kinds(self):
        foot = I.Footprints()
        foot.add(0.0, 0.0, 1.0, 'tree')
        self.assertFalse(foot.clear(1.5, 0.0, 1.0))
        self.assertTrue(foot.clear(1.5, 0.0, 1.0, ignore=('tree',)))
        self.assertTrue(foot.clear(3.0, 0.0, 1.0))
        self.assertEqual(len(foot.overlaps(1.5, 0.0, 1.0, ('tree',))), 1)

    def test_segment_distance(self):
        self.assertAlmostEqual(I.segment_distance(0.0, 1.0, -5.0, 0.0, 5.0, 0.0), 1.0)
        self.assertAlmostEqual(I.segment_distance(8.0, 0.0, -5.0, 0.0, 5.0, 0.0), 3.0)      # beyond the end: distance to the endpoint
        self.assertAlmostEqual(I.segment_distance(1.0, 1.0, 2.0, 2.0, 2.0, 2.0), math.sqrt(2))


if __name__ == '__main__':
    unittest.main()
