"""Create independent, never-overwritten game labs without touching source installs."""

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from apps.launcher.installations import GameInstallation, discover_manual
from tools.lab.paths import LAB_GAMES, refuse_reparse, validate_lab_destination


def _fingerprints(root: Path, names) -> dict:
    result = {}
    for filename in names:
        path = root / filename
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            result[filename] = digest.hexdigest()
    return result


def create_lab_copy(installation: GameInstallation | None, destination: Path,
                    lab_root: Path = LAB_GAMES) -> dict:
    if installation is None:
        raise ValueError('A validated installation is required')
    refuse_reparse(installation.root)
    source = installation.root.resolve()
    destination = validate_lab_destination(destination, lab_root)
    if destination.name != installation.game or destination.parent != Path(os.path.abspath(lab_root)):
        raise ValueError('A lab copy must be in Lab/Games/<game>')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Source and destination must be disjoint')
    if 'SlimeRancher-Remaster-TestCopy-20260929' in str(source):
        raise ValueError('The old remaster lab cannot be used as a source')
    staging = destination.with_name(destination.name + '.partial')
    if destination.exists() or staging.exists():
        raise FileExistsError(f'Lab or partial copy already exists: {destination}')
    for root, dirs, files in os.walk(source):
        for name in dirs + files:
            path = Path(root) / name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Symlinks or junctions in the source could escape its root; copy refused')
    key_files = (installation.executable.name, 'UnityPlayer.dll', 'GameAssembly.dll',
                 installation.executable.stem + '_Data/Managed/Assembly-CSharp.dll',
                 installation.executable.stem + '_Data/il2cpp_data/Metadata/global-metadata.dat')
    # Fingerprint the source BEFORE copying, then require the staged copy to match: a source
    # that changes mid-copy (game update, launcher repair) must never be promoted as a lab.
    fingerprints = _fingerprints(source, key_files)
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staging, copy_function=shutil.copy2)
    if _fingerprints(staging, key_files) != fingerprints or _fingerprints(source, key_files) != fingerprints:
        raise RuntimeError('Source changed during copy; partial copy retained and not promoted')
    if discover_manual(staging) is None:
        raise RuntimeError('Copied lab did not pass runtime verification; partial copy retained')
    manifest = {
        'game': installation.game, 'source': str(source), 'source_type': installation.store,
        'copy_date_utc': datetime.now(timezone.utc).isoformat(),
        'runtime': installation.unity_runtime, 'loader': None, 'sha256': fingerprints,
    }
    (staging / 'slimebridge-lab.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    staging.rename(destination)
    return manifest


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Create an isolated full game lab; never overwrite existing labs')
    parser.add_argument('source', type=Path, help='Source game root or executable')
    parser.add_argument('destination', type=Path, help='New target lab folder')
    args = parser.parse_args()
    print(json.dumps(create_lab_copy(discover_manual(args.source), args.destination), indent=2))


if __name__ == '__main__':
    main()
