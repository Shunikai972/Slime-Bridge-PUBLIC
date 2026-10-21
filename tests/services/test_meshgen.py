"""SlimeBridge gateway generator: parameters, gameplay contract, geometry validation, collision."""

import dataclasses
import functools
import math
import random
import unittest

from bridge.core import diagnostics as D
from bridge.meshgen import mesh as M
from bridge.meshgen import primitives as P
from bridge.meshgen import vec as V
from bridge.meshgen import volumes as B
from bridge.meshgen.palettes import PALETTES, SEMANTIC_MATERIALS
from bridge.meshgen.portal import (PARAM_LIMITS, PARAM_NAMES, REQUIRED_SOCKETS, SPAWN_CLEARANCE, VARIANTS,
                                   PortalParams, compute_layout, generate_portal, params_from_dict, surface_y,
                                   variant_params)

FAST = {'ring_segments': 32, 'bevel_segments': 2}


@functools.lru_cache(maxsize=None)
def model(variant='meadow', **overrides):
    return generate_portal(variant_params(variant, **overrides))


def fast(variant='meadow'):
    return model(variant, **FAST)


def codes(m):
    return {d['code'] for d in m.diagnostics}


class Params(unittest.TestCase):
    def test_defaults_and_every_variant_generate_cleanly(self):
        self.assertEqual(generate_portal().diagnostics, model().diagnostics)
        for name in VARIANTS:
            m = fast(name)
            self.assertTrue(m.ok, (name, m.diagnostics))
            self.assertEqual(m.diagnostics, [])
            self.assertIn(m.params.palette, PALETTES)
            self.assertEqual(m.metadata['variant'], name)

    def test_variants_only_change_parameters(self):
        for name, overrides in VARIANTS.items():
            self.assertTrue(set(overrides) <= set(PARAM_NAMES), name)
            self.assertEqual(variant_params(name), PortalParams(**dict(overrides, variant=name)))
        self.assertEqual(len({fast(v).params_hash for v in VARIANTS}), len(VARIANTS))

    def test_invalid_values_give_diagnostics_not_crashes_or_nan(self):
        cases = {
            'ring_inner_radius': (-1.0, 0.0, float('nan'), float('inf'), 1e9, 'big', True, None),
            'ring_segments': (0, -5, 10 ** 9, 2.5, float('nan')),
            'deck_width': (0, -2.0, float('-inf'), 1e300),
            'bevel_radius': (0.0, float('nan')),
            'lod_count': (0, 7),
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    m = generate_portal(dataclasses.replace(PortalParams(), **{name: value}))
                    self.assertFalse(m.ok)
                    self.assertEqual(m.lods, [])
                    self.assertTrue(codes(m) & {'PARAM_OUT_OF_RANGE', 'PARAM_NOT_FINITE', 'PARAM_TYPE'}, m.diagnostics)
                    self.assertTrue(all(d['subject'] == name for d in m.diagnostics))
                    for d in m.diagnostics:
                        self.assertEqual(D.validate(d), [])  # unified and JSON-safe (no NaN leaks)

    def test_untrusted_json_parameters(self):
        p, diags = params_from_dict({'ring_inner_radius': 2.2, 'teleport': 1})
        self.assertIsNone(p)
        self.assertEqual({d['code'] for d in diags}, {'PARAM_UNKNOWN'})
        p, diags = params_from_dict({'ring_inner_radius': 2.2})
        self.assertEqual((p.ring_inner_radius, diags), (2.2, []))
        self.assertEqual(codes(generate_portal({'palette': 'neon'})), {'PARAM_UNKNOWN_PALETTE'})
        self.assertEqual(codes(generate_portal({'variant': '../x'})), {'PARAM_INVALID_NAME'})
        self.assertEqual(codes(generate_portal([1, 2])), {'PARAMS_INVALID'})
        self.assertEqual(codes(generate_portal('garbage')), {'PARAMS_INVALID'})

    def test_cross_field_rules(self):
        self.assertIn('PARAM_BEVEL_TOO_LARGE', codes(generate_portal(PortalParams(bevel_radius=0.35))))
        self.assertIn('PARAM_DECK_BELOW_GROUND',
                      codes(generate_portal(PortalParams(deck_height=0.2, deck_thickness=0.4))))

    def test_seeded_fuzz_never_raises_and_valid_layouts_always_validate(self):
        rng = random.Random(7)
        base = PortalParams().to_dict()
        built = 0
        for _ in range(16):
            values = dict(base, ring_segments=12, bevel_segments=1, lod_count=1)
            for name, (kind, lo, hi) in PARAM_LIMITS.items():
                if kind == 'float' and not name.startswith(('min_', 'max_')):
                    values[name] = min(hi, max(lo, base[name] * (0.8 + 0.4 * rng.random())))
            m = generate_portal(values)
            if m.ok:
                built += 1
                self.assertEqual(m.diagnostics, [])
                for part in m.parts(0):
                    self.assertTrue(all(math.isfinite(c) for p in part.positions for c in p))
            else:  # only pre-geometry contract findings, never a post-generation failure
                self.assertEqual(m.lods, [])
                self.assertFalse({d['code'] for d in m.diagnostics} & {'GENERATION_FAILED', 'DECK_NOT_SUPPORTED',
                                                                         'COLLISION_BLOCKS_OPENING'})
        self.assertGreater(built, 3)

    def test_regression_thin_short_deck_curbs_do_not_fold(self):
        """Fuzz finding: halving the curb height at the toes folded the curb sweep on short ramps."""
        m = generate_portal(PortalParams(ring_inner_radius=2.873, ring_tube_width=0.449, ring_tube_depth=0.757,
                                         ring_center_lift=1.387, deck_length=6.832, deck_width=3.1,
                                         deck_thickness=0.102, deck_height=0.102, ramp_slope_deg=20.0,
                                         curb_width=0.477, rail_post_spacing=0.964, pedestal_height=0.654,
                                         pedestal_depth=2.507, core_radius=0.052, ring_segments=12,
                                         bevel_segments=1, lod_count=1))
        self.assertTrue(m.ok, m.diagnostics)

    def test_limits_cover_every_numeric_parameter(self):
        self.assertEqual(set(PARAM_LIMITS) | {'variant', 'palette'}, set(PARAM_NAMES))
        for name, (_kind, lo, hi) in PARAM_LIMITS.items():
            self.assertLess(lo, hi, name)
            self.assertTrue(lo <= getattr(PortalParams(), name) <= hi, name)


class GameplayContract(unittest.TestCase):
    def test_too_small_opening(self):
        m = generate_portal(PortalParams(ring_inner_radius=1.3, ring_center_lift=0.6))
        self.assertFalse(m.ok)
        self.assertTrue({'OPENING_TOO_NARROW', 'OPENING_TOO_LOW'} <= codes(m), m.diagnostics)
        self.assertIn('OPENING_TOO_LOW', codes(generate_portal(PortalParams(min_clear_height=3.5))))
        self.assertIn('OPENING_TOO_NARROW', codes(generate_portal(PortalParams(min_clear_width=3.0))))

    def test_too_steep_ramp(self):
        m = generate_portal(PortalParams(ramp_slope_deg=30.0))
        self.assertEqual(codes(m), {'RAMP_TOO_STEEP'})
        self.assertTrue(generate_portal(PortalParams(ramp_slope_deg=30.0, max_ramp_slope_deg=32.0,
                                                     **FAST)).ok)

    def test_too_narrow_walkway(self):
        self.assertIn('WALKABLE_TOO_NARROW', codes(generate_portal(PortalParams(deck_width=2.2, curb_width=0.16))))
        self.assertIn('WALKABLE_TOO_NARROW', codes(generate_portal(PortalParams(deck_width=1.4, curb_width=0.5))))

    def test_ring_legs_never_cut_the_deck(self):
        self.assertIn('RING_INTRUDES_DECK', codes(generate_portal(PortalParams(ring_center_lift=1.9))))
        self.assertIn('PEDESTAL_TOO_NARROW', codes(generate_portal(PortalParams(pedestal_width=0.45))))

    def test_opening_and_walkway_meet_the_contract(self):
        for name in VARIANTS:
            m = fast(name)
            md, p = m.metadata, m.params
            self.assertGreaterEqual(md['opening']['clear_width'], p.min_clear_width)
            self.assertGreaterEqual(md['opening']['clear_height'], p.min_clear_height)
            self.assertGreaterEqual(md['bridge']['walkable_width'], p.min_walkable_width)
            self.assertLessEqual(md['bridge']['ramp_slope_deg'], p.max_ramp_slope_deg)
            self.assertGreaterEqual(p.min_clear_width, 2.2)
            self.assertGreaterEqual(p.min_clear_height, 2.6)
            self.assertGreaterEqual(p.min_walkable_width, 2.0)

    def test_player_rectangle_is_free_of_visual_geometry(self):
        """Independent of the layout math: no ring/rail/base vertex inside a player-sized box."""
        m = model()
        p, lay = m.params, compute_layout(m.params)[0]
        hw, y0, y1 = 0.5 * p.min_clear_width, lay['hd'] + 0.05, lay['hd'] + p.min_clear_height
        for part in m.parts(0):
            if part.name == 'portal.membrane':  # the energy surface is meant to be walked through
                continue
            for x, y, z in part.positions:
                inside = abs(x) < hw and y0 < y < y1 and abs(z) < 0.5 * lay['td']
                self.assertFalse(inside, (part.name, x, y, z))

    def test_walkable_width_measured_from_curbs(self):
        m = model()
        lay = compute_layout(m.params)[0]
        curbs = next(part for part in m.parts(0) if part.name == 'deck.curbs')
        inner = min(abs(x) for x, _y, _z in curbs.positions)
        self.assertAlmostEqual(2 * inner, lay['walk'], places=4)
        self.assertGreaterEqual(2 * inner, m.params.min_walkable_width)

    def test_ramp_slope_from_geometry(self):
        m = model()
        limit = math.radians(m.params.max_ramp_slope_deg)
        for box in m.collision:
            if box['name'].startswith('ramp_'):
                up = B.axes(box)[1]
                self.assertLessEqual(math.acos(max(-1.0, min(1.0, up[1]))), limit + 1e-6)
        body = next(part for part in m.parts(0) if part.name == 'deck.body')
        walking = 0
        for t in range(0, len(body.indices), 3):
            a, b, c = (body.positions[i] for i in body.indices[t:t + 3])
            n = V.normalize(M.triangle_normal(a, b, c))
            if n[1] > 0.5 and abs(n[0]) < 0.05:  # upward faces of the walking strip (not edge bevels)
                walking += 1
                self.assertLessEqual(math.degrees(math.acos(n[1])), m.params.ramp_slope_deg + 1e-3)
        self.assertGreater(walking, 4)


class Geometry(unittest.TestCase):
    def test_every_lod_validates(self):
        for name in VARIANTS:
            m = fast(name)
            for lod in m.lods:
                self.assertEqual(M.validate_mesh(lod['parts'], subject=f'{name}/LOD{lod["level"]}'), [])
                for part in lod['parts']:
                    n = part.vertex_count
                    self.assertTrue(all(math.isfinite(c) for p in part.positions for c in p))
                    self.assertTrue(all(0 <= i < n for i in part.indices))
                    self.assertTrue(all(abs(V.length(nn) - 1.0) < 1e-5 for nn in part.normals))
                    self.assertEqual(len(part.indices) % 3, 0)

    def test_default_lods_reduce_triangles(self):
        m = model()
        counts = m.metadata['triangle_counts']
        self.assertEqual(len(counts), 3)
        self.assertTrue(counts[0] > counts[1] > counts[2] > 500, counts)
        self.assertEqual(counts, [M.triangle_count(lod['parts']) for lod in m.lods])
        self.assertEqual(len(generate_portal(PortalParams(lod_count=1, **FAST)).lods), 1)

    def test_only_the_six_semantic_materials(self):
        self.assertEqual(len(SEMANTIC_MATERIALS), 6)
        m = model()
        for lod in m.lods:
            self.assertTrue({p.material for p in lod['parts']} <= set(SEMANTIC_MATERIALS))
        self.assertEqual({p.material for p in m.parts(0)}, set(SEMANTIC_MATERIALS))
        self.assertEqual(m.metadata['materials'], sorted(SEMANTIC_MATERIALS))

    def test_pivot_symmetry_and_bounds(self):
        m = model()
        lo, hi = M.bounds(m.parts(0))
        self.assertAlmostEqual(lo[1], 0.0, places=6)  # stands on the ground plane
        self.assertAlmostEqual(lo[0], -hi[0], places=5)  # left/right symmetric
        self.assertAlmostEqual(lo[2], -hi[2], places=5)  # front/back symmetric: usable from both sides
        self.assertEqual(m.metadata['bounds'], {'min': list(lo), 'max': list(hi)})
        self.assertLess(max(hi), 10.0)

    def test_deterministic_and_hash_sensitive(self):
        a = generate_portal(variant_params('meadow', **FAST))
        b = generate_portal(variant_params('meadow', **FAST))
        self.assertEqual(a.metadata, b.metadata)
        self.assertEqual([(p.positions, p.normals, p.uvs, p.indices) for p in a.parts(0)],
                         [(p.positions, p.normals, p.uvs, p.indices) for p in b.parts(0)])
        self.assertEqual(a.params_hash, b.params_hash)
        self.assertNotEqual(a.params_hash, generate_portal(variant_params('meadow', ring_segments=33,
                                                                          bevel_segments=2)).params_hash)

    def test_metadata_conventions_and_evidence(self):
        md = model().metadata
        conv = md['conventions']
        self.assertEqual((conv['units'], conv['up_axis'], conv['forward_axis'], conv['handedness']),
                         ('metres', '+Y', '+Z', 'right'))
        self.assertEqual(md['evidence'], 'NEEDS_LOCAL_VISUAL_PROOF')
        self.assertNotIn('VERIFIED', ' '.join(md['evidence_detail'].values()).replace('UNIT_TESTED', ''))
        self.assertRegex(md['params_hash'], r'^[0-9a-f]{64}$')
        self.assertEqual(md['generator_version'], '1.0.0')


class CollisionTriggerSockets(unittest.TestCase):
    def test_no_collision_inside_the_opening(self):
        m = model()
        probe = B.shrunk(m.trigger, 0.005)
        self.assertFalse([b['name'] for b in m.collision if B.overlap(probe, b)])
        names = {b['name'] for b in m.collision}
        self.assertTrue({'deck', 'ramp_front', 'ramp_back', 'pedestal_left', 'pedestal_right', 'keystone'} <= names)
        self.assertTrue(any(n.startswith('ring_') for n in names))
        for b in m.collision:
            self.assertTrue(all(e > 0 and math.isfinite(e) for e in b['half_extents']), b)

    def test_collision_supports_the_deck_and_encloses_the_visuals(self):
        m = model()
        lay = compute_layout(m.params)[0]
        for k in range(61):
            z = -lay['ztoe'] + 0.05 + (2 * lay['ztoe'] - 0.1) * k / 60
            for x in (-0.5 * lay['walk'] + 0.05, 0.0, 0.5 * lay['walk'] - 0.05):
                pt = (x, surface_y(lay, z) - 0.02, z)
                self.assertTrue(any(B.contains_point(b, pt, 0.02) for b in m.collision), pt)
        vlo, vhi = M.bounds(m.parts(0))
        clo, chi = B.aabb(m.collision)
        for k in range(3):
            self.assertLessEqual(clo[k], vlo[k] + 0.06)
            self.assertGreaterEqual(chi[k], vhi[k] - 0.06)
        deck = next(b for b in m.collision if b['name'] == 'deck')
        self.assertGreaterEqual(2 * deck['half_extents'][0], m.metadata['bridge']['walkable_width'])

    def test_trigger_fills_the_opening_inside_the_ring(self):
        m = model()
        lay = compute_layout(m.params)[0]
        t = m.trigger
        self.assertEqual((t['front_normal'], t['back_normal']), ([0.0, 0.0, 1.0], [0.0, 0.0, -1.0]))
        for x, y, z in B.corners(t):
            self.assertLessEqual(x * x + (y - lay['cy']) ** 2, lay['reff'] ** 2 + 1e-6)
            self.assertGreaterEqual(y, lay['hd'] - 1e-6)
            self.assertLessEqual(abs(z), 0.5 * lay['td'] + 1e-6)
        self.assertGreaterEqual(2 * t['half_extents'][0], m.params.min_clear_width)
        self.assertGreaterEqual(2 * t['half_extents'][1], 2.0)  # a full player height crosses it

    def test_sockets(self):
        m = model()
        s = m.sockets
        self.assertTrue(set(REQUIRED_SOCKETS) <= set(s))
        self.assertGreater(s['socket.spawn_front']['position'][2], 0)
        self.assertLess(s['socket.spawn_back']['position'][2], 0)
        self.assertEqual(s['socket.spawn_front']['forward'], [0.0, 0.0, 1.0])
        self.assertEqual(s['socket.spawn_back']['forward'], [0.0, 0.0, -1.0])
        self.assertLess(s['socket.sign_left']['position'][0], 0)  # viewer-left when approaching the +Z front
        self.assertGreater(s['socket.sign_right']['position'][0], 0)
        lay = compute_layout(m.params)[0]
        for name in ('socket.spawn_front', 'socket.spawn_back'):
            pos = tuple(s[name]['position'])
            self.assertFalse(B.contains_point(m.trigger, pos, SPAWN_CLEARANCE))
            self.assertAlmostEqual(pos[1], surface_y(lay, pos[2]), places=6)
        keystone = next(b for b in m.collision if b['name'] == 'keystone')
        self.assertTrue(B.contains_point(keystone, tuple(s['socket.energy_core']['position'])))
        self.assertEqual(s['socket.vfx_center']['position'], [0.0, round(lay['cy'], 6), 0.0])
        for sock in s.values():
            fwd, up, q = sock['forward'], sock['up'], sock['rotation']
            ax, ay, az = V.axes_from_quat(tuple(q))
            self.assertLess(V.length(V.sub(az, tuple(fwd))), 1e-5)
            self.assertLess(V.length(V.sub(ay, tuple(up))), 1e-5)


class MeshHelpers(unittest.TestCase):
    def tri(self):
        part = M.MeshPart('t', 'accent')
        for p in ((0, 0, 0), (1, 0, 0), (0, 1, 0)):
            part.add_vertex(tuple(float(c) for c in p), (0.0, 0.0, 1.0), (0.0, 0.0))
        part.add_triangle(0, 1, 2)
        return part

    def test_validate_mesh_detects_each_problem(self):
        self.assertEqual(M.validate_mesh(self.tri()), [])
        self.assertEqual({d['code'] for d in M.validate_mesh([])}, {'MESH_EMPTY'})
        bad = self.tri()
        bad.positions[1] = (float('nan'), 0.0, 0.0)
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_NON_FINITE'})
        bad = self.tri()
        bad.indices[2] = 3
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_INDEX_OUT_OF_RANGE'})
        bad = self.tri()
        bad.positions[2] = (2.0, 0.0, 0.0)
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_DEGENERATE_TRIANGLES'})
        bad = self.tri()
        bad.normals[0] = (0.0, 0.0, 2.0)
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_NORMAL_NOT_UNIT'})
        bad = self.tri()
        bad.indices = [0, 2, 1]
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_WINDING_INCONSISTENT'})
        bad = self.tri()
        bad.positions[0] = (-500.0, 0.0, 0.0)
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_BOUNDS_UNREASONABLE'})
        bad = self.tri()
        bad.uvs.pop()
        self.assertEqual({d['code'] for d in M.validate_mesh(bad)}, {'MESH_ATTRIBUTE_MISMATCH'})
        for d in M.validate_mesh(bad):
            self.assertEqual(D.validate(d), [])

    def test_helpers_keep_meshes_valid(self):
        box = P.rounded_box('b', 'accent', (0.5, 0.3, 0.8), 0.1, 2)
        mirrored = M.transform(box, V.scaling((-1.0, 1.0, 1.0)))
        self.assertEqual(M.validate_mesh(mirrored), [])
        rotated = M.transform(box, V.compose(V.translation((1, 2, 3)), V.rotation_axis((1, 1, 0), 0.7)))
        self.assertEqual(M.validate_mesh(rotated), [])
        self.assertEqual(M.validate_mesh(M.compute_flat_normals(box)), [])
        self.assertEqual(M.validate_mesh(M.compute_smooth_normals(box, by_position=True)), [])
        merged = M.merge([box, rotated], name='m')
        self.assertEqual((merged.vertex_count, merged.triangle_count),
                         (2 * box.vertex_count, 2 * box.triangle_count))
        with self.assertRaises(ValueError):
            M.merge([box, P.sphere('s', 'frame_primary', (0, 0, 0), 1.0, 8)])
        lo, hi = M.bounds([box])
        self.assertEqual((lo, hi), ((-0.5, -0.3, -0.8), (0.5, 0.3, 0.8)))

    def test_primitives_are_valid_with_outward_normals(self):
        parts = [P.ring_arc('r', 'a', 2.0, 0.5, 0.7, 0.12, 24, 2),
                 P.ring_arc('r', 'a', 2.0, 0.5, 0.7, 0.12, 24, 2, start=-0.5, end=math.pi + 0.5),
                 P.rounded_box('b', 'a', (0.5, 0.3, 0.8), 0.1, 3),
                 P.capsule('c', 'a', (0, 0, 0), (1, 1, 0), 0.2, 12, 3),
                 P.sphere('s', 'a', (1, 2, 3), 0.3, 12),
                 P.rounded_cylinder('y', 'a', (0, 0, 0), (0, 1, 0), 0.2, 0.05, 12, 2),
                 P.clipped_disc('d', 'a', (0, 1.6, 0), 2.0, 32, 0.46),
                 P.swept_slab('s', 'a', [(-3, 0.04, 0.04), (-1.7, 0.45, 0.3), (1.7, 0.45, 0.3), (3, 0.04, 0.04)],
                              3.0, 0.08, 2),
                 P.rail('h', 'a', [(0, 1, 0), (1, 1, 0), (2, 1.3, 0)], 0.05, 8)]
        for part in parts:
            self.assertEqual(M.validate_mesh(part, subject=part.name), [], part.name)
        box = parts[2]
        self.assertTrue(all(V.dot(n, p) > 0 for p, n in zip(box.positions, box.normals)))
        sph = parts[4]
        self.assertTrue(all(V.dot(n, V.sub(p, (1, 2, 3))) > 0 for p, n in zip(sph.positions, sph.normals)))
        disc = parts[6]
        self.assertGreaterEqual(min(p[1] for p in disc.positions), 0.46 - 1e-9)


class Volumes(unittest.TestCase):
    def test_separating_axis_overlap(self):
        a = B.make_box('a', (0, 0, 0), (1, 1, 1))
        self.assertTrue(B.overlap(a, B.make_box('b', (1.5, 0, 0), (1, 1, 1))))
        self.assertFalse(B.overlap(a, B.make_box('b', (2.5, 0, 0), (1, 1, 1))))
        self.assertFalse(B.overlap(a, B.make_box('b', (2.0, 0, 0), (1, 1, 1))))  # touching is not overlapping
        c, s = math.cos(math.pi / 4), math.sin(math.pi / 4)
        rotated = B.make_box('r', (2.3, 0, 0), (1, 1, 1), ((c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0)))
        self.assertTrue(B.overlap(a, rotated))  # its corner reaches x = 2.3 - 1.414
        far = B.make_box('r', (2.5, 0, 0), (1, 1, 1), ((c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0)))
        self.assertFalse(B.overlap(a, far))  # its corner only reaches x = 1.086
        self.assertFalse(B.overlap(a, B.make_box('r', (2.5, 0, 0), (1, 0.1, 0.1),
                                                  ((c, s, 0.0), (-s, c, 0.0), (0.0, 0.0, 1.0)))))
        self.assertTrue(B.contains_point(a, (0.99, -0.99, 0.5)))
        self.assertFalse(B.contains_point(a, (1.01, 0, 0)))


if __name__ == '__main__':
    unittest.main()
