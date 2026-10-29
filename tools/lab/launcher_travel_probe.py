"""Protected run of the portal crossing through the REAL launcher path (LauncherAPI.launch, BridgeHost child process,
per-game tokens, the background travel thread), on real installs.

    py -3.12 -m tools.lab.launcher_travel_probe --sr1 <SR1 folder> --sr2 <SR2 folder> --current-user-authorized

Only SR1 is started with Play (as a player would). Developer mode walks to the portal; everything after that is the
product: the crossing request, SR2 started by the coordinator (new save for this ranch), arrival, return.
Saves, registry and display settings are backed up and restored.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from apps.launcher import plugins
from apps.launcher.desktop import LauncherAPI
from tools.lab import oracle, sanctuary_oracle, save_guard, screen_guard
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.window_shot import capture

REPO_ROOT = oracle.REPO_ROOT
BUILD = {'SR1': REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472', 'SR2': REPO_ROOT / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'}


def wait_for(api, label, timeout, result):
    deadline = time.monotonic() + timeout
    seen = set()
    while time.monotonic() < deadline:
        crossings = [api._travel.public(t) for t in api._travel.transitions]
        last = crossings[-1] if crossings else None
        if last and (last['id'], last['state']) not in seen:
            seen.add((last['id'], last['state']))
            print(f'[launcher-travel] {label}: {last["state"]} {last.get("code") or ""}', flush=True)
        if last and last['state'] in ('COMPLETE', 'ROLLED_BACK', 'FAILED', 'CANCELLED') and last['id'] not in result.get('done_ids', []):
            result.setdefault('done_ids', []).append(last['id'])
            return last
        time.sleep(1.0)
    return None


def run(sr1_root, sr2_root, timeout_s):
    roots = {'SR1': str(Path(sr1_root).resolve()), 'SR2': str(Path(sr2_root).resolve())}
    assert_no_other_game_processes()
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('launcher-travel-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    result = {'passed': False, 'roots': roots}
    snap = save_guard.snapshot(['SR1', 'SR2'], 'launcher-travel-pre')
    screens = screen_guard.snapshot(('SR1', 'SR2'))
    config = Path(tempfile.mkdtemp(prefix='sb-launcher-')) / 'Launcher' / 'installations.json'
    api = None
    try:
        original = plugins.find_source
        plugins.find_source = lambda realm, repo_root=None: BUILD[realm]
        try:
            result['plugins'] = {r: plugins.install(r, roots[r]) for r in ('SR1', 'SR2')}
        finally:
            plugins.find_source = original
        api = LauncherAPI(config_path=config)
        api._primary = lambda realm: {'game': realm, 'root': roots[realm], 'store': 'manual'}   # these two installs
        api._travel._install_for = api._primary
        ops = ['wait:25', 'unlock', 'wait:12', 'give:PINK_PLORT:2', 'tp:gate', 'wait:2', 'tp:portal', 'wait:4', 'tp:portal_in', 'wait:120']
        os.environ.update({'SLIMEBRIDGE_TRAVEL_MODE': 'RESUME', 'SLIMEBRIDGE_TRAVEL_NEW': 'SlimeBridge launcher test',
                           'SLIMEBRIDGE_DEV': '1', 'SLIMEBRIDGE_DEV_OPS': json.dumps(ops)})
        if sanctuary_oracle.SR1_LOG.is_file():
            sanctuary_oracle.SR1_LOG.replace(sanctuary_oracle.SR1_LOG.with_name('Player.before-probe.log'))
        launched = api.launch('SR1', confirmed=True)
        for key in ('SLIMEBRIDGE_TRAVEL_MODE', 'SLIMEBRIDGE_TRAVEL_NEW', 'SLIMEBRIDGE_DEV', 'SLIMEBRIDGE_DEV_OPS'):
            os.environ.pop(key, None)                    # SR2 is started by the coordinator with the product environment only
        result['launch'] = launched
        print('[launcher-travel] Play SR1:', launched.get('ok'), launched.get('warnings'), launched.get('errors'), flush=True)
        out = wait_for(api, 'out', timeout_s, result)
        result['outbound'] = out
        if out and out['state'] == 'COMPLETE':
            time.sleep(6)
            sr2 = api._launcher._children.get('SR2')
            result['sr2_screen'] = capture(sr2.pid, directory / 'sr2-arrival.png') if sr2 else 'no SR2'
            status = api._host.state()
            beat = (status.get('heartbeats') or {}).get('SR2') or {}
            token = api._host.realm_env('SR2').get('SLIMEBRIDGE_TOKEN')
            body = {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': 'SR2', 'portal_id': 'portal.sr2.return_portal',
                    'save_id': (beat.get('status') or {}).get('save_id') or 'unknown', 'request_id': uuid.uuid4().hex}
            with urlopen(Request(status['url'] + '/v1/portal/cross', json.dumps(body).encode(), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}), timeout=5):
                pass
            back = wait_for(api, 'back', 180, result)
            result['back'] = back
            time.sleep(5)
            sr1 = api._launcher._children.get('SR1')
            result['sr1_screen'] = capture(sr1.pid, directory / 'sr1-return.png') if sr1 else 'no SR1'
        result['passed'] = bool(out and out['state'] == 'COMPLETE' and result.get('back') and result['back']['state'] == 'COMPLETE')
    finally:
        if api is not None:
            for realm, child in list(api._launcher._children.items()):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=20)
                    except Exception:  # noqa: BLE001
                        child.kill()
            api.shutdown()
        time.sleep(2)
        oracle._finalize_current_user(result, snap, [], directory)
        result['display_settings_restored'] = screen_guard.restore(screens)
        (directory / 'launcher-travel.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
        print(f'evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr1', required=True)
    parser.add_argument('--sr2', required=True)
    parser.add_argument('--timeout', type=float, default=720)
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    if not args.current_user_authorized:
        raise SystemExit('--current-user-authorized is required')
    result = run(args.sr1, args.sr2, args.timeout)
    for key in ('outbound', 'back'):
        item = result.get(key) or {}
        print(key, item.get('state'), item.get('code'))
        for h in item.get('history', []):
            print('   ', h['text'])
    print(json.dumps({k: result.get(k) for k in ('passed', 'saves_match_backup_after_restore', 'display_settings_restored')}))


if __name__ == '__main__':
    main()
