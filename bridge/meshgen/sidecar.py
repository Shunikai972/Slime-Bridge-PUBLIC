"""``<name>.portal.json`` sidecar: everything a game adapter or DCC script needs besides meshes.

Holds the exact parameters and their hash, frame conventions, collision proxy, trigger volume,
sockets, LOD statistics, the palette (sRGB and linear), output file hashes and evidence. The
document is canonical JSON with a ``content_sha256`` over everything else, so identical
parameters give an identical sidecar.
"""

import hashlib

from bridge.content.canonical import pretty_json, sha256_json

from .palettes import SEMANTIC_MATERIALS, linear, material
from .portal import CONVENTIONS, GENERATOR, GENERATOR_VERSION, SCHEMA, SCHEMA_VERSION, VISUAL_EVIDENCE

ADAPTER_NOTES = {
    'frame_primary': 'main ring shell: map to the target game\'s soft painted structure material',
    'frame_secondary': 'structural body (keystone, pedestals, deck body, curbs, posts)',
    'portal_energy': 'animated/translucent portal surface and energy core; double-sided',
    'accent': 'playful highlight colour (collars, caps, bezels, rail knobs)',
    'bridge_surface': 'walkable planks; may receive the target game\'s wood/footstep material',
    'emissive_trim': 'unlit/emissive light strips (ring rims, guide lines, pod lights)',
}


def build_sidecar(model, files=None):
    """Sidecar document for a generated model. ``files`` maps output name -> bytes."""
    if not model.ok:
        raise ValueError('cannot describe a model with errors')
    palette = model.params.palette
    materials = []
    for semantic in SEMANTIC_MATERIALS:
        m = material(palette, semantic)
        materials.append({'semantic': semantic, 'used': semantic in model.metadata['materials'],
                          'base_color_srgb': list(m['base_srgb']), 'base_color_linear': list(linear(m['base_srgb'])),
                          'alpha': m['alpha'], 'metallic': m['metallic'], 'roughness': m['roughness'],
                          'emissive_srgb': list(m['emissive_srgb']), 'double_sided': m['double_sided'],
                          'adapter_note': ADAPTER_NOTES[semantic]})
    doc = {
        'schema': SCHEMA, 'schema_version': SCHEMA_VERSION,
        'generator': GENERATOR, 'generator_version': GENERATOR_VERSION,
        'params': model.params.to_dict(), 'params_hash': model.params_hash,
        'conventions': dict(CONVENTIONS),
        'metadata': model.metadata,
        'collision': {'shape_set': 'oriented_boxes', 'count': len(model.collision), 'boxes': model.collision,
                      'rules': ['no collision piece intersects the trigger/passage volume',
                                'collision bounds enclose the LOD0 visual bounds within 0.06 m',
                                'every walkable deck/ramp sample point is supported']},
        'trigger': model.trigger,
        'sockets': model.sockets,
        'materials': materials,
        'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                  for name, data in sorted((files or {}).items())},
        'diagnostics': model.diagnostics,
        'evidence': VISUAL_EVIDENCE,
        'tested_here': ['parameter/contract validation', 'mesh validation of every LOD',
                        'GLB container/accessor/index self-check (validate_glb)', 'OBJ index ranges',
                        'deterministic re-generation'],
        'not_tested_here': ['Blender import', 'Unity import', 'appearance or collision inside any game'],
    }
    doc['content_sha256'] = sha256_json(doc)
    return doc


def sidecar_bytes(model, files=None):
    return pretty_json(build_sidecar(model, files)).encode('utf-8')
