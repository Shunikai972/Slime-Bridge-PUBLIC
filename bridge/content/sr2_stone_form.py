"""Opt-in, read-only extraction of the SR2 slime stone-form statue (schema ``bridge-stone-form`` v1).

Resolves the petrified-statue prefab either from a species appearance object
(``_stoneFormAppearance`` -> ``StoneFormAppearance`` -> ``StoneFormPrefab``) or from
explicit report path IDs (stone-form ScriptableObject or ``RingtailStatue`` component),
then walks the statue subtree through the LODGroup, decodes the three dedicated static
LOD meshes and the shared stone material subset with its albedo/normal textures.

Boundaries and guarantees
- Read-only source inspection. This module never launches the game, never writes into the
  game or the canonical asset cache, and is NOT wired into ``extract_species`` or any
  existing extractor: it is a standalone reader with its own versioned schema.
- Its only optional output is :func:`export_stone_form`, which writes PNG textures plus
  ``stone-form.json`` and a minimal receipt into a NEW caller-provided folder and refuses
  to touch an existing destination.
- Fail-closed: malformed, external, ambiguous, duplicated, budget-breaking, compressed,
  skinned or incompletely textured source data always raises ``ValueError``; nothing is
  guessed from names, and no shader is adapted or simulated.
- Prerequisite: generated local MonoBehaviour schemas must be loaded on ``assets.env``
  (``env.typetree_generator`` built from the game's Cpp2IL ``cpp2il_out`` folder, the
  layout proven by the repository's SR2 probes).
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path

from bridge.content.sr2_appearance import source_object_index, source_ref

SCHEMA = 'bridge-stone-form'
SCHEMA_VERSION = 1

# Declared hard budgets (v1 contract). Tests may lower them; source data never exceeds them.
MAX_OBJECTS = 100_000          # whole-snapshot object count
MAX_COMPONENTS = 64            # components on any statue GameObject
MAX_CHILDREN = 16              # children on any statue Transform
MAX_NODES = 32                 # nodes in the statue subtree
MAX_DEPTH = 4                  # statue subtree depth
MAX_LODS = 3                   # exactly three dedicated statue LODs
MAX_VERTICES = 2048            # vertices per LOD mesh
MAX_TRIANGLES = 4096           # triangles per LOD mesh
MAX_CHANNELS = 16              # vertex channels per mesh
MAX_VERTEX_BYTES = 1 << 17     # 2048 vertices * 64 bytes of vertex attributes
MAX_SUBMESHES = 4              # submeshes per mesh
MAX_PROPERTY_ENTRIES = 512     # entries in one material property list
MAX_TEXTURE_SIZE = 8192        # texture width/height bound
MAX_STREAM_PATH = 128          # streamed sidecar path length bound

# Documented material subset carried by bridge-stone-form v1. The statue material is
# converted by a later, separate stage; these are the source values that stage consumes.
MATERIAL_FLOATS = ('_Gloss', '_GlossPower', '_Outline', '_RimPower', '_RimStrength', '_Shininess')
MATERIAL_COLORS = ('_TopColor', '_MiddleColor', '_BottomColor',
                   '_RedTopColor', '_RedMiddleColor', '_RedBottomColor',
                   '_GreenTopColor', '_GreenMiddleColor', '_GreenBottomColor',
                   '_BlueTopColor', '_BlueMiddleColor', '_BlueBottomColor')

ROOT_COMPONENT_TYPES = frozenset({'Transform', 'LODGroup', 'SphereCollider', 'BoxCollider',
                                  'CapsuleCollider', 'MeshCollider', 'Rigidbody', 'MonoBehaviour'})
ATTACHMENT_COMPONENT_TYPES = frozenset({'Transform', 'SphereCollider', 'BoxCollider',
                                        'CapsuleCollider', 'MeshCollider', 'MonoBehaviour'})
LOD_COMPONENT_TYPES = frozenset({'Transform', 'MeshFilter', 'MeshRenderer'})
MESH_COMPONENT_TYPES = frozenset({'MeshFilter', 'MeshRenderer'})


@dataclass(frozen=True)
class StoneForm:
    """Typed, JSON-clean stone-form result independent of the canonical extractor."""

    schema: str
    version: int
    entry: str
    provenance: dict
    root: dict
    meshes: tuple
    material: dict

    def to_dict(self) -> dict:
        return {
            'schema': self.schema,
            'version': self.version,
            'entry': self.entry,
            'provenance': copy.deepcopy(self.provenance),
            'root': copy.deepcopy(self.root),
            'meshes': [copy.deepcopy(mesh) for mesh in self.meshes],
            'material': copy.deepcopy(self.material),
        }


def _bounded(value, maximum, label, minimum=1):
    if type(value) is not int or not (minimum <= value <= maximum):
        raise ValueError(f'{label} is outside the declared budget [{minimum}, {maximum}]: {value!r}')
    return value


def _flag(value, label):
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError(f'{label} must be a boolean flag')
    return bool(value)


def _name(value, label):
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError(f'{label} must be a non-empty bounded name')
    return value


def _finite(value, label):
    if type(value) not in (int, float) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def _vector(value, size, label, axes='xyzw'):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a vector')
    out = []
    for axis in axes[:size]:
        if axis not in value:
            raise ValueError(f'{label} is missing axis {axis}')
        out.append(_finite(value[axis], f'{label}.{axis}'))
    return out


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not (minimum <= value <= maximum):
        raise ValueError(f'{label} must be an integer in [{minimum}, {maximum}]')
    return value


def _payload_length(value, label):
    """Length of a raw byte payload as read by UnityPy (bytes or a bounded uint8 list)."""
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            if type(item) is not int or not (0 <= item <= 255):
                raise ValueError(f'{label} byte payload entry is malformed')
        return len(value)
    raise ValueError(f'{label} byte payload is unreadable')


def _trs(tree, label):
    position = _vector(tree.get('m_LocalPosition'), 3, f'{label} local position')
    rotation = _vector(tree.get('m_LocalRotation'), 4, f'{label} local rotation')
    scale = _vector(tree.get('m_LocalScale'), 3, f'{label} local scale')
    norm = math.sqrt(sum(value * value for value in rotation))
    if abs(norm - 1.0) > 1e-4:
        raise ValueError(f'{label} local rotation is not a unit quaternion')
    if any(value <= 0.0 for value in scale):
        raise ValueError(f'{label} local scale is degenerate')
    return {'local_position': position, 'local_rotation': rotation, 'local_scale': scale}


class _Graph:
    """Bounded, read-only view over one source snapshot (duplicates refused)."""

    def __init__(self, assets):
        objects = getattr(assets, '_objects', None)
        if not isinstance(objects, list):
            raise ValueError('stone form source has no object snapshot to read')
        _bounded(len(objects), MAX_OBJECTS, 'stone form source object count', minimum=1)
        self.objects = source_object_index(assets)
        self._trees = {}
        self._classes = {}

    def ref(self, pointer, label, minimum=1):
        if not isinstance(pointer, dict):
            raise ValueError(f'{label} must be a source reference')
        pid = source_ref(pointer)
        if pid < minimum:
            raise ValueError(f'{label} is unresolved or malformed')
        return pid

    def obj_by_id(self, pid, kind=None, class_name=None, label='stone form reference'):
        try:
            obj = self.objects[pid]
        except KeyError as ex:
            raise ValueError(f'{label} {pid} is missing from the source snapshot') from ex
        if kind and obj.type.name != kind:
            raise ValueError(f'{label} {pid} is not a {kind}')
        if class_name and self.class_name(obj) != class_name:
            raise ValueError(f'{label} {pid} is not a {class_name}')
        return obj

    def obj(self, pointer, kind=None, class_name=None, label='stone form reference'):
        return self.obj_by_id(self.ref(pointer, label), kind=kind, class_name=class_name, label=label)

    def class_name(self, obj):
        name = self._classes.get(obj.path_id)
        if name is None:
            if obj.type.name != 'MonoBehaviour':
                raise ValueError(f'stone form class check requires a MonoBehaviour, got {obj.type.name}')
            try:
                script = obj.parse_monobehaviour_head().m_Script.deref().read()
                name = script.m_ClassName
            except Exception as ex:
                raise ValueError(f'stone form MonoBehaviour {obj.path_id} class could not be read') from ex
            if not isinstance(name, str) or not name:
                raise ValueError(f'stone form MonoBehaviour {obj.path_id} class name is missing')
            self._classes[obj.path_id] = name
        return name

    def tree(self, obj):
        pid = obj.path_id
        if pid not in self._trees:
            try:
                tree = obj.read_typetree()
            except Exception as ex:
                raise ValueError(f'stone form object {pid} could not be read: {type(ex).__name__}') from ex
            if not isinstance(tree, dict):
                raise ValueError(f'stone form object {pid} did not produce a typetree')
            self._trees[pid] = tree
        return self._trees[pid]

    def components(self, go):
        tree = self.tree(go)
        entries = tree.get('m_Component')
        if not isinstance(entries, list):
            raise ValueError('stone form GameObject components are unreadable')
        _bounded(len(entries), MAX_COMPONENTS, 'stone form GameObject component count', minimum=1)
        components = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict) or 'component' not in entry:
                raise ValueError('stone form GameObject has a malformed component entry')
            pid = self.ref(entry['component'], 'stone form GameObject component')
            if pid in seen:
                raise ValueError(f'stone form GameObject declares component {pid} twice')
            seen.add(pid)
            component = self.obj_by_id(pid, label='stone form GameObject component')
            owner = self.tree(component).get('m_GameObject')
            if self.ref(owner, 'stone form component owner', minimum=0) != go.path_id:
                raise ValueError(f'stone form component {pid} is not owned by its GameObject')
            components.append(component)
        return components

    def transform(self, go):
        candidates = [c for c in self.components(go) if c.type.name == 'Transform']
        if len(candidates) != 1:
            raise ValueError('stone form GameObject has no unique Transform')
        return candidates[0]

    def subtree(self, root_go):
        """Walk the statue subtree: reciprocal transforms, bounded depth/nodes, no cycles."""
        nodes = []
        seen_go = set()
        seen_transforms = set()

        def visit(go, depth, parent):
            if depth > MAX_DEPTH:
                raise ValueError('stone form subtree exceeds the declared depth budget')
            if len(nodes) >= MAX_NODES:
                raise ValueError('stone form subtree exceeds the declared node budget')
            if go.path_id in seen_go:
                raise ValueError('stone form subtree repeats a GameObject')
            seen_go.add(go.path_id)
            transform = self.transform(go)
            if transform.path_id in seen_transforms:
                raise ValueError('stone form subtree repeats a Transform')
            seen_transforms.add(transform.path_id)
            tree = self.tree(transform)
            children = tree.get('m_Children')
            if not isinstance(children, list):
                raise ValueError('stone form Transform children are unreadable')
            _bounded(len(children), MAX_CHILDREN, 'stone form Transform child count', minimum=0)
            if parent is not None:
                if self.ref(tree.get('m_Father'), 'stone form Transform parent') != parent.path_id:
                    raise ValueError('stone form Transform hierarchy is not reciprocal')
            child_entries = []
            for entry in children:
                child_transform = self.obj_by_id(self.ref(entry, 'stone form child Transform'),
                                                 label='stone form child Transform')
                if child_transform.type.name != 'Transform':
                    raise ValueError('stone form child is not a Transform')
                child_go = self.obj(self.tree(child_transform).get('m_GameObject'), 'GameObject',
                                    label='stone form child GameObject')
                child_entries.append((child_go, child_transform))
            nodes.append({'go': go, 'transform': transform, 'depth': depth})
            for child_go, _child_transform in child_entries:
                visit(child_go, depth + 1, transform)

        visit(root_go, 0, None)
        return nodes

    def lod_renderers(self, lod_group):
        tree = self.tree(lod_group)
        lods = tree.get('m_LODs')
        if not isinstance(lods, list):
            raise ValueError('stone form LODGroup has no readable LOD list')
        if len(lods) != MAX_LODS:
            raise ValueError(f'stone form LOD set does not contain exactly {MAX_LODS} LODs '
                             'within the declared budget')
        renderers = []
        for entry in lods:
            if not isinstance(entry, dict):
                raise ValueError('stone form LOD entry is malformed')
            registered = entry.get('renderers')
            if not isinstance(registered, list) or len(registered) != 1:
                raise ValueError('stone form LOD must register exactly one renderer')
            item = registered[0]
            if not isinstance(item, dict) or 'renderer' not in item:
                raise ValueError('stone form LOD renderer entry is malformed')
            renderers.append(self.obj(item['renderer'], 'MeshRenderer',
                                      label='stone form LOD renderer'))
        if len({renderer.path_id for renderer in renderers}) != len(renderers):
            raise ValueError('stone form LOD set repeats a renderer')
        return renderers

    def renderer_details(self, renderer, root_transform):
        tree = self.tree(renderer)
        go = self.obj(tree.get('m_GameObject'), 'GameObject', label='stone form LOD GameObject')
        components = self.components(go)
        transform = self.transform(go)
        ttree = self.tree(transform)
        if self.ref(ttree.get('m_Father'), 'stone form LOD parent') != root_transform.path_id:
            raise ValueError('stone form LOD is not a direct child of the statue root')
        if ttree.get('m_Children') != []:
            raise ValueError('stone form LOD object has child transforms')
        materials = tree.get('m_Materials')
        if not isinstance(materials, list) or len(materials) != 1:
            raise ValueError('stone form LOD renderer must declare exactly one material')
        material = self.obj(materials[0], 'Material', label='stone form LOD material')
        filters = [c for c in components if c.type.name == 'MeshFilter']
        if len(filters) != 1:
            raise ValueError('stone form LOD object has no unique MeshFilter')
        mesh = self.obj(self.tree(filters[0]).get('m_Mesh'), 'Mesh', label='stone form LOD mesh')
        return {'renderer': renderer, 'go': go, 'transform': transform, 'material': material,
                'mesh_filter': filters[0], 'mesh': mesh,
                'enabled': _flag(tree.get('m_Enabled'), 'stone form LOD renderer enabled flag'),
                'components': components}


def _resolve(graph, appearance_id, stone_form_id, statue_prefab_id):
    supplied = {name: value for name, value in (('appearance', appearance_id),
                                                ('stone_form', stone_form_id),
                                                ('statue_prefab', statue_prefab_id))
                if value is not None}
    if len(supplied) != 1:
        raise ValueError('exactly one stone form entry point must be provided')
    entry, value = next(iter(supplied.items()))
    if type(value) is not int or value <= 0:
        raise ValueError('stone form entry point ids must be positive integers')
    appearance = None
    stone_form = None
    statue = None
    if entry == 'appearance':
        appearance = graph.obj_by_id(value, 'MonoBehaviour', class_name='SlimeAppearance',
                                     label='stone form appearance')
        linked = graph.ref(graph.tree(appearance).get('_stoneFormAppearance'),
                           'stone form appearance link')
        stone_form = graph.obj_by_id(linked, 'MonoBehaviour', class_name='StoneFormAppearance',
                                     label='stone form appearance link')
    elif entry == 'stone_form':
        stone_form = graph.obj_by_id(value, 'MonoBehaviour', class_name='StoneFormAppearance',
                                     label='stone form asset')
    else:
        statue = graph.obj_by_id(value, 'MonoBehaviour', class_name='RingtailStatue',
                                 label='stone form statue prefab')
    stone_form_name = None
    if stone_form is not None:
        tree = graph.tree(stone_form)
        stone_form_name = _name(tree.get('m_Name'), 'stone form asset name')
        if statue is None:
            prefab = graph.ref(tree.get('StoneFormPrefab'), 'stone form prefab link')
            statue = graph.obj_by_id(prefab, 'MonoBehaviour', class_name='RingtailStatue',
                                     label='stone form prefab link')
    return {'entry': entry, 'appearance': appearance, 'stone_form': stone_form,
            'stone_form_name': stone_form_name, 'statue': statue}


def _mesh_layout(tree, label):
    """Validate source mesh metadata before the geometry decoder; streams must be canonical."""
    name = _name(tree.get('m_Name'), f'{label} mesh name')
    compression = tree.get('m_MeshCompression')
    if type(compression) is not int or compression != 0:
        raise ValueError(f'{label} mesh is compressed or declares malformed compression')
    index_format = tree.get('m_IndexFormat')
    if type(index_format) is not int or index_format != 0:
        raise ValueError(f'{label} mesh index format is unsupported')
    vertex_data = tree.get('m_VertexData')
    if not isinstance(vertex_data, dict):
        raise ValueError(f'{label} mesh has no readable vertex data')
    vertex_count = _bounded(vertex_data.get('m_VertexCount'), MAX_VERTICES,
                            f'{label} vertex count', minimum=3)
    channels = vertex_data.get('m_Channels')
    if not isinstance(channels, list):
        raise ValueError(f'{label} mesh channel layout is unreadable')
    _bounded(len(channels), MAX_CHANNELS, f'{label} mesh channel count', minimum=1)
    data_length = _payload_length(vertex_data.get('m_DataSize'), f'{label} mesh vertex')
    stream = tree.get('m_StreamData')
    if not isinstance(stream, dict):
        raise ValueError(f'{label} mesh stream metadata is unreadable')
    path, offset, size = stream.get('path'), stream.get('offset'), stream.get('size')
    if not isinstance(path, str) or type(offset) is not int or type(size) is not int:
        raise ValueError(f'{label} mesh stream metadata is malformed')
    if offset < 0 or size < 0:
        raise ValueError(f'{label} mesh stream metadata is negative')
    if path:
        # Only the canonical Unity sidecar layout of a SerializedFile is supported.
        if ('/' in path or '\\' in path or ':' in path or len(path) > MAX_STREAM_PATH
                or not path.endswith('.assets.resS')):
            raise ValueError(f'{label} mesh stream path is not a canonical sidecar')
        if data_length:
            raise ValueError(f'{label} mesh declares both inline and streamed vertex data')
        payload = _bounded(size, MAX_VERTEX_BYTES, f'{label} mesh stream byte size',
                           minimum=vertex_count)
        _bounded(offset, 1 << 31, f'{label} mesh stream offset', minimum=0)
    else:
        if offset != 0 or size != 0:
            raise ValueError(f'{label} inline mesh declares stream offsets')
        payload = _bounded(data_length, MAX_VERTEX_BYTES, f'{label} mesh vertex byte size',
                           minimum=vertex_count)
    if payload % vertex_count:
        raise ValueError(f'{label} mesh vertex stride is malformed')
    stride = payload // vertex_count
    if not 4 <= stride <= 64:
        raise ValueError(f'{label} mesh vertex stride is outside the supported range')
    submeshes = tree.get('m_SubMeshes')
    if not isinstance(submeshes, list):
        raise ValueError(f'{label} mesh submesh layout is unreadable')
    _bounded(len(submeshes), MAX_SUBMESHES, f'{label} mesh submesh count', minimum=1)
    buffer_length = _payload_length(tree.get('m_IndexBuffer'), f'{label} mesh index')
    if buffer_length % 2:
        raise ValueError(f'{label} mesh index payload is malformed')
    total = buffer_length // 2
    _bounded(total, MAX_TRIANGLES * 3, f'{label} mesh index count', minimum=3)
    expected = 0
    for entry in submeshes:
        if not isinstance(entry, dict):
            raise ValueError(f'{label} mesh submesh entry is malformed')
        first_byte, count = entry.get('firstByte'), entry.get('indexCount')
        if type(first_byte) is not int or type(count) is not int:
            raise ValueError(f'{label} mesh submesh slices are unreadable')
        if first_byte != expected:
            raise ValueError(f'{label} mesh submesh slices are not contiguous')
        if count < 3 or count % 3:
            raise ValueError(f'{label} mesh submesh is not a triangle list')
        expected += count
        if type(entry.get('topology')) is not int or entry.get('topology') != 0:
            raise ValueError(f'{label} mesh submesh topology is unsupported')
        if type(entry.get('baseVertex')) is not int or entry.get('baseVertex') != 0:
            raise ValueError(f'{label} mesh submesh base vertex is unsupported')
        if entry.get('firstVertex') != 0 or entry.get('vertexCount') != vertex_count:
            raise ValueError(f'{label} mesh submesh does not span the vertex buffer')
    if expected != total:
        raise ValueError(f'{label} mesh submesh slices do not cover the index buffer')
    if tree.get('m_BindPose') or tree.get('m_BoneNameHashes'):
        raise ValueError(f'{label} mesh declares skin or bone data')
    return {'name': name, 'vertex_count': vertex_count, 'index_count': total}


def _decode_mesh(mesh, label):
    from bridge.content.sr2_asset_extract import decode_mesh

    try:
        return decode_mesh(mesh.read(), mesh)
    except ValueError as ex:
        raise ValueError(f'{label} geometry refused by the decoder: {ex}') from ex
    except Exception as ex:
        raise ValueError(f'{label} geometry could not be decoded: {type(ex).__name__}') from ex


def _channel(entries, count, size, label, allow_empty=True):
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise ValueError(f'{label} must be a list')
    if not entries:
        if allow_empty:
            return []
        raise ValueError(f'{label} is empty')
    if len(entries) != count:
        raise ValueError(f'{label} length does not match the vertex count')
    out = []
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != size:
            raise ValueError(f'{label} entry is malformed')
        out.append([_finite(value, f'{label} component') for value in entry])
    return out


def _geometry(decoded, vertex_count, index_count, label):
    if not isinstance(decoded, dict):
        raise ValueError(f'{label} geometry decoder returned no mapping')
    if decoded.get('vertex_count') != vertex_count:
        raise ValueError(f'{label} decoded vertex count disagrees with the source header')
    if decoded.get('bind_poses') or decoded.get('bone_weights'):
        raise ValueError(f'{label} decoded with unexpected skin data')
    vertices = _channel(decoded.get('vertices'), vertex_count, 3, f'{label} vertices',
                        allow_empty=False)
    normals = _channel(decoded.get('normals'), vertex_count, 3, f'{label} normals')
    tangents = _channel(decoded.get('tangents'), vertex_count, 4, f'{label} tangents')
    uv = _channel(decoded.get('uv'), vertex_count, 2, f'{label} uv')
    raw = decoded.get('triangles')
    if not isinstance(raw, list) or not raw:
        raise ValueError(f'{label} decoded with no triangle indices')
    _bounded(len(raw), MAX_SUBMESHES, f'{label} decoded submesh count', minimum=1)
    triangles = []
    total = 0
    for submesh in raw:
        if not isinstance(submesh, (list, tuple)) or len(submesh) < 3 or len(submesh) % 3:
            raise ValueError(f'{label} decoded triangle list is malformed')
        row = []
        for index in submesh:
            if type(index) is not int or not (0 <= index < vertex_count):
                raise ValueError(f'{label} decoded triangle index is out of range')
            row.append(index)
        triangles.append(row)
        total += len(row)
    if total != index_count:
        raise ValueError(f'{label} decoded index count disagrees with the source header')
    if total // 3 > MAX_TRIANGLES:
        raise ValueError(f'{label} decoded triangle count exceeds the declared budget')
    return {'vertex_count': vertex_count, 'index_count': total, 'triangle_count': total // 3,
            'vertices': vertices, 'normals': normals, 'tangents': tangents, 'uv': uv,
            'triangles': triangles}


def _properties(entries, kind, label):
    if not isinstance(entries, list):
        raise ValueError(f'{label} {kind} properties are unreadable')
    _bounded(len(entries), MAX_PROPERTY_ENTRIES, f'{label} {kind} property count', minimum=0)
    out = {}
    for entry in entries:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f'{label} {kind} property entry is malformed')
        prop = _name(entry[0], f'{label} {kind} property name')
        if prop in out:
            raise ValueError(f'{label} {kind} property {prop} is declared twice')
        out[prop] = entry[1]
    return out


def _material(graph, material, label):
    tree = graph.tree(material)
    name = _name(tree.get('m_Name'), f'{label} name')
    shader = graph.obj(tree.get('m_Shader'), 'Shader', label=f'{label} shader')
    parsed = graph.tree(shader).get('m_ParsedForm')
    if not isinstance(parsed, dict):
        raise ValueError(f'{label} shader form is unreadable')
    shader_name = _name(parsed.get('m_Name'), f'{label} shader name')
    queue = tree.get('m_CustomRenderQueue')
    if type(queue) is not int or not (-1 <= queue <= 65535):
        raise ValueError(f'{label} render queue is malformed')
    saved = tree.get('m_SavedProperties')
    if not isinstance(saved, dict):
        raise ValueError(f'{label} saved properties are unreadable')
    floats = _properties(saved.get('m_Floats'), 'float', label)
    colors = _properties(saved.get('m_Colors'), 'color', label)
    texenvs = _properties(saved.get('m_TexEnvs'), 'texture', label)
    carried_floats = {}
    for prop in MATERIAL_FLOATS:
        if prop not in floats:
            raise ValueError(f'{label} is missing required float {prop}')
        carried_floats[prop] = _finite(floats[prop], f'{label} float {prop}')
    carried_colors = {}
    for prop in MATERIAL_COLORS:
        if prop not in colors:
            raise ValueError(f'{label} is missing required color {prop}')
        carried_colors[prop] = _vector(colors[prop], 4, f'{label} color {prop}', axes='rgba')
    textures = {}
    for prop in ('_MainTex', '_Normal'):
        if prop not in texenvs:
            raise ValueError(f'{label} is missing required texture {prop}')
        slot = texenvs[prop]
        if not isinstance(slot, dict):
            raise ValueError(f'{label} texture slot {prop} is malformed')
        pid = graph.ref(slot.get('m_Texture'), f'{label} texture {prop}')
        texture = graph.obj_by_id(pid, 'Texture2D', label=f'{label} texture {prop}')
        ttree = graph.tree(texture)
        tname = _name(ttree.get('m_Name'), f'{label} texture {prop} name')
        width = _integer(ttree.get('m_Width'), f'{label} texture {prop} width', 1, MAX_TEXTURE_SIZE)
        height = _integer(ttree.get('m_Height'), f'{label} texture {prop} height', 1, MAX_TEXTURE_SIZE)
        scale = _vector(slot.get('m_Scale'), 2, f'{label} texture {prop} scale')
        offset = _vector(slot.get('m_Offset'), 2, f'{label} texture {prop} offset')
        if scale != [1.0, 1.0] or offset != [0.0, 0.0]:
            raise ValueError(f'{label} texture {prop} uses an unsupported tiling or offset')
        textures[prop] = {'path_id': pid, 'name': tname, 'width': width, 'height': height,
                          'file': None}
    for prop, slot in texenvs.items():
        if prop in textures:
            continue
        if not isinstance(slot, dict):
            raise ValueError(f'{label} texture slot {prop} is malformed')
        if graph.ref(slot.get('m_Texture'), f'{label} texture slot {prop}', minimum=0):
            raise ValueError(f'{label} declares an unsupported extra texture {prop}')
    return {'path_id': material.path_id, 'name': name,
            'shader_path_id': shader.path_id, 'shader_name': shader_name,
            'render_queue': queue, 'floats': carried_floats, 'colors': carried_colors,
            'textures': textures}


def collect_stone_form(assets, *, appearance_id=None, stone_form_id=None, statue_prefab_id=None):
    """Read the statue stone form from one source snapshot.

    Exactly one entry point must be given: a ``SlimeAppearance`` object id, a
    ``StoneFormAppearance`` object id, or a ``RingtailStatue`` component id. Returns a
    :class:`StoneForm`; every ambiguity raises ``ValueError``.
    """
    graph = _Graph(assets)
    resolved = _resolve(graph, appearance_id, stone_form_id, statue_prefab_id)
    statue = resolved['statue']
    root_go = graph.obj(graph.tree(statue).get('m_GameObject'), 'GameObject',
                        label='stone form statue root')
    root_components = graph.components(root_go)
    for component in root_components:
        if component.type.name not in ROOT_COMPONENT_TYPES:
            raise ValueError(f'stone form statue root component {component.type.name} is unsupported')
    lod_groups = [c for c in root_components if c.type.name == 'LODGroup']
    if len(lod_groups) != 1:
        raise ValueError('stone form statue root has no unique LODGroup')
    root_transform = graph.transform(root_go)
    root_tree = graph.tree(root_transform)
    if graph.ref(root_tree.get('m_Father'), 'stone form root parent', minimum=0) != 0:
        raise ValueError('stone form prefab root is not a root transform')
    root_trs = _trs(root_tree, 'stone form root')
    _flag(graph.tree(root_go).get('m_IsActive'), 'stone form root active flag')
    renderers = graph.lod_renderers(lod_groups[0])

    nodes = graph.subtree(root_go)
    for node in nodes:
        names = [component.type.name for component in graph.components(node['go'])]
        node['component_types'] = names
        mesh_parts = [name for name in names if name in MESH_COMPONENT_TYPES]
        if node['depth'] == 0:
            allowed = ROOT_COMPONENT_TYPES
        elif mesh_parts:
            if node['depth'] != MAX_DEPTH - (MAX_DEPTH - 1):
                raise ValueError('stone form mesh geometry is nested below the LOD level')
            if names.count('MeshFilter') != 1 or names.count('MeshRenderer') != 1:
                raise ValueError('stone form LOD object must declare one MeshFilter and one MeshRenderer')
            allowed = LOD_COMPONENT_TYPES
        else:
            allowed = ATTACHMENT_COMPONENT_TYPES
        for name in names:
            if name not in allowed:
                raise ValueError(f'stone form component {name} is unsupported in this position')
        _flag(graph.tree(node['go']).get('m_IsActive'), 'stone form GameObject active flag')

    details = [graph.renderer_details(renderer, root_transform) for renderer in renderers]
    lod_go_ids = [detail['go'].path_id for detail in details]
    if len(set(lod_go_ids)) != len(lod_go_ids):
        raise ValueError('stone form LOD set repeats a GameObject')
    mesh_go_ids = sorted(node['go'].path_id for node in nodes
                         if any(name in MESH_COMPONENT_TYPES for name in node['component_types']))
    if sorted(lod_go_ids) != mesh_go_ids:
        raise ValueError('stone form LOD renderers do not match the mesh children exactly')
    material_ids = {detail['material'].path_id for detail in details}
    if len(material_ids) != 1:
        raise ValueError('stone form LODs do not share a unique stone material')
    mesh_ids = [detail['mesh'].path_id for detail in details]
    if len(set(mesh_ids)) != len(mesh_ids):
        raise ValueError('stone form LOD set repeats a mesh')

    meshes = []
    for index, detail in enumerate(details):
        label = f'stone form LOD{index}'
        layout = _mesh_layout(graph.tree(detail['mesh']), label)
        geometry = _geometry(_decode_mesh(detail['mesh'], label), layout['vertex_count'],
                             layout['index_count'], label)
        ttree = graph.tree(detail['transform'])
        meshes.append({
            'lod': index,
            'path_id': detail['mesh'].path_id,
            'name': layout['name'],
            'game_object_id': detail['go'].path_id,
            'game_object_name': _name(graph.tree(detail['go']).get('m_Name'),
                                      f'{label} GameObject name'),
            'transform_id': detail['transform'].path_id,
            'mesh_filter_id': detail['mesh_filter'].path_id,
            'mesh_renderer_id': detail['renderer'].path_id,
            'renderer_enabled': detail['enabled'],
            'transform': _trs(ttree, label),
            'vertex_count': geometry['vertex_count'],
            'index_count': geometry['index_count'],
            'triangle_count': geometry['triangle_count'],
            'vertices': geometry['vertices'],
            'normals': geometry['normals'],
            'tangents': geometry['tangents'],
            'uv': geometry['uv'],
            'triangles': geometry['triangles'],
        })
    material = _material(graph, details[0]['material'], 'stone form material')

    provenance = {
        'entry': resolved['entry'],
        'appearance_id': resolved['appearance'].path_id if resolved['appearance'] else None,
        'stone_form_id': resolved['stone_form'].path_id if resolved['stone_form'] else None,
        'stone_form_name': resolved['stone_form_name'],
        'statue_id': statue.path_id,
        'statue_game_object_id': root_go.path_id,
        'root_transform_id': root_transform.path_id,
        'lod_group_id': lod_groups[0].path_id,
        'lod_renderer_ids': [detail['renderer'].path_id for detail in details],
        'mesh_path_ids': [detail['mesh'].path_id for detail in details],
        'material_path_id': material['path_id'],
    }
    root = {
        'game_object_id': root_go.path_id,
        'name': _name(graph.tree(root_go).get('m_Name'), 'stone form root name'),
        'transform_id': root_transform.path_id,
        'transform': root_trs,
        'components': [{'path_id': component.path_id, 'type': component.type.name}
                       for component in root_components],
    }
    return StoneForm(schema=SCHEMA, version=SCHEMA_VERSION, entry=resolved['entry'],
                     provenance=provenance, root=root, meshes=tuple(meshes), material=material)


def _validate_export_form(data):
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('version') != SCHEMA_VERSION:
        raise ValueError('stone form export requires a bridge-stone-form v1 result')
    meshes = data.get('meshes')
    if not isinstance(meshes, list) or len(meshes) != MAX_LODS:
        raise ValueError('stone form export requires the complete three-LOD mesh set')
    for mesh in meshes:
        if not isinstance(mesh, dict) or type(mesh.get('lod')) is not int:
            raise ValueError('stone form export received a malformed mesh record')
        for key in ('vertices', 'triangles'):
            if not isinstance(mesh.get(key), list) or not mesh[key]:
                raise ValueError('stone form export received incomplete geometry')
    material = data.get('material')
    textures = material.get('textures') if isinstance(material, dict) else None
    if not isinstance(textures, dict):
        raise ValueError('stone form export requires resolved material textures')
    for prop in ('_MainTex', '_Normal'):
        record = textures.get(prop)
        if (not isinstance(record, dict) or type(record.get('path_id')) is not int
                or record['path_id'] <= 0):
            raise ValueError(f'stone form export is missing the {prop} texture reference')


def export_stone_form(assets, form, out_dir):
    """Write PNG albedo/normal + ``stone-form.json`` + receipt into a NEW folder.

    ``form`` is a :class:`StoneForm` or its dict form. The destination must not exist;
    nothing is ever overwritten. Returns a small report of what was written.
    """
    data = form.to_dict() if hasattr(form, 'to_dict') else copy.deepcopy(form)
    _validate_export_form(data)
    out_dir = Path(out_dir)
    from bridge.content.sr2_asset_extract import (TextureExporter, _sha256,
                                                  refuse_existing_leaf, refuse_reparse_chain)
    refuse_existing_leaf(out_dir)
    if out_dir.exists():
        raise ValueError(f'stone form export destination already exists: {out_dir}')
    refuse_reparse_chain(out_dir, out_dir.parent)
    objects = source_object_index(assets)
    out_dir.mkdir(parents=True)
    exporter = TextureExporter(assets, out_dir)
    files = []
    for prop in ('_MainTex', '_Normal'):
        record = data['material']['textures'][prop]
        texture = objects.get(record['path_id'])
        if texture is None or texture.type.name != 'Texture2D':
            raise ValueError(f'stone form export texture {prop} is missing from the source')
        record['file'] = exporter.export_source(texture)
        target = out_dir / record['file']
        files.append({'name': record['file'], 'size': target.stat().st_size,
                      'sha256': _sha256(target)})
    target = out_dir / 'stone-form.json'
    refuse_existing_leaf(target)
    target.write_text(json_dump(data), encoding='utf-8')
    files.append({'name': 'stone-form.json', 'size': target.stat().st_size,
                  'sha256': _sha256(target)})
    receipt = {'schema': SCHEMA, 'version': SCHEMA_VERSION, 'source_read_only': True,
               'files': files,
               'counts': {'meshes': len(data['meshes']),
                          'vertices': sum(mesh['vertex_count'] for mesh in data['meshes']),
                          'triangles': sum(mesh['triangle_count'] for mesh in data['meshes'])}}
    receipt_target = out_dir / 'stone-form-receipt.json'
    refuse_existing_leaf(receipt_target)
    receipt_target.write_text(json_dump(receipt), encoding='utf-8')
    return {'schema': SCHEMA, 'version': SCHEMA_VERSION, 'folder': str(out_dir),
            'files': [entry['name'] for entry in files] + ['stone-form-receipt.json'],
            'textures': {prop: data['material']['textures'][prop]['file']
                         for prop in ('_MainTex', '_Normal')}}


def json_dump(payload):
    """Deterministic UTF-8 JSON for the exported files (only plain data is present)."""
    import json

    return json.dumps(payload, indent=1, ensure_ascii=False) + '\n'
