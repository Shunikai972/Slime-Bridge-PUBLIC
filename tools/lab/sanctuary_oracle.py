"""Protected LAB run of the SR1 sanctuary: build the island in the real game, play scripted ops, capture screenshots.

    py -3.12 -m tools.lab.sanctuary_oracle --layout Lab/GeneratedContent/sanctuary/layout-001 \
        --ops '["shots","status"]' --current-user-authorized

Owner-authorized current-user mode (same doctrine as the other oracles): the LAB copy of SR1 is launched on the real
profile after a verified save + registry backup; the owned child is stopped at the end and saves, registry and the LAB
plugin folder are restored and re-verified. Nothing is written to the original install. Evidence (receipt, screenshots,
game-log excerpt) lands in Lab/RuntimeArtifacts/<run>/.
"""
from __future__ import annotations

import argparse
import hashlib
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

REPO_ROOT = oracle.REPO_ROOT
PLUGIN_SOURCE = REPO_ROOT / 'mods' / 'sr1-bridge' / 'bin' / 'Release' / 'net472'
PLUGIN_DEST = REPO_ROOT / 'Lab' / 'Games' / 'SR1' / 'SRML' / 'Mods' / 'SlimeBridge'
PLUGIN_FILES = ('SlimeBridge.SR1.dll', 'SlimeBridge.SR1.pdb', 'modinfo.json')
SR1_LOG = Path.home() / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher' / 'Player.log'
LAYOUT_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'sanctuary'


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_plugin():
    """Bytes of the plugin files currently deployed (restored afterwards); absent files are remembered as absent."""
    state = {}
    for name in PLUGIN_FILES:
        path = PLUGIN_DEST / name
        state[name] = path.read_bytes() if path.is_file() else None
    return state


def restore_plugin(state):
    for name, data in state.items():
        path = PLUGIN_DEST / name
        if data is None:
            if path.is_file():
                path.unlink()
        else:
            path.write_bytes(data)
    return {name: (_sha(PLUGIN_DEST / name) if (PLUGIN_DEST / name).is_file() else None) for name in PLUGIN_FILES}


def deploy_plugin():
    from tools.lab.deploy import deploy_plugin as deploy
    files = [PLUGIN_SOURCE / name for name in PLUGIN_FILES if (PLUGIN_SOURCE / name).is_file()]
    if not any(f.name == 'SlimeBridge.SR1.dll' for f in files):
        raise RuntimeError('build the SR1 plugin first: dotnet build mods/sr1-bridge -c Release')
    deploy(files, PLUGIN_DEST)
    return {f.name: _sha(PLUGIN_DEST / f.name) for f in files}


def log_excerpt(started_at):
    if not SR1_LOG.is_file():
        return {'present': False}
    lines = SR1_LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    relevant = [line for line in lines if 'SlimeBridge' in line and 'anctuary' in line]
    nre = [line for line in lines if 'NullReferenceException' in line or 'Exception:' in line]
    return {'present': True, 'lines': len(lines), 'sanctuary_lines': relevant[-40:], 'exception_lines': len(nre),
            'exception_samples': nre[:6]}


def run(layout_dir, ops, timeout_s, current_user_authorized, hold_s=3.0, new_game=True, species_spec=None, species_ops=None):
    if not current_user_authorized:
        raise ValueError('--current-user-authorized is required (owner-authorized protected run)')
    layout_dir = Path(layout_dir).resolve()
    layout_path = layout_dir / 'sanctuary-layout.json'
    if LAYOUT_ROOT not in layout_path.parents or not layout_path.is_file():
        raise ValueError('layout must be Lab/GeneratedContent/sanctuary/<run>/sanctuary-layout.json')
    refuse_reparse(layout_path)
    executable = validate_lab_destination(oracle.GAMES['SR1'], oracle.LAB)
    refuse_reparse(executable)
    assert_no_other_game_processes()
    run_id = 'sanctuary-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    artifact_root = REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    refuse_reparse(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_root / run_id
    directory.mkdir(exist_ok=False)
    (directory / 'state').mkdir()
    result = {'run_id': run_id, 'started_utc': datetime.now(timezone.utc).isoformat(), 'realm': 'SR1', 'ops': ops,
              'layout_sha256': _sha(layout_path), 'passed': False, 'profile_mode': 'current-user-authorized',
              'realms': {}, 'cleanup_completed': False}
    result['sources_before'] = oracle.source_fingerprints(['SR1'])
    if any(v['actual'] != v['expected'] for v in result['sources_before'].values()):
        raise RuntimeError('original SR1 fingerprints differ from the lab manifest; refusing to launch')
    snap = save_guard.snapshot(['SR1'], 'sanctuary-pre')
    result['save_snapshot'] = str(snap)
    print(f'[sanctuary] saves backed up to {snap}', flush=True)
    previous_plugin = snapshot_plugin()
    proc = None
    started = {}
    try:
        assert_no_other_game_processes()
        result['plugin_deployed'] = deploy_plugin()
        env = dict(os.environ,
                   SLIMEBRIDGE_LAB_EXE=str(executable),
                   SLIMEBRIDGE_SANCTUARY_LAYOUT=str(layout_path), SLIMEBRIDGE_SANCTUARY_OUT=str(directory),
                   SLIMEBRIDGE_SANCTUARY_STATE_DIR=str(directory / 'state'),
                   SLIMEBRIDGE_SANCTUARY_OPS=json.dumps(ops))
        if species_spec:
            # the imported SR2 species armed in the same session (LAB only): the sanctuary and the species are measured together
            env.update(SLIMEBRIDGE_IMPORT_SPEC=str(Path(species_spec).resolve()), SLIMEBRIDGE_IMPORT_OUT=str(directory),
                       SLIMEBRIDGE_IMPORT_RUN=run_id)
            if species_ops:
                env['SLIMEBRIDGE_IMPORT_OPS'] = json.dumps({'ops': species_ops})
        if new_game:
            env['SLIMEBRIDGE_SANCTUARY_NEWGAME'] = '1'          # throw-away native new game; the protected cleanup removes its save
        else:
            env['SLIMEBRIDGE_AUTOLOAD'] = 'continue'
        for key in ('SLIMEBRIDGE_URL', 'SLIMEBRIDGE_TOKEN'):
            env.pop(key, None)
        proc = subprocess.Popen([str(executable), *oracle.WINDOW_ARGS], cwd=str(executable.parent), env=env)
        started['SR1'] = proc
        print(f'[sanctuary] launched SR1 pid={proc.pid}', flush=True)
        receipt = directory / 'sanctuary-receipt.json'
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and proc.poll() is None and not receipt.is_file():
            time.sleep(1.0)
        if receipt.is_file():
            time.sleep(hold_s)
            result['receipt'] = json.loads(receipt.read_text(encoding='utf-8'))
            result['receipt_sha256'] = _sha(receipt)
            result['shots_present'] = sorted(p.name for p in (directory / 'shots').glob('*.png')) if (directory / 'shots').is_dir() else []
        else:
            result['error'] = 'no receipt before timeout' if proc.poll() is None else f'game exited early ({proc.returncode})'
        result['game_still_running_before_stop'] = proc.poll() is None
        result['passed'] = bool(result.get('receipt')) and result['receipt'].get('built') is True and not result['receipt'].get('failed')
    except BaseException as ex:
        result['passed'] = False
        result['error'] = f'{type(ex).__name__}: {ex}'
        raise
    finally:
        try:
            result['stop_errors'] = oracle._stop_owned(started) if started else {}
            result['log'] = log_excerpt(result['started_utc'])
            result['plugin_restored'] = restore_plugin(previous_plugin)
            oracle._finalize_current_user(result, snap, ['SR1'], directory)
            if result.get('stop_errors'):
                result['passed'] = False
            result['cleanup_completed'] = not any(result.get(k) for k in ('stop_errors', 'safety_error', 'registry_error',
                                                  'restoration_skipped', 'registry_restore_skipped', 'save_restore_errors'))
        finally:
            (directory / 'sanctuary-oracle.json').write_text(json.dumps(result, indent=2, default=str), encoding='utf-8')
            print(f'[sanctuary] evidence: {directory}', flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--layout', required=True, type=Path, help='Lab/GeneratedContent/sanctuary/<run> directory')
    parser.add_argument('--ops', default='["shots","status"]', help='JSON list: shots, shot:<name>, tp:<target>, status, wait, unlock, reset, gold:N, fill:SLIME')
    parser.add_argument('--timeout', type=float, default=420)
    parser.add_argument('--continue-save', action='store_true', help='continue the profile save instead of a throw-away new game')
    parser.add_argument('--species-spec', type=Path, help='arm the imported species of this import spec in the same session')
    parser.add_argument('--species-ops', default='[]', help='JSON list of imported-species ops (spawn:slime.ringtail:5 ...)')
    parser.add_argument('--current-user-authorized', action='store_true')
    args = parser.parse_args(argv)
    ops = json.loads(args.ops)
    if not isinstance(ops, list) or not all(isinstance(o, str) for o in ops):
        parser.error('--ops must be a JSON list of strings')
    result = run(args.layout, ops, args.timeout, args.current_user_authorized, new_game=not args.continue_save,
                 species_spec=args.species_spec, species_ops=json.loads(args.species_ops) or None)
    print(json.dumps({k: result.get(k) for k in ('run_id', 'passed', 'error', 'cleanup_completed', 'shots_present')}, indent=2))
    return 0 if result['passed'] and result['cleanup_completed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
