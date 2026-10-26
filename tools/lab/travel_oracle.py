"""Protected LAB run of a full SR1 <-> SR2 crossing with both real games, a real BridgeHost and the real launcher coordinator.

    py -3.12 -m tools.lab.travel_oracle --layout Lab/GeneratedContent/sanctuary/layout-006 --current-user-authorized

Flow: SR1 (throw-away new game, sanctuary unlocked) walks into the portal -> BridgeHost -> TravelCoordinator starts SR2 on the
profile's Continue save (the product path: SLIMEBRIDGE_TRAVEL_MODE=DESTINATION, no LAB flags) while SR1 plays the portal
animation -> SR2 comes to the front and emerges -> SR1 is throttled and minimized. Then SR2 asks to go back and SR1 emerges from
its portal. Same doctrine as the other oracles: verified save + registry backup, owned children stopped, saves/registry/plugin
folders restored and re-verified. Evidence (timeline, heartbeats, screenshots, log excerpts): Lab/RuntimeArtifacts/<run>/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from apps.launcher import savelist, travel, windows
from bridge.host.server import BridgeHost
from tools.lab import oracle, save_guard, sanctuary_oracle, sr2_probe_oracle
from tools.lab.paths import refuse_reparse, validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes, process_image_path

REPO_ROOT = oracle.REPO_ROOT
SR1_LOG = sanctuary_oracle.SR1_LOG
SR2_LOG = REPO_ROOT / 'Lab' / 'Games' / 'SR2' / 'MelonLoader' / 'Latest.log'
PORTAL_SR1, PORTAL_SR2 = 'portal.sr1.sanctuary_gate', 'portal.sr2.return_portal'


class _Done(Exception):
    """A scenario that finished its own work (skips the rest of the default flow)."""


class OracleHost:
    """The slice of HostProcess the coordinator uses, over an in-process BridgeHost."""

    def __init__(self, host):
        self.host = host

    def state(self):
        return {'state': 'RUNNING', 'url': self.host.url, 'pid': os.getpid(), 'heartbeats': self.host.heartbeats()}

    def admin_token(self):
        return self.host.token

    def env(self):
        return {'SLIMEBRIDGE_URL': self.host.url, 'SLIMEBRIDGE_TOKEN': self.host.token}


class LabLauncher:
    """Starts only the LAB copies; hands each game its own realm credential (never the admin token)."""

    def __init__(self, host, started):
        self.host, self.started = host, started

    def running(self, realm):
        proc = self.started.get(realm)
        return proc is not None and proc.poll() is None

    def launch(self, installation, extra_env=None):
        realm = installation['game']
        exe = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
        refuse_reparse(exe)
        assert_no_other_game_processes(p.pid for p in self.started.values())
        env = dict(os.environ)
        env.update(extra_env or {})
        env['SLIMEBRIDGE_TOKEN'] = self.host.realm_token(realm)
        env['SLIMEBRIDGE_URL'] = self.host.url
        proc = subprocess.Popen([str(exe), *oracle.WINDOW_ARGS], cwd=str(exe.parent), env=env)
        self.started[realm] = proc
        print(f'[travel] launched {realm} pid={proc.pid}', flush=True)
        return {'realm': realm, 'pid': proc.pid}


def post(host, path, body, token):
    request = Request(host.url + path, json.dumps(body).encode(), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def grab(directory, name):
    try:
        from PIL import ImageGrab
        path = directory / 'screens' / f'{name}.png'
        path.parent.mkdir(exist_ok=True)
        ImageGrab.grab(all_screens=True).save(path)
        return path.name
    except Exception as ex:                                                # noqa: BLE001 - evidence only
        return f'unavailable: {type(ex).__name__}'


def log_lines(path, needles, limit=80):
    if not path.is_file():
        return {'present': False}
    lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    return {'present': True, 'lines': [line for line in lines if any(n in line for n in needles)][-limit:],
            'exceptions': sum(1 for line in lines if 'NullReferenceException' in line or 'Exception:' in line)}


def beats(host):
    out = {}
    for realm, entry in host.heartbeats().items():
        status = entry.get('status') or {}
        out[realm] = {'pid': entry.get('pid'), 'save': status.get('save_id'), 'alive': status.get('player_alive')}
    return out


def drive(host, coordinator, directory, label, timeout_s, timeline, choose=None):
    """Poll the coordinator until the newest crossing is terminal; screenshot each new state."""
    seen = set()
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        crossings = coordinator.poll()
        last = crossings[-1] if crossings else None
        if last is not None:
            state = last['state']
            if state == 'NEEDS_LINK' and (last['id'], 'answered') not in seen:
                seen.add((last['id'], 'answered'))
                pick = choose if choose in {c['group'] for c in last['candidates']} else next((c['group'] for c in last['candidates'] if not c['linked'] and not c.get('new')), None)
                print(f'[travel] {label}: the player is asked which save to link; picking {pick}', flush=True)
                coordinator.choose_link(last['id'], pick)
            if (last['id'], state) not in seen:
                seen.add((last['id'], state))
                shot = grab(directory, f'{label}-{state.lower()}')
                timeline.append({'label': label, 'state': state, 'code': last.get('code'), 't': round(time.time(), 1), 'screen': shot,
                                 'beats': beats(host), 'history': last['history'][-3:]})
                print(f'[travel] {label}: {state} {last.get("code") or ""}', flush=True)
            if state in ('COMPLETE', 'ROLLED_BACK', 'FAILED'):
                return last
        time.sleep(1.0)
    return last


def run(layout_dir, timeout_s, current_user_authorized, scenario='both'):
    if not current_user_authorized:
        raise ValueError('--current-user-authorized is required (owner-authorized protected run)')
    layout_dir = Path(layout_dir).resolve()
    layout_path = layout_dir / 'sanctuary-layout.json'
    if sanctuary_oracle.LAYOUT_ROOT not in layout_path.parents or not layout_path.is_file():
        raise ValueError('layout must be Lab/GeneratedContent/sanctuary/<run>/sanctuary-layout.json')
    refuse_reparse(layout_path)
    for realm in ('SR1', 'SR2'):
        refuse_reparse(validate_lab_destination(oracle.GAMES[realm], oracle.LAB))
    assert_no_other_game_processes()
    run_id = 'travel-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    refuse_reparse(root)
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'state').mkdir()
    result = {'run_id': run_id, 'started_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'profile_mode': 'current-user-authorized', 'cleanup_completed': False, 'timeline': []}
    result['sources_before'] = oracle.source_fingerprints(['SR1', 'SR2'])
    if any(v['actual'] != v['expected'] for v in result['sources_before'].values()):
        raise RuntimeError('original game fingerprints differ from the lab manifest; refusing to launch')
    snap = save_guard.snapshot(['SR1', 'SR2'], 'travel-pre')
    result['save_snapshot'] = str(snap)
    print(f'[travel] saves backed up to {snap}', flush=True)
    previous = {'SR1': sanctuary_oracle.snapshot_plugin(), 'SR2': sr2_probe_oracle.snapshot_plugin()}
    started = {}
    host = None
    try:
        assert_no_other_game_processes()
        result['plugins_deployed'] = {'SR1': sanctuary_oracle.deploy_plugin()}
        files = [sr2_probe_oracle.PLUGIN_SOURCE / n for n in sr2_probe_oracle.PLUGIN_FILES if (sr2_probe_oracle.PLUGIN_SOURCE / n).is_file()]
        from tools.lab.deploy import deploy_plugin
        deploy_plugin(files, sr2_probe_oracle.PLUGIN_DEST)
        result['plugins_deployed']['SR2'] = {f.name: sanctuary_oracle._sha(sr2_probe_oracle.PLUGIN_DEST / f.name) for f in files}
        host = BridgeHost().__enter__()
        launcher = LabLauncher(host, started)
        links = travel.TravelLinks(directory / 'state' / 'travel-links.json')
        verified = lambda pid: any(p.pid == pid and p.poll() is None for p in started.values()) and process_image_path(pid) is not None
        coordinator = travel.TravelCoordinator(
            OracleHost(host), launcher, lambda realm: {'game': realm, 'root': str(oracle.LAB / realm)}, lambda: True,
            windows.Win32Windows(verify=verified), links, settings=lambda: {}, list_saves=savelist.list_groups)
        if scenario == 'create':
            # SR2 starts alone on its Continue save; its player asks to go to SR1 and chooses "a new Slime Rancher save".
            launcher.launch({'game': 'SR2'}, extra_env={'SLIMEBRIDGE_LAB_EXE': str(oracle.GAMES['SR2']), 'SLIMEBRIDGE_AUTOLOAD': 'continue'})
            result['timeline'].append({'label': 'start', 'state': 'SR2_LAUNCHED', 't': round(time.time(), 1)})
            sr2_save = None
            for _ in range(240):
                sr2_save = (host.heartbeats().get('SR2', {}).get('status') or {}).get('save_id')
                if sr2_save:
                    break
                time.sleep(1.0)
            if not sr2_save:
                raise RuntimeError('SR2 never reported a loaded save')
            time.sleep(8.0)
            post(host, '/v1/portal/cross', {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': 'SR2', 'portal_id': PORTAL_SR2,
                                            'save_id': sr2_save, 'request_id': uuid.uuid4().hex}, host.realm_token('SR2'))
            created = drive(host, coordinator, directory, 'create', timeout_s, result['timeline'], choose=travel.NEW_SAVE)
            result['created'] = created
            time.sleep(10.0)
            grab(directory, 'create-after-10s')
            result['beats_after_create'] = beats(host)
            result['links'] = json.loads((directory / 'state' / 'travel-links.json').read_text(encoding='utf-8')) if (directory / 'state' / 'travel-links.json').is_file() else {}
            if not created or created['state'] != 'COMPLETE':
                result['error'] = 'crossing with a new save did not complete'
            elif sr2_save not in result['links'].get('SR2:' + sr2_save, '') and not result['links'].get('SR2:' + sr2_save):
                result['error'] = 'the new save was not linked'
            result['passed'] = 'error' not in result
            raise _Done
        ops = ['wait:45', 'status', 'give:CARROT_VEGGIE:3', 'give:PINK_PLORT:2', 'give:HEN:1', 'unlock', 'wait:2', 'tp:portal', 'wait:4', 'tp:portal_in', 'wait:900']
        sr1_env = {'SLIMEBRIDGE_LAB_EXE': str(oracle.GAMES['SR1']), 'SLIMEBRIDGE_SANCTUARY_LAYOUT': str(layout_path),
                   'SLIMEBRIDGE_SANCTUARY_OUT': str(directory), 'SLIMEBRIDGE_SANCTUARY_STATE_DIR': str(directory / 'state'),
                   'SLIMEBRIDGE_SANCTUARY_OPS': json.dumps(ops), 'SLIMEBRIDGE_SANCTUARY_NEWGAME': '1'}
        launcher.launch({'game': 'SR1'}, extra_env=sr1_env)
        result['timeline'].append({'label': 'start', 'state': 'SR1_LAUNCHED', 't': round(time.time(), 1)})
        # --- outbound crossing: SR1 portal -> SR2
        outbound = drive(host, coordinator, directory, 'out', timeout_s, result['timeline'])
        result['outbound'] = outbound
        if not outbound or outbound['state'] != 'COMPLETE':
            result['error'] = 'outbound crossing did not complete'
        else:
            time.sleep(8.0)
            grab(directory, 'out-after-8s')
            result['beats_after_outbound'] = beats(host)
            # --- return crossing: SR2 asks to go back (its own portal does not exist yet: same request the portal will send)
            sr2_save = (host.heartbeats().get('SR2', {}).get('status') or {}).get('save_id')
            post(host, '/v1/portal/cross', {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': 'SR2', 'portal_id': PORTAL_SR2,
                                            'save_id': sr2_save or 'unknown', 'request_id': uuid.uuid4().hex}, host.realm_token('SR2'))
            back = drive(host, coordinator, directory, 'back', 120, result['timeline'])
            result['back'] = back
            time.sleep(6.0)
            grab(directory, 'back-after-6s')
            result['beats_after_back'] = beats(host)
            if not back or back['state'] != 'COMPLETE':
                result['error'] = 'return crossing did not complete'
        result['links'] = json.loads((directory / 'state' / 'travel-links.json').read_text(encoding='utf-8')) if (directory / 'state' / 'travel-links.json').is_file() else {}
        result['passed'] = 'error' not in result
    except _Done:
        pass
    except BaseException as ex:
        result['error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        try:
            result['stop_errors'] = oracle._stop_owned(started) if started else {}
            if host is not None:
                host.__exit__(None, None, None)
            result['sr1_log'] = log_lines(SR1_LOG, ('travel', 'sanctuary', 'SlimeBridge'))
            result['sr2_log'] = log_lines(SR2_LOG, ('travel', 'SlimeBridge'))
            result['plugins_restored'] = {'SR1': sanctuary_oracle.restore_plugin(previous['SR1']), 'SR2': sr2_probe_oracle.restore_plugin(previous['SR2'])}
            oracle._finalize_current_user(result, snap, ['SR1', 'SR2'], directory)
            if result.get('stop_errors'):
                result['passed'] = False
            result['cleanup_completed'] = not any(result.get(k) for k in ('stop_errors', 'safety_error', 'registry_error',
                                                  'restoration_skipped', 'registry_restore_skipped', 'save_restore_errors'))
        finally:
            (directory / 'travel-oracle.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'[travel] evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--layout', required=True, type=Path)
    parser.add_argument('--timeout', type=float, default=480)
    parser.add_argument('--scenario', choices=('both', 'create'), default='both', help='both: SR1 -> SR2 -> SR1; create: SR2 asks to go to SR1 and creates a new Slime Rancher save')
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    result = run(args.layout, args.timeout, args.current_user_authorized, args.scenario)
    print(json.dumps({k: result.get(k) for k in ('run_id', 'passed', 'error', 'cleanup_completed')}, indent=2))
    return 0 if result['passed'] and result['cleanup_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
