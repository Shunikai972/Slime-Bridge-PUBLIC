"""Protected SR2 native-asset dump: capture the actual Ringtail (or any named species) mesh,
renderer hierarchy and material textures from the user's local SR2 installation into a
Bridge-owned local asset cache, so the SR1 SlimeBridge plugin can load the genuine model.

Runs one SR2-only protected session (SR1+SR2 guard) exactly like the registry oracle, waits for
`model.json` + `dump-receipt.json` from the mod's mesh-dump mode, validates hashes/payloads after
protected cleanup, and publishes `Lab/AssetCache/sr2-to-sr1/<canonical>/` with a cache manifest.
The cache is a private ignored directory: extracted assets are never committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.lab import oracle
from tools.lab.paths import refuse_reparse, validate_lab_destination
from bridge.content.cache_publication import cache_path, checked_path, checked_tree, publish_directory

_CANONICAL_RE = re.compile(r'[a-z0-9]+(?:[._-][a-z0-9]+)*')


def validate_canonical(canonical: str) -> str:
    """Canonical ids are flat tokens: no traversal, separators, or leading dots."""
    if not isinstance(canonical, str) or len(canonical) > 127 or not _CANONICAL_RE.fullmatch(canonical):
        raise ValueError(f'invalid canonical id: {canonical!r}')
    return canonical


DUMP_SCHEMA = 'bridge-native-mesh'
DUMP_SCHEMA_VERSION = 2
RECEIPT_SCHEMA = 'bridge-native-mesh-receipt'
RECEIPT_SCHEMA_VERSION = 2
CONVERTER_VERSION = 2
CACHE_ROOT = ('Lab', 'AssetCache', 'sr2-to-sr1')


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_from_reference(reference: str) -> str:
    """`SlimeDefinition.GlitchTarr` -> `slime.glitch_tarr`."""
    name = reference.split('.')[-1]
    snake = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).lower()
    return 'slime.' + snake


def _texture_name(name):
    return (isinstance(name, str) and name.startswith('tex-') and name.endswith('.png') and
            len(name) <= 240 and Path(name).name == name and
            not any(ord(c) < 32 or c in '\\/:*?"<>|' for c in name))


def _validate_dump(directory: Path, target: str):
    """Bind every model dependency and the exact receipt to confined, verified dump bytes."""
    directory = checked_tree(directory)
    problems = []
    model_path, receipt_path = directory / 'model.json', directory / 'dump-receipt.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    model = json.loads(model_path.read_text(encoding='utf-8'))
    if not isinstance(receipt, dict) or not isinstance(model, dict):
        return {}, ['model and receipt must be objects']
    if receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('schema_version') != RECEIPT_SCHEMA_VERSION:
        problems.append('dump receipt schema/version mismatch')
    if not isinstance(target, str) or not target or receipt.get('target') != target:
        problems.append('receipt target mismatch')
    reference = receipt.get('reference_id')
    if not isinstance(reference, str) or not reference:
        problems.append('receipt has no reference_id')
    model_sha = _sha(model_path)
    if model_sha != receipt.get('model_sha256'):
        problems.append('model.json hash does not match the receipt')
    if model.get('schema') != DUMP_SCHEMA or model.get('schema_version') != DUMP_SCHEMA_VERSION:
        problems.append('model schema/version mismatch')
    if model.get('reference_id') != reference:
        problems.append('model reference_id does not match the receipt')
    texture_files = receipt.get('textures')
    if not isinstance(texture_files, list) or not texture_files:
        return {}, problems + ['receipt carries no texture list']
    texture_hashes, names = {}, {}
    for row in texture_files:
        name = row.get('file') if isinstance(row, dict) else None
        if not _texture_name(name):
            problems.append('unsafe texture filename')
            continue
        if name.casefold() in names and names[name.casefold()] != name:
            problems.append('case-aliased texture filenames')
            continue
        names[name.casefold()] = name
        path = checked_path(directory / name)
        if not path.is_file():
            problems.append(f'texture {name} missing')
        else:
            actual = _sha(path)
            if actual != row.get('sha256'):
                problems.append(f'texture {name} hash mismatch')
            texture_hashes[name] = actual
    skinned, static = model.get('skinned_renderers') or [], model.get('static_renderers') or []
    if not isinstance(skinned, list) or not isinstance(static, list):
        return {}, problems + ['renderer collections must be lists']
    vertex_total, texture_rows = 0, 0
    for renderer in skinned + static:
        if not isinstance(renderer, dict) or not isinstance(renderer.get('vertices'), list):
            problems.append('invalid renderer')
            continue
        vertex_total += len(renderer['vertices'])
        materials = renderer.get('materials') or []
        if not isinstance(materials, list):
            problems.append('invalid material collection')
            continue
        for material in materials:
            if material is None:
                continue
            textures = material.get('textures') if isinstance(material, dict) else None
            if not isinstance(textures, list):
                problems.append('invalid texture collection')
                continue
            for texture in textures:
                if not isinstance(texture, dict) or 'error' in texture or not _texture_name(texture.get('file')):
                    problems.append('failed or invalid model texture export')
                    continue
                texture_rows += 1
                if texture['file'] not in texture_hashes:
                    problems.append('model texture is not covered by the receipt')
    if vertex_total <= 0:
        problems.append('model carries no vertices')
    details = {'reference_id': reference, 'model_sha256': model_sha, 'receipt_sha256': _sha(receipt_path),
               'skinned_renderers': len(skinned), 'static_renderers': len(static),
               'vertex_total': vertex_total, 'texture_files': len(texture_files),
               'texture_rows': texture_rows, 'texture_sha256': dict(sorted(texture_hashes.items()))}
    return details, problems


def make_scenario(directory: Path, target: str, executable: Path, timeout_s: float):
    directory, executable = Path(directory), Path(executable)

    def scenario(host, beats, pids, *, procs):
        child = procs.get('SR2')
        if child is None or pids.get('SR2') != child.pid:
            return {'passed': False, 'error': 'run has no exact owned child'}
        deadline = time.monotonic() + timeout_s
        while True:
            beat = host.heartbeats().get('SR2')
            if not oracle.heartbeat_matches_launched(beat, child, executable):
                return {'passed': False, 'error': 'owned child PID/image or heartbeat changed'}
            if (directory / 'model.json').is_file() and (directory / 'dump-receipt.json').is_file():
                try:
                    details, problems = _validate_dump(directory, target)
                except (PermissionError, json.JSONDecodeError, OSError):
                    pass  # Exclusive publication makes the name visible before its handle closes.
                else:
                    if not oracle.heartbeat_matches_launched(host.heartbeats().get('SR2'), child, executable):
                        return {'passed': False, 'error': 'owned child changed during validation'}
                    return {'passed': not problems, 'problems': problems, 'dump': details,
                            'pid': child.pid, 'executable': str(executable), 'owned_process_verified': True}
            if time.monotonic() >= deadline:
                return {'passed': False, 'error': 'mesh dump did not appear before the deadline'}
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    return scenario


def run_dump(target='Ringtail', canonical=None, *, current_user_authorized=False, timeout_s=600):
    if current_user_authorized is not True:
        raise ValueError('explicit current-user authorization is required')
    executable = validate_lab_destination(oracle.GAMES['SR2'], oracle.LAB)
    game = executable.parent
    active_build = game / 'GameAssembly.dll'
    deployed = game / 'Mods/SlimeBridge.SR2.dll'
    built = oracle.REPO_ROOT / 'mods/sr2-bridge/bin/Release/net6.0/SlimeBridge.SR2.dll'
    for path in (active_build, deployed, built):
        refuse_reparse(path)
    plugin_sha = _sha(built)
    if plugin_sha != _sha(deployed):
        raise ValueError('deployed SR2 plugin does not match the current Release build')
    build_sha = _sha(active_build)

    run_id = 'sr2-mesh-dump-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:12]
    artifact_root = oracle.REPO_ROOT / 'Lab/RuntimeArtifacts'
    refuse_reparse(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    directory = artifact_root / run_id
    directory.mkdir(exist_ok=False)
    refuse_reparse(directory)

    env = {'SLIMEBRIDGE_MESH_DUMP': target,
           'SLIMEBRIDGE_DUMP_OUT': str(directory),
           'SLIMEBRIDGE_LAB_EXE': str(executable),
           'SLIMEBRIDGE_AUTOLOAD': 'continue'}
    result = oracle.run(['SR2'], timeout_s, False, directory, require_status=False,
                        current_user=True, guard_realms=['SR1', 'SR2'], creation_realms=(),
                        scenario=make_scenario(directory, target, executable, timeout_s),
                        extra_env={'SR2': env})
    required = ('passed', 'cleanup_completed', 'sources_untouched', 'lab_fingerprints_unchanged',
                'saves_match_backup_after_restore')
    approved = all(result.get(key) is True for key in required)
    scenario = result.get('scenario', {})
    approved = (approved and scenario.get('passed') is True and
                scenario.get('owned_process_verified') is True)

    run_canonical = canonical
    reference = ''
    details = scenario.get('dump') or {}
    if details.get('reference_id'):
        reference = details['reference_id']
        run_canonical = run_canonical or canonical_from_reference(reference)
    if run_canonical:
        run_canonical = validate_canonical(run_canonical)
    stable = (_sha(active_build) == build_sha and _sha(deployed) == plugin_sha and
              _sha(built) == plugin_sha)
    result.update(passed=False, run_directory=str(directory), run_id=run_id, target=target,
                  canonical_id=run_canonical, plugin_sha256=plugin_sha, active_sha256=build_sha,
                  plugin_and_build_unchanged=stable, dump=details,
                  validation_scope='protected_sr2_mesh_dump_failed')

    cache_dir = None
    if approved and stable and run_canonical:
        try:
            post_details, post_problems = _validate_dump(directory, target)
        except (OSError, ValueError, json.JSONDecodeError) as ex:
            result['post_cleanup_error'] = f'{type(ex).__name__}: {ex}'
        else:
            same = post_details == details
            result['post_cleanup_dump'] = post_details
            result['dump_bound_to_live_scenario'] = same
            if not same:
                result['post_cleanup_error'] = 'dump changed since the live scenario'
            elif post_problems:
                result['post_cleanup_error'] = '; '.join(post_problems)
            else:
                cache_dir = publish_cache(directory, run_canonical)
                result.update(passed=True, validation_scope='protected_sr2_mesh_dump',
                              cache_directory=str(cache_dir))
    refuse_reparse(directory)
    receipt = directory / 'sr2-mesh-dump-receipt.json'
    with receipt.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, default=str)
    return result


def publish_cache(directory: Path, canonical: str) -> Path:
    """Copy the validated dump into the private local asset cache with a manifest."""
    canonical = validate_canonical(canonical)
    directory = checked_tree(directory)
    cache_dir = cache_path(oracle.REPO_ROOT / 'Lab' / 'AssetCache', 'sr2-to-sr1', canonical)
    if directory == cache_dir or directory in cache_dir.parents or cache_dir in directory.parents:
        raise ValueError('dump source and cache target overlap')
    receipt = json.loads((directory / 'dump-receipt.json').read_text(encoding='utf-8'))
    target = receipt.get('target') if isinstance(receipt, dict) else None
    details, problems = _validate_dump(directory, target)
    if problems:
        raise ValueError('; '.join(problems))
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    files = ['model.json', 'dump-receipt.json', *details['texture_sha256']]
    with tempfile.TemporaryDirectory(prefix='.mesh-', dir=cache_dir.parent) as temporary:
        staging = Path(temporary)
        for name in files:
            shutil.copyfile(directory / name, staging / name)
        copied, problems = _validate_dump(staging, target)
        if problems or copied != details:
            raise ValueError('dump changed during cache staging')
        manifest = {'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                    'canonical_id': canonical, 'converter_version': CONVERTER_VERSION,
                    'source_game': 'SR2', 'source_run_id': directory.name,
                    'files': {name: _sha(staging / name) for name in files},
                    'published_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
        (staging / 'cache-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        publish_directory(staging, cache_dir)
    return cache_dir


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--target', default='Ringtail', help='species reference id or name fragment')
    parser.add_argument('--canonical', default=None, help='override the derived canonical id')
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--current-user-authorized', action='store_true', required=True)
    args = parser.parse_args(argv)
    result = run_dump(args.target, args.canonical,
                      current_user_authorized=args.current_user_authorized, timeout_s=args.timeout)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
