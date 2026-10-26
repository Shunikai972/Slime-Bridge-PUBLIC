"""Protected LAB run that lets the SR2 plugin write a read-only probe of the live world around the player.

    py -3.12 -m tools.lab.sr2_probe_oracle --save-group 20250619134130_1 --current-user-authorized

Same doctrine as tools.lab.sanctuary_oracle: owner-authorized current-user mode (verified save + registry backup, the owned child
is stopped, saves/registry/plugin folder restored and re-verified). Evidence: Lab/RuntimeArtifacts/<run>/sr2-world-probe.json + sr2-view.png.
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

from tools.lab import oracle, save_guard
from tools.lab.paths import refuse_reparse, validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.sanctuary_oracle import _sha

REPO_ROOT = oracle.REPO_ROOT
PLUGIN_SOURCE = REPO_ROOT / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'
PLUGIN_DEST = REPO_ROOT / 'Lab' / 'Games' / 'SR2' / 'Mods'
PLUGIN_FILES = ('SlimeBridge.SR2.dll', 'SlimeBridge.SR2.pdb')


def snapshot_plugin():
    return {name: ((PLUGIN_DEST / name).read_bytes() if (PLUGIN_DEST / name).is_file() else None) for name in PLUGIN_FILES}


def restore_plugin(state):
    for name, data in state.items():
        path = PLUGIN_DEST / name
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)
    return {name: (_sha(PLUGIN_DEST / name) if (PLUGIN_DEST / name).is_file() else None) for name in PLUGIN_FILES}


def run(save_group, timeout_s, current_user_authorized, *, portal=False, gate_cost=1000):
    if not current_user_authorized:
        raise ValueError('--current-user-authorized is required (owner-authorized protected run)')
    executable = validate_lab_destination(oracle.GAMES['SR2'], oracle.LAB)
    refuse_reparse(executable)
    assert_no_other_game_processes()
    run_id = 'sr2probe-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    root = REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    refuse_reparse(root)
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=False)
    result = {'run_id': run_id, 'started_utc': datetime.now(timezone.utc).isoformat(), 'realm': 'SR2', 'passed': False,
              'profile_mode': 'current-user-authorized', 'cleanup_completed': False}
    result['sources_before'] = oracle.source_fingerprints(['SR2'])
    if any(v['actual'] != v['expected'] for v in result['sources_before'].values()):
        raise RuntimeError('original SR2 fingerprints differ from the lab manifest; refusing to launch')
    snap = save_guard.snapshot(['SR2'], 'sr2probe-pre')
    result['save_snapshot'] = str(snap)
    previous = snapshot_plugin()
    started = {}
    try:
        assert_no_other_game_processes()
        from tools.lab.deploy import deploy_plugin
        files = [PLUGIN_SOURCE / n for n in PLUGIN_FILES if (PLUGIN_SOURCE / n).is_file()]
        deploy_plugin(files, PLUGIN_DEST)
        result['plugin_deployed'] = {f.name: _sha(PLUGIN_DEST / f.name) for f in files}
        env = dict(os.environ, SLIMEBRIDGE_LAB_EXE=str(executable), SLIMEBRIDGE_AUTOLOAD='continue')
        if portal:
            # The return portal needs the travel link (a BridgeHost address); nothing is crossed in this run.
            from bridge.host.server import BridgeHost
            host = BridgeHost().__enter__()
            started['host'] = host
            env.update(SLIMEBRIDGE_SR2_PORTAL_LAB_OUT=str(directory), SLIMEBRIDGE_SR2_PORTAL_LAB_COST=str(gate_cost),
                       SLIMEBRIDGE_URL=host.url, SLIMEBRIDGE_TOKEN=host.realm_token('SR2'))
        else:
            env['SLIMEBRIDGE_SR2_PROBE_OUT'] = str(directory)
        if save_group:
            env['SLIMEBRIDGE_LAB_SAVE_ID'] = save_group
        if not portal:
            for key in ('SLIMEBRIDGE_URL', 'SLIMEBRIDGE_TOKEN'):
                env.pop(key, None)
        proc = subprocess.Popen([str(executable), *oracle.WINDOW_ARGS], cwd=str(executable.parent), env=env)
        started['SR2'] = proc
        print(f'[sr2probe] launched SR2 pid={proc.pid}', flush=True)
        probe = directory / ('sr2-portal-receipt.json' if portal else 'sr2-world-probe.json')
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and proc.poll() is None and not probe.is_file():
            time.sleep(1.0)
        if probe.is_file():
            time.sleep(2.0)
            result['probe_sha256'] = _sha(probe)
            result['passed'] = True
        else:
            result['error'] = 'no probe before timeout' if proc.poll() is None else f'game exited early ({proc.returncode})'
    except BaseException as ex:
        result['error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        try:
            host = started.pop('host', None)
            result['stop_errors'] = oracle._stop_owned(started) if started else {}
            if host is not None:
                host.__exit__(None, None, None)
            result['plugin_restored'] = restore_plugin(previous)
            oracle._finalize_current_user(result, snap, ['SR2'], directory)
            result['cleanup_completed'] = not any(result.get(k) for k in ('stop_errors', 'safety_error', 'registry_error',
                                                  'restoration_skipped', 'registry_restore_skipped', 'save_restore_errors'))
        finally:
            (directory / 'sr2-probe-oracle.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'[sr2probe] evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--save-group', default=None, help='native save group id; default: the Continue save of the profile')
    parser.add_argument('--timeout', type=float, default=420)
    parser.add_argument('--current-user-authorized', action='store_true')
    parser.add_argument('--portal', action='store_true', help='check the return portal (build, shots, receipt) instead of the world probe')
    parser.add_argument('--gate-cost', type=int, default=1000)
    args = parser.parse_args(argv)
    result = run(args.save_group, args.timeout, args.current_user_authorized, portal=args.portal, gate_cost=args.gate_cost)
    print(json.dumps({k: result.get(k) for k in ('run_id', 'passed', 'error', 'cleanup_completed')}, indent=2))
    return 0 if result['passed'] and result['cleanup_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
