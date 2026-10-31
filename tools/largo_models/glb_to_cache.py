"""Blender (headless) half of the largo model adapter: one finished GLB -> one SlimeBridge model cache both games load.

    blender -b --factory-startup --python tools/largo_models/glb_to_cache.py -- <model.glb> <out_dir> <canonical> [--tris 14000] [--tex 1024]

The source GLB is only read. The cache is the format the plugins already load for imported largos (bridge-native-mesh v2 +
bridge-asset-cache-manifest v1): Unity-space skinned parts (the rig and every clip of the GLB, sampled at 30 Hz), a lighter
in-game mesh (decimated to --tris triangles in total, UVs and weights kept), the base-colour textures downscaled to --tex,
a front 3/4 icon rendered from the model itself. Materials use the shader name "SlimeBridge/Baked": both plugins draw them
with a textured lit material instead of repainting them with a slime palette.
Clip names the games react to: Idle -> "Idle v4" (default), Jump -> "Pounce v4", Feed/Chomp -> "Bite v4".
"""
import hashlib
import json
import math
import os
import re
import sys

import bpy
import bmesh
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index('--') + 1:]
SRC, OUT, CANONICAL = argv[0], argv[1], argv[2]
TRIS = int(argv[argv.index('--tris') + 1]) if '--tris' in argv else 14000
TEX = int(argv[argv.index('--tex') + 1]) if '--tex' in argv else 1024
YAW = float(argv[argv.index('--yaw') + 1]) if '--yaw' in argv else 0.0     # degrees about the up axis: turns the face to the front
RATE = 30.0
GAME_CLIPS = {'idle': 'Idle v4', 'jump': 'Pounce v4', 'feed': 'Bite v4', 'chomp': 'Bite v4', 'eat': 'Bite v4'}
os.makedirs(OUT, exist_ok=True)
report = {'source': os.path.basename(SRC), 'yaw': YAW, 'skipped_parts': [], 'decimated': {}, 'clips': {}}

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)
scene = bpy.context.scene
fps = scene.render.fps / scene.render.fps_base

# ---- what is in the file ---------------------------------------------------------------------------------------------
shapes = {pb.custom_shape for o in bpy.data.objects if o.type == 'ARMATURE' for pb in o.pose.bones if pb.custom_shape}
for o in list(bpy.data.objects):
    if o in shapes or (o.type == 'MESH' and o.parent is None and not o.data.materials and o.name.startswith('Icosphere')):
        bpy.data.objects.remove(o, do_unlink=True)
armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']


def skinned_to(o):
    for m in o.modifiers:
        if m.type == 'ARMATURE' and m.object is not None:
            return m.object
    return None


def safe(name):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:60] or 'node'


def transparent_fx(o):
    """Effect shells (FX_* auras, motes, halos; alpha-blended parts without a colour texture): drawn opaque in game they would
    hide the slime or float as solid blobs."""
    if o.name.upper().startswith('FX'):
        return True
    mats = [m for m in o.data.materials if m]
    if not mats:
        return False
    for m in mats:
        if getattr(m, 'surface_render_method', 'DITHERED') != 'BLENDED' and getattr(m, 'blend_method', 'OPAQUE') != 'BLEND':
            return False
        if base_image(m) is not None:
            return False
    return True


def bsdf_of(m):
    if not m or not m.use_nodes:
        return None
    return next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)


def upstream_image(socket, depth=0):
    if socket is None or not socket.is_linked or depth > 8:
        return None
    node = socket.links[0].from_node
    if node.type == 'TEX_IMAGE' and node.image:
        return node.image
    for inp in node.inputs:
        if inp.type in ('RGBA', 'VALUE', 'VECTOR') and inp.is_linked and inp.name not in ('Vector', 'UV'):
            found = upstream_image(inp, depth + 1)
            if found:
                return found
    return None


def base_image(m):
    b = bsdf_of(m)
    return upstream_image(b.inputs['Base Color']) if b else None


def emission_image(m):
    b = bsdf_of(m)
    if not b:
        return None
    sock = b.inputs.get('Emission Color') or b.inputs.get('Emission')
    return upstream_image(sock)


def base_factor(m):
    b = bsdf_of(m)
    if b and not b.inputs['Base Color'].is_linked:
        c = list(b.inputs['Base Color'].default_value)
        alpha = b.inputs['Alpha'].default_value if 'Alpha' in b.inputs else 1.0
        return [c[0], c[1], c[2], alpha]
    return [1.0, 1.0, 1.0, 1.0] if b else list(m.diffuse_color) if m else [1, 1, 1, 1]


meshes = []
for o in bpy.data.objects:
    if o.type != 'MESH' or not o.data.polygons:
        continue
    if transparent_fx(o):
        report['skipped_parts'].append(o.name + ' (transparent effect shell)')
        continue
    meshes.append(o)
if not meshes:
    raise SystemExit('no visible mesh')

# ---- nodes: armature objects + their bones + every non-skinned object a mesh hangs on -----------------------------
nodes = []          # dicts: name, kind ('bone'|'object'), ref, parent index
index_of = {}


def add_node(key, name, kind, ref, parent):
    base, n = safe(name), 1
    taken = {x['name'] for x in nodes}
    final = base
    while final in taken:
        n += 1
        final = f'{base}_{n}'
    index_of[key] = len(nodes)
    nodes.append({'name': final, 'kind': kind, 'ref': ref, 'parent': parent})
    return index_of[key]


def ensure_object_node(o):
    key = ('obj', o.name)
    if key in index_of:
        return index_of[key]
    parent = -1
    if o.parent is not None:
        if o.parent_type == 'BONE' and o.parent.type == 'ARMATURE' and o.parent_bone:
            ensure_object_node(o.parent)
            parent = index_of[('bone', o.parent.name, o.parent_bone)]
        else:
            parent = ensure_object_node(o.parent)
    idx = add_node(key, o.name, 'object', o, parent)
    if o.type == 'ARMATURE':
        for b in o.data.bones:                       # parents come before children in this list
            p = index_of[('bone', o.name, b.parent.name)] if b.parent else idx
            add_node(('bone', o.name, b.name), b.name, 'bone', (o, b.name), p)
    return idx


for a in armatures:
    ensure_object_node(a)
for o in meshes:
    if skinned_to(o) is None:
        ensure_object_node(o)
    elif o.parent is not None:
        ensure_object_node(o.parent)

# ---- Unity space ---------------------------------------------------------------------------------------------------
C = Matrix(((-1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))       # Blender (x, y, z) -> Unity (-x, z, -y)
CI = C.inverted()
C = C @ Matrix.Rotation(math.radians(YAW), 4, 'Z')
CI = C.inverted()
C3 = C.to_3x3()


def node_world(n):
    if n['kind'] == 'bone':
        arm, name = n['ref']
        return arm.matrix_world @ arm.pose.bones[name].matrix
    return n['ref'].matrix_world.copy()


def rest_world(n):
    if n['kind'] == 'bone':
        arm, name = n['ref']
        return arm.matrix_world @ arm.data.bones[name].matrix_local
    return n['ref'].matrix_world.copy()


def clear_animation():
    for o in bpy.data.objects:
        if o.animation_data:
            o.animation_data.action = None
        if o.type == 'ARMATURE':
            for pb in o.pose.bones:
                pb.location = (0, 0, 0)
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.rotation_euler = (0, 0, 0)
                pb.scale = (1, 1, 1)
    scene.frame_set(0)


actions_by_object = {}
for o in bpy.data.objects:
    ad = o.animation_data
    if ad:
        names = set()
        for t in ad.nla_tracks:
            for s in t.strips:
                if s.action:
                    names.add(s.action.name)
        if ad.action:
            names.add(ad.action.name)
        actions_by_object[o.name] = names
        for t in ad.nla_tracks:
            t.mute = True
clear_animation()

# ---- lighter in-game mesh ------------------------------------------------------------------------------------------
for o in meshes:                      # morph targets cannot travel to the games: keep the base shape
    if o.data.shape_keys:
        report.setdefault('shape_keys_dropped', []).append(o.name)
        o.shape_key_clear()
total = sum(len(o.data.polygons) for o in meshes)
tri_counts = {}
for o in meshes:
    o.data.calc_loop_triangles()
    tri_counts[o.name] = len(o.data.loop_triangles)
total_tris = sum(tri_counts.values())
if total_tris > TRIS:
    for o in meshes:
        share = max(400, TRIS * tri_counts[o.name] / total_tris)
        ratio = min(1.0, share / max(1, tri_counts[o.name]))
        if ratio < 0.98:
            bpy.context.view_layer.objects.active = o
            for other in bpy.context.selected_objects:
                other.select_set(False)
            o.select_set(True)
            mod = o.modifiers.new('sb_decimate', 'DECIMATE')
            mod.decimate_type = 'COLLAPSE'
            mod.ratio = ratio
            mod.use_collapse_triangulate = True
            while o.modifiers[0] != mod:
                bpy.ops.object.modifier_move_up(modifier=mod.name)
            bpy.ops.object.modifier_apply(modifier=mod.name)
            o.data.calc_loop_triangles()
            report['decimated'][o.name] = [tri_counts[o.name], len(o.data.loop_triangles)]

# ---- normalisation: the slime fits a 1-unit box, feet on y=0, centred (visual_transform then lowers it by 0.5 like the
# extracted largos) ---------------------------------------------------------------------------------------------------
lo, hi = Vector((1e9, 1e9, 1e9)), Vector((-1e9, -1e9, -1e9))
for o in meshes:
    mw = o.matrix_world
    vs = o.data.vertices
    for vi in range(0, len(vs), max(1, len(vs) // 4000)):
        p = C @ (mw @ vs[vi].co)
        for i in range(3):
            lo[i], hi[i] = min(lo[i], p[i]), max(hi[i], p[i])
ext = hi - lo
k = 1.0 / max(ext.x, ext.y, ext.z)
centre = Vector(((lo.x + hi.x) / 2, lo.y, (lo.z + hi.z) / 2))
N = Matrix.Diagonal((k, k, k, 1.0)) @ Matrix.Translation(-centre)
report['normalisation'] = {'scale': k, 'source_extent': list(ext)}


def to_unity(m_world):
    return N @ C @ m_world @ CI


def trs(m):
    loc, rot, sca = m.decompose()
    return [loc.x, loc.y, loc.z], [rot.x, rot.y, rot.z, rot.w], [sca.x, sca.y, sca.z]


def round_list(values, nd=6):
    return [round(float(v), nd) for v in values]


rest = [to_unity(rest_world(n)) for n in nodes]


def local(worlds, i):
    p = nodes[i]['parent']
    return worlds[i] if p < 0 else worlds[p].inverted() @ worlds[i]


bones_out = []
for i, n in enumerate(nodes):
    p, q, s = trs(local(rest, i))
    bones_out.append({'name': n['name'], 'parent': n['parent'], 'p': round_list(p), 'q': round_list(q), 's': round_list(s)})
bind_poses = [round_list([m[r][c] for r in range(4) for c in range(4)], 7) for m in (x.inverted() for x in rest)]

# ---- textures: each material's final colour, baked -----------------------------------------------------------------
# The finished materials regrade their source textures with node trees (colour ramps, mixes, measured palettes), so the
# raw image is not the colour the model shows. Cycles bakes what the material really produces onto the game mesh's UVs:
# the base colour after every node (DIFFUSE, colour only) and the light it emits, strength included (EMIT).
def _bake_sizes():
    tris = {}
    for o in meshes:
        o.data.calc_loop_triangles()
        for tri in o.data.loop_triangles:
            m = o.data.materials[tri.material_index] if tri.material_index < len(o.data.materials) else None
            if m is not None:
                tris[m.name] = tris.get(m.name, 0) + 1
    total = max(1, sum(tris.values()))
    return {name: (TEX if n / total > 0.2 else TEX // 2 if n / total > 0.03 else TEX // 4) for name, n in tris.items()}


def _emits(m):
    b = bsdf_of(m)
    if b is None:
        return False
    strength = b.inputs.get('Emission Strength')
    colour = b.inputs.get('Emission Color') or b.inputs.get('Emission')
    if strength is None or colour is None:
        return False
    if not strength.is_linked and strength.default_value <= 0:
        return False
    return colour.is_linked or any(c > 0.001 for c in list(colour.default_value)[:3])


baked = {}            # material name -> {'albedo': file, 'emission': file or None}
bake_report = {}


def bake_all():
    sizes = _bake_sizes()
    mats = [bpy.data.materials[n] for n in sizes if bpy.data.materials.get(n) and bpy.data.materials[n].use_nodes]
    usable = [o for o in meshes if o.data.uv_layers]
    if not mats or not usable:
        return
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.render.bake.margin = 4
    scene.render.bake.use_clear = True
    for o in bpy.context.selected_objects:
        o.select_set(False)
    for o in usable:
        o.select_set(True)
    bpy.context.view_layer.objects.active = usable[0]

    def run(kind, wanted):
        nodes = {}
        for m in wanted:
            size = sizes[m.name]
            img = bpy.data.images.new(f'sb_{kind}_{m.name}', size, size, alpha=False)
            node = m.node_tree.nodes.new('ShaderNodeTexImage')
            node.image = img
            for other in m.node_tree.nodes:
                other.select = False
            node.select = True
            m.node_tree.nodes.active = node
            nodes[m.name] = (node, img)
        # materials not baked in this pass still need an active image node that is not one of their own textures
        for m in mats:
            if m.name not in nodes:
                dummy = bpy.data.images.new(f'sb_skip_{m.name}', 8, 8)
                node = m.node_tree.nodes.new('ShaderNodeTexImage')
                node.image = dummy
                m.node_tree.nodes.active = node
                nodes['skip:' + m.name] = (node, dummy)
        if kind == 'albedo':
            bpy.ops.object.bake(type='DIFFUSE', pass_filter={'COLOR'}, margin=4, use_clear=True)
        else:
            bpy.ops.object.bake(type='EMIT', margin=4, use_clear=True)
        out = {}
        for key, (node, img) in nodes.items():
            if not key.startswith('skip:'):
                name = f'tex-{len(baked) + len(out):02d}-{kind}-{safe(key)[:28]}.png'
                img.filepath_raw = os.path.join(OUT, name)
                img.file_format = 'PNG'
                img.save()
                out[key] = name
            node.id_data.nodes.remove(node)
        return out

    albedo = run('albedo', mats)
    emitting = [m for m in mats if _emits(m)]
    emission = run('emission', emitting) if emitting else {}
    for m in mats:
        baked[m.name] = {'albedo': albedo.get(m.name), 'emission': emission.get(m.name)}
    bake_report.update({'materials': len(mats), 'emitting': len(emitting), 'sizes': {k: v for k, v in sizes.items()}})


bake_all()
report['bake'] = bake_report


def material_entry(m):
    colour = base_factor(m)
    entry = {'name': safe(m.name if m else 'material'), 'shader': 'SlimeBridge/Baked',
             'colors': {'_Color': round_list(colour, 5), '_TopColor': round_list(colour, 5), '_MiddleColor': round_list(colour, 5),
                        '_BottomColor': round_list(colour, 5)}, 'floats': {}, 'textures': []}
    done = baked.get(m.name) if m else None
    if done and done['albedo']:
        entry['textures'].append({'property': '_MainTex', 'file': done['albedo'], 'source_color_space': 1})
    if done and done['emission']:
        entry['textures'].append({'property': '_EmissionMap', 'file': done['emission'], 'source_color_space': 1})
    return entry


# ---- parts ---------------------------------------------------------------------------------------------------------
parts = []
names_index = {n['name']: i for i, n in enumerate(nodes)}
largest = max(meshes, key=lambda o: len(o.data.loop_triangles))
for o in meshes:
    me = o.data
    me.calc_loop_triangles()
    uv_layer = me.uv_layers.active
    mw = o.matrix_world
    arm = skinned_to(o)
    group_bone = {}
    if arm is not None:
        for vg in o.vertex_groups:
            key = ('bone', arm.name, vg.name)
            if key in index_of:
                group_bone[vg.index] = index_of[key]
        fallback = index_of[('obj', arm.name)]
    else:
        fallback = index_of[('obj', o.name)]
    normals = me.corner_normals if hasattr(me, 'corner_normals') else None
    vert_key, verts, norms, uvs, weights = {}, [], [], [], []
    tris_by_slot = {}
    vweights = {}
    for tri in me.loop_triangles:
        out = []
        for li in tri.loops:
            vi = me.loops[li].vertex_index
            uv = tuple(round(c, 5) for c in uv_layer.data[li].uv) if uv_layer else (0.0, 0.0)
            nv = normals[li].vector if normals is not None else me.loops[li].normal
            nk = (round(nv.x, 3), round(nv.y, 3), round(nv.z, 3))
            key = (vi, uv, nk)
            idx = vert_key.get(key)
            if idx is None:
                idx = len(verts)
                vert_key[key] = idx
                p = N @ (C @ (mw @ me.vertices[vi].co))
                verts.append(round_list(p, 5))
                nn = (C3 @ (mw.to_3x3() @ Vector(nv))).normalized()
                norms.append(round_list(nn, 4))
                uvs.append([uv[0], uv[1]])
                if vi not in vweights:
                    ws = []
                    if arm is not None:
                        for g in me.vertices[vi].groups:
                            if g.group in group_bone and g.weight > 1e-4:
                                ws.append((g.weight, group_bone[g.group]))
                    ws.sort(reverse=True)
                    ws = ws[:4]
                    s = sum(w for w, _ in ws)
                    if s <= 0:
                        ws, s = [(1.0, fallback)], 1.0
                    ws = [(w / s, b) for w, b in ws]
                    while len(ws) < 4:
                        ws.append((0.0, ws[0][1]))
                    wr = [round(w, 6) for w, _ in ws]
                    wr[0] = round(1.0 - sum(wr[1:]), 6)
                    vweights[vi] = wr + [b for _, b in ws]
                weights.append(vweights[vi])
            out.append(idx)
        tris_by_slot.setdefault(tri.material_index, []).extend((out[0], out[2], out[1]))    # mirrored space: reverse winding
    if len(verts) > 100000:
        raise SystemExit(f'{o.name}: {len(verts)} vertices after splitting (limit 100000): lower --tris')
    slots = sorted(tris_by_slot)
    mats = [material_entry(me.materials[s] if s < len(me.materials) else None) for s in slots]
    role = 'body' if o == largest else 'attachment'
    parts.append({'enabled': True, 'role': role, 'path': f'{CANONICAL}/{safe(o.name)}', 'local_position': [0.0, 0.0, 0.0],
                  'local_rotation': [0.0, 0.0, 0.0, 1.0], 'local_scale': [1.0, 1.0, 1.0], 'mesh_name': safe(o.name),
                  'vertices': verts, 'normals': norms, 'uv': uvs, 'uv2': [], 'albedo_uv_set': 'uv', 'colors': [],
                  'triangles': [tris_by_slot[s] for s in slots], 'bind_poses': bind_poses, 'bone_weights': weights,
                  'skin_bones': [n['name'] for n in nodes], 'root_bone': nodes[fallback]['name'], 'materials': mats})

# ---- palette: the body's own colours, top / middle / bottom third (splats, vac and ammo colours; the fallback paint) --
def measure_palette(part):
    import numpy as np
    textured = next((m for m in part['materials'] if m['textures']), None)
    if textured is None:
        c = part['materials'][0]['colors']['_Color']
        return [c, c, c]
    img = bpy.data.images.load(os.path.join(OUT, textured['textures'][0]['file']))
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(h, w, 4)
    bpy.data.images.remove(img)
    verts, uvs = part['verts_np'], part['uv_np']
    ys = verts[:, 1]
    lo_, hi_ = ys.min(), ys.max()
    out = []
    for a, b in ((2 / 3, 1.01), (1 / 3, 2 / 3), (-0.01, 1 / 3)):
        sel = (ys >= lo_ + a * (hi_ - lo_)) & (ys <= lo_ + b * (hi_ - lo_))
        if not sel.any():
            sel = np.ones_like(ys, dtype=bool)
        u = np.clip((uvs[sel, 0] % 1.0) * (w - 1), 0, w - 1).astype(int)
        v = np.clip((uvs[sel, 1] % 1.0) * (h - 1), 0, h - 1).astype(int)
        rgb = px[v, u, :3].mean(axis=0)
        out.append([round(float(x), 4) for x in rgb] + [1.0])
    return out


import numpy as _np
body = next(p_ for p_ in parts if p_['role'] == 'body')
body['verts_np'] = _np.array(body['vertices'], dtype=_np.float32)
body['uv_np'] = _np.array(body['uv'], dtype=_np.float32)
top, middle, bottom = measure_palette(body)
del body['verts_np'], body['uv_np']
for m in body['materials']:
    m['colors'].update({'_TopColor': top, '_MiddleColor': middle, '_BottomColor': bottom})
report['palette'] = {'top': top, 'middle': middle, 'bottom': bottom}

# ---- clips ---------------------------------------------------------------------------------------------------------
clip_names = sorted({a for names in actions_by_object.values() for a in names})
samples = {}
for clip in clip_names:
    action = bpy.data.actions[clip]
    start, end = action.frame_range
    duration = max((end - start) / fps, 1.0 / RATE)
    count = int(math.floor(duration * RATE + 1e-6)) + 1
    times = [round(min(i / RATE, duration), 6) for i in range(count)]
    times = sorted(set(times))
    clear_animation()
    for o in bpy.data.objects:
        if action.name in actions_by_object.get(o.name, ()):
            o.animation_data.action = action
            slots_ = getattr(action, 'slots', None)
            if slots_ and len(slots_):
                match = next((s for s in slots_ if s.identifier.endswith(o.name)), slots_[0])
                try:
                    o.animation_data.action_slot = match
                except Exception:
                    pass
    per_node = [[] for _ in nodes]
    for t in times:
        f = start + t * fps
        scene.frame_set(int(math.floor(f)), subframe=f - math.floor(f))
        worlds = [to_unity(node_world(n)) for n in nodes]
        for i in range(len(nodes)):
            per_node[i].append(trs(local(worlds, i)))
    samples[clip] = (duration, times, per_node)

animated = set()
for duration, times, per_node in samples.values():
    for i, rows in enumerate(per_node):
        first = rows[0]
        for p, q, s in rows[1:]:
            if max(abs(a - b) for a, b in zip(p + s, first[0] + first[2])) > 1e-4 or abs(sum(a * b for a, b in zip(q, first[1]))) < 0.99999:
                animated.add(i)
                break
paths = {}
for i, n in enumerate(nodes):
    chain, j = [], i
    while j >= 0:
        chain.append(nodes[j]['name'])
        j = nodes[j]['parent']
    paths[i] = '/'.join(reversed(chain))

clips_out, used_names = [], set()
budget = 262144
channels_per_clip = 3 * len(animated)
total_samples = sum(len(t) for _, t, _ in samples.values()) * max(1, channels_per_clip)
stride = max(1, math.ceil(total_samples / (budget * 0.9)))
for clip in clip_names:
    duration, times, per_node = samples[clip]
    keep = list(range(0, len(times), stride))
    game = GAME_CLIPS.get(clip.lower())
    name = game if game and game not in used_names else clip
    used_names.add(name)
    channels = []
    for i in sorted(animated):
        rows = [per_node[i][j] for j in keep]
        quats, prev = [], None
        for _, q, _ in rows:
            q = list(q)
            if prev is not None and sum(a * b for a, b in zip(q, prev)) < 0:
                q = [-c for c in q]
            quats.append(round_list(q, 6))
            prev = q
        channels.append({'path': paths[i], 'property': 'local_position', 'values': [round_list(r[0], 5) for r in rows]})
        channels.append({'path': paths[i], 'property': 'local_rotation', 'values': quats})
        channels.append({'path': paths[i], 'property': 'local_scale', 'values': [round_list(r[2], 5) for r in rows]})
    clips_out.append({'name': name, 'duration': round(duration, 6), 'loop': True, 'sample_rate': RATE / stride,
                      'times': [times[j] for j in keep], 'sample_count': len(keep), 'channels': channels})
    report['clips'][clip] = name
default = next((c['name'] for c in clips_out if c['name'] == 'Idle v4'), clips_out[0]['name'] if clips_out else None)

# ---- icon: the model itself, front 3/4, transparent ---------------------------------------------------------------
clear_animation()
icon = 'icon.png'
cam_data = bpy.data.cameras.new('sb_cam')
cam_data.type = 'ORTHO'
cam = bpy.data.objects.new('sb_cam', cam_data)
scene.collection.objects.link(cam)
centre_b = CI @ (N.inverted() @ Vector((0, 0.5, 0, 1)))
centre_b = Vector(centre_b[:3])
size_b = 1.0 / k
cam_data.ortho_scale = size_b * 1.25
direction = (C3.inverted() @ Vector((0.55, 0.45, 1.0))).normalized()      # Unity front (+Z), a bit right and above
cam.location = centre_b + direction * size_b * 4
cam.rotation_euler = (centre_b - cam.location).to_track_quat('-Z', 'Y').to_euler()
cam_data.clip_end = size_b * 20
scene.camera = cam
sun = bpy.data.objects.new('sb_sun', bpy.data.lights.new('sb_sun', 'SUN'))
sun.data.energy = 3.0
sun.rotation_euler = (0.8, 0.2, 0.6)
scene.collection.objects.link(sun)
world = bpy.data.worlds.new('sb_world')
world.use_nodes = True
bg = next(n for n in world.node_tree.nodes if n.type == 'BACKGROUND')
bg.inputs[1].default_value = 0.8
scene.world = world
for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'BLENDER_WORKBENCH'):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue
scene.render.film_transparent = True
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = os.path.join(OUT, icon)
try:
    bpy.ops.render.render(write_still=True)
except Exception as exc:                       # the icon is optional for the games
    report['icon_error'] = str(exc)
    icon = None

# ---- write ---------------------------------------------------------------------------------------------------------
model = {'schema': 'bridge-native-mesh', 'schema_version': 2, 'source_game': 'USER', 'source_kind': 'user-glb',
         'reference_id': 'SlimeBridge.UserLargo.' + CANONICAL, 'display_name': CANONICAL, 'prefab_name': CANONICAL,
         'prefab_scale': 1.0, 'visual_root': '',
         'visual_transform': {'local_position': [0.0, -0.5, 0.0], 'local_rotation': [0.0, 0.0, 0.0, 1.0], 'local_scale': [1.0, 1.0, 1.0]},
         'skeleton': {'bone_count': len(bones_out), 'bones': bones_out}, 'skinned_renderers': parts, 'static_renderers': [],
         'source_extraction_scope': 'user-glb-lod0-v1'}
if clips_out:
    model['source_animation'] = {'default_clip': default, 'clips': clips_out}
files = {}


def write_json(name, doc):
    data = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    with open(os.path.join(OUT, name), 'wb') as fh:
        fh.write(data)


write_json('model.json', model)
if icon and os.path.isfile(os.path.join(OUT, icon)):
    write_json('icon-descriptor.json', {
        'schema': 'bridge-source-sprite', 'schema_version': 1, 'mandatory': False, 'file': icon, 'texture_size': [512, 512],
        'rect': [0, 0, 512, 512], 'pivot': [0.5, 0.5], 'pixels_per_unit': 100,
        'vertices': [[-2.56, -2.56], [2.56, -2.56], [2.56, 2.56], [-2.56, 2.56]], 'triangles': [0, 1, 2, 0, 2, 3],
        'source_sprite_name': 'icon_' + CANONICAL.replace('.', '_'), 'pixel_encoding': 'rendered-from-user-model'})
report['vertices'] = sum(len(p['vertices']) for p in parts)
report['triangles'] = sum(len(t) // 3 for p in parts for t in p['triangles'])
report['bones'] = len(bones_out)
report['animated_nodes'] = len(animated)
write_json('adapter-report.json', report)
for name in sorted(os.listdir(OUT)):
    if name != 'cache-manifest.json' and os.path.isfile(os.path.join(OUT, name)):
        with open(os.path.join(OUT, name), 'rb') as fh:
            files[name] = hashlib.sha256(fh.read()).hexdigest()
write_json('cache-manifest.json', {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1, 'canonical_id': CANONICAL,
                                   'converter_version': 1, 'source_game': 'USER', 'source_run_id': 'user-largo-' + os.path.basename(SRC),
                                   'files': files})
print('SB_ADAPTER_OK', json.dumps({k: report[k] for k in ('vertices', 'triangles', 'bones', 'animated_nodes', 'clips', 'skipped_parts')}))
