"""Deterministic layout of the SR1 sanctuary (walkway extension, island, statue ring, gate, portal) from a local kit.

    python -m bridge.sanctuary.layout --kit Lab/AssetCache/sanctuary-kit-001 --out Lab/GeneratedContent/sanctuary/layout-001

The layout only *names* native pieces (``woodPlat01``, ``ruinPillar01``, ``PlortSwitch_pink`` ...) and places them; the SR1
plugin resolves meshes and materials from the running game. Same kit + same settings => byte-identical layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import stat
import tempfile
from pathlib import Path
import random

from bridge.sanctuary import island as I

REPO_ROOT = Path(__file__).resolve().parents[2]


def generator_id():
    """Short hash of the layout generator's own sources: an installed layout made by older code is detectably stale."""
    digest = hashlib.sha256()
    for name in ('layout.py', 'island.py'):
        digest.update((Path(__file__).with_name(name)).read_bytes())
    return digest.hexdigest()[:16]

LAYOUT_SCHEMA = 'bridge-sanctuary-layout'
LAYOUT_VERSION = 1
EXCLUSIONS_FILE = Path(__file__).with_name('slime_statue_exclusions.json')
ROOT_NAME = 'SlimeBridge_SR1_WorldRoot'
OUTPUT_ROOTS = ('Lab/GeneratedContent', 'Lab/AssetCache', 'Lab/RuntimeArtifacts')

DEFAULT_CONFIG = {
    'sr1_bridge_access_mode': 'ENDGAME',        # ALWAYS | ENDGAME (ENDGAME: deposit Gold Plorts to open the walkway)
    'sr1_gold_plort_gate_cost': 10,
    'dynamic_statue_requirements': False,        # False: the puzzle manifest is frozen at first creation
    'sr1_portal_requires_statues': True,         # False: the portal opens as soon as the walkway does (statues are decoration)
    'bridge_planks': 1,                          # woodPlat01 segments appended to the dead end (19 m each)
    'island_distance': 28.5,                     # metres from the anchor to the plaza centre (short bridge, clear of the cliff behind)
    'ring_radius': 13.5,                         # inner statue clearing (the portal court and its henge lie inside it)
    'hidden_radius': 23.0,                       # outer statue clearing: the hidden species stand here, framed by rocks and trees
    'island_scale': 4.6,
    'island_mesh': 'mtnBase01',                  # the rock the island is cut from (mtnBase01 is the Moss zone's own mountain base | mtnBase02)
    'island_ground': 'moss',                     # moss: the green moss rock material of the Moss zone | native: the original sand rock
    'flora_density': 1.0,                        # 0 = bare island, 2 = twice the trees, flowers and grass
    'seed': 20261017,                            # the garden is random but reproducible
}
ACCESS_MODES = ('ALWAYS', 'ENDGAME')
GROUNDS = ('moss', 'native')
ISLAND_MESHES = ('mtnBase01', 'mtnBase02')


class LayoutError(ValueError):
    pass


# --------------------------------------------------------------------------------------------------------------------
# frame helpers: u = metres along the walkway heading, v = metres to the right of it
# --------------------------------------------------------------------------------------------------------------------
class Frame:
    def __init__(self, origin, heading_deg):
        self.origin = list(origin)
        self.h = math.radians(heading_deg)
        self.heading = heading_deg
        self.forward = [math.sin(self.h), 0.0, math.cos(self.h)]
        self.right = [math.cos(self.h), 0.0, -math.sin(self.h)]

    def at(self, u, v, y):
        return [round(self.origin[0] + self.forward[0] * u + self.right[0] * v, 4), round(y, 4),
                round(self.origin[2] + self.forward[2] * u + self.right[2] * v, 4)]

    def toward(self, source, target):
        """Unity yaw (degrees) turning local +Z from *source* toward *target*."""
        return math.degrees(math.atan2(target[0] - source[0], target[2] - source[2]))


def yaw_quaternion(yaw_degrees):
    half = math.radians(yaw_degrees) / 2
    return [0.0, round(math.sin(half), 6), 0.0, round(math.cos(half), 6)]


def normalise_yaw(value):
    return round((value + 180.0) % 360.0 - 180.0, 3)


# --------------------------------------------------------------------------------------------------------------------
# kit access
# --------------------------------------------------------------------------------------------------------------------
def kit_pieces(kit):
    return {p['name']: p for p in kit['pieces']}


def kit_piece(pieces, name):
    try:
        return pieces[name]
    except KeyError as exc:
        raise LayoutError(f'native piece {name} is missing from the kit') from exc


def load_exclusions(path=EXCLUSIONS_FILE):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema') != 'slime-statue-exclusions' or data.get('schema_version') != 1:
        raise LayoutError('unsupported statue exclusion file')
    return data


def config_from_settings(settings):
    """Layout settings from a normalized launcher settings document (``bridge`` section)."""
    bridge = settings['bridge']
    return {key: bridge[key] for key in ('sr1_bridge_access_mode', 'sr1_gold_plort_gate_cost', 'dynamic_statue_requirements',
                                         'sr1_portal_requires_statues')}


def validate_config(config):
    merged = dict(DEFAULT_CONFIG, **(config or {}))
    unknown = set(merged) - set(DEFAULT_CONFIG)
    if unknown:
        raise LayoutError('unknown settings: ' + ', '.join(sorted(unknown)))
    if merged['sr1_bridge_access_mode'] not in ACCESS_MODES:
        raise LayoutError('sr1_bridge_access_mode must be ALWAYS or ENDGAME')
    cost = merged['sr1_gold_plort_gate_cost']
    if type(cost) is not int or not 1 <= cost <= 999:
        raise LayoutError('sr1_gold_plort_gate_cost must be an integer in 1..999')
    for key in ('dynamic_statue_requirements', 'sr1_portal_requires_statues'):
        if type(merged[key]) is not bool:
            raise LayoutError(f'{key} must be a boolean')
    planks = merged['bridge_planks']
    if type(planks) is not int or not 1 <= planks <= 6:
        raise LayoutError('bridge_planks must be 1..6')
    for key in ('island_distance', 'ring_radius', 'hidden_radius', 'island_scale'):
        if not isinstance(merged[key], (int, float)) or isinstance(merged[key], bool) or not 3 <= merged[key] <= 200:
            raise LayoutError(f'{key} out of range')
    if merged['hidden_radius'] <= merged['ring_radius'] + 2:
        raise LayoutError('hidden_radius must lie outside the statue ring')
    if merged['island_ground'] not in GROUNDS:
        raise LayoutError('island_ground must be moss or native')
    if merged['island_mesh'] not in ISLAND_MESHES:
        raise LayoutError('island_mesh must be mtnBase01 or mtnBase02')
    density = merged['flora_density']
    if not isinstance(density, (int, float)) or isinstance(density, bool) or not 0 <= density <= 2:
        raise LayoutError('flora_density must be 0..2')
    if type(merged['seed']) is not int:
        raise LayoutError('seed must be an integer')
    return merged


# --------------------------------------------------------------------------------------------------------------------
# statues
# --------------------------------------------------------------------------------------------------------------------
def statue_template(kit, construct):
    for statue in kit['statues']:
        if statue['construct'] == construct:
            return statue
    raise LayoutError(f'native statue {construct} is missing from the kit')


def trimmed_nodes(statue):
    """Only what the plugin needs to rebuild a statue: parts, lights, colliders, the plort display."""
    nodes = []
    for node in statue['nodes']:
        entry = {'path': node['path'], 'name': node['name'], 'local': node['local'], 'active': node['active']}
        if node['render']:
            entry['mesh'] = node['render']['mesh']
            entry['materials'] = [m['name'] if m else None for m in node['render']['materials']]
        comps = []
        for component in node['components']:
            if component['type'] in ('Light', 'SphereCollider', 'BoxCollider', 'CapsuleCollider'):
                comps.append(component)
        if comps:
            entry['components'] = comps
        nodes.append(entry)
    return nodes


def plort_colors(kit, material_name):
    material = (kit.get('materials') or {}).get(material_name)
    if not material:
        return None
    c = material['colors']
    return {'top': c.get('_TopColor'), 'middle': c.get('_MiddleColor'), 'bottom': c.get('_BottomColor')}


def select_statues(kit, exclusions):
    rows = []
    optional = set(exclusions.get('optional', {}))
    hidden = list(exclusions.get('hidden', []))
    for row in kit['statue_catalog']:
        key = row['slime']
        if row['status'] not in ('native', 'synthesized') or key in exclusions['excluded']:
            continue
        rows.append(dict(row, optional=key in optional, hidden=key in hidden))
    if not rows:
        raise LayoutError('no statues selected')
    order = sorted(rows, key=lambda r: (r['hidden'], r['optional'], r['slime']))
    return order


PLINTH_HEIGHT = 0.55


def build_statue_entries(kit, config, frame, plaza, deck_top, rows, rng, footprints):
    """One entry per selected slime. Required species fill the inner and middle clearings, hidden ones the outer clearing
    (placement 'alcove'), optional bonus species the outer clearing too. Returns (entries, plinths)."""
    ordered = [r for r in rows if not r['hidden'] and not r['optional']] + [r for r in rows if r['optional'] and not r['hidden']] +               [r for r in rows if r['hidden']]
    for attempt in range(300):
        points = I.statue_slots(len(ordered), config['ring_radius'], config['hidden_radius'], 0.82,
                                random.Random(config['seed'] + 7919 * attempt))
        try:
            I.check_statue_spacing(points)
            break
        except I.IslandError:
            continue
    else:
        raise LayoutError('no statue arrangement keeps the statues apart')
    # The hidden species take the outermost spots, the required ones the nearest; species are shuffled with a fixed stride so
    # colour-alike neighbours do not sit together.
    points.sort(key=lambda pt: math.hypot(pt[0] / 0.82, pt[1]))
    plain = [r for r in ordered if not r['hidden'] and not r['optional']]
    plain = [plain[(i * 5) % len(plain)] for i in range(len(plain))] if plain and len(plain) % 5 else plain
    ordered = plain + [r for r in ordered if r['optional'] and not r['hidden']] + [r for r in ordered if r['hidden']]
    assignment = [(row, (0, du, dv, 0.0)) for row, (du, dv) in zip(ordered, points)]
    if len(assignment) != len(ordered):
        raise LayoutError('statue slots do not match the statue count')
    entries, plinths = [], []
    for row, (ring, du, dv, angle) in assignment:
        placement = 'alcove' if row['hidden'] else ('bonus' if row['optional'] else 'ring')
        u, v = plaza['u'] + du, dv
        top = deck_top + (PLINTH_HEIGHT if 'ruinPlatform05' in kit_pieces(kit) else 0.0)
        position = frame.at(u, v, top)
        yaw = normalise_yaw(frame.toward(position, [plaza['position'][0], 0, plaza['position'][2]]))
        entries.append(statue_entry(kit, row, position, yaw, placement))
        footprints.add(u, v, I.STATUE_RADIUS, 'statue')
        plinths.append((u, v, yaw))
    return entries, plinths


def statue_entry(kit, row, position, yaw, placement):
    construct = (row.get('constructs') or [row.get('template')])[0]
    template = statue_template(kit, construct)
    key = row['slime']
    colors = plort_colors(kit, row.get('plort_display_material'))
    entry = {
        'id': 'statue.' + key.lower(), 'slime': key, 'plort_id': row['plort_id'], 'plort': row['plort'],
        'status': row['status'], 'required': not row.get('optional', False), 'optional': row.get('optional', False),
        'hidden': row.get('hidden', False),
        'placement': placement, 'position': position, 'yaw': yaw, 'rotation': yaw_quaternion(yaw),
        'template': {'construct': construct, 'catch_name': template['catch_name'], 'nodes': trimmed_nodes(template)},
        'plort_display_material': row.get('plort_display_material'),
    }
    if row['status'] == 'native':
        entry['statue_material'] = row.get('statue_material')
        entry['eyes_material'] = _eyes_material(kit, key)
    else:
        # Synthesized: the Pink statue cloned and tinted from this plort's own colours.
        entry['statue_material'] = 'objPlortStatuePink'
        entry['eyes_material'] = 'plortEyePink'
        entry['detail_texture'] = 'envRuinStone_' + key.lower()
        entry['tint'] = colors
    entry['light_color'] = colors['top'] if colors else None
    return entry


def _eyes_material(kit, key):
    aliases = {'PHOSPHOR': 'Phos'}
    name = 'plortEye' + aliases.get(key, key.title())
    return name if name in (kit.get('materials') or {}) else 'plortEyePink'


# --------------------------------------------------------------------------------------------------------------------
# main build
# --------------------------------------------------------------------------------------------------------------------
def build_layout(kit, config=None, exclusions=None):
    config = validate_config(config)
    exclusions = exclusions or load_exclusions()
    pieces = kit_pieces(kit)
    walkway = kit['walkway']
    end = walkway['end_pieces'][0]
    anchor = walkway['anchor']
    frame = Frame(anchor['position'], anchor['heading_y'])

    plat9 = kit_piece(pieces, 'woodPlat09')
    plank = kit_piece(pieces, 'woodPlat01')
    end_scale = end['world']['scale'][1]
    deck_top = round(end['world']['position'][1] + (plat9['bounds']['center'][1] + plat9['bounds']['extent'][1]) * end_scale, 4)
    plank_scale = plank['example_world_scale'][0]
    plank_half_x = plank['bounds']['extent'][0] * plank_scale
    plank_origin_y = round(deck_top - plank['bounds']['extent'][1] * plank_scale, 4)
    plank_len = plank_half_x * 2

    out = []
    rng = random.Random(config['seed'])
    foot = I.Footprints()
    have = lambda *names: all(n in pieces for n in names)

    def add_raw(group, ident, mesh, materials, position, rotation, scale, collider='mesh', phase='court', shadows=True, name=None):
        out.append({'id': ident, 'group': group, 'name': name or mesh, 'mesh': mesh, 'materials': list(materials),
                    'position': [round(c, 4) for c in position], 'yaw': normalise_yaw(math.degrees(2 * math.atan2(rotation[1], rotation[3]))),
                    'rotation': [round(c, 6) for c in rotation], 'scale': [round(c, 4) for c in scale], 'collider': collider,
                    'phase': phase, **({} if shadows else {'shadows': False})})

    def add(group, name, position, yaw, scale, collider='mesh', ident=None, tag=None, phase='court', shadows=True, materials=None):
        piece = kit_piece(pieces, name)
        out.append({'id': ident or f'{group}.{len(out):03d}', 'group': group, 'name': name, 'mesh': piece['mesh'],
                    'materials': list(materials) if materials else piece['materials'], 'position': position, 'yaw': normalise_yaw(yaw),
                    'rotation': yaw_quaternion(yaw), 'scale': [round(s, 4) for s in scale], 'collider': collider, 'phase': phase,
                    **({} if shadows else {'shadows': False}), **({'tag': tag} if tag else {})})

    def top_of(name, scale_y):
        piece = kit_piece(pieces, name)
        return (piece['bounds']['center'][1] + piece['bounds']['extent'][1]) * scale_y

    # ----- walkway extension: planks along the heading, supported by posts and lit by torches
    plank_yaw = frame.heading - 90.0                      # local +X (the long axis) points along the heading
    for i in range(config['bridge_planks']):
        u = plank_len * (i + 0.5)
        add('bridge', 'woodPlat01', frame.at(u, 0.0, plank_origin_y), plank_yaw, [plank_scale] * 3, ident=f'bridge.plank{i}', phase='span')
    post_scale = 1.5
    post_y = round(deck_top + 0.9 - top_of('woodPosts01', post_scale), 4)   # supports under the deck, only a short bollard above it
    for i in range(config['bridge_planks'] + 1):
        u = plank_len * i
        for side in (-1, 1):
            if i == 0 and side == 1:
                continue                                   # the existing dead-end platform already carries posts here
            add('bridge', 'woodPosts01', frame.at(u + 0.4, side * (plank['bounds']['extent'][2] * plank_scale + 0.3), post_y),
                17.0 * i + 11.0 * side, [post_scale] * 3, ident=f'bridge.post{i}{"l" if side < 0 else "r"}', phase='span')
    for i in range(config['bridge_planks'] * 2):
        u = plank_len * (i + 0.5) / 2 + plank_len / 4
        add('bridge', 'woodTorch01', frame.at(u, (-1) ** i * 2.55, deck_top), 0.0, [1.5] * 3, collider='none', ident=f'bridge.torch{i}', phase='span')

    # ----- island: a moss rock base the size of the plaza
    plaza_u = config['island_distance']
    plaza_pos = frame.at(plaza_u, 0.0, deck_top)
    plaza = {'u': plaza_u, 'position': plaza_pos}
    mesh_name = config['island_mesh'] if config['island_mesh'] in pieces else 'mtnBase02'
    base = kit_piece(pieces, mesh_name)
    base_scale = config['island_scale']
    if mesh_name == 'mtnBase01':
        # mtnBase01 is longer than wide (8.1 x 4.5): stretch it into a disc of the same radius as mtnBase02 at island_scale
        sx = base_scale * 6.35 / base['bounds']['extent'][0]
        base_scales = [sx, sx, sx * base['bounds']['extent'][0] / base['bounds']['extent'][2]]
    else:
        base_scales = [base_scale] * 3
    base_top = base['bounds']['center'][1] + base['bounds']['extent'][1]
    # envMossNav01 is the Moss zone's walkable ground (green topper on rock); the env_voxel * materials are the Glitch zone's
    # voxel shader and show a neon pixel grid up close.
    ground = ['envMossNav01'] if config['island_ground'] == 'moss' else None
    add('island', mesh_name, frame.at(plaza_u, 0.0, deck_top - base_top * base_scales[1]), 0.0, base_scales, ident='island.base',
        phase='rise', materials=ground)
    if mesh_name == 'mtnBase01':
        # a landing garden at the foot of the bridge: the same rock, smaller, so the island reaches the dead end instead of
        # leaving a notch of water beside the walkway
        landing = base_scales[0] * 0.5
        add('island', mesh_name, frame.at(plaza_u - 22.0, 0.0, deck_top - base_top * landing), 0.0,
            [landing, landing, landing * base['bounds']['extent'][0] / base['bounds']['extent'][2]], ident='island.landing', phase='rise', materials=ground)
    grow = config['island_scale'] / 3.0
    for j, (du, dv, yaw, name, scale) in enumerate([(-9.0 * grow, 12.0 * grow, 20.0, 'cliffBase02', 1.5 * grow), (-9.0 * grow, -12.0 * grow, -35.0, 'cliffBase02', 1.5 * grow),
                                                    (10.0 * grow, 13.0 * grow, 80.0, 'cliffBase04', 1.2 * grow), (11.0 * grow, -12.0 * grow, 140.0, 'cliffBase04', 1.2 * grow)]):
        piece = kit_piece(pieces, name)
        top = piece['bounds']['center'][1] + piece['bounds']['extent'][1]
        add('island', name, frame.at(plaza_u + du, dv, deck_top - top * scale - 0.5), yaw, [scale] * 3, ident=f'island.rock{j}', phase='rise')

    # ----- portal court: a flat paved disc and a henge of pillars with a gap toward the bridge
    if have('ruinPlatform05'):
        for k, yaw in enumerate((0.0, 30.0, 60.0)):
            sy = 0.1
            add('plaza', 'ruinPlatform05', frame.at(plaza_u, 0.0, round(deck_top + 0.1 - top_of('ruinPlatform05', sy), 4)), yaw,
                [0.72, sy, 0.72], collider='box', ident=f'plaza.court{k}', shadows=False)
    foot.add(plaza_u, 0.0, 8.0, 'court')
    pillar = kit_piece(pieces, 'ruinPillar01')
    pillar_count = 10
    for k in range(pillar_count):
        angle = math.radians(-128.0 + k * (256.0 / (pillar_count - 1)))
        u, v = plaza_u + 8.8 * math.cos(angle), 8.8 * math.sin(angle)
        pos = frame.at(u, v, deck_top)
        add('plaza', 'ruinPillar01', pos, frame.toward(pos, [plaza_pos[0], 0, plaza_pos[2]]), [pillar['example_world_scale'][0]] * 3,
            ident=f'plaza.pillar{k}')
        foot.add(u, v, 1.5, 'pillar')

    # ----- statues, plinths and stone paths
    statues, plinths = build_statue_entries(kit, config, frame, plaza, deck_top, select_statues(kit, exclusions), rng, foot)
    required = [s['slime'] for s in statues if s['required']]
    paths = []
    if have('ruinPlatform05'):
        for k, (u, v, yaw) in enumerate(plinths):
            add('plaza', 'ruinPlatform05', frame.at(u, v, round(deck_top + PLINTH_HEIGHT - top_of('ruinPlatform05', 0.36), 4)), yaw,
                [0.27, 0.36, 0.27], collider='box', ident=f'plaza.plinth{k}')
    if have('decoRuinGlowStick'):
        stick = kit_piece(pieces, 'decoRuinGlowStick')
        stick_scale = 0.3
        for k, (u, v, yaw) in enumerate(plinths):
            # a glow stick behind-right of every statue (the statue faces the court; Unity yaw: forward = (sin, cos), right = (cos, -sin))
            heading = math.radians(yaw)
            dx = -math.sin(heading) * 2.4 + math.cos(heading) * 1.4
            dz = -math.cos(heading) * 2.4 - math.sin(heading) * 1.4
            u2 = u + dx * frame.forward[0] + dz * frame.forward[2]
            v2 = v + dx * frame.right[0] + dz * frame.right[2]
            add('plaza', 'decoRuinGlowStick', frame.at(u2, v2, round(deck_top - (stick['bounds']['center'][1] - stick['bounds']['extent'][1]) * stick_scale, 4)), 0.0,
                [stick_scale] * 3, collider='none', ident=f'plaza.glow{k}', shadows=False)
    if have('ruinPath01'):
        tile = kit_piece(pieces, 'ruinPath01')
        path_material = 'envRuinsBlocks01'
        tile_scale = 0.62
        tile_len = tile['bounds']['extent'][0] * tile_scale * 2
        for k, (u, v, _) in enumerate(plinths):
            span = max(0.1, math.hypot(u - plaza_u, v))
            start = (plaza_u + 8.2 * (u - plaza_u) / span, 8.2 * v / span)
            length = math.hypot(u - start[0], v - start[1]) - 2.9
            count = max(0, int(length // (tile_len * 0.96)))
            heading = math.atan2(v - start[1], u - start[0])        # frame angle: 0 along +u
            for t in range(count):
                along = (t + 0.5) * length / max(1, count)
                tu, tv = start[0] + math.cos(heading) * along, start[1] + math.sin(heading) * along
                pos = frame.at(tu, tv, round(deck_top + 0.06 - (tile['bounds']['center'][1] - tile['bounds']['extent'][1]) * tile_scale, 4))
                dx = frame.forward[0] * math.cos(heading) + frame.right[0] * math.sin(heading)
                dz = frame.forward[2] * math.cos(heading) + frame.right[2] * math.sin(heading)
                yaw_world = math.degrees(math.atan2(-dz, dx))        # local +X (the tile's long axis) along the path
                add('plaza', 'ruinPath01', pos, yaw_world, [tile_scale] * 3, collider='none', ident=f'plaza.path{k}.{t}', phase='court', shadows=False,
                    materials=[path_material])
            paths.append((start[0], start[1], u, v))

    # ----- flora: trees on the rim and in the clearings, flower beds, glowing flowers, mushrooms, mossy rocks and grass
    flora_count = {'trees': 0, 'accents': 0, 'rocks': 0, 'grass': 0}
    compounds = I.compound_by_key(kit)
    density = config['flora_density']

    def inside_island(u, v, margin):
        du, dv = (u - plaza_u) / 0.82, v
        return math.hypot(du, dv) <= config['hidden_radius'] + 5.5 - margin

    def clear_of_paths(u, v, radius):
        return all(I.segment_distance(u, v, a, b, c, d) > radius for a, b, c, d in paths)

    def corridor_free(u, v, radius):
        return not (u < plaza_u - 6.0 and abs(v) < 4.5 + radius)

    def away_from_statues(u, v, distance):
        return all(math.hypot(u - su, v - sv) > distance for su, sv, _ in plinths)

    def free(u, v, radius, ignore=()):
        return inside_island(u, v, radius) and foot.clear(u, v, radius, ignore=ignore) and clear_of_paths(u, v, radius * 0.6 + 1.2) \
            and corridor_free(u, v, radius) and math.hypot(u - plaza_u, v) > 9.8 and away_from_statues(u, v, 3.8 + radius)

    def place_compound(key, u, v, yaw, scale, solid_trunk=True):
        compound = compounds[key]
        q = I.yaw_q(yaw)
        base_pos = frame.at(u, v, deck_top)
        # the layout yaw is a Unity yaw (about +Y); frame.at already gave the world position of the root
        for node in compound['nodes']:
            local = node['local']
            offset = I.q_rotate(q, [c * scale for c in local['position']])
            rotation = I.q_mul(q, local['rotation'])
            solid = solid_trunk and 'trunk' in node['name'].lower()
            add_raw('flora', f'flora.{key}.{len(out):04d}', node['mesh'], node['materials'],
                    [base_pos[0] + offset[0], base_pos[1] + offset[1], base_pos[2] + offset[2]], rotation,
                    [c * scale for c in local['scale']], collider='box' if solid else 'none', phase='flora', shadows=False, name=node['name'])

    trees = [k for k in I.TREE_KEYS if k in compounds]
    for n in range(int(14 * density)):
        if not trees:
            break
        key = trees[n % len(trees)]
        radius = I.compound_radius(compounds[key])
        for attempt in range(160):
            angle, dist = rng.uniform(-math.pi, math.pi), rng.uniform(config['hidden_radius'] - 1.0, config['hidden_radius'] + 4.5)
            u, v = plaza_u + dist * math.cos(angle) * 0.82, dist * math.sin(angle)
            scale = rng.uniform(0.4, 0.65)
            if free(u, v, radius * scale + 1.4):
                place_compound(key, u, v, rng.uniform(0, 360), scale)
                foot.add(u, v, radius * scale, 'tree')
                flora_count['trees'] += 1
                break
    accents = [k for k in ('flowers.bed', 'flower.glow', 'flowers.bed', 'flower.glow', 'shroom.giant') if k in compounds]
    shrooms = 0
    for n in range(int(26 * density)):
        if not accents:
            break
        key = accents[n % len(accents)]
        if key == 'shroom.giant':
            if shrooms >= max(1, int(4 * density)):
                continue                                    # the giant mushrooms are a few landmarks, not a forest
            shrooms += 1
        for attempt in range(120):
            angle, dist = rng.uniform(-math.pi, math.pi), rng.uniform(10.5, config['hidden_radius'] + 4.0)
            u, v = plaza_u + dist * math.cos(angle) * 0.82, dist * math.sin(angle)
            if free(u, v, 1.4, ignore=('grass',)):
                place_compound(key, u, v, rng.uniform(0, 360), rng.uniform(0.4, 0.55) if key == 'shroom.giant' else rng.uniform(0.8, 1.2),
                               solid_trunk=False)
                foot.add(u, v, 0.8, 'accent')
                flora_count['accents'] += 1
                break
    rocks = [n for n in I.ROCK_PIECES if have(n)]
    for n in range(int(14 * density)):
        if not rocks:
            break
        name = rocks[n % len(rocks)]
        rock = kit_piece(pieces, name)
        for attempt in range(120):
            angle, dist = rng.uniform(-math.pi, math.pi), rng.uniform(12.0, config['hidden_radius'] + 5.0)
            u, v = plaza_u + dist * math.cos(angle) * 0.82, dist * math.sin(angle)
            scale = rng.uniform(0.9, 1.7) * rock['example_world_scale'][0] / 2.0
            r = max(rock['bounds']['extent'][0], rock['bounds']['extent'][2]) * scale
            if free(u, v, r + 0.8):
                add('flora', name, frame.at(u, v, round(deck_top - (rock['bounds']['center'][1] - rock['bounds']['extent'][1]) * scale - 0.15, 4)),
                    rng.uniform(0, 360), [scale] * 3, collider='box', ident=f'flora.rock{len(out)}', phase='flora', shadows=False)
                foot.add(u, v, r, 'rock')
                flora_count['rocks'] += 1
                break
    grass = [n for n in I.GRASS_PIECES if have(n)]
    for n in range(int(150 * density)):
        if not grass:
            break
        name = grass[n % len(grass)]
        for attempt in range(40):
            angle, dist = rng.uniform(-math.pi, math.pi), rng.uniform(5.0, config['hidden_radius'] + 4.5)
            u, v = plaza_u + dist * math.cos(angle) * 0.82, dist * math.sin(angle)
            if inside_island(u, v, 1.0) and foot.clear(u, v, 0.5, ignore=('grass', 'accent')) and clear_of_paths(u, v, 1.4) and corridor_free(u, v, 0.5) \
                    and math.hypot(u - plaza_u, v) > 8.2 and away_from_statues(u, v, 4.6):
                # grass stays low (about 1-1.8 m: the player's eye is at 1.5 m) with a clear apron of 4.6 m around every statue
                add('flora', name, frame.at(u, v, deck_top - 0.05), rng.uniform(0, 360), [rng.uniform(0.5, 0.85)] * 3, collider='none',
                    ident=f'flora.grass{len(out)}', phase='flora', shadows=False)
                foot.add(u, v, 0.3, 'grass')
                flora_count['grass'] += 1
                break
    # hidden species are secluded, never unreachable: two mossy rocks flank each of them
    if have('rockMossTall01'):
        rock = kit_piece(pieces, 'rockMossTall01')
        for hidden in [x for x in statues if x['placement'] == 'alcove']:
            hx, hz = hidden['position'][0], hidden['position'][2]
            radial = math.atan2(hx - plaza_pos[0], hz - plaza_pos[2])
            for slot, (back, side) in enumerate(((3.6, 2.9), (3.6, -2.9))):
                px = hx + math.sin(radial) * back + math.cos(radial) * side
                pz = hz + math.cos(radial) * back - math.sin(radial) * side
                scale = 1.2 * rock['example_world_scale'][0] / 2.0
                add('flora', 'rockMossTall01', [round(px, 4), round(deck_top - (rock['bounds']['center'][1] - rock['bounds']['extent'][1]) * scale - 0.2, 4), round(pz, 4)],
                    math.degrees(radial) + 40.0 * slot, [scale] * 3, collider='box', ident=f"flora.nook.{hidden['slime'].lower()}.{slot}", phase='flora', shadows=False)
    # nothing may overlap a statue: trees, rocks and pillars are checked against every statue footprint
    for s in statues:
        su = (s['position'][0] - frame.origin[0]) * frame.forward[0] + (s['position'][2] - frame.origin[2]) * frame.forward[2]
        sv = (s['position'][0] - frame.origin[0]) * frame.right[0] + (s['position'][2] - frame.origin[2]) * frame.right[2]
        hits = foot.overlaps(su, sv, I.STATUE_RADIUS * 0.8, ('pillar', 'tree', 'rock', 'court'))
        if hits:
            raise LayoutError(f"statue {s['id']} overlaps a {hits[0][3]}")
    for a in statues:
        for b in statues:
            if a['id'] < b['id'] and math.dist(a['position'], b['position']) < I.MIN_STATUE_GAP - 0.01:
                raise LayoutError(f"statues {a['id']} and {b['id']} overlap")

    gold_row = next((r for r in kit['statue_catalog'] if r['slime'] == 'GOLD'), None)
    if gold_row is None or gold_row.get('plort_id') is None:
        raise LayoutError('the kit has no Gold plort for the gate')
    # The depositor stands on a small side pad so it never blocks the walkway.
    pad = kit_piece(pieces, 'woodPlat03')
    pad_scale = pad['example_world_scale'][0]
    half_width = plank['bounds']['extent'][2] * plank_scale
    pad_half_v = pad['bounds']['extent'][2] * pad_scale
    pad_v = half_width + pad_half_v - 0.5
    pad_u = 2.0                                      # right at the dead end: the only thing standing there until the gate is paid
    pad_origin_y = round(deck_top - (pad['bounds']['center'][1] + pad['bounds']['extent'][1]) * pad_scale, 4)
    add('bridge', 'woodPlat03', frame.at(pad_u, pad_v, pad_origin_y), plank_yaw, [pad_scale] * 3, ident='bridge.depositor_pad')
    for k, du in enumerate((-2.2, 2.2)):
        # supports under the pad: only a short bollard shows above the deck, nothing stands between the player and the statue
        add('bridge', 'woodPosts01', frame.at(pad_u + du, pad_v + pad_half_v - 0.3, post_y), 23.0 * k + 5.0, [post_scale] * 3,
            ident=f'bridge.padpost{k}')
    if have('ruinPlatform05'):
        add('bridge', 'ruinPlatform05', frame.at(pad_u, pad_v, round(deck_top + PLINTH_HEIGHT - top_of('ruinPlatform05', 0.36), 4)), plank_yaw,
            [0.27, 0.36, 0.27], collider='box', ident='bridge.padplinth')
    gate_position = frame.at(pad_u, pad_v, deck_top + (PLINTH_HEIGHT if have('ruinPlatform05') else 0.0))
    across = frame.at(pad_u, pad_v - 5.0, deck_top)
    gate_yaw = normalise_yaw(frame.toward(gate_position, across))
    gate_statue = statue_entry(kit, dict(gold_row, status='synthesized', template='PlortSwitch_pink', optional=False, hidden=False),
                               gate_position, gate_yaw, 'gate')
    gate_statue['id'] = 'gate.gold'
    # The depositor is a real gold slime (the game's own Gold slime mesh and material, frozen on a plinth); the tablet beside it
    # carries the hidden message of the "H" who watches over the ruins.
    block_name = 'ruinBlock01' if 'ruinBlock01' in pieces else 'ruinBlock03'
    slab = kit_piece(pieces, block_name)
    slab_scale = [1.15, 1.45, 0.2]
    tablet_pos = frame.at(pad_u + 3.0, pad_v + 0.9, round(deck_top - (slab['bounds']['center'][1] - slab['bounds']['extent'][1]) * slab_scale[1], 4))
    tablet = {
        'id': 'tablet.h', 'position': tablet_pos, 'yaw': gate_yaw, 'rotation': yaw_quaternion(gate_yaw), 'scale': slab_scale,
        'mesh': slab['mesh'], 'materials': slab['materials'], 'height': round(slab['bounds']['extent'][1] * slab_scale[1] * 2, 3),
        'title': 'H',
        'lines': ['Pay the price, and a new universe opens its door.', '', 'Feed the golden statue its plorts, and the way beyond the sea is yours.'],
        'signature': '- H',
    }
    gate = {
        'kind': 'gold_depositor', 'plort': 'GOLD_PLORT', 'plort_id': kit['identifiables']['plorts']['GOLD'],
        'cost': config['sr1_gold_plort_gate_cost'], 'enabled': config['sr1_bridge_access_mode'] == 'ENDGAME',
        'position': gate_position, 'yaw': gate_yaw, 'statue': gate_statue, 'tablet': tablet,
        'figure': {'kind': 'native_slime', 'slime': 'GOLD', 'scale': 0.85, 'lift': 0.0},
        'barrier': {'position': frame.at(9.0, 0.0, deck_top), 'yaw': normalise_yaw(frame.heading),
                    'width': round(plank['bounds']['extent'][2] * plank_scale * 2, 4), 'height': 6.0,
                    'frame_piece': 'ruinBarrierFrame', 'door_piece': 'ruinBarrierDoor',
                    'frame_mesh': kit_piece(pieces, 'ruinBarrierFrame')['mesh'], 'frame_materials': kit_piece(pieces, 'ruinBarrierFrame')['materials'],
                    'door_mesh': kit_piece(pieces, 'ruinBarrierDoor')['mesh'], 'door_materials': kit_piece(pieces, 'ruinBarrierDoor')['materials']},
    }
    block = kit_piece(pieces, 'ruinBlock03')
    portal = {'position': plaza_pos, 'yaw': normalise_yaw(frame.heading + 180.0), 'trigger_radius': 3.2,
              'requires': 'all_required_statues' if config['sr1_portal_requires_statues'] else 'none', 'destination': 'SR2',
              'portal_id': 'portal.sr1.sanctuary_gate',
              'ring': {'mesh': block['mesh'], 'materials': block['materials'], 'count': 18, 'radius': 5.2, 'center_height': 5.3,
                       'scale': 1.3},
              'membrane': {'material': 'plortEyeQuantum', 'radius': 4.4, 'fallback_material': 'plortEyePink'}}
    spawn = {'position': frame.at(plaza_u - config['ring_radius'] - 1.5, 0.0, deck_top + 0.1),
             'yaw': normalise_yaw(frame.heading)}
    layout = {
        'schema': LAYOUT_SCHEMA, 'schema_version': LAYOUT_VERSION, 'realm': 'SR1', 'root_name': ROOT_NAME,
        'kit_sha256': kit.get('kit_sha256') or hashlib.sha256(json.dumps(kit, sort_keys=True).encode()).hexdigest(),
        'config': {k: config[k] for k in ('sr1_bridge_access_mode', 'sr1_gold_plort_gate_cost', 'dynamic_statue_requirements',
                                          'sr1_portal_requires_statues')},
        'frame': {'origin': frame.origin, 'heading_y': frame.heading, 'forward': [round(v, 6) for v in frame.forward],
                  'right': [round(v, 6) for v in frame.right]},
        'generator': generator_id(), 'deck_top_y': deck_top, 'plank_length': round(plank_len, 4),
        'pieces': out, 'statues': statues, 'gate': gate, 'portal': portal, 'spawn': spawn,
        'requirement_manifest': {'version': 1, 'created_from_kit': True, 'required': sorted(required),
                                 'dynamic': config['dynamic_statue_requirements']},
        'shots': build_shots(frame, plaza, deck_top, statues),
    }
    validate_layout(layout)
    return layout


def build_shots(frame, plaza, deck_top, statues):
    """Camera poses (position, look-at) for the LAB visual oracle: walkway, island overview, portal, a few statues."""
    shots = []

    def shot(name, u, v, y, target):
        shots.append({'name': name, 'position': frame.at(u, v, y), 'look_at': target})

    centre = plaza['position']
    shot('walkway-start', -6.0, 3.0, deck_top + 3.0, frame.at(14.0, 0.0, deck_top + 1.0))
    shot('walkway-mid', 14.0, 3.5, deck_top + 4.0, frame.at(40.0, 0.0, deck_top + 1.0))
    shot('island-approach', plaza['u'] - 22.0, 0.0, deck_top + 2.6, [centre[0], deck_top + 3.0, centre[2]])
    shot('court-high', plaza['u'] - 16.0, 12.0, deck_top + 9.0, [centre[0], deck_top + 1.0, centre[2]])
    shot('tablet', 7.0, -1.0, deck_top + 2.4, frame.at(2.0, 5.5, deck_top + 1.6))
    shot('island-overview', plaza['u'] - 50.0, 16.0, deck_top + 34.0, [centre[0], deck_top, centre[2]])
    shot('portal-close', plaza['u'] - 11.0, 0.0, deck_top + 3.4, [centre[0], deck_top + 2.4, centre[2]])
    shot('gate', -1.0, 2.0, deck_top + 2.5, frame.at(9.0, 0.0, deck_top + 2.5))
    shot('island-top', plaza['u'] + 0.5, 0.0, deck_top + 80.0, [centre[0], deck_top, centre[2]])
    chosen = statues[:3] + [x for x in statues if x['hidden']][:4]
    for statue in chosen:
        pos = statue['position']
        front = [pos[0] + math.sin(math.radians(statue['yaw'])) * 5.0, deck_top + 2.0, pos[2] + math.cos(math.radians(statue['yaw'])) * 5.0]
        shots.append({'name': statue['id'], 'position': front, 'look_at': [pos[0], deck_top + 1.5, pos[2]]})
    return shots


def validate_layout(layout):
    problems = []
    if layout.get('schema') != LAYOUT_SCHEMA or layout.get('schema_version') != LAYOUT_VERSION:
        problems.append('wrong schema')
    ids = [p['id'] for p in layout['pieces']] + [s['id'] for s in layout['statues']]
    if len(ids) != len(set(ids)):
        problems.append('duplicate ids')
    for p in layout['pieces']:
        if not p['mesh'] or not p['materials'] or None in p['materials']:
            problems.append(f"piece {p['id']} has no native mesh/material")
    if not layout['statues']:
        problems.append('no statues')
    required = {s['slime'] for s in layout['statues'] if s['required']}
    if required != set(layout['requirement_manifest']['required']):
        problems.append('requirement manifest disagrees with the statues')
    if any(s['required'] and s['plort_id'] is None for s in layout['statues']):
        problems.append('a required statue has no plort')
    if any(s['hidden'] and s['placement'] != 'alcove' for s in layout['statues']):
        problems.append('hidden statue not in an alcove')
    if layout['gate']['cost'] < 1:
        problems.append('gate cost')
    phases = set(I.PHASES)
    for p in layout['pieces']:
        if p.get('phase', 'court') not in phases:
            problems.append(f"piece {p['id']} has an unknown reveal phase")
    if problems:
        raise LayoutError('; '.join(problems))
    return layout


# --------------------------------------------------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------------------------------------------------
def layout_bytes(layout):
    return (json.dumps(layout, indent=1, sort_keys=False, allow_nan=False) + '\n').encode('utf-8')


def check_output(output):
    output = Path(output).absolute()
    roots = [REPO_ROOT / r for r in OUTPUT_ROOTS]
    if '..' in output.parts or not any(output != root and output.is_relative_to(root) for root in roots):
        raise LayoutError('output must be a fresh folder inside this checkout\'s Lab/')
    for part in (output, *output.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise LayoutError('output reparse point refused')
    if output.exists():
        raise LayoutError('output exists; choose a fresh folder')
    return output


def publish_layout(layout, output):
    output = check_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.sanctuary-stage-', dir=output.parent))
    try:
        data = layout_bytes(layout)
        (stage / 'sanctuary-layout.json').write_bytes(data)
        check_output(output)
        stage.rename(output)
        return {'ok': True, 'directory': str(output), 'sha256': hashlib.sha256(data).hexdigest(), 'statues': len(layout['statues']),
                'required': len(layout['requirement_manifest']['required']), 'pieces': len(layout['pieces'])}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def load_kit(kit_dir):
    path = Path(kit_dir) / 'sanctuary-kit.json'
    kit = json.loads(path.read_text(encoding='utf-8'))
    kit['kit_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    return kit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--kit', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--set', action='append', default=[], metavar='KEY=JSON', help='override a setting, e.g. sr1_gold_plort_gate_cost=15')
    args = parser.parse_args(argv)
    try:
        config = {}
        for item in args.set:
            key, _, raw = item.partition('=')
            config[key] = json.loads(raw)
        print(json.dumps(publish_layout(build_layout(load_kit(args.kit), config), args.out), indent=2))
    except (LayoutError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
