"""Protected run of a full SR1 <-> SR2 crossing on the player's REAL installs (SR2 1.x), with a real BridgeHost and the
real launcher coordinator in its default (AUTO link) mode.

    py -3.12 -m tools.lab.real_travel_oracle --sr1 <SR1 folder> --sr2 <SR2 folder> --current-user-authorized

Same doctrine as tools.lab.travel_oracle (which is tied to the LAB copies, SR2 0.6): every SR1/SR2 save file and the games'
registry keys are backed up and verified first and restored afterwards (so the throw-away SR1 game and the SR2 game created for it
disappear), the games this run started are stopped, the sanctuary progress lives in the run folder. The freshly built plugins are
installed into both games (and stay: they are what the launcher installs anyway).

Flow: SR1 starts on a throw-away new game, the sanctuary is unlocked, the player walks into the portal -> the coordinator finds no
SR2 save for this ranch, starts SR2 with SLIMEBRIDGE_TRAVEL_NEW -> SR2 creates a new game by itself -> the player arrives at the
SR2 portal, SR1 is throttled and minimized. Then SR2 asks to go back and SR1 comes out of its portal.
Evidence: Lab/RuntimeArtifacts/real-travel-<run>/ (timeline, heartbeats, window states, screenshots, log excerpts).
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

from apps.launcher import plugins, savelist, travel, windows
from bridge.host.server import BridgeHost
from tools.lab import oracle, sanctuary_oracle, save_guard
from tools.lab.process_guard import assert_no_other_game_processes, process_image_path
from tools.lab.travel_oracle import OracleHost, beats, drive, grab, log_lines, post

REPO_ROOT = oracle.REPO_ROOT
EXE = {'SR1': 'SlimeRancher.exe', 'SR2': 'SlimeRancher2.exe'}
BUILD = {'SR1': REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472', 'SR2': REPO_ROOT / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'}


class RealLauncher:
    """Starts the two real installs, each with its own realm credential (never the admin token)."""

    def __init__(self, host, roots, started):
        self.host, self.roots, self.started = host, roots, started

    def running(self, realm):
        proc = self.started.get(realm)
        return proc is not None and proc.poll() is None

    def launch(self, installation, extra_env=None):
        realm = installation['game']
        exe = Path(self.roots[realm]) / EXE[realm]
        assert_no_other_game_processes(p.pid for p in self.started.values())
        env = dict(os.environ)
        env.update(extra_env or {})
        env['SLIMEBRIDGE_TOKEN'] = self.host.realm_token(realm)
        env['SLIMEBRIDGE_URL'] = self.host.url
        proc = subprocess.Popen([str(exe), *oracle.WINDOW_ARGS], cwd=str(exe.parent), env=env)
        self.started[realm] = proc
        print(f'[real-travel] launched {realm} pid={proc.pid}', flush=True)
        return {'realm': realm, 'pid': proc.pid}


def window_states(started):
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out = {}
    for realm, proc in started.items():
        found = []

        def cb(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True
        user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(cb), 0)
        fg = user32.GetForegroundWindow()
        out[realm] = {'windows': len(found), 'minimized': any(user32.IsIconic(h) for h in found), 'foreground': fg in found}
    return out


def stop_started(started, roots):
    """Stop exactly the games this run started (checked by PID and executable path), never another process."""
    errors = {}
    for realm, proc in started.items():
        try:
            if proc.poll() is None:
                image = process_image_path(proc.pid)
                expected = (Path(roots[realm]) / EXE[realm]).resolve()
                if image is None or Path(image).resolve() != expected:
                    errors[realm] = f'pid {proc.pid} is not {expected}'
                    continue
                proc.terminate()
                proc.wait(timeout=20)
        except Exception as ex:                                         # noqa: BLE001
            errors[realm] = f'{type(ex).__name__}: {ex}'
    return errors


def install_plugins(roots):
    """The fresh build, through the launcher's own installer (same files and checks as a player's install)."""
    original = plugins.find_source
    plugins.find_source = lambda realm, repo_root=None: BUILD[realm]
    try:
        return {realm: plugins.install(realm, roots[realm]) for realm in ('SR1', 'SR2')}
    finally:
        plugins.find_source = original


def run(sr1_root, sr2_root, timeout_s, current_user_authorized):
    if not current_user_authorized:
        raise ValueError('--current-user-authorized is required (owner-authorized protected run)')
    roots = {'SR1': str(Path(sr1_root).resolve()), 'SR2': str(Path(sr2_root).resolve())}
    for realm, root in roots.items():
        if not (Path(root) / EXE[realm]).is_file():
            raise ValueError(f'{root} has no {EXE[realm]}')
    assert_no_other_game_processes()
    run_id = 'real-travel-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / run_id
    (directory / 'state').mkdir(parents=True)
    result = {'run_id': run_id, 'started_utc': datetime.now(timezone.utc).isoformat(), 'roots': roots, 'passed': False, 'timeline': []}
    snap = save_guard.snapshot(['SR1', 'SR2'], 'real-travel-pre')
    from tools.lab import screen_guard
    screens = screen_guard.snapshot(('SR1', 'SR2'))
    result['save_snapshot'] = str(snap)
    print(f'[real-travel] saves backed up to {snap}', flush=True)
    started, host = {}, None
    try:
        result['plugins'] = install_plugins(roots)
        host = BridgeHost().__enter__()
        launcher = RealLauncher(host, roots, started)
        links = travel.TravelLinks(directory / 'state' / 'travel-links.json')
        verified = lambda pid: any(p.pid == pid and p.poll() is None for p in started.values()) and process_image_path(pid) is not None
        coordinator = travel.TravelCoordinator(
            OracleHost(host), launcher, lambda realm: {'game': realm, 'root': roots[realm]}, lambda: True,
            windows.Win32Windows(verify=verified), links, settings=lambda: {}, list_saves=savelist.list_groups)
        # product paths only: SR1 creates a new game by itself (RESUME + TRAVEL_NEW) and developer mode walks to the portal
        ops = ['wait:25', 'unlock', 'wait:12', 'give:PINK_PLORT:2', 'tp:gate', 'wait:2', 'tp:statue.boom', 'wait:10', 'vac', 'tp:portal', 'wait:4', 'tp:portal_in', 'wait:120']  # the island needs its reveal before anyone stands on it
        sr1_env = {'SLIMEBRIDGE_TRAVEL_MODE': 'RESUME', 'SLIMEBRIDGE_TRAVEL_NEW': 'SlimeBridge travel test',
                   'SLIMEBRIDGE_DEV': '1', 'SLIMEBRIDGE_DEV_OPS': json.dumps(ops)}
        if sanctuary_oracle.SR1_LOG.is_file():                  # never read the previous run's log as this one
            sanctuary_oracle.SR1_LOG.replace(sanctuary_oracle.SR1_LOG.with_name('Player.before-probe.log'))
        launcher.launch({'game': 'SR1'}, extra_env=sr1_env)
        result['timeline'].append({'label': 'start', 'state': 'SR1_LAUNCHED', 't': round(time.time(), 1)})
        # look at one statue (badge: native slime + name) before the portal
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if 'dev op tp:statue.boom' in (sanctuary_oracle.SR1_LOG.read_text(encoding='utf-8', errors='replace') if sanctuary_oracle.SR1_LOG.is_file() else ''):
                time.sleep(5.0)
                from tools.lab.window_shot import capture
                result['statue_screen'] = capture(started['SR1'].pid, directory / 'screens' / 'statue-boom.png')
                break
            time.sleep(1.0)
        import threading
        from tools.lab.window_shot import capture
        stop_watch = threading.Event()

        def watch_sr2():
            n = 0
            while not stop_watch.wait(15.0):
                if 'SR2' in started and started['SR2'].poll() is None:
                    n += 1
                    capture(started['SR2'].pid, directory / 'screens' / f'sr2-watch-{n:02d}.png')
        threading.Thread(target=watch_sr2, daemon=True).start()
        outbound = drive(host, coordinator, directory, 'out', timeout_s, result['timeline'])
        stop_watch.set()
        result['outbound'] = outbound
        if not outbound or outbound['state'] != 'COMPLETE':
            result['error'] = 'outbound crossing did not complete'
        else:
            time.sleep(6.0)
            grab(directory, 'out-after-6s')
            from tools.lab.window_shot import capture
            result['sr2_arrival_screen'] = capture(started['SR2'].pid, directory / 'screens' / 'sr2-arrival.png')
            result['beats_after_outbound'] = beats(host)
            result['windows_after_outbound'] = window_states(started)
            sr2_save = (host.heartbeats().get('SR2', {}).get('status') or {}).get('save_id')
            post(host, '/v1/portal/cross', {'protocol_version': 1, 'message_type': 'portal_cross', 'realm': 'SR2', 'portal_id': 'portal.sr2.return_portal',
                                            'save_id': sr2_save or 'unknown', 'request_id': uuid.uuid4().hex}, host.realm_token('SR2'))
            back = drive(host, coordinator, directory, 'back', 120, result['timeline'])
            result['back'] = back
            time.sleep(5.0)
            grab(directory, 'back-after-5s')
            result['sr1_return_screen'] = capture(started['SR1'].pid, directory / 'screens' / 'sr1-return.png')
            result['beats_after_back'] = beats(host)
            result['windows_after_back'] = window_states(started)
            if not back or back['state'] != 'COMPLETE':
                result['error'] = 'return crossing did not complete'
        result['events'] = coordinator._events
        result['links'] = json.loads((directory / 'state' / 'travel-links.json').read_text(encoding='utf-8')) if (directory / 'state' / 'travel-links.json').is_file() else {}
        result['passed'] = 'error' not in result
    except BaseException as ex:
        result['error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        try:
            result['stop_errors'] = stop_started(started, roots)
            if host is not None:
                host.__exit__(None, None, None)
            result['sr1_log'] = log_lines(sanctuary_oracle.SR1_LOG, ('travel', 'sanctuary', 'SlimeBridge'))
            result['sr2_log'] = log_lines(Path(roots['SR2']) / 'MelonLoader' / 'Latest.log', ('travel', 'SlimeBridge', 'autoload', 'boot'))
            oracle._finalize_current_user(result, snap, [], directory)
            result['display_settings_restored'] = screen_guard.restore(screens)
            # the verdict is the two crossings + saves restored; registry leftovers of the new SR2 game are reported, not fatal
            result['passed'] = bool(not result.get('error') and (result.get('outbound') or {}).get('state') == 'COMPLETE'
                                    and (result.get('back') or {}).get('state') == 'COMPLETE' and result.get('saves_match_backup_after_restore')
                                    and not result.get('stop_errors'))
        finally:
            (directory / 'real-travel.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'[real-travel] evidence: {directory}', flush=True)
    return result


def travel_layout():
    from apps.launcher import sanctuary
    sanctuary.refresh_layout()
    latest = sanctuary.latest_layout()
    if not latest:
        raise RuntimeError('no sanctuary layout')
    return Path(latest['path']) / 'sanctuary-layout.json'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr1', required=True)
    parser.add_argument('--sr2', required=True)
    parser.add_argument('--timeout', type=float, default=480)
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    result = run(args.sr1, args.sr2, args.timeout, args.current_user_authorized)
    print(json.dumps({k: result.get(k) for k in ('passed', 'error', 'links', 'windows_after_outbound', 'windows_after_back', 'saves_match_backup_after_restore')}, indent=1, default=str))
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
