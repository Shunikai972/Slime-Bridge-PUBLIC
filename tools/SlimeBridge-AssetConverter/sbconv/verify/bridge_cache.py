"""Validate a ``bridge-native-mesh`` v2 cache the way SlimeBridge's plugin does.

* A faithful Python port of mods/shared/NativeMeshParser.cs + NativeMeshCache.cs rules (limits, unit quaternions,
  unique bone names, skin palette, weights, texture basename rules, animation sampling rules, hash coverage).
* Optionally the repo's own Python scanner (``bridge.conversion.source_cache._scan_cache``) when the SlimeBridge
  repository is available (read-only import).
The C# parser itself is exercised separately by the compiled harness (csharp/); this port is the offline gate."""

import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

CANONICAL_RE = re.compile(r'^slime\.[a-z0-9_.-]{1,80}$')
DEVICE = {'CON', 'PRN', 'AUX', 'NUL'}


def _finite_float(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return False
    if not math.isfinite(x):
        return False
    try:
        f = struct.unpack('<f', struct.pack('<f', x))[0]
    except OverflowError:
        return False
    return not (x != 0 and abs(x) < 1.4e-45) and math.isfinite(f)


def _vec(v, n):
    return isinstance(v, list) and len(v) == n and all(_finite_float(x) for x in v)


def _unit(q):
    return math.isclose(sum(x * x for x in q) ** 0.5, 1.0, abs_tol=0.01)


def _safe_file(name):
    if name in ('.', '..') or name.endswith('.') or name.endswith(' '):
        return False
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
        return False
    stem = name.split('.')[0].upper()
    return not (stem in DEVICE or (len(stem) == 4 and stem[:3] in ('COM', 'LPT') and stem[3] in '123456789'))


def validate_cache(cache_dir, repo=None):
    d = Path(cache_dir)
    P, W = [], []
    manifest_path = d / 'cache-manifest.json'
    try:
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError) as e:
        return {'ok': False, 'problems': [f'MANIFEST: {e}'], 'warnings': []}
    if len(raw) > 1024 * 1024:
        P.append('MANIFEST_TOO_LARGE')
    if manifest.get('schema') != 'bridge-asset-cache-manifest' or manifest.get('schema_version') != 1:
        P.append('MANIFEST_SCHEMA')
    cid = manifest.get('canonical_id')
    if not isinstance(cid, str) or not CANONICAL_RE.match(cid):
        P.append(f'CANONICAL_ID: {cid!r} does not match {CANONICAL_RE.pattern} (the plugin rejects it)')
    files = manifest.get('files')
    if not isinstance(files, dict):
        return {'ok': False, 'problems': P + ['MANIFEST_FILES_INVALID'], 'warnings': W}
    for name, digest in files.items():
        p = d / name
        if '/' in name or '\\' in name or not p.is_file() or p.is_symlink():
            P.append(f'FILE_MISSING:{name}')
        elif hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            P.append(f'FILE_HASH_MISMATCH:{name}')
    for p in d.iterdir():
        if p.name != 'cache-manifest.json' and p.name not in files:
            P.append(f'UNLISTED:{p.name}')
    try:
        model_raw = (d / 'model.json').read_bytes()
        if len(model_raw) > 16 * 1024 * 1024:
            P.append('MODEL_TOO_LARGE')
        model = json.loads(model_raw.decode('utf-8-sig'))
    except (OSError, ValueError) as e:
        return {'ok': False, 'problems': P + [f'MODEL: {e}'], 'warnings': W}
    _check_model(model, d, files, P, W)
    # the repo's own scanner, when available
    if repo:
        try:
            sys.path.insert(0, str(repo))
            from bridge.conversion.source_cache import _scan_cache  # type: ignore
            scan = _scan_cache(d)
            for p in scan['problems']:
                W.append(f'repo-scan: {p}') if p.startswith(('PROVENANCE', 'ICON')) else P.append(f'repo-scan: {p}')
        except Exception as e:
            W.append(f'repo scanner unavailable: {type(e).__name__}: {e}')
        finally:
            if sys.path and sys.path[0] == str(repo):
                sys.path.pop(0)
    return {'ok': not P, 'problems': P, 'warnings': W, 'canonical_id': cid}


def _check_model(m, d, files, P, W):
    def bad(msg):
        P.append(msg)
    if m.get('schema') != 'bridge-native-mesh' or m.get('schema_version') != 2:
        bad('MODEL_SCHEMA')
    for k in ('source_game', 'reference_id'):
        if not isinstance(m.get(k), str) or not m[k]:
            bad(f'MODEL.{k}')
    if not (isinstance(m.get('prefab_scale', 1), (int, float)) and m.get('prefab_scale', 1) > 0):
        bad('prefab_scale')
    vt = m.get('visual_transform')
    if vt is not None and not (_vec(vt.get('local_position'), 3) and _vec(vt.get('local_rotation'), 4) and _unit(vt['local_rotation']) and _vec(vt.get('local_scale'), 3)):
        bad('visual_transform')
    bones = (m.get('skeleton') or {}).get('bones', [])
    if len(bones) > 1024:
        bad('bones > 1024')
    names, paths = set(), set()
    for i, b in enumerate(bones):
        n = b.get('name')
        if not isinstance(n, str) or not n or re.search(r'[\\/\0]', n) or n in names:
            bad(f'bone.name invalid/duplicate: {n!r}')
        names.add(n)
        if not isinstance(b.get('parent'), int) or not -1 <= b['parent'] < len(bones):
            bad(f'bone.parent {i}')
        if not (_vec(b.get('p'), 3) and _vec(b.get('q'), 4) and _unit(b['q']) and _vec(b.get('s'), 3)):
            bad(f'bone transform {n}')
    for i in range(len(bones)):
        seen, j = set(), i
        while j != -1 and 0 <= j < len(bones):
            if j in seen:
                bad('bones: parent cycle')
                break
            seen.add(j)
            j = bones[j]['parent']
    def path_of(i):
        parts, j = [], i
        while j != -1:
            parts.append(bones[j]['name'])
            j = bones[j]['parent']
        return '/'.join(reversed(parts))
    try:
        paths = {path_of(i) for i in range(len(bones))}
    except Exception:
        paths = set()
    parts = m.get('skinned_renderers', []) + m.get('static_renderers', [])
    if len(parts) > 256:
        bad('parts > 256')
    roles = set()
    total_tex = 0
    for part in parts:
        roles.add(part.get('role'))
        nv = len(part.get('vertices', []))
        if nv == 0 or not all(_vec(v, 3) for v in part['vertices']):
            bad(f"{part.get('mesh_name')}: vertices empty/invalid")
            continue
        for key, w in (('normals', 3), ('tangents', 4), ('uv', 2), ('uv2', 2), ('colors', 4)):
            rows = part.get(key, [])
            if rows and (len(rows) != nv or not all(_vec(r, w) for r in rows)):
                bad(f"{part['mesh_name']}: {key} row mismatch")
        tris = part.get('triangles', [])
        if len(tris) > 256:
            bad('submeshes > 256')
        for sm in tris:
            if len(sm) % 3 or any(not isinstance(i, int) or not 0 <= i < nv for i in sm):
                bad(f"{part['mesh_name']}: triangles invalid")
        if not _vec(part.get('local_position'), 3) or not _vec(part.get('local_rotation'), 4) or not _unit(part['local_rotation']) or not _vec(part.get('local_scale'), 3):
            bad(f"{part['mesh_name']}: transform invalid")
        if part in m.get('skinned_renderers', []):
            if part.get('root_bone') not in names:
                bad(f"{part['mesh_name']}: root_bone unresolved")
            pal = part.get('skin_bones', [])
            if not pal or len(pal) > len(bones) or len(set(pal)) != len(pal) or any(n not in names for n in pal):
                bad(f"{part['mesh_name']}: skin_bones invalid")
            bp, bw = part.get('bind_poses', []), part.get('bone_weights', [])
            if len(bp) != len(pal) or len(bw) != nv or not all(_vec(r, 16) for r in bp):
                bad(f"{part['mesh_name']}: bind pose / weight count mismatch")
            for row in bw:
                if not _vec(row, 8) or min(row[:4]) < 0 or abs(sum(row[:4]) - 1) > 0.001 or any(row[4 + k] != int(row[4 + k]) or not 0 <= row[4 + k] < len(pal) for k in range(4)):
                    bad(f"{part['mesh_name']}: bone_weights invalid")
                    break
        mats = part.get('materials', [])
        if len(mats) > 256:
            bad('materials > 256')
        for mat in mats:
            if not -1 <= mat.get('render_queue', -1) <= 5000:
                bad('render_queue range')
            for k in ('floats',):
                if not all(_finite_float(v) for v in mat.get(k, {}).values()):
                    bad(f"{mat.get('name')}: non-finite float property")
            if not all(_vec(v, 4) for v in mat.get('colors', {}).values()):
                bad(f"{mat.get('name')}: color property invalid")
            if len(mat.get('textures', [])) > 256:
                bad('textures > 256')
            for t in mat.get('textures', []):
                f = t.get('file', '')
                if not _safe_file(f):
                    bad(f'texture.file unsafe: {f!r}')
                elif f not in files:
                    bad(f'texture {f} not in manifest')
                else:
                    sz = (d / f).stat().st_size if (d / f).is_file() else 0
                    total_tex += sz
                    if sz > 16 * 1024 * 1024:
                        bad(f'texture {f} > 16 MiB')
    if total_tex > 64 * 1024 * 1024:
        bad('aggregate texture bytes > 64 MiB')
    if 'body' not in roles:
        bad('NO_BODY_PART')
    if 'face' not in roles:
        W.append('no face part (the SR1 plugin requires one for imported species)')
    anim = m.get('source_animation')
    if anim:
        clips = anim.get('clips', [])
        if len(clips) > 64:
            bad('clips > 64')
        cn = set()
        for c in clips:
            if c['name'] in cn:
                bad('clip duplicate name')
            cn.add(c['name'])
            if not (0 < c['duration'] <= 3600 and 0 < c['sample_rate'] <= 240):
                bad(f"clip {c['name']}: duration/sample_rate")
            ts = c['times']
            if not ts or len(ts) > 4096 or ts[0] != 0 or any(ts[i] >= ts[i + 1] for i in range(len(ts) - 1)) or ts[-1] > c['duration'] + 1e-9:
                bad(f"clip {c['name']}: times must start at 0, ascend strictly and not exceed duration")
            ch = c['channels']
            if len(ch) > 3072:
                bad('channels > 3072')
            tg = set()
            for x in ch:
                w = 4 if x['property'] == 'local_rotation' else (3 if x['property'] in ('local_position', 'local_scale') else 0)
                key = (x['path'], x['property'])
                if w == 0 or x['path'] not in paths or key in tg:
                    bad(f"clip {c['name']}: invalid or duplicate channel {key}")
                tg.add(key)
                if len(x['values']) != len(ts) or any(not _vec(v, w) for v in x['values']):
                    bad(f"clip {c['name']}: channel {key} sample mismatch")
        if anim.get('default_clip') not in cn:
            bad('default_clip missing')
