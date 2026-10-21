"""Protected LAB-only imported-species registration + dev-spawn run (Q007/Q033).

Registers the Bridge-owned imported-species spec inside the exact LAB executable through the
reviewed plugin path (SRML), executes one-shot ops (spawn / vacpack add / save round-trip /
release / query) against the native objects, and requires the receipts to be bound to the live
child, unchanged after protected cleanup, with saves restored and sources untouched.
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from tools.lab import oracle
from tools.lab.paths import refuse_reparse

SPEC_ROOT = oracle.REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
RECEIPT = 'imported-species-receipt.json'
OPS_RECEIPT = 'imported-ops-receipt.json'
DEFAULT_OPS = ['spawn:{species}', 'vac_add:{species}:1', 'save_roundtrip:{species}',
               'release:{species}', 'query:{species}']


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def expected_identities(spec_path, species):
    """Spec-declared native identity for one species (the spec bytes are hash-bound by receipts)."""
    spec_path = Path(spec_path)
    refuse_reparse(spec_path)
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    entry = next((e for e in spec.get('entries', []) if e.get('canonical_id') == species), None)
    if entry is None:
        raise ValueError(f'{species} missing from spec')
    ref = entry.get('native_ref_id')
    return {'native_id': entry.get('native_id'), 'native_ref_id': ref if ref else None}


def validate_registration(directory, realm, run_id, species, spec_sha, expected=None):
    directory = Path(directory)
    path = directory / RECEIPT
    refuse_reparse(path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    problems = []
    if doc.get('schema') != 'bridge-imported-species-receipt' or doc.get('schema_version') != 1:
        problems.append('unexpected registration receipt schema')
    if doc.get('realm') != realm or doc.get('run_id') != run_id:
        problems.append('registration receipt not bound to this run')
    if doc.get('spec_sha256') != spec_sha:
        problems.append('registration receipt does not bind the spec bytes this run consumed')
    entries = {entry.get('canonical_id'): entry for entry in doc.get('entries', [])}
    entry = entries.get(species)
    if entry is None:
        problems.append(f'{species} missing from the registration receipt')
    else:
        checks = entry.get('checks') or {}
        if entry.get('status') != 'REGISTERED':
            problems.append(f'{species} status is {entry.get("status")}: {entry.get("problems")}')
        if entry.get('problems'):
            problems.append(f'{species} registration problems: {entry["problems"]}')
        if expected:
            expected_id = expected.get('native_id')
            if expected_id is not None and entry.get('native_id') != expected_id:
                problems.append(f"{species} registered native_id {entry.get('native_id')} != spec {expected_id}")
            expected_ref = expected.get('native_ref_id')
            if expected_ref:
                if not entry.get('native_ref_id'):
                    problems.append(f'{species} registration carries no native_ref_id but the spec expects {expected_ref}')
                elif entry.get('native_ref_id') != expected_ref:
                    problems.append(f"{species} registered native_ref_id {entry.get('native_ref_id')} != spec {expected_ref}")
        if checks.get('eat_map_entries', 0) < 1:
            problems.append(f'{species} has no native eat-map entries (diet not functional)')
        required = (('definition_indexed', 'definition_in_array', 'group_member', 'prefab_retargeted',
                     'post_refresh_in_array', 'post_refresh_indexed')
                    if realm == 'SR2' else
                    ('definition_indexed', 'prefab_registered', 'plort_prefab_registered',
                     'slime_class', 'plort_class', 'appearance_chosen'))
        for key in required:
            if checks.get(key) is not True:
                problems.append(f'{species} native read-back failed: {key}')
        plort_key = 'plort_group_member' if realm == 'SR2' else 'plort_prefab_registered'
        if checks.get('plort_expected') and checks.get(plort_key) is not True:
            problems.append(f'{species} plort registration read-back failed')
        if realm == 'SR2' and checks.get('plort_expected') and checks.get('plort_prefab_retargeted') is not True:
            problems.append(f'{species} plort prefab retarget read-back failed')
    return {'passed': not problems, 'problems': problems, 'entry': entry,
            'registered_count': doc.get('registered_count'), 'receipt_sha256': _sha(path)}


def parse_requested_ops(ops):
    """Parse the requested op strings into exact (kind, item, count) expectations."""
    requested = []
    for op in ops:
        parts = op.split(':')
        kind = parts[0]
        item = parts[1] if len(parts) > 1 else ''
        count = int(parts[2]) if len(parts) > 2 else 1
        requested.append({'op': kind, 'item': item, 'count': count})
    return requested


def validate_ops(directory, run_id, requested, native_id=None, native_ref_id=None):
    directory = Path(directory)
    path = directory / OPS_RECEIPT
    refuse_reparse(path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    problems = []
    if doc.get('schema') != 'bridge-imported-ops-receipt' or doc.get('schema_version') != 1 or doc.get('run_id') != run_id:
        problems.append('ops receipt not bound to this run')
    ops = doc.get('ops', [])
    if len(ops) != len(requested):
        problems.append(f'ops receipt has {len(ops)} rows for {len(requested)} requested ops')
    for index, want in enumerate(requested):
        if index >= len(ops):
            break
        got = ops[index]
        if got.get('op') != want['op'] or got.get('item') != want['item']:
            problems.append(f"op {index} is {got.get('op')}:{got.get('item')} but {want['op']}:{want['item']} was requested")
        row_count = got.get('count')
        if isinstance(row_count, bool) or row_count != want['count']:
            problems.append(f"op {index} count {row_count!r} != requested {want['count']}")
        # Identity binding for every identity field a row carries (SR2 emits native_ref_id on all
        # rows and instance_ref_id on spawn/release rows; each present field must verify independently).
        for label in ('native_ref_id', 'instance_ref_id'):
            value = got.get(label)
            if value is None:
                continue
            if not native_ref_id:
                problems.append(f"op {index} {label} {value} cannot be verified against the spec")
            elif value != native_ref_id:
                problems.append(f"op {index} {label} {value} != expected {native_ref_id}")
        row_id = got.get('instance_id')
        if row_id is not None:
            if isinstance(row_id, bool):
                problems.append(f"op {index} instance_id is a boolean")
            elif native_id is None:
                problems.append(f"op {index} instance_id {row_id} cannot be verified against the spec")
            elif row_id != native_id:
                problems.append(f"op {index} instance_id {row_id} != expected native id {native_id}")
        if want['op'] == 'spawn' and got.get('instance_ref_id') is None and row_id is None:
            problems.append('spawn row carries no instance identity (instance_ref_id/instance_id)')
    for op in ops:
        if op.get('executed') is not True:
            problems.append(f"op {op.get('op')} on {op.get('item')} not executed: {op.get('problems')}")
        if op.get('problems'):
            problems.append(f"op {op.get('op')} on {op.get('item')} reported problems: {op['problems']}")
        if op.get('op') in ('vac_add', 'vac_remove', 'release', 'save_roundtrip') and op.get('count'):
            if op.get('before') is not None and op.get('after') is not None:
                expected_delta = {'vac_add': op['count'], 'vac_remove': -op['count'],
                                  'release': -op['count']}.get(op['op'], 0)
                if op['after'] - op['before'] != expected_delta:
                    problems.append(f"op {op['op']} count delta {op['after'] - op['before']} != {expected_delta}")
    return {'passed': not problems, 'problems': problems, 'ops': ops, 'receipt_sha256': _sha(path)}


def make_scenario(realm, directory, run_id, executable, timeout_s, species, requested_ops, spec_sha, expected):
    directory, executable = Path(directory), Path(executable)

    def scenario(host, beats, pids, *, procs):
        child = procs.get(realm)
        if child is None or pids.get(realm) != child.pid:
            return {'passed': False, 'error': 'run has no exact owned child'}
        deadline = time.monotonic() + timeout_s
        while True:
            beat = host.heartbeats().get(realm)
            if not oracle.heartbeat_matches_launched(beat, child, executable):
                return {'passed': False, 'error': 'owned child PID/image or heartbeat changed'}
            wanted = [directory / RECEIPT]
            if requested_ops is not None:
                wanted.append(directory / OPS_RECEIPT)
            if all(path.is_file() for path in wanted):
                try:
                    registration = validate_registration(directory, realm, run_id, species, spec_sha, expected)
                    ops = (validate_ops(directory, run_id, requested_ops, expected.get('native_id'),
                                        expected.get('native_ref_id'))
                           if requested_ops is not None else None)
                except (PermissionError, json.JSONDecodeError):
                    # Exclusive native publication makes the name visible before its handle closes.
                    pass
                else:
                    if not oracle.heartbeat_matches_launched(host.heartbeats().get(realm), child, executable):
                        return {'passed': False, 'error': 'owned child changed during receipt validation'}
                    if realm == 'SR2' and requested_ops and any(o['op'] == 'spawn' for o in requested_ops):
                        shot = directory / 'sr2-spawn.png'            # the plugin renders a close-up of each spawned actor a few seconds after the ops
                        hold_until = time.monotonic() + 25
                        while not shot.is_file() and time.monotonic() < hold_until:
                            time.sleep(0.5)
                        time.sleep(1.0)
                    return {'passed': registration['passed'] and (ops is None or ops['passed']),
                            'registration': registration, 'ops': ops, 'pid': child.pid,
                            'executable': str(executable), 'owned_process_verified': True}
            if time.monotonic() >= deadline:
                return {'passed': False, 'error': 'import receipts did not appear before the deadline'}
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    return scenario


def run_import(realm, spec_path, species, ops=None, *, current_user_authorized=False, timeout_s=600):
    """Run one realm exclusively with the imported-species spec; protected cleanup applies."""
    if current_user_authorized is not True:
        raise ValueError('explicit current-user authorization is required')
    if realm not in ('SR1', 'SR2'):
        raise ValueError('unsupported realm')
    spec_path = Path(spec_path)
    from tools.lab.paths import validate_spec_destination
    try:
        spec_path = validate_spec_destination(spec_path, SPEC_ROOT)
    except ValueError:
        raise ValueError('spec must be a Bridge-owned spec under Lab/GeneratedContent/species-import')
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if spec.get('schema') != 'bridge-imported-species-spec' or spec.get('realm') != realm:
        raise ValueError('spec schema/realm mismatch')
    target = next((e for e in spec['entries'] if e.get('canonical_id') == species), None)
    if target is None:
        raise ValueError(f'{species} is not in the spec')
    if target.get('status') != 'READY':
        raise ValueError(f'{species} is not READY in the spec: {target.get("blockers")}')
    if ops is None:
        ops = [op.format(species=species) for op in DEFAULT_OPS]
    requested_ops = parse_requested_ops(ops) if ops else None
    expected = expected_identities(spec_path, species)

    root = oracle.REPO_ROOT
    from tools.lab.paths import validate_lab_destination
    executable = validate_lab_destination(oracle.GAMES[realm], oracle.LAB)
    game = executable.parent
    active_build = game / ('SlimeRancher_Data/Managed/Assembly-CSharp.dll' if realm == 'SR1' else 'GameAssembly.dll')
    deployed = (game / 'SRML/Mods/SlimeBridge/SlimeBridge.SR1.dll' if realm == 'SR1' else game / 'Mods/SlimeBridge.SR2.dll')
    built = (root / 'mods/sr1-bridge/bin/Release/net472/SlimeBridge.SR1.dll' if realm == 'SR1'
             else root / 'mods/sr2-bridge/bin/Release/net6.0/SlimeBridge.SR2.dll')
    for path in (active_build, deployed, built, spec_path):
        refuse_reparse(path)
    plugin_sha = _sha(built)
    if plugin_sha != _sha(deployed):
        raise ValueError('deployed plugin does not match the current Release build')
    build_sha = _sha(active_build)
    spec_sha = _sha(spec_path)

    import uuid
    run_id = 'species-import-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    artifact_root = root / 'Lab/RuntimeArtifacts'
    refuse_reparse(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_root / run_id
    directory.mkdir(exist_ok=False)
    refuse_reparse(directory)

    env = {'SLIMEBRIDGE_IMPORT_SPEC': str(spec_path),
           'SLIMEBRIDGE_IMPORT_OUT': str(directory),
           'SLIMEBRIDGE_IMPORT_RUN': run_id,
           'SLIMEBRIDGE_AUTOLOAD': 'continue',
           'SLIMEBRIDGE_LAB_EXE': str(executable)}
    caches = root / 'Lab' / 'AssetCache' / 'sr1-to-sr2'
    if realm == 'SR2' and caches.is_dir():          # SR1 species/Largos the player extracted: one cache folder per canonical id
        env['SLIMEBRIDGE_IMPORT_CACHE_ROOT'] = str(caches)
    if ops:
        env['SLIMEBRIDGE_IMPORT_OPS'] = json.dumps({'ops': ops})
    result = oracle.run([realm], timeout_s, False, directory, require_status=False,
                        current_user=True, guard_realms=['SR1', 'SR2'], creation_realms=(),
                        scenario=make_scenario(realm, directory, run_id, executable, timeout_s, species, requested_ops, spec_sha, expected),
                        extra_env={realm: env})
    required = ('passed', 'cleanup_completed', 'sources_untouched', 'lab_fingerprints_unchanged',
                'saves_match_backup_after_restore')
    approved = all(result.get(key) is True for key in required)
    scenario = result.get('scenario', {})
    scenario_ops = scenario.get('ops')
    approved = (approved and scenario.get('passed') is True and
                scenario.get('owned_process_verified') is True and
                scenario.get('registration', {}).get('passed') is True and
                (scenario_ops is None or scenario_ops.get('passed') is True))
    stable = _sha(active_build) == build_sha and _sha(deployed) == plugin_sha and _sha(built) == plugin_sha
    result.update(passed=False, run_directory=str(directory), run_id=run_id, spec=str(spec_path),
                  species=species, ops=ops, plugin_sha256=plugin_sha, active_sha256=build_sha,
                  spec_sha256=spec_sha, plugin_and_build_unchanged=stable,
                  validation_scope='protected_species_import_run_failed')
    if approved and stable:
        try:
            registration = validate_registration(directory, realm, run_id, species, spec_sha, expected)
            ops_check = (validate_ops(directory, run_id, requested_ops, expected.get('native_id'),
                                      expected.get('native_ref_id'))
                         if requested_ops is not None else None)
        except (OSError, ValueError) as ex:
            result['post_cleanup_error'] = f'{type(ex).__name__}: {ex}'
        else:
            same = (registration == scenario['registration'] and ops_check == scenario_ops)
            result['post_cleanup_registration'] = registration
            if ops_check is not None:
                result['post_cleanup_ops'] = ops_check
            result['receipts_bound_to_live_scenario'] = same
            result['passed'] = same and registration['passed'] and (ops_check is None or ops_check['passed'])
            if not same:
                result['post_cleanup_error'] = 'receipts changed since the live scenario'
    if result['passed']:
        result['validation_scope'] = 'protected_species_import_run'
    refuse_reparse(directory)
    receipt = directory / 'species-import-runtime-receipt.json'
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, default=str)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--realm', default='SR1', choices=('SR1', 'SR2'))
    parser.add_argument('--spec', required=True)
    parser.add_argument('--species', required=True)
    parser.add_argument('--ops', default=None, help='JSON array of one-shot ops; defaults to the canary set')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    args = parser.parse_args(argv)
    ops = json.loads(args.ops) if args.ops else None
    result = run_import(args.realm, args.spec, args.species, ops=ops,
                        current_user_authorized=args.current_user_authorized, timeout_s=args.timeout)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
