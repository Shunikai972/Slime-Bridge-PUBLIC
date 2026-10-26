"""Read-only UnityPy extraction of the SR1 statue / walkway kit used to build the SlimeBridge sanctuary.

Reads the user's own SR1 installation (never a scene copy in the repository) and writes ``sanctuary-kit.json`` into a
fresh private cache folder under ``Lab/``. The kit is a *description*: which native meshes, materials, lights,
colliders and puzzle-slot parameters make up each native plort statue, where the Starlight-Strand wooden walkway ends,
and which ruin / wood / rock pieces exist. The SR1 plugin resolves those names against the live game at runtime, so no
Monomi Park mesh, texture or material is copied, committed or shipped. Nothing here launches the game.

    python -m bridge.content.sr1_sanctuary --sr1-dir Lab/Games/SR1/SlimeRancher_Data --out Lab/AssetCache/sanctuary-kit-001
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import tempfile
import time
from pathlib import Path

from bridge.content.dotnet_enum import read_nested_enum
from bridge.content.sr1_asset_extract import Sr1Assets, check_output, sha256, write_json

KIT_SCHEMA = 'bridge-sanctuary-kit'
KIT_VERSION = 1
SCENE_FILE = 'level3'                     # the SR1 world scene ("worldGenerated")
SWITCH_PREFIX = 'PlortSwitch'
WALKWAY_CELL = 'cellSea_Bridgeway'
# User-supplied anchor: the dead end of the wooden walkway (world position, heading in degrees about +Y).
WALKWAY_ANCHOR = {'position': [-91.5, 3.0, 287.8], 'heading_y': 67.8}

# Normal-collection policy (see docs/SANCTUARY.md). Slimes without a plort have no mandatory statue.
EXCLUDED_SLIMES = {
    'QUICKSILVER': 'event-only species',
    'GLITCH': 'technical/debug species',
    'GLITCH_TARR': 'technical/debug species',
    'TARR': 'no collectable plort',
    'LUCKY': 'no collectable plort',
}
PIECE_PATTERN = re.compile(
    r'^(ruin(Statue|Pillar|Platform|Arc|Path|Ramp|Rail|Wall|Block|DoorFrame|DoorLock|Light|Cube|Ceiling|Planter|Barrier)'
    r'|wood(Plat|Posts|Arch|Torch|Rope)|rope|rockCrag|areaCragChunk|areaBlockCap|cliffBase|mtnBase|mountaintop'
    # flora / decoration singles (compound trees are described by bridge/content/sr1_flora.py)
    r'|rockMoss(Flat|Tall|Boxy)01|decoRuinGlowStick|growth(Base|Flower|Stem)0|mushroom(Cap|Base)0|flowerBed|strangeDiamond'
    r'|grassThin(Sprig|Colony)0[12]_LOD0|drapeMoss0|vine0|areaPlane0|grassPlane0|decalMound0)', re.I)


# --------------------------------------------------------------------------------------------------------------------
# small matrix helpers (4x4 row-major lists; Unity local TRS = translation * rotation * scale)
# --------------------------------------------------------------------------------------------------------------------
def _identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def trs_matrix(position, rotation, scale):
    x, y, z, w = rotation
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    m = _identity()
    for i in range(3):
        for j in range(3):
            m[i][j] = r[i][j] * scale[j]
        m[i][3] = position[i]
    return m


def decompose(m):
    """(position, quaternion xyzw, scale) of a TRS matrix without shear."""
    scale = [math.sqrt(sum(m[i][j] ** 2 for i in range(3))) for j in range(3)]
    if min(scale) < 1e-9:
        raise ValueError('degenerate transform scale')
    r = [[m[i][j] / scale[j] for j in range(3)] for i in range(3)]
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1.0) * 2
        q = [(r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s, 0.25 * s]
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        q = [0.25 * s, (r[0][1] + r[1][0]) / s, (r[0][2] + r[2][0]) / s, (r[2][1] - r[1][2]) / s]
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        q = [(r[0][1] + r[1][0]) / s, 0.25 * s, (r[1][2] + r[2][1]) / s, (r[0][2] - r[2][0]) / s]
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        q = [(r[0][2] + r[2][0]) / s, (r[1][2] + r[2][1]) / s, 0.25 * s, (r[1][0] - r[0][1]) / s]
    return [m[i][3] for i in range(3)], q, scale


def heading_y(rotation):
    """Yaw (degrees, Unity convention) of a quaternion."""
    x, y, z, w = rotation
    return math.degrees(math.atan2(2 * (w * y + x * z), 1 - 2 * (y * y + x * x)))


def _round(values, digits=5):
    return [round(float(v), digits) for v in values]


# --------------------------------------------------------------------------------------------------------------------
# scene access
# --------------------------------------------------------------------------------------------------------------------
class Scene(Sr1Assets):
    """The SR1 world scene with cached name / transform lookups (read-only)."""

    def __init__(self, data_dir):
        super().__init__(data_dir, SCENE_FILE)
        self._names = {}
        self._world = {}
        self._mesh_names = {}
        self._material = {}
        self.game_objects = [o for o in self.objects if o.type.name == 'GameObject']

    # ----- naming / hierarchy
    def name(self, go):
        key = go.path_id
        if key not in self._names:
            self._names[key] = self.tree(go)['m_Name']
        return self._names[key]

    def transform(self, go):
        return self.component(go, 'Transform')

    def parent(self, go):
        father = self.tree(self.transform(go))['m_Father']
        if father['m_PathID'] == 0:
            return None
        return self.follow(go, self.tree(self.follow(go, father, 'Transform'))['m_GameObject'], 'GameObject')

    def path_of(self, go, limit=64):
        parts = []
        node = go
        for _ in range(limit):
            parts.append(self.name(node))
            node = self.parent(node)
            if node is None:
                return '/'.join(reversed(parts))
        raise ValueError('hierarchy too deep or cyclic')

    def children(self, go):
        transform = self.tree(self.transform(go))
        return [self.follow(go, self.tree(self.follow(go, p, 'Transform'))['m_GameObject'], 'GameObject')
                for p in transform['m_Children']]

    def descendants(self, go, limit=20000):
        out, pending = [], [go]
        while pending:
            node = pending.pop()
            out.append(node)
            if len(out) > limit:
                raise ValueError('oversized hierarchy')
            pending.extend(self.children(node))
        return out

    def local_trs(self, go):
        tree = self.tree(self.transform(go))
        p, r, s = tree['m_LocalPosition'], tree['m_LocalRotation'], tree['m_LocalScale']
        return [p['x'], p['y'], p['z']], [r['x'], r['y'], r['z'], r['w']], [s['x'], s['y'], s['z']]

    def world_matrix(self, go):
        key = go.path_id
        if key not in self._world:
            parent = self.parent(go)
            local = trs_matrix(*self.local_trs(go))
            self._world[key] = _mul(self.world_matrix(parent), local) if parent is not None else local
        return self._world[key]

    def world_trs(self, go):
        position, rotation, scale = decompose(self.world_matrix(go))
        return {'position': _round(position), 'rotation': _round(rotation, 6), 'scale': _round(scale),
                'heading_y': round(heading_y(rotation), 2)}

    def local_description(self, go):
        position, rotation, scale = self.local_trs(go)
        return {'position': _round(position), 'rotation': _round(rotation, 6), 'scale': _round(scale)}

    # ----- assets
    def mesh_info(self, pointer, owner):
        key = (pointer['m_FileID'], pointer['m_PathID'], owner.assets_file.name)
        if key not in self._mesh_names:
            if pointer['m_PathID'] == 0:
                self._mesh_names[key] = None
            else:
                mesh = self.tree(self.follow(owner, pointer, 'Mesh'))
                box = mesh.get('m_LocalAABB') or {}
                bounds = None
                if box:
                    bounds = {'center': _round([box['m_Center'][k] for k in 'xyz']), 'extent': _round([box['m_Extent'][k] for k in 'xyz'])}
                self._mesh_names[key] = {'name': mesh['m_Name'], 'bounds': bounds}
        return self._mesh_names[key]

    def mesh_name(self, pointer, owner):
        info = self.mesh_info(pointer, owner)
        return info['name'] if info else None

    def material(self, owner, pointer):
        key = (pointer['m_FileID'], pointer['m_PathID'], owner.assets_file.name)
        if key not in self._material:
            self._material[key] = None if pointer['m_PathID'] == 0 else self.describe_material(self.follow(owner, pointer, 'Material'))
        return self._material[key]

    def describe_material(self, material):
        tree = self.tree(material)
        props = tree['m_SavedProperties']
        shader_name = ''
        if tree['m_Shader']['m_PathID'] != 0:                      # a few authoring leftovers reference no shader
            shader_tree = self.tree(self.follow(material, tree['m_Shader'], 'Shader'))
            shader_name = (shader_tree.get('m_ParsedForm') or {}).get('m_Name') or shader_tree.get('m_Name') or ''
        textures = {}
        for prop, env in props['m_TexEnvs']:
            pointer = env['m_Texture']
            texture = None
            if pointer['m_PathID'] != 0:
                texture = self.tree(self.follow(material, pointer))['m_Name']
            textures[prop] = {'texture': texture, 'scale': _round([env['m_Scale']['x'], env['m_Scale']['y']]),
                              'offset': _round([env['m_Offset']['x'], env['m_Offset']['y']])}
        return {'name': tree['m_Name'], 'shader': shader_name, 'keywords': tree.get('m_ShaderKeywords', ''),
                'textures': textures,
                'colors': {prop: _round([c['r'], c['g'], c['b'], c['a']]) for prop, c in props['m_Colors']},
                'floats': {prop: round(float(v), 6) for prop, v in props['m_Floats']}}

    # ----- components
    def describe_renderer_pair(self, go):
        """Mesh + materials of one GameObject, or ``None`` when it renders nothing."""
        filters = self.components(go, 'MeshFilter')
        renderers = self.components(go, 'MeshRenderer')
        if len(filters) != 1 or len(renderers) != 1:
            return None
        info = self.mesh_info(self.tree(filters[0])['m_Mesh'], filters[0])
        mesh = info['name'] if info else None
        renderer = self.tree(renderers[0])
        materials = [self.material(renderers[0], m) for m in renderer['m_Materials']]
        return {'mesh': mesh, 'bounds': info['bounds'] if info else None, 'materials': materials, 'enabled': bool(renderer['m_Enabled']),
                'cast_shadows': renderer['m_CastShadows'], 'receive_shadows': bool(renderer['m_ReceiveShadows'])}

    def describe_components(self, go):
        out = []
        for comp in self.components(go):
            kind, tree = comp.type.name, self.tree(comp)
            if kind in ('MeshFilter', 'MeshRenderer', 'Transform'):
                continue
            entry = {'type': kind}
            if kind in ('SphereCollider', 'BoxCollider', 'CapsuleCollider'):
                entry.update(trigger=bool(tree['m_IsTrigger']), center=_round([tree['m_Center'][k] for k in 'xyz']))
                if kind == 'SphereCollider':
                    entry['radius'] = round(tree['m_Radius'], 5)
                elif kind == 'BoxCollider':
                    entry['size'] = _round([tree['m_Size'][k] for k in 'xyz'])
                else:
                    entry.update(radius=round(tree['m_Radius'], 5), height=round(tree['m_Height'], 5), direction=tree['m_Direction'])
            elif kind == 'Light':
                c = tree['m_Color']
                entry.update(light_type=tree['m_Type'], color=_round([c['r'], c['g'], c['b'], c['a']]),
                             intensity=round(tree['m_Intensity'], 4), range=round(tree['m_Range'], 4),
                             spot_angle=round(tree['m_SpotAngle'], 3), shadows=tree['m_Shadows']['m_Type'])
            elif kind == 'MonoBehaviour':
                entry['class'] = self.class_name(comp)
                entry['fields'] = self.describe_fields(comp, tree)
            out.append(entry)
        return out

    def describe_fields(self, owner, tree):
        fields = {}
        for key, value in tree.items():
            if key in ('m_GameObject', 'm_Script', 'm_Name', 'm_Enabled', 'm_EditorHideFlags', 'm_EditorClassIdentifier'):
                continue
            fields[key] = self.describe_value(owner, value)
        return fields

    def describe_value(self, owner, value, depth=0):
        if isinstance(value, dict) and set(value) == {'m_FileID', 'm_PathID'}:
            if value['m_PathID'] == 0:
                return None
            try:
                target = self.follow(owner, value)
            except ValueError:
                return {'unresolved': True}
            info = {'ref': target.type.name}
            if target.type.name == 'GameObject':
                info['name'] = self.name(target)
            else:
                name = self.tree(target).get('m_Name') if target.type.name not in ('Transform', 'MonoBehaviour') else None
                if name:
                    info['name'] = name
            return info
        if isinstance(value, dict) and depth < 4:
            return {k: self.describe_value(owner, v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)) and depth < 4:
            return [self.describe_value(owner, v, depth + 1) for v in value[:64]]
        return value

    def describe_tree(self, root, relative_to=None, limit=64):
        """Every GameObject below *root* (inclusive) with local transform, renderer pair and non-trivial components."""
        rows = []
        base = relative_to or root
        for node in self.descendants(root, limit):
            path = self.path_of(node)
            prefix = self.path_of(base)
            rel = path[len(prefix):].lstrip('/') if path.startswith(prefix) else path
            rows.append({'path': rel or self.name(node), 'name': self.name(node), 'active': bool(self.tree(node)['m_IsActive']),
                         'layer': self.tree(node)['m_Layer'], 'tag': self.tree(node)['m_Tag'],
                         'local': self.local_description(node), 'render': self.describe_renderer_pair(node),
                         'components': self.describe_components(node)})
        rows.sort(key=lambda r: r['path'])
        return rows


# --------------------------------------------------------------------------------------------------------------------
# kit assembly
# --------------------------------------------------------------------------------------------------------------------
def slime_key(plort_name):
    return plort_name[:-len('_PLORT')]


def build_identifiables(assembly):
    members = read_nested_enum(assembly, 'Identifiable', 'Id')
    by_name = {name: value for value, name in members.items()}
    plorts = {slime_key(n): v for n, v in by_name.items() if n.endswith('_PLORT')}
    slimes = {n[:-len('_SLIME')]: v for n, v in by_name.items() if n.endswith('_SLIME')}
    return members, plorts, slimes


def extract_statue_instances(scene, members):
    """One record per native ``PlortSwitch_*`` construct (numbered duplicates of the same construct are merged)."""
    groups = {}
    for go in scene.game_objects:
        name = scene.name(go)
        if not name.startswith(SWITCH_PREFIX + '_'):
            continue
        base = re.sub(r' \(\d+\)$', '', name)
        groups.setdefault(base, []).append(go)
    statues = []
    for base, instances in sorted(groups.items()):
        first = sorted(instances, key=lambda g: scene.path_of(g))[0]
        slots = [c for c in scene.components(first, 'MonoBehaviour') if scene.class_name(c) == 'PuzzleSlot']
        if len(slots) != 1:
            raise ValueError(f'{base}: expected exactly one PuzzleSlot')
        slot = scene.tree(slots[0])
        catch_id = slot['catchId']
        if catch_id not in members:
            raise ValueError(f'{base}: catchId {catch_id} is not an Identifiable.Id')
        path = scene.path_of(first)
        zone, _, rest = path.partition('/')
        cell = rest.split('/')[0]
        statues.append({
            'construct': base, 'instances': len(instances), 'zone': zone, 'cell': cell,
            'catch_id': catch_id, 'catch_name': members[catch_id], 'fill_on_awake': bool(slot['fillOnAwake']),
            'world': scene.world_trs(first), 'nodes': scene.describe_tree(first)})
    return statues


def piece_key(scene, go, pair):
    """Identity of a reusable piece: the authored object name (``woodPlat09``) or, failing that, its mesh name."""
    if pair is None or not pair['mesh']:
        return None
    base = re.sub(r' \(\d+\)$', '', scene.name(go))
    for candidate in (base, pair['mesh']):
        if PIECE_PATTERN.match(candidate):
            return candidate
    return None


def extract_pieces(scene):
    """First-seen rendering recipe (mesh, materials, scale) for every ruin / wood / rock piece in the world."""
    pieces = {}
    uses = {}
    for go in scene.game_objects:
        pair = scene.describe_renderer_pair(go)
        key = piece_key(scene, go, pair)
        if key is None:
            continue
        uses[key] = uses.get(key, 0) + 1
        if key not in pieces:
            pieces[key] = {'name': key, 'mesh': pair['mesh'], 'bounds': pair['bounds'], 'materials': [m['name'] if m else None for m in pair['materials']],
                           'example_path': scene.path_of(go), 'example_world_scale': scene.world_trs(go)['scale'],
                           'layer': scene.tree(go)['m_Layer'], 'has_collider': bool(scene.components(go, 'MeshCollider') or scene.components(go, 'BoxCollider')),
                           'example_local': scene.local_description(go)}
    for key, count in uses.items():
        pieces[key]['uses'] = count
    return [pieces[k] for k in sorted(pieces)]


def extract_walkway(scene):
    cells = [g for g in scene.game_objects if scene.name(g) == WALKWAY_CELL]
    if len(cells) != 1:
        raise ValueError(f'expected one {WALKWAY_CELL}, found {len(cells)}')
    cell = cells[0]
    rows = []
    for go in scene.descendants(cell):
        pair = scene.describe_renderer_pair(go)
        key = piece_key(scene, go, pair)
        if key is None:
            continue
        rows.append({'path': scene.path_of(go).split(WALKWAY_CELL + '/', 1)[-1], 'name': key, 'mesh': pair['mesh'], 'bounds': pair['bounds'],
                     'materials': [m['name'] if m else None for m in pair['materials']], 'world': scene.world_trs(go)})
    rows.sort(key=lambda r: r['path'])
    anchor_pos = WALKWAY_ANCHOR['position']
    planks = [r for r in rows if r['name'].startswith('woodPlat')]
    if not planks:
        raise ValueError('no walkway planks found in the Bridgeway cell')
    nearest = sorted(planks, key=lambda r: sum((a - b) ** 2 for a, b in zip(r['world']['position'], anchor_pos)))[:3]
    cell_world = scene.world_trs(cell)
    return {'cell': WALKWAY_CELL, 'cell_world': cell_world, 'anchor': WALKWAY_ANCHOR,
            'end_pieces': [{'path': r['path'], 'name': r['name'], 'mesh': r['mesh'], 'world': r['world'],
                            'distance': round(math.dist(r['world']['position'], anchor_pos), 3)} for r in nearest],
            'pieces': rows}


def plort_display_materials(scene):
    """Materials named ``plort<Name>Base`` / ``plortEye<...>`` / ``objPlortStatue*`` anywhere the scene links to."""
    found = {}
    for source in ('sharedassets2.assets', 'sharedassets3.assets'):
        scene.env.load_file(str(scene.data_dir / source))
    for obj in scene.env.objects:
        if obj.type.name != 'Material':
            continue
        try:
            name = obj.peek_name()
        except Exception:                                                        # noqa: BLE001 - unreadable names are skipped
            continue
        if name and re.match(r'^(plort\w+Base|plortEye\w+|objPlortStatue\w*|envPlortStatue\w*)$', name):
            found.setdefault(name, scene.describe_material(obj))
    return found


STATUE_MATERIAL_ALIASES = {
    'BOOM': 'Boom', 'HONEY': 'Honey', 'PHOSPHOR': 'Phos', 'PINK': 'Pink', 'QUANTUM': 'Quantum_Desert', 'RAD': 'Rad',
    'ROCK': 'Rock', 'TABBY': 'Tabby', 'DERVISH': '_Dervish', 'MOSAIC': '_Mosaic', 'TANGLE': '_Tangle'}


def build_catalog(plorts, slimes, statues, materials):
    """Which slimes get a statue, with the native source of each (or a synthesized one built from a template)."""
    native = {}
    for statue in statues:
        native.setdefault(slime_key(statue['catch_name']), []).append(statue['construct'])
    catalog = []
    for key in sorted(set(plorts) | set(slimes)):
        row = {'slime': key, 'slime_id': slimes.get(key), 'plort_id': plorts.get(key), 'plort': key + '_PLORT' if key in plorts else None}
        if key in EXCLUDED_SLIMES:
            row.update(status='excluded', reason=EXCLUDED_SLIMES[key])
        elif key not in plorts:
            row.update(status='decorative', reason='no collectable plort: its statue cannot be a required slot')
        elif key in native:
            alias = STATUE_MATERIAL_ALIASES.get(key)
            row.update(status='native', constructs=native[key],
                       statue_material=next((m for m in (f'objPlortStatue{alias}', f'objPlortStatue{alias}_Desert') if m in materials), None) if alias else None)
        else:
            row.update(status='synthesized', template='PlortSwitch_pink',
                       reason='no native statue: clone of the Pink statue with this plort catch id and display material')
        display = f"plort{key.title().replace('_', '')}Base"
        row['plort_display_material'] = next((n for n in materials if n.lower() == display.lower()), None)
        catalog.append(row)
    return catalog


def build_kit(scene, sr1_dir):
    assembly = Path(sr1_dir) / 'Managed' / 'Assembly-CSharp.dll'
    members, plorts, slimes = build_identifiables(assembly)
    statues = extract_statue_instances(scene, members)
    materials = plort_display_materials(scene)
    kit = {
        'schema': KIT_SCHEMA, 'schema_version': KIT_VERSION, 'source_game': 'SR1',
        'unity_version': scene.unity_version, 'scene': SCENE_FILE,
        'note': 'Description only: names, transforms and parameters of native SR1 objects. No mesh, texture or material data.',
        'identifiables': {'plorts': plorts, 'slimes': slimes},
        'statues': statues, 'statue_catalog': build_catalog(plorts, slimes, statues, materials),
        'materials': materials, 'walkway': extract_walkway(scene), 'pieces': extract_pieces(scene)}
    from bridge.content.sr1_flora import extract_compounds
    kit['flora'] = extract_compounds(scene)
    return kit


def validate_kit(kit):
    """Fail closed on a kit the builder or plugin could not use."""
    problems = []
    if kit.get('schema') != KIT_SCHEMA or kit.get('schema_version') != KIT_VERSION:
        problems.append('wrong schema')
    if not kit.get('statues'):
        problems.append('no native statues found')
    for statue in kit.get('statues', []):
        names = {n['name'] for n in statue['nodes']}
        if not {'Statue', 'Eyes'} <= names:
            problems.append(f"{statue['construct']}: missing Statue/Eyes parts")
    ends = kit.get('walkway', {}).get('end_pieces', [])
    if not ends or ends[0]['distance'] > 15:
        problems.append('walkway end is not near the configured anchor')
    if problems:
        raise ValueError('; '.join(problems))
    return kit


def publish_kit(output, producer):
    output = check_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.sr1-sanctuary-stage-', dir=output.parent))
    try:
        producer(stage)
        files = {p.name: sha256(p) for p in sorted(stage.iterdir()) if p.is_file()}
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        write_json(stage / 'cache-manifest.json', {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                                                    'canonical_id': 'sanctuary.sr1', 'converter_version': KIT_VERSION,
                                                    'source_game': 'SR1', 'published_utc': stamp, 'files': files})
        check_output(output)
        stage.rename(output)
        return {'ok': True, 'cache_directory': str(output), 'kit_sha256': files['sanctuary-kit.json']}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def extract_kit(sr1_dir, out_dir, allow_nonlab=False):
    source = Path(sr1_dir).resolve()
    if not source.is_dir():
        raise ValueError('SR1 data directory missing')
    if not allow_nonlab and not any(p.name == 'Lab' for p in source.parents):
        raise ValueError('SR1 source outside Lab requires --allow-nonlab')
    check_output(out_dir)
    scene = Scene(source)

    def produce(stage):
        kit = validate_kit(build_kit(scene, source))
        sources = scene.sync_sources()
        kit['source_files'] = sources
        write_json(stage / 'sanctuary-kit.json', kit)
        statues = [s for s in kit['statue_catalog'] if s['status'] in ('native', 'synthesized')]
        write_json(stage / 'kit-receipt.json', {
            'schema': 'bridge-sanctuary-kit-receipt', 'schema_version': 1, 'kit_sha256': sha256(stage / 'sanctuary-kit.json'),
            'source_files': sources, 'native_statue_constructs': len(kit['statues']), 'catalog_statues': len(statues),
            'walkway_end': kit['walkway']['end_pieces'][0], 'evidence_level': 'OFFLINE_EXTRACTED_HASH_VERIFIED'})

    try:
        return publish_kit(out_dir, produce)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError('incomplete or malformed SR1 scene metadata') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr1-dir', required=True, type=Path, help='SlimeRancher_Data of a LAB copy of SR1')
    parser.add_argument('--out', required=True, type=Path, help='fresh folder under Lab/RuntimeArtifacts or Lab/AssetCache')
    parser.add_argument('--allow-nonlab', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = extract_kit(args.sr1_dir, args.out, args.allow_nonlab)
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
