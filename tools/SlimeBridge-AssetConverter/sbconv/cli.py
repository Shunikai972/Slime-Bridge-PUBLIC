"""sbconv command line. Human output by default, ``--json`` for machine output, ``serve`` for the launcher's
JSON-lines protocol (see docs/LAUNCHER_PROTOCOL.md)."""

import argparse
import json
import sys

from . import api, __version__

DEFAULT_OUTPUT = 'output'


def _emit(args, payload, human):
    if getattr(args, 'json', False):
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + '\n')
    else:
        human(payload)
    return 0 if payload.get('ok', True) else 1


def _paths(p):
    p.add_argument('--sr1-path', help='Slime Rancher 1 install folder (wrapper folders are searched)')
    p.add_argument('--sr2-path', help='Slime Rancher 2 install folder')
    p.add_argument('--json', action='store_true', help='machine-readable output')


def _out(p):
    p.add_argument('--output', '-o', default=DEFAULT_OUTPUT, help=f'output/work directory (default ./{DEFAULT_OUTPUT}); never inside a game folder')


def _select(p):
    p.add_argument('--direction', choices=['sr1-to-sr2', 'sr2-to-sr1', 'both'], default='both')
    p.add_argument('--slime', action='append', default=[], help='species name or canonical id (repeatable, comma separated ok)')
    p.add_argument('--all', dest='all_species', action='store_true', help='select every species of the source game(s)')
    p.add_argument('--kind', choices=['all', 'base', 'largo'], default='all')
    p.add_argument('--limit', type=int, help='stop after N species (testing)')
    p.add_argument('--only-missing', action='store_true', default=False,
                   help='only species a player cannot meet in the OTHER game (largos included); default: everything')


def _slimes(args):
    return [s.strip() for item in args.slime for s in item.split(',') if s.strip()]


def _print_scan(res):
    for game in ('SR1', 'SR2'):
        info = res['games'].get(game)
        print(f'== {game}')
        if not info:
            print('   not found')
            for d in res['not_found_hints'].get(game, []):
                print('   -', d)
            continue
        u = info['unity']
        print(f"   root         {info['root']}")
        print(f"   game version {info['game_version']['value']} (from {info['game_version']['source']})")
        print(f"   unity        {u['version']} (serialized v{u['serialized_format']})")
        print(f"   platform     {info['platform']} / {info['architecture']} / {info['backend']}")
        g = info['graphics']
        print(f"   rendering    pipeline={g.get('pipeline')} color_space={info['color_space']} always-included shaders={g.get('always_included_shaders')}")
        tt = info['type_tree']
        print(f"   typetree     ggm={tt['globalgamemanagers']}  with={tt['files_with_typetree']} without={tt['files_without_typetree']}")
        b = info['bundles']
        print(f"   bundles      {b['count']} ({b['total_bytes'] / 1e6:.0f} MB) addressables={b['addressables']} engines={b['engine_versions']}")
        print(f"   resources    {', '.join(info['asset_directories']['resource_containers'][:8])}")
        print(f"   recognition  {info['recognition']['status']}")
        for d in info['diagnostics']:
            print(f"   [{d['level']}] {d['code']}: {d['message']}")


def _print_list(res):
    print(f"{res['count']} species")
    for r in res['species']:
        flag = ' (already playable in target)' if r['native_in_target'] else ' (MISSING in target)'
        print(f"  {r['game']}  {r['kind']:<5} {r['canonical_id']:<34} {r['name']:<22} {str(r['status'] or '-'):<18}{flag}")


def _print_convert(res):
    if res.get('blocked'):
        print('BLOCKED:', res['error'])
        for b in res['blocked']:
            print(f"  {b['game']}: {b['status']} (Unity {b['unity']})")
        return
    if res.get('dry_run'):
        print(f"dry run: {res['count']} conversion(s) would run (nothing written)")
        for p in res['would_convert']:
            print(f"  {p['source']} -> {p['target']}  {p['canonical_id']}  ({p['name']})")
        return
    for r in res['results']:
        if 'canonical_id' not in r:
            print(f"  {r.get('game')}: {r.get('error')}")
            continue
        extra = f"fidelity={r.get('fidelity')}" if r['ok'] else f"ERROR {r.get('error', '')[:120]}"
        print(f"  {r['source']} -> {r['target']}  {r['canonical_id']:<34} {r['status']:<10} {extra}")
    print(f"{res['count']} processed, {len(res['failed'])} failed. Output: {res['output']}")


def _print_verify(res):
    for r in res['results']:
        print(f"  {r['source']}:{r['canonical_id']:<34} {r['status']:<16} checks={r.get('checks', '-')}  failed={[c['id'] for c in r['failed']]}")
        for w in r.get('warnings', []):
            print(f"      warn {w['id']}: {str(w['detail'])[:150]}")
    print(res['note'])


def _print_status(res):
    print(f"{res['count']} entries; summary {res['summary']}")
    for e in res['entries']:
        print(f"  {e['key']:<44} {e.get('status', '?'):<18} stage={e.get('stage')}  fidelity={e.get('worst_material_fidelity', '-')}")


def _print_engines(res):
    for e in res['engines']:
        print(f"  {e['name']} {e['version']}  license={e['license']}  notes={e.get('notes')}")
    ue = res['unity_editors']
    print(f"  Unity editors found: {ue['editors'] or 'none'}; license file present: {ue['license_file_present']}")
    print(' ', ue['note'])


def build_parser():
    ap = argparse.ArgumentParser(prog='sbconv', description='SlimeBridge AssetConverter: local SR1 <-> SR2 slime asset conversion. '
                                 'Reads your own game installs (read-only) and writes only under --output.')
    ap.add_argument('--version', action='version', version=f'sbconv {__version__}')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan', help='detect and analyse both installations')
    _paths(p)
    p.add_argument('--search-root', action='append', default=[], help='extra folder to search for installs')
    p.add_argument('--deep', action='store_true', help='also search Desktop/Downloads/Games')
    p.add_argument('--fast', action='store_true', help='skip SHA-256 fingerprints')

    p = sub.add_parser('list', help='list species and their conversion status')
    _paths(p); _out(p)
    p.add_argument('--game', choices=['SR1', 'SR2', 'both'], default='both')
    p.add_argument('--kind', choices=['all', 'base', 'largo'], default='all')

    p = sub.add_parser('convert', help='extract, convert and rebuild species for the other game')
    _paths(p); _out(p); _select(p)
    p.add_argument('--dry-run', action='store_true', help='show what would run; write nothing')
    p.add_argument('--force', action='store_true', help='ignore caches')
    p.add_argument('--jobs', type=int, default=3, help='parallel workers for the inventory pass')
    p.add_argument('--stop-on-error', action='store_true')
    p.add_argument('--allow-unvalidated', action='store_true', help='attempt conversion on a game build that is not in the validated table')

    p = sub.add_parser('verify', help='verify built packages (offline checks) and update their status')
    _paths(p); _out(p); _select(p)
    p.add_argument('--no-glb', action='store_true', help='skip the GLB export/re-import check')

    p = sub.add_parser('preview', help='render 4-view software previews of built packages (inspection aid)')
    _paths(p); _out(p); _select(p)

    p = sub.add_parser('export', help='write SlimeBridge bridge-native-mesh caches (what the SR1/SR2 plugins load) and validate them')
    _paths(p); _out(p); _select(p)
    p.add_argument('--format', choices=['bridge-cache'], default='bridge-cache')
    p.add_argument('--largo-id-style', choices=['slime-compat', 'native'], default='slime-compat',
                   help='slime-compat: largo ids become slime.largo.a.b (accepted by the current plugin); native: largo.a.b')
    p.add_argument('--no-largo-icon-fallback', action='store_true', help='do not reuse the first parent icon for largos (they have none in the game data)')
    p.add_argument('--bridge-repo', help='SlimeRancher-Bridge checkout whose own cache scanner is also run (read-only)')
    p.add_argument('--force', action='store_true')

    p = sub.add_parser('unity-verify', help='load built packages in the matching Unity Editor (batch mode) and verify skinning/animation')
    _paths(p); _out(p); _select(p)

    p = sub.add_parser('status', help='show the status ledger')
    _paths(p); _out(p)

    p = sub.add_parser('engines', help='show extraction engines and Unity editors available')
    _paths(p)

    p = sub.add_parser('serve', help='JSON-lines protocol on stdin/stdout for the launcher')
    p.add_argument('--output', '-o', default=DEFAULT_OUTPUT)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    log = (lambda m: print(m, file=sys.stderr)) if not getattr(args, 'json', False) else (lambda m: print(m, file=sys.stderr))
    if args.cmd == 'scan':
        res = api.scan(sr1_path=args.sr1_path, sr2_path=args.sr2_path, search_roots=args.search_root, deep=args.deep, full_hash=not args.fast)
        return _emit(args, res, _print_scan)
    if args.cmd == 'list':
        return _emit(args, api.list_species(args.output, args.sr1_path, args.sr2_path, args.game, args.kind, log=log), _print_list)
    if args.cmd == 'convert':
        if not (args.slime or args.all_species) and not args.dry_run:
            print('nothing selected: pass --slime NAME (repeatable) or --all', file=sys.stderr)
            return 2
        res = api.convert(args.output, args.direction.replace('_', '-'), _slimes(args), args.kind, args.all_species, args.sr1_path, args.sr2_path,
                          dry_run=args.dry_run, force=args.force, limit=args.limit, log=log, workers=args.jobs, stop_on_error=args.stop_on_error,
                          allow_unvalidated=args.allow_unvalidated, only_missing=args.only_missing)
        return _emit(args, res, _print_convert)
    if args.cmd == 'verify':
        res = api.verify(args.output, args.direction, _slimes(args), args.kind, args.all_species, args.sr1_path, args.sr2_path, log=log, glb=not args.no_glb, only_missing=args.only_missing)
        return _emit(args, res, _print_verify)
    if args.cmd == 'preview':
        res = api.preview(args.output, args.direction, _slimes(args), args.kind, args.all_species, args.sr1_path, args.sr2_path, log=log, only_missing=args.only_missing)
        return _emit(args, res, lambda r: [print(f"  {x['source']}:{x['canonical_id']:<34} {x.get('file') or x.get('error')}") for x in r['results']])
    if args.cmd == 'export':
        res = api.export_bridge(args.output, args.direction, _slimes(args), args.kind, args.all_species, args.sr1_path, args.sr2_path, log=log,
                                only_missing=args.only_missing, largo_style=args.largo_id_style, repo=args.bridge_repo, force=args.force, largo_icon_from_parent=not args.no_largo_icon_fallback)
        return _emit(args, res, lambda r: ([print(f"  {x.get('source', '?')}:{x['canonical_id']:<34} {'OK ' if x['ok'] else 'FAIL'} {x.get('path', '')} {x['problems'][:2] if not x['ok'] else ''}") for x in r['results']], print(r['note'])))
    if args.cmd == 'unity-verify':
        res = api.unity_verify(args.output, args.direction, _slimes(args), args.kind, args.all_species, args.sr1_path, args.sr2_path, log=log,
                               only_missing=args.only_missing, limit=args.limit)
        return _emit(args, res, lambda r: ([print(f"  {x.get('source', '?')}:{x['canonical_id']:<34} {x['status']:<16} Unity {x.get('unity')} pose={x.get('pose_comparison')}") for x in r['results']], print(r['note'])))
    if args.cmd == 'status':
        return _emit(args, api.status(args.output), _print_status)
    if args.cmd == 'engines':
        return _emit(args, api.engines(args.sr1_path, args.sr2_path), _print_engines)
    if args.cmd == 'serve':
        from .protocol import serve
        return serve(args.output)
    return 2


if __name__ == '__main__':
    sys.exit(main())
