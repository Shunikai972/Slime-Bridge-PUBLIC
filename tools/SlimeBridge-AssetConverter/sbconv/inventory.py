"""Object inventory of an install: every serialized file / bundle -> (path_id, class, name) rows plus the
bundle container map. Built in isolated worker processes (one source file each, so memory stays
bounded) and cached incrementally by (size, mtime) under the work directory."""

import json
import multiprocessing as mp
import os
import time
from pathlib import Path

CACHE_VERSION = 2


def source_files(install):
    """Ordered list of Unity source files (relative to data_dir) that can hold gameplay assets."""
    data = Path(install['data_dir'])
    out = []
    for h in install['serialized_files']:
        out.append(h['name'])
    for ext in ('*.bundle', '*.unity3d', '*.assetbundle'):
        out += sorted(p.relative_to(data).as_posix() for p in data.rglob(ext))
    return out


def _scan_one(args):
    data_dir, rel = args
    import UnityPy
    path = os.path.join(data_dir, rel)
    t0 = time.time()
    rec = {'version': CACHE_VERSION, 'file': rel, 'size': os.path.getsize(path), 'mtime_ns': os.stat(path).st_mtime_ns,
           'objects': [], 'container': {}, 'dependencies': [], 'externals': {}, 'error': None}
    try:
        env = UnityPy.load(path)
        for rd in env.objects:
            t = rd.type.name
            name = None
            if t in ('GameObject', 'Mesh', 'Material', 'Texture2D', 'AnimationClip', 'Shader', 'AudioClip', 'Avatar',
                     'AnimatorController', 'RuntimeAnimatorController', 'Sprite', 'MonoBehaviour', 'MonoScript',
                     'TextAsset', 'AssetBundle', 'Cubemap', 'Texture3D', 'RenderTexture', 'Font', 'PhysicMaterial',
                     'AnimatorOverrideController'):
                try:
                    name = rd.peek_name()
                except Exception:
                    name = None
            rec['objects'].append([rd.assets_file.name if hasattr(rd.assets_file, 'name') else '', rd.path_id, t, name])
            if t == 'AssetBundle':
                tree = rd.read_typetree()
                rec['dependencies'] = tree.get('m_Dependencies', [])
                for k, v in tree.get('m_Container', []):
                    a = v.get('asset', {})
                    rec['container'][k] = [a.get('m_FileID', 0), a.get('m_PathID', 0)]
        for key, f in env.files.items():
            subs = getattr(f, 'files', None)
            for sk, sf in (subs.items() if isinstance(subs, dict) else [(key, f)]):
                ext = getattr(sf, 'externals', None)
                if ext is not None:
                    rec['externals'][sk] = [getattr(e, 'path', str(e)) for e in ext]
    except Exception as e:  # recorded, never raised: one bad source must not abort the inventory
        rec['error'] = f'{type(e).__name__}: {e}'
    rec['seconds'] = round(time.time() - t0, 2)
    return rec


def _cache_path(work, game, rel):
    safe = rel.replace('/', '__').replace('\\', '__')
    return Path(work) / 'inventory' / game / (safe + '.json')


def build(install, out_root, workers=4, progress=None, force=False):
    """Return {rel_file: record}. ``out_root`` is an OutputRoot (writes confined, atomic)."""
    game, data_dir = install['game'], install['data_dir']
    todo, results = [], {}
    for rel in source_files(install):
        src = Path(data_dir) / rel
        cp = _cache_path('', game, rel)
        cached = None if force else out_root.read_json(cp)
        if cached and cached.get('version') == CACHE_VERSION and cached.get('size') == src.stat().st_size \
                and cached.get('mtime_ns') == src.stat().st_mtime_ns and not cached.get('error'):
            results[rel] = cached
        else:
            todo.append((data_dir, rel))
    if todo:
        ctx = mp.get_context('spawn')
        with ctx.Pool(processes=max(1, workers), maxtasksperchild=1) as pool:
            for i, rec in enumerate(pool.imap_unordered(_scan_one, todo), 1):
                out_root.write_json(_cache_path('', game, rec['file']), rec)
                results[rec['file']] = rec
                if progress:
                    progress(i, len(todo), rec['file'], rec.get('error'))
    return results


def find(results, type_name=None, name=None, name_regex=None):
    import re
    rx = re.compile(name_regex, re.I) if name_regex else None
    for rel, rec in results.items():
        for cab, pid, t, n in rec['objects']:
            if type_name and t != type_name:
                continue
            if name is not None and n != name:
                continue
            if rx and not (n and rx.search(n)):
                continue
            yield rel, cab, pid, t, n


def cab_map(results):
    """CAB (archive) name, lower-case -> source file, so bundle dependencies can be resolved."""
    out = {}
    for rel, rec in results.items():
        for cab in {o[0] for o in rec['objects']}:
            out[str(cab).lower()] = rel
        for cab in rec.get('externals', {}):
            out.setdefault(str(cab).lower(), rel)
    return out


def closure(results, rels, include_always=()):
    """Dependency closure (bundle files) of the given source files, in stable order."""
    cabs = cab_map(results)
    seen, order, stack = set(), [], list(rels)
    while stack:
        rel = stack.pop(0)
        if rel in seen or rel not in results:
            continue
        seen.add(rel)
        order.append(rel)
        for dep in results[rel].get('dependencies', []):
            tgt = cabs.get(str(dep).lower())
            if tgt:
                stack.append(tgt)
    for extra in include_always:
        if extra not in seen and extra in results:
            order.append(extra)
    return order


def locate(results, container_path):
    """Container (Addressables asset path) -> (source file, path_id) or None. Case-insensitive."""
    low = container_path.lower()
    for rel, rec in results.items():
        for k, v in rec.get('container', {}).items():
            if k.lower() == low:
                return rel, v[1]
    return None
