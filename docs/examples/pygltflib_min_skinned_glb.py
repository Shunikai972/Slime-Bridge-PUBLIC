"""Minimal, Khronos-valid GLB writer example for the SR2 -> SR1 transport pipeline.

Writes a skinned + animated + textured GLB from already-decoded arrays -- exactly the
shape of data the pipeline has after UnityPy decodes an SR2 asset:
  positions/normals/uvs/joints/weights/indices  +  bone hierarchy + inverse bind
  matrices (IBMs)  +  animation curves  +  embedded PNG texture.

Uses pygltflib (>= 1.16.5) and numpy only. The produced file passed the official
Khronos glTF-Validator (2.0.0-dev.3.10) with 0 errors and 0 warnings.

Key gotchas encoded below:
  * accessors used by vertex attributes need byte-aligned bufferViews (pad to 4 bytes)
  * POSITION accessors need min/max; animation input accessors need min/max
  * unused JOINTS_0 slots must be 0 where the weight is 0 (else ACCESSOR_JOINTS_USED_ZERO_WEIGHT)
  * matrices must be written column-major (transpose before .tobytes())
  * GLB binary blob is attached with gltf.set_binary_blob(...) + gltf.save_binary(...)
  * pygltflib does NOT validate; run the Khronos validator in CI
"""
import struct
import zlib

import numpy as np
from pygltflib import (
    GLTF2, Accessor, Animation, AnimationChannel, AnimationChannelTarget,
    AnimationSampler, Asset, Attributes, Buffer, BufferView, Image, Material,
    Mesh, Node, PbrMetallicRoughness, Primitive, Sampler, Scene, Skin, Texture,
    TextureInfo,
)

OUT = "skinned_animated.glb"

# ---------------------------------------------------------------- decoded arrays
positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32)
normals = np.array([[0, 0, 1], [0, 0, 1], [0, 0, 1]], dtype=np.float32)
uvs = np.array([[0, 0], [1, 0], [0, 1]], dtype=np.float32)
joints = np.array([[0, 1, 0, 0], [0, 1, 0, 0], [1, 0, 0, 0]], dtype=np.uint16)
weights = np.array([[0.7, 0.3, 0, 0], [0.5, 0.5, 0, 0], [1.0, 0, 0, 0]], dtype=np.float32)
indices = np.array([0, 1, 2], dtype=np.uint16)
joints = np.where(weights > 0, joints, 0).astype(np.uint16)  # validator: zero weight => joint 0

# bone bind-world matrices -> inverse bind matrices (glTF stores them column-major)
bind_world = np.stack([
    np.eye(4, dtype=np.float64),
    np.array([[1, 0, 0, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float64),
])
ibms = np.linalg.inv(bind_world).astype(np.float32)          # (2, 4, 4)
ibm_bytes = ibms.transpose(0, 2, 1).tobytes()                # -> column-major

# animation: rotate bone 1 around Z by 0/90/180 deg at t = 0/0.5/1.0
times = np.array([0.0, 0.5, 1.0], dtype=np.float32)
angles = np.array([0.0, np.pi / 2, np.pi])
quats = np.stack([
    np.zeros(3, dtype=np.float32), np.zeros(3, dtype=np.float32),
    np.sin(angles / 2).astype(np.float32), np.cos(angles / 2).astype(np.float32),
], axis=1)                                                   # (3, 4) xyzw


def make_png(w=2, h=2, rgba=(255, 0, 0, 255)):
    """Generate a minimal valid PNG without extra dependencies."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


png_bytes = make_png()

# ---------------------------------------------------- pack the binary chunk (4B aligned)
blob = bytearray()
layout = {}


def add(name, arr_bytes):
    blob.extend(b"\x00" * ((-len(blob)) % 4))
    layout[name] = (len(blob), len(arr_bytes))
    blob.extend(arr_bytes)
    return layout[name]


add("POSITION", positions.tobytes())
add("NORMAL", normals.tobytes())
add("TEXCOORD_0", uvs.tobytes())
add("JOINTS_0", joints.tobytes())
add("WEIGHTS_0", weights.tobytes())
add("INDICES", indices.tobytes())
add("IBM", ibm_bytes)
add("ANIM_INPUT", times.tobytes())
add("ANIM_OUTPUT", quats.tobytes())
add("IMAGE", png_bytes)


def view(name, target=None):
    off, ln = layout[name]
    return BufferView(buffer=0, byteOffset=off, byteLength=ln, target=target)


buffer_views = [
    view("POSITION", 34962), view("NORMAL", 34962), view("TEXCOORD_0", 34962),
    view("JOINTS_0", 34962), view("WEIGHTS_0", 34962), view("INDICES", 34963),
    view("IBM"), view("ANIM_INPUT"), view("ANIM_OUTPUT"), view("IMAGE"),
]

accessors = [
    Accessor(bufferView=0, componentType=5126, count=3, type="VEC3",
             min=positions.min(axis=0).tolist(), max=positions.max(axis=0).tolist()),
    Accessor(bufferView=1, componentType=5126, count=3, type="VEC3"),
    Accessor(bufferView=2, componentType=5126, count=3, type="VEC2"),
    Accessor(bufferView=3, componentType=5123, count=3, type="VEC4"),
    Accessor(bufferView=4, componentType=5126, count=3, type="VEC4"),
    Accessor(bufferView=5, componentType=5123, count=3, type="SCALAR"),
    Accessor(bufferView=6, componentType=5126, count=2, type="MAT4"),
    Accessor(bufferView=7, componentType=5126, count=3, type="SCALAR",
             min=[float(times.min())], max=[float(times.max())]),
    Accessor(bufferView=8, componentType=5126, count=3, type="VEC4"),
]

gltf = GLTF2(
    asset=Asset(version="2.0", generator="pygltflib-minimal-example"),
    scene=0,
    scenes=[Scene(name="Scene", nodes=[0, 2])],
    nodes=[
        Node(name="Armature", children=[1]),
        Node(name="Bone_1", translation=[0.0, 1.0, 0.0]),
        Node(name="SkinnedMesh", mesh=0, skin=0),
    ],
    meshes=[Mesh(name="Tri", primitives=[Primitive(
        attributes=Attributes(POSITION=0, NORMAL=1, TEXCOORD_0=2, JOINTS_0=3, WEIGHTS_0=4),
        indices=5, material=0)])],
    skins=[Skin(name="Rig", inverseBindMatrices=6, skeleton=0, joints=[0, 1])],
    animations=[Animation(
        name="Spin",
        samplers=[AnimationSampler(input=7, output=8, interpolation="LINEAR")],
        channels=[AnimationChannel(sampler=0,
                                   target=AnimationChannelTarget(node=1, path="rotation"))])],
    materials=[Material(
        name="RedMat",
        pbrMetallicRoughness=PbrMetallicRoughness(
            baseColorTexture=TextureInfo(index=0), metallicFactor=0.0, roughnessFactor=1.0),
        doubleSided=True)],
    textures=[Texture(source=0, sampler=0)],
    images=[Image(bufferView=9, mimeType="image/png")],
    samplers=[Sampler(magFilter=9729, minFilter=9987, wrapS=10497, wrapT=10497)],
    accessors=accessors, bufferViews=buffer_views,
    buffers=[Buffer(byteLength=len(blob))],
)

gltf.set_binary_blob(bytes(blob))
gltf.save_binary(OUT)

# ---------------------------------------------------------------- self-verify
g2 = GLTF2().load_binary(OUT)
assert g2.skins[0].joints == [0, 1]
assert g2.skins[0].inverseBindMatrices == 6
assert len(g2.animations) == 1 and len(g2.animations[0].channels) == 1
assert g2.textures[0].source == 0 and g2.images[0].mimeType == "image/png"
assert g2.meshes[0].primitives[0].attributes.JOINTS_0 == 3

import os
print(f"OK wrote {OUT}: {os.path.getsize(OUT)} bytes; "
      f"skins={len(g2.skins)} joints={g2.skins[0].joints} "
      f"animations={len(g2.animations)} (channels={len(g2.animations[0].channels)}) "
      f"textures={len(g2.textures)} images={[(i.mimeType, i.bufferView) for i in g2.images]}")
