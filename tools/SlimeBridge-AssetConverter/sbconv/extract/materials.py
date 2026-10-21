"""Material + texture + shader-identity extraction (read-only). Everything is returned as plain dicts
so the translator (``sbconv.materials``) can reason about shader properties explicitly instead of
assuming one engine's semantics."""

import hashlib
import io

from .prefab import ref_key

# Texture2D.m_TextureFormat values that are linear data containers by convention are decided by
# property name in the translator; here we only report what the file says.
TEXTURE_FORMATS = {
    1: 'Alpha8', 2: 'ARGB4444', 3: 'RGB24', 4: 'RGBA32', 5: 'ARGB32', 7: 'RGB565', 9: 'R16', 10: 'DXT1', 12: 'DXT5',
    13: 'RGBA4444', 14: 'BGRA32', 15: 'RHalf', 16: 'RGHalf', 17: 'RGBAHalf', 18: 'RFloat', 19: 'RGFloat',
    20: 'RGBAFloat', 24: 'BC6H', 25: 'BC7', 26: 'BC4', 27: 'BC5', 28: 'DXT1Crunched', 29: 'DXT5Crunched',
    62: 'RG16', 63: 'R8',
}


def shader_info(session, rd):
    """Identity of a Shader object: name, dependencies and keyword/property census (no bytecode)."""
    if rd is None:
        return None
    info = {'ref': ref_key(rd), 'name': None, 'properties': []}
    try:
        sh = rd.read()
        parsed = getattr(sh, 'm_ParsedForm', None)
        info['name'] = (parsed.m_Name if parsed is not None and getattr(parsed, 'm_Name', None) else None) or getattr(sh, 'm_Name', None)
        if parsed is not None:
            props = []
            for p in parsed.m_PropInfo.m_Props:
                props.append({'name': p.m_Name, 'type': int(p.m_Type), 'default': [float(x) for x in
                              (p.m_DefValue or [])], 'range': None, 'description': p.m_Description,
                              'flags': int(getattr(p, 'm_Flags', 0)), 'texdim': int(getattr(p, 'm_DefTexture', None).m_TexDim) if getattr(p, 'm_DefTexture', None) is not None else None})
            info['properties'] = props
            info['fallback'] = parsed.m_FallbackName
            info['custom_editor'] = getattr(parsed, 'm_CustomEditorName', None)
    except Exception as e:
        info['read_error'] = f'{type(e).__name__}: {e}'
    return info


def read_material(session, rd):
    """Material -> dict with shader identity, keywords, saved float/color/vector/texture properties."""
    mat = rd.read()
    owner = rd.assets_file
    sh_rd = session.resolve_pptr(owner, mat.m_Shader)
    out = {'ref': ref_key(rd), 'name': mat.m_Name, 'shader_ref': ref_key(sh_rd), 'shader': None,
           'keywords': [], 'render_queue': int(getattr(mat, 'm_CustomRenderQueue', -1)),
           'lightmap_flags': int(getattr(mat, 'm_LightmapFlags', 0)),
           'floats': {}, 'colors': {}, 'textures': {}, 'tags': {}, 'disabled_passes': []}
    if sh_rd is not None:
        out['shader'] = shader_info(session, sh_rd)['name']
    else:
        ext = session.unresolved_external(owner, getattr(mat.m_Shader, 'm_FileID', 0))
        out['shader_unresolved'] = ext or 'null'
    kws = list(getattr(mat, 'm_ShaderKeywords', '') and mat.m_ShaderKeywords.split() or [])
    kws += list(getattr(mat, 'm_ValidKeywords', None) or [])
    out['keywords'] = sorted(set(kws))
    sp = mat.m_SavedProperties
    for name, v in sp.m_Floats:
        out['floats'][name] = float(v)
    for name, c in sp.m_Colors:
        out['colors'][name] = [float(c.r), float(c.g), float(c.b), float(c.a)]
    for name, te in sp.m_TexEnvs:
        trd = session.resolve_pptr(owner, te.m_Texture)
        ent = {'texture': ref_key(trd), 'scale': [float(te.m_Scale.x), float(te.m_Scale.y)],
               'offset': [float(te.m_Offset.x), float(te.m_Offset.y)]}
        if trd is None and getattr(te.m_Texture, 'm_PathID', 0):
            ent['unresolved'] = session.unresolved_external(owner, getattr(te.m_Texture, 'm_FileID', 0)) or 'missing'
        out['textures'][name] = ent
    try:
        out['tags'] = dict(getattr(mat, 'stringTagMap', {}) or {})
    except Exception:
        pass
    return out


def decode_texture(session, rd, max_dim=None):
    """Texture2D -> (png_bytes, info). RGBA8 only (sRGB/linear is a flag, not a transform)."""
    from PIL import Image
    t = rd.read()
    info = {'ref': ref_key(rd), 'name': t.m_Name, 'width': int(t.m_Width), 'height': int(t.m_Height),
            'format': TEXTURE_FORMATS.get(int(t.m_TextureFormat), int(t.m_TextureFormat)),
            'mip_count': int(getattr(t, 'm_MipCount', 1)),
            'srgb': bool(getattr(t, 'm_ColorSpace', 1)),
            'wrap_u': int(getattr(t.m_TextureSettings, 'm_WrapU', getattr(t.m_TextureSettings, 'm_WrapMode', 0))),
            'wrap_v': int(getattr(t.m_TextureSettings, 'm_WrapV', getattr(t.m_TextureSettings, 'm_WrapMode', 0))),
            'filter': int(getattr(t.m_TextureSettings, 'm_FilterMode', 1)),
            'aniso': int(getattr(t.m_TextureSettings, 'm_Aniso', 1))}
    img = t.image  # PIL.Image, already top-down (UnityPy flips for us)
    if img is None:
        raise ValueError(f'{t.m_Name}: texture could not be decoded ({info["format"]})')
    info['decoded_mode'] = img.mode
    if img.mode not in ('RGBA', 'RGB', 'L', 'LA'):
        img = img.convert('RGBA')
    if max_dim and max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    data = buf.getvalue()
    info['png_sha256'] = hashlib.sha256(data).hexdigest()
    info['png_bytes'] = len(data)
    return data, info
