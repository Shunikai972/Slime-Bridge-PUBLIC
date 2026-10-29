"""Protected check of SR2 1.x new-game creation on a real install, alone (no SR1, no crossing).

    py -3.12 -m tools.lab.sr2_newgame_probe --sr2 <SR2 folder> [--plugin-dir <built net6.0 folder>] --current-user-authorized

SR2 saves and registry keys are backed up and verified first, the plugin is installed, SR2 is started with
SLIMEBRIDGE_TRAVEL_MODE=RESUME + SLIMEBRIDGE_TRAVEL_NEW (the launcher's "new save for this ranch" path), the MelonLoader log is
followed until the game reports the new game (or gives up), then SR2 is stopped and the saves are restored.
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
from tools.lab import oracle, save_guard
from tools.lab.process_guard import assert_no_other_game_processes
from tools.lab.travel_oracle import grab

REPO_ROOT = oracle.REPO_ROOT
NEEDLES = ('travel', 'autoload', 'SlimeBridge_SR2_Core] boot', 'Exception', 'save slot')


def run(sr2_root, plugin_dir, timeout_s):
    root = Path(sr2_root).resolve()
    exe = root / 'SlimeRancher2.exe'
    log = root / 'MelonLoader' / 'Latest.log'
    assert_no_other_game_processes()
    directory = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / ('sr2-newgame-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    directory.mkdir(parents=True)
    result = {'root': str(root), 'lines': [], 'passed': False}
    snap = save_guard.snapshot(['SR2'], 'sr2-newgame-pre')
    from tools.lab import screen_guard
    screens = screen_guard.snapshot(('SR2',))
    proc = None
    try:
        original = plugins.find_source
        plugins.find_source = lambda realm, repo_root=None: Path(plugin_dir)
        try:
            result['plugin'] = plugins.install('SR2', root)
        finally:
            plugins.find_source = original
        env = dict(os.environ, SLIMEBRIDGE_TRAVEL_MODE='RESUME', SLIMEBRIDGE_TRAVEL_NEW='SlimeBridge probe', SLIMEBRIDGE_URL='http://127.0.0.1:9', SLIMEBRIDGE_TOKEN='probe')
        if log.is_file():                                        # never read the previous run's log as this one
            log.replace(log.with_name('Latest.before-probe.log'))
        proc = subprocess.Popen([str(exe), *oracle.WINDOW_ARGS], cwd=str(root), env=env)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and proc.poll() is None:
            text = log.read_text(encoding='utf-8', errors='replace') if log.is_file() else ''
            lines = [l for l in text.splitlines() if any(n in l for n in NEEDLES)]
            result['lines'] = lines[-60:]
            if any('started a new native game' in l for l in lines):
                time.sleep(60)                                   # let the new world load, then look at it
                result['screen'] = grab(directory, 'sr2-new-game')
                text = log.read_text(encoding='utf-8', errors='replace')
                result['lines'] = [l for l in text.splitlines() if any(n in l for n in NEEDLES)][-60:]
                result['passed'] = True
                break
            if any('giving up' in l for l in lines):
                break
            time.sleep(3)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
        time.sleep(2)
        oracle._finalize_current_user(result, snap, [], directory)
        result['display_settings_restored'] = screen_guard.restore(screens)
        (directory / 'sr2-newgame.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
        print(f'evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sr2', required=True)
    parser.add_argument('--plugin-dir', default=str(REPO_ROOT / 'mods' / 'sr2-bridge' / 'bin' / 'Release' / 'net6.0'))
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    if not args.current_user_authorized:
        raise SystemExit('--current-user-authorized is required')
    result = run(args.sr2, args.plugin_dir, args.timeout)
    print('\n'.join(result['lines'][-25:]))
    print(json.dumps({k: result.get(k) for k in ('passed', 'screen', 'saves_match_backup_after_restore')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
