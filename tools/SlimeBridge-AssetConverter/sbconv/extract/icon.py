"""Species icon (UI Sprite) extraction in the exact ``bridge-source-sprite`` v1 contract SlimeBridge's loaders read
(unpacked full-texture sprite + its tight mesh). Failures are reported, never papered over."""

import io
import math
import re

from .prefab import ref_key


def _vec(mapping, keys):
    values = [mapping[k] for k in keys]
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in values):
        raise ValueError('malformed source icon number')
    return values


def _pointer(mapping):
    if type(mapping.get('m_FileID')) is not int or type(mapping.get('m_PathID')) is not int:
        raise ValueError('malformed source icon reference')
    return mapping['m_PathID']


def build_descriptor(tree, texture_size, vertices, triangles, sprite_id, appearance_id, texture_id):
    try:
        width, height = texture_size
        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise ValueError('source icon texture size invalid')
        name = tree['m_Name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', name):
            raise ValueError('unsafe source icon name')
        rd = tree['m_RD']
        if (tree['m_AtlasTags'] or _pointer(tree['m_SpriteAtlas']) or rd['settingsRaw'] != 64 or _pointer(rd['alphaTexture'])
                or rd.get('downscaleMultiplier', 1) != 1):
            raise ValueError('packed/rotated/separate-alpha source icon unsupported')
        rect = _vec(tree['m_Rect'], ('x', 'y', 'width', 'height'))
        pivot = _vec(tree['m_Pivot'], 'xy')
        border = _vec(tree['m_Border'], 'xyzw')
        if rect != [0, 0, width, height] or pivot != [.5, .5] or any(border):
            raise ValueError('non-full-texture/bordered/unexpected-pivot source icon unsupported')
        ppu = tree['m_PixelsToUnits']
        if type(ppu) not in (int, float) or not math.isfinite(ppu) or ppu <= 0:
            raise ValueError('source icon units invalid')
        crop = _vec(rd['textureRect'], ('x', 'y', 'width', 'height'))
        offset = _vec(rd['textureRectOffset'], 'xy')
        if (crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0 or crop[0] + crop[2] > width + .01
                or crop[1] + crop[3] > height + .01 or offset != crop[:2]):
            raise ValueError('source icon tight rectangle invalid')
        uv = _vec(rd['uvTransform'], 'xyzw')
        if uv != [ppu, width / 2, ppu, height / 2]:
            raise ValueError('source icon UV transform incompatible with framing')
        if not 3 <= len(vertices) <= 4096 or not triangles or len(triangles) > 24576 or len(triangles) % 3:
            raise ValueError('source icon geometry budget invalid')
        for row in vertices:
            if len(row) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in row):
                raise ValueError('source icon vertex invalid')
            if abs(row[0]) > width / (2 * ppu) + .01 or abs(row[1]) > height / (2 * ppu) + .01:
                raise ValueError('source icon vertex outside logical rectangle')
        if any(type(i) is not int or not 0 <= i < len(vertices) for i in triangles):
            raise ValueError('source icon triangle invalid')
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('incomplete or malformed source icon metadata') from exc
    pos = lambda v: abs(int(v)) or 1          # Addressables ids may be negative 64-bit; the contract wants positive integers
    return {'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': True, 'file': name + '.source.png',
            'texture_size': [width, height], 'rect': rect, 'pivot': pivot, 'pixels_per_unit': ppu, 'vertices': vertices,
            'triangles': triangles, 'source_sprite_name': name, 'source_sprite_path_id': pos(sprite_id),
            'source_appearance_path_id': pos(appearance_id), 'source_texture_path_id': pos(texture_id),
            'source_texture_rect': crop, 'source_texture_rect_offset': offset, 'source_settings_raw': rd['settingsRaw'],
            'packing_rotation': 0, 'source_uv_transform': uv, 'pixel_encoding': 'source-texture-rgba-unresampled'}


def extract_icon(session, sprite_reader, appearance_reader):
    """-> (png_bytes, descriptor) or raises ValueError with the precise reason."""
    from UnityPy.helpers.MeshHelper import MeshHandler
    if sprite_reader is None or sprite_reader.type.name != 'Sprite':
        raise ValueError('appearance has no Sprite icon')
    tree = sprite_reader.read_typetree()
    tex_rd = session.resolve_pptr(sprite_reader.assets_file, tree['m_RD']['texture'])
    if tex_rd is None or tex_rd.type.name != 'Texture2D':
        raise ValueError('icon texture not found')
    image = tex_rd.read().image
    if image is None:
        raise ValueError('icon texture could not be decoded')
    image = image.convert('RGBA')
    data = sprite_reader.read()
    mesh = MeshHandler(data.m_RD, sprite_reader.version)
    mesh.process()
    vertices = [[float(v[0]), float(v[1])] for v in mesh.m_Vertices]
    triangles = [int(i) for group in mesh.get_triangles() for tri in group for i in tri]
    desc = build_descriptor(tree, image.size, vertices, triangles, sprite_reader.path_id,
                            appearance_reader.path_id if appearance_reader is not None else 1, tex_rd.path_id)
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    desc['source_ref'] = ref_key(sprite_reader)
    return buf.getvalue(), desc
