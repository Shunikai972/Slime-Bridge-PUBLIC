"""Original SlimeBridge colour palettes for the six semantic gateway materials.

Colours are authored in sRGB (0..1) for readability; ``linear`` converts them for glTF
``baseColorFactor`` / ``emissiveFactor``, which the glTF 2.0 spec defines in linear space.
These are placeholder look-dev values chosen for a soft, colourful ranch setting. They are not
sampled from any game; a game adapter maps each semantic id to its own target material later.
"""

SEMANTIC_MATERIALS = ('frame_primary', 'frame_secondary', 'portal_energy', 'accent', 'bridge_surface',
                      'emissive_trim')

# base: sRGB base colour; alpha: 1 = opaque; emissive: sRGB emissive colour (black = none)
_M = dict

PALETTES = {
    'meadow': {
        'frame_primary': _M(base=(0.97, 0.93, 0.84), metallic=0.0, roughness=0.55),
        'frame_secondary': _M(base=(0.27, 0.66, 0.64), metallic=0.1, roughness=0.5),
        'accent': _M(base=(1.0, 0.62, 0.52), metallic=0.0, roughness=0.45),
        'bridge_surface': _M(base=(0.86, 0.66, 0.42), metallic=0.0, roughness=0.8),
        'portal_energy': _M(base=(0.45, 0.98, 0.9), alpha=0.55, emissive=(0.3, 0.95, 0.85), roughness=0.2),
        'emissive_trim': _M(base=(0.72, 1.0, 0.86), emissive=(0.55, 1.0, 0.78), roughness=0.3),
    },
    'twilight': {
        'frame_primary': _M(base=(0.9, 0.87, 0.98), metallic=0.0, roughness=0.5),
        'frame_secondary': _M(base=(0.33, 0.29, 0.58), metallic=0.15, roughness=0.45),
        'accent': _M(base=(1.0, 0.8, 0.38), metallic=0.2, roughness=0.4),
        'bridge_surface': _M(base=(0.5, 0.53, 0.66), metallic=0.0, roughness=0.75),
        'portal_energy': _M(base=(0.86, 0.5, 1.0), alpha=0.55, emissive=(0.75, 0.38, 1.0), roughness=0.2),
        'emissive_trim': _M(base=(1.0, 0.66, 0.92), emissive=(1.0, 0.5, 0.86), roughness=0.3),
    },
    'coral': {
        'frame_primary': _M(base=(0.99, 0.9, 0.78), metallic=0.0, roughness=0.55),
        'frame_secondary': _M(base=(0.93, 0.47, 0.4), metallic=0.05, roughness=0.5),
        'accent': _M(base=(0.27, 0.8, 0.78), metallic=0.0, roughness=0.45),
        'bridge_surface': _M(base=(0.76, 0.62, 0.5), metallic=0.0, roughness=0.8),
        'portal_energy': _M(base=(1.0, 0.6, 0.7), alpha=0.55, emissive=(1.0, 0.45, 0.6), roughness=0.2),
        'emissive_trim': _M(base=(1.0, 0.86, 0.6), emissive=(1.0, 0.78, 0.45), roughness=0.3),
    },
}

DOUBLE_SIDED = ('portal_energy',)


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def material(palette_id, semantic):
    """Normalized material record (sRGB colours, defaults filled in)."""
    raw = PALETTES[palette_id][semantic]
    return {'semantic': semantic, 'base_srgb': tuple(raw['base']), 'alpha': raw.get('alpha', 1.0),
            'metallic': raw.get('metallic', 0.0), 'roughness': raw.get('roughness', 0.6),
            'emissive_srgb': tuple(raw.get('emissive', (0.0, 0.0, 0.0))),
            'double_sided': semantic in DOUBLE_SIDED}


def linear(rgb):
    return tuple(round(srgb_to_linear(c), 6) for c in rgb)
