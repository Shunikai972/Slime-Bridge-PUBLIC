"""Target-game material template library, derived from the player's own install.

For every shader used by the game's slime appearances (body + face materials of all species) we record the
property contract (which floats/colors/textures/keywords really exist on materials using that shader)
and keep concrete example materials. The translator uses it to (a) choose a target shader, (b) copy only
properties the target shader understands, (c) name a native material the runtime adapter can clone.
Only names and numeric values are stored - no textures or meshes.
"""

import json
from collections import defaultdict

from ..catalog import fld
from ..extract import prefab as pf
from ..extract import materials as mt
from ..extract.appearance import _reader_from_key

VERSION = 3


def build(session, rows, game, limit_examples=6):
    shaders = defaultdict(lambda: {'floats': set(), 'colors': set(), 'textures': set(), 'keywords': set(), 'examples': [], 'count': 0})
    seen = set()
    normal_formats = defaultdict(int)

    def take(rd):
        if rd is None:
            return
        key = pf.ref_key(rd)
        if key in seen:
            return
        seen.add(key)
        try:
            m = mt.read_material(session, rd)
        except Exception:
            return
        for prop, t in m['textures'].items():
            if t.get('texture') and ('normal' in prop.lower() or 'bump' in prop.lower()):
                trd = _reader_from_key(session, t['texture'])
                if trd is not None and trd.type.name == 'Texture2D':
                    try:
                        normal_formats[mt.TEXTURE_FORMATS.get(int(trd.read().m_TextureFormat), int(trd.read().m_TextureFormat))] += 1
                    except Exception:
                        pass
        name = m['shader']
        if not name:
            return
        e = shaders[name]
        e['count'] += 1
        e['floats'] |= set(m['floats'])
        e['colors'] |= set(m['colors'])
        e['textures'] |= set(m['textures'])
        e['keywords'] |= set(m['keywords'])
        if len(e['examples']) < limit_examples:
            e['examples'].append({'ref': key, 'name': m['name'], 'keywords': m['keywords'],
                                  'floats': m['floats'], 'colors': m['colors'],
                                  'texture_props': sorted(k for k, v in m['textures'].items() if v.get('texture'))})

    for row in rows:
        if 'error' in row or not row.get('appearances'):
            continue
        ap = _reader_from_key(session, row['appearances'][0])
        if ap is None:
            continue
        try:
            d = ap.parse_as_dict()
        except Exception:
            continue
        owner = ap.assets_file
        for st in fld(d, 'Structures', default=[]) or []:
            for mm in st.get('DefaultMaterials') or []:
                take(session.resolve_pptr(owner, mm))
            for ov in st.get('ElementMaterials') or []:
                for mm in ov.get('Materials') or []:
                    take(session.resolve_pptr(owner, mm))
        face = session.resolve_pptr(owner, fld(d, 'Face'))
        if face is not None:
            try:
                fd = face.parse_as_dict()
                for ef in fd.get('ExpressionFaces', []):
                    for k in ('Eyes', 'Mouth'):
                        take(session.resolve_pptr(face.assets_file, ef.get(k)))
            except Exception:
                pass
    return {'version': VERSION, 'game': game, 'normal_map_formats': dict(normal_formats),
            'shaders': {k: {'count': v['count'], 'floats': sorted(v['floats']), 'colors': sorted(v['colors']),
                            'textures': sorted(v['textures']), 'keywords': sorted(v['keywords']), 'examples': v['examples']}
                        for k, v in sorted(shaders.items())}}
