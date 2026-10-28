"""Protected SR1-only run on a real install: a new game made by the plugin, then developer-mode ops, then the log.

    py -3.12 -m tools.lab.sr1_ops_probe --sr1 <SR1 folder> --ops '["wait:25","give:PINK_PLORT:2","vac"]' --current-user-authorized

SR1 saves/registry are backed up and restored; the freshly built plugin is installed. Prints the "dev op" log lines.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from apps.launcher import plugins
from tools.lab import oracle, sanctuary_oracle, save_guard
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.window_shot import capture

REPO_ROOT = oracle.REPO_ROOT
BUILD = REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472'


def run(sr1_root, ops, seconds, shots):
    root = Path(sr1_root).resolve()
    assert_no_other_game_processes()
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('sr1-ops-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    result = {'passed': False}
    snap = save_guard.snapshot(['SR1'], 'sr1-ops-pre')
    proc = None
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
        result['passed'] = True
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        time.sleep(2)
        oracle._finalize_current_user(result, snap, [], directory)
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


if __name__ == '__main__':
    main()
