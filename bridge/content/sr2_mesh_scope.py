"""Opt-in, read-only LOD0 mesh scope; NOT a complete-source cache exporter.

Returns in-memory source objects/geometry, never writes files or adapts shaders.
Generated local MonoBehaviour schemas must already be loaded on assets.env.
"""
from __future__ import annotations

import math
import re

from bridge.content import sr2_appearance
from bridge.content.sr2_appearance import resolve_part_binding, source_object_index, source_ref


def _bounded(value, maximum, label, minimum=1):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f'{label} missing, malformed or oversized budget')
    return value


def _flag(value):
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError('mesh-scope flag malformed')
    return bool(value)


def _name(value):
    if (not isinstance(value, str) or not value or len(value) > 256
            or '/' in value or '\\' in value or value in ('.', '..')):
        raise ValueError('mesh-scope name malformed or unsupported')
    return value


def _vector(value, size):
    if (not isinstance(value, (list, tuple)) or len(value) != size
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        raise ValueError('mesh-scope numeric vector malformed')
    return list(value)


def _trs(tree):
    p = _vector([tree['m_LocalPosition'][k] for k in 'xyz'], 3)
    q = _vector([tree['m_LocalRotation'][k] for k in 'xyzw'], 4)
    s = _vector([tree['m_LocalScale'][k] for k in 'xyz'], 3)
    if any(v == 0 for v in s) or abs(sum(v * v for v in q) - 1) > 1e-4:
        raise ValueError('mesh-scope TRS is singular or quaternion is not unit length')
    return {'local_position': p, 'local_rotation': q, 'local_scale': s}

SCOPE = 'appearance-lod0-mesh-only-v1'
MAX_STRUCTURES = 64
MAX_LODS = 8
MAX_COMPONENTS = 64
MAX_NODES = 128
MAX_DEPTH = 8
MAX_BONES = 512
MAX_OBJECTS = 800_000
MAX_VERTICES = 100_000
MAX_INDICES = 600_000
MAX_VERTEX_BYTES = 32_000_000


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{label} malformed or budget exceeded')
    return value


def _buffer_size(value, maximum):
    if not isinstance(value, (bytes, bytearray, list)) or not 1 <= len(value) <= maximum:
        raise ValueError('source mesh buffer missing or budget exceeded')
    if isinstance(value, list) and any(type(v) is not int or not 0 <= v <= 255 for v in value):
        raise ValueError('source mesh byte buffer malformed')
    return len(value)


def _mesh_layout(tree):
    """Preflight before MeshHandler allocation; verified inline 16-bit slice only."""
    if type(tree['m_MeshCompression']) is not int or tree['m_MeshCompression'] != 0:
        raise ValueError('compressed source meshes unsupported')
    if type(tree['m_IndexFormat']) is not int or tree['m_IndexFormat'] != 0:
        raise ValueError('only verified source 16-bit indices supported')
    stream = tree['m_StreamData']
    # Meshes are stored either inline or in the shared resources.assets.resS stream (vertex and index data both; the inline
    # buffers are then empty). Any other external file stays refused.
    streamed = stream['path'] != '' or (type(stream['size']) is int and stream['size'] != 0)
    if streamed:
        if (not (stream['path'] == 'resources.assets.resS' or str(stream['path']).startswith('archive:/'))
                or type(stream['offset']) is not int or stream['offset'] < 0
                or type(stream['size']) is not int or not 1 <= stream['size'] <= MAX_VERTEX_BYTES):
            raise ValueError('streamed source geometry outside resources.assets.resS unsupported')
    elif type(stream['offset']) is not int or stream['offset'] != 0 or type(stream['size']) is not int:
        raise ValueError('source mesh stream descriptor malformed')
    vd = tree['m_VertexData']
    vertices = _integer(vd['m_VertexCount'], 3, MAX_VERTICES, 'source vertex count')
    _bounded(vd['m_Channels'], 16, 'source vertex channels')
    if not streamed:
        _buffer_size(vd['m_DataSize'], MAX_VERTEX_BYTES)
        index_bytes = _buffer_size(tree['m_IndexBuffer'], MAX_INDICES * 2)
    else:
        index_bytes = None
    indices = 0
    for submesh in _bounded(tree['m_SubMeshes'], 16, 'source submeshes'):
        start = _integer(submesh['firstByte'], 0, MAX_INDICES * 2, 'source first byte')
        count = _integer(submesh['indexCount'], 3, MAX_INDICES, 'source index count')
        first = _integer(submesh['firstVertex'], 0, vertices - 1, 'source first vertex')
        n = _integer(submesh['vertexCount'], 1, vertices, 'source submesh vertices')
        if (type(submesh['topology']) is not int or submesh['topology'] != 0
                or type(submesh['baseVertex']) is not int or submesh['baseVertex'] != 0
                or count % 3 or start != indices * 2 or first + n > vertices):
            raise ValueError('unsupported source topology, base vertex or buffer slice')
        indices += count
        if indices > MAX_INDICES:
            raise ValueError('source index budget exceeded')
    if index_bytes is not None and indices * 2 != index_bytes:
        raise ValueError('source submesh/index buffer coverage mismatch')
    _bounded(tree['m_BindPose'], MAX_BONES, 'source bind poses', minimum=0)
    _bounded(tree['m_BoneNameHashes'], MAX_BONES, 'source bone hashes', minimum=0)
    return vertices, indices


class _Graph:
    def __init__(self, assets):
        _bounded(assets._objects, MAX_OBJECTS, 'source objects')
        self.objects = source_object_index(assets)
        if any(pid == 0 or (pid < 0 and not sr2_appearance.ALLOW_EXTERNAL_REFS) for pid in self.objects):
            raise ValueError('source object identifier must be positive')
        self.trees = {}
        self.component_cache = {}
        self.used_nodes = set()

    def obj(self, pointer, kind=None, class_name=None):
        pid = source_ref(pointer)
        obj = self.objects.get(pid)
        if not pid or obj is None or (kind and obj.type.name != kind):
            raise ValueError('missing or unsupported mesh-scope reference (%s %s file %s%s)' % (
                kind or 'object', pid, pointer.get('m_FileID') if isinstance(pointer, dict) else '?',
                '' if obj is None else ', found a ' + obj.type.name))
        if class_name and self.class_name(obj) != class_name:
            raise ValueError('unexpected mesh-scope MonoBehaviour class')
        return obj

    def class_name(self, obj):
        if obj.type.name != 'MonoBehaviour':
            raise ValueError('expected mesh-scope MonoBehaviour')
        return _name(obj.parse_monobehaviour_head().m_Script.deref().read().m_ClassName)

    def tree(self, obj):
        if obj.path_id not in self.trees:
            value = obj.read_typetree()
            if not isinstance(value, dict):
                raise ValueError('mesh-scope typetree malformed')
            self.trees[obj.path_id] = value
        return self.trees[obj.path_id]

    def components(self, go):
        if go.path_id not in self.component_cache:
            refs = _bounded(self.tree(go)['m_Component'], MAX_COMPONENTS, 'components')
            rows = [self.obj(c['component']) for c in refs]
            if len({c.path_id for c in rows}) != len(rows):
                raise ValueError('duplicate mesh-scope component')
            for component in rows:
                if self.obj(self.tree(component)['m_GameObject'], 'GameObject') is not go:
                    raise ValueError('appearance component belongs to another GameObject')
            self.component_cache[go.path_id] = rows
        return self.component_cache[go.path_id]

    def transform(self, go):
        rows = [c for c in self.components(go) if c.type.name == 'Transform']
        if len(rows) != 1:
            raise ValueError('no unique mesh-scope Transform')
        return rows[0]

    def subtree(self, root):
        """Standalone SAO root, bounded reciprocal Transform edges, no aliases."""
        pending = [(root, _name(self.tree(root)['m_Name']), 0, 0)]
        count = 0
        while pending:
            go, path, depth, expected_parent = pending.pop()
            count += 1
            if count > MAX_NODES or depth > MAX_DEPTH:
                raise ValueError('appearance subtree node/depth budget exceeded')
            if go.path_id in self.used_nodes:
                raise ValueError('duplicate or cyclic mesh-scope subtree node')
            self.used_nodes.add(go.path_id)
            components = self.components(go)
            # Light: the SR2 1.x Hyper slime's glow. Not ported (no geometry); the node is walked like any other.
            allowed = {'Transform', 'MonoBehaviour', 'MeshFilter', 'SkinnedMeshRenderer',
                       'MeshRenderer', 'ParticleSystemRenderer', 'ParticleSystem', 'Light'}
            unsupported = sorted({c.type.name for c in components if c.type.name not in allowed})
            if unsupported:
                raise ValueError('unsupported appearance component or renderer: ' + ', '.join(unsupported))
            transform = self.transform(go)
            tt = self.tree(transform)
            if source_ref(tt['m_Father']) != expected_parent:
                raise ValueError('nonreciprocal or nested appearance root Transform')
            _trs(tt)
            _flag(self.tree(go)['m_IsActive'])
            children = _bounded(tt['m_Children'], MAX_NODES, 'children', minimum=0)
            ids = [source_ref(p) for p in children]
            if len(set(ids)) != len(ids):
                raise ValueError('duplicate appearance child Transform')
            yield go, transform, components, path
            for pointer in reversed(children):
                child_transform = self.obj(pointer, 'Transform')
                child = self.obj(self.tree(child_transform)['m_GameObject'], 'GameObject')
                if self.transform(child) is not child_transform:
                    raise ValueError('appearance child Transform not owned by its GameObject')
                pending.append((child, path + '/' + _name(self.tree(child)['m_Name']),
                                depth + 1, transform.path_id))

    def materials(self, pointers):
        rows = [self.obj(p, 'Material') for p in _bounded(pointers, 16, 'materials')]
        provenance = []
        for material in rows:
            mt = self.tree(material)
            shader = self.obj(mt['m_Shader'], 'Shader')
            provenance.append({'material_path_id': material.path_id,
                               'material_name': _name(mt['m_Name']),
                               'shader_path_id': shader.path_id,
                               'shader_name': self.tree(shader)['m_ParsedForm']['m_Name']})
        return rows, provenance


def _ptr(pid):
    return {'m_FileID': 0, 'm_PathID': pid}


def _bones(graph, applicator, skeleton):
    _bounded(skeleton, MAX_BONES, 'skeleton')
    names = set()
    for index, bone in enumerate(skeleton):
        name = _name(bone['name'])
        parent = bone['parent']
        if (name in names or type(parent) is not int or not -1 <= parent < index
                or (index > 0 and parent == -1)):
            raise ValueError('ambiguous or malformed source skeleton')
        names.add(name)
        _vector(bone['p'], 3)
        _vector(bone['q'], 4)
        _vector(bone['s'], 3)
    bone_names, bone_ids = {}, {}
    for row in _bounded(applicator['Bones'], MAX_BONES, 'applicator bones'):
        enum = row['Bone']
        go = graph.obj(row['BoneObject'], 'GameObject')
        name = _name(graph.tree(go)['m_Name'])
        if type(enum) is not int or enum <= 0 or enum in bone_names or name in bone_names.values() or name not in names:
            raise ValueError('duplicate or unresolved applicator bone')
        bone_names[enum], bone_ids[enum] = name, go.path_id
    return bone_names, bone_ids


def _binding(graph, sao, transform, bone_names, bone_ids, mesh_tree=None, skeleton=None):
    part = graph.tree(sao)
    parent, root, attached = part['ParentBone'], part['RootBone'], part['AttachedBones']
    if (type(parent) is not int or parent < 0 or type(root) is not int or root < 0
            or not isinstance(attached, list) or len(attached) > MAX_BONES
            or any(type(b) is not int or b <= 0 for b in attached)
            or len(set(attached)) != len(attached)):
        raise ValueError('malformed appearance bone binding')
    tt = graph.tree(transform)
    binding = {**_trs(tt), 'source_transform_id': transform.path_id,
               'source_transform_parent_id': source_ref(tt['m_Father'])}
    if mesh_tree is not None:
        binding.update(resolve_part_binding(part, bone_names, mesh_tree, skeleton))
        binding.update(source_parent_bone=parent, source_root_bone=root,
                       source_attached_bones=list(attached))
    else:
        if parent == 0 and 1 in bone_names:
            parent = 1                      # "no bone" (the Shadow aura): the part stays on the visual root
        if parent not in bone_names or root != 0 or attached:
            raise ValueError('unsupported or unresolved rigid appearance attachment')
        binding.update(parent_bone=bone_names[parent], source_parent_bone=parent,
                       source_parent_bone_object_id=bone_ids[parent], source_root_bone=root,
                       source_attached_bones=list(attached))
    return binding


def _face_passes(graph, appearance, applicator, structure, material_refs):
    expression = applicator['SlimeExpression']
    if type(expression) is not int or expression <= 0:
        raise ValueError('source face expression malformed')
    face = graph.obj(appearance['_face'], 'MonoBehaviour', 'SlimeFace')
    expressions = _bounded(graph.tree(face)['ExpressionFaces'], 128, 'face expressions')
    matches = [r for r in expressions if type(r['SlimeExpression']) is int and r['SlimeExpression'] == expression]
    if len(matches) != 1:
        raise ValueError('no unique source face expression')
    rules = _bounded(structure['FaceRules'], MAX_LODS, 'face LOD rules')[0]
    passes = [{'face_role': 'base-null', 'source_path_id': graph.obj(p, 'Material').path_id}
              for p in material_refs]
    refs = list(material_refs)
    for field, role, ref_field in [('ShowEyes', 'eyes', 'Eyes'), ('ShowMouth', 'mouth', 'Mouth')]:
        if _flag(rules[field]):
            pointer = matches[0][ref_field]
            refs.append(pointer)
            passes.append({'face_role': role, 'source_path_id': graph.obj(pointer, 'Material').path_id})
    return refs, {'source_face': True, 'source_face_expression': expression,
                  'source_face_materials': passes}


def _geometry(geometry, kind, binding):
    vertices = _bounded(geometry['vertices'], MAX_VERTICES, 'geometry vertices')
    if type(geometry['vertex_count']) is not int or geometry['vertex_count'] != len(vertices):
        raise ValueError('decoded geometry vertex count mismatch')
    for vertex in vertices:
        _vector(vertex, 3)
    triangles = _bounded(geometry['triangles'], 16, 'geometry submeshes')
    count = 0
    for row in triangles:
        _bounded(row, MAX_INDICES, 'geometry indices', 3)
        count += len(row)
        if count > MAX_INDICES:
            raise ValueError('geometry index budget exceeded')
        if len(row) % 3 or any(type(i) is not int or not 0 <= i < len(vertices) for i in row):
            raise ValueError('decoded geometry triangle indices malformed')
    for channel, size in [('normals', 3), ('uv', 2), ('uv2', 2), ('colors', 4), ('tangents', 4)]:
        values = geometry[channel]
        if not isinstance(values, list) or len(values) not in (0, len(vertices)):
            raise ValueError('decoded mesh optional channel length mismatch')
        for row in values:
            _vector(row, size)
    poses, weights = geometry['bind_poses'], geometry['bone_weights']
    if kind == 'static':
        if poses or weights:
            raise ValueError('static appearance mesh unexpectedly has skin data')
    else:
        n = len(binding['skin_bones'])
        if not isinstance(poses, list) or len(poses) != n or not isinstance(weights, list) or len(weights) != len(vertices):
            raise ValueError('decoded mesh skin arrays inconsistent with appearance binding')
        for row in poses:
            _vector(row, 16)
        for row in weights:
            if not isinstance(row, list) or len(row) != 8:
                raise ValueError('decoded bone weight malformed')
            values = _vector(row[:4], 4)
            if any(w < 0 for w in values) or abs(sum(values) - 1) > 1e-4:
                raise ValueError('decoded bone weights are not normalized')
            if any(type(i) is not int or not 0 <= i < n for i in row[4:]):
                raise ValueError('decoded bone indices outside skin binding')
    return count


def _embeds_face(element_name):
    """An element named like a body that supports faces is a body with the face painted on (Saber), not a separate face mesh."""
    return 'body' in element_name.lower()


def _derive_candidates(graph, appearance):
    """Skinned part meshes straight from the appearance graph (a Largo's parts carry no species-named meshes to look up)."""
    out = []
    for structure in _bounded(appearance['_structures'], MAX_STRUCTURES, 'appearance structures'):
        element = graph.obj(structure['Element'], 'MonoBehaviour', 'SlimeAppearanceElement')
        element_name = graph.tree(element).get('m_Name') or ''
        sao = graph.obj(_bounded(graph.tree(element)['Prefabs'], MAX_LODS, 'element LOD prefabs')[0], 'MonoBehaviour', 'SlimeAppearanceObject')
        go = graph.obj(graph.tree(sao)['m_GameObject'], 'GameObject')
        renderers = [c for c in graph.components(go) if c.type.name == 'SkinnedMeshRenderer']
        if len(renderers) != 1:
            continue                       # rigid attachments and particle parts are read inline by the scope reader
        mesh_obj = graph.obj(graph.tree(renderers[0])['m_Mesh'], 'Mesh')
        mesh = mesh_obj.read()
        embeds_face = False
        if _flag(structure['SupportsFaces']) and _embeds_face(element_name) and not any(c['role'] == 'body' for c in out):
            role, embeds_face = 'body', True       # Saber: one body mesh that also carries the face passes
        elif _flag(structure['SupportsFaces']):
            role = 'face'
        elif 'body' in element_name.lower():
            role = 'body'
        else:
            role = (re.sub(r'[^0-9a-z]+', '_', element_name.lower()).strip('_') or 'part')
        out.append({'role': role, 'mesh_name': mesh.m_Name, 'mesh': mesh, 'mesh_obj': mesh_obj, 'embeds_face': embeds_face})
    return out


def _rgba(value):
    if not isinstance(value, dict):
        return None
    out = [float(value.get(k, 0.0)) for k in ('r', 'g', 'b', 'a')]
    return out if all(math.isfinite(v) and abs(v) < 1000 for v in out) else None


def _effect_rows(graph, nodes, shell_path):
    """Lights, glow meshes and particle systems of a lit effect element, relative to its shell (bounded, read-only)."""
    rows = []
    for node, transform, components, path in nodes[:MAX_STRUCTURES]:
        trs = _trs(graph.tree(transform))
        base = {'source_path': path, 'parent': shell_path, 'active': _flag(graph.tree(node)['m_IsActive']), **trs}
        kinds = {c.type.name: c for c in components}
        if 'Light' in kinds:
            light = graph.tree(kinds['Light'])
            rows.append(dict(base, kind='light', light_type=int(light.get('m_Type', 2)), color=_rgba(light.get('m_Color')),
                             range=float(light.get('m_Range', 1.0)), source_intensity=float(light.get('m_Intensity', 1.0))))
        elif 'ParticleSystem' in kinds:
            system = graph.tree(kinds['ParticleSystem'])
            main = system.get('InitialModule', {})
            colors = {}
            renderer = kinds.get('ParticleSystemRenderer')
            if renderer is not None:
                for material in graph.materials(graph.tree(renderer)['m_Materials'])[0][:1]:
                    saved = material.read_typetree().get('m_SavedProperties', {})
                    colors = {k: _rgba(v) for k, v in saved.get('m_Colors', []) if k in ('_Color', '_ColorCore', '_ColorEdge')}
            rows.append(dict(base, kind='particles', lifetime=float(main.get('startLifetime', {}).get('scalar', 1.0)),
                             size=float(main.get('startSize', {}).get('scalar', 1.0)),
                             speed=float(main.get('startSpeed', {}).get('scalar', 0.0)),
                             rate=float(system.get('EmissionModule', {}).get('rateOverTime', {}).get('scalar', 0.0)),
                             max_particles=int(main.get('maxNumParticles', 100)), colors=colors))
        elif 'MeshRenderer' in kinds:
            colors = {}
            for material in graph.materials(graph.tree(kinds['MeshRenderer'])['m_Materials'])[0][:1]:
                saved = material.read_typetree().get('m_SavedProperties', {})
                colors = {k: _rgba(v) for k, v in saved.get('m_Colors', []) if k in ('_Color', '_ColorTop', '_ColorBottom')}
            rows.append(dict(base, kind='glow_sphere', colors=colors))
    return rows


def collect_appearance_mesh_scope(assets, resolved, candidates, skeleton) -> dict:
    """Opt-in graph reader; malformed/unsupported graphs always raise ValueError.
    `candidates` None derives the skinned part meshes from the appearance itself (Largos)."""
    try:
        return _collect(assets, resolved, candidates, skeleton)
    except (KeyError, IndexError, TypeError, AttributeError, OverflowError) as ex:
        raise ValueError('incomplete or malformed appearance mesh-scope metadata') from ex


def _collect(assets, resolved, candidates, skeleton):
    from bridge.content.sr2_asset_extract import decode_mesh
    graph = _Graph(assets)
    appearance_obj = graph.obj(_ptr(resolved['appearance_id']), 'MonoBehaviour', 'SlimeAppearance')
    appearance = graph.tree(appearance_obj)
    applicator_obj = graph.obj(_ptr(resolved['applicator_id']), 'MonoBehaviour', 'SlimeAppearanceApplicator')
    applicator = graph.tree(applicator_obj)
    prefab = graph.obj(_ptr(resolved['prefab_id']), 'GameObject')
    if applicator_obj not in graph.components(prefab) or source_ref(applicator['Appearance']) not in (0, appearance_obj.path_id):
        raise ValueError('resolved appearance/applicator source mismatch')
    visual = graph.obj(applicator['RootAppearanceObject'], 'GameObject')
    visual_transform = graph.transform(visual)
    prefab_transform = graph.transform(prefab)
    if (source_ref(graph.tree(visual_transform)['m_Father']) != prefab_transform.path_id
            or visual_transform.path_id not in [source_ref(p) for p in _bounded(
                graph.tree(prefab_transform)['m_Children'], MAX_NODES, 'prefab children')]):
        raise ValueError('visual Transform does not belong to resolved prefab')
    bone_names, bone_ids = _bones(graph, applicator, skeleton)
    if candidates is None:
        candidates = _derive_candidates(graph, appearance)
    by_mesh = {}
    for candidate in _bounded(candidates, MAX_STRUCTURES, 'mesh candidates'):
        mesh_obj = candidate['mesh_obj']
        pid = mesh_obj.path_id
        if (pid in by_mesh or graph.obj(_ptr(pid), 'Mesh') is not mesh_obj
                or candidate['mesh_name'] != mesh_obj.read().m_Name
                or candidate['mesh'].m_Name != candidate['mesh_name']):
            raise ValueError('ambiguous or inconsistent mesh candidates')
        _name(candidate['role'])
        by_mesh[pid] = candidate
    if candidates is not None:
        # Species whose meshes do not follow the slime<Name> naming (SR2 1.x Hyper): the appearance graph names the parts the
        # name lookup missed. Known meshes keep their looked-up role; an added part never takes a role already used.
        taken = {c['role'] for c in by_mesh.values()}
        for extra in _derive_candidates(graph, appearance):
            if extra['mesh_obj'].path_id in by_mesh:
                continue
            role = extra['role']
            while role in taken:
                role = role + '_extra'
            taken.add(role)
            by_mesh[extra['mesh_obj'].path_id] = dict(extra, role=role)
    parts, inventory, exclusions, structures_inventory, effects = [], [], [], [], []
    used_meshes, used_saos = set(), set()
    entries = []
    for index, structure in enumerate(_bounded(appearance['_structures'], MAX_STRUCTURES, 'appearance structures')):
        element = graph.obj(structure['Element'], 'MonoBehaviour', 'SlimeAppearanceElement')
        prefabs = _bounded(graph.tree(element)['Prefabs'], MAX_LODS, 'element LOD prefabs')
        first = graph.obj(prefabs[0], 'MonoBehaviour', 'SlimeAppearanceObject')
        entries.append((index, structure, element, first))
        # One element can carry several objects of the same (first) LOD: the Bat wings are two prefabs, left and right
        if graph.tree(first).get('LODIndex', 0) == 0:
            for extra in prefabs[1:]:
                other = graph.obj(extra, 'MonoBehaviour', 'SlimeAppearanceObject')
                if graph.tree(other).get('LODIndex') == 0 and other.path_id != first.path_id:
                    entries.append((index, structure, element, other))
    for index, structure, element, sao in entries:
        if sao.path_id in used_saos:
            raise ValueError('duplicate LOD0 appearance object')
        used_saos.add(sao.path_id)
        go = graph.obj(graph.tree(sao)['m_GameObject'], 'GameObject')
        if sao not in graph.components(go):
            raise ValueError('appearance object not owned by declared GameObject')
        supports_faces = _flag(structure['SupportsFaces'])
        override = _bounded(structure['ElementMaterials'], MAX_LODS, 'material LOD overrides')[0]
        material_refs = override['Materials'] if _flag(override['OverrideDefaults']) else structure['DefaultMaterials']
        if not material_refs and not any(c.type.name in ('SkinnedMeshRenderer', 'MeshRenderer') for c in graph.components(go)):
            # an effect element (the Phosphor shine rotator ...): nothing to draw, so nothing to port
            exclusions.append({'source_structure_index': index, 'source_element_id': element.path_id, 'source_part_id': sao.path_id,
                               'status': 'excluded', 'reason': 'effect-element-without-materials'})
            structures_inventory.append({'source_structure_index': index, 'source_element_id': element.path_id,
                                         'source_part_id': sao.path_id, 'renderer_ids': [], 'status': 'excluded', 'ignored_fx_children': []})
            continue
        graph.materials(material_refs)
        nodes = list(graph.subtree(go))
        if any(c.type.name == 'Light' for n in nodes for c in n[2]):
            # a lit effect element (the SR2 1.x Hyper slime's energy aura): the root mesh (the aura shell) is exported like any
            # rigid attachment; the lights, glow spheres and particle systems below it are described for the destination to
            # rebuild with its own effects (the source shaders do not exist there)
            root_kinds = {c.type.name for c in nodes[0][2]}
            described = _effect_rows(graph, nodes[1:], nodes[0][3])
            effects.extend(dict(e, source_structure_index=index) for e in described)
            if not {'MeshRenderer', 'MeshFilter'} <= root_kinds:
                exclusions.append({'source_structure_index': index, 'source_element_id': element.path_id, 'source_part_id': sao.path_id,
                                   'status': 'excluded', 'reason': 'lit-effect-element-without-shell'})
                structures_inventory.append({'source_structure_index': index, 'source_element_id': element.path_id,
                                             'source_part_id': sao.path_id, 'renderer_ids': [], 'status': 'excluded', 'ignored_fx_children': []})
                continue
            nodes = nodes[:1]
        root_types = [c.type.name for c in nodes[0][2]]
        particle_only = 'ParticleSystemRenderer' in root_types
        fx_children = []
        if not particle_only and len(nodes) != 1:
            # a mesh part may carry particle-effect children (e.g. the Angler lure's daze charge): the effects are not ported
            # (same scope as a particle-only part), but nothing that draws a mesh may hide in the subtree
            extra = nodes[1:]
            if any(c.type.name in ('SkinnedMeshRenderer', 'MeshRenderer', 'MeshFilter') for n in extra for c in n[2]):
                raise ValueError('nested mesh appearance parts unsupported (structure %d, %d nodes: %s)' % (index, len(nodes), ', '.join(n[3] for n in nodes[:6])))
            fx_children = [n[3] for n in extra]
            nodes = nodes[:1]
        rows = []
        for node, transform, components, path in nodes:
            renderers = [c for c in components if c.type.name in ('SkinnedMeshRenderer', 'MeshRenderer', 'ParticleSystemRenderer')]
            filters = [c for c in components if c.type.name == 'MeshFilter']
            systems = [c for c in components if c.type.name == 'ParticleSystem']
            if len(renderers) != 1:
                raise ValueError('no unique supported appearance renderer')
            renderer = renderers[0]
            rt = graph.tree(renderer)
            row = {'source_structure_index': index, 'source_element_id': element.path_id,
                   'source_part_id': sao.path_id, 'source_game_object_id': node.path_id,
                   'source_renderer_id': renderer.path_id, 'source_renderer_type': renderer.type.name,
                   'source_path': path, 'game_object_active': _flag(graph.tree(node)['m_IsActive']),
                   'renderer_enabled': _flag(rt['m_Enabled']),
                   'source_transform_id': transform.path_id,
                   'source_transform_parent_id': source_ref(graph.tree(transform)['m_Father']),
                   'local_transform': _trs(graph.tree(transform)),
                   'source_components': [{'path_id': c.path_id, 'type': c.type.name,
                       **({'class_name': graph.class_name(c), 'ported': False} if c.type.name == 'MonoBehaviour' else {})}
                       for c in components]}
            refs = material_refs if node is go else rt['m_Materials']
            if particle_only:
                if renderer.type.name != 'ParticleSystemRenderer' or len(systems) != 1 or filters or supports_faces:
                    raise ValueError('unsupported mixed or malformed particle subtree')
                if type(rt['m_RenderMode']) is not int or rt['m_RenderMode'] != 0:
                    raise ValueError('only billboard particle inventory is supported')
                # Inactive billboard slot: metadata, NOT a traversed mesh PPtr.
                inert = rt['m_Mesh']
                if (not isinstance(inert, dict) or any(type(inert.get(k)) is not int or inert[k] < 0
                                                     for k in ('m_FileID', 'm_PathID'))):
                    raise ValueError('particle inert mesh reference malformed')
                materials, provenance = graph.materials(refs)
                row.update(status='excluded', reason='source-particle-system-not-ported',
                           source_particle_system_id=systems[0].path_id,
                           source_particle_mesh_ref=dict(inert), source_particle_render_mode=0,
                           source_material_ids=[m.path_id for m in materials], source_material_provenance=provenance)
                if node is go:
                    try:
                        row['appearance_binding'] = _binding(graph, sao, transform, bone_names, bone_ids)
                    except ValueError:
                        pass            # a particle trail that follows no bone (the Shadow slime's trail): an effect that is not ported anyway
                exclusions.append(dict(row))
            else:
                if systems or renderer.type.name == 'ParticleSystemRenderer':
                    raise ValueError('unsupported mixed appearance renderer')
                kind = 'skinned' if renderer.type.name == 'SkinnedMeshRenderer' else 'static'
                mesh_pointer = rt['m_Mesh'] if kind == 'skinned' else (graph.tree(filters[0])['m_Mesh'] if len(filters) == 1 else None)
                if (sr2_appearance.ALLOW_EXTERNAL_REFS and mesh_pointer is not None and not supports_faces and
                        (not source_ref(mesh_pointer) or source_ref(mesh_pointer) not in graph.objects)):
                    # a renderer whose mesh is filled in at runtime or lives outside the loaded files (the Rad aura/core shells): effect geometry, not ported
                    exclusions.append(dict(row, status='excluded', reason='renderer-without-mesh'))
                    inventory.append(dict(row, status='excluded', reason='renderer-without-mesh'))
                    rows.append(renderer.path_id)
                    continue
                if kind == 'skinned':
                    if filters:
                        raise ValueError('skinned appearance has unexpected MeshFilter')
                    mesh_obj = graph.obj(rt['m_Mesh'], 'Mesh')
                    if mesh_obj.path_id not in by_mesh:
                        raise ValueError('skinned mesh is not an existing candidate')
                    part = dict(by_mesh[mesh_obj.path_id])
                    if supports_faces != (part['role'] == 'face' or bool(part.get('embeds_face'))):
                        raise ValueError('appearance SupportsFaces does not match candidate role')
                    binding = _binding(graph, sao, transform, bone_names, bone_ids, graph.tree(mesh_obj), skeleton)
                    embedded_face = None
                    if supports_faces and part.get('embeds_face'):
                        # Saber: the body mesh carries its own eyes/mouth passes. They become a face part of their own on the same
                        # geometry, so everything downstream (face expressions, native body paint) sees an ordinary body + face.
                        all_refs, face_binding = _face_passes(graph, appearance, applicator, structure, refs)
                        passes = face_binding['source_face_materials'][len(refs):]          # the eyes/mouth passes; the body pass stays on the body
                        embedded_face = (all_refs[len(refs):], dict(face_binding, source_face_materials=passes, embedded_face=True))
                    elif supports_faces:
                        refs, face_binding = _face_passes(graph, appearance, applicator, structure, refs)
                        binding.update(face_binding)
                else:
                    embedded_face = None
                    if len(filters) != 1 or supports_faces:
                        raise ValueError('no unique static MeshFilter or unsupported static face')
                    mesh_obj = graph.obj(graph.tree(filters[0])['m_Mesh'], 'Mesh')
                    mt = graph.tree(mesh_obj)
                    if mt['m_BindPose'] or mt['m_BoneNameHashes']:
                        raise ValueError('static appearance mesh declares unexpected skin')
                    mesh = mesh_obj.read()
                    part = {'mesh_obj': mesh_obj, 'mesh': mesh, 'mesh_name': mesh.m_Name, 'role': 'attachment'}
                    binding = _binding(graph, sao, transform, bone_names, bone_ids)
                    row['source_mesh_filter_id'] = filters[0].path_id
                if mesh_obj.path_id in used_meshes:
                    raise ValueError('duplicate declared appearance mesh')
                used_meshes.add(mesh_obj.path_id)
                materials, provenance = graph.materials(refs)
                expected_vertices, expected_indices = _mesh_layout(graph.tree(mesh_obj))
                geometry = decode_mesh(part['mesh'], mesh_obj)
                indices = _geometry(geometry, kind, binding)
                if geometry['vertex_count'] != expected_vertices or indices != expected_indices:
                    raise ValueError('decoded geometry differs from declared source layout')
                if len(materials) < len(geometry['triangles']):
                    raise ValueError('effective materials do not cover mesh submeshes')
                row.update(status='included', renderer_kind=kind, source_mesh_id=mesh_obj.path_id,
                           source_material_ids=[m.path_id for m in materials], source_material_provenance=provenance,
                           vertex_count=geometry['vertex_count'], index_count=indices, appearance_binding=binding)
                part.update(row, geometry=geometry,
                            source_material_slots=[(m.read().m_Name, m.read()) for m in materials])
                parts.append(part)
                if embedded_face is not None:
                    face_refs, face_binding = embedded_face
                    face_materials, face_provenance = graph.materials(face_refs)
                    face_row = dict(row, source_material_ids=[m.path_id for m in face_materials], source_material_provenance=face_provenance,
                                    appearance_binding=dict(binding, **face_binding))
                    face_part = {'mesh_obj': mesh_obj, 'mesh': part['mesh'], 'mesh_name': part['mesh_name'], 'role': 'face'}
                    face_part.update(face_row, geometry=geometry,
                                     source_material_slots=[(m.read().m_Name, m.read()) for m in face_materials])
                    parts.append(face_part)
            inventory.append(row)
            rows.append(renderer.path_id)
        structures_inventory.append({'source_structure_index': index, 'source_element_id': element.path_id,
                                     'source_part_id': sao.path_id, 'renderer_ids': rows,
                                     'status': 'excluded' if particle_only else 'included', 'ignored_fx_children': fx_children})
    if not parts:
        raise ValueError('appearance mesh scope contains no exportable meshes')
    return {'extraction_scope': SCOPE, 'coverage': 'partial', 'source_particles_ported': False,
            'source_native_behaviour_ported': False, 'source_animation_ported': False,
            'source_shader_parity_verified': False, 'source_visual_parity_verified': False,
            'source_file': 'resources.assets', 'source_appearance_id': appearance_obj.path_id,
            'source_prefab_id': prefab.path_id, 'source_applicator_id': applicator_obj.path_id,
            'visual_transform': _trs(graph.tree(visual_transform)),
            'parts': parts, 'inventory': inventory, 'exclusions': exclusions, 'structures': structures_inventory,
            'effects': effects}
