"""Lossless on-disk IR storage.

* ``.sbmesh``: one mesh, Unity space, little-endian, trivially readable from C# (BinaryReader) and numpy.
      b'SBM1' | u32 header_len | header JSON (utf-8) | blob
  The header lists every array as {name, dtype, shape, offset, nbytes}; arrays are 4-byte aligned.
* Textures are stored once per content hash as PNG (``textures/<sha256[:16]>.png``).
"""

import hashlib
import json
import struct

import numpy as np

MAGIC = b'SBM1'


def _pad(n):
    return (4 - n % 4) % 4


def pack_mesh(md):
    arrays = {'positions': md.positions.astype('<f4')}
    if md.normals is not None:
        arrays['normals'] = md.normals.astype('<f4')
    if md.tangents is not None:
        arrays['tangents'] = md.tangents.astype('<f4')
    for ch, uv in sorted(md.uvs.items()):
        arrays[f'uv{ch}'] = uv.astype('<f4')
    if md.colors is not None:
        arrays['colors'] = md.colors.astype('<f4')
    if md.bone_indices is not None:
        arrays['bone_indices'] = md.bone_indices.astype('<u2')
        arrays['bone_weights'] = md.bone_weights.astype('<f4')
    if md.bind_poses is not None:
        arrays['bind_poses'] = md.bind_poses.astype('<f4')
    for i, sm in enumerate(md.submeshes):
        arrays[f'indices{i}'] = sm['indices'].astype('<u4')
    blob, entries = bytearray(), []
    for name, a in arrays.items():
        a = np.ascontiguousarray(a)
        entries.append({'name': name, 'dtype': a.dtype.str, 'shape': list(a.shape), 'offset': len(blob), 'nbytes': a.nbytes})
        blob += a.tobytes()
        blob += b'\x00' * _pad(len(blob))
    header = {'version': 1, 'name': md.name, 'vertex_count': md.vertex_count, 'arrays': entries,
              'submeshes': [{'topology': sm['topology'], 'first_vertex': sm['first_vertex'],
                             'vertex_count': sm['vertex_count'], 'index_array': f'indices{i}'}
                            for i, sm in enumerate(md.submeshes)],
              'bone_name_hashes': md.bone_name_hashes, 'root_bone_name_hash': md.root_bone_name_hash,
              'bounds_center': list(md.bounds_center), 'bounds_extent': list(md.bounds_extent),
              'blend_shapes': md.blend_shapes, 'source': md.source}
    hb = json.dumps(header, sort_keys=True, separators=(',', ':')).encode('utf-8')
    hb += b' ' * _pad(len(hb) + 8)
    return MAGIC + struct.pack('<I', len(hb)) + hb + bytes(blob)


def unpack_mesh(raw):
    """-> (header dict, {array_name: np.ndarray})"""
    if raw[:4] != MAGIC:
        raise ValueError('not an sbmesh file')
    hl, = struct.unpack('<I', raw[4:8])
    header = json.loads(raw[8:8 + hl].decode('utf-8'))
    base = 8 + hl
    arrays = {}
    for e in header['arrays']:
        a = np.frombuffer(raw, dtype=np.dtype(e['dtype']), count=int(np.prod(e['shape'])), offset=base + e['offset'])
        arrays[e['name']] = a.reshape(e['shape'])
    return header, arrays


def sha16(data):
    return hashlib.sha256(data).hexdigest()[:16]


def repack(header, arrays, header_updates=None):
    """Re-serialise an unpacked mesh (same layout rules as pack_mesh)."""
    blob, entries = bytearray(), []
    for name, a in arrays.items():
        a = np.ascontiguousarray(a)
        entries.append({'name': name, 'dtype': a.dtype.str, 'shape': list(a.shape), 'offset': len(blob), 'nbytes': a.nbytes})
        blob += a.tobytes()
        blob += b'\x00' * _pad(len(blob))
    h = dict(header)
    h['arrays'] = entries
    h.update(header_updates or {})
    hb = json.dumps(h, sort_keys=True, separators=(',', ':')).encode('utf-8')
    hb += b' ' * _pad(len(hb) + 8)
    return MAGIC + struct.pack('<I', len(hb)) + hb + bytes(blob)


def remap_uv0_from_uv1(raw):
    """Copy of a mesh whose uv0 is the old uv1 (unique atlas layout); the old uv0 (lighting UV) is kept as uv3."""
    header, arrays = unpack_mesh(raw)
    if 'uv1' not in arrays:
        return None
    new = dict(arrays)
    if 'uv0' in arrays:
        new['uv3'] = arrays['uv0']
    new['uv0'] = arrays['uv1']
    return repack(header, new, {'uv_remap': {'uv0': 'from uv1', 'uv3': 'from uv0 (lighting UV)'}})
