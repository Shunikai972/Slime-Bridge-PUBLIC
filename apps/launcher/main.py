"""Shared development launcher backend (UI is intentionally separate)."""

import os
from pathlib import Path
from apps.launcher.installations import discover_manual, discover_stores


# Optional extra install folders for --known-local / the launcher's first scan (set by the player, never hard-coded).
KNOWN_MANUAL_SR1 = Path(os.environ['SLIMEBRIDGE_SR1_DIR']) if os.environ.get('SLIMEBRIDGE_SR1_DIR') else None
KNOWN_MANUAL_SR2 = Path(os.environ['SLIMEBRIDGE_SR2_DIR']) if os.environ.get('SLIMEBRIDGE_SR2_DIR') else None


def available_installations(manual_paths=()):
    candidates = {(item.game, item.root): item for item in discover_stores()}
    for path in manual_paths:
        item = discover_manual(path)
        # Manually re-selecting a store install must not erase its Steam/Epic provenance.
        if item is not None and (item.game, item.root) not in candidates:
            candidates[(item.game, item.root)] = item
    return sorted(candidates.values(), key=lambda item: (item.game, item.store, str(item.root)))


def main():
    import argparse
    import json
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description='SlimeBridge local install diagnosis')
    parser.add_argument('--manual', action='append', type=Path, default=[], help='Game root or EXE (repeatable)')
    parser.add_argument('--known-local', action='store_true', help='Include SLIMEBRIDGE_SR1_DIR / SLIMEBRIDGE_SR2_DIR')
    args = parser.parse_args()
    paths = args.manual + ([p for p in (KNOWN_MANUAL_SR1, KNOWN_MANUAL_SR2) if p] if args.known_local else [])
    print(json.dumps([asdict(item) for item in available_installations(paths)], indent=2, default=str))


if __name__ == '__main__':
    main()
