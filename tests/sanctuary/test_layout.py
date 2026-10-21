import json
import math
from pathlib import Path
import shutil
import unittest
import uuid

from bridge.sanctuary import layout as L
from tests.sanctuary.fixtures import copy_kit

ROOT = Path(__file__).resolve().parents[2]


class LayoutTests(unittest.TestCase):
    def build(self, **config):
        return L.build_layout(copy_kit(), config)

    def test_is_deterministic(self):
        self.assertEqual(L.layout_bytes(self.build()), L.layout_bytes(self.build()))

    def test_required_optional_hidden_and_excluded(self):
        layout = self.build()
        by = {s['slime']: s for s in layout['statues']}
        self.assertNotIn('QUICKSILVER', by)
        self.assertNotIn('TARR', by)
        self.assertFalse(by['GOLD']['required'])
        self.assertTrue(by['GOLD']['optional'])
        for key in ('PUDDLE', 'SABER', 'FIRE'):
            self.assertTrue(by[key]['hidden'], key)
            self.assertEqual(by[key]['placement'], 'alcove')
        self.assertEqual(set(layout['requirement_manifest']['required']),
                         {'PINK', 'BOOM', 'ROCK', 'TABBY', 'CRYSTAL', 'FIRE', 'PUDDLE', 'SABER', 'HUNTER'})
        self.assertEqual(layout['requirement_manifest']['dynamic'], False)

    def test_synthesized_statues_clone_the_pink_template_and_carry_their_plort_colours(self):
        crystal = next(s for s in self.build()['statues'] if s['slime'] == 'CRYSTAL')
        self.assertEqual(crystal['status'], 'synthesized')
        self.assertEqual(crystal['statue_material'], 'objPlortStatuePink')
        self.assertEqual(crystal['template']['construct'], 'PlortSwitch_pink')
        self.assertEqual(crystal['tint']['top'], [1.0, 0.4, 0.6, 1.0])
        self.assertEqual(crystal['detail_texture'], 'envRuinStone_crystal')

    def test_statues_face_the_portal_and_never_overlap(self):
        layout = self.build()
        centre = layout['portal']['position']
        for s in layout['statues']:
            facing = [math.sin(math.radians(s['yaw'])), math.cos(math.radians(s['yaw']))]
            to_centre = [centre[0] - s['position'][0], centre[2] - s['position'][2]]
            dot = (facing[0] * to_centre[0] + facing[1] * to_centre[1]) / math.hypot(*to_centre)
            self.assertGreater(dot, 0.999, s['id'])
        for a in layout['statues']:
            for b in layout['statues']:
                if a['id'] < b['id']:
                    self.assertGreaterEqual(math.dist(a['position'], b['position']), 3.4)

    def test_ring_keeps_an_entrance_gap_toward_the_bridge(self):
        layout = self.build()
        forward, centre = layout['frame']['forward'], layout['portal']['position']
        entrance = [centre[0] - forward[0] * 8.6, centre[2] - forward[2] * 8.6]
        ring = [s for s in layout['statues'] if s['placement'] == 'ring']
        self.assertGreater(min(math.hypot(s['position'][0] - entrance[0], s['position'][2] - entrance[1]) for s in ring), 4.0)

    def test_statues_are_spread_over_the_island_and_the_walkway_is_short(self):
        # The statues are dispersed over the garden (clearings 12-23 m from the portal), never inside the portal court or its
        # henge of pillars, and the walkway stays one plank long.
        layout = self.build()
        centre = layout['portal']['position']
        radii = {s['id']: math.hypot(s['position'][0] - centre[0], s['position'][2] - centre[2]) for s in layout['statues']}
        self.assertGreaterEqual(min(radii.values()), 12.0)
        self.assertLessEqual(max(radii.values()), 24.0)
        self.assertGreater(max(radii.values()) - min(radii.values()), 6.0)          # genuinely spread, not one ring
        self.assertGreaterEqual(min(math.dist(a['position'], b['position']) for a in layout['statues'] for b in layout['statues']
                                    if a['id'] < b['id']), 6.3)
        self.assertEqual(len([p for p in layout['pieces'] if p['id'].startswith('bridge.plank')]), 1)
        plaza_u = (centre[0] - layout['frame']['origin'][0]) * layout['frame']['forward'][0] +                   (centre[2] - layout['frame']['origin'][2]) * layout['frame']['forward'][2]
        self.assertLess(plaza_u, 32.0)                        # the island is close to the dead end, clear of the cliff behind it
        self.assertTrue(any(p['id'] == 'bridge.depositor_pad' for p in layout['pieces']))

    def test_nothing_grows_or_stands_through_a_statue(self):
        layout = self.build()
        solid = [p for p in layout['pieces'] if p['id'].startswith(('plaza.pillar', 'flora.rock', 'flora.nook')) or
                 ('trunk' in p['name'].lower() and p['collider'] != 'none')]
        self.assertTrue(solid)
        for s in layout['statues']:
            for p in solid:
                self.assertGreater(math.dist([s['position'][0], s['position'][2]], [p['position'][0], p['position'][2]]), 1.8, (s['id'], p['id']))

    def test_the_garden_exists_and_stays_out_of_the_entrance_corridor(self):
        layout = self.build()
        flora = [p for p in layout['pieces'] if p['group'] == 'flora']
        self.assertGreater(len(flora), 20)
        self.assertTrue(any('tree' in p['id'] for p in flora))
        frame = layout['frame']
        plaza_u = None
        for p in flora:
            dx, dz = p['position'][0] - frame['origin'][0], p['position'][2] - frame['origin'][2]
            u = dx * frame['forward'][0] + dz * frame['forward'][2]
            v = dx * frame['right'][0] + dz * frame['right'][2]
            self.assertFalse(abs(v) < 4.0 and 4.0 < u < 22.0, p['id'])          # the walk from the bridge to the court stays clear
        bare = self.build(flora_density=0)
        self.assertFalse([p for p in bare['pieces'] if p['group'] == 'flora' and not p['id'].startswith('flora.nook')])

    def test_no_tall_poles_hide_the_depositor_and_the_pieces_have_a_reveal_phase(self):
        layout = self.build()
        deck = layout['deck_top_y']
        for p in layout['pieces']:
            self.assertIn(p['phase'], ('rise', 'span', 'court', 'flora', 'statue'))
            if p['name'] == 'woodPosts01':
                # top of the post = origin + (centre + extent) * scale: only a short bollard may show above the deck
                top = p['position'][1] + (4.05 + 4.7) * p['scale'][1]
                self.assertLess(top, deck + 1.2, p['id'])
        self.assertEqual(next(p for p in layout['pieces'] if p['id'] == 'island.base')['phase'], 'rise')

    def test_gold_depositor_is_a_native_slime_figure_with_the_h_tablet(self):
        gate = self.build()['gate']
        self.assertEqual(gate['figure']['kind'], 'native_slime')
        self.assertEqual(gate['figure']['slime'], 'GOLD')
        tablet = gate['tablet']
        text = ' '.join(tablet['lines']).lower()
        self.assertEqual(tablet['title'], 'H')
        self.assertIn('price', text)
        self.assertIn('universe', text)
        self.assertTrue(tablet['signature'].endswith('H'))

    def test_statue_arrangement_is_reproducible_and_depends_on_the_seed(self):
        a, b = self.build(), self.build()
        self.assertEqual(L.layout_bytes(a), L.layout_bytes(b))
        other = self.build(seed=7)
        self.assertNotEqual([s['position'] for s in a['statues']], [s['position'] for s in other['statues']])

    def test_bridge_planks_start_at_the_anchor_and_run_along_the_heading(self):
        layout = self.build(bridge_planks=3)
        planks = [p for p in layout['pieces'] if p['id'].startswith('bridge.plank')]
        self.assertEqual(len(planks), 3)
        origin, forward, length = layout['frame']['origin'], layout['frame']['forward'], layout['plank_length']
        for i, plank in enumerate(planks):
            along = (plank['position'][0] - origin[0]) * forward[0] + (plank['position'][2] - origin[2]) * forward[2]
            self.assertAlmostEqual(along, length * (i + 0.5), places=2)
            yaw = math.radians(plank['yaw'])           # the plank's long axis (local +X) must point along the heading
            self.assertAlmostEqual(math.cos(yaw), forward[0], places=3)
            self.assertAlmostEqual(-math.sin(yaw), forward[2], places=3)
            self.assertEqual(plank['position'][1], planks[0]['position'][1])

    def test_gate_modes_and_cost(self):
        self.assertTrue(self.build()['gate']['enabled'])
        self.assertEqual(self.build()['gate']['cost'], 10)
        always = self.build(sr1_bridge_access_mode='ALWAYS', sr1_gold_plort_gate_cost=25)
        self.assertFalse(always['gate']['enabled'])
        self.assertEqual(always['gate']['cost'], 25)
        self.assertEqual(always['gate']['plort'], 'GOLD_PLORT')

    def test_bad_settings_are_refused(self):
        for bad in ({'sr1_bridge_access_mode': 'NEVER'}, {'sr1_gold_plort_gate_cost': 0}, {'sr1_gold_plort_gate_cost': '10'},
                    {'bridge_planks': 0}, {'hidden_radius': 5}, {'mystery': 1}, {'dynamic_statue_requirements': 'no'},
                    {'island_ground': 'lava'}, {'flora_density': 3}, {'seed': 'x'}):
            with self.assertRaises(L.LayoutError, msg=str(bad)):
                self.build(**bad)

    def test_missing_native_piece_fails_closed(self):
        kit = copy_kit()
        kit['pieces'] = [p for p in kit['pieces'] if p['name'] != 'woodPlat01']
        with self.assertRaises(L.LayoutError):
            L.build_layout(kit)

    def test_layout_names_native_meshes_and_materials_only(self):
        layout = self.build()
        text = json.dumps(layout)
        for forbidden in ('"vertices"', '"triangles"', '"pixels"'):
            self.assertNotIn(forbidden, text)
        for piece in layout['pieces']:
            self.assertTrue(piece['mesh'] and piece['materials'])

    def test_publish_is_fresh_only_and_confined_to_lab(self):
        layout = self.build()
        out = ROOT / 'Lab' / 'GeneratedContent' / ('sanctuary-test-' + uuid.uuid4().hex[:8])
        try:
            self.assertTrue(L.publish_layout(layout, out)['ok'])
            self.assertEqual(json.loads((out / 'sanctuary-layout.json').read_text(encoding='utf-8'))['schema'], L.LAYOUT_SCHEMA)
            with self.assertRaises(L.LayoutError):
                L.publish_layout(layout, out)
        finally:
            shutil.rmtree(out, ignore_errors=True)
        with self.assertRaises(L.LayoutError):
            L.publish_layout(layout, ROOT / 'elsewhere' / 'layout')


if __name__ == '__main__':
    unittest.main()
