"""Blender (headless): the colour reference of a finished largo, as Blender's Material Preview shows it.

    blender -b --factory-startup --python tools/largo_models/reference_render.py -- <model.glb> <out.png> [--yaw deg]

EEVEE, the Material Preview "forest" HDRI at strength 1, the default AgX view transform, a front view a little from above
(like the games' close-ups), transparent background, FX shells hidden like in the game version.
"""
import math
import os
import sys

import bpy
from mathutils import Matrix, Vector

argv = sys.argv[sys.argv.index('--') + 1:]
SRC, OUT = argv[0], argv[1]
YAW = float(argv[argv.index('--yaw') + 1]) if '--yaw' in argv else 0.0

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=SRC)
scene = bpy.context.scene
for o in list(bpy.data.objects):
    if o.type == 'MESH' and (o.name.upper().startswith('FX') or (not o.data.materials and o.name.startswith('Icosphere'))):
        o.hide_render = True
meshes = [o for o in scene.objects if o.type == 'MESH' and not o.hide_render]
lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
for o in meshes:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector(map(min, lo, w)); hi = Vector(map(max, hi, w))
centre, size = (lo + hi) / 2, max(hi - lo)
front = Matrix.Rotation(math.radians(-YAW), 3, 'Z') @ Vector((0, -1, 0))        # the face side, as the game turns it
cam = bpy.data.objects.new('ref_cam', bpy.data.cameras.new('ref_cam'))
scene.collection.objects.link(cam)
cam.data.lens = 50
direction = (front + Vector((0, 0, 0.17))).normalized()
cam.location = centre + direction * size * 2.6
cam.rotation_euler = (centre - cam.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera = cam
world = bpy.data.worlds.new('preview')
scene.world = world
world.use_nodes = True
nodes, links = world.node_tree.nodes, world.node_tree.links
env = nodes.new('ShaderNodeTexEnvironment')
env.image = bpy.data.images.load(os.path.join(os.path.dirname(bpy.app.binary_path), f'{bpy.app.version[0]}.{bpy.app.version[1]}',
                                              'datafiles', 'studiolights', 'world', 'forest.exr'))
bg = next(n for n in nodes if n.type == 'BACKGROUND')
bg.inputs['Strength'].default_value = 1.0
links.new(env.outputs['Color'], bg.inputs['Color'])
for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
    try:
        scene.render.engine = engine
        break
    except TypeError:
        continue
scene.render.film_transparent = True
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.filepath = OUT
bpy.ops.render.render(write_still=True)
print('REF_OK', OUT, scene.view_settings.view_transform)
