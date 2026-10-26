"""Offline SR2 native-asset extraction (UnityPy) for the SlimeBridge asset pipeline.

Reads the locally installed Slime Rancher 2 Unity data files directly (no game launch, no manual
export) and produces the portable converted-model cache consumed by the destination-game runtime
loaders: mesh geometry (body/face/ears/tail parts, decoded from the actual `Mesh` objects), the
prefab's skeleton references, materials with their real color palettes and display textures
(decoded to PNG), all hash-stamped in a cache manifest.

This is the PRIMARY extraction path; the in-game runtime dump remains a targeted fallback for
data that only exists at runtime. Nothing here ships in the repository: outputs land in the
private ignored `Lab/AssetCache/` tree. Distribution model: every player's own installation is
the asset source, so no proprietary payload is ever redistributed.

Usage:
  py -3.12 -m bridge.content.sr2_asset_extract --species Ringtail --canonical slime.ringtail \
      [--sr2-dir <SlimeRancher2_Data>] [--out <cache dir>]

Opt-in appearance mesh scope (skinned body/face + bone-attached static parts; never weakens
the strict complete-source contract and never writes the canonical cache):
  py -3.12 -m bridge.content.sr2_asset_extract --species <Species> --canonical slime.<species> \
      --appearance-mesh-only --out <fresh cache dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import sys
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SR2_DATA = REPO_ROOT / 'Lab' / 'Games' / 'SR2' / 'SlimeRancher2_Data'
CACHE_ROOT = REPO_ROOT / 'Lab' / 'AssetCache' / 'sr2-to-sr1'
UNPACK_ROOT = REPO_ROOT / 'Lab' / 'AssetCache' / 'unpacked'      # scratch: bundles unpacked to loose files (safe to delete)

SCHEMA = 'bridge-native-mesh'
SCHEMA_VERSION = 2
CACHE_SCHEMA = 'bridge-asset-cache-manifest'
CACHE_SCHEMA_VERSION = 1
CONVERTER_VERSION = 5

SKIP_TEXTURES = {'quantumStatic', 'noise_pixel', 'dptNoiseClouds', 'WorleyNoise32RGB'}
# SR2 1.x shader inputs that are 3D noise volumes (snow/weather breakup): not displayable by the SR1 Standard shader.
SKIP_TEXTURE_PROPERTIES = {'_SnowNoiseBreakup3d'}
# SR2 FX shaders without an SR1 counterpart, drawn through the native slime body material with an authored look
# (the destination re-skins slime-family parts with its own body shader and these colours):
# the Hyper slime's sunglasses are near-black glossy glass with a warm tint.
DISPLAY_SUBSTITUTES = {
    'SR/AMP/Slimes/FX/Hyper Slime Shades': {
        'shader': 'SR/AMP/Slime/Body', 'textures': [],
        'colors': {'_TopColor': [0.17, 0.11, 0.07, 1.0], '_MiddleColor': [0.08, 0.05, 0.035, 1.0],
                   '_BottomColor': [0.035, 0.022, 0.016, 1.0], '_GlossRGB': [1.6, 1.6, 1.6, 1.0],
                   '_GlossPowerRGB': [2.5, 2.5, 2.5, 1.0]},
        # the body family requires its three lighting controls (same as the Hyper body: plain lit, no UV lighting)
        'floats': {'_BodyLightingContribution': 1.0, '_LightingUVContribution': 0.0, '_LightingUVHorizontalAdjust': 0.0}},
}


# Unity TextureFormat -> (numpy dtype, channels) for the float formats UnityPy hands to Pillow without a decoder.
FLOAT_TEXTURE_FORMATS = {15: ('<f2', 1), 16: ('<f2', 2), 17: ('<f2', 4), 18: ('<f4', 1), 19: ('<f4', 2), 20: ('<f4', 4)}


def float_texture_image(data):
    """8-bit RGBA image of an uncompressed half/float texture (values clamped to 0..1, Unity's bottom-up rows flipped)."""
    import numpy as np
    from PIL import Image
    layout = FLOAT_TEXTURE_FORMATS.get(int(getattr(data, 'm_TextureFormat', -1)))
    if layout is None:
        return None
    dtype, channels = layout
    width, height = int(data.m_Width), int(data.m_Height)
    raw = bytes(data.image_data)
    count = width * height * channels
    values = np.frombuffer(raw, dtype=dtype, count=count).astype(np.float32).reshape(height, width, channels)
    rgba = np.ones((height, width, 4), dtype=np.float32)
    rgba[..., :channels] = values
    if channels == 1:
        rgba[..., 1] = rgba[..., 2] = rgba[..., 0]
    pixels = (np.clip(np.nan_to_num(rgba), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return Image.fromarray(pixels[::-1].copy(), 'RGBA')


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


_CANONICAL_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]*$')


def validate_canonical(canonical: str) -> str:
    """Canonical ids are flat tokens: no traversal, separators, or leading dots."""
    if (not canonical or not _CANONICAL_RE.match(canonical) or '..' in canonical
            or '/' in canonical or '\\' in canonical):
        raise ValueError(f'invalid canonical id: {canonical!r}')
    return canonical


_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def refuse_existing_leaf(path: Path) -> None:
    """Reject a destination leaf that already exists as a symlink/reparse point before writing
    through it (a pre-existing symlink could redirect a write outside the cache)."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f'destination leaf is a link/reparse point: {path}')


def refuse_reparse_chain(path: Path, stop_root: Path) -> None:
    """Reject any symlink/reparse component on the existing chain from path up to stop_root."""
    stop = stop_root.resolve()
    current = path
    for _ in range(64):
        try:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f'path chain contains a link/reparse point: {current}')
        except FileNotFoundError:
            pass
        if current.resolve() == stop:
            return
        parent = current.parent
        if parent == current:
            return
        current = parent


def _fresh_external_output(output_dir: Path) -> Path:
    """Fresh scratch output for --allow-nonlab appearance mesh-only runs.

    The canonical cache tree is never a target (refused up front), the directory must not
    exist yet, and no link/reparse component may sit on its chain, so a symlinked scratch
    path cannot redirect the write elsewhere.
    """
    if 'sr2-to-sr1' in output_dir.parts:
        raise ValueError('appearance mesh-only output may not target the canonical cache tree')
    refuse_reparse_chain(output_dir, Path(output_dir.anchor))
    if output_dir.exists():
        raise ValueError(f'appearance mesh-only output already exists: {output_dir}')
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


def _f(value) -> float:
    return float(value)


def _v3(v) -> list:
    try:
        return [round(_f(v.x), 6), round(_f(v.y), 6), round(_f(v.z), 6)]
    except AttributeError:
        v = list(v)
        return [round(_f(v[0]), 6), round(_f(v[1]), 6), round(_f(v[2]), 6)]


def _v2(v) -> list:
    try:
        return [round(_f(v.x), 6), round(_f(v.y), 6)]
    except AttributeError:
        v = list(v)
        return [round(_f(v[0]), 6), round(_f(v[1]), 6)]


class Sr2Assets:
    """Lazily loaded view over the SR2 data files."""

    def __init__(self, data_dir: Path, bundle: str | None = None, extras: tuple = ('shaders_assets',)):
        import UnityPy  # local import keeps module import light for tests

        self.data_dir = data_dir
        self.bundle = bundle
        self.extras = tuple(extras)
        resources = data_dir / 'resources.assets'
        if bundle:
            # Some species (and every Largo of the later SR2 updates) live in an addressables bundle instead of resources.assets
            # The requested bundle plus every other asset bundle (shaders, textures, meshes its objects point to) share one environment
            # and one object index: addressables path ids are unique 64-bit numbers, so a pointer's file number can be ignored
            # (sr2_appearance.ALLOW_EXTERNAL_REFS). The script bundle is loaded for class names but never indexed (its ids repeat).
            from bridge.content import sr2_appearance
            from bridge.content.bundle_unpack import unpack_bundle
            folder = data_dir / 'StreamingAssets' / 'aa' / 'StandaloneWindows64'
            matches = sorted(folder.glob(bundle + '*.bundle'))
            if not matches:
                raise ValueError(f'SR2 bundle {bundle}* not found')
            scratch = UNPACK_ROOT
            # Bundles are unpacked to loose files first (constant memory, see bundle_unpack): UnityPy then streams them from disk instead
            # of holding the decompressed archive and a second copy of every node in RAM.
            def serialized(bundle_path):
                files = unpack_bundle(bundle_path, scratch)
                return [f for f in files if '.' not in f.name]
            self.env = UnityPy.load(str(serialized(matches[0])[0]))
            primary = list(self.env.objects)
            seen = {o.path_id for o in primary}
            scripts = sorted(folder.glob('623ac1c2e88e30c73405332f5f920934_monoscripts_*.bundle'))
            if scripts:
                for cab in serialized(scripts[0]):
                    self.env.load_file(str(cab))
            for extra in sorted(folder.glob('*.bundle')):
                name = extra.name.lower()
                # only the bundles a slime's parts point into: the shared mesh/texture bundle (`duplicateassetisolation_assets`) is opt-in
                if extra == matches[0] or not any(name.startswith(k) for k in extras):
                    continue
                for cab in serialized(extra):
                    loaded = self.env.load_file(str(cab))
                    for o in (getattr(loaded, 'objects', None) or {}).values():
                        if o.path_id not in seen:
                            seen.add(o.path_id)
                            primary.append(o)
            sr2_appearance.ALLOW_EXTERNAL_REFS = True
        else:
            if not resources.is_file():
                raise ValueError(f'SR2 resources.assets not found: {resources}')
            self.env = UnityPy.load(str(resources))
            primary = None
        self._objects = primary if primary is not None else list(self.env.objects)

    def by_name(self, type_name: str, name: str):
        # one pass per object type (first object of a name wins, as the old linear scan did), then dictionary lookups
        names = self.__dict__.setdefault('_name_index', {})
        index = names.get(type_name)
        if index is None:
            index = {}
            for obj in self._objects:
                if obj.type.name != type_name:
                    continue
                found = getattr(obj.read(), 'm_Name', None)
                if found is not None and found not in index:
                    index[found] = obj
            names[type_name] = index
        obj = index.get(name)
        if obj is None:
            return None, None
        return obj, obj.read()

    def all_named(self, type_name: str):
        for obj in self._objects:
            if obj.type.name != type_name:
                continue
            data = obj.read()
            yield obj, data

    def find_objects(self, type_name: str, predicate):
        for obj in self._objects:
            if obj.type.name != type_name:
                continue
            data = obj.read()
            if predicate(data):
                yield obj, data


def decode_mesh(mesh, mesh_obj=None) -> dict:
    """Decode a UnityPy Mesh object into portable geometry arrays."""
    from UnityPy.helpers import MeshHelper

    handler = MeshHelper.MeshHandler(mesh)
    handler.process()
    vertex_count = handler.m_VertexCount

    def vec(entry, size):
        try:
            values = [entry.x, entry.y]
            if size > 2:
                values.append(entry.z)
            if size > 3:
                values.append(entry.w)
        except AttributeError:
            values = list(entry)[:size]
        return [round(_f(value), 6) for value in values]

    vertices = [vec(v, 3) for v in handler.m_Vertices]
    triangles = []
    try:
        indices = handler.m_IndexBuffer
        if indices is not None:
            triangles = [int(i) for i in indices]
    except Exception:
        triangles = []
    submeshes = []
    try:
        for submesh in (mesh.m_SubMeshes or []):
            unit = 2 if getattr(handler, 'm_Use16BitIndices', True) else 4
            start = int(submesh.firstByte) // unit
            count = int(submesh.indexCount)
            submeshes.append([int(i) for i in triangles[start:start + count]])
    except Exception:
        submeshes = []
    if not submeshes or not any(submeshes):
        # Fall back to the flattened buffer when submesh metadata is unavailable.
        submeshes = [triangles]
    # Fail closed: a nonempty vertex buffer without usable indices would publish invisible
    # geometry that still substitutes for the placeholder at runtime.
    vertex_count = len(vertices)
    if not any(submeshes):
        raise ValueError(f'mesh {mesh.m_Name} has no triangle indices')
    for submesh in submeshes:
        if len(submesh) < 3:
            raise ValueError(f'mesh {mesh.m_Name} has an empty or degenerate submesh')
        for index in submesh:
            if index < 0 or index >= vertex_count:
                raise ValueError(f'mesh {mesh.m_Name} has an out-of-range index {index}')
    normals = []
    try:
        normals = [vec(v, 3) for v in handler.m_Normals]
    except Exception:
        normals = []
    uv = []
    try:
        uv = [vec(v, 2) for v in handler.m_UV0]
    except Exception:
        uv = []
    uv2 = []
    try:
        uv2 = [vec(v, 2) for v in handler.m_UV1]
    except Exception:
        uv2 = []
    colors = []
    try:
        colors = [vec(c, 4) for c in handler.m_Colors]
    except Exception:
        colors = []
    tangents = []
    try:
        tangents = [vec(t, 4) for t in handler.m_Tangents]
    except Exception:
        tangents = []
    bind_poses = []
    try:
        tree = mesh_obj.read_typetree() if mesh_obj is not None else mesh.read_typetree()
        for m in tree.get('m_BindPose') or []:
            if isinstance(m, dict):
                bind_poses.append([round(_f(m.get(f'e{r}{c}', m.get(f'm{r}{c}', 0))), 6)
                                   for r in range(4) for c in range(4)])
            else:
                bind_poses.append([round(_f(getattr(m, f'm{r}{c}')), 6) for r in range(4) for c in range(4)])
    except Exception:
        bind_poses = []
    weights = []
    try:
        bw = handler.m_BoneWeights or []
        bi = handler.m_BoneIndices or []
        for index in range(min(len(bw), len(bi))):
            w = bw[index]
            b = bi[index]
            weights.append([round(_f(w[0]), 6), round(_f(w[1]), 6), round(_f(w[2]), 6), round(_f(w[3]), 6),
                            int(b[0]), int(b[1]), int(b[2]), int(b[3])])
    except Exception:
        weights = []
    return {
        'vertex_count': vertex_count,
        'vertices': vertices,
        'normals': normals,
        'tangents': tangents,
        'uv': uv,
        'uv2': uv2,
        'colors': colors,
        'triangles': submeshes,
        'bind_poses': bind_poses,
        'bone_weights': weights,
    }


class TextureExporter:
    def __init__(self, assets: Sr2Assets, out_dir: Path):
        self.assets = assets
        self.out_dir = out_dir
        self.written: list[str] = []
        self._done: dict[str | tuple[str, int], str] = {}

    def export_source(self, obj) -> str:
        """Encode the exact resolved Texture2D, retaining decoded atlas pixels.

        Source path IDs disambiguate identical names; do not route these textures
        through by_name or the body display-texture skip list.
        """
        key = ('source', obj.path_id)
        if key in self._done:
            return self._done[key]
        data = obj.read()
        file_name = ('tex-source-' + str(obj.path_id) + '-'
                     + re.sub(r'[^0-9A-Za-z._-]', '_', data.m_Name) + '.png')
        target = self.out_dir / file_name
        refuse_existing_leaf(target)
        try:
            data.image.save(target)
            payload = target.read_bytes()
        except Exception as ex:
            raise ValueError(f'source face texture {obj.path_id} could not be encoded: {type(ex).__name__}') from ex
        if not payload.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            raise ValueError('source face texture did not produce a valid PNG payload')
        self.written.append(file_name)
        self._done[key] = file_name
        return file_name

    def export(self, texture_name: str) -> str | None:
        if texture_name in SKIP_TEXTURES:
            return None
        if texture_name in self._done:
            return self._done[texture_name]
        _, data = self.assets.by_name('Texture2D', texture_name)
        if data is None:
            return None
        file_name = 'tex-' + re.sub(r'[^0-9A-Za-z._-]', '_', texture_name) + '.png'
        target = self.out_dir / file_name
        refuse_existing_leaf(target)
        try:
            try:
                image = data.image
            except ValueError:
                image = float_texture_image(data)   # SR2 1.x gradient ramps are float textures Pillow has no raw mode for
                if image is None:
                    raise
            image.save(target)
        except Exception as ex:
            raise ValueError(
                f'texture {texture_name} could not be encoded to PNG: {type(ex).__name__}') from ex
        # Validate the produced file is a real PNG before it is published in the cache.
        try:
            payload = target.read_bytes()
        except OSError as ex:
            raise ValueError(f'texture {texture_name} could not be read back: {type(ex).__name__}') from ex
        if not payload.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            raise ValueError(f'texture {texture_name} did not produce a valid PNG payload')
        self.written.append(file_name)
        self._done[texture_name] = file_name
        return file_name


def export_source_face_materials(assets, binding: dict, exporter: TextureExporter) -> list[dict]:
    """Export resolved authored passes, not atlas-name guesses or baked masks.

    Only the three exact native shader roles are portable. Preserve all saved
    colors/floats, including legacy HDRP metadata that the target may not apply.
    Only NULL Unity built-in lightmap slots may be omitted: those are unused
    scene-lighting metadata on these dynamic face shaders. Any other null,
    external, missing, ambiguous or unsupported reference fails closed.
    """
    from bridge.content.sr2_appearance import source_object_index, source_ref
    shaders = {'base-null': 'SR/Null Render',
               'eyes': 'SR/AMP/Slime/Face/Eyes Default',
               'mouth': 'SR/AMP/Slime/Face/Mouth Default'}
    null_lightmaps = {'unity_Lightmaps', 'unity_LightmapsInd', 'unity_ShadowMasks'}
    objects = source_object_index(assets)

    def resolve(pointer, kind):
        pid = source_ref(pointer)
        if not pid or pid not in objects or objects[pid].type.name != kind:
            raise ValueError(f'missing or unsupported source face {kind} reference')
        return objects[pid]

    def number(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('non-finite or malformed source face property')
        return value  # No color/property rounding or clamping.

    def properties(entries, convert):
        result = {}
        for key, value in entries:
            if not isinstance(key, str) or not key or key in result:
                raise ValueError('ambiguous source face property name')
            result[key] = convert(value)
        return result

    def vector(value, axes):
        return [number(value[k]) for k in axes]

    expression = binding.get('source_face_expression')
    if binding.get('source_face') is not True or type(expression) is not int or expression <= 0:
        raise ValueError('source face lacks a resolved initial expression')
    rows = binding.get('source_face_materials')
    if not isinstance(rows, list) or not rows:
        raise ValueError('source face has no authored material passes')
    if not all(isinstance(r, dict) and isinstance(r.get('face_role'), str) for r in rows):
        raise ValueError('source face material roles are malformed')
    roles = [r['face_role'] for r in rows]
    allowed = [['eyes', 'mouth']] if binding.get('embedded_face') is True else [['base-null', 'eyes', 'mouth'], ['base-null', 'eyes']]   # Yolky has no mouth
    if roles not in allowed:
        raise ValueError('unknown, unordered or ambiguous source face roles')
    result = []
    try:
        for row in rows:
            role = row['face_role']
            pid = row['source_path_id']
            material = resolve({'m_FileID': 0, 'm_PathID': pid}, 'Material').read_typetree()
            shader = resolve(material['m_Shader'], 'Shader').read_typetree()['m_ParsedForm']['m_Name']
            # the glow eye family (some Largos) is a native SR1 shader too; the plugin maps it with its optional noise map
            if shader != shaders[role] and not (role == 'eyes' and shader in ('SR/AMP/Slime/Face/Eyes Glow', 'SR/AMP/Slime/Face/Eyes Shadow'))                     and not (role == 'mouth' and shader == 'SR/AMP/Slime/Face/Mouth Shadow'):
                raise ValueError(f'unsupported source face shader for {role}: {shader}')
            if not isinstance(material['m_Name'], str) or not material['m_Name']:
                raise ValueError('source face material name is missing')
            saved = material['m_SavedProperties']
            if saved.get('m_Ints'):
                raise ValueError('integer source face properties are unsupported')
            colors = properties(saved['m_Colors'], lambda v: vector(v, 'rgba'))
            floats = properties(saved['m_Floats'], number)
            queue = material['m_CustomRenderQueue']
            if type(queue) is not int or not -1 <= queue <= 5000:
                raise ValueError('source face render queue must be an integer in [-1, 5000]')
            if not all(isinstance(material[k], list) for k in ('m_ValidKeywords', 'm_InvalidKeywords')):
                raise ValueError('source face keywords must be lists')
            keywords = material['m_ValidKeywords'] + material['m_InvalidKeywords']
            if not all(isinstance(k, str) and k for k in keywords):
                raise ValueError('source face keyword metadata is malformed')
            textures = []
            texenvs = properties(saved['m_TexEnvs'], lambda v: v)
            for prop, env in texenvs.items():
                texture_id = source_ref(env['m_Texture'])
                if not texture_id and prop in null_lightmaps:
                    continue
                glow_noise = role == 'eyes' and prop == '_GlowNoise'
                if glow_noise and not texture_id:
                    continue
                if role == 'base-null' or (prop != '_FaceAtlas' and not glow_noise):
                    raise ValueError('unsupported source face texture property or textured null pass')
                texture_obj = resolve(env['m_Texture'], 'Texture2D')
                texture_tree = texture_obj.read_typetree()
                settings = texture_tree['m_TextureSettings']
                expected_settings = {'m_FilterMode': 1, 'm_Aniso': 1, 'm_MipBias': 0.0,
                                     'm_WrapU': 0, 'm_WrapV': 0, 'm_WrapW': 0}
                if glow_noise:
                    expected_settings = dict(settings) if isinstance(settings, dict) else expected_settings   # tiling noise: its own sampler
                if (not isinstance(settings, dict) or set(settings) != set(expected_settings)
                        or any(number(settings[k]) != v for k, v in expected_settings.items())
                        or any(type(settings[k]) is not int for k in expected_settings if k != 'm_MipBias')
                        or type(texture_tree['m_MipCount']) is not int or (texture_tree['m_MipCount'] != 1 and not glow_noise)
                        or type(texture_tree['m_ColorSpace']) is not int or (texture_tree['m_ColorSpace'] != 1 and not glow_noise)):
                    raise ValueError('unsupported source face atlas sampler, mip or color-space metadata')
                # m_ColorSpace is uninterpreted serialized provenance, NOT the
                # UnityEngine.ColorSpace enum. Only the measured source bound is supported.
                textures.append({'property': prop, 'file': exporter.export_source(texture_obj),
                    'source_name': texture_obj.read().m_Name,
                    'scale': vector(env['m_Scale'], 'xy'), 'offset': vector(env['m_Offset'], 'xy'),
                    'source_texture_settings': dict(settings),
                    'source_color_space': texture_tree['m_ColorSpace'], 'mip_count': texture_tree['m_MipCount']})
            if role in ('eyes', 'mouth') and not any(t['property'] == '_FaceAtlas' for t in textures):
                raise ValueError('source face pass has no native face atlas')
            result.append({'face_role': role, 'name': material['m_Name'], 'source_path_id': pid,
                           'shader': shader, 'colors': colors, 'floats': floats,
                           'render_queue': queue, 'keywords': keywords, 'textures': textures})
    except (KeyError, IndexError, TypeError, AttributeError) as ex:
        raise ValueError('source face material metadata is incomplete or malformed') from ex
    return result


def material_texture_names(material) -> dict:
    """Resolve a material's texture-property map. A non-null reference that cannot be resolved
    is an extraction failure (fail closed), never a silent omission."""
    out = {}
    try:
        for entry in material.m_SavedProperties.m_TexEnvs:
            texture = entry[1].m_Texture
            if texture is None or not getattr(texture, 'path_id', 0):
                continue
            try:
                out[entry[0]] = texture.deref().read().m_Name
            except Exception as ex:
                raise ValueError(f"texture reference {entry[0]} on {material.m_Name} could not be resolved: {type(ex).__name__}") from ex
    except ValueError:
        raise
    except Exception as ex:
        raise ValueError(f"texture properties of {material.m_Name} could not be read: {type(ex).__name__}") from ex
    return out


def material_colors(material) -> dict:
    out = {}
    try:
        for entry in material.m_SavedProperties.m_Colors:
            c = entry[1]
            out[entry[0]] = [round(_f(c.r), 4), round(_f(c.g), 4), round(_f(c.b), 4), round(_f(c.a), 4)]
    except Exception:
        pass
    return out


BODY_LIGHTING_PROPERTIES = ('_LightingUVContribution', '_BodyLightingContribution',
                            '_LightingUVHorizontalAdjust')


def material_body_properties(material) -> tuple[str, dict]:
    """Preserve the bounded source body controls; no guessed values or color conversion.

    These names/register roles were checked in the compiled SR2 Body and SR1 Stripe
    shaders. Other shader families retain their identity but get no lighting adapter.
    """
    try:
        shader = material.m_Shader.deref().read_typetree()['m_ParsedForm']['m_Name']
        if not isinstance(shader, str) or not shader:
            raise ValueError('source material shader name is missing')
        if shader != 'SR/AMP/Slime/Body':
            return shader, {}
        floats = {}
        for key, value in material.m_SavedProperties.m_Floats:
            if key not in BODY_LIGHTING_PROPERTIES:
                continue
            if key in floats or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('invalid or duplicate source body lighting property: ' + key)
            floats[key] = value
        if set(floats) != set(BODY_LIGHTING_PROPERTIES):
            raise ValueError('source body lighting controls are incomplete')
        return shader, floats
    except ValueError:
        raise
    except (KeyError, TypeError, AttributeError) as ex:
        raise ValueError('source body shader or lighting metadata is unreadable') from ex


def _lerp_rgb(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def destination_stripe_mask(color_mask_red, color_mask_blue):
    """Destination `_StripeTexture` convention (SR/AMP/Slime/Body/Stripe): bright = plain
    body, dark = the marked/striped region; shipped SR1 masks range from a 179 floor
    (body_stripes, subtle stripes) to 0 (body_stripes_hunter). The SR2 `_ColorMask`
    encodes the opposite (R = dark markings incl. ear pattern, B = tail ring bands), so
    the mark union must be inverted. Writing max(R,B) directly made the stripe treatment
    cover the unmarked body and left the real markings untreated (wrong color patches).
    Strength matches the shipped native family (full-contrast masks exist natively)."""
    import numpy as np
    return 255.0 - np.maximum(color_mask_red, color_mask_blue)


def bake_display_albedo(assets: Sr2Assets, out_dir: Path, material_name: str,
                        texture_map: dict, colors: dict, written: list[str],
                        fallback_colors: dict | None = None) -> str | None:
    """Compose a display albedo the destination Standard shader can actually render.

    SR2 slime materials are custom-shader driven and their textures are DATA MASKS, not
    albedo: `_ColorMask` carries per-channel region marks (observed on the Ringtail: R =
    dark markings incl. the ear pattern, B = the tail ring bands, G = a small light
    region), `_StripeTexture` is a mostly-black pattern mask and `_FaceAtlas` carries the
    expression art (R channel = the two eye blobs; G = a wide region shown incorrectly as
    a giant dark patch in the previous attempt). This bakes a flat destination albedo:
    the top-color base with the R/B regions tinted by the material's own `_Red*`/`_Blue*`
    color sets and the G region lightly shifted by `_Green*`; face atlases compose eyes
    from the R blobs plus a designed smile over the face base color.
    """
    import numpy as np  # local import: extraction-only dependency
    from PIL import Image as PilImage

    def _rgb(colors_dict, key):
        value = colors_dict.get(key)
        if value is None:
            return None
        return (value[0], value[1], value[2])

    fallback = fallback_colors or {}
    top = (_rgb(colors, '_TopColor') or _rgb(colors, '_MiddleColor') or _rgb(colors, '_BottomColor')
           or _rgb(fallback, '_TopColor') or _rgb(fallback, '_MiddleColor'))
    if top is None and '_FaceAtlas' in texture_map:
        top = (0.88, 0.82, 0.70)  # default slime face base color (source cream)
    if top is None:
        return None

    if '_ColorMask' in texture_map:
        mode, source_name = 'color-mask', texture_map['_ColorMask']
    elif '_StripeTexture' in texture_map:
        mode, source_name = 'stripe', texture_map['_StripeTexture']
    elif '_FaceAtlas' in texture_map:
        mode, source_name = 'face', texture_map['_FaceAtlas']
    else:
        return None
    _, texture = assets.by_name('Texture2D', source_name)
    if texture is None:
        return None
    image = texture.image.convert('RGB')
    arr = np.asarray(image).astype(np.float64)
    # Smooth the data-mask channels before compositing: the source masks are hard-edged data
    # (rendered in-shader with its own filtering), so baking them straight into an albedo
    # produces jagged region borders on the destination mesh. A light gaussian pass gives the
    # destination albedo clean mark edges.
    if mode in ('color-mask', 'stripe'):
        from PIL import ImageFilter as PilFilter
        arr = np.asarray(PilImage.fromarray(arr.astype(np.uint8)).filter(PilFilter.GaussianBlur(1.2))).astype(np.float64)
    h, w = arr.shape[:2]
    result = np.empty((h, w, 3), dtype=np.float64)

    if mode == 'color-mask':
        base = np.array(top) * 255.0
        result[:] = base
        red = (_rgb(colors, '_RedMiddleColor') or _rgb(colors, '_MiddleColor') or top)
        blue = (_rgb(colors, '_BlueMiddleColor') or _rgb(colors, '_BottomColor') or top)
        green = (_rgb(colors, '_GreenTopColor') or top)
        red = np.array(red) * 255.0
        blue = np.array(blue) * 255.0
        green = np.array(green) * 255.0

        def blend(channel, color, lo, hi, cap):
            t = np.clip((channel - lo) / (hi - lo), 0.0, 1.0) * cap
            return result * (1.0 - t[..., None]) + color * t[..., None]

        result = blend(arr[:, :, 0], red, 40.0, 160.0, 1.0)     # dark markings incl. ears
        result = blend(arr[:, :, 1], green, 140.0, 220.0, 0.45)  # light region (subtle)
        result = blend(arr[:, :, 2], blue, 60.0, 200.0, 1.0)     # tail ring bands
    elif mode == 'stripe':
        # Legacy ramp for stripe-mask-only materials: dark mask, brighter pattern regions
        # ramp between the top and middle colors.
        base = np.array(top) * 255.0
        mid = np.array(_rgb(colors, '_MiddleColor') or _rgb(fallback, '_MiddleColor') or top) * 255.0
        t = np.clip((arr[:, :, 0] - 64.0) / 115.0, 0.0, 1.0)
        result = base[None, None, :] * (1.0 - t[..., None]) + mid[None, None, :] * t[..., None]
    else:  # face
        base = np.array(top) * 255.0
        result[:] = base
        eye = np.array(_rgb(colors, '_EyeRed') or (0.11, 0.04, 0.01)) * 255.0
        glint = np.array(_rgb(colors, '_EyeBlue') or (0.95, 0.89, 0.88)) * 255.0
        mouth = np.array(_rgb(colors, '_EyeGreen') or (0.31, 0.12, 0.03)) * 255.0
        mouth = mouth * 0.8
        # Eye blobs observed in the source atlas (R channel): x[13,19] y[27,33] and
        # x[44,50] y[27,33] on the 64x64 happy face, kept at their source positions.
        sx = w / 64.0
        sy = h / 64.0

        def ellipse(cx, cy, rx, ry, color, strength=1.0, feather=1.4):
            yy, xx = np.mgrid[0:h, 0:w]
            dist = np.sqrt((((xx + 0.5) / sx - cx) / rx) ** 2 + (((yy + 0.5) / sy - cy) / ry) ** 2)
            # Feathered edge: 1 inside, fading to 0 across the last feather band.
            t = np.clip((1.0 - dist) / (feather / max(rx, 1.0)), 0.0, 1.0) * strength
            return result * (1.0 - t[..., None]) + color * t[..., None]

        lid = np.array(_rgb(colors, '_EyeRed') or (0.11, 0.04, 0.01)) * 255.0 * 0.85
        eyeball = np.array(_rgb(colors, '_TopColor') or (0.88, 0.82, 0.70)) * 255.0
        maskbrown = np.array(_rgb(colors, '_RedMiddleColor') or _rgb(colors, '_MiddleColor') or (0.40, 0.29, 0.25)) * 255.0
        # The source body mask carries the brown "bandit mask" band across the eye region
        # (the ringtail's signature raccoon band); the destination face mesh overlays that
        # area, so the band must be part of the face art itself or it disappears.
        result = ellipse(32.0, 30.5, 26.0, 7.2, maskbrown, strength=0.96, feather=2.4)  # band
        # Half-lidded eye art (official ringtail style): a cream eyeball, a dark lid covering
        # the upper half, a small pupil and one glint dot per eye - all feathered.
        for ex in (16.5, 47.5):
            result = ellipse(ex, 31.0, 5.0, 4.4, eyeball)                    # eyeball
        for ex in (16.5, 47.5):
            result = ellipse(ex, 28.6, 4.6, 2.6, lid, strength=0.92)         # upper lid
        for ex in (16.5, 47.5):
            result = ellipse(ex, 32.6, 2.0, 1.8, eye, strength=0.95)         # pupil
        for ex in (15.7, 46.7):
            result = ellipse(ex, 29.7, 1.2, 1.1, glint)                      # glint

    if mode == 'color-mask':
        # Destination _StripeTexture mask (SR/AMP/Slime/Body/Stripe): the R (markings incl. the
        # ear pattern) and B (tail ring bands) channels merged to grayscale, so the destination
        # body shader applies its stripe treatment exactly where the source shader would.
        marks = destination_stripe_mask(arr[:, :, 0], arr[:, :, 2])
        masks_name = 'tex-baked-' + re.sub(r'[^0-9A-Za-z._-]', '_', material_name) + '-marks.png'
        masks_target = out_dir / masks_name
        refuse_existing_leaf(masks_target)
        masks_image = PilImage.fromarray(np.clip(marks, 0, 255).astype(np.uint8), 'L')
        masks_image.save(masks_target)
        masks_payload = masks_target.read_bytes()
        if not masks_payload.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            raise ValueError(f'baked marks for {material_name} did not produce a valid PNG payload')
        written.append(masks_name)

    if mode == 'face':
        # Destination _FaceAtlas mask (SR/AMP/Slime/Face/Eyes): same channel semantics as the
        # source atlas (R = eyes, G = mouth region) recomposed for the target mesh UVs: the eye
        # blobs are enlarged to the source proportions and the wide source mouth band becomes a
        # small smile, so the destination face shader draws the intended cute face.
        tex_scale_x = w / 64.0
        tex_scale_y = h / 64.0
        mask = np.zeros((h, w, 3), dtype=np.uint8)
        yy, xx = np.mgrid[0:h, 0:w]
        for eye_cx, eye_cy in ((16.5, 30.5), (47.5, 30.5)):
            inside = ((((xx + 0.5) - eye_cx * tex_scale_x) / (5.6 * tex_scale_x)) ** 2 +
                      (((yy + 0.5) - eye_cy * tex_scale_y) / (5.2 * tex_scale_y)) ** 2) <= 1.0
            mask[inside, 0] = 255
        inside = ((((xx + 0.5) - 32.0 * tex_scale_x) / (10.5 * tex_scale_x)) ** 2 +
                  (((yy + 0.5) - 43.5 * tex_scale_y) / (4.2 * tex_scale_y)) ** 2) <= 1.0
        mouth_band = inside & ((yy + 0.5) >= 40.5 * tex_scale_y)
        mask[mouth_band, 1] = 255
        mask_name = 'tex-baked-' + re.sub(r'[^0-9A-Za-z._-]', '_', material_name) + '-facemask.png'
        mask_target = out_dir / mask_name
        refuse_existing_leaf(mask_target)
        PilImage.fromarray(mask, 'RGB').save(mask_target)
        mask_payload = mask_target.read_bytes()
        if not mask_payload.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
            raise ValueError(f'baked face mask for {material_name} did not produce a valid PNG payload')
        written.append(mask_name)

    albedo = PilImage.fromarray(np.clip(result, 0, 255).astype(np.uint8), 'RGB')
    file_name = 'tex-baked-' + re.sub(r'[^0-9A-Za-z._-]', '_', material_name) + '.png'
    target = out_dir / file_name
    refuse_existing_leaf(target)
    try:
        albedo.save(target)
        payload = target.read_bytes()
    except OSError as ex:
        raise ValueError(f'baked albedo for {material_name} could not be written: {type(ex).__name__}') from ex
    if not payload.startswith(bytes((137, 80, 78, 71, 13, 10, 26, 10))):
        raise ValueError(f'baked albedo for {material_name} did not produce a valid PNG payload')
    written.append(file_name)
    return file_name


def snake(name: str) -> str:
    value = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name)
    return value.lower()


def canonical_from_reference(reference: str) -> str:
    return 'slime.' + snake(reference.split('.')[-1])


def species_from_canonical(canonical: str) -> str:
    return ''.join(part.capitalize() for part in canonical.split('.')[-1].split('_'))


def pick_default_face(assets: Sr2Assets, species: str) -> str | None:
    """Find the default (happy) face material for a species among `_FaceAtlas` carriers.

    Retained for diagnostics/lookups; part_material_slots resolves the eye+mouth slot pair.
    """
    key = snake(species)
    wanted = [f'face_{key}_happy', f'face_{key}', 'face_default_happy', 'face_default']
    by_atlas: dict[str, list[str]] = {}
    for _, data in assets.all_named('Material'):
        name = data.m_Name or ''
        if 'Shadow' in name or 'Twin' in name:
            continue
        textures = material_texture_names(data)
        atlas = textures.get('_FaceAtlas')
        if atlas:
            by_atlas.setdefault(atlas, []).append(name)
    for atlas in wanted:
        names = by_atlas.get(atlas)
        if names:
            for preferred in ('eyeHappy', 'mouthHappy'):
                if preferred in names:
                    return preferred
            names = sorted(names, key=len)
            return names[0]
    for names in by_atlas.values():
        if names:
            return sorted(names, key=len)[0]
    return None


def _mat4_from_trs(p, q, s):
    """Compose a Unity-style TRS matrix (row-major 4x4 as nested lists)."""
    import numpy as np
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    rot = [
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ]
    m = np.eye(4)
    for r in range(3):
        for c in range(3):
            m[r, c] = rot[r][c] * s[c]
        m[r, 3] = p[r]
    return m


def collect_bone_tree(assets: Sr2Assets, species: str) -> list[dict]:
    """Locate <prefab root>/Appearance/bone_root and flatten its Transform hierarchy.

    Returns bones as [{'name', 'parent' (index or -1), 'p', 'q', 's'}] in a stable order.
    """
    root_name = f'slime{species}'
    from bridge.content.sr2_appearance import source_object_index
    objects = source_object_index(assets)
    root_transform = None
    for obj in objects.values():
        if obj.type.name != 'GameObject':
            continue
        try:
            if (getattr(obj.read(), 'm_Name', '') or '') != root_name:
                continue
            data = obj.read()
            for comp in data.m_Component or []:
                ref = comp['component'] if isinstance(comp, dict) else comp.component
                cpid = ref['m_PathID'] if isinstance(ref, dict) else ref.m_PathID
                cobj = objects.get(cpid)
                if cobj is not None and cobj.type.name == 'Transform':
                    root_transform = cpid
                    break
        except Exception:
            continue
        if root_transform is not None:
            break
    if root_transform is None:
        return []

    def go_name(pid):
        o = objects.get(pid)
        if o is None or o.type.name != 'GameObject':
            return ''
        try:
            return getattr(o.read(), 'm_Name', '') or ''
        except Exception:
            return ''

    def children(pid):
        o = objects.get(pid)
        if o is None or o.type.name != 'Transform':
            return []
        try:
            tree = o.read_typetree()
        except Exception:
            return []
        return [c['m_PathID'] for c in tree.get('m_Children') or []]

    # BFS from the prefab root to find the bone_root transform.
    from collections import deque
    queue = deque([(root_transform, 0)])
    bone_root_pid = None
    while queue:
        pid, depth = queue.popleft()
        if depth > 8:
            continue
        gid = None
        o = objects.get(pid)
        if o is not None:
            try:
                gid = o.read_typetree().get('m_GameObject', {}).get('m_PathID')
            except Exception:
                gid = None
        if gid is not None and go_name(gid) == 'bone_root':
            bone_root_pid = pid
            break
        for child in children(pid):
            queue.append((child, depth + 1))
    if bone_root_pid is None:
        return []

    bones = []
    index_by_pid = {}

    def walk(pid, parent_index):
        o = objects.get(pid)
        if o is None or o.type.name != 'Transform':
            return
        try:
            tree = o.read_typetree()
        except Exception:
            return
        gid = tree.get('m_GameObject', {}).get('m_PathID')
        pos = tree.get('m_LocalPosition') or {}
        rot = tree.get('m_LocalRotation') or {}
        scl = tree.get('m_LocalScale') or {}
        index_by_pid[pid] = len(bones)
        bones.append({
            'name': go_name(gid) if gid else '?',
            'parent': parent_index,
            'p': [_f(pos.get('x', 0)), _f(pos.get('y', 0)), _f(pos.get('z', 0))],
            'q': [_f(rot.get('x', 0)), _f(rot.get('y', 0)), _f(rot.get('z', 0)), _f(rot.get('w', 1))],
            's': [_f(scl.get('x', 1)), _f(scl.get('y', 1)), _f(scl.get('z', 1))],
        })
        own_index = len(bones) - 1
        for child in children(pid):
            walk(child, own_index)

    walk(bone_root_pid, -1)
    return bones


def _bind_matrix_determinant(row) -> float | None:
    """3x3 determinant of a 4x4 affine bind matrix (dict or flattened-sequence form)."""
    try:
        if isinstance(row, dict):
            values = [_f(row.get(f'e{r}{c}', row.get(f'm{r}{c}', 0)))
                      for r in range(4) for c in range(4)]
        else:
            values = [_f(row[r * 4 + c]) for r in range(4) for c in range(4)]
    except (TypeError, ValueError, IndexError, KeyError, AttributeError):
        return None
    if any(not math.isfinite(value) for value in values):
        return None
    det = (values[0] * (values[5] * values[10] - values[6] * values[9])
           - values[1] * (values[4] * values[10] - values[6] * values[8])
           + values[2] * (values[4] * values[9] - values[5] * values[8]))
    return det if abs(det) > 1e-9 else None


def skeleton_skin_index(bones: list[dict]) -> dict[int, int]:
    """Map crc32(bone transform path from bone_root) -> skeleton bone index.

    Verified against the shipped SR2 files (Unity 2021.3.28f1): every Mesh
    m_BoneNameHashes entry is exactly the unsigned CRC32 of the UTF-8 transform
    path joining the GameObject names from `bone_root` down to the bone, e.g.
    'bone_root/bone_slime/bone_core/bone_jiggle_rig/bone_skin_rig'.
    """
    table: dict[int, int] = {}
    texts: dict[int, str] = {}
    for index, bone in enumerate(bones):
        chain = [bone.get('name') or '?']
        parent = bone.get('parent')
        steps = 0
        while isinstance(parent, int) and parent >= 0:
            if parent >= len(bones) or steps > len(bones):
                raise ValueError('skeleton bone parent chain is malformed')
            chain.append(bones[parent].get('name') or '?')
            parent = bones[parent].get('parent')
            steps += 1
        chain.reverse()
        text = '/'.join(chain)
        hashed = zlib.crc32(text.encode('utf-8'))
        previous = texts.get(hashed)
        if previous is not None and previous != text:
            raise ValueError('skeleton skin path hash collides')
        texts[hashed] = text
        table[hashed] = index
    return table


def resolve_mesh_skin_bones(mesh_tree, skeleton: list[dict], declared_names: list[str]) -> list[str]:
    """Resolve a part Mesh's own ordered skin bones to declared skeleton names.

    The Mesh is the authority: m_BindPose and m_BoneNameHashes are parallel,
    equal-length arrays whose order matches the vertex bone-weight indices. Every
    mesh bone must be a real skeleton bone and must be declared by the appearance,
    directly or through its nearest declared ancestor (the jiggle parent whose
    rest matrix is equal in SR2). One authored hash per real mesh no longer
    matches any transform (LF_ringtail_tail_LOD0); such a slot is accepted only
    when the declaration is positionally consistent with every resolvable mesh
    bone, and it is refused otherwise.
    """
    if not isinstance(mesh_tree, dict):
        raise ValueError('part mesh is missing')
    bind_poses = mesh_tree.get('m_BindPose')
    hashes = mesh_tree.get('m_BoneNameHashes')
    if not isinstance(bind_poses, list) or not bind_poses:
        raise ValueError('part mesh has no bind poses')
    if not isinstance(hashes, (list, tuple)) or len(hashes) != len(bind_poses):
        raise ValueError('mesh bones and bind poses are inconsistent')
    for row in bind_poses:
        if _bind_matrix_determinant(row) is None:
            raise ValueError('mesh bind pose is not an invertible transform')
    if not isinstance(declared_names, (list, tuple)) or not declared_names:
        raise ValueError('appearance skin declaration is missing')
    try:
        wanted = [int(entry) for entry in hashes]
    except (TypeError, ValueError) as ex:
        raise ValueError('mesh bone name hash is malformed') from ex
    table = skeleton_skin_index(skeleton)
    declared = set(declared_names)
    names = [skeleton[index].get('name') for index in range(len(skeleton))]
    resolved: list[str | None] = []
    for hashed in wanted:
        index = table.get(hashed)
        resolved.append(names[index] if index is not None else None)
    representatives: list[str | None] = []
    for index, name in enumerate(resolved):
        if name is None:
            representatives.append(None)
            continue
        bone_index: int | None = table[wanted[index]]
        representative = None
        while isinstance(bone_index, int) and bone_index >= 0:
            candidate = skeleton[bone_index].get('name')
            if candidate in declared:
                representative = candidate
                break
            parent = skeleton[bone_index].get('parent')
            bone_index = parent if isinstance(parent, int) and parent >= 0 else None
        if representative is None:
            raise ValueError(f'mesh skin bone {name!r} is not declared by the appearance')
        representatives.append(representative)
    if any(representative is None for representative in representatives):
        if len(bind_poses) != len(declared_names):
            raise ValueError('mesh skin bone hash does not resolve to the skeleton')
        for position, representative in enumerate(representatives):
            if representative is not None and representative != declared_names[position]:
                raise ValueError('mesh skin bone hash does not resolve to the skeleton')
        for position, representative in enumerate(representatives):
            if representative is None:
                fallback = declared_names[position]
                if fallback not in set(names):
                    raise ValueError('declared skin bone is missing from the skeleton')
                representatives[position] = fallback
    resolved_names: list[str] = []
    for representative in representatives:
        if representative is None:
            raise ValueError('mesh skin bone hash does not resolve to the skeleton')
        resolved_names.append(representative)
    if len(set(resolved_names)) != len(resolved_names):
        raise ValueError('mesh skin bones collapse onto one declared bone')
    return resolved_names


def match_bindposes_to_bones(bones: list[dict], bind_poses: list[list[float]]) -> list[str]:
    """Resolve each bindpose row to a bone NAME by comparing inverse rest world matrices."""
    import numpy as np
    if not bones or not bind_poses:
        return []
    world = []
    for bone in bones:
        local = _mat4_from_trs(bone['p'], bone['q'], bone['s'])
        world.append(local if bone['parent'] < 0 else world[bone['parent']] @ local)
    inverses = [np.linalg.inv(w) for w in world]
    names = []
    for row in bind_poses:
        target = np.array(row).reshape(4, 4)
        best = None
        best_err = None
        best_is_deform = False
        for i, inv in enumerate(inverses):
            err = float(np.abs(inv - target).max())
            is_deform = 'jiggle' in bones[i]['name'] or 'skin' in bones[i]['name']
            better = best_err is None or err < best_err - 1e-6
            tied_deform = (not better and best_err is not None and abs(err - best_err) <= 1e-6
                           and is_deform and not best_is_deform)
            if better or tied_deform:
                best_err = err
                best = i
                best_is_deform = is_deform
        if best is None or best_err is None or best_err > 1e-2:
            return []
        names.append(bones[best]['name'])
    return names


def collect_species_meshes(assets: Sr2Assets, species: str) -> list[dict]:
    """Locate the part meshes for a species: shared body/face + LF_<species> parts (LOD0)."""
    key = snake(species)
    parts: list[dict] = []
    seen = set()

    def add(role: str, name: str):
        if name in seen:
            return
        obj, mesh = assets.by_name('Mesh', name)
        if mesh is None:
            return
        seen.add(name)
        parts.append({'role': role, 'mesh_name': name, 'mesh': mesh, 'mesh_obj': obj})

    add('body', f'slime_{key}_v4_LOD0')
    if not any(p['role'] == 'body' for p in parts):
        add('body', f'slime_{key}_LOD0')
    if not any(p['role'] == 'body' for p in parts):
        # a species with its own body mesh under another naming scheme (SR2_yolky_body_LOD0)
        for _, mesh in assets.all_named('Mesh'):
            name = (mesh.m_Name or '').lower()
            if key in name and name.endswith('body_lod0'):
                add('body', mesh.m_Name)
                break
    if not any(p['role'] == 'body' for p in parts):
        add('body', 'slime_default_v4_LOD0')
    for _, mesh in assets.all_named('Mesh'):                  # the species' own face mesh (SR2_yolky_face_LOD0), else the shared one
        name = (mesh.m_Name or '').lower()
        if key in name and name.endswith('face_lod0'):
            add('face', mesh.m_Name)
            break
    if not any(p['role'] == 'face' for p in parts):
        add('face', 'slime_face_LOD0')
    for prefix in (f'LF_{key}_', f'{key}_'):
        for _, mesh in assets.all_named('Mesh'):
            name = mesh.m_Name or ''
            if not name.startswith(prefix) or '_LOD0' not in name or 'stone' in name.lower():
                continue
            if name.endswith('_LOD0'):
                role_part = name[len(prefix):-len('_LOD0')]
                add(role_part or 'part', name)
    return parts


def part_material_slots(assets: Sr2Assets, species: str, role: str):
    """Resolve source material slots for a part, preserving submesh correspondence.

    Faces carry separate eye and mouth materials in the default (happy) expression; body parts
    carry their species base material. Returns an ordered list of (name, material) pairs.
    """
    if role == 'face':
        names = []
        by_atlas = {}
        for _, data in assets.all_named('Material'):
            name = data.m_Name or ''
            if 'Shadow' in name or 'Twin' in name:
                continue
            atlas = material_texture_names(data).get('_FaceAtlas')
            if atlas:
                by_atlas.setdefault(atlas, []).append(name)
        happy = by_atlas.get('face_default_happy', [])
        for preferred in ('eyeHappy', 'mouthHappy'):
            if preferred in happy:
                names.append(preferred)
        if not names and happy:
            names = sorted(happy, key=len)[:1]
        slots = []
        for name in names:
            _, material = assets.by_name('Material', name)
            if material is not None:
                slots.append((name, material))
        return slots
    base_name = f'slime{species}Base'
    _, base = assets.by_name('Material', base_name)
    return [(base_name, base)] if base is not None else []


def _skeleton_bone_paths(bones: list[dict]) -> dict[str, str]:
    """Map unique skeleton bone names to their '/'-joined path from the skeleton root.

    Mirrors the loader's name-based rebuild: only uniquely named bones can be addressed,
    and only parent-before-child rows can be rebuilt (BuildSkeleton parents by index).
    Rigid attachments reference their parent bone by name; the path is what makes the
    loader's EnsurePath find the already-rebuilt source bone instead of creating an
    identity container.
    """
    index_by_name = {}
    for index, bone in enumerate(bones):
        name = bone.get('name')
        parent = bone.get('parent')
        if not isinstance(name, str) or not name or name in index_by_name:
            raise ValueError('skeleton bone names are missing or duplicated')
        if type(parent) is not int or parent < -1 or parent >= len(bones) or parent >= index:
            raise ValueError('skeleton bone tree is malformed')
        index_by_name[name] = index
    paths = {}
    for name, index in index_by_name.items():
        chain = []
        current = index
        while current != -1:
            chain.append(bones[current]['name'])
            current = bones[current]['parent']
        paths[name] = '/'.join(reversed(chain))
    return paths


def albedo_uv_set(role: str, geometry: dict, materials: list) -> str:
    """Which UV channel the SR2 body shader samples for its colour-mask / stripe / baked albedo textures.

    SR2 body meshes carry two UV sets: `uv` (UV0) is a tiling set (it spans -0.5..1.5 around the body, used by the
    noise textures) and `uv2` (UV1) is the unique 0..1 atlas the species colour masks are authored against. Sampling
    the masks with UV0 repeats the pattern twice around the body (the Ringtail's wrong tan patches). Face meshes keep
    UV0 (the face atlas). A part without a valid unique second set, or without a baked markings mask, keeps UV0.
    """
    if role == 'face':
        return 'uv'
    uv2 = geometry.get('uv2') or []
    vertices = geometry.get('vertices') or []
    if not uv2 or len(uv2) != len(vertices):
        return 'uv'
    if not any('Slime/Body' in str(m.get('shader', '')) for m in materials or []):
        return 'uv'
    # SR1's body shader derives the top/middle/bottom gradient from UV0 (the native slime mesh's UV0 is this same tiling set,
    # v tracking height). Only a part that carries a baked markings mask needs the atlas; every other body keeps UV0, or its
    # colors split left/middle/right instead of top to bottom.
    if not any(t.get('property') == '_StripeTextureBaked' for m in materials or [] for t in (m.get('textures') or [])):
        return 'uv'
    low = min(min(p_[0], p_[1]) for p_ in uv2)
    high = max(max(p_[0], p_[1]) for p_ in uv2)
    return 'uv2' if low >= -0.01 and high <= 1.01 else 'uv'


def extract_species(species: str, canonical: str | None, sr2_dir: Path, out_dir: Path | None,
                    allow_nonlab: bool = False, *, complete_source: bool = False, source_bundle: str | None = None, assets=None,
                    appearance_mesh_only: bool = False,
                    clip_ids: list[int] | None = None, default_clip: str | None = None) -> dict:
    if complete_source and appearance_mesh_only:
        raise ValueError('complete source and appearance mesh-only extraction are mutually exclusive')
    if complete_source:
        if (not isinstance(clip_ids, list) or not 1 <= len(clip_ids) <= 16
                or any(type(p) is not int or p == 0 for p in clip_ids)
                or len(set(clip_ids)) != len(clip_ids)
                or not isinstance(default_clip, str) or not default_clip or out_dir is None):
            raise ValueError('complete source cache requires explicit clip IDs, exact default name and fresh output')
    elif appearance_mesh_only:
        if not isinstance(out_dir, Path):
            raise ValueError('appearance mesh-only extraction requires a fresh explicit output directory')
        # Optional: explicit source clips (and the species icon) can ride along with the mesh-only scope.
        if (clip_ids is None) != (default_clip is None):
            raise ValueError('appearance mesh-only clip selection needs both --clip-id and --default-clip')
        if clip_ids is not None and (not 1 <= len(clip_ids) <= 16 or any(type(p_) is not int or p_ == 0 for p_ in clip_ids)
                                     or len(set(clip_ids)) != len(clip_ids) or not isinstance(default_clip, str) or not default_clip):
            raise ValueError('appearance mesh-only clip IDs/default name malformed or duplicated')
    elif clip_ids is not None or default_clip is not None:
        raise ValueError('clip selection requires --complete-source')
    started = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    canonical = canonical or ('slime.' + snake(species))
    canonical = validate_canonical(canonical)
    data_dir = sr2_dir
    if not data_dir.is_dir():
        raise ValueError(f'SR2 data dir not found: {data_dir}')
    if not allow_nonlab:
        try:
            data_dir.resolve().relative_to((REPO_ROOT / 'Lab').resolve())
        except ValueError:
            raise ValueError('untrusted SR2 data dir outside the repository Lab tree')
    out_dir = out_dir or (CACHE_ROOT / canonical)
    if complete_source:
        if '..' in out_dir.parts:
            raise ValueError('complete source cache output traversal refused')
        # Do not resolve aliases before the fresh private-output reparse checks.
        # --allow-nonlab permits a read-only source elsewhere, never a writable escape.
        out_dir = out_dir.absolute()
    elif appearance_mesh_only:
        if '..' in out_dir.parts:
            raise ValueError('appearance mesh-only output traversal refused')
        # Same freshness contract as above: never resolve aliases before the checks, and
        # only ever write into a brand-new directory (never the canonical cache tree).
        out_dir = out_dir.absolute()
    elif not allow_nonlab:
        # Reject reparse points on the output chain BEFORE resolving, then write through the
        # resolved path so a symlinked directory cannot redirect writes elsewhere in Lab.
        refuse_reparse_chain(out_dir, REPO_ROOT / 'Lab')
        out_dir = out_dir.resolve()
        try:
            out_dir.relative_to((REPO_ROOT / 'Lab').resolve())
        except ValueError:
            raise ValueError('cache output must stay inside the repository Lab tree')
    else:
        out_dir = out_dir.resolve()

    # a caller that already holds the (multi-GB) game files passes them in instead of loading a second copy
    assets = assets or (Sr2Assets(data_dir, source_bundle) if source_bundle else Sr2Assets(data_dir))
    # a Largo's parts come from its appearance graph (the species-named mesh lookup scans every Mesh in the game)
    parts = [] if canonical.startswith('largo.') else collect_species_meshes(assets, species)
    resolved = None
    bones = None
    mesh_scope = None
    if complete_source:
        # Reuse the existing appearance schema loader before resolving references.
        from bridge.content.sr2_appearance import collect_source_appearance
        collect_source_appearance(assets, species, [])
        from bridge.content.sr2_source_cache import resolve_species_source, collect_declared_parts
        resolved = resolve_species_source(assets, species)
        parts = collect_declared_parts(assets, resolved, parts)
    elif appearance_mesh_only:
        # Opt-in LOD0 mesh scope: read the declared appearance graph directly. The strict
        # complete-source collector above must keep refusing these species; it is never
        # reached in this mode, so the complete-source contract is not weakened.
        from bridge.content.sr2_appearance import collect_source_appearance
        from bridge.content.sr2_mesh_scope import collect_appearance_mesh_scope
        from bridge.content.sr2_source_cache import resolve_species_source
        collect_source_appearance(assets, species, [])  # loads the generated TypeTree schemas
        bones = collect_bone_tree(assets, species)
        resolved = resolve_species_source(assets, species)
        # a Largo has no species-named part meshes: its parts come from the appearance graph itself
        mesh_scope = collect_appearance_mesh_scope(assets, resolved, None if resolved.get('is_largo') else parts, bones)
        if mesh_scope.get('extraction_scope') != 'appearance-lod0-mesh-only-v1':
            raise ValueError('unexpected appearance mesh scope version')
        scope_parts = mesh_scope.get('parts')
        if not isinstance(scope_parts, list) or not scope_parts:
            raise ValueError(f'no appearance mesh parts found for {species}')
        if len(scope_parts) > 256:
            raise ValueError('appearance mesh scope exceeds the renderer budget')
        if not any(p.get('role') == 'body' for p in scope_parts):
            raise ValueError(f'no body mesh found for {species}')
    if not appearance_mesh_only and not any(p['role'] == 'body' for p in parts):
        raise ValueError(f'no body mesh found for {species}')

    if complete_source:
        from bridge.content.sr2_icon import _fresh_private_output
        out_dir = _fresh_private_output(out_dir)
    elif appearance_mesh_only:
        from bridge.content.sr2_icon import _fresh_private_output
        # Re-use the existing strict private-output guard inside the repository tree; the
        # explicit --allow-nonlab scratch contract gets the equivalent external guard.
        out_dir = _fresh_external_output(out_dir) if allow_nonlab else _fresh_private_output(out_dir)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
    texture_exporter = TextureExporter(assets, out_dir)

    base_material_name = f'slime{species}Base'
    base_material = None
    if not complete_source and not appearance_mesh_only:
        _, base_material = assets.by_name('Material', base_material_name)

    if bones is None:
        bones = collect_bone_tree(assets, species)
    if appearance_mesh_only:
        appearance = None
    else:
        from bridge.content.sr2_appearance import collect_source_appearance
        appearance = collect_source_appearance(assets, species, parts, skeleton=bones)
    bone_paths = _skeleton_bone_paths(bones) if appearance_mesh_only else {}
    loop_parts = mesh_scope['parts'] if appearance_mesh_only else parts
    skinned_renderers = []
    static_renderers = []
    shell_paths = {}                     # source node path -> exported static node path (effects attach to their shell)
    texture_files: list[str] = []
    moved = set()
    for part in loop_parts:
        if appearance_mesh_only:
            # The scope reader already decoded, bounded and validated this geometry.
            geometry = part['geometry']
            binding = part['appearance_binding']
        else:
            geometry = decode_mesh(part['mesh'], part.get('mesh_obj'))
            binding = appearance['parts'][part['mesh_name']]
        static_path = None
        if appearance_mesh_only and part.get('renderer_kind') == 'static':
            parent_bone = binding.get('parent_bone')
            if not isinstance(parent_bone, str) or parent_bone not in bone_paths:
                raise ValueError(f"static part {part['mesh_name']} has no resolvable parent bone")
            static_path = f"{bone_paths[parent_bone]}/{part['role']}_{part['mesh_name']}"
        if not geometry['vertices'] or not geometry['triangles'] or not any(geometry['triangles']):
            raise ValueError(f"part {part['mesh_name']} has no usable geometry")
        materials_out = []
        if part['role'] == 'face':
            # Authored material passes are independent of submesh count: Unity
            # draws extra materials over its final submesh. Never trim this list.
            materials_out = export_source_face_materials(assets, binding, texture_exporter)
            slots = []  # Generic atlas guesses and display baking are unreachable.
            for material in materials_out:
                for texture in material['textures']:
                    file_name = texture['file']
                    if file_name not in moved:
                        moved.add(file_name)
                        texture_files.append(file_name)
        else:
            submesh_count = max(1, len(geometry['triangles']))
            if complete_source or appearance_mesh_only:
                # PPtr-declared effective passes only (never a name-based material guess);
                # the declared count must cover the mesh's submeshes.
                slots = part['source_material_slots']
                if not slots or len(slots) < submesh_count:
                    raise ValueError('declared appearance material count does not cover mesh submeshes')
            else:
                slots = part_material_slots(assets, species, part['role'])
                if not slots:
                    slots = [(base_material_name, base_material)] if base_material is not None else []
                if slots:
                    if len(slots) < submesh_count:
                        slots = slots + [slots[-1]] * (submesh_count - len(slots))
                    else:
                        slots = slots[:submesh_count]
        body_colors: dict | None = None
        for material_name, material in slots:
            if material is None:
                continue
            texture_map = material_texture_names(material)
            colors = material_colors(material)
            shader_name, lighting_floats = material_body_properties(material)
            if canonical.startswith('largo.') and shader_name == 'SR/AMP/Slime/Body':
                # A Largo's body masks (color mask, stripes) are authored for its parents' own meshes and UV layouts; baked onto the
                # shared Largo body they turn into marbled noise. The Largo keeps its palette (top/middle/bottom colors) only.
                texture_map = {}
            if body_colors is None and (colors.get('_TopColor') or colors.get('_MiddleColor')):
                body_colors = colors
            textures_out = []
            for prop, texture_name in texture_map.items():
                if texture_name in SKIP_TEXTURES or prop in SKIP_TEXTURE_PROPERTIES or prop.lower().endswith('3d'):
                    continue  # intentionally non-display textures (noise/static helpers, 3D noise volumes)
                file_name = texture_exporter.export(texture_name)
                if not file_name:
                    if canonical.startswith('largo.'):
                        continue          # a Largo can carry a decoration of a parent that lives in another game file (Boom's cracks)
                    raise ValueError(f"texture {texture_name} ({prop}) for material {material_name} could not be exported")
                textures_out.append({'property': prop, 'file': file_name, 'source_name': texture_name})
                if file_name not in moved:
                    moved.add(file_name)
                    texture_files.append(file_name)
            # The source materials are mask-driven custom shaders; bake a flat display albedo
            # so the destination Standard shader shows real colors instead of raw masks.
            baked = bake_display_albedo(assets, out_dir, material_name, texture_map, colors,
                                        texture_files, fallback_colors=body_colors)
            if baked and baked not in moved:
                moved.add(baked)
            if baked:
                textures_out.insert(0, {'property': '_MainTex', 'file': baked, 'source_name': 'baked-display-albedo'})
                facemask_file = 'tex-baked-' + re.sub(r'[^0-9A-Za-z._-]', '_', material_name) + '-facemask.png'
                if (out_dir / facemask_file).exists():
                    textures_out.append({'property': '_FaceAtlasBaked', 'file': facemask_file, 'source_name': 'baked-face-mask'})
                marks_file = 'tex-baked-' + re.sub(r'[^0-9A-Za-z._-]', '_', material_name) + '-marks.png'
                if (out_dir / marks_file).exists():
                    textures_out.append({'property': '_StripeTextureBaked', 'file': marks_file, 'source_name': 'baked-marks'})
            materials_out.append({
                'name': material_name,
                'shader': shader_name,
                'colors': colors,
                'floats': lighting_floats,
                'textures': textures_out,
            })
        trs = {key: binding[key] for key in ('local_position', 'local_rotation', 'local_scale')
               if appearance_mesh_only and key in binding}
        if static_path is not None:
            # Rigid source attachment: the node path mirrors the rebuilt skeleton names so the
            # loader's EnsurePath parents it under the real source bone, carrying the authored
            # attachment TRS and no skin data at all.
            if set(trs) != {'local_position', 'local_rotation', 'local_scale'}:
                raise ValueError(f"static part {part['mesh_name']} has no authored local transform")
            shell_paths[part.get('source_path')] = static_path
            static_renderers.append({
                'enabled': True,
                'role': part['role'],
                'path': static_path,
                'local_position': trs['local_position'],
                'local_rotation': trs['local_rotation'],
                'local_scale': trs['local_scale'],
                'mesh_name': part['mesh_name'],
                'vertices': geometry['vertices'],
                'normals': geometry['normals'],
                'tangents': geometry['tangents'],
                'uv': geometry['uv'],
                'uv2': geometry['uv2'],
                'albedo_uv_set': albedo_uv_set(part['role'], geometry, materials_out),
                'colors': geometry['colors'],
                'triangles': geometry['triangles'],
                **{k: v for k, v in binding.items()
                   if k != 'source_face_materials'
                   and k not in ('local_position', 'local_rotation', 'local_scale')},
                'materials': materials_out,
            })
        else:
            row = {
                'enabled': True,
                'role': part['role'],
                'path': f'slime{species}/{part['role']}_{part['mesh_name']}',
                'local_position': trs.get('local_position', [0, 0, 0]),
                'local_rotation': trs.get('local_rotation', [0, 0, 0, 1]),
                'local_scale': trs.get('local_scale', [1, 1, 1]),
                'mesh_name': part['mesh_name'],
                'vertices': geometry['vertices'],
                'normals': geometry['normals'],
                'tangents': geometry['tangents'],
                'uv': geometry['uv'],
                'uv2': geometry['uv2'],
                'albedo_uv_set': albedo_uv_set(part['role'], geometry, materials_out),
                'colors': geometry['colors'],
                'triangles': geometry['triangles'],
                'bind_poses': geometry['bind_poses'],
                'bone_weights': geometry['bone_weights'],
                **{k: v for k, v in binding.items()
                   if k != 'source_face_materials' and k not in trs},
                'materials': materials_out,
            }
            skinned_renderers.append(row)

    document = {
        'schema': SCHEMA,
        'schema_version': SCHEMA_VERSION,
        'source_game': 'SR2',
        'source_kind': 'offline-unitypy',
        'reference_id': f'SlimeDefinition.{species}',
        'display_name': species,
        'prefab_name': f'slime{species}',
        'prefab_scale': 1.0,
        'utc': started,
        'visual_root': '',
        'visual_transform': (mesh_scope['visual_transform'] if appearance_mesh_only
                             else appearance['visual_transform']),
        'skeleton': {'bone_count': len(bones), 'bones': bones},
        'skinned_renderers': skinned_renderers,
        'static_renderers': static_renderers,
    }
    for row in skinned_renderers + static_renderers:
        for slot, material in enumerate(row.get('materials', [])):
            substitute = DISPLAY_SUBSTITUTES.get(material.get('shader'))
            if substitute is not None:
                row['materials'][slot] = dict(material, **substitute, source_shader=material.get('shader'))
    if appearance_mesh_only:
        # Reader provenance only: components beyond the mesh scope are explicitly unported.
        document['source_extraction_scope'] = mesh_scope['extraction_scope']
        effects = []
        for effect in mesh_scope.get('effects', []):
            shell = shell_paths.get(effect['parent'])
            if shell is not None:
                effects.append({k: v for k, v in dict(effect, parent_path=shell).items() if k != 'parent'})
        if effects:
            document['effects'] = effects       # rebuilt by the destination with its own effects (lights, particles, glow)

    extra_files = []
    icon = None
    mesh_only_animation = appearance_mesh_only and clip_ids is not None
    if complete_source or mesh_only_animation:
        # Keep source-derived content deterministic; timestamps belong only to
        # publication metadata, not the model/receipt/descriptor payload hashes.
        document.pop('utc')
        from bridge.content.sr2_source_cache import extract_source_animation
        from bridge.content.sr2_icon import export_source_icon
        document['source_animation'], provenance = extract_source_animation(
            assets, resolved, bones, clip_ids, default_clip)
        provenance['source_part_material_ids'] = {p['mesh_name']: p['source_material_ids']
                                                  for p in (mesh_scope['parts'] if appearance_mesh_only else parts)}
        if appearance_mesh_only:
            provenance['limitations'] = list(provenance['limitations']) + ['mesh-only scope: skinned body/face + rigid attachments, no particles or native behaviours']
        icon = export_source_icon(assets, resolved, out_dir)
        provenance['source_files'] = {str(data_dir / name): _sha256(data_dir / name)
            for name in ('resources.assets', 'resources.assets.resS') if (data_dir / name).is_file()}
        for name, payload in [('icon-descriptor.json', icon), ('source-animation-provenance.json', provenance)]:
            (out_dir / name).write_text(json.dumps(payload, indent=2, allow_nan=False), encoding='utf-8')
            extra_files.append(name)
        extra_files.append(icon['file'])

    model_path = out_dir / 'model.json'
    refuse_existing_leaf(model_path)
    model_path.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')

    renderers = skinned_renderers + static_renderers
    vertex_total = sum(len(r['vertices']) for r in renderers)
    receipt = {
        'schema': 'bridge-native-mesh-receipt',
        'schema_version': 2,
        'target': species,
        'reference_id': f'SlimeDefinition.{species}',
        'skinned_renderers': len(skinned_renderers),
        'static_renderers': len(static_renderers),
        'model': model_path.name,
        'model_sha256': _sha256(model_path),
        'vertex_total': vertex_total,
        'parts': [{'role': r['role'], 'mesh': r['mesh_name'], 'vertices': len(r['vertices'])} for r in renderers],
        'materials': [r['materials'][0]['name'] if r['materials'] else None for r in renderers],
        'textures': [],
        'source': {'data_dir': str(data_dir), 'kind': 'offline-unitypy'},
    }
    for file_name in texture_files:
        path = out_dir / file_name
        receipt['textures'].append({'file': file_name, 'sha256': _sha256(path)})
    receipt_path = out_dir / 'dump-receipt.json'
    refuse_existing_leaf(receipt_path)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')

    files = list(dict.fromkeys([model_path.name, receipt_path.name] + texture_files + extra_files))
    manifest = {
        'schema': CACHE_SCHEMA,
        'schema_version': CACHE_SCHEMA_VERSION,
        'canonical_id': canonical,
        'converter_version': CONVERTER_VERSION,
        'source_game': 'SR2',
        'source_run_id': f'offline-{started}',
        'files': {name: _sha256(out_dir / name) for name in files},
        'published_utc': started,
    }
    if icon is not None:
        manifest['icon'] = icon
    manifest_path = out_dir / 'cache-manifest.json'
    refuse_existing_leaf(manifest_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    summary = {
        'ok': True,
        'canonical_id': canonical,
        'cache_directory': str(out_dir),
        'parts': receipt['parts'],
        'vertex_total': vertex_total,
        'texture_count': len(texture_files),
        'materials': receipt['materials'],
        'static_renderers': len(static_renderers),
    }
    if appearance_mesh_only:
        summary['extraction_scope'] = mesh_scope['extraction_scope']
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--species', required=True, help='species name as in the asset library, e.g. Ringtail')
    parser.add_argument('--canonical', default=None, help='canonical id, e.g. slime.ringtail (derived if omitted)')
    parser.add_argument('--sr2-dir', default=str(DEFAULT_SR2_DATA), help='path to SlimeRancher2_Data')
    parser.add_argument('--out', default=None, help='cache output directory (default Lab/AssetCache/sr2-to-sr1/<canonical>)')
    parser.add_argument('--allow-nonlab', action='store_true', help='permit paths outside the repository Lab tree (developer override)')
    parser.add_argument('--complete-source', action='store_true', help='fresh private converter-v4 cache with actual Sprite and explicit source clips; no Animator graph reconstruction')
    parser.add_argument('--appearance-mesh-only', action='store_true', help='opt-in LOD0 mesh-scope cache (skinned body/face + bone-attached static parts); requires a fresh explicit --out, never complete-source')
    parser.add_argument('--clip-id', type=int, action='append', dest='clip_ids', help='repeat for each exact controller/appearance-referenced AnimationClip path ID (max 16)')
    parser.add_argument('--default-clip', help='exact authored clip name to play by default (explicit, never inferred)')
    args = parser.parse_args(argv)
    try:
        result = extract_species(args.species, args.canonical, Path(args.sr2_dir),
                                 Path(args.out) if args.out else None,
                                 allow_nonlab=args.allow_nonlab, complete_source=args.complete_source,
                                 appearance_mesh_only=args.appearance_mesh_only,
                                 clip_ids=args.clip_ids, default_clip=args.default_clip)
    except ValueError as ex:
        print(json.dumps({'ok': False, 'error': str(ex)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
