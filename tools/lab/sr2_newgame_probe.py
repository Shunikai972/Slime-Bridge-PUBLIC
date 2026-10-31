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
NEEDLES = ('travel', 'autoload', 'SlimeBridge_SR2_Core] boot', 'Exception', 'save slot', 'imported', 'user largo', 'visual refused', 'physics')


def run(sr2_root, plugin_dir, timeout_s, import_spec=None, import_ops=None):
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
        if import_spec:                                          # imported species/Largos + their LAB ops in this new game
            env.update(SLIMEBRIDGE_LAB_EXE=str(exe.resolve()), SLIMEBRIDGE_IMPORT_SPEC=str(Path(import_spec).resolve()),
                       SLIMEBRIDGE_IMPORT_OUT=str(directory), SLIMEBRIDGE_IMPORT_RUN=directory.name,
                       SLIMEBRIDGE_IMPORT_CACHE_ROOT=str(REPO_ROOT / 'Lab' / 'AssetCache' / 'sr1-to-sr2'))
            if import_ops:
                env['SLIMEBRIDGE_IMPORT_OPS'] = json.dumps({'ops': import_ops})
        start = log.stat().st_size if log.is_file() else 0       # never read the previous run's log as this one (the launcher's
                                                                 # console may hold it open, so it is not renamed)
        started_at = time.time()

        def fresh():
            if not log.is_file():
                return ''
            text = log.read_text(encoding='utf-8', errors='replace')
            rewritten = log.stat().st_mtime >= started_at and len(text.encode('utf-8', 'replace')) < start
            return text if rewritten or start == 0 else text[start:] if len(text) > start else text
        proc = subprocess.Popen([str(exe), *oracle.WINDOW_ARGS], cwd=str(root), env=env)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and proc.poll() is None:
            text = fresh()
            lines = [l for l in text.splitlines() if any(n in l for n in NEEDLES)]
            result['lines'] = lines[-60:]
            if any('started a new native game' in l for l in lines):
                time.sleep(60)                                   # let the new world load, then look at it
                if import_ops:                                   # the ops run once the player stands in the world
                    end = time.monotonic() + 240
                    while time.monotonic() < end and not (directory / 'imported-ops-receipt.json').is_file():
                        time.sleep(3)
                    time.sleep(8)
                result['screen'] = grab(directory, 'sr2-new-game')
                text = fresh()
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
    parser.add_argument('--import-spec', help='import-spec-SR2.json to register in that game')
    parser.add_argument('--import-ops', default='[]', help='JSON list of imported-species ops (spawn:largo.x.y, diet:...)')
    args = parser.parse_args(argv)
    if not args.current_user_authorized:
        raise SystemExit('--current-user-authorized is required')
    result = run(args.sr2, args.plugin_dir, args.timeout, args.import_spec, json.loads(args.import_ops) or None)
    print('\n'.join(result['lines'][-25:]))
    print(json.dumps({k: result.get(k) for k in ('passed', 'screen', 'saves_match_backup_after_restore')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
