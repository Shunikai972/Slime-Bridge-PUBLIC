"""Wavefront OBJ + MTL fallback export (one LOD, one object per part, 1-based shared indices).

OBJ has no axis convention of its own; the data is written in the glTF frame (metres, +Y up,
+Z forward), which matches Blender's default OBJ import settings (forward -Z, up Y) and Unity's
OBJ import (which mirrors X). Texture V is flipped to OBJ's bottom-left origin.
"""

from .palettes import material
from .portal import GENERATOR, GENERATOR_VERSION, VISUAL_EVIDENCE


def _f(v):
    return f'{round(v, 6) + 0.0:.6f}'


def export_obj(model, level=0, mtl_name='portal.mtl'):
    """``(obj_text, mtl_text)`` for one LOD of a generated ``PortalModel``."""
    if not model.ok:
        raise ValueError('cannot export a model with errors')
    if '/' in mtl_name or '\\' in mtl_name or not mtl_name.endswith('.mtl'):
        raise ValueError('mtl_name must be a plain file name ending in .mtl')
    lod = next(lod for lod in model.lods if lod['level'] == level)
    lines = [f'# SlimeBridge {GENERATOR} {GENERATOR_VERSION} - original procedural gateway',
             f'# variant={model.params.variant} palette={model.params.palette} lod={level}',
             f'# params_hash={model.params_hash}',
             '# units=metres up=+Y forward=+Z (glTF frame), counter-clockwise faces',
             f'# evidence={VISUAL_EVIDENCE}',
             f'mtllib {mtl_name}']
    base = 1
    used = []
    for part in lod['parts']:
        if part.material not in used:
            used.append(part.material)
        lines.append(f'o {part.name}')
        lines.extend(f'v {_f(p[0])} {_f(p[1])} {_f(p[2])}' for p in part.positions)
        lines.extend(f'vt {_f(uv[0])} {_f(1.0 - uv[1])}' for uv in part.uvs)
        lines.extend(f'vn {_f(n[0])} {_f(n[1])} {_f(n[2])}' for n in part.normals)
        lines.append(f'usemtl {part.material}')
        lines.append('s off')
        idx = part.indices
        for t in range(0, len(idx), 3):
            a, b, c = (base + i for i in idx[t:t + 3])
            lines.append(f'f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}')
        base += part.vertex_count
    mtl = [f'# SlimeBridge {GENERATOR} {GENERATOR_VERSION} palette={model.params.palette} (sRGB colours)']
    for semantic in sorted(used):
        m = material(model.params.palette, semantic)
        kd = ' '.join(_f(c) for c in m['base_srgb'])
        ke = ' '.join(_f(c) for c in m['emissive_srgb'])
        shininess = _f(max(1.0, (1.0 - m['roughness']) * 250.0))
        mtl += ['', f'newmtl {semantic}', f'Ka {kd}', f'Kd {kd}', 'Ks 0.100000 0.100000 0.100000',
                f'Ns {shininess}', f'Ke {ke}', f'd {_f(m["alpha"])}', 'illum 2']
    return '\n'.join(lines) + '\n', '\n'.join(mtl) + '\n'
