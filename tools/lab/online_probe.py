"""Protected run of "Multi > Play together > Host (both games)" through the REAL launcher path, on isolated LAB copies.

    py -3.12 -m tools.lab.online_probe --sr1 <SR1 folder> --sr2 <SR2 folder> --current-user-authorized

The launcher starts both games on the newest save of each (Continue on a linked pair), Slime Rancher 2 hosts with
Ranching Together's Auto Host, then Slime Rancher opens its SRMP lobby under the same code. Passes when both games report
hosting with one code. Needs the internet (SRMP signs in to Epic Online Services; Auto Host needs UPnP on the router).
Saves, registry, display settings, the SlimeBridge plugins and the online mods of the LAB copies are restored afterwards.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path

from apps.launcher import online, plugins, savelist
from apps.launcher.desktop import LauncherAPI
from tools.lab import oracle, save_guard, screen_guard
from tools.lab.paths import validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.window_shot import capture

REPO_ROOT = oracle.REPO_ROOT
BUILD = {'SR1': REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472', 'SR2': REPO_ROOT / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'}
ONLINE_FILES = {'SR1': ('SRML/Mods/SRMP.dll', 'SRML/Mods/SRMP.install.json'),
                'SR2': ('Mods/SR2MP.dll', 'Mods/Starlight.dll', 'SlimeBridge/online.json')}


def snapshot_online(roots):
    saved = {}
    for realm, rels in ONLINE_FILES.items():
        for rel in rels:
            path = validate_lab_destination(Path(roots[realm]) / rel, oracle.LAB)
            saved[str(path)] = path.read_bytes() if path.is_file() else None
    return saved


def restore_online(saved):
    assert_no_other_game_processes()
    for name, data in saved.items():
        path = validate_lab_destination(Path(name), oracle.LAB)
        if data is None:
            if path.is_file():
                path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + '.restore')
            temp.write_bytes(data)
            temp.replace(path)
    return True


def run(sr1_root, sr2_root, timeout_s, mode='BOTH'):
    roots = {realm: str(validate_lab_destination(Path(root) / name, oracle.LAB).parent)
             for realm, root, name in (('SR1', sr1_root, 'SlimeRancher.exe'), ('SR2', sr2_root, 'SlimeRancher2.exe'))}
    assert_no_other_game_processes()
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('online-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    result = {'passed': False, 'roots': roots, 'steps': []}
    snap = save_guard.snapshot(['SR1', 'SR2'], 'online-pre')
    screens = screen_guard.snapshot(('SR1', 'SR2'))
    config = Path(tempfile.mkdtemp(prefix='sb-launcher-')) / 'Launcher' / 'installations.json'
    api = None
    previous_plugins = oracle.snapshot_plugins(roots)
    previous_online = snapshot_online(roots)
    try:
        original = plugins.find_source
        plugins.find_source = lambda realm, repo_root=None: BUILD[realm]
        try:
            result['plugins'] = {r: plugins.install(r, roots[r]) for r in ('SR1', 'SR2')}
        finally:
            plugins.find_source = original
        result['online_install'] = {r: online.install(r, roots[r]) for r in ('SR1', 'SR2')}
        api = LauncherAPI(config_path=config)
        api._models_dir = lambda data=None: REPO_ROOT / 'Lab' / 'Largos' / 'models'
        api._primary = lambda realm: {'game': realm, 'root': roots[realm], 'store': 'manual'}
        api._travel._install_for = api._primary
        saves = {realm: (savelist.list_groups(realm) or [{}])[0].get('group') for realm in ('SR1', 'SR2')}
        result['saves'] = saves
        if not all(saves.values()):
            raise RuntimeError(f'each game needs one save to load: {saves}')
        api._travel._links.remember(saves['SR1'], saves['SR2'])            # the pair Continue starts
        api._session.set('SR1' if mode == 'SR1' else 'SR2', saves['SR1' if mode == 'SR1' else 'SR2'])
        hosted = api.mp_host(mode)
        result['host_call'] = {'ok': hosted.get('ok'), 'launched': (hosted.get('launched') or {}).get('ok'), 'request': hosted.get('request')}
        print('[online] host both:', result['host_call'], flush=True)
        deadline, last = time.monotonic() + timeout_s, None
        while time.monotonic() < deadline:
            state = api.mp_session()
            request = state.get('request') or {}
            key = (request.get('step'), state.get('code'), json.dumps(state.get('games'), sort_keys=True))
            if key != last:
                last = key
                result['steps'].append({'t': round(timeout_s - (deadline - time.monotonic())), 'step': request.get('step'), 'code': state.get('code'),
                                        'error': request.get('error'), 'games': state.get('games')})
                print(f"[online] {request.get('step')} code={state.get('code')!r} error={request.get('error')!r} games={state.get('games')}", flush=True)
            if request.get('done'):
                break
            time.sleep(2.0)
        final = api.mp_session()
        result['final'] = final
        games = final.get('games') or {}
        sr1, sr2 = games.get('SR1') or {}, games.get('SR2') or {}
        result['same_code_in_both_games'] = bool(sr2.get('code') and sr2.get('code') == sr1.get('code'))
        time.sleep(4)
        for realm in ('SR1', 'SR2'):
            child = api._launcher._children.get(realm)
            result[realm.lower() + '_screen'] = capture(child.pid, directory / f'{realm.lower()}-online.png') if child else 'not running'
        hosting = (final.get('request') or {}).get('step') == 'hosting'
        result['passed'] = bool(hosting and sr1.get('hosting') and sr1.get('code')) if mode == 'SR1' else             bool(hosting and sr1.get('hosting') and sr2.get('hosting') and result['same_code_in_both_games'])
    finally:
        try:
            oracle.cleanup_steps(result, [
                ('stop_errors', lambda: oracle._stop_owned(dict(api._launcher._children),
                    {realm: Path(roots[realm]) / name for realm, name in
                     (('SR1', 'SlimeRancher.exe'), ('SR2', 'SlimeRancher2.exe'))}) if api is not None else {}),
                ('launcher_shutdown', lambda: api.shutdown() if api is not None else None),
                ('plugins_restored', lambda: oracle.restore_plugins(previous_plugins)),
                ('online_restored', lambda: restore_online(previous_online)),
                ('protected_finalizer', lambda: oracle._finalize_current_user(result, snap, [], directory)),
                ('display_settings_restored', lambda: screen_guard.restore(screens)),
            ])
        finally:
            (directory / 'online.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr1', required=True)
    parser.add_argument('--sr2', required=True)
    parser.add_argument('--timeout', type=float, default=480)
    parser.add_argument('--mode', choices=('BOTH', 'SR1', 'SR2'), default='BOTH')
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    if not args.current_user_authorized:
        raise SystemExit('--current-user-authorized is required')
    result = run(args.sr1, args.sr2, args.timeout, args.mode)
    print(json.dumps({k: result.get(k) for k in ('passed', 'same_code_in_both_games', 'saves_match_backup_after_restore', 'cleanup_completed')}))
    return 0 if result.get('passed') is True and result.get('cleanup_completed') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
