"""Copy built plugin files into a lab game. Refuses any destination outside Lab/Games."""

import os
import shutil
import uuid
from pathlib import Path

from tools.lab.paths import LAB_GAMES, refuse_reparse, validate_lab_destination

DEFAULT_LAB_GAMES = LAB_GAMES


def deploy_plugin(files: list[Path], destination: Path, lab_root: Path = DEFAULT_LAB_GAMES) -> list[Path]:
    destination = validate_lab_destination(destination, lab_root)
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for source in files:
        if not source.is_file():
            raise FileNotFoundError(source)
        target = destination / source.name
        validate_lab_destination(target, lab_root)
        if target.is_symlink() or target.is_junction():
            raise ValueError(f'Destination file is a reparse point: {target}')
        # Never write through an existing destination entry: a symlink or hard link there could
        # point at an original game file. Copy to a fresh exclusive name, then replace the directory
        # entry itself (os.replace swaps the name; it never writes into the old file's data).
        temp = destination / f'.{source.name}.{uuid.uuid4().hex}.deploying'
        try:
            with source.open('rb') as reader, open(temp, 'xb') as writer:
                shutil.copyfileobj(reader, writer)
            shutil.copystat(source, temp)
            refuse_reparse(temp)
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink()
        refuse_reparse(target)
        copied.append(target)
    return copied


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description='Deploy built plugins into Lab games')
    parser.add_argument('realms', nargs='*', default=['SR1', 'SR2'], choices=['SR1', 'SR2'])
    args = parser.parse_args()
    for realm in args.realms:
        if realm == 'SR1':
            source_dir = Path('mods/sr1-bridge/bin/Release/net472')
            dest_dir = Path('Lab/Games/SR1/SRML/Mods/SlimeBridge')
            files = [source_dir / f for f in ('SlimeBridge.SR1.dll', 'SlimeBridge.SR1.pdb', 'modinfo.json') if (source_dir / f).exists()]
            copied = deploy_plugin(files, dest_dir)
            print(f'Deployed {realm}: {[p.name for p in copied]}')
        elif realm == 'SR2':
            source_dir = Path('mods/sr2-bridge/bin/Release/net6.0')
            dest_dir = Path('Lab/Games/SR2/Mods')
            files = [source_dir / f for f in ('SlimeBridge.SR2.dll', 'SlimeBridge.SR2.pdb') if (source_dir / f).exists()]
            copied = deploy_plugin(files, dest_dir)
            print(f'Deployed {realm}: {[p.name for p in copied]}')


if __name__ == '__main__':
    main()
