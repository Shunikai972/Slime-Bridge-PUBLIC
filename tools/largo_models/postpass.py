"""Second half of the largo model adapter, on a cache written by glb_to_cache.py (plain Python, no Blender):

* size: uniform scale so the slime's own body (the vertices on its body bone, not rings, wings or ears) is as wide as the
  games' slime mesh, centred and resting on y = 0; each game then applies its own Largo scale.
* slime physics: the games squash, stretch and wobble a slime (hops, landings, vacpack pull) with RubberBoneEffect, a spring
  simulation that runs in the frame of the visual's root and moves the native bone_jiggle_* bones. bone_core + six
  bone_jiggle_* / bone_skin_* are added under the model root (a frame that never rotates), the body's weight on its body
  bone (and that bone's parents) moves to them by direction, and bone_core replays the body bone's authored translation
  and squash (not its rotation): the body keeps its bounce and breathing, ears, wings and tails every clip, and the game's
  spring adds the squash and wobble of hops, landings and the vacpack.
* the body renderer comes first and is named "body" (the spring follows that renderer and sleeps while it is not visible).
* textures are stored RGBA; cache-manifest.json is rewritten with the new hashes.

    py -3.12 -m tools.largo_models.postpass <cache dir> [...]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

# native layout (offsets in units of the body's half size), read from the games' own Largo rigs
JIGGLE = (('bac', (0.0, 0.0, 1.0)), ('bot', (0.0, -1.0, 0.0)), ('fro', (0.0, 0.0, -1.0)),
          ('lef', (1.0, 0.0, 0.0)), ('rig', (-1.0, 0.0, 0.0)), ('top', (0.0, 1.0, 0.0)))
BODY_WIDTH = 0.976          # the games' slime body mesh, edge to edge


def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _trs(p, q, s):
    m = np.eye(4)
    m[:3, :3] = _quat_matrix(q) @ np.diag(s)
    m[:3, 3] = p
    return m


def worlds(bones):
    out = [None] * len(bones)

    def world(i):
        if out[i] is None:
            b = bones[i]
            local = _trs(b['p'], b['q'], b['s'])
            out[i] = local if b['parent'] < 0 else world(b['parent']) @ local
        return out[i]
    for i in range(len(bones)):
        world(i)
    return out


def _paths(bones):
    out = []
    for i in range(len(bones)):
        chain, j = [], i
        while j >= 0:
            chain.append(bones[j]['name'])
            j = bones[j]['parent']
        out.append('/'.join(reversed(chain)))
    return out


def _body(model):
    return next(p for p in model['skinned_renderers'] if p['role'] == 'body')


def _anchor(model):
    """The bone the body follows (most body weight) and, per body vertex, its weight on that bone."""
    body = _body(model)
    weights = np.array(body['bone_weights'], dtype=float)
    palette = body['skin_bones']
    totals = np.zeros(len(palette))
    for k in range(4):
        np.add.at(totals, weights[:, 4 + k].astype(int), weights[:, k])
    pal = int(totals.argmax())
    on = np.zeros(len(weights))
    for k in range(4):
        on += np.where(weights[:, 4 + k].astype(int) == pal, weights[:, k], 0)
    return palette[pal], on


def _core(model):
    verts = np.array(_body(model)['vertices'], dtype=float)
    _, on = _anchor(model)
    core = verts[on >= 0.6] if (on >= 0.6).sum() >= 50 else verts
    return verts, np.percentile(core, 2, axis=0), np.percentile(core, 98, axis=0)


def normalise_to_body(model: dict) -> dict:
    anchor_name, _ = _anchor(model)
    _, lo, hi = _core(model)
    f = BODY_WIDTH / max(hi[0] - lo[0], hi[2] - lo[2], 1e-6)
    shift = np.array([-(lo[0] + hi[0]) / 2 * f, -lo[1] * f, -(lo[2] + hi[2]) / 2 * f])
    T = np.eye(4)
    T[:3, :3] *= f
    T[:3, 3] = shift
    Tinv = np.linalg.inv(T)
    for part in model['skinned_renderers']:
        v = np.array(part['vertices'], dtype=float)
        part['vertices'] = np.round(v * f + shift, 5).tolist()
        part['bind_poses'] = [[round(float(x), 7) for x in (np.array(bp).reshape(4, 4) @ Tinv).reshape(-1)] for bp in part['bind_poses']]
    bones = model['skeleton']['bones']
    roots = {i for i, b in enumerate(bones) if b['parent'] < 0}
    for i in roots:
        b = bones[i]
        b['p'] = [round(float(x), 6) for x in np.array(b['p']) * f + shift]
        b['s'] = [round(float(x) * f, 6) for x in b['s']]
    paths = _paths(bones)
    root_paths = {paths[i] for i in roots}
    for clip in model.get('source_animation', {}).get('clips', []):
        for ch in clip['channels']:
            if ch['path'] not in root_paths:
                continue
            if ch['property'] == 'local_position':
                ch['values'] = [[round(float(x), 5) for x in np.array(v) * f + shift] for v in ch['values']]
            elif ch['property'] == 'local_scale':
                ch['values'] = [[round(float(x) * f, 5) for x in v] for v in ch['values']]
    return {'body_bone': anchor_name, 'scale': round(float(f), 4)}


def add_jiggle(model: dict) -> dict:
    bones = model['skeleton']['bones']
    names = [b['name'] for b in bones]
    if 'bone_core' in names:
        return {'jiggle': 'present'}
    body = _body(model)
    anchor_name, _ = _anchor(model)
    chain, j = set(), names.index(anchor_name)
    while j >= 0:
        chain.add(j)
        j = bones[j]['parent']
    verts, lo, hi = _core(model)
    centre, half = (lo + hi) / 2, np.maximum((hi - lo) / 2, 1e-4)
    paths = _paths(bones)
    # the body's authored motion (bounce, breathing squash) drives bone_core too: same translation and scale, never the
    # rotation (the spring runs in the root frame; a rotating frame is what made it shiver)
    anchor = names.index(anchor_name)
    order = []
    j = anchor
    while j >= 0:
        order.append(j)
        j = bones[j]['parent']
    order.reverse()
    rest_anchor = worlds(bones)[anchor]
    driven = 0
    for clip in model.get('source_animation', {}).get('clips', []):
        by_path = {(ch['path'], ch['property']): ch['values'] for ch in clip['channels']}
        positions, scales = [], []
        for k in range(len(clip['times'])):
            w = np.eye(4)
            for i in order:
                b = bones[i]
                pos = by_path.get((paths[i], 'local_position'), [b['p']] * (k + 1))[k]
                rot = by_path.get((paths[i], 'local_rotation'), [b['q']] * (k + 1))[k]
                sca = by_path.get((paths[i], 'local_scale'), [b['s']] * (k + 1))[k]
                w = w @ _trs(pos, rot, sca)
            delta = w @ np.linalg.inv(rest_anchor)
            positions.append([round(float(x), 5) for x in (delta @ np.append(centre, 1.0))[:3]])
            scales.append([round(float(x), 5) for x in np.linalg.norm(delta[:3, :3], axis=0)])
        clip['channels'].append({'path': 'bone_core', 'property': 'local_position', 'values': positions})
        clip['channels'].append({'path': 'bone_core', 'property': 'local_scale', 'values': scales})
        driven += 1
    core_index = len(bones)
    core_world = np.eye(4)
    core_world[:3, 3] = centre
    new = [{'name': 'bone_core', 'parent': -1, 'p': [round(float(v), 6) for v in centre], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]}]
    new_world = [core_world]
    for side, axis in JIGGLE:
        offset = np.array(axis) * half
        jw = np.eye(4)
        jw[:3, 3] = centre + offset
        new.append({'name': f'bone_jiggle_{side}', 'parent': core_index, 'p': [round(float(v), 6) for v in offset], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]})
        new_world.append(jw)
        new.append({'name': f'bone_skin_{side}', 'parent': len(bones) + len(new) - 1, 'p': [0.0, 0.0, 0.0], 'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]})
        new_world.append(jw)
    bones.extend(new)
    model['skeleton']['bone_count'] = len(bones)
    added = [b['name'] for b in new]
    binds = [[round(float(v), 7) for v in np.linalg.inv(w).reshape(-1)] for w in new_world]
    for part in model['skinned_renderers']:
        part['skin_bones'] = part['skin_bones'] + added
        part['bind_poses'] = part['bind_poses'] + binds
    palette = body['skin_bones']
    chain_pal = {palette.index(names[i]) for i in chain if names[i] in palette}
    targets = {side: palette.index('bone_jiggle_top' if side == 'top' else f'bone_skin_{side}') for side, _ in JIGGLE}
    axes = np.array([a for _, a in JIGGLE])
    moved, rows = 0, []
    for v, row in zip(verts, body['bone_weights']):
        pairs = [(row[k], int(row[4 + k])) for k in range(4) if row[k] > 0]
        wa = sum(w for w, b in pairs if b in chain_pal)
        if wa <= 0:
            rows.append(row)
            continue
        moved += 1
        d = (v - centre) / half
        n = np.linalg.norm(d)
        d = d / n if n > 1e-6 else np.array([0.0, 1.0, 0.0])
        share = np.clip(axes @ d, 0, None) ** 2
        best = np.argsort(-share)[:2]
        share = share[best] / max(share[best].sum(), 1e-9)
        pairs = [(w, b) for w, b in pairs if b not in chain_pal] + [(wa * s_, targets[JIGGLE[i][0]]) for s_, i in zip(share, best)]
        pairs = sorted(pairs, reverse=True)[:4]
        total = sum(w for w, _ in pairs)
        pairs = [(w / total, b) for w, b in pairs]
        while len(pairs) < 4:
            pairs.append((0.0, pairs[0][1]))
        ws = [round(w, 6) for w, _ in pairs]
        ws[0] = round(1.0 - sum(ws[1:]), 6)
        rows.append(ws + [b for _, b in pairs])
    body['bone_weights'] = rows
    model['skinned_renderers'].remove(body)
    model['skinned_renderers'].insert(0, body)
    body['mesh_name'] = 'body'
    body['path'] = body['path'].rsplit('/', 1)[0] + '/body'
    return {'jiggle': 'grafted', 'body_bone': anchor_name, 'vertices_moved': moved, 'clips_driving_core': driven}


# The games' ambient light is tinted (SR1's desert sky is cyan) and their glow blooms: measured in the LAB, the shown colour
# matches the author's when the self-light map is 0.7 x the baked colour + 0.3 x the baked glow and the scene light draws
# the base at 0.25 (shading only).
LIT_SHARE, GLOW_SHARE, EMIT_SHARE = 0.25, 0.7, 0.3


def glow_textures(model: dict, cache: Path) -> int:
    """Per material, a self-light map = baked colour x GLOW_SHARE + baked glow x EMIT_SHARE; the loaders draw the base at LIT_SHARE."""
    from PIL import Image
    made, done = 0, {}
    for part in model['skinned_renderers']:
        for material in part['materials']:
            files = {t['property']: t['file'] for t in material.get('textures', [])}
            if '_MainTex' not in files or material.get('shader') != 'SlimeBridge/Baked':
                continue
            key = (files['_MainTex'], files.get('_EmissionMap'))
            if key not in done:
                base = np.asarray(Image.open(cache / files['_MainTex']).convert('RGB'), dtype=float)
                glow = base * GLOW_SHARE
                if files.get('_EmissionMap'):
                    emit = Image.open(cache / files['_EmissionMap']).convert('RGB')
                    if emit.size != (base.shape[1], base.shape[0]):
                        emit = emit.resize((base.shape[1], base.shape[0]))
                    glow = glow + np.asarray(emit, dtype=float) * EMIT_SHARE
                name = files['_MainTex'].replace('-albedo-', '-glow-')
                if name == files['_MainTex']:
                    name = 'glow-' + name
                Image.fromarray(np.clip(glow, 0, 255).astype(np.uint8)).save(cache / name)
                done[key] = name
                made += 1
            material['textures'] = [t for t in material['textures'] if t['property'] != '_EmissionMap'] + [
                {'property': '_EmissionMap', 'file': done[key], 'source_color_space': 1}]
            material['floats']['_LitShare'] = LIT_SHARE
    for key in done:                       # the raw emission maps are folded into the glow maps
        if key[1] and not any(t['file'] == key[1] for part in model['skinned_renderers'] for m in part['materials'] for t in m['textures']):
            (cache / key[1]).unlink(missing_ok=True)
    return made


def rgba_textures(cache: Path) -> int:
    from PIL import Image
    changed = 0
    for png in sorted(cache.glob('tex-*.png')):
        image = Image.open(png)
        if image.mode != 'RGBA':
            image.convert('RGBA').save(png, optimize=True)
            changed += 1
    return changed


def rehash(cache: Path):
    manifest_path = cache / 'cache-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files'] = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(cache.iterdir())
                         if f.is_file() and f.name != 'cache-manifest.json'}
    manifest_path.write_text(json.dumps(manifest, separators=(',', ':')), encoding='utf-8')


def run(cache) -> dict:
    cache = Path(cache)
    model_path = cache / 'model.json'
    model = json.loads(model_path.read_text(encoding='utf-8'))
    already = any(b['name'] == 'bone_core' for b in model['skeleton']['bones'])
    result = {'size': 'kept' if already else normalise_to_body(model)}
    result.update(add_jiggle(model))
    result['glow_maps'] = glow_textures(model, cache)
    model_path.write_text(json.dumps(model, separators=(',', ':')), encoding='utf-8')
    result['rgba'] = rgba_textures(cache)
    report_path = cache / 'adapter-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
    report['postpass'] = result
    report_path.write_text(json.dumps(report, separators=(',', ':')), encoding='utf-8')
    rehash(cache)
    return result


if __name__ == '__main__':
    for arg in sys.argv[1:]:
        print(arg, run(arg))
