"""Stable Python API (``API_VERSION`` in ``sbconv``). The CLI and the JSON line protocol are thin wrappers
over these functions. Every function returns a plain JSON-serialisable dict with an ``ok`` flag and never
raises for per-species failures (those are reported in ``results``)."""

import json
import traceback
from pathlib import Path

from . import API_VERSION, __version__
from .detect import discovery, install

DIRECTIONS = {'sr1-to-sr2': ['SR1'], 'sr2-to-sr1': ['SR2'], 'both': ['SR1', 'SR2']}


def scan(sr1_path=None, sr2_path=None, search_roots=(), deep=False, full_hash=True):
    found = discovery.discover(extra_roots=search_roots, explicit={'SR1': sr1_path, 'SR2': sr2_path}, deep=deep)
    games, hints, diags = {}, {}, []
    for game, cands in found.items():
        if not cands:
            hints[game] = [f'no install found; pass --{game.lower()}-path <folder>']
            continue
        chosen = cands[0]
        try:
            games[game] = install.analyse(chosen.root, game, full_hash=full_hash)
            games[game]['discovery'] = {'source': chosen.source, 'other_candidates': [c.root for c in cands[1:]]}
        except Exception as e:  # keep scanning the other game; report precisely
            diags.append({'level': 'error', 'code': 'ANALYSE_FAILED', 'game': game, 'message': f'{type(e).__name__}: {e}'})
    ok = bool(games) and not any(d['level'] == 'error' for d in diags)
    return {'ok': ok, 'api_version': API_VERSION, 'tool_version': __version__, 'games': games,
            'not_found_hints': hints, 'diagnostics': diags}


def _ctx(output, sr1_path, sr2_path, log, dry_run=False, search_roots=(), deep=False, workers=3):
    from .context import Context
    return Context(output, sr1_path=sr1_path, sr2_path=sr2_path, search_roots=search_roots, deep=deep, log=log or (lambda m: None),
                   workers=workers, dry_run=dry_run)


def engines(sr1_path=None, sr2_path=None):
    from .engines import assetripper
    from .engines.unitypy_engine import UnityPyEngine
    out = {'ok': True, 'engines': [UnityPyEngine().capabilities().as_dict(), assetripper.capabilities()], 'unity_editors': unity_editors()}
    return out


def unity_editors():
    """Installed Unity Editors (detected, never launched). Needed only for the optional AssetBundle builder."""
    import os
    roots = [r'C:\Program Files\Unity\Hub\Editor', r'D:\Unity\Editor', os.path.expandvars(r'%ProgramFiles%\Unity\Editor')]
    roots += [str(p) for p in os.environ.get('SBCONV_UNITY_ROOTS', '').split(os.pathsep) if p]
    found = []
    for r in roots:
        p = Path(r)
        if not p.is_dir():
            continue
        if (p / 'Editor' / 'Unity.exe').is_file():
            found.append({'path': str(p / 'Editor' / 'Unity.exe'), 'version': p.name})
        for v in p.iterdir():
            for exe in (v / 'Editor' / 'Unity.exe', v / 'Editor' / 'Editor' / 'Unity.exe'):    # 2nd: installer run with /D=...\Editor
                if exe.is_file():
                    found.append({'path': str(exe), 'version': v.name})
                    break
    lic = [Path(os.environ.get('PROGRAMDATA', r'C:\ProgramData')) / 'Unity' / 'Unity_lic.ulf']
    return {'editors': found, 'license_file_present': any(p.is_file() for p in lic),
            'note': 'AssetBundle building requires an editor matching the target Unity version and a license you activate yourself; '
                    'sbconv never activates or bypasses licensing.'}


def _select(ctx, game, slimes, kind, allf, limit, matched=None, only_missing=False):
    """Rows of ``game`` chosen by name / --all / --kind. Names are recorded in ``matched`` when found so that
    a direction covering both games only reports names found in neither."""
    from .extract.species import canonical_id
    rows = [r for r in ctx.catalog(game) if 'error' not in r]
    other = 'SR2' if game == 'SR1' else 'SR1'
    present = ctx.playable_ids(other) if (only_missing and other in ctx.installs) else set()
    keep = (lambda r: canonical_id(r) not in present) if only_missing else (lambda r: True)
    if slimes:
        out = []
        for s_ in slimes:
            try:
                row = ctx.find_row(game, s_)
            except Exception:
                continue
            if matched is not None:
                matched.add(s_)
            if keep(row):
                out.append(row)
        return out
    if not allf:
        return []
    if kind in ('base', 'largo'):
        rows = [r for r in rows if bool(r.get('is_largo')) == (kind == 'largo')]
    rows = [r for r in rows if keep(r)]
    return rows[:limit] if limit else rows


def list_species(output, sr1_path=None, sr2_path=None, game='both', kind='all', log=None):
    from .extract.species import canonical_id
    from . import pipeline
    ctx = _ctx(output, sr1_path, sr2_path, log, dry_run=False)
    led = pipeline.ledger(ctx)['entries']
    rows_out = []
    games = ['SR1', 'SR2'] if game == 'both' else [game.upper()]
    cats = {g: ctx.catalog(g) for g in ('SR1', 'SR2') if g in ctx.installs}
    ids = {g: ctx.playable_ids(g) for g in cats}
    for g in games:
        tgt = 'SR2' if g == 'SR1' else 'SR1'
        for r in cats.get(g, []):
            if 'error' in r:
                continue
            if kind != 'all' and bool(r.get('is_largo')) != (kind == 'largo'):
                continue
            cid = canonical_id(r)
            st = led.get(f'{g}:{cid}->{tgt}') or led.get(f'{g}:{cid}')
            rows_out.append({'game': g, 'name': r['name'], 'display': r.get('display'), 'canonical_id': cid,
                             'kind': 'largo' if r.get('is_largo') else 'base', 'base_slimes': r.get('base_slimes'),
                             'native_in_target': cid in ids.get(tgt, set()), 'playable_in_source': cid in ids.get(g, set()), 'status': st['status'] if st else None,
                             'stage': st.get('stage') if st else None})
    return {'ok': True, 'count': len(rows_out), 'species': rows_out}


def convert(output, direction='both', slimes=(), kind='all', all_species=False, sr1_path=None, sr2_path=None, dry_run=False,
            force=False, limit=None, log=None, workers=3, stop_on_error=False, allow_unvalidated=False, only_missing=False):
    from . import build, convert as conv
    from .extract.species import canonical_id
    ctx = _ctx(output, sr1_path, sr2_path, log, dry_run=dry_run, workers=workers)
    results, plan, matched = [], [], set()
    for src in DIRECTIONS[direction]:
        if src not in ctx.installs:
            results.append({'game': src, 'ok': False, 'error': f'{src} install unavailable'})
            continue
        for row in _select(ctx, src, list(slimes), kind, all_species, limit, matched, only_missing):
            plan.append({'source': src, 'target': conv.other(src), 'name': row['name'], 'canonical_id': canonical_id(row)})
    for name in slimes:
        if name not in matched:
            results.append({'game': '/'.join(DIRECTIONS[direction]), 'ok': False, 'error': f'no species {name!r} in the selected source game(s)'})
    if dry_run:
        return {'ok': True, 'dry_run': True, 'would_convert': plan, 'count': len(plan), 'output': str(output),
                'note': 'nothing was written; no game file was modified'}
    blocked = []
    if not allow_unvalidated:
        for g, inst in ctx.installs.items():
            if inst['recognition']['status'] in ('UNKNOWN_ENGINE_VERSION', 'UNRECOGNIZED'):
                blocked.append({'game': g, 'status': inst['recognition']['status'], 'unity': inst['unity']['version'],
                                'diagnostics': [d for d in inst['diagnostics'] if d['level'] != 'info']})
    if blocked:
        return {'ok': False, 'blocked': blocked, 'results': [], 'count': 0, 'failed': [],
                'error': 'install version not validated; original files untouched. Send `sbconv scan --json` for a compatibility report, '
                         'or pass allow_unvalidated=True (--allow-unvalidated) to attempt anyway (results stay unverified).'}
    from .safefs import RunLock
    lock = RunLock(ctx.out.root)
    lock.__enter__()
    try:
        return _run_plan(ctx, plan, results, force, stop_on_error, output)
    finally:
        lock.__exit__(None, None, None)


def _run_plan(ctx, plan, results, force, stop_on_error, output):
    from . import build, convert as conv
    for p in plan:
        try:
            pk = build.build_runtime(ctx, p['source'], p['name'], force=force)
            results.append({**p, 'ok': pk['status'] == 'REBUILT', 'status': pk['status'], 'fidelity': pk['material_fidelity']['worst_fidelity'],
                            'problems': [x for x in pk.get('problems', []) if x['level'] == 'error'][:5],
                            'package': f"runtime/{conv.direction_key(p['source'])}/{p['canonical_id']}"})
        except Exception as e:
            from . import pipeline
            pipeline.record(ctx, f"{p['source']}:{p['canonical_id']}->{p['target']}", status='INCOMPLETE', stage='error',
                            error=f'{type(e).__name__}: {e}', name=p['name'])
            results.append({**p, 'ok': False, 'status': 'INCOMPLETE', 'error': f'{type(e).__name__}: {e}', 'trace': traceback.format_exc(limit=4)})
            if stop_on_error:
                break
    ok = bool(results) and all(r['ok'] for r in results)
    return {'ok': ok, 'results': results, 'count': len(results), 'failed': [r for r in results if not r['ok']], 'output': str(output)}


def verify(output, direction='both', slimes=(), kind='all', all_species=False, sr1_path=None, sr2_path=None, log=None, glb=True, only_missing=False):
    from . import convert as conv, pipeline
    from .extract.species import canonical_id
    from .verify.runtime import verify_runtime
    from .verify.glb_roundtrip import verify_glb
    ctx = _ctx(output, sr1_path, sr2_path, log)
    results, matched = [], set()
    for src in DIRECTIONS[direction]:
        if src not in ctx.installs:
            continue
        tgt = conv.other(src)
        for row in _select(ctx, src, list(slimes), kind, all_species, None, matched, only_missing):
            cid = canonical_id(row)
            base = ctx.out.root / 'runtime' / conv.direction_key(src) / cid
            if not (base / 'package.json').is_file():
                results.append({'source': src, 'canonical_id': cid, 'ok': False, 'status': 'NOT_BUILT', 'failed': []})
                continue
            R, pk = verify_runtime(base, templates=ctx.templates(tgt))
            s = R.summary()
            rt = []
            if glb:
                irdir = ctx.out.root / 'ir' / src / cid
                try:
                    rt = verify_glb(irdir, base / 'preview.glb')
                except Exception as e:
                    rt = [{'ok': False, 'detail': f'{type(e).__name__}: {e}'}]
                if rt and not all(x['ok'] for x in rt):
                    s['passed'] = False
                    s['failed'].append({'id': 'glb.roundtrip', 'detail': [x for x in rt if not x['ok']][:3]})
            status = 'OFFLINE_VERIFIED' if s['passed'] else 'INCOMPLETE'
            led_key = f'{src}:{cid}->{tgt}'
            prev = pipeline.ledger(ctx)['entries'].get(led_key, {})
            if s['passed'] and prev.get('status') in ('UNITY_VERIFIED', 'GAME_RUNTIME_VERIFIED') and prev.get('package_key') == pk.get('params_key'):
                status = prev['status']        # evidence is monotonic: an offline pass never downgrades in-engine proof for the same package
            else:
                pipeline.record(ctx, led_key, status=status, stage='verify', verify_failed=[c['id'] for c in s['failed']],
                                verify_warnings=[c['id'] for c in s['warnings']], package_key=pk.get('params_key'))
            results.append({'source': src, 'target': tgt, 'canonical_id': cid, 'ok': s['passed'], 'status': status, 'checks': s['checks'],
                            'failed': s['failed'], 'warnings': [{'id': c['id'], 'detail': c['detail']} for c in s['warnings']],
                            'glb_roundtrip_ok': all(x['ok'] for x in rt) if rt else None})
    return {'ok': bool(results) and all(r['ok'] for r in results), 'results': results,
            'note': 'OFFLINE_VERIFIED proves internal consistency only; UNITY_VERIFIED / GAME_RUNTIME_VERIFIED require in-engine evidence.'}


def status(output, sr1_path=None, sr2_path=None):
    from . import pipeline
    from .safefs import OutputRoot
    out = OutputRoot(output, dry_run=True)
    led = out.read_json(pipeline.LEDGER, {'entries': {}})
    entries = [{'key': k, **v} for k, v in sorted(led['entries'].items())]
    hist = {}
    for e in entries:
        hist[e.get('status')] = hist.get(e.get('status'), 0) + 1
    return {'ok': True, 'count': len(entries), 'summary': hist, 'entries': entries}


def preview(output, direction='both', slimes=(), kind='all', all_species=False, sr1_path=None, sr2_path=None, log=None, only_missing=False):
    """Software preview sheets (4 views) of built runtime packages, written next to each package."""
    from . import convert as conv
    from .extract.species import canonical_id
    from .preview import render_package
    ctx = _ctx(output, sr1_path, sr2_path, log)
    results, matched = [], set()
    for src in DIRECTIONS[direction]:
        if src not in ctx.installs:
            continue
        for row in _select(ctx, src, list(slimes), kind, all_species, None, matched, only_missing):
            cid = canonical_id(row)
            base = ctx.out.root / 'runtime' / conv.direction_key(src) / cid
            if not (base / 'package.json').is_file():
                results.append({'source': src, 'canonical_id': cid, 'ok': False, 'error': 'not built'})
                continue
            try:
                info = render_package(base, base / 'preview.png')
                results.append({'source': src, 'canonical_id': cid, 'ok': True, **info})
            except Exception as e:
                results.append({'source': src, 'canonical_id': cid, 'ok': False, 'error': f'{type(e).__name__}: {e}'})
    return {'ok': bool(results) and all(r['ok'] for r in results), 'results': results,
            'note': 'Inspection aid only: palette/baked colours in rest pose; faces, effects and the game shaders are not rendered.'}


# When shipped inside SlimeBridge (tools/SlimeBridge-AssetConverter), the SlimeBridge repository is two levels up.
DEFAULT_BRIDGE_REPO = str(Path(__file__).resolve().parents[3]) if (Path(__file__).resolve().parents[3] / 'bridge').is_dir() else ''


def export_bridge(output, direction='both', slimes=(), kind='all', all_species=False, sr1_path=None, sr2_path=None, log=None,
                  only_missing=False, largo_style='slime-compat', repo=None, force=False, workers=3, largo_icon_from_parent=True):
    """Write SlimeBridge ``bridge-native-mesh`` caches (the format the SR1/SR2 plugins load) and validate each one with a
    port of the plugin parser (+ the SlimeBridge repo's own scanner when ``repo`` exists). Output:
    ``<output>/bridge-cache/<SRC>-to-<TGT>/<id>/``; copy a folder to the plugin's asset-cache directory to use it."""
    from . import convert as conv, pipeline
    from .export.bridge_cache import export as bc_export, BridgeExportError
    from .extract.species import canonical_id
    from .safefs import RunLock
    from .verify.bridge_cache import validate_cache
    ctx = _ctx(output, sr1_path, sr2_path, log, workers=workers)
    repo = repo or (DEFAULT_BRIDGE_REPO or None)
    results, matched = [], set()
    with RunLock(ctx.out.root):
        for src in DIRECTIONS[direction]:
            if src not in ctx.installs:
                continue
            for row in _select(ctx, src, list(slimes), kind, all_species, None, matched, only_missing):
                cid = canonical_id(row)
                try:
                    manifest = pipeline.extract(ctx, src, row['name'], force=force)
                    if manifest['status'] != 'EXTRACTED':
                        raise BridgeExportError('extraction INCOMPLETE: ' + '; '.join(p['message'] for p in manifest['problems'] if p['level'] == 'error')[:300])
                    pkg = ctx.out.root / pipeline.ir_dir(src, cid)
                    recipes = ctx.out.root / 'converted' / conv.direction_key(src) / cid / 'convert.json'
                    dest = ctx.out.root / 'bridge-cache' / conv.direction_key(src) / cid
                    import shutil
                    if dest.exists():
                        shutil.rmtree(dest)
                    fallback = None
                    if largo_icon_from_parent and not manifest['appearance'].get('icon') and row.get('is_largo'):
                        for pname in sorted(n for n in (row.get('base_slimes') or []) if n):
                            pm = pipeline.extract(ctx, src, pname)
                            if pm.get('appearance', {}).get('icon'):
                                fallback = (ctx.out.root / pipeline.ir_dir(src, pm['species']['canonical_id']), pm['appearance']['icon'], pm['species']['canonical_id'])
                                break
                    info = bc_export(pkg, dest, largo_style=largo_style, recipes_path=None, icon_fallback=fallback)
                    rep = validate_cache(dest, repo=repo)
                    pipeline.record(ctx, f"{src}:{cid}->{conv.other(src)}:bridge-cache", status='REBUILT' if rep['ok'] else 'INCOMPLETE',
                                    stage='export-bridge', problems=rep['problems'][:5], bridge_canonical_id=info['canonical_id'])
                    results.append({'source': src, 'target': conv.other(src), 'canonical_id': cid, 'bridge_canonical_id': info['canonical_id'],
                                    'ok': rep['ok'], 'problems': rep['problems'], 'warnings': rep['warnings'], 'files': info['files'],
                                    'model_bytes': info['model_bytes'], 'clips': info['clips'],
                                    'path': f"bridge-cache/{conv.direction_key(src)}/{cid}"})
                except BridgeExportError as e:
                    results.append({'source': src, 'target': conv.other(src), 'canonical_id': cid, 'ok': False, 'problems': [str(e)], 'warnings': []})
                except Exception as e:
                    results.append({'source': src, 'target': conv.other(src), 'canonical_id': cid, 'ok': False,
                                    'problems': [f'{type(e).__name__}: {e}'], 'warnings': []})
    for name in slimes:
        if name not in matched:
            results.append({'ok': False, 'canonical_id': name, 'problems': [f'no species {name!r} in the selected source game(s)'], 'warnings': []})
    return {'ok': bool(results) and all(r['ok'] for r in results), 'results': results, 'count': len(results),
            'note': 'Plugin-side acceptance is checked offline (parser port + repo scanner); in-game loading is not claimed. '
                    'Largos use ids "slime.largo.a.b" by default because the plugin only accepts "slime.*".'}


def unity_verify(output, direction='both', slimes=(), kind='all', all_species=False, sr1_path=None, sr2_path=None, log=None,
                 only_missing=False, limit=None):
    """UNITY_VERIFIED: load each built package in the Unity Editor that matches the TARGET game (batch mode, your own license),
    let Unity skin/animate it and compare with this tool's maths. Needs `dotnet build` of csharp/ and the editor installed."""
    from . import convert as conv, pipeline, unity_verify as uv
    from .extract.species import canonical_id
    ctx = _ctx(output, sr1_path, sr2_path, log)
    results, matched, n = [], set(), 0
    for src in DIRECTIONS[direction]:
        if src not in ctx.installs:
            continue
        tgt = conv.other(src)
        version = ctx.install(tgt)['unity']['version']
        for row in _select(ctx, src, list(slimes), kind, all_species, limit, matched, only_missing):
            cid = canonical_id(row)
            base = ctx.out.root / 'runtime' / conv.direction_key(src) / cid
            if not (base / 'package.json').is_file():
                results.append({'source': src, 'canonical_id': cid, 'ok': False, 'status': 'NOT_BUILT'})
                continue
            try:
                r = uv.verify(base, version, ctx.out.root / 'work' / 'unity')
            except Exception as e:
                r = {'ok': False, 'status': 'ERROR', 'error': f'{type(e).__name__}: {e}'}
            key = f"{src}:{cid}->{tgt}"
            if r.get('ok'):
                # in-engine proof never overrides an offline failure (e.g. a core material with no equivalent in the target game)
                from .verify.runtime import verify_runtime
                R, pk = verify_runtime(base, templates=ctx.templates(tgt))
                off = R.summary()
                pkey = pk.get('params_key')
                if off['passed']:
                    pipeline.record(ctx, key, status='UNITY_VERIFIED', stage='unity-verify', unity_version=r.get('unity'),
                                    pose_comparison=r.get('pose_comparison'), screenshot=r.get('screenshot'), package_key=pkey)
                else:
                    pipeline.record(ctx, key, status='INCOMPLETE', stage='verify', verify_failed=[c['id'] for c in off['failed']],
                                    unity_loaded_ok=True, unity_version=r.get('unity'), package_key=pkey)
                    r = {**r, 'ok': False, 'status': 'INCOMPLETE', 'error': 'offline verification failed: ' + ', '.join(c['id'] for c in off['failed'])}
            results.append({'source': src, 'target': tgt, 'canonical_id': cid, 'ok': bool(r.get('ok')), 'status': r.get('status'),
                            'unity': r.get('unity') or version, 'pose_comparison': r.get('pose_comparison'), 'screenshot': r.get('screenshot'),
                            'error': r.get('error') or (r.get('report') or {}).get('errors')})
    return {'ok': bool(results) and all(x['ok'] for x in results), 'results': results,
            'note': 'Placeholder (Standard) shaders: proves loading, skeleton, skinning, animation and references in the matching Unity version, not the games shaders.'}
