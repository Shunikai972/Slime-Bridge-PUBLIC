"""Blender helper for the SlimeBridge gateway (manual, local use only; needs Blender's ``bpy``).

Import an already generated GLB + sidecar and organise it::

    blender --background --python bridge/meshgen/blender/portal_blender.py -- \
        --glb Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.glb \
        --sidecar Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.portal.json \
        [--fbx Lab/GeneratedContent/portal3d/slimebridge_gateway_meadow.fbx] [--blend out.blend]

or rebuild the same portal from the same JSON parameters with the repository's own pure-Python
generator (identical bytes to the CLI) and then import it::

    blender --background --python bridge/meshgen/blender/portal_blender.py -- \
        --params my_params.json --repo . --out Lab/GeneratedContent/portal3d/blender

What it does inside Blender: imports the GLB (the glTF importer converts +Y-up to Blender's
+Z-up), puts LOD meshes into ``SlimeBridge_Visual_LOD<n>`` collections (LOD1/LOD2 hidden),
replaces the collision/trigger empties with wire boxes in ``SlimeBridge_Collision`` /
``SlimeBridge_Trigger`` (excluded from render), keeps sockets as arrow empties in
``SlimeBridge_Sockets``, tags the six semantic materials, stores the parameters, hash and
evidence as custom properties, and optionally exports FBX (+Y up / -Z forward) or saves .blend.

This file is never imported by SlimeBridge product code or tests (tests only ``ast.parse`` it
and compare ``PORTAL_PARAM_KEYS`` with ``PortalParams``). It has NOT been run in this
environment: everything it produces is NEEDS_LOCAL_VISUAL_PROOF.
"""

import argparse
import json
import os
import sys

# Must equal the fields of bridge.meshgen.portal.PortalParams (checked by the unit tests).
PORTAL_PARAM_KEYS = (
    'variant', 'palette', 'ring_inner_radius', 'ring_tube_width', 'ring_tube_depth', 'bevel_radius',
    'ring_center_lift', 'ring_segments', 'bevel_segments', 'deck_length', 'deck_width', 'deck_thickness',
    'deck_height', 'ramp_slope_deg', 'curb_width', 'curb_height', 'rail_height', 'rail_post_spacing',
    'pedestal_width', 'pedestal_height', 'pedestal_depth', 'core_radius', 'lod_count', 'min_clear_width',
    'min_clear_height', 'min_walkable_width', 'max_ramp_slope_deg',
)
SEMANTIC_MATERIALS = ('frame_primary', 'frame_secondary', 'portal_energy', 'accent', 'bridge_surface',
                      'emissive_trim')
COLLECTION_PREFIX = 'SlimeBridge'


def parse_args(argv):
    argv = argv[argv.index('--') + 1:] if '--' in argv else []
    p = argparse.ArgumentParser(prog='portal_blender.py')
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument('--glb', help='generated GLB to import')
    src.add_argument('--params', help='JSON PortalParams to regenerate with the repository generator')
    p.add_argument('--sidecar', help='<name>.portal.json next to --glb (default: derived from --glb)')
    p.add_argument('--repo', help='repository root for --params (adds it to sys.path)')
    p.add_argument('--out', default='Lab/GeneratedContent/portal3d/blender', help='relative output dir for --params')
    p.add_argument('--fbx', help='optional FBX export path')
    p.add_argument('--blend', help='optional .blend save path')
    p.add_argument('--clear', action='store_true', help='delete every object in the current scene first')
    return p.parse_args(argv)


def check_params(params):
    """Problems (strings) if a parameter document does not match PORTAL_PARAM_KEYS exactly."""
    if not isinstance(params, dict):
        return ['parameters must be a JSON object']
    problems = [f'unknown parameter: {k}' for k in sorted(set(params) - set(PORTAL_PARAM_KEYS))]
    problems += [f'missing parameter: {k}' for k in PORTAL_PARAM_KEYS if k not in params]
    return problems


def generate_with_repo(repo, params_path, out_rel):
    """Regenerate GLB + sidecar with the repository's generator; returns (glb_path, sidecar_path)."""
    sys.path.insert(0, os.path.abspath(repo))
    from bridge.meshgen import cli  # noqa: E402  (pure Python; no bpy needed)
    from bridge.meshgen.portal import generate_portal, params_from_dict

    with open(params_path, encoding='utf-8') as stream:
        raw = json.load(stream)
    params, diags = params_from_dict(raw)
    if params is None:
        raise SystemExit(f'invalid parameters: {[d["code"] for d in diags]}')
    model = generate_portal(params)
    if not model.ok:
        raise SystemExit(f'generation failed: {[d["code"] for d in model.diagnostics]}')
    name = f'slimebridge_gateway_{params.variant}'
    root, out = cli.resolve_output(repo, out_rel)
    cli.write_bundle(root, out, cli.build_files(model, name))
    return str(out / f'{name}.glb'), str(out / f'{name}.portal.json')


def _collection(bpy, name, hidden=False):
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    coll.hide_render = hidden
    return coll


def _move(obj, coll):
    for other in list(obj.users_collection):
        other.objects.unlink(obj)
    coll.objects.link(obj)


def _wire_box(bpy, name, matrix_world, coll, props):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)  # spans [-1, 1]; the empty's scale holds the half extents
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    obj.matrix_world = matrix_world
    obj.display_type = 'WIRE'
    obj.hide_render = True
    for key, value in props.items():
        obj[key] = value
    return obj


def organise(bpy, imported, sidecar):
    meta = sidecar['metadata']
    visual = {lod['level']: _collection(bpy, f'{COLLECTION_PREFIX}_Visual_LOD{lod["level"]}')
              for lod in meta['lods']}
    collision = _collection(bpy, f'{COLLECTION_PREFIX}_Collision', hidden=True)
    trigger = _collection(bpy, f'{COLLECTION_PREFIX}_Trigger', hidden=True)
    sockets = _collection(bpy, f'{COLLECTION_PREFIX}_Sockets', hidden=True)
    boxes = {b['name']: b for b in sidecar['collision']['boxes']}
    for obj in imported:
        name = obj.name.split('.0')[0] if obj.name.startswith(('COL_', 'TRIGGER_')) else obj.name
        if name.startswith('COL_'):
            box = boxes.get(name[4:], {})
            _wire_box(bpy, name, obj.matrix_world.copy(), collision,
                      {'slimebridge_collision': json.dumps(box, sort_keys=True)})
            bpy.data.objects.remove(obj, do_unlink=True)
        elif name.startswith('TRIGGER_'):
            _wire_box(bpy, name, obj.matrix_world.copy(), trigger,
                      {'slimebridge_trigger': json.dumps(sidecar['trigger'], sort_keys=True)})
            bpy.data.objects.remove(obj, do_unlink=True)
        elif obj.name.startswith('socket.'):
            obj.empty_display_type = 'ARROWS'
            obj.empty_display_size = 0.35
            _move(obj, sockets)
        elif obj.type == 'MESH' and '_LOD' in obj.name:
            level = int(obj.name.rsplit('_LOD', 1)[1][:1])
            _move(obj, visual.get(level, visual[0]))
            if level > 0:
                obj.hide_set(True)
                obj.hide_render = True
    for mat in bpy.data.materials:
        if mat.name in SEMANTIC_MATERIALS:
            mat['slimebridge_semantic_material'] = mat.name
            entry = next(m for m in sidecar['materials'] if m['semantic'] == mat.name)
            mat.diffuse_color = tuple(entry['base_color_linear']) + (entry['alpha'],)
    scene = bpy.context.scene
    scene['slimebridge_params'] = json.dumps(sidecar['params'], sort_keys=True)
    scene['slimebridge_params_hash'] = sidecar['params_hash']
    scene['slimebridge_generator'] = f'{sidecar["generator"]} {sidecar["generator_version"]}'
    scene['slimebridge_evidence'] = sidecar['evidence']
    scene['slimebridge_conventions'] = json.dumps(sidecar['conventions'], sort_keys=True)


def main(argv=None):
    import bpy  # only available inside Blender; never at module import time

    args = parse_args(sys.argv if argv is None else argv)
    if args.params:
        if not args.repo:
            raise SystemExit('--params needs --repo (the SlimeBridge checkout providing the generator)')
        with open(args.params, encoding='utf-8') as stream:
            problems = check_params(dict(json.load(stream)))
        if [p for p in problems if p.startswith('unknown')]:
            raise SystemExit(f'parameter keys do not match PortalParams: {problems}')
        glb_path, sidecar_path = generate_with_repo(args.repo, args.params, args.out)
    else:
        glb_path = args.glb
        sidecar_path = args.sidecar or glb_path[:-len('.glb')] + '.portal.json'
    with open(sidecar_path, encoding='utf-8') as stream:
        sidecar = json.load(stream)
    problems = check_params(sidecar.get('params'))
    if problems:
        raise SystemExit(f'sidecar parameters do not match this script: {problems}')
    if args.clear:
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.abspath(glb_path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    organise(bpy, imported, sidecar)
    if args.fbx:
        bpy.ops.export_scene.fbx(filepath=os.path.abspath(args.fbx), object_types={'MESH', 'EMPTY'},
                                 use_custom_props=True, apply_scale_options='FBX_SCALE_ALL',
                                 axis_forward='-Z', axis_up='Y', add_leaf_bones=False)
    if args.blend:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))
    print(json.dumps({'status': 'IMPORTED', 'glb': glb_path, 'objects': len(imported),
                      'evidence': sidecar['evidence']}, sort_keys=True))


if __name__ == '__main__':
    main()
