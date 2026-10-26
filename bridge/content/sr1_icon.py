"""Unpacked SR1 Sprite contract, same portable descriptor as SR2; no art bake."""
from __future__ import annotations

import math
import re


def build_icon_descriptor(tree, texture_size, vertices, triangles, *, sprite_id, appearance_id):
    def vec(mapping, keys):
        values = [mapping[k] for k in keys]
        if any(type(n) not in (int, float) or not math.isfinite(n) for n in values):
            raise ValueError('malformed source icon number')
        return values

    def pointer(mapping):
        if (type(mapping.get('m_FileID')) is not int or type(mapping.get('m_PathID')) is not int
                or mapping['m_FileID'] < 0 or mapping['m_PathID'] < 0):
            raise ValueError('malformed source icon reference')
        return mapping['m_PathID']

    try:
        if any(type(i) is not int or i <= 0 for i in (sprite_id, appearance_id)):
            raise ValueError('invalid source icon identity')
        if len(texture_size) != 2 or any(type(i) is not int or not 1 <= i <= 8192 for i in texture_size):
            raise ValueError('source icon texture size invalid')
        width, height = texture_size
        name = tree['m_Name']
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', name):
            raise ValueError('unsafe source icon name')
        rd = tree['m_RD']
        if (tree['m_AtlasTags'] or pointer(tree['m_SpriteAtlas']) or rd['settingsRaw'] != 64
                or type(rd['settingsRaw']) is not int or pointer(rd['alphaTexture'])
                or rd.get('downscaleMultiplier', 1) != 1 or not pointer(rd['texture'])):
            raise ValueError('packed/rotated/separate-alpha source icon unsupported')
        rect = vec(tree['m_Rect'], ('x', 'y', 'width', 'height'))
        pivot = vec(tree['m_Pivot'], 'xy')
        border = vec(tree['m_Border'], 'xyzw')
        if rect != [0, 0, width, height] or pivot != [.5, .5] or any(border):
            raise ValueError('non-full-texture/bordered/unexpected-pivot source icon unsupported')
        ppu = tree['m_PixelsToUnits']
        if type(ppu) not in (int, float) or not math.isfinite(ppu) or ppu <= 0:
            raise ValueError('source icon units invalid')
        crop = vec(rd['textureRect'], ('x', 'y', 'width', 'height'))
        offset = vec(rd['textureRectOffset'], 'xy')
        if (crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0
                or crop[0]+crop[2] > width+.01 or crop[1]+crop[3] > height+.01
                or offset != crop[:2]):
            raise ValueError('source icon tight rectangle invalid')
        uv = vec(rd['uvTransform'], 'xyzw')
        if uv != [ppu, width/2, ppu, height/2]:
            raise ValueError('source icon UV transform incompatible with framing')
        if not 3 <= len(vertices) <= 4096 or not triangles or len(triangles) > 24576 or len(triangles) % 3:
            raise ValueError('source icon geometry budget invalid')
        for row in vertices:
            if len(row) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) for n in row):
                raise ValueError('source icon vertex invalid')
            if abs(row[0]) > width/(2*ppu)+.01 or abs(row[1]) > height/(2*ppu)+.01:
                raise ValueError('source icon vertex outside logical rectangle')
        if any(type(i) is not int or not 0 <= i < len(vertices) for i in triangles):
            raise ValueError('source icon triangle invalid')
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError('incomplete or malformed source icon metadata') from exc
    return {'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': True,
            'file': name + '.source.png', 'texture_size': list(texture_size), 'rect': rect,
            'pivot': pivot, 'pixels_per_unit': ppu, 'vertices': vertices, 'triangles': triangles,
            'source_sprite_name': name, 'source_sprite_path_id': sprite_id,
            'source_appearance_path_id': appearance_id, 'source_texture_path_id': rd['texture']['m_PathID'],
            'source_texture_rect': crop, 'source_texture_rect_offset': offset,
            'source_settings_raw': rd['settingsRaw'], 'packing_rotation': 0,
            'source_uv_transform': uv, 'pixel_encoding': 'source-texture-rgba-unresampled'}
