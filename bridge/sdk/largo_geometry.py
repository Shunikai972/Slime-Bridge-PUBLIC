"""Bounded OBJ/MTL, STL and static GLB2 readers. No edits, conversion, image decoding or Unity loading."""
import hashlib
import math
import re
import struct
from pathlib import Path

from bridge.content.canonical import diag
from bridge.sdk import largo
from bridge.sdk.largo_io import MAX_JSON_BYTES, ImportProblem, parse_json, read_bytes

MAX_VERTICES = 500_000
MAX_TRIANGLES = 500_000
MAX_LINE_BYTES = 65536
MAX_REFERENCES = 128


def numbers(values):
    result = [float(value) for value in values]
    if not all(math.isfinite(value) and abs(value) <= 1e20 for value in result):
        raise ImportProblem('NONFINITE_GEOMETRY', 'nonfinite/out-of-range geometry or material value')
    return result


def lines(raw):
    for line in raw.decode('utf-8-sig').splitlines():
        if len(line) > MAX_LINE_BYTES:
            raise ImportProblem('GEOMETRY_LIMIT', 'line length limit')
        fields = line.partition('#')[0].split()
        if fields:
            yield fields[0], fields[1:]


def triangle(a, b, c):
    u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    cross = [u[1]*v[2] - u[2]*v[1], u[2]*v[0] - u[0]*v[2], u[0]*v[1] - u[1]*v[0]]
    if not any(value != 0 for value in cross):
        raise ImportProblem('EMPTY_GEOMETRY', 'degenerate triangle has no surface area')


def bounds(vertices):
    if not vertices:
        raise ImportProblem('EMPTY_GEOMETRY', 'no vertices')
    return {'min': [min(v[i] for v in vertices) for i in range(3)],
            'max': [max(v[i] for v in vertices) for i in range(3)]}


def reference(root, source, rel, records):
    if len(records) >= MAX_REFERENCES:
        raise ImportProblem('GEOMETRY_LIMIT', 'reference limit')
    if largo._safe_relative(root, rel) is None:
        raise ImportProblem('UNSAFE_REFERENCE', f'unsafe reference {rel!r}')
    full_rel = (source.parent / rel).relative_to(root).as_posix()
    path = largo._safe_relative(root, full_rel)
    if path is None:
        raise ImportProblem('UNSAFE_REFERENCE', f'linked reference {rel!r}')
    if not path.is_file():
        raise ImportProblem('REFERENCE_UNREADABLE', f'missing/nonregular reference {rel!r}')
    try:
        raw = read_bytes(path)
    except ImportProblem as exc:
        if exc.code == 'UNSAFE_SOURCE':
            raise ImportProblem('UNSAFE_REFERENCE', str(exc)) from exc
        raise
    except OSError as exc:
        raise ImportProblem('REFERENCE_UNREADABLE', rel) from exc
    if not raw:
        raise ImportProblem('REFERENCE_EMPTY', f'empty external dependency {rel!r}')
    records[full_rel] = {'path': full_rel, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
    return path, raw


def material_library(root, source, rel, records):
    path, raw = reference(root, source, rel, records)
    defined, current = set(), None
    for key, args in lines(raw):
        if key == 'newmtl':
            if len(args) != 1 or args[0] in defined:
                raise ValueError('unique MTL material name required')
            current = args[0]
            defined.add(current)
        elif current is None:
            raise ValueError('MTL property before material name')
        elif key in ('Ka', 'Kd', 'Ks', 'Ke', 'Tf'):
            if len(args) != 3:
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'spectral/xyz MTL variant unsupported')
            numbers(args)
        elif key in ('Ns', 'Ni', 'd', 'Tr', 'Pr', 'Pm', 'Ps', 'Pc', 'Pcr'):
            if len(args) != 1:
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'MTL numeric variant unsupported')
            numbers(args)
        elif key == 'illum':
            if len(args) != 1 or not re.fullmatch(r'\d+', args[0]):
                raise ValueError('invalid illumination model')
        elif key.startswith('map_') or key in ('bump', 'norm', 'disp', 'decal', 'refl'):
            if len(args) != 1 or args[0].startswith('-'):
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'MTL texture options/space-containing names unsupported')
            reference(root, path, args[0], records)
        else:
            raise ImportProblem('NEEDS_FORMAT_ADAPTER', f'unsupported MTL directive {key}')
    return defined


def inspect_obj(root, path, raw):
    vertices, uv, normals, faces, used, defined, records = [], [], [], 0, set(), set(), {}

    def index(value, data):
        if not re.fullmatch(r'-?[1-9]\d*', value):
            raise ValueError('nonzero integer OBJ index required')
        n = int(value)
        n = n - 1 if n > 0 else len(data) + n
        if not 0 <= n < len(data):
            raise ValueError('OBJ index out of bounds')
        return n

    for key, args in lines(raw):
        if key == 'v':
            if len(args) not in (3, 4):
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'OBJ vertex extension unsupported')
            position = numbers(args)
            if len(position) == 4:
                if position[3] == 0:
                    raise ValueError('zero homogeneous coordinate')
                position = numbers([position[i] / position[3] for i in range(3)])
            vertices.append(position)
        elif key == 'vt':
            if not 1 <= len(args) <= 3:
                raise ValueError('invalid texture coordinate')
            uv.append(numbers(args))
        elif key == 'vn':
            if len(args) != 3:
                raise ValueError('invalid normal')
            normals.append(numbers(args))
        elif key == 'f':
            if len(args) != 3:
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'only already-triangulated OBJ faces supported')
            points = []
            for value in args:
                parts = value.split('/')
                if len(parts) > 3 or not parts[0] or (len(parts) == 2 and not parts[1]):
                    raise ValueError('invalid face reference')
                points.append(vertices[index(parts[0], vertices)])
                if len(parts) > 1 and parts[1]:
                    index(parts[1], uv)
                if len(parts) > 2:
                    index(parts[2], normals)
            triangle(*points)
            faces += 1
        elif key == 'mtllib':
            if not args:
                raise ValueError('empty mtllib')
            for rel in args:
                names = material_library(root, path, rel, records)
                if names & defined:
                    raise ValueError('material defined in multiple libraries')
                defined |= names
        elif key == 'usemtl':
            if len(args) != 1:
                raise ValueError('invalid material name')
            used.add(args[0])
        elif key in ('o', 'g', 's'):
            pass  # labels/smoothing groups do not alter positions or indices
        else:
            raise ImportProblem('NEEDS_FORMAT_ADAPTER', f'unsupported OBJ directive {key}')
        if max(len(vertices), len(uv), len(normals)) > MAX_VERTICES or faces > MAX_TRIANGLES:
            raise ImportProblem('GEOMETRY_LIMIT', 'vertex/triangle limit')
    if used - defined:
        raise ImportProblem('MATERIAL_UNDEFINED', ','.join(sorted(used - defined)))
    if not faces:
        raise ImportProblem('EMPTY_GEOMETRY', 'no triangles')
    return {'vertices': len(vertices), 'triangles': faces, 'bounds': bounds(vertices),
            'materials': sorted(defined), 'external_references': [records[key] for key in sorted(records)]}


def inspect_stl(raw):
    vertices, count = [], 0
    if len(raw) >= 84 and len(raw) == 84 + struct.unpack_from('<I', raw, 80)[0] * 50:
        count = struct.unpack_from('<I', raw, 80)[0]
        if count * 3 > MAX_VERTICES or count > MAX_TRIANGLES:
            raise ImportProblem('GEOMETRY_LIMIT', 'STL vertex/triangle limit')
        for offset in range(84, len(raw), 50):
            *values, attribute = struct.unpack_from('<12fH', raw, offset)
            numbers(values)
            if attribute:
                raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'binary STL colour/attribute unsupported')
            points = [values[i:i+3] for i in (3, 6, 9)]
            triangle(*points)
            vertices.extend(points)
    else:
        data = list(lines(raw))
        if not data or data[0][0] != 'solid' or data[-1][0] != 'endsolid':
            raise ValueError('invalid/truncated STL header/length')
        body = data[1:-1]
        if len(body) % 7:
            raise ValueError('incomplete ASCII STL facet')
        count = len(body) // 7
        if count * 3 > MAX_VERTICES or count > MAX_TRIANGLES:
            raise ImportProblem('GEOMETRY_LIMIT', 'STL vertex/triangle limit')
        for offset in range(0, len(body), 7):
            facet = body[offset:offset+7]
            if facet[0][0] != 'facet' or len(facet[0][1]) != 4 or facet[0][1][0] != 'normal' or \
                    facet[1] != ('outer', ['loop']) or facet[5] != ('endloop', []) or facet[6] != ('endfacet', []):
                raise ValueError('malformed ASCII STL facet')
            numbers(facet[0][1][1:])
            points = []
            for key, args in facet[2:5]:
                if key != 'vertex' or len(args) != 3:
                    raise ValueError('malformed ASCII STL vertex')
                points.append(numbers(args))
            triangle(*points)
            vertices.extend(points)
    if not count:
        raise ImportProblem('EMPTY_GEOMETRY', 'no STL triangles')
    return {'vertices': len(vertices), 'triangles': count, 'bounds': bounds(vertices),
            'materials': [], 'external_references': []}


GLB_JSON, GLB_BIN = 0x4E4F534A, 0x004E4942
GLTF_COMPONENTS = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2), 5123: ('H', 2), 5125: ('I', 4), 5126: ('f', 4)}
GLTF_INDEX_COMPONENTS = {5121, 5123, 5125}
GLTF_TYPES = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
GLTF_ROOT_KEYS = {'asset', 'scene', 'scenes', 'nodes', 'meshes', 'materials', 'buffers', 'bufferViews',
                  'accessors', 'extras'}
GLTF_MATERIAL_KEYS = {'name', 'extras', 'pbrMetallicRoughness', 'doubleSided', 'alphaMode', 'alphaCutoff',
                      'emissiveFactor'}
GLTF_PBR_KEYS = {'baseColorFactor', 'metallicFactor', 'roughnessFactor', 'extras'}


def adapter(detail):
    return ImportProblem('NEEDS_FORMAT_ADAPTER', detail)


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def integer(value, detail, minimum=0):
    require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum, detail)
    return value


def item(document, key, value, detail):
    collection = document.get(key)
    require(isinstance(collection, list), f'{key} array required for {detail}')
    integer(value, f'{detail} index must be an integer')
    require(value < len(collection) and isinstance(collection[value], dict), f'{detail} index out of range')
    return collection[value]


def only(mapping, allowed, detail):
    require(isinstance(mapping, dict), f'{detail} must be an object')
    extra = set(mapping) - allowed
    if extra:
        raise adapter(f'{detail} feature unsupported: {",".join(sorted(extra))[:80]}')


def float32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def glb_chunks(raw):
    require(len(raw) >= 12 and raw[:4] == b'glTF', 'GLB magic required')
    version, length = struct.unpack_from('<II', raw, 4)
    if version != 2:
        raise adapter(f'GLB container version {version} unsupported')
    require(length == len(raw), 'GLB declared length does not match file size')
    chunks, offset = [], 12
    while offset < len(raw):
        require(offset + 8 <= len(raw), 'truncated GLB chunk header')
        size, kind = struct.unpack_from('<II', raw, offset)
        require(size % 4 == 0 and offset + 8 + size <= len(raw), 'misaligned or truncated GLB chunk')
        chunks.append((kind, raw[offset + 8:offset + 8 + size]))
        offset += 8 + size
    require(chunks and chunks[0][0] == GLB_JSON, 'GLB JSON chunk must be first')
    kinds = [kind for kind, _ in chunks]
    require(kinds.count(GLB_JSON) == 1 and kinds.count(GLB_BIN) <= 1, 'duplicate GLB JSON/BIN chunk')
    require(GLB_BIN not in kinds or kinds.index(GLB_BIN) == 1, 'GLB BIN chunk must be second')
    if any(kind not in (GLB_JSON, GLB_BIN) for kind in kinds):
        raise adapter('unknown GLB extension chunk')
    if len(chunks[0][1]) > MAX_JSON_BYTES:
        raise ImportProblem('GEOMETRY_LIMIT', 'GLB JSON chunk limit')
    return chunks[0][1], (chunks[1][1] if len(chunks) > 1 else None)


def gltf_accessor(document, binary, index, role):
    accessor = item(document, 'accessors', index, role)
    if 'sparse' in accessor or 'bufferView' not in accessor:
        raise adapter('sparse/implicit-zero accessor unsupported')
    only(accessor, {'bufferView', 'byteOffset', 'componentType', 'normalized', 'count', 'type', 'max', 'min',
                    'name', 'extras'}, f'{role} accessor')
    component = accessor.get('componentType')
    require(component in GLTF_COMPONENTS and not isinstance(component, bool), f'{role} componentType invalid')
    require(accessor.get('type') in GLTF_TYPES, f'{role} accessor type invalid')
    require(accessor.get('normalized', False) in (False, True), f'{role} normalized must be boolean')
    if role == 'POSITION':
        require(accessor['type'] == 'VEC3', 'POSITION must be VEC3')
        if component != 5126:
            raise adapter('quantized POSITION unsupported')
        require(accessor.get('normalized') is not True, 'float POSITION cannot be normalized')
    else:
        require(accessor['type'] == 'SCALAR' and component in GLTF_INDEX_COMPONENTS and
                accessor.get('normalized') is not True, 'indices must be unnormalized unsigned SCALAR')
    count = integer(accessor.get('count'), f'{role} count', 1)
    limit = MAX_VERTICES if role == 'POSITION' else MAX_TRIANGLES * 3
    if count > limit:
        raise ImportProblem('GEOMETRY_LIMIT', 'vertex/triangle limit')
    view = item(document, 'bufferViews', accessor['bufferView'], f'{role} bufferView')
    only(view, {'buffer', 'byteOffset', 'byteLength', 'byteStride', 'target', 'name', 'extras'}, 'bufferView')
    buffer = item(document, 'buffers', view.get('buffer'), 'buffer')
    view_offset = integer(view.get('byteOffset', 0), 'bufferView byteOffset')
    view_length = integer(view.get('byteLength'), 'bufferView byteLength', 1)
    require(view_offset + view_length <= buffer['byteLength'], 'bufferView exceeds buffer')
    code, size = GLTF_COMPONENTS[component]
    width = GLTF_TYPES[accessor['type']]
    element = size * width
    stride = view.get('byteStride')
    if stride is None:
        stride = element
    else:
        integer(stride, 'byteStride', 4)
        require(role == 'POSITION', 'index bufferView must not define byteStride')
        require(stride <= 252 and stride % 4 == 0 and stride >= element, 'byteStride invalid')
    offset = integer(accessor.get('byteOffset', 0), 'accessor byteOffset')
    require(offset % size == 0 and (view_offset + offset) % size == 0, 'accessor misaligned')
    require(offset + (count - 1) * stride + element <= view_length, 'accessor exceeds bufferView')
    base = view_offset + offset
    values = [struct.unpack_from(f'<{width}{code}', binary, base + i * stride) for i in range(count)]
    if code == 'f':
        for value in values:
            numbers(value)  # nonfinite data is reported as such, before any min/max comparison
    for key in ('min', 'max'):
        declared = accessor.get(key)
        if declared is None and role != 'POSITION':
            continue
        require(isinstance(declared, list) and len(declared) == width and
                all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in declared),
                f'{role} accessor {key} invalid')
        actual = [(min if key == 'min' else max)(v[i] for v in values) for i in range(width)]
        expected = [float32(v) for v in declared] if code == 'f' else declared
        require(actual == expected, f'{role} accessor {key} does not match decoded data')
    return values


def inspect_glb(raw):
    """Static single-primitive GLB2 profile; every valid feature outside it needs an adapter."""
    text, binary = glb_chunks(raw)
    try:
        document = parse_json(text)
    except RecursionError:
        raise ImportProblem('GEOMETRY_LIMIT', 'GLB JSON nesting limit') from None
    require(isinstance(document, dict), 'GLB JSON root must be an object')
    asset = document.get('asset')
    require(isinstance(asset, dict) and isinstance(asset.get('version'), str), 'asset.version required')
    require(re.fullmatch(r'\d+\.\d+', asset['version']), 'asset.version must be major.minor')
    if asset['version'] != '2.0' or 'minVersion' in asset:
        raise adapter(f'glTF asset version {asset["version"]} unsupported')
    only(document, GLTF_ROOT_KEYS, 'glTF')
    buffers = document.get('buffers')
    require(isinstance(buffers, list) and buffers, 'buffers required')
    if len(buffers) != 1:
        raise adapter('multiple buffers unsupported')
    buffer = buffers[0]
    if isinstance(buffer, dict) and 'uri' in buffer:
        raise adapter('external/data-URI buffers are never opened')
    only(buffer, {'byteLength', 'name', 'extras'}, 'buffer')
    length = integer(buffer.get('byteLength'), 'buffer byteLength', 1)
    require(binary is not None and length <= len(binary) <= length + 3, 'embedded BIN chunk missing or mis-sized')
    scenes, nodes, meshes = document.get('scenes'), document.get('nodes'), document.get('meshes')
    require(isinstance(meshes, list) and meshes, 'meshes required')
    if not (isinstance(scenes, list) and len(scenes) == 1 and isinstance(nodes, list) and len(nodes) == 1
            and len(meshes) == 1):
        raise adapter('only one scene, one node and one mesh are supported')
    if 'scene' in document:
        item(document, 'scenes', document['scene'], 'scene')
    only(scenes[0], {'nodes', 'name', 'extras'}, 'scene')
    require(scenes[0].get('nodes') == [0], 'scene must reference its single node')
    only(nodes[0], {'mesh', 'name', 'extras'}, 'node')
    item(document, 'meshes', nodes[0].get('mesh'), 'node mesh')
    only(meshes[0], {'primitives', 'name', 'extras'}, 'mesh')
    primitives = meshes[0].get('primitives')
    require(isinstance(primitives, list) and primitives, 'mesh primitives required')
    if len(primitives) != 1:
        raise adapter('multiple primitives unsupported')
    primitive = primitives[0]
    only(primitive, {'attributes', 'indices', 'material', 'mode', 'extras'}, 'primitive')
    mode = primitive.get('mode', 4)
    require(mode in range(7) and not isinstance(mode, bool), 'primitive mode invalid')
    if mode != 4:
        raise adapter('only TRIANGLES primitives are supported')
    attributes = primitive.get('attributes')
    require(isinstance(attributes, dict) and attributes, 'primitive attributes required')
    if set(attributes) != {'POSITION'}:
        raise adapter('only the POSITION attribute is supported')
    materials = document.get('materials', [])
    require(isinstance(materials, list), 'materials must be an array')
    names = set()
    for material in materials:
        only(material, GLTF_MATERIAL_KEYS, 'material')
        if 'pbrMetallicRoughness' in material:
            only(material['pbrMetallicRoughness'], GLTF_PBR_KEYS, 'material PBR')
        if isinstance(material.get('name'), str):
            names.add(material['name'])
    if 'material' in primitive:
        item(document, 'materials', primitive['material'], 'primitive material')
    data = binary[:length]
    try:
        vertices = [numbers(v) for v in gltf_accessor(document, data, attributes['POSITION'], 'POSITION')]
        if 'indices' in primitive:
            indices = [v[0] for v in gltf_accessor(document, data, primitive['indices'], 'indices')]
        else:
            indices = list(range(len(vertices)))
    except struct.error as exc:
        raise ValueError(f'GLB accessor read failed: {exc}') from None
    restart = {1: 0xFF, 2: 0xFFFF, 4: 0xFFFFFFFF}
    if 'indices' in primitive:
        size = GLTF_COMPONENTS[document['accessors'][primitive['indices']]['componentType']][1]
        require(restart[size] not in indices, 'primitive-restart index value is invalid')
    require(len(indices) % 3 == 0 and all(i < len(vertices) for i in indices), 'invalid triangle indices')
    for offset in range(0, len(indices), 3):
        triangle(*(vertices[i] for i in indices[offset:offset + 3]))
    return {'vertices': len(vertices), 'triangles': len(indices) // 3, 'bounds': bounds(vertices),
            'materials': sorted(names), 'external_references': []}


def inspect_model(root, path):
    root, path = Path(root), Path(path)
    report = {'path': path.relative_to(root).as_posix(), 'format': path.suffix.lower().lstrip('.'),
              'status': 'REJECTED', 'geometry_checked': False, 'runtime_checked': False,
              'vertices': None, 'triangles': None, 'bounds': None, 'materials': [],
              'external_references': [], 'sha256': None, 'diagnostics': [],
              'transform_scope': 'static local coordinates only; import transform checked separately',
              'texture_scope': 'confined references and hashes only; image pixels not decoded'}
    try:
        raw = read_bytes(path)
        report['sha256'] = hashlib.sha256(raw).hexdigest()
        if not raw:
            raise ImportProblem('EMPTY_GEOMETRY', 'empty file')
        if report['format'] == 'obj':
            checked = inspect_obj(root, path, raw)
        elif report['format'] == 'stl':
            checked = inspect_stl(raw)
        elif report['format'] == 'glb':
            checked = inspect_glb(raw)
        else:
            raise ImportProblem('NEEDS_FORMAT_ADAPTER', 'no verified bounded reader for this format')
        report.update(checked, status='GEOMETRY_VALID', geometry_checked=True)
    except (OSError, ValueError, OverflowError) as exc:
        code = getattr(exc, 'code', 'MALFORMED_GEOMETRY')
        report['status'] = 'NEEDS_FORMAT_ADAPTER' if code == 'NEEDS_FORMAT_ADAPTER' else 'REJECTED'
        report['diagnostics'] = [diag(code, 'warning' if code == 'NEEDS_FORMAT_ADAPTER' else 'error',
                                      report['path'], str(exc)[:200])]
    return report
