"""One species -> one self-contained, hash-addressed IR package (``species.json`` + meshes/ textures/ anim/).

Status after this stage is EXTRACTED when no blocking problem was recorded, otherwise INCOMPLETE; no
claim about the target game is made here.
"""

import hashlib
import json
from pathlib import Path

from .. import __version__
from ..catalog import slug
from ..safefs import sha256_file
from .appearance import SpeciesExtractor, ExtractionProblem, _reader_from_key
from . import prefab as pf

SCHEMA = 'sbconv-ir'
SCHEMA_VERSION = 1
BLOCKING = {'error'}


def canonical_id(row):
    if row.get('is_largo') and len(row.get('base_slimes') or []) == 2 and all(row['base_slimes']):
        a, b = sorted(slug(n) for n in row['base_slimes'])
        return f'largo.{a}.{b}'
    return f'slime.{slug(row["name"])}'


def _hash_cache(work_root):
    p = Path(work_root) / 'hashes.json'
    try:
        return p, json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return p, {}


def source_hashes(session, rels, work_root):
    """sha256 of every install file an extraction touched (cached by size+mtime)."""
    p, cache = _hash_cache(work_root)
    out = {}
    changed = False
    for rel in sorted(rels):
        f = session.data_dir / rel
        if not f.is_file():
            continue
        st = f.stat()
        ent = cache.get(str(f))
        if not ent or ent['size'] != st.st_size or ent['mtime_ns'] != st.st_mtime_ns:
            ent = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns, 'sha256': sha256_file(f)}
            cache[str(f)] = ent
            changed = True
        out[rel] = ent['sha256']
    if changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, indent=1), encoding='utf-8')
    return out


def _walk_refs(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_refs(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _walk_refs(v, acc)
    elif isinstance(obj, str) and '#' in obj:
        name = obj.rsplit('#', 1)[0]
        if name and not name.startswith(('/', ' ')):
            acc.add(name)


def _animation_section(session, anim, doc, writer, problems):
    prefab = doc['prefab']
    nodes = prefab['nodes']
    sec = {'controller': None, 'clips': {}, 'overrides': {}, 'avatar': None, 'animator_node': None}
    if anim is None:
        problems.add('warning', 'ANIM_ENGINE_MISSING', 'animation export unavailable: clips/controllers not extracted')
        return sec
    animators = prefab['animators']
    if not animators:
        problems.add('warning', 'NO_ANIMATOR', 'prefab has no Animator')
        return sec
    a = animators[0]
    sec['animator_node'] = a['node']
    base_path = nodes[a['node']]['path']
    skel_paths = {n['path'][len(base_path) + 1:] for n in nodes if n['path'].startswith(base_path + '/')}
    ctrl_rd = _reader_from_key(session, a.get('controller'))
    av_rd = _reader_from_key(session, a.get('avatar'))
    sec['avatar'] = av_rd.peek_name() if av_rd is not None else None
    if ctrl_rd is None:
        problems.add('warning', 'CONTROLLER_UNRESOLVED', 'Animator controller reference not found', ref=a.get('controller'))
        return sec
    cname = ctrl_rd.peek_name()
    ctrl = anim.controller(cname)
    if ctrl is None:
        problems.add('error', 'CONTROLLER_NOT_EXPORTED', f'controller {cname} missing from animation export')
        return sec
    sec['controller'] = ctrl
    wanted = {asset: None for asset in anim.clip_assets(ctrl)}
    ap = doc['appearance']['extras']
    for key in ('idle_override', 'wing_flap_override', 'bite_override'):
        rd = _reader_from_key(session, ap.get(key))
        if rd is not None:
            sec['overrides'][key] = rd.peek_name()
            wanted[rd.peek_name()] = None
    ov_rd = _reader_from_key(session, ap.get('animator_override'))
    if ov_rd is not None:
        sec['overrides']['animator_override_controller'] = ov_rd.peek_name()
        for cand in anim.by_name['override'].get(ov_rd.peek_name(), [])[:1]:
            from . import animation as A
            for cid, _, body in A.load_unity_yaml(cand):
                if cid == 221:
                    pairs = []
                    for c in body.get('m_Clips') or []:
                        o = A_guid(anim, (c.get('m_OriginalClip') or {}).get('guid'))
                        n = A_guid(anim, (c.get('m_OverrideClip') or {}).get('guid'))
                        pairs.append({'original': o, 'override': n})
                        if n:
                            wanted[n] = None
                    sec['overrides']['clip_pairs'] = pairs
    for asset in list(wanted):
        clip = anim.clip(asset)
        if clip is None:
            problems.add('error', 'CLIP_MISSING', f'clip {asset} not in animation export')
            continue
        from .animation import resolve_hashed_paths
        resolved = resolve_hashed_paths(clip, skel_paths)
        clip['resolved_hashed_paths'] = resolved
        miss = [p for p in _clip_paths(clip) if p and p not in skel_paths]
        if miss:
            problems.add('info', 'CLIP_FOREIGN_BONES', f'{clip["name"]}: {len(miss)} curve path(s) target bones this species does not have (dropped at build)', paths=miss[:6])
        clip['missing_paths'] = miss
        rel = writer.put_json(f'anim/clips/{hashlib.sha256(json.dumps(clip, sort_keys=True).encode()).hexdigest()[:16]}.json', clip)
        sec['clips'][asset] = {'name': clip['name'], 'file': rel, 'length': clip['length'], 'loop': clip['loop'],
                               'counts': clip['curve_counts'], 'events': len(clip['events']), 'missing_paths': len(miss)}
    return sec


def A_guid(anim, guid):
    return anim.guids.get(guid) if guid else None


def _clip_paths(clip):
    from .animation import clip_paths
    return clip_paths(clip)


def gameplay_section(prefab):
    comps, colliders, bodies = [], [], []
    for i, n in enumerate(prefab['nodes']):
        for c in n['components']:
            if c['type'] == 'MonoBehaviour':
                comps.append({'node': i, 'path': n['path'], 'script': c.get('script'), 'assembly': c.get('assembly'), 'enabled': c.get('enabled')})
            elif c['type'] in pf.COLLIDER_TYPES:
                colliders.append({'node': i, 'path': n['path'], **{k: v for k, v in c.items() if k not in ('ref',)}})
            elif c['type'] == 'Rigidbody':
                bodies.append({'node': i, 'path': n['path'], **{k: v for k, v in c.items() if k not in ('ref',)}})
    return {'behaviours': comps, 'colliders': colliders, 'rigidbodies': bodies,
            'note': 'Native/IL2CPP scripts are recorded as metadata only; behaviour is re-implemented by the runtime adapter of the target game.'}


def extract_species(session, install, row, writer, work_root, anim=None, appearance_index=0):
    problems = ExtractionProblem()
    ex = SpeciesExtractor(session, install, writer, problems)
    doc = ex.extract(row, appearance_index)
    if doc is None:
        return {'status': 'INCOMPLETE', 'problems': problems.items}
    doc['animation'] = _animation_section(session, anim, doc, writer, problems)
    doc['gameplay'] = gameplay_section(doc['prefab'])
    refs = set()
    _walk_refs({k: doc[k] for k in ('prefab', 'appearance', 'materials', 'textures', 'meshes')}, refs)
    rels = set()
    for name in refs:
        f = session.file(name)
        rel = session._owner.get(id(f)) if f is not None else None
        if rel:
            rels.add(rel)
    kind = 'largo' if row.get('is_largo') else 'base'
    manifest = {
        'schema': SCHEMA, 'schema_version': SCHEMA_VERSION, 'tool_version': __version__,
        'source': {'game': install['game'], 'unity_version': install['unity']['version'], 'backend': install['backend'],
                   'pipeline': install['graphics'].get('pipeline'), 'color_space': install['color_space'],
                   'game_version': install['game_version']['value'], 'fingerprint': install['fingerprint'],
                   'recognition': install['recognition']['status'], 'engine': 'unitypy+assetripper'},
        'species': {'name': row['name'], 'display': row.get('display'), 'kind': kind, 'canonical_id': canonical_id(row),
                    'base_slimes': row.get('base_slimes') or [], 'identifiable_id': row.get('identifiable_id'),
                    'prefab_scale': row.get('prefab_scale'), 'definition': row['ref'], 'color': row.get('color')},
        'prefab': doc['prefab'], 'appearance': doc['appearance'], 'animation': doc['animation'], 'gameplay': doc['gameplay'],
        'materials': doc['materials'], 'textures': doc['textures'], 'meshes': doc['meshes'],
        'provenance': {'sources': source_hashes(session, rels, work_root)},
        'problems': problems.items,
    }
    blocking = [p for p in problems.items if p['level'] in BLOCKING]
    manifest['status'] = 'INCOMPLETE' if blocking else 'EXTRACTED'
    manifest['files'] = dict(sorted(writer.files.items()))
    writer.put_json('species.json', manifest)
    return manifest
