"""Binary glTF 2.0 (.glb) writer and a stdlib self-check validator.

Layout written by ``export_glb``:
  * one buffer (the GLB BIN chunk); one tightly packed bufferView per accessor, 4-byte aligned;
  * one glTF mesh (single triangle primitive) per part per LOD: POSITION (with min/max),
    NORMAL, TEXCOORD_0 and uint16/uint32 indices;
  * one material per semantic id actually used, named by that id, with metallic-roughness
    factors from the palette (linear colour), emissive factors and BLEND/double-sided for the
    portal energy; no textures;
  * nodes: ``<root>`` -> ``gateway_LOD<n>`` -> ``<part>_LOD<n>``; ``collision`` -> ``COL_<box>``;
    ``TRIGGER_portal``; ``sockets`` -> ``socket.*``. Collision/trigger nodes are empties whose
    scale is the box half extents (a unit box spanning [-1, 1] fits them) with the exact box in
    ``extras``; sockets carry translation + rotation (+Z forward, +Y up).

``validate_glb`` parses the container, JSON and binary, and checks buffer-view bounds, accessor
sizes/alignment/min/max, finite floats, index ranges and the node graph. It is the only import
test performed here: Blender/Unity import is NEEDS_LOCAL_VISUAL_PROOF.
"""

import json
import math
import struct

from bridge.core import diagnostics as D

from . import mesh as M
from .palettes import SEMANTIC_MATERIALS, linear, material
from .portal import GENERATOR, GENERATOR_VERSION, VISUAL_EVIDENCE

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
BYTE, UBYTE, SHORT, USHORT, UINT, FLOAT = 5120, 5121, 5122, 5123, 5125, 5126
ARRAY_BUFFER, ELEMENT_ARRAY_BUFFER = 34962, 34963
_COMPONENT = {BYTE: ('b', 1), UBYTE: ('B', 1), SHORT: ('h', 2), USHORT: ('H', 2), UINT: ('I', 4), FLOAT: ('f', 4)}
_TYPE = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}


def _pad(data, fill=b'\x00'):
    return data + fill * ((4 - len(data) % 4) % 4)


class _Bin:
    def __init__(self):
        self.data = bytearray()
        self.views = []
        self.accessors = []

    def _view(self, payload, target):
        offset = len(self.data)
        self.data += _pad(payload)
        self.views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(payload), 'target': target})
        return len(self.views) - 1

    def floats(self, rows, kind, minmax=False):
        width = _TYPE[kind]
        flat = [c for row in rows for c in row]
        payload = struct.pack(f'<{len(flat)}f', *flat)
        accessor = {'bufferView': self._view(payload, ARRAY_BUFFER), 'componentType': FLOAT, 'count': len(rows),
                    'type': kind}
        if minmax:
            f32 = struct.unpack(f'<{len(flat)}f', payload)  # min/max must match the stored float32 values
            accessor['min'] = [min(f32[k::width]) for k in range(width)]
            accessor['max'] = [max(f32[k::width]) for k in range(width)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def indices(self, values, vertex_count):
        ctype, code = (USHORT, 'H') if vertex_count <= 0xFFFF else (UINT, 'I')
        payload = struct.pack(f'<{len(values)}{code}', *values)
        self.accessors.append({'bufferView': self._view(payload, ELEMENT_ARRAY_BUFFER), 'componentType': ctype,
                               'count': len(values), 'type': 'SCALAR'})
        return len(self.accessors) - 1


def _material_doc(palette_id, semantic):
    m = material(palette_id, semantic)
    doc = {'name': semantic,
           'pbrMetallicRoughness': {'baseColorFactor': list(linear(m['base_srgb'])) + [m['alpha']],
                                    'metallicFactor': m['metallic'], 'roughnessFactor': m['roughness']},
           'extras': {'slimebridge_semantic_material': semantic, 'palette': palette_id,
                      'base_color_srgb': list(m['base_srgb'])}}
    if any(m['emissive_srgb']):
        doc['emissiveFactor'] = list(linear(m['emissive_srgb']))
    if m['alpha'] < 1.0:
        doc['alphaMode'] = 'BLEND'
    if m['double_sided']:
        doc['doubleSided'] = True
    return doc


def _box_node(name, box, extras):
    return {'name': name, 'translation': list(box['center']), 'rotation': list(box['rotation']),
            'scale': list(box['half_extents']), 'extras': extras}


def export_glb(model, levels=None, name=None):
    """GLB bytes for a generated ``PortalModel`` (all LODs unless ``levels`` is given)."""
    if not model.ok:
        raise ValueError('cannot export a model with errors')
    levels = [lod['level'] for lod in model.lods] if levels is None else list(levels)
    lods = [lod for lod in model.lods if lod['level'] in levels]
    used = [s for s in SEMANTIC_MATERIALS if any(p.material == s for lod in lods for p in lod['parts'])]
    mat_index = {s: i for i, s in enumerate(used)}
    out = _Bin()
    meshes, nodes = [], [None]
    root_children = []
    for lod in lods:
        level = lod['level']
        group = {'name': f'gateway_LOD{level}', 'children': [],
                 'extras': {'lod': level, 'triangles': M.triangle_count(lod['parts']),
                            'detail': lod['detail']}}
        nodes.append(group)
        root_children.append(len(nodes) - 1)
        for part in lod['parts']:
            prim = {'attributes': {'POSITION': out.floats(part.positions, 'VEC3', minmax=True),
                                   'NORMAL': out.floats(part.normals, 'VEC3'),
                                   'TEXCOORD_0': out.floats(part.uvs, 'VEC2')},
                    'indices': out.indices(part.indices, part.vertex_count),
                    'material': mat_index[part.material], 'mode': 4}
            meshes.append({'name': f'{part.name}_LOD{level}', 'primitives': [prim],
                           'extras': {'semantic_material': part.material}})
            nodes.append({'name': f'{part.name}_LOD{level}', 'mesh': len(meshes) - 1,
                          'extras': {'part': part.name, 'semantic_material': part.material, 'lod': level}})
            group['children'].append(len(nodes) - 1)
    collision = {'name': 'collision', 'children': [],
                 'extras': {'slimebridge_role': 'collision_proxy', 'render': False,
                            'note': 'box empties: scale = half extents; no collision across the opening'}}
    nodes.append(collision)
    root_children.append(len(nodes) - 1)
    for box in model.collision:
        nodes.append(_box_node(f'COL_{box["name"]}', box, {'slimebridge_collision': box}))
        collision['children'].append(len(nodes) - 1)
    nodes.append(_box_node('TRIGGER_portal', model.trigger, {'slimebridge_trigger': model.trigger, 'render': False}))
    root_children.append(len(nodes) - 1)
    sockets = {'name': 'sockets', 'children': [], 'extras': {'slimebridge_role': 'sockets'}}
    nodes.append(sockets)
    root_children.append(len(nodes) - 1)
    for sname in sorted(model.sockets):
        s = model.sockets[sname]
        nodes.append({'name': sname, 'translation': s['position'], 'rotation': s['rotation'],
                      'extras': {'slimebridge_socket': {'forward': s['forward'], 'up': s['up']}}})
        sockets['children'].append(len(nodes) - 1)
    md = model.metadata
    root_name = name or f'SlimeBridgeGateway_{model.params.variant}'
    nodes[0] = {'name': root_name, 'children': root_children,
                'extras': {'slimebridge': {k: md[k] for k in ('schema', 'schema_version', 'generator',
                                                              'generator_version', 'variant', 'palette', 'params_hash',
                                                              'conventions', 'bounds', 'opening', 'bridge', 'contract',
                                                              'triangle_counts', 'evidence', 'evidence_detail')},
                           'params': model.params.to_dict()}}
    doc = {
        'asset': {'version': '2.0', 'generator': f'SlimeBridge {GENERATOR} {GENERATOR_VERSION}',
                  'extras': {'evidence': VISUAL_EVIDENCE, 'params_hash': model.params_hash}},
        'scene': 0,
        'scenes': [{'name': root_name, 'nodes': [0]}],
        'nodes': nodes,
        'meshes': meshes,
        'materials': [_material_doc(model.params.palette, s) for s in used],
        'accessors': out.accessors,
        'bufferViews': out.views,
        'buffers': [{'byteLength': len(out.data)}],
    }
    json_bytes = _pad(json.dumps(doc, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                                 allow_nan=False).encode('utf-8'), b' ')
    bin_bytes = bytes(out.data)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    return (struct.pack('<III', GLB_MAGIC, GLB_VERSION, total) +
            struct.pack('<II', len(json_bytes), JSON_CHUNK) + json_bytes +
            struct.pack('<II', len(bin_bytes), BIN_CHUNK) + bin_bytes)


# --- self-check ---------------------------------------------------------------------------------

def read_glb(data):
    """``(json_document, bin_bytes)``; raises ValueError on a malformed container."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 20:
        raise ValueError('too short for a GLB')
    magic, version, length = struct.unpack_from('<III', data, 0)
    if magic != GLB_MAGIC:
        raise ValueError('bad magic')
    if version != GLB_VERSION:
        raise ValueError(f'unsupported GLB version {version}')
    if length != len(data):
        raise ValueError('header length does not match the data size')
    jlen, jtype = struct.unpack_from('<II', data, 12)
    if jtype != JSON_CHUNK or jlen % 4 or 20 + jlen > len(data):
        raise ValueError('first chunk must be a 4-byte aligned JSON chunk inside the file')
    try:
        doc = json.loads(bytes(data[20:20 + jlen]).decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f'JSON chunk does not parse: {exc}') from None
    if not isinstance(doc, dict):
        raise ValueError('JSON chunk is not an object')
    offset, binary = 20 + jlen, b''
    if offset < len(data):
        if offset + 8 > len(data):
            raise ValueError('truncated BIN chunk header')
        blen, btype = struct.unpack_from('<II', data, offset)
        if btype != BIN_CHUNK or blen % 4 or offset + 8 + blen != len(data):
            raise ValueError('second chunk must be a 4-byte aligned BIN chunk ending the file')
        binary = bytes(data[offset + 8:offset + 8 + blen])
    return doc, binary


def _index(value, items):
    return type(value) is int and 0 <= value < len(items)


def validate_glb(data):
    """Unified diagnostics for GLB bytes (empty list == passes this self-check). Never raises."""
    out = []

    def err(code, message, **details):
        out.append(D.make(code, 'error', 'meshgen', message, subject='glb', details=details))
    try:
        doc, binary = read_glb(data)
    except (ValueError, struct.error) as exc:
        err('GLB_CONTAINER_INVALID', str(exc))
        return out
    try:
        _validate_doc(doc, binary, err)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError, struct.error) as exc:
        err('GLB_STRUCTURE_INVALID', f'{type(exc).__name__}: {exc}')
    return out


def _validate_doc(doc, binary, err):
    if not isinstance(doc.get('asset'), dict) or doc['asset'].get('version') != '2.0':
        err('GLB_ASSET_VERSION', 'asset.version must be "2.0"')
    buffers = doc.get('buffers', [])
    if len(buffers) != 1 or 'uri' in buffers[0]:
        err('GLB_BUFFERS', 'expected exactly one embedded buffer')
        return
    blen = buffers[0].get('byteLength')
    if type(blen) is not int or blen > len(binary) or len(binary) - blen > 3:
        err('GLB_BUFFER_LENGTH', 'buffer byteLength does not match the BIN chunk', byteLength=blen, bin=len(binary))
        return
    views = doc.get('bufferViews', [])
    for i, v in enumerate(views):
        off, ln = v.get('byteOffset', 0), v.get('byteLength')
        if v.get('buffer') != 0 or type(off) is not int or type(ln) is not int or off < 0 or ln <= 0 or off + ln > blen:
            err('GLB_BUFFER_VIEW_BOUNDS', 'bufferView outside the buffer', view=i)
        elif off % 4:
            err('GLB_BUFFER_VIEW_ALIGNMENT', 'bufferView offset is not 4-byte aligned', view=i)
        stride = v.get('byteStride')
        if stride is not None and (type(stride) is not int or stride < 4 or stride > 252 or stride % 4):
            err('GLB_BUFFER_VIEW_STRIDE', 'invalid byteStride', view=i)
    accessors = doc.get('accessors', [])
    values = {}
    for i, a in enumerate(accessors):
        ok = _index(a.get('bufferView'), views) and a.get('componentType') in _COMPONENT and a.get('type') in _TYPE
        count, aoff = a.get('count'), a.get('byteOffset', 0)
        if not ok or type(count) is not int or count < 1 or type(aoff) is not int or aoff < 0:
            err('GLB_ACCESSOR_INVALID', 'accessor references or fields are invalid', accessor=i)
            continue
        code, size = _COMPONENT[a['componentType']]
        width = _TYPE[a['type']]
        view = views[a['bufferView']]
        elem = size * width
        stride = view.get('byteStride') or elem
        needed = aoff + stride * (count - 1) + elem
        if needed > view['byteLength']:
            err('GLB_ACCESSOR_BOUNDS', 'accessor reads past its bufferView', accessor=i, needed=needed,
                available=view['byteLength'])
            continue
        start = view.get('byteOffset', 0) + aoff
        if start % size:
            err('GLB_ACCESSOR_ALIGNMENT', 'accessor data is not aligned to its component size', accessor=i)
            continue
        if stride != elem:
            continue  # this writer never interleaves; strided data is not decoded here
        vals = struct.unpack_from(f'<{count * width}{code}', binary, start)
        values[i] = vals
        if a['componentType'] == FLOAT:
            if not all(math.isfinite(x) for x in vals):
                err('GLB_NON_FINITE', 'accessor contains NaN or infinity', accessor=i)
                continue
            for key, fn in (('min', min), ('max', max)):
                if key in a:
                    if not isinstance(a[key], list) or len(a[key]) != width:
                        err('GLB_ACCESSOR_MINMAX', f'accessor {key} has the wrong size', accessor=i)
                    elif [fn(vals[k::width]) for k in range(width)] != a[key]:
                        err('GLB_ACCESSOR_MINMAX', f'accessor {key} does not match its data', accessor=i)
    materials = doc.get('materials', [])
    for mi, mesh in enumerate(doc.get('meshes', [])):
        prims = mesh.get('primitives')
        if not isinstance(prims, list) or not prims:
            err('GLB_MESH_EMPTY', 'mesh has no primitives', mesh=mi)
            continue
        for pi, prim in enumerate(prims):
            attrs = prim.get('attributes', {})
            pos = attrs.get('POSITION')
            if not _index(pos, accessors) or not all(_index(x, accessors) for x in attrs.values()):
                err('GLB_PRIMITIVE_ATTRIBUTES', 'primitive attributes reference missing accessors',
                    mesh=mi, primitive=pi)
                continue
            if accessors[pos].get('type') != 'VEC3' or 'min' not in accessors[pos] or 'max' not in accessors[pos]:
                err('GLB_POSITION_ACCESSOR', 'POSITION must be VEC3 with min and max', mesh=mi, primitive=pi)
            expected = {'NORMAL': 'VEC3', 'TEXCOORD_0': 'VEC2'}
            for key, acc in attrs.items():
                if key in expected and accessors[acc].get('type') != expected[key]:
                    err('GLB_ATTRIBUTE_TYPE', f'{key} has the wrong accessor type', mesh=mi, primitive=pi)
            vcount = accessors[pos]['count']
            if any(accessors[x]['count'] != vcount for x in attrs.values()):
                err('GLB_ATTRIBUTE_COUNT', 'attribute accessors disagree on the vertex count', mesh=mi, primitive=pi)
            if prim.get('mode', 4) != 4:
                err('GLB_PRIMITIVE_MODE', 'only triangle lists are expected', mesh=mi, primitive=pi)
            if 'material' in prim and not _index(prim['material'], materials):
                err('GLB_MATERIAL_INDEX', 'primitive references a missing material', mesh=mi, primitive=pi)
            idx = prim.get('indices')
            if idx is not None:
                acc = accessors[idx] if _index(idx, accessors) else None
                if acc is None or acc.get('type') != 'SCALAR' or acc.get('componentType') not in (UBYTE, USHORT, UINT):
                    err('GLB_INDEX_ACCESSOR', 'indices must be an unsigned SCALAR accessor', mesh=mi, primitive=pi)
                    continue
                if acc['count'] % 3:
                    err('GLB_INDEX_COUNT', 'triangle index count is not a multiple of 3', mesh=mi, primitive=pi)
                if idx in values and values[idx] and max(values[idx]) >= vcount:
                    err('GLB_INDEX_RANGE', 'triangle index outside the vertex range', mesh=mi, primitive=pi,
                        max_index=max(values[idx]), vertices=vcount)
    nodes = doc.get('nodes', [])
    parent = {}
    for ni, node in enumerate(nodes):
        if 'mesh' in node and not _index(node['mesh'], doc.get('meshes', [])):
            err('GLB_NODE_MESH', 'node references a missing mesh', node=ni)
        for child in node.get('children', []):
            if not _index(child, nodes) or child == ni:
                err('GLB_NODE_CHILD', 'node references an invalid child', node=ni)
            elif child in parent:
                err('GLB_NODE_PARENTS', 'node has more than one parent', node=child)
            else:
                parent[child] = ni
        for key, size in (('translation', 3), ('rotation', 4), ('scale', 3)):
            value = node.get(key)
            if value is not None and (not isinstance(value, list) or len(value) != size or
                                      not all(isinstance(c, (int, float)) and math.isfinite(c) for c in value)):
                err('GLB_NODE_TRANSFORM', f'node {key} is malformed', node=ni)
        rot = node.get('rotation')
        if isinstance(rot, list) and len(rot) == 4 and abs(math.sqrt(sum(c * c for c in rot)) - 1.0) > 1e-3:
            err('GLB_NODE_ROTATION', 'node rotation is not a unit quaternion', node=ni)
    for ni in range(len(nodes)):  # cycle check through the parent chain
        seen, cur = set(), ni
        while cur in parent:
            if cur in seen:
                err('GLB_NODE_CYCLE', 'node hierarchy contains a cycle', node=ni)
                break
            seen.add(cur)
            cur = parent[cur]
    scenes = doc.get('scenes', [])
    for si, scene in enumerate(scenes):
        for root in scene.get('nodes', []):
            if not _index(root, nodes) or root in parent:
                err('GLB_SCENE_ROOT', 'scene root is missing or is a child node', scene=si)
    if 'scene' in doc and not _index(doc['scene'], scenes):
        err('GLB_SCENE_INDEX', 'default scene index is invalid')
