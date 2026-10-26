"""Extract the genuine unpacked Ringtail Sprite, including its original tight mesh.

Contract: extract_ringtail_icon(data_dir, fresh_output_dir) creates only a NEW
ignored Lab directory and returns {'icon': descriptor, 'files': hash mapping}.
Merge that fragment into a new converter-v4 cache-manifest (canonical_id remains
slime.ringtail) and copy the named file unchanged. Never rewrite canonical caches.
The full 1024 texture is deliberate: Sprite.image is only a rounded tight crop.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import stat
from pathlib import Path

SPRITE_ID = 3052
APPEARANCE_ID = 31303
SPRITE_NAME = 'iconSlimeRingtail'
FILE_NAME = 'iconSlimeRingtail.source.png'
REPO_ROOT = Path(__file__).resolve().parents[2]


def build_icon_descriptor(tree, texture_size, vertices, triangles):
    """Backwards-compatible verified Ringtail-only descriptor contract."""
    if tree.get('m_Name') != SPRITE_NAME:
        raise ValueError('unexpected Ringtail source Sprite framing')
    return build_source_icon_descriptor(tree, texture_size, vertices, triangles,
                                        sprite_id=SPRITE_ID, appearance_id=APPEARANCE_ID)


def build_source_icon_descriptor(tree, texture_size, vertices, triangles, *, sprite_id, appearance_id):
    """Keep exact source framing/mesh; bounded unpacked 1024 Sprite contract."""
    import re
    from bridge.content.sr2_appearance import source_ref
    for pid in (sprite_id, appearance_id):
        if type(pid) is not int or pid <= 0:
            raise ValueError('source Sprite/appearance identifiers must be positive integers')
    name = tree['m_Name']
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,127}', name):
        raise ValueError('unsupported source Sprite name')
    def numbers(value):
        if not isinstance(value, dict) or any(type(n) not in (int, float) or not math.isfinite(n) for n in value.values()):
            raise ValueError('source Sprite numeric metadata malformed')
    rd = tree['m_RD']
    for vector in (tree['m_Rect'], tree['m_Pivot'], tree['m_Border'], rd['textureRect'],
                   rd['textureRectOffset'], rd['uvTransform']):
        numbers(vector)
    if type(rd['settingsRaw']) is not int:
        raise ValueError('source Sprite settings must be an integer')
    source_ref(rd['texture'])
    source_ref(rd['alphaTexture'])
    if not source_ref(rd['texture']):
        raise ValueError('source Sprite texture is null')
    crop = [rd['textureRect'][k] for k in ('x', 'y', 'width', 'height')]
    offset = [rd['textureRectOffset'][k] for k in ('x', 'y')]
    if (crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0
            or crop[0] + crop[2] > 1024.01 or crop[1] + crop[3] > 1024.01
            or offset != crop[:2]):
        raise ValueError('source Sprite tight rect invalid')
    rect = [tree['m_Rect'][k] for k in ('x', 'y', 'width', 'height')]
    if rect != [0, 0, 1024, 1024] or tuple(texture_size) != (1024, 1024):
        raise ValueError('unexpected Ringtail source Sprite framing')
    if tree['m_Pivot'] != dict(x=.5, y=.5) or tree['m_PixelsToUnits'] != 100:
        raise ValueError('unexpected Ringtail source pivot/units')
    if tree.get('m_AtlasTags') or tree.get('m_SpriteAtlas', {}).get('m_PathID', 0):
        raise ValueError('atlas-packed source Sprite unsupported')
    if rd['settingsRaw'] != 64 or rd.get('downscaleMultiplier', 1) != 1:
        raise ValueError('packed/rotated/downscaled source Sprite unsupported')
    if rd['alphaTexture']['m_PathID'] or rd['texture']['m_FileID']:
        raise ValueError('external/separate-alpha Sprite unsupported')
    if rd['uvTransform'] != dict(x=100, y=512, z=100, w=512):
        raise ValueError('source UV transform does not match logical rect')
    if any(tree['m_Border'].values()):
        raise ValueError('bordered Sprite unsupported')
    if not vertices or len(vertices) < 3 or len(vertices) > 4096 or not triangles or len(triangles) > 24576 or len(triangles) % 3:
        raise ValueError('source Sprite geometry budget/shape invalid')
    if any(len(v) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 5.13 for n in v) for v in vertices):
        raise ValueError('invalid Sprite vertex')
    if any(type(i) is not int or i < 0 or i >= len(vertices) for i in triangles):
        raise ValueError('invalid Sprite triangle')
    return {
        'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': True,
        'file': name + '.source.png', 'texture_size': list(texture_size), 'rect': rect,
        'pivot': [tree['m_Pivot'][k] for k in ('x', 'y')],
        'pixels_per_unit': tree['m_PixelsToUnits'],
        'vertices': vertices, 'triangles': triangles,
        'source_sprite_name': name, 'source_sprite_path_id': sprite_id,
        'source_appearance_path_id': appearance_id,
        'source_texture_path_id': rd['texture']['m_PathID'],
        'source_texture_rect': [rd['textureRect'][k] for k in ('x', 'y', 'width', 'height')],
        'source_texture_rect_offset': [rd['textureRectOffset'][k] for k in ('x', 'y')],
        'source_settings_raw': rd['settingsRaw'], 'packing_rotation': 0,
        'source_uv_transform': [rd['uvTransform'][k] for k in ('x', 'y', 'z', 'w')],
        'pixel_encoding': 'source-texture-rgba-unresampled',
    }


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fresh_private_output(output_dir):
    output = Path(output_dir).absolute()
    private_roots = [REPO_ROOT / 'Lab' / 'AssetCache', REPO_ROOT / 'Lab' / 'RuntimeArtifacts']
    if ('..' in output.parts or 'sr2-to-sr1' in output.parts
            or not any(output.is_relative_to(root) and output != root for root in private_roots)):
        raise ValueError('output must be a fresh private Lab cache/artifact directory, never canonical cache')
    for part in (output, *output.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('output reparse point refused')
    if output.exists():
        raise ValueError('output directory already exists; choose a unique new directory')
    output.mkdir(parents=True, exist_ok=False)
    return output


_ICON_CACHE: dict = {}


def parent_icon_image(data_dir, species):
    """The species' own icon (Sprite `iconSlime<Species>`, from resources.assets or the slime-icons bundle) as an RGBA image, or None."""
    key = (str(data_dir), species)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    import UnityPy
    image = None
    sources = [Path(data_dir) / 'resources.assets']
    sources += sorted((Path(data_dir) / 'StreamingAssets' / 'aa' / 'StandaloneWindows64').glob('*slimeicons*.bundle'))
    for source in sources:
        if not source.is_file():
            continue
        env = UnityPy.load(str(source))
        for obj in env.objects:
            if obj.type.name != 'Sprite':
                continue
            sprite = obj.read()
            if sprite.m_Name == 'iconSlime' + species:
                try:
                    image = sprite.m_RD.texture.read().image.convert('RGBA')
                except Exception:  # noqa: BLE001 - a sprite whose texture lives elsewhere is simply not available
                    image = None
                break
        if image is not None:
            break
    if image is None:
        image = full_art_icon(data_dir, species)      # SR2 1.x: one full-art bundle per icon
    _ICON_CACHE[key] = image
    return image


def export_largo_icon(assets, resolved, output_dir):
    """A Largo has no icon of its own in SR2 (the game composes one from its parents): both parents' source Sprites are drawn
    overlapped on one 1024 canvas and described as a full-rect Sprite the plugin loads like any other source icon."""
    from PIL import Image
    from bridge.content.sr2_asset_extract import refuse_existing_leaf
    names = list(resolved.get('parent_names') or [])
    if len(names) != 2:
        raise ValueError('a largo icon needs both parents')
    images = [parent_icon_image(assets.data_dir, n) for n in names]
    if not any(images):
        raise ValueError('neither parent has a source icon Sprite')
    canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    if all(images):
        for image, origin in ((images[0], (40, 250)), (images[1], (284, 70))):
            canvas.alpha_composite(image.resize((700, 700), Image.LANCZOS), origin)
    else:                                      # one parent has no icon in the game files (Mosaic, Quantum): show the other alone
        only = images[0] or images[1]
        canvas.alpha_composite(only.resize((860, 860), Image.LANCZOS), (82, 82))
    name = 'iconLargo_' + str(resolved.get('largo_name') or 'pair')
    file = name + '.source.png'
    target = Path(output_dir) / file
    refuse_existing_leaf(target)
    canvas.save(target)
    half = 5.12
    return {
        'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': False,
        'file': file, 'texture_size': [1024, 1024], 'rect': [0, 0, 1024, 1024], 'pivot': [0.5, 0.5], 'pixels_per_unit': 100,
        'vertices': [[-half, -half], [half, -half], [half, half], [-half, half]], 'triangles': [0, 1, 2, 0, 2, 3],
        'source_sprite_name': name, 'source_sprite_path_id': 0, 'source_appearance_path_id': resolved['appearance_id'],
        'source_texture_path_id': 0, 'source_texture_rect': [0, 0, 1024, 1024], 'source_texture_rect_offset': [0, 0],
        'source_settings_raw': 64, 'packing_rotation': 0, 'source_uv_transform': [100, 512, 100, 512],
        'pixel_encoding': 'source-texture-rgba-unresampled', 'composed_from_parent_sprites': names,
    }


def full_art_icon(data_dir, species):
    """The species' full-art icon (SR2 1.x: one ``fullarticons_..._iconslime<species>.png_<hash>.bundle`` each), or None."""
    import UnityPy
    folder = Path(data_dir) / 'StreamingAssets' / 'aa' / 'StandaloneWindows64'
    wanted = 'iconslime' + species.lower() + '.png_'
    for bundle in sorted(folder.glob('fullarticons_*.bundle')):
        if wanted not in bundle.name.lower():
            continue
        env = UnityPy.load(str(bundle))
        for obj in env.objects:
            if obj.type.name == 'Texture2D':
                try:
                    return obj.read().image.convert('RGBA')
                except Exception:  # noqa: BLE001 - an undecodable texture is simply not usable
                    return None
    return None


def atlas_icon(assets, tree, atlas, sprite_id, appearance_id, output_dir):
    """Plain 1024 square icon for an atlas-packed SR2 1.x sprite (full art first, else the sprite frame from the atlas)."""
    from PIL import Image
    from bridge.content.sr2_asset_extract import refuse_existing_leaf
    name = str(tree['m_Name'])
    species = name[len('iconSlime'):] if name.startswith('iconSlime') else name
    species = species[:-len('_small')] if species.endswith('_small') else species
    art = full_art_icon(assets.data_dir, species)
    source = 'full-art'
    if art is None:
        rect = tree['m_Rect']
        x, y, w, h = (float(rect[k]) for k in ('x', 'y', 'width', 'height'))
        height = atlas.size[1]
        art = atlas.crop((int(round(x)), int(round(height - y - h)), int(round(x + w)), int(round(height - y))))
        source = 'atlas-frame'
    art.thumbnail((860, 860), Image.LANCZOS)
    if max(art.size) < 860:
        scale = 860 / max(art.size)
        art = art.resize((max(1, round(art.size[0] * scale)), max(1, round(art.size[1] * scale))), Image.LANCZOS)
    canvas = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    canvas.alpha_composite(art, ((1024 - art.size[0]) // 2, (1024 - art.size[1]) // 2))
    file = 'iconSlime' + species + '.source.png'
    target = Path(output_dir) / file
    refuse_existing_leaf(target)
    canvas.save(target)
    half = 5.12
    return {
        'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': False,
        'file': file, 'texture_size': [1024, 1024], 'rect': [0, 0, 1024, 1024], 'pivot': [0.5, 0.5], 'pixels_per_unit': 100,
        'vertices': [[-half, -half], [half, -half], [half, half], [-half, half]], 'triangles': [0, 1, 2, 0, 2, 3],
        'source_sprite_name': name, 'source_sprite_path_id': sprite_id, 'source_appearance_path_id': appearance_id,
        'source_texture_path_id': 0, 'source_texture_rect': [0, 0, 1024, 1024], 'source_texture_rect_offset': [0, 0],
        'source_settings_raw': 64, 'packing_rotation': 0, 'source_uv_transform': [100, 512, 100, 512],
        'pixel_encoding': 'composed-square-icon', 'icon_source': source,
    }


def export_source_icon(assets, resolved, output_dir):
    """Write the exact appearance-referenced Sprite texture in an already fresh cache."""
    if resolved.get('is_largo') and not resolved.get('sprite_id'):
        return export_largo_icon(assets, resolved, output_dir)
    from UnityPy.helpers.MeshHelper import MeshHandler
    from bridge.content.sr2_appearance import source_object_index, source_ref
    from bridge.content.sr2_asset_extract import refuse_existing_leaf
    objects = source_object_index(assets)
    try:
        appearance_id = resolved['appearance_id']
        sprite_id = source_ref(objects[appearance_id].read_typetree()['_icon'])
        if sprite_id != resolved['sprite_id']:
            raise ValueError('source icon does not match the applied appearance reference')
        sprite = objects[sprite_id]
        if sprite.type.name != 'Sprite':
            raise ValueError('source icon is not a Sprite')
        tree = sprite.read_typetree()
        texture_id = source_ref(tree['m_RD']['texture'])
        texture = objects[texture_id]
        if texture.type.name != 'Texture2D':
            raise ValueError('source Sprite texture is not a Texture2D')
        image = texture.read().image.convert('RGBA')
        data = sprite.read()
        mesh = MeshHandler(data.m_RD, sprite.version)
        mesh.process()
        vertices = [[float(v[0]), float(v[1])] for v in mesh.m_Vertices]
        triangles = [int(i) for group in mesh.get_triangles() for tri in group for i in tri]
        try:
            descriptor = build_source_icon_descriptor(tree, image.size, vertices, triangles,
                sprite_id=sprite_id, appearance_id=appearance_id)
        except ValueError:
            # SR2 1.x: the appearance icon is a 128 px sprite packed in a shared atlas. Use the species' full-art icon
            # (its own bundle) or, failing that, the sprite's frame cut out of the atlas, as a plain square icon.
            return atlas_icon(assets, tree, image, sprite_id, appearance_id, output_dir)
        descriptor['source_assets_sha256'] = _hash(assets.data_dir / 'resources.assets')
        target = Path(output_dir) / descriptor['file']
        refuse_existing_leaf(target)
        image.save(target)
    except (KeyError, IndexError, TypeError, AttributeError) as ex:
        raise ValueError('source Sprite metadata incomplete or malformed') from ex
    return descriptor


def extract_ringtail_icon(data_dir, output_dir):
    import UnityPy
    from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator

    data_dir = Path(data_dir)
    source = data_dir / 'resources.assets'
    env = UnityPy.load(str(source))
    objects = {o.path_id: o for o in env.objects if o.assets_file.name == 'resources.assets'}
    sprite = objects[SPRITE_ID]
    if sprite.type.name != 'Sprite':
        raise ValueError('source object is not a Sprite')
    generator = TypeTreeGenerator(sprite.assets_file.unity_version)
    dlls = data_dir.parent / 'MelonLoader/Dependencies/Il2CppAssemblyGenerator/Cpp2IL/cpp2il_out'
    generator.load_local_dll_folder(str(dlls))
    env.typetree_generator = generator
    appearance = objects[APPEARANCE_ID].read_typetree()
    icon = appearance.get('_icon')
    if icon != {'m_FileID': 0, 'm_PathID': SPRITE_ID}:
        raise ValueError('Ringtail appearance Icon does not resolve to verified source Sprite')
    from types import SimpleNamespace
    if sprite.read_typetree()['m_Name'] != SPRITE_NAME:
        raise ValueError('unexpected Ringtail source Sprite name')
    output = _fresh_private_output(output_dir)
    descriptor = export_source_icon(SimpleNamespace(data_dir=data_dir, _objects=list(objects.values())),
        {'appearance_id': APPEARANCE_ID, 'sprite_id': SPRITE_ID}, output)
    fragment = {'icon': descriptor, 'files': {FILE_NAME: _hash(output / FILE_NAME)}}
    (output / 'icon-manifest-fragment.json').write_text(json.dumps(fragment, indent=2) + '\n', encoding='utf-8')
    return fragment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sr2-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    fragment = extract_ringtail_icon(args.sr2_dir, args.out)
    print(json.dumps({'out': str(args.out), 'file': fragment['icon']['file'],
                      'sha256': fragment['files'][FILE_NAME], 'texture_size': fragment['icon']['texture_size'],
                      'vertices': len(fragment['icon']['vertices']), 'indices': len(fragment['icon']['triangles'])}))


if __name__ == '__main__':
    main()
