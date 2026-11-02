"""Protected SR1-only LAB run: a new game made by the plugin, then developer-mode ops, then the log.

    py -3.12 -m tools.lab.sr1_ops_probe --sr1 <SR1 folder> --ops '["wait:25","give:PINK_PLORT:2","vac"]' --current-user-authorized

SR1 saves/registry are backed up and restored; the freshly built plugin is installed. Prints the "dev op" log lines.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from apps.launcher import plugins
from tools.lab import oracle, sanctuary_oracle, save_guard
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.paths import validate_lab_destination
from tools.lab.window_shot import capture

REPO_ROOT = oracle.REPO_ROOT
BUILD = REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472'


def _ops_verified(ops, lines):
    """Match ordered native developer results, not a launch or an arbitrary log line.

    Only operations with explicit SanctuaryHost developer log outcomes can prove
    success here. Waits have no result of their own; screenshot/material-only
    operations need a different receipt and must not silently pass this probe.
    """
    records = [line.split('[SlimeBridge] dev op ', 1)[1]
               for line in lines if '[SlimeBridge] dev op ' in line]
    position = verified = 0
    for op in ops:
        if op == 'wait':
            continue
        if op.startswith('wait:'):
            try:
                seconds = float(op[5:])
            except ValueError:
                return False
            if not math.isfinite(seconds) or not 0 <= seconds <= 120:
                return False
            continue
        prefix = op + ' -> '
        while position < len(records) and not records[position].startswith(prefix):
            position += 1
        if position == len(records):
            return False
        outcome = records[position][len(prefix):]
        position += 1
        parts = op.split(':')
        verb = parts[0].lower()
        good = False
        if verb == 'give' and len(parts) == 3:
            try:
                amount = int(parts[2])
            except ValueError:
                return False
            good = (-2**31 <= amount < 2**31 and
                    outcome == f'gave {parts[1].upper()} x{max(1, amount)}')
        elif verb == 'vac' and len(parts) == 1:
            good = (outcome.startswith('vac: ') and len(outcome) > len('vac: ') and
                    outcome != 'vac: unavailable')
        elif op == 'unlock':
            good = outcome == 'gate, statues and portal unlocked'
        elif op == 'reset':
            good = outcome == 'sanctuary progress reset'
        elif op.startswith('tp:'):
            good = outcome == 'teleported'
        elif verb in ('gold', 'fill'):
            good = outcome.startswith("sanctuary save '")
        if not good:
            return False
        verified += 1
    return verified > 0


def run(sr1_root, ops, seconds, shots):
    root = validate_lab_destination(Path(sr1_root) / 'SlimeRancher.exe', oracle.LAB).parent
    if (not isinstance(ops, list) or not ops or len(ops) > 256 or
            any(not isinstance(op, str) or not op or len(op) > 512 or chr(10) in op or chr(13) in op for op in ops)):
        raise ValueError('ops must be a bounded list of nonempty single-line commands')
    assert_no_other_game_processes()
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('sr1-ops-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    result = {'passed': False}
    snap = save_guard.snapshot(['SR1'], 'sr1-ops-pre')
    from tools.lab import screen_guard
    screens = screen_guard.snapshot(('SR1',))
    proc = None
    previous_plugins = oracle.snapshot_plugins({'SR1': root})
    try:
        original = plugins.find_source
        plugins.find_source = lambda realm, repo_root=None: BUILD
        try:
            plugins.install('SR1', root)
        finally:
            plugins.find_source = original
        env = dict(os.environ, SLIMEBRIDGE_TRAVEL_MODE='RESUME', SLIMEBRIDGE_TRAVEL_NEW='SlimeBridge probe', SLIMEBRIDGE_URL='http://127.0.0.1:9',
                   SLIMEBRIDGE_TOKEN='probe', SLIMEBRIDGE_DEV='1', SLIMEBRIDGE_DEV_OPS=json.dumps(ops))
        if sanctuary_oracle.SR1_LOG.is_file():                  # never read the previous run's log as this one
            sanctuary_oracle.SR1_LOG.replace(sanctuary_oracle.SR1_LOG.with_name('Player.before-probe.log'))
        proc = subprocess.Popen([str(root / 'SlimeRancher.exe'), *oracle.WINDOW_ARGS], cwd=str(root), env=env)
        started = time.monotonic()
        taken = set()
        while time.monotonic() - started < seconds and proc.poll() is None:
            text = sanctuary_oracle.SR1_LOG.read_text(encoding='utf-8', errors='replace') if sanctuary_oracle.SR1_LOG.is_file() else ''
            for marker, name in shots:
                if name not in taken and marker in text:
                    time.sleep(3)
                    result.setdefault('shots', []).append(capture(proc.pid, directory / f'{name}.png'))
                    taken.add(name)
            time.sleep(1)
        text = sanctuary_oracle.SR1_LOG.read_text(encoding='utf-8', errors='replace') if sanctuary_oracle.SR1_LOG.is_file() else ''
        result['lines'] = [l for l in text.splitlines() if 'dev op' in l or 'carry' in l or 'travel' in l]
        result['child_exit_code'] = proc.poll()
        result['ops_verified'] = _ops_verified(ops, result['lines'])
        result['passed'] = (result['child_exit_code'] is None and result['ops_verified'] and
                            len(taken) == len(shots))
    finally:
        try:
            oracle.cleanup_steps(result, [
                ('stop_errors', lambda: oracle._stop_owned({'SR1': proc}, {'SR1': root / 'SlimeRancher.exe'}) if proc is not None else {}),
                ('plugins_restored', lambda: oracle.restore_plugins(previous_plugins)),
                ('protected_finalizer', lambda: oracle._finalize_current_user(result, snap, [], directory)),
                ('display_settings_restored', lambda: screen_guard.restore(screens)),
            ])
        finally:
            (directory / 'sr1-ops.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr1', required=True)
    parser.add_argument('--ops', required=True)
    parser.add_argument('--seconds', type=float, default=120)
    parser.add_argument('--shot', action='append', default=[], help='marker=name: capture the game window when the log shows marker')
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    if not args.current_user_authorized:
        raise SystemExit('--current-user-authorized is required')
    shots = [tuple(s.split('=', 1)) for s in args.shot]
    result = run(args.sr1, json.loads(args.ops), args.seconds, shots)
    print('\n'.join(result.get('lines', [])))
    print(json.dumps({k: result.get(k) for k in ('passed', 'shots', 'saves_match_backup_after_restore')}))
    return 0 if result.get('passed') is True and result.get('cleanup_completed') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
