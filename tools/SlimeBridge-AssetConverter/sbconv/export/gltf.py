"""IR package -> GLB (geometry, skeleton, skins, animations) for inspection, DCC import and as a portable
transport. Material *behaviour* is not representable in glTF: each glTF material carries the full source
material in ``extras`` (sbconv.material) and a plain base colour for previews. Unity space (left-handed)
is converted with the UniGLTF 'ReverseZ' convention; ``read_back`` undoes it for round-trip tests."""

import json
from pathlib import Path

import numpy as np
from pygltflib import (GLTF2, Scene, Node, Mesh, Primitive, Attributes, Buffer, BufferView, Accessor, Skin, Material,
                       PbrMetallicRoughness, Animation, AnimationChannel, AnimationChannelTarget, AnimationSampler,
                       Image, Texture, TextureInfo, Asset)

from ..ir.curves import eval_vector, sample_times
from ..ir.store import unpack_mesh

F32, U16, U32 = 5126, 5123, 5125


def z(v):  # position / normal / scale-free vectors (N,3)
    a = np.array(v, dtype=np.float32, copy=True)
    a[..., 2] *= -1
    return a


def quat_z(q):
    a = np.array(q, dtype=np.float32, copy=True)
    a[..., 0] *= -1
    a[..., 1] *= -1
    return a


S = np.diag([1.0, 1.0, -1.0, 1.0])


class _Buf:
    def __init__(self):
        self.data = bytearray()
        self.views, self.accessors = [], []

    def add(self, arr, ctype, atype, target=None, minmax=False):
        arr = np.ascontiguousarray(arr)
        while len(self.data) % 4:
            self.data.append(0)
        self.views.append(BufferView(buffer=0, byteOffset=len(self.data), byteLength=arr.nbytes, target=target))
        self.data += arr.tobytes()
        acc = Accessor(bufferView=len(self.views) - 1, componentType=ctype, count=int(arr.shape[0]), type=atype)
        if minmax:
            acc.min = [float(x) for x in arr.reshape(arr.shape[0], -1).min(axis=0)]
            acc.max = [float(x) for x in arr.reshape(arr.shape[0], -1).max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def _base_color(mat, palette):
    c = mat['colors'].get('_Color') or mat['colors'].get('_BaseColor') or [1, 1, 1, 1]
    if palette and (mat['colors'].get('_MiddleColor') is not None):
        c = mat['colors']['_MiddleColor']
    return [float(x) for x in c[:3]] + [1.0]


def export_glb(pkg_dir, out_path, lod=0, fps=30.0, with_animation=True):
    pkg = Path(pkg_dir)
    m = json.loads((pkg / 'species.json').read_text(encoding='utf-8'))
    nodes_ir = m['prefab']['nodes']
    root = m['prefab']['appearance_root']
    g = GLTF2(asset=Asset(generator='sbconv', version='2.0'))
    buf = _Buf()
    node_of = {}

    def make_node(i):
        n = nodes_ir[i]
        t = n['transform'] or {'position': [0, 0, 0], 'rotation': [0, 0, 0, 1], 'scale': [1, 1, 1]}
        gn = Node(name=n['name'], translation=z(t['position']).tolist(), rotation=quat_z(t['rotation']).tolist(), scale=list(map(float, t['scale'])))
        if i == root:
            gn.translation, gn.rotation, gn.scale = [0, 0, 0], [0, 0, 0, 1], [1, 1, 1]
        g.nodes.append(gn)
        node_of[i] = len(g.nodes) - 1
        gn.children = [make_node(c) for c in n['children']]
        return node_of[i]
    root_idx = make_node(root)
    g.scenes = [Scene(nodes=[root_idx])]
    g.scene = 0

    mat_index, tex_index = {}, {}

    def gmat(mat, palette=True):
        key = mat['ref']
        if key in mat_index:
            return mat_index[key]
        pm = PbrMetallicRoughness(baseColorFactor=_base_color(mat, palette), metallicFactor=0.0, roughnessFactor=0.7)
        gm = Material(name=mat['name'], pbrMetallicRoughness=pm, alphaMode='OPAQUE', doubleSided=True,
                      extras={'sbconv.material': {'shader': mat['shader'], 'keywords': mat['keywords'], 'floats': mat['floats'],
                                                  'colors': mat['colors'], 'textures': {k: v.get('file') for k, v in mat['textures'].items() if v.get('file')}}})
        for prop in ('_MainTex', '_Diffuse', '_BaseColorMap'):
            t = mat['textures'].get(prop)
            if t and t.get('file'):
                f = t['file']
                if f not in tex_index:
                    data = (pkg / f).read_bytes()
                    buf.views.append(BufferView(buffer=0, byteOffset=_pad(buf), byteLength=len(data)))
                    buf.data += data
                    g.images.append(Image(bufferView=len(buf.views) - 1, mimeType='image/png', name=Path(f).stem))
                    g.textures.append(Texture(source=len(g.images) - 1))
                    tex_index[f] = len(g.textures) - 1
                pm.baseColorTexture = TextureInfo(index=tex_index[f])
                pm.baseColorFactor = [1, 1, 1, 1]
                break
        g.materials.append(gm)
        mat_index[key] = len(g.materials) - 1
        return mat_index[key]

    for part in m['appearance']['parts']:
        lod_rec = next((l for l in part['lods'] if l['lod'] == lod), None) or (part['lods'][0] if part['lods'] else None)
        if not lod_rec:
            continue
        mats = lod_rec.get('override_materials') or part['default_materials']
        for r in lod_rec['renderers']:
            me = r.get('mesh')
            if not me:
                continue
            hdr, arrs = unpack_mesh((pkg / me['file']).read_bytes())
            attr = Attributes()
            attr.POSITION = buf.add(z(arrs['positions']), F32, 'VEC3', 34962, True)
            if 'normals' in arrs:
                attr.NORMAL = buf.add(z(arrs['normals']), F32, 'VEC3', 34962)
            if 'tangents' in arrs:
                t = np.array(arrs['tangents'], dtype=np.float32, copy=True)
                t[:, 2] *= -1
                t[:, 3] *= -1
                attr.TANGENT = buf.add(t, F32, 'VEC4', 34962)
            for ch in range(2):
                if f'uv{ch}' in arrs:
                    uv = np.array(arrs[f'uv{ch}'], dtype=np.float32, copy=True)
                    uv[:, 1] = 1.0 - uv[:, 1]
                    setattr(attr, f'TEXCOORD_{ch}', buf.add(uv, F32, 'VEC2', 34962))
            if 'colors' in arrs:
                attr.COLOR_0 = buf.add(arrs['colors'].astype(np.float32), F32, 'VEC4', 34962)
            skinned = r['type'] == 'SkinnedMeshRenderer' and 'bone_indices' in arrs and lod_rec['attached_bones'] and all(b is not None for b in lod_rec['attached_bones'])
            if skinned:
                attr.JOINTS_0 = buf.add(arrs['bone_indices'].astype(np.uint16), U16, 'VEC4', 34962)
                attr.WEIGHTS_0 = buf.add(arrs['bone_weights'].astype(np.float32), F32, 'VEC4', 34962)
            prims = []
            for si, sm in enumerate(hdr['submeshes']):
                if sm['topology'] != 0:
                    continue
                idx = arrs[sm['index_array']].astype(np.uint32).reshape(-1, 3)[:, [0, 2, 1]].reshape(-1)  # flip winding
                mat = mats[min(si, len(mats) - 1)] if mats else None
                prims.append(Primitive(attributes=attr, indices=buf.add(idx, U32, 'SCALAR', 34963),
                                       material=gmat(mat) if mat else None, mode=4))
            g.meshes.append(Mesh(name=r['node'], primitives=prims, extras={'sbconv.part': part['element'], 'sbconv.lod': lod_rec['lod']}))
            nd = Node(name=f"{part['element']}:{r['node']}", mesh=len(g.meshes) - 1)
            g.nodes.append(nd)
            g.nodes[root_idx].children.append(len(g.nodes) - 1)
            if skinned:
                ibm = np.stack([S @ b @ S for b in arrs['bind_poses'].astype(np.float64)]).astype(np.float32)
                ibm = np.ascontiguousarray(np.transpose(ibm, (0, 2, 1)))  # glTF is column-major
                g.skins.append(Skin(name=part['element'], joints=[node_of[b] for b in lod_rec['attached_bones']], skeleton=root_idx,
                                    inverseBindMatrices=buf.add(ibm, F32, 'MAT4')))
                nd.skin = len(g.skins) - 1

    if with_animation:
        base = nodes_ir[root]['path']
        path_node = {n['path'][len(base) + 1:]: node_of[i] for i, n in enumerate(nodes_ir) if i in node_of and n['path'].startswith(base + '/')}
        for asset, meta in m['animation']['clips'].items():
            clip = json.loads((pkg / meta['file']).read_text(encoding='utf-8'))
            anim = Animation(name=clip['name'], extras={'sbconv.loop': clip['loop'], 'sbconv.events': clip['events']})
            length = max(clip['length'], 1e-3)
            for grp, prop, width in (('positions', 'translation', 3), ('rotations', 'rotation', 4), ('scales', 'scale', 3)):
                for c in clip[grp]:
                    if c['path'] not in path_node or not c['keys']:
                        continue
                    ts = sample_times(length, fps, c['keys'])
                    vals = eval_vector(c['keys'], ts, width).astype(np.float32)
                    if prop == 'translation':
                        vals = z(vals)
                    elif prop == 'rotation':
                        vals = quat_z(vals)
                        vals /= np.maximum(np.linalg.norm(vals, axis=1, keepdims=True), 1e-9)
                    si = len(anim.samplers)
                    anim.samplers.append(AnimationSampler(input=buf.add(ts.astype(np.float32), F32, 'SCALAR', None, True),
                                                          output=buf.add(vals, F32, 'VEC3' if width == 3 else 'VEC4'), interpolation='LINEAR'))
                    anim.channels.append(AnimationChannel(sampler=si, target=AnimationChannelTarget(node=path_node[c['path']], path=prop)))
            if anim.channels:
                g.animations.append(anim)

    g.bufferViews, g.accessors = buf.views, buf.accessors
    g.buffers = [Buffer(byteLength=len(buf.data))]
    g.set_binary_blob(bytes(buf.data))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out))
    return {'nodes': len(g.nodes), 'meshes': len(g.meshes), 'skins': len(g.skins), 'animations': len(g.animations),
            'materials': len(g.materials), 'bytes': out.stat().st_size}


def _pad(buf):
    while len(buf.data) % 4:
        buf.data.append(0)
    return len(buf.data)


def read_back(glb_path):
    """Round-trip helper: rest-pose skinned positions per mesh node, converted back to Unity space."""
    g = GLTF2().load_binary(str(glb_path))
    blob = g.binary_blob()

    def acc(i):
        a = g.accessors[i]
        bv = g.bufferViews[a.bufferView]
        comps = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}[a.type]
        dt = {5126: np.float32, 5123: np.uint16, 5125: np.uint32}[a.componentType]
        return np.frombuffer(blob, dtype=dt, count=a.count * comps, offset=(bv.byteOffset or 0) + (a.byteOffset or 0)).reshape(a.count, comps)
    return g, acc
