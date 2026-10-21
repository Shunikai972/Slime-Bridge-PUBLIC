"""SR1 sanctuary kit extractor: pure helpers always, the real-scene extraction only when a LAB SR1 copy exists."""
import json
import math
from pathlib import Path
import shutil
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bridge.content import sr1_sanctuary as K  # noqa: E402

SR1_DATA = ROOT / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data'


class MatrixTests(unittest.TestCase):
    def test_trs_round_trip(self):
        angle = math.radians(67.8)
        rotation = [0.0, math.sin(angle / 2), 0.0, math.cos(angle / 2)]
        matrix = K.trs_matrix([1.0, 2.0, 3.0], rotation, [1.5, 2.0, 0.5])
        position, quaternion, scale = K.decompose(matrix)
        self.assertEqual([round(v, 6) for v in position], [1.0, 2.0, 3.0])
        self.assertEqual([round(v, 6) for v in scale], [1.5, 2.0, 0.5])
        self.assertAlmostEqual(K.heading_y(quaternion), 67.8, places=4)

    def test_parent_child_composition_places_the_child(self):
        parent = K.trs_matrix([10.0, 0.0, 0.0], [0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4)], [1, 1, 1])  # +90 deg yaw
        child = K.trs_matrix([0.0, 0.0, 2.0], [0.0, 0.0, 0.0, 1.0], [1, 1, 1])
        position, _, _ = K.decompose(K._mul(parent, child))
        self.assertEqual([round(v, 6) for v in position], [12.0, 0.0, 0.0])      # local +Z of a +90 yaw is world +X

    def test_degenerate_scale_is_refused(self):
        with self.assertRaises(ValueError):
            K.decompose(K.trs_matrix([0, 0, 0], [0, 0, 0, 1], [0, 1, 1]))


class CatalogTests(unittest.TestCase):
    PLORTS = {'PINK': 6, 'GOLD': 7, 'CRYSTAL': 20, 'QUICKSILVER': 30}
    SLIMES = {'PINK': 3, 'GOLD': 11, 'CRYSTAL': 21, 'QUICKSILVER': 31, 'TARR': 10, 'GLITCH': 40, 'LUCKY': 41}

    def catalog(self):
        statues = [{'construct': 'PlortSwitch_pink', 'catch_name': 'PINK_PLORT'}]
        materials = {'objPlortStatuePink': {}, 'plortPinkBase': {}, 'plortGoldBase': {}, 'plortCrystalBase': {}}
        return {row['slime']: row for row in K.build_catalog(self.PLORTS, self.SLIMES, statues, materials)}

    def test_native_synthesized_excluded_and_decorative(self):
        catalog = self.catalog()
        self.assertEqual(catalog['PINK']['status'], 'native')
        self.assertEqual(catalog['PINK']['statue_material'], 'objPlortStatuePink')
        self.assertEqual(catalog['GOLD']['status'], 'synthesized')
        self.assertEqual(catalog['GOLD']['template'], 'PlortSwitch_pink')
        self.assertEqual(catalog['QUICKSILVER']['status'], 'excluded')
        self.assertEqual(catalog['TARR']['status'], 'excluded')
        self.assertEqual(catalog['GLITCH']['status'], 'excluded')

    def test_every_slime_is_classified_exactly_once(self):
        catalog = K.build_catalog(self.PLORTS, self.SLIMES, [], {})
        self.assertEqual(len({r['slime'] for r in catalog}), len(catalog))
        self.assertEqual({r['slime'] for r in catalog}, set(self.PLORTS) | set(self.SLIMES))

    def test_slime_without_plort_is_never_a_required_slot(self):
        rows = {r['slime']: r for r in K.build_catalog({'PINK': 6}, {'PINK': 3, 'SOMEWHERE': 99}, [], {})}
        self.assertEqual(rows['SOMEWHERE']['status'], 'decorative')
        self.assertIsNone(rows['SOMEWHERE']['plort_id'])


class ValidationTests(unittest.TestCase):
    def kit(self):
        return {'schema': K.KIT_SCHEMA, 'schema_version': K.KIT_VERSION,
                'statues': [{'construct': 'PlortSwitch_pink', 'nodes': [{'name': 'Statue'}, {'name': 'Eyes'}]}],
                'walkway': {'end_pieces': [{'distance': 2.0}]}}

    def test_good_kit_passes(self):
        self.assertIs(K.validate_kit(self.kit())['schema'], K.KIT_SCHEMA)

    def test_fails_closed(self):
        for mutate in (lambda k: k.update(schema='other'), lambda k: k.update(statues=[]),
                       lambda k: k['statues'][0]['nodes'].pop(), lambda k: k['walkway'].update(end_pieces=[]),
                       lambda k: k['walkway']['end_pieces'][0].update(distance=99)):
            kit = self.kit()
            mutate(kit)
            with self.assertRaises(ValueError):
                K.validate_kit(kit)

    def test_output_outside_lab_is_refused(self):
        with self.assertRaises(ValueError):
            K.extract_kit(SR1_DATA, ROOT / 'not-lab' / 'kit')


@unittest.skipUnless((SR1_DATA / 'level3').is_file(), 'needs a LAB SR1 copy')
class RealSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = ROOT / 'Lab' / 'RuntimeArtifacts' / ('sanctuary-kit-test-' + uuid.uuid4().hex[:8])
        result = K.extract_kit(SR1_DATA, cls.out)
        cls.kit = json.loads((cls.out / 'sanctuary-kit.json').read_text(encoding='utf-8'))
        cls.result = result

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_native_statues_and_catch_ids_come_from_the_game(self):
        constructs = {s['construct']: s for s in self.kit['statues']}
        self.assertEqual(constructs['PlortSwitch_pink']['catch_name'], 'PINK_PLORT')
        self.assertEqual(constructs['PlortSwitch_boom']['catch_name'], 'BOOM_PLORT')
        names = {n['name'] for n in constructs['PlortSwitch_pink']['nodes']}
        self.assertTrue({'Statue', 'Eyes', 'Activate Toggle'} <= names)
        meshes = {n['render']['mesh'] for n in constructs['PlortSwitch_pink']['nodes'] if n['render']}
        self.assertEqual(meshes, {'ruinStatue03', 'ruinStatue03_eyes', 'plort'})

    def test_catalog_covers_normal_slimes_and_excludes_event_ones(self):
        rows = {r['slime']: r for r in self.kit['statue_catalog']}
        for slime in ('PINK', 'ROCK', 'TABBY', 'QUANTUM', 'MOSAIC'):
            self.assertEqual(rows[slime]['status'], 'native', slime)
        for slime in ('CRYSTAL', 'HUNTER', 'SABER', 'FIRE', 'PUDDLE', 'GOLD'):
            self.assertEqual(rows[slime]['status'], 'synthesized', slime)
        self.assertEqual(rows['QUICKSILVER']['status'], 'excluded')
        self.assertTrue(all(rows[s]['plort_display_material'] for s in rows if rows[s]['status'] in ('native', 'synthesized')))

    def test_walkway_end_matches_the_user_anchor(self):
        end = self.kit['walkway']['end_pieces'][0]
        self.assertLess(end['distance'], 5)
        self.assertTrue(end['name'].startswith('woodPlat'))
        self.assertEqual(self.kit['walkway']['anchor']['heading_y'], 67.8)

    def test_kit_contains_descriptions_only_and_matches_its_hash(self):
        text = json.dumps(self.kit)
        for forbidden in ('"vertices"', '"triangles"', '"pixels"', '"png"'):
            self.assertNotIn(forbidden, text)
        manifest = json.loads((self.out / 'cache-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['files']['sanctuary-kit.json'], self.result['kit_sha256'])

    def test_existing_output_is_never_overwritten(self):
        with self.assertRaises(ValueError):
            K.extract_kit(SR1_DATA, self.out)


if __name__ == '__main__':
    unittest.main()
