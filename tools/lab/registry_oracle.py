"""Protected LAB-only read-only registry capture, reusing the approved launch/cleanup oracle.

A capture reader alone checks recorded consistency, not runtime execution. The runner binds
that check to its own live child PID and exact LAB executable, then requires protected cleanup.
No deployment, autoload, native creation or inventory command is performed here.
"""

import hashlib
import json
import time
from pathlib import Path

from tools.lab import oracle

from bridge.content.registry_capture import load_capture, SNAPSHOT_FILE, RECEIPT_FILE
from bridge.content.scan import checked_runtime_proof
from tools.lab.paths import refuse_reparse


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capture_summary(directory, realm, active_build, run_id):
    """Independently read one capture without changing its raw evidence; not a PID attestation."""
    directory = Path(directory)
    paths = [directory / SNAPSHOT_FILE, directory / RECEIPT_FILE]
    for path in [Path(active_build), *paths]:
        refuse_reparse(path)
    before = {path.name: _sha(path) for path in paths}
    scan = load_capture(directory, active_build=Path(active_build), realm=realm, expected_run=run_id)
    raw = json.loads(paths[0].read_text(encoding='utf-8'))
    for path in [Path(active_build), *paths]:
        refuse_reparse(path)
    if before != {path.name: _sha(path) for path in paths}:
        raise ValueError('capture evidence changed during validation')
    return {'passed': checked_runtime_proof(scan), 'realm': realm, 'run_id': run_id,
            'completeness': scan['completeness'], 'definition_count': len(scan['definitions']),
            'counts': raw['collector']['counts'], 'errors': scan['errors'],
            'active_build': str(Path(active_build)), 'active_sha256': scan['build_proof']['verified_active_sha256'],
            'artifact_sha256': before, 'validation_scope': 'recorded_consistency_only'}


def make_scenario(realm, directory, active_build, run_id, executable, timeout_s):
    """Wait for both collector files while continuously checking the exact owned child."""
    directory, executable = Path(directory), Path(executable)

    def scenario(host, beats, pids, *, procs):
        child = procs.get(realm)
        if child is None or pids.get(realm) != child.pid:
            return {'passed': False, 'error': 'capture has no exact owned child'}
        deadline = time.monotonic() + timeout_s
        while True:
            beat = host.heartbeats().get(realm)
            if not oracle.heartbeat_matches_launched(beat, child, executable):
                return {'passed': False, 'error': 'owned child PID/image or heartbeat changed'}
            if all((directory / name).is_file() for name in (SNAPSHOT_FILE, RECEIPT_FILE)):
                try:
                    captured = capture_summary(directory, realm, active_build, run_id)
                except PermissionError:
                    # Exclusive native publication makes the name visible before its handle closes.
                    # Retry only unreadable publication, within this deadline and owned-child loop.
                    pass
                else:
                    if not oracle.heartbeat_matches_launched(host.heartbeats().get(realm), child, executable):
                        return {'passed': False, 'error': 'owned child changed during capture validation'}
                    return {'passed': captured['passed'], 'capture': captured, 'pid': child.pid,
                            'executable': str(executable), 'owned_process_verified': True}
            if time.monotonic() >= deadline:
                return {'passed': False, 'error': 'collector did not publish readable files before deadline'}
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    return scenario


def run_capture(realm, *, current_user_authorized=False, timeout_s=600):
    """Run one realm exclusively in registry mode; require the deployed, freshly built plugin."""
    if current_user_authorized is not True:
        raise ValueError('explicit current-user authorization is required')
    if realm not in ('SR1', 'SR2'):
        raise ValueError('invalid registry realm')
    import math
    if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 1800:
        raise ValueError('timeout must be finite and between zero and 1800 seconds')
    from datetime import datetime
    import uuid
    from tools.lab.paths import validate_lab_destination

    root = oracle.REPO_ROOT
    executable = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
    game = executable.parent
    active_build = (game / 'SlimeRancher_Data/Managed/Assembly-CSharp.dll' if realm == 'SR1'
                    else game / 'GameAssembly.dll')
    deployed = (game / 'SRML/Mods/SlimeBridge/SlimeBridge.SR1.dll' if realm == 'SR1'
                else game / 'Mods/SlimeBridge.SR2.dll')
    built = (root / 'mods/sr1-bridge/bin/Release/net472/SlimeBridge.SR1.dll' if realm == 'SR1'
             else root / 'mods/sr2-bridge/bin/Release/net6.0/SlimeBridge.SR2.dll')
    for path in (active_build, deployed, built):
        refuse_reparse(path)
    plugin_sha = _sha(built)
    if plugin_sha != _sha(deployed):
        raise ValueError('deployed plugin does not match the current Release build')
    build_sha = _sha(active_build)
    run_id = 'registry-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    artifact_root = root / 'Lab/RuntimeArtifacts'
    refuse_reparse(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_root / run_id
    directory.mkdir(exist_ok=False)
    refuse_reparse(directory)
    result = oracle.run([realm], timeout_s, False, directory, require_status=False,
                        current_user=True, guard_realms=['SR1', 'SR2'],
                        creation_realms=(),
                        scenario=make_scenario(realm, directory, active_build, run_id, executable, timeout_s),
                        extra_env={realm: {'SLIMEBRIDGE_LAB_REGISTRY_OUT': str(directory),
                                           'SLIMEBRIDGE_LAB_REGISTRY_RUN': run_id,
                                           'SLIMEBRIDGE_LAB_EXE': str(executable)}})
    # Scenario success was provisional: only stopped, restored, fingerprint-stable runs qualify.
    required = ('passed', 'cleanup_completed', 'sources_untouched', 'lab_fingerprints_unchanged',
                'saves_match_backup_after_restore')
    approved = all(result.get(key) is True for key in required)
    scenario = result.get('scenario', {})
    approved = (approved and scenario.get('passed') is True and
                scenario.get('owned_process_verified') is True and scenario.get('capture', {}).get('passed') is True)
    for path in (active_build, deployed, built):
        refuse_reparse(path)
    stable = _sha(active_build) == build_sha and _sha(deployed) == plugin_sha and _sha(built) == plugin_sha
    result.update(passed=False, run_directory=str(directory), run_id=run_id,
                  plugin_sha256=plugin_sha, active_sha256=build_sha, plugin_and_build_unchanged=stable,
                  validation_scope='protected_registry_capture_failed', native_creation_enabled=False,
                  capture_bound_to_live_scenario=False)
    if approved and stable:
        try:
            captured = capture_summary(directory, realm, active_build, run_id)
        except (OSError, ValueError) as ex:
            result['capture_validation_error'] = f'{type(ex).__name__}: {ex}'
        else:
            result['post_cleanup_capture'] = captured
            # Re-reading a valid same-run capture is not enough: it must be exactly the one
            # validated while our child was live, including BOTH raw artifact hashes.
            result['capture_bound_to_live_scenario'] = captured == scenario['capture']
            result['passed'] = captured['passed'] is True and result['capture_bound_to_live_scenario']
            if not result['capture_bound_to_live_scenario']:
                result['capture_validation_error'] = 'capture evidence changed since live scenario'
    if result['passed']:
        result['validation_scope'] = 'protected_live_registry_capture'
    refuse_reparse(directory)
    receipt = directory / 'registry-runtime-receipt.json'
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, default=str)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--realm', required=True, choices=('SR1', 'SR2'))
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args(argv)
    result = run_capture(args.realm, current_user_authorized=args.current_user_authorized,
                         timeout_s=args.timeout)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
