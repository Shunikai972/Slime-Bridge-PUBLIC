"""Run the launcher's Play flow without a window (developer / LAB): everything linked, portal crossings served.

    py -3.12 -m apps.launcher.headless [--target SR1|SR2|BOTH] [--no-wait]

It uses the same LauncherAPI as the desktop launcher (BridgeHost, plugin + island + imported species for LAB copies,
travel between the two games), with its own configuration file under ``Lab/LauncherDev`` so the player's launcher
settings are never touched. While a game runs it keeps polling the crossing broker, which is what lets the SR1
sanctuary portal start Slime Rancher 2 and bring the player back. Saves are backed up first, like the dev session.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / 'Lab' / 'LauncherDev' / 'installations.json'


def make_api():
    from apps.launcher.desktop import LauncherAPI
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    api = LauncherAPI(config_path=CONFIG, known_paths=())
    api.set_preference('prefer_lab', True)
    return api


def backup_saves(realms):
    from tools.lab import save_guard
    return save_guard.snapshot(list(realms), 'play-pre')


def play(target='SR1', wait=True, api=None, out=sys.stdout):
    api = api or make_api()
    realms = ('SR1', 'SR2') if target == 'BOTH' else (target,)
    snapshot = backup_saves(realms)
    print(f'[play] save backup: {snapshot}', file=out, flush=True)
    result = api.launch(target, confirmed=True)
    print(json.dumps({k: result[k] for k in ('ok', 'launched', 'errors', 'warnings')}, indent=2, default=str), file=out, flush=True)
    if not result['ok'] or not wait:
        return result
    print('[play] serving portal crossings while the game runs (Ctrl+C to stop)', file=out, flush=True)
    try:
        from apps.launcher import travel
        while any(api._launcher.running(realm) for realm in ('SR1', 'SR2')) or \
                any(t['state'] in travel.ACTIVE for t in api._travel.transitions):
            api.crossings_poll()
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        api.host_stop()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--target', default='SR1', choices=('SR1', 'SR2', 'BOTH'))
    parser.add_argument('--no-wait', action='store_true', help='launch and return (portal crossings then need the desktop launcher)')
    args = parser.parse_args(argv)
    result = play(args.target, wait=not args.no_wait)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
