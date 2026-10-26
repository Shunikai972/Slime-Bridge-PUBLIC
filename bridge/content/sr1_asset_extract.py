"""Read-only UnityPy SR1 Rad extraction to a fresh, atomic Lab source cache.

No game launch, proprietary committed fixtures, appearance guessing or animation
fallback. SR1 and SR2 both consume unchanged left-handed Unity-local coordinates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import zlib

from bridge.content.sr1_animation import animation_paths, extract_animation

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVERTER_VERSION = 4


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, payload):
    Path(path).write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf-8')


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('nonfinite or malformed source number')
    return value


def vector(value, keys):
    return [number(value[k]) for k in keys]


def trs(tree):
    return {'local_position': vector(tree['m_LocalPosition'], 'xyz'),
            'local_rotation': vector(tree['m_LocalRotation'], 'xyzw'),
            'local_scale': vector(tree['m_LocalScale'], 'xyz')}


def flag(value):
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError('malformed source flag')
    return bool(value)


def ref(pointer, *, null=False):
    if (not isinstance(pointer, dict) or type(pointer.get('m_FileID')) is not int
            or type(pointer.get('m_PathID')) is not int
            or pointer['m_FileID'] < 0 or pointer['m_PathID'] < 0
            or (not null and pointer['m_PathID'] == 0)):
        raise ValueError('missing or malformed source PPtr')
    return pointer['m_FileID'], pointer['m_PathID']


class Sr1Assets:
    """One explicit source file (default: the prefab library) plus its referenced dependencies."""
    def __init__(self, data_dir, source_name='sharedassets2.assets'):
        import UnityPy
        from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
        self.data_dir = Path(data_dir).resolve()
        self.source_files = {}
        self.trees = {}
        self.headers = {}
        self.env = UnityPy.Environment()
        self.env.path = str(self.data_dir)
        original_load = self.env.load_file

        def traced_load(file, *args, **kwargs):
            if isinstance(file, (str, os.PathLike)):
                path = Path(file)
                if not path.is_file():
                    path = self.data_dir / file
                if path.is_file():
                    self.record(path)
            return original_load(file, *args, **kwargs)
        self.env.load_file = traced_load
        source = self.data_dir / source_name
        self.env.load_file(str(source))
        self.objects = [o for o in self.env.objects if o.assets_file.name == source.name]
        if not self.objects:
            raise ValueError('SR1 prefab library is empty')
        version = self.objects[0].assets_file.unity_version
        if not version.startswith('2019.4.'):
            raise ValueError('unsupported SR1 Unity version; expected 2019.4')
        self.unity_version = version
        generator = TypeTreeGenerator(version)
        # SRML retains Assembly-CSharp_old.dll with the same assembly identity.
        # Load the installed patched assembly once, never both identities.
        for dll in sorted((self.data_dir / 'Managed').glob('*.dll')):
            if dll.name == 'Assembly-CSharp_old.dll':
                continue
            self.record(dll)
            generator.load_dll(dll.read_bytes())
        self.env.typetree_generator = generator

    def record(self, path):
        path = Path(path).resolve()
        try:
            name = path.relative_to(self.data_dir).as_posix()
        except ValueError as exc:
            raise ValueError('source dependency escapes the selected SR1 data directory') from exc
        digest = sha256(path)
        previous = self.source_files.setdefault(name, digest)
        if previous != digest:
            raise ValueError('source file changed during extraction')

    def sync_sources(self):
        for name in list(self.env.files):
            path = Path(name)
            if not path.is_file():
                path = self.data_dir / name
            if not path.is_file() and path.name == 'unity default resources':
                path = self.data_dir / 'Resources' / path.name
            if not path.is_file():
                raise ValueError(f'cannot hash loaded source file: {name}')
            self.record(path)
        for name, digest in self.source_files.items():
            if sha256(self.data_dir / name) != digest:
                raise ValueError('source file changed during extraction')
        return dict(sorted(self.source_files.items()))

    @staticmethod
    def identity(obj):
        return obj.assets_file.name, obj.path_id

    def describe(self, obj):
        return {'file': obj.assets_file.name, 'path_id': obj.path_id, 'type': obj.type.name,
                'name': self.tree(obj).get('m_Name', '')}

    def class_name(self, obj):
        key = self.identity(obj)
        if key not in self.headers:
            if obj.type.name != 'MonoBehaviour':
                self.headers[key] = ''
            else:
                self.headers[key] = obj.parse_monobehaviour_head().m_Script.deref().read().m_ClassName
        return self.headers[key]

    def tree(self, obj):
        key = self.identity(obj)
        if key not in self.trees:
            self.trees[key] = obj.read_typetree()
        return self.trees[key]

    def follow(self, owner, pointer, kind=None, cls=None):
        from UnityPy.classes import PPtr
        file_id, path_id = ref(pointer)
        try:
            obj = PPtr(m_FileID=file_id, m_PathID=path_id, assetsfile=owner.assets_file).deref()
        except (KeyError, FileNotFoundError, ValueError) as exc:
            raise ValueError('unresolved source reference') from exc
        if obj is None or (kind and obj.type.name != kind) or (cls and self.class_name(obj) != cls):
            raise ValueError('source reference has missing or unexpected type')
        return obj

    def components(self, go, kind=None, cls=None):
        objects = [self.follow(go, c['component']) for c in self.tree(go)['m_Component']]
        return [o for o in objects if (not kind or o.type.name == kind) and (not cls or self.class_name(o) == cls)]

    def component(self, go, kind=None, cls=None):
        objects = self.components(go, kind, cls)
        if len(objects) != 1:
            raise ValueError(f'GameObject has no unique {kind or cls} component')
        return objects[0]

    def walk(self, go):
        seen = set()
        pending = [(go, self.tree(go)['m_Name'])]
        while pending:
            node, path = pending.pop(0)
            key = self.identity(node)
            if key in seen or len(seen) >= 4096:
                raise ValueError('cyclic or oversized prefab hierarchy')
            seen.add(key)
            transform = self.component(node, 'Transform')
            yield node, transform, path
            for pointer in self.tree(transform)['m_Children']:
                child = self.follow(transform, pointer, 'Transform')
                child_go = self.follow(child, self.tree(child)['m_GameObject'], 'GameObject')
                pending.append((child_go, path + '/' + self.tree(child_go)['m_Name']))


def resolve_rad(assets):
    return resolve_species(assets, 'slimeRad', 'Rad')


def resolve_species(assets, prefab_name, display, largo=False):
    """The prefab -> applicator -> definition -> default appearance chain of any SR1 species (or Largo) prefab."""
    roots = [o for o in assets.objects if o.type.name == 'GameObject' and assets.tree(o).get('m_Name') == prefab_name]
    if len(roots) != 1:
        raise ValueError(f'{display} prefab missing or ambiguous in SR1 prefab library')
    root = roots[0]
    applicator = assets.component(root, cls='SlimeAppearanceApplicator')
    at = assets.tree(applicator)
    definition = assets.follow(applicator, at['SlimeDefinition'], cls='SlimeDefinition')
    dt = assets.tree(definition)
    identifiable = assets.component(root, cls='Identifiable')
    if bool(dt['IsLargo']) != bool(largo) or dt['IdentifiableId'] != assets.tree(identifiable)['id']:
        raise ValueError(f'{display} definition and prefab identity disagree')
    defaults = dt['AppearancesDefault']
    if not isinstance(defaults, list) or len(defaults) != 1:
        raise ValueError(f'{display} default appearance ambiguous')
    appearance = assets.follow(definition, defaults[0], cls='SlimeAppearance')
    if ref(at['Appearance'], null=True)[1] and assets.identity(assets.follow(applicator, at['Appearance'])) != assets.identity(appearance):
        raise ValueError('nondefault appearance unsupported')
    visual = assets.follow(applicator, at['RootAppearanceObject'], 'GameObject')
    animator = assets.component(visual, 'Animator')
    if ref(assets.tree(appearance)['AnimatorOverride'], null=True)[1]:
        raise ValueError('appearance AnimatorOverride unsupported; refusing animation ambiguity')
    controller = assets.follow(animator, assets.tree(animator)['m_Controller'], 'AnimatorController')
    bones, bone_names = [], {}
    nodes = list(assets.walk(visual))
    roots = [(go, tr) for go, tr, _ in nodes if assets.tree(go)['m_Name'] == 'bone_root']
    if len(roots) != 1:
        raise ValueError('source skeleton root missing or ambiguous')

    def add(go, parent):
        transform = assets.component(go, 'Transform')
        t = assets.tree(transform)
        values = trs(t)
        name = assets.tree(go)['m_Name']
        if name in bone_names or len(bones) >= 4096:
            raise ValueError('ambiguous or oversized source skeleton')
        own = len(bones)
        bones.append({'name': name, 'parent': parent, 'p': values['local_position'],
                      'q': values['local_rotation'], 's': values['local_scale']})
        bone_names[name] = own
        for pointer in t['m_Children']:
            child = assets.follow(transform, pointer, 'Transform')
            add(assets.follow(child, assets.tree(child)['m_GameObject'], 'GameObject'), own)
    add(roots[0][0], -1)
    enums = {0: ''}
    for row in at['Bones']:
        bone_id = row['Bone']
        go = assets.follow(applicator, row['BoneObject'], 'GameObject')
        name = assets.tree(go)['m_Name']
        if type(bone_id) is not int or bone_id in enums or name not in bone_names:
            raise ValueError('applicator bone mapping missing or ambiguous')
        enums[bone_id] = name
    if len(set(enums.values())) != len(enums):
        raise ValueError('duplicate source bone enum target')
    return dict(root=root, applicator=applicator, definition=definition, appearance=appearance,
                visual=visual, controller=controller, bones=bones, enums=enums)


def resolve_skin(mesh_tree, skeleton, declared):
    import numpy as np
    hashes = mesh_tree.get('m_BoneNameHashes')
    poses = mesh_tree.get('m_BindPose')
    if not isinstance(hashes, list) or not hashes or not isinstance(poses, list) or len(hashes) != len(poses):
        raise ValueError('mesh bones and bind poses inconsistent')
    paths = animation_paths(skeleton)
    names = []
    for hashed, pose in zip(hashes, poses):
        if type(hashed) is not int or hashed not in paths or not paths[hashed]:
            raise ValueError('mesh bone hash unresolved; no positional fallback')
        name = paths[hashed].rsplit('/', 1)[-1]
        if name not in declared or name in names:
            raise ValueError('mesh bone binding not uniquely declared')
        values = [number(pose[f'e{r}{c}']) for r in range(4) for c in range(4)]
        if abs(float(np.linalg.det(np.array(values).reshape(4, 4)))) < 1e-12:
            raise ValueError('singular source bind pose')
        names.append(name)
    return names


def validate_geometry(geometry, skin_bones):
    vertices = geometry['vertices']
    if not vertices or len(vertices) > 1_000_000:
        raise ValueError('missing or oversized source mesh')
    for field, width in [('vertices', 3), ('normals', 3), ('tangents', 4), ('uv', 2), ('uv2', 2), ('colors', 4)]:
        values = geometry[field]
        if values and len(values) != len(vertices):
            raise ValueError('vertex attribute count mismatch')
        for row in values:
            if len(row) != width:
                raise ValueError('malformed source vertex attribute')
            for value in row:
                number(value)
    for group in geometry['triangles']:
        if not group or len(group) % 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in group):
            raise ValueError('invalid source triangle indices')
    if not geometry['triangles']:
        raise ValueError('missing source triangles')
    weights, poses = geometry['bone_weights'], geometry['bind_poses']
    if skin_bones:
        if len(weights) != len(vertices):
            raise ValueError('source skin weight count mismatch')
        if len(poses) != len(skin_bones):
            raise ValueError('source bind pose count mismatch')
        for row in weights:
            if len(row) != 8 or any(number(w) < 0 for w in row[:4]) or abs(sum(row[:4]) - 1) > 1e-4:
                raise ValueError('invalid source skin weight values')
            if any(type(i) is not int or not 0 <= i < len(skin_bones) for i in row[4:]):
                raise ValueError('source skin weight index out of range')
    elif weights or poses:
        raise ValueError('static renderer unexpectedly carries skin data')


def palette_entry(appearance, display='Rad'):
    palette = appearance['ColorPalette']
    values = [number(palette[k][c]) for k in ('Top', 'Middle', 'Bottom', 'Ammo') for c in 'rgba']
    return {'native_ref': 'SlimeDefinition.' + display, 'display_name': display,
            'save_set': str(appearance['SaveSet']), 'palette_legacy': values, 'palette_new': None}


class Materials:
    def __init__(self, assets, output):
        self.assets, self.output, self.images, self.rows = assets, output, {}, {}

    def texture(self, owner, pointer):
        obj = self.assets.follow(owner, pointer, 'Texture2D')
        key = self.assets.identity(obj)
        if key not in self.images:
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', self.assets.tree(obj)['m_Name'])
            filename = f'tex-{name}-{obj.path_id}.png'
            if (self.output / filename).exists():
                raise ValueError('ambiguous source texture filename')
            image = obj.read().image.convert('RGBA')
            image.save(self.output / filename)
            self.images[key] = filename
        return obj, self.images[key]

    def material(self, obj, role=None):
        key = self.assets.identity(obj)
        if key not in self.rows:
            tree = self.assets.tree(obj)
            shader = self.assets.follow(obj, tree['m_Shader'], 'Shader')
            st = self.assets.tree(shader)
            name = st.get('m_ParsedForm', {}).get('m_Name') or st.get('m_Name')
            if not name:
                raise ValueError('unresolved source shader identity')
            props = tree['m_SavedProperties']
            textures = []
            for prop, binding in props['m_TexEnvs']:
                if not ref(binding['m_Texture'], null=True)[1]:
                    continue
                if self.assets.follow(obj, binding['m_Texture']).type.name != 'Texture2D':
                    continue                       # cubemaps (Boom cracks ...) have no 2D image to carry
                texture, filename = self.texture(obj, binding['m_Texture'])
                textures.append({'property': prop, 'file': filename,
                    'source_name': self.assets.tree(texture)['m_Name'],
                    'scale': vector(binding['m_Scale'], 'xy'), 'offset': vector(binding['m_Offset'], 'xy')})
            self.rows[key] = {'name': tree['m_Name'], 'shader': name,
                'colors': {k: vector(v, 'rgba') for k, v in props['m_Colors']},
                'floats': {k: number(v) for k, v in props['m_Floats']}, 'textures': textures,
                'shader_keywords': tree['m_ShaderKeywords'], 'render_queue': tree['m_CustomRenderQueue']}
        row = dict(self.rows[key])
        if role:
            row['role'] = role
            row['source_role'] = role
        return row


def collect_parts(assets, resolved, lenient=False):
    """The LOD0 part of every appearance element. Strict (the Rad contract): exactly one LOD0 part with exactly one renderer. Lenient (any
    other species/Largo): an element may carry several LOD0 pieces (the two Phosphor wings), parts without a mesh renderer (trails, rotators)
    are effects, and a face body with several LOD0 variants (Quicksilver grounded/airborne) uses the first."""
    appearance = resolved['appearance']
    at = assets.tree(appearance)
    parts = []
    for structure in at['Structures']:
        element = assets.follow(appearance, structure['Element'], cls='SlimeAppearanceElement')
        selected = []
        for pointer in assets.tree(element)['Prefabs']:
            part = assets.follow(element, pointer, cls='SlimeAppearanceObject')
            pt = assets.tree(part)
            if flag(pt['IgnoreLODIndex']) or pt['LODIndex'] == 0:
                selected.append(part)
        if not lenient and len(selected) != 1:
            raise ValueError('appearance element LOD0 part missing or ambiguous')
        supports_faces = flag(structure['SupportsFaces'])
        built = 0
        for part in selected:                       # an element may carry several LOD0 pieces (the two Phosphor wings)
            pt = assets.tree(part)
            go = assets.follow(part, pt['m_GameObject'], 'GameObject')
            renderers = assets.components(go, 'SkinnedMeshRenderer') + assets.components(go, 'MeshRenderer')
            if len(renderers) != 1:
                if not lenient:
                    raise ValueError('LOD0 appearance part has no unique renderer')
                continue                            # trails, particle shells, rotators: effects without a mesh to carry
            override = structure['ElementMaterials'][pt['LODIndex']]
            pointers = override['Materials'] if flag(override['OverrideDefaults']) else structure['DefaultMaterials']
            materials = [(assets.follow(appearance, p, 'Material'), None) for p in pointers]
            if supports_faces:
                face = assets.follow(appearance, at['Face'], cls='SlimeFace')
                initial = assets.tree(resolved['applicator'])['SlimeExpression']
                expressions = [e for e in assets.tree(face)['ExpressionFaces'] if e['SlimeExpression'] == initial]
                if len(expressions) != 1:
                    raise ValueError('initial source face expression missing or ambiguous')
                rules = structure['FaceRules'][pt['LODIndex']]
                for field, rule, role in [('Eyes', 'ShowEyes', 'eyes'), ('Mouth', 'ShowMouth', 'mouth')]:
                    pointer = expressions[0][field]
                    if flag(rules[rule]) and ref(pointer, null=True)[1]:
                        materials.append((assets.follow(face, pointer, 'Material'), role))
            parts.append(dict(part=part, go=go, renderer=renderers[0], materials=materials,
                              supports_faces=supports_faces, element_name=assets.tree(element)['m_Name']))
            built += 1
            if supports_faces:
                break
        if not built and not selected:
            raise ValueError('appearance element LOD0 part missing')
    if sum(p['supports_faces'] for p in parts) != 1:
        raise ValueError('source body selection ambiguous')
    return parts


def static_attachment(bones, parent_name, local):
    """Compose bone attachment in rest space; refuse shear rather than distort."""
    import numpy as np
    from bridge.content.sr2_asset_extract import _mat4_from_trs
    if not parent_name:
        return local
    world = []
    for bone in bones:
        matrix = _mat4_from_trs(bone['p'], bone['q'], bone['s'])
        world.append(matrix if bone['parent'] < 0 else world[bone['parent']] @ matrix)
    parent = next(i for i, b in enumerate(bones) if b['name'] == parent_name)
    matrix = world[parent] @ _mat4_from_trs(local['local_position'], local['local_rotation'], local['local_scale'])
    scales = np.linalg.norm(matrix[:3, :3], axis=0)
    if any(abs(s) < 1e-12 for s in scales):
        raise ValueError('degenerate static attachment scale')
    rotation = matrix[:3, :3] / scales
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-6) or np.linalg.det(rotation) < 0:
        raise ValueError('static attachment shear/reflection unsupported')
    return {'local_position': matrix[:3, 3].tolist(), 'local_rotation': _quaternion(rotation),
            'local_scale': scales.tolist()}


def _quaternion(m):
    """Unit quaternion (x, y, z, w) of a proper rotation matrix."""
    import math
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        k = math.sqrt(trace + 1.0) * 2
        q = [(m[2][1] - m[1][2]) / k, (m[0][2] - m[2][0]) / k, (m[1][0] - m[0][1]) / k, 0.25 * k]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        k = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [0.25 * k, (m[0][1] + m[1][0]) / k, (m[0][2] + m[2][0]) / k, (m[2][1] - m[1][2]) / k]
    elif m[1][1] > m[2][2]:
        k = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][1] + m[1][0]) / k, 0.25 * k, (m[1][2] + m[2][1]) / k, (m[0][2] - m[2][0]) / k]
    else:
        k = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[0][2] + m[2][0]) / k, (m[1][2] + m[2][1]) / k, 0.25 * k, (m[1][0] - m[0][1]) / k]
    norm = math.sqrt(sum(v * v for v in q))
    return [float(v) / norm for v in q]


def extract_parts(assets, resolved, materials, prefab_name='slimeRad', lenient=False):
    from bridge.content.sr2_asset_extract import decode_mesh
    skinned, static, effects = [], [], []
    enums, bones = resolved['enums'], resolved['bones']
    for part in collect_parts(assets, resolved, lenient):
        obj, go, renderer = part['part'], part['go'], part['renderer']
        pt, rt = assets.tree(obj), assets.tree(renderer)
        transform = assets.component(go, 'Transform')
        tt = assets.tree(transform)
        if ref(tt['m_Father'], null=True)[1]:
            raise ValueError('nested appearance part transform unsupported')
        parent = enums.get(pt['ParentBone'])
        if parent is None:
            raise ValueError('source part attachment bone unresolved')
        if renderer.type.name == 'SkinnedMeshRenderer':
            mesh = assets.follow(renderer, rt['m_Mesh'], 'Mesh')
            if parent:
                raise ValueError('bone-parented skinned part unsupported')
            declared = [enums.get(e) for e in pt['AttachedBones']]
            if not declared or any(not n for n in declared):
                raise ValueError('source skin bone declaration missing')
            skin = resolve_skin(assets.tree(mesh), bones, declared)
            root_bone = enums.get(pt['RootBone'])
            if not root_bone:
                raise ValueError('source root bone unresolved')
        else:
            mesh_filter = assets.component(go, 'MeshFilter')
            mesh = assets.follow(mesh_filter, assets.tree(mesh_filter)['m_Mesh'], 'Mesh')
            skin, root_bone = [], ''
        geometry = decode_mesh(mesh.read(), mesh)
        validate_geometry(geometry, skin)
        if len(part['materials']) < len(geometry['triangles']):
            raise ValueError('appearance materials do not cover submeshes')
        local = trs(tt)
        body = part['supports_faces']
        if not body and renderer.type.name == 'MeshRenderer':
            local = static_attachment(bones, parent, local)
        row = {'enabled': flag(rt['m_Enabled']) and body, 'role': 'body' if body else 'source-effect',
               'path': prefab_name + '/Appearance/' + assets.tree(go)['m_Name'],
               'mesh_name': assets.tree(mesh)['m_Name'], **local,
               **{k: v for k, v in geometry.items() if k != 'vertex_count'},
               'materials': [materials.material(m, role) for m, role in part['materials']],
               'skin_bones': skin, 'root_bone': root_bone,
               'source_part': assets.describe(obj), 'source_parent_bone': parent}
        (skinned if skin else static).append(row)
        if not body:
            effects.append({'name': part['element_name'], 'attachment_bone': parent,
                           'source_local_transform': trs(tt), 'renderer_path': row['path'],
                           'rendered': False, 'source': assets.describe(obj),
                           'components': component_descriptions(assets, go),
                           'materials': row['materials']})
    if not any(r['enabled'] for r in skinned + static):
        raise ValueError('no active source body geometry')
    return skinned, static, effects


def component_descriptions(assets, go):
    return [{'type': assets.class_name(o) if o.type.name == 'MonoBehaviour' else o.type.name,
             'source': assets.describe(o),
             'parameters': {k: v for k, v in assets.tree(o).items() if k not in ('m_GameObject', 'm_Script')}}
            for o in assets.components(go)]


def extract_effects(assets, resolved, entries):
    # Gameplay RadSource/RadSlimeExpand and particle/light/glow controllers are
    # descriptive only. Follow the exact prefab hierarchy, never a name search
    # across unrelated assets or an inferred emitter.
    kinds = {'ParticleSystem', 'ParticleSystemRenderer', 'Light'}
    for go, transform, path in assets.walk(resolved['root']):
        components = assets.components(go)
        selected = [o for o in components if o.type.name in kinds or
                    (o.type.name == 'MonoBehaviour' and any(k in assets.class_name(o).lower()
                        for k in ('rad', 'glow', 'rubberbone')))]
        if selected:
            entries.append({'name': assets.tree(go)['m_Name'], 'attachment_path': path,
                            'source_local_transform': trs(assets.tree(transform)), 'rendered': False,
                            'components': component_descriptions(assets, go)})
    return {'schema': 'bridge-source-effects', 'schema_version': 1, 'source_game': 'SR1',
            'rendered': False, 'entries': entries,
            'limits': 'serialized descriptions only; no particle, light, aura, glow or procedural runtime reconstruction'}


def export_largo_icon(assets, resolved, output):
    """A Largo has no icon of its own: both parents' icons drawn overlapped on one 1024 canvas, described as a full-rect sprite."""
    from PIL import Image
    definition = assets.tree(resolved['definition'])
    images, names = [], []
    for pointer in definition['BaseSlimes']:
        parent = assets.follow(resolved['definition'], pointer, cls='SlimeDefinition')
        pt = assets.tree(parent)
        appearance = assets.follow(parent, pt['AppearancesDefault'][0], cls='SlimeAppearance')
        names.append(pt['Name'])
        icon = assets.tree(appearance)['Icon']
        if not ref(icon, null=True)[1]:
            images.append(None)
            continue
        sprite = assets.follow(appearance, icon, 'Sprite')
        texture = assets.follow(sprite, assets.tree(sprite)['m_RD']['texture'], 'Texture2D')
        images.append(texture.read().image.convert('RGBA'))
    if len(images) != 2 or not any(images):
        raise ValueError('neither Largo parent has an icon')
    canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    if all(images):
        for image, origin in ((images[0], (40, 250)), (images[1], (284, 70))):
            canvas.alpha_composite(image.resize((700, 700), Image.LANCZOS), origin)
    else:
        canvas.alpha_composite((images[0] or images[1]).resize((860, 860), Image.LANCZOS), (82, 82))
    name = 'iconLargo_' + ''.join(names)
    canvas.save(output / (name + '.source.png'))
    half = 5.12
    return {'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': False, 'file': name + '.source.png',
            'texture_size': [1024, 1024], 'rect': [0, 0, 1024, 1024], 'pivot': [0.5, 0.5], 'pixels_per_unit': 100,
            'vertices': [[-half, -half], [half, -half], [half, half], [-half, half]], 'triangles': [0, 1, 2, 0, 2, 3],
            'source_sprite_name': name, 'source_sprite_path_id': 0, 'source_appearance_path_id': resolved['appearance'].path_id,
            'source_texture_path_id': 0, 'source_texture_rect': [0, 0, 1024, 1024], 'source_texture_rect_offset': [0, 0],
            'source_settings_raw': 64, 'packing_rotation': 0, 'pixel_encoding': 'source-texture-rgba-unresampled',
            'composed_from_parent_sprites': names}


def export_icon(assets, resolved, output):
    from UnityPy.helpers.MeshHelper import MeshHandler
    appearance = resolved['appearance']
    if assets.tree(resolved['definition'])['IsLargo'] and not ref(assets.tree(appearance)['Icon'], null=True)[1]:
        return export_largo_icon(assets, resolved, output)
    sprite = assets.follow(appearance, assets.tree(appearance)['Icon'], 'Sprite')
    tree = assets.tree(sprite)
    rd = tree['m_RD']
    if (tree['m_AtlasTags'] or ref(tree['m_SpriteAtlas'], null=True)[1]
            or rd['settingsRaw'] != 64 or ref(rd['alphaTexture'], null=True)[1]
            or rd.get('downscaleMultiplier', 1) != 1):
        raise ValueError('packed/rotated/separate-alpha source icon unsupported')
    texture = assets.follow(sprite, rd['texture'], 'Texture2D')
    image = texture.read().image.convert('RGBA')
    rect = vector(tree['m_Rect'], ('x', 'y', 'width', 'height'))
    if rect != [0, 0, *image.size] or any(tree['m_Border'].values()):
        raise ValueError('non-full-texture or bordered source icon unsupported')
    mesh = MeshHandler(sprite.read().m_RD, sprite.version)
    mesh.process()
    vertices = [[number(float(v[0])), number(float(v[1]))] for v in mesh.m_Vertices]
    triangles = [int(i) for group in mesh.get_triangles() for tri in group for i in tri]
    if not vertices or not triangles or len(triangles) % 3 or any(not 0 <= i < len(vertices) for i in triangles):
        raise ValueError('source icon geometry invalid')
    from bridge.content.sr1_icon import build_icon_descriptor
    descriptor = build_icon_descriptor(tree, image.size, vertices, triangles,
        sprite_id=sprite.path_id, appearance_id=appearance.path_id)
    image.save(output / descriptor['file'])
    return descriptor


def build_source_cache(assets, output, target=None):
    target = target or {'species': 'Rad', 'canonical': 'slime.rad', 'prefab': 'slimeRad', 'largo': False}
    display, prefab_name = target['species'], target['prefab']
    resolved = resolve_species(assets, prefab_name, display, bool(target.get('largo')))
    skinned, static, effects = extract_parts(assets, resolved, Materials(assets, output), prefab_name, bool(target.get('lenient')))
    animation, provenance = extract_animation(assets, resolved['controller'], resolved['bones'])
    icon = export_icon(assets, resolved, output)
    model = {'schema': 'bridge-native-mesh', 'schema_version': 2, 'source_game': 'SR1',
             'source_kind': 'offline-unitypy', 'reference_id': 'SlimeDefinition.' + display,
             'display_name': display, 'prefab_name': prefab_name,
             'prefab_scale': number(assets.tree(resolved['definition'])['PrefabScale']),
             'visual_root': 'Appearance',
             'visual_transform': trs(assets.tree(assets.component(resolved['visual'], 'Transform'))),
             'skeleton': {'bone_count': len(resolved['bones']), 'bones': resolved['bones']},
             'skinned_renderers': skinned, 'static_renderers': static,
             'source_animation': animation, 'source_effects': {'file': 'source-effects.json', 'rendered': False},
             'coordinate_system': {'handedness': 'Unity-left-handed', 'unit': 'Unity source unit',
                                   'axis_conversion': 'none', 'scale_conversion': 1}}
    write_json(output/'model.json', model)
    write_json(output/'icon-descriptor.json', icon)
    write_json(output/'source-effects.json', extract_effects(assets, resolved, effects))
    write_json(output/'source-animation-provenance.json', provenance)
    sources = assets.sync_sources()
    palette = {'schema': 'bridge-appearance-colors', 'schema_version': 1, 'realm': 'SR1',
               'run_id': 'offline-sr1-' + target['canonical'].replace('.', '-'), 'collector_version': 'sr1-unitypy-v1',
               'active_sha256': sources['Managed/Assembly-CSharp.dll'],
               'entries': [palette_entry(assets.tree(resolved['appearance']), display)], 'errors': []}
    write_json(output/'appearance-colors.json', palette)
    rows = skinned + static
    receipt = {'schema': 'bridge-native-mesh-receipt', 'schema_version': 2, 'target': display,
               'reference_id': 'SlimeDefinition.' + display, 'model': 'model.json',
               'model_sha256': sha256(output/'model.json'), 'source_game': 'SR1',
               'source': {'data_dir': str(assets.data_dir), 'kind': 'offline-unitypy',
                          'unity_version': assets.unity_version, 'assembly_policy': 'installed patched assembly; old duplicate excluded'},
               'source_files': sources, 'bone_count': len(resolved['bones']),
               'skinned_renderers': len(skinned), 'static_renderers': len(static),
               'vertex_total': sum(len(r['vertices']) for r in rows),
               'parts': [{'role': r['role'], 'mesh': r['mesh_name'], 'vertices': len(r['vertices']),
                          'enabled': r['enabled']} for r in rows],
               'clips': [c['name'] for c in animation['clips']], 'icon_size': icon['texture_size'],
               'palette': palette['entries'][0], 'evidence_level': 'OFFLINE_EXTRACTED_HASH_VERIFIED'}
    write_json(output/'dump-receipt.json', receipt)
    return {'icon': icon, **{k: receipt[k] for k in ('vertex_total', 'parts', 'bone_count', 'clips', 'icon_size', 'palette')}}


def check_output(output):
    output = Path(output).absolute()
    roots = [REPO_ROOT/'Lab/RuntimeArtifacts', REPO_ROOT/'Lab/AssetCache']
    if '..' in output.parts or not any(output != root and output.is_relative_to(root) for root in roots):
        raise ValueError('output must be a fresh private directory inside this worktree Lab')
    for part in (output, *output.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('output reparse point refused')
    if output.exists():
        raise ValueError('output directory exists; choose a fresh cache')
    return output


def publish_cache(output, producer, canonical='slime.rad'):
    output = check_output(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.sr1-rad-stage-', dir=output.parent))
    try:
        result = producer(stage)
        files = {p.name: sha256(p) for p in sorted(stage.iterdir()) if p.is_file()}
        if set(files) != {p.name for p in stage.iterdir()}:
            raise ValueError('unexpected nested cache payload')
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        manifest = {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                    'canonical_id': canonical, 'converter_version': CONVERTER_VERSION,
                    'source_game': 'SR1', 'source_run_id': 'offline-' + stamp,
                    'published_utc': stamp, 'files': files}
        if result.get('icon') is not None:
            manifest['icon'] = result['icon']
        write_json(stage/'cache-manifest.json', manifest)
        check_output(output)
        # Windows rename is atomic on this same-volume sibling and refuses an
        # existing destination; never overwrite a previous accepted cache.
        stage.rename(output)
        return {'ok': True, 'canonical_id': canonical, 'cache_directory': str(output),
                'manifest_sha256': sha256(output/'cache-manifest.json'),
                'model_sha256': files['model.json'], **result}
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def extract_species(species, canonical, sr1_dir, out_dir, allow_nonlab=False, *, prefab=None, largo=False, assets=None, lenient=None):
    """One SR1 species (prefab `slime<Species>`) or Largo (prefab given: SR1 names them in its own parent order) into a cache."""
    source = Path(sr1_dir).resolve()
    if not source.is_dir():
        raise ValueError('SR1 data directory missing')
    # --allow-nonlab authorizes read-only source elsewhere; never output escape.
    if not allow_nonlab and not any(p.name == 'Lab' for p in source.parents):
        raise ValueError('SR1 source outside Lab requires --allow-nonlab')
    check_output(out_dir)
    assets = assets or Sr1Assets(source)
    target = {'species': species, 'canonical': canonical, 'prefab': prefab or 'slime' + species, 'largo': largo,
              'lenient': (canonical != 'slime.rad') if lenient is None else bool(lenient)}   # Rad keeps its strict contract
    try:
        return publish_cache(out_dir, lambda stage: build_source_cache(assets, stage, target), canonical)
    except (KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError('incomplete or malformed SR1 source metadata') from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--species', required=True)
    parser.add_argument('--canonical', required=True)
    parser.add_argument('--sr1-dir', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--allow-nonlab', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = extract_species(args.species, args.canonical, args.sr1_dir, args.out, args.allow_nonlab)
    except (ValueError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
