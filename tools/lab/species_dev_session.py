"""LAB-only dev session for imported species (manual play + SLIMEBRIDGE console command).

Arms the imported-species spec on the exact LAB executable, launches it on the current-user
profile after a verified save backup, waits for the registration/ops receipts, scans the game
log for spawn-path NullReferenceExceptions, and then LEAVES THE GAME RUNNING so the operator can
play and use the in-game console command `slimebridge spawn <canonical>`.

Unlike tools.lab.species_import_oracle this is NOT a protected run: the game is not stopped and
saves are not auto-restored while it runs. The backup snapshot path is recorded in the session
receipt; restore with `--restore-backup <path>` after closing the game.

Usage:
    py -3.12 -m tools.lab.species_dev_session --realm SR1 --spec <spec> --species slime.ringtail \
        --current-user-authorized [--ops '["spawn:slime.ringtail"]'] [--timeout 600] [--hold 10]
    py -3.12 -m tools.lab.species_dev_session --restore-backup <snapshot-dir>
"""

import argparse
import json
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from tools.lab import oracle, save_guard
from tools.lab.paths import refuse_reparse, validate_lab_destination
from tools.lab.process_guard import assert_no_other_game_processes

SPEC_ROOT = oracle.REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
LOG_PATHS = {
    'SR1': Path.home() / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher' / 'Player.log',
    'SR2': Path.home() / 'AppData' / 'LocalLow' / 'MonomiPark' / 'SlimeRancher2' / 'Player.log',
}
DEFAULT_OPS = '["spawn:{species}"]'


def _sha(path):
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def scan_log_for_spawn_nres(log_path):
    """Count NRE/class occurrences after the last ops marker in the current log (evidence only).

    Anchoring on the marker inside the whole file avoids Unity's per-launch Player.log rotation,
    which makes byte-offset deltas unreliable.
    """
    log_path = Path(log_path)
    if not log_path.is_file():
        return {'log': str(log_path), 'present': False}
    lines = log_path.read_text(encoding='utf-8', errors='replace').splitlines()
    marker = None
    for index, line in enumerate(lines):
        if '[SlimeBridge] imported ops:' in line:
            marker = index
    post = lines[marker + 1:] if marker is not None else lines
    nres = [line for line in post if 'NullReferenceException' in line]
    emotions = [line for line in post if 'SlimeEmotions' in line or 'SlimeFaceAnimator' in line]
    appearance = [line for line in post if 'SlimeAppearanceApplicator' in line]
    import_lines = [line for line in post if 'SlimeBridgeImport' in line]
    return {'log': str(log_path), 'present': True, 'file_lines': len(lines),
            'ops_marker_found': marker is not None, 'post_marker_lines': len(post),
            'post_marker_nres': len(nres),
            'slime_emotions_or_face_lines': len(emotions),
            'appearance_bounds_lines': len(appearance),
            'import_instance_lines': len(import_lines),
            'nre_samples': nres[:3]}


def run_dev_session(realm, spec_path, species, ops, timeout_s, hold_s, diag=False):
    from tools.lab.paths import validate_spec_destination
    try:
        spec_path = validate_spec_destination(Path(spec_path), SPEC_ROOT)
    except ValueError:
        raise ValueError('spec must be a Bridge-owned spec under Lab/GeneratedContent/species-import')
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if spec.get('schema') != 'bridge-imported-species-spec' or spec.get('realm') != realm:
        raise ValueError('spec schema/realm mismatch')
    target = next((e for e in spec['entries'] if e.get('canonical_id') == species), None)
    if target is None or target.get('status') != 'READY':
        raise ValueError(f'{species} is not READY in the spec')
    executable = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
    refuse_reparse(executable)
    assert_no_other_game_processes()
    if ops is None:
        ops = json.loads(DEFAULT_OPS.format(species=species))

    snap = save_guard.snapshot([realm], 'devsession-pre')
    print(f'[dev-session] save backup: {snap}', flush=True)

    run_id = 'species-devsession-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    # Reparse checks run BEFORE any mkdir: creating the run directory through a junction would
    # write outside the artifact root even if the refusal fires afterwards.
    artifact_root = oracle.REPO_ROOT / 'Lab' / 'RuntimeArtifacts'
    refuse_reparse(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_root / run_id
    refuse_reparse(directory)
    directory.mkdir(exist_ok=False)
    refuse_reparse(directory)

    env = {'SLIMEBRIDGE_IMPORT_SPEC': str(spec_path),
           'SLIMEBRIDGE_IMPORT_OUT': str(directory),
           'SLIMEBRIDGE_IMPORT_RUN': run_id,
           'SLIMEBRIDGE_AUTOLOAD': 'continue',
           'SLIMEBRIDGE_LAB_EXE': str(executable)}
    caches = oracle.REPO_ROOT / 'Lab' / 'AssetCache' / 'sr1-to-sr2'
    if realm == 'SR2' and caches.is_dir():          # SR1 species/Largos the player extracted: one cache folder per canonical id
        env['SLIMEBRIDGE_IMPORT_CACHE_ROOT'] = str(caches)
    if ops:
        env['SLIMEBRIDGE_IMPORT_OPS'] = json.dumps({'ops': ops})
    if diag:
        env['SLIMEBRIDGE_DIAG'] = species
    log_path = LOG_PATHS[realm]

    import os
    launch_env = dict(os.environ, **env)
    for key in ('SLIMEBRIDGE_URL', 'SLIMEBRIDGE_TOKEN'):
        launch_env.pop(key, None)  # dev sessions do not need the BridgeHost
    proc = subprocess.Popen([str(executable), *oracle.WINDOW_ARGS], cwd=str(executable.parent), env=launch_env)
    print(f'[dev-session] launched {realm} pid={proc.pid}', flush=True)

    registration_receipt = directory / 'imported-species-receipt.json'
    ops_receipt = directory / 'imported-ops-receipt.json'
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if registration_receipt.is_file() and (not ops or ops_receipt.is_file()):
            break
        time.sleep(0.5)
    time.sleep(max(0.0, hold_s))

    receipt = {'schema': 'bridge-dev-session-receipt', 'schema_version': 1, 'realm': realm,
               'run_id': run_id, 'species': species, 'ops': ops, 'pid': proc.pid,
               'executable': str(executable), 'executable_sha256': _sha(executable),
               'save_backup': str(snap), 'log': str(log_path),
               'registration_receipt': {'present': registration_receipt.is_file(),
                                        'sha256': _sha(registration_receipt) if registration_receipt.is_file() else None},
               'ops_receipt': {'present': ops_receipt.is_file(),
                               'sha256': _sha(ops_receipt) if ops_receipt.is_file() else None},
               'game_running': proc.poll() is None,
               'nre_scan': scan_log_for_spawn_nres(log_path)}
    if ops_receipt.is_file():
        ops_doc = json.loads(ops_receipt.read_text(encoding='utf-8'))
        receipt['ops_executed'] = [{'op': row.get('op'), 'item': row.get('item'),
                                    'executed': row.get('executed'), 'problems': row.get('problems')}
                                   for row in ops_doc.get('ops', [])]
    receipt['visual_diagnostics'] = {
        path.name: _sha(path) for path in sorted(directory.glob('visual-diagnostics-*.json'))
    }
    out_receipt = directory / 'dev-session-receipt.json'
    out_receipt.write_text(json.dumps(receipt, indent=2, default=str), encoding='utf-8')

    print(json.dumps({k: receipt[k] for k in
                      ('run_id', 'pid', 'game_running', 'save_backup')}, indent=2), flush=True)
    print('[dev-session] the game stays open. In-game console command: '
          f'"slimebridge spawn {species}" (and "slimebridge list").', flush=True)
    print(f'[dev-session] receipt: {out_receipt}', flush=True)
    print(f'[dev-session] after closing the game, restore saves with: '
          f'py -3.12 -m tools.lab.species_dev_session --restore-backup "{snap}"', flush=True)
    return receipt


def restore_backup(snap_path):
    snap = Path(snap_path)
    refuse_reparse(snap)
    assert_no_other_game_processes()
    report = save_guard.compare(snap)
    result = save_guard.restore_originals(snap, report)
    after = save_guard.compare(snap)
    clean = all(not [f for f in r['modified'] + r['removed'] + r['added'] if not f.lower().endswith('.log')]
                for r in after.values())
    print(json.dumps({'restored': result, 'matches_backup': clean}, indent=2, default=str))
    return 0 if clean else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--realm', default='SR1', choices=('SR1', 'SR2'))
    parser.add_argument('--spec')
    parser.add_argument('--species')
    parser.add_argument('--ops', default=None, help='JSON array of ops; default is a single spawn')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--hold', type=float, default=10, help='seconds to sample after receipts')
    parser.add_argument('--diag', action='store_true',
                        help='enable the SLIMEBRIDGE_DIAG visual-diagnostics dump and traces (off by default: a play session stays light)')
    parser.add_argument('--no-diag', action='store_true', help=argparse.SUPPRESS)       # kept so old command lines still parse
    parser.add_argument('--current-user-authorized', action='store_true')
    parser.add_argument('--restore-backup', default=None)
    args = parser.parse_args(argv)
    if args.restore_backup:
        return restore_backup(args.restore_backup)
    if not (args.spec and args.species and args.current_user_authorized):
        parser.error('--spec, --species and --current-user-authorized are required for a dev session')
    ops = json.loads(args.ops) if args.ops is not None else None
    receipt = run_dev_session(args.realm, args.spec, args.species, ops, args.timeout, args.hold,
                              diag=args.diag and not args.no_diag)
    return 0 if receipt['game_running'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
