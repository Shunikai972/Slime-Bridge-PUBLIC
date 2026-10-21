"""LAB-only dev-spawn receipt tool (test/developer path, never player progression).

Authorizes one requested dev spawn of an imported species against the derived parity roster
and the exact LAB installation, writes the deterministic decision receipt under the ignored
``Lab/RuntimeArtifacts/dev-spawn`` tree, and refuses everything unsafe: unknown species,
unregistered species, native collisions, non-LAB or fingerprint-mismatched installations.

Native execution stays BLOCKED_ON_LOCAL_QUERY (Q033): this tool never launches a game and
never spawns anything by itself. Its exit code reports the *authorization* result only.

Usage:
  py -3.12 -m tools.lab.dev_spawn --species slime.ringtail --realm SR1
  py -3.12 -m tools.lab.dev_spawn --species slime.rad --realm SR2 --adapters-dir <stamp>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from bridge.cache.fs import atomic_write, confined_root
from bridge.content.canonical import pretty_json
from bridge.conversion.dev_spawn import plan_dev_spawn, validate_dev_spawn
from tools.lab.paths import LAB_GAMES, REPO_ROOT, validate_lab_destination

DEFAULT_CLASSIFICATION = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'classification-20261006-runtime'
ADAPTERS_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-adapters'
RECEIPT_ROOT = REPO_ROOT / 'Lab' / 'RuntimeArtifacts' / 'dev-spawn'
ACTIVE_BUILD = {'SR1': Path('SlimeRancher_Data') / 'Managed' / 'Assembly-CSharp.dll',
                'SR2': Path('GameAssembly.dll')}


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise SystemExit(f'cannot read {path}: {exc}')


def latest_adapters_dir() -> Path:
    candidates = [path for path in sorted(ADAPTERS_ROOT.glob('*')) if (path / 'parity.json').is_file()]
    if not candidates:
        raise SystemExit(f'no prepared adapters under {ADAPTERS_ROOT}; run '
                         'py -3.12 -m tools.content.build_species_adapter first')
    return candidates[-1]


def _adapters_from(directory: Path) -> list:
    docs = []
    for path in sorted(directory.glob('adapter.*.json')):
        docs.append(_load(path))
    if not docs:
        raise SystemExit(f'no adapter descriptors in {directory}')
    return docs


def install_facts(realm: str) -> dict:
    """Read-only identity of the LAB game copy: path validated, active build hashed."""
    game_dir = validate_lab_destination(LAB_GAMES / realm)
    active = game_dir / ACTIVE_BUILD[realm]
    if not active.is_file():
        raise SystemExit(f'active build missing: {active}')
    return {'realm': realm, 'root': str(game_dir), 'lab': True,
            'build_fingerprint': hashlib.sha256(active.read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description='LAB-only dev-spawn authorization receipt (test tool)')
    parser.add_argument('--species', required=True, help='canonical species id, e.g. slime.ringtail')
    parser.add_argument('--realm', required=True, choices=('SR1', 'SR2'), help='target realm to spawn into')
    parser.add_argument('--classification', default=str(DEFAULT_CLASSIFICATION))
    parser.add_argument('--adapters-dir', default=None,
                        help='prepared adapters stamp directory (default: the newest one)')
    parser.add_argument('--policy', default=None, help='foreign-spawn policy document (default NATIVE_ONLY)')
    parser.add_argument('--out', default=None, help='receipt path (default under Lab/RuntimeArtifacts/dev-spawn)')
    args = parser.parse_args()

    classification = _load(Path(args.classification) / 'classification.json')
    adapters_dir = Path(args.adapters_dir) if args.adapters_dir else latest_adapters_dir()
    adapters = _adapters_from(adapters_dir)
    policy = _load(Path(args.policy)) if args.policy else None
    install = install_facts(args.realm)
    doc = plan_dev_spawn({'canonical_id': args.species, 'target_realm': args.realm},
                         classification, adapters, install, policy)
    problems = validate_dev_spawn(doc)
    if problems:
        print(pretty_json({'schema': 'bridge-dev-spawn-tool-error', 'problems': problems}))
        return 5
    root = confined_root(RECEIPT_ROOT)
    out = Path(args.out) if args.out else root / f'{datetime.now().strftime("%Y%m%d-%H%M%S")}-{args.species}.json'
    out = confined_root(out)
    if not out.is_relative_to(root):
        print(pretty_json({'schema': 'bridge-dev-spawn-tool-error',
                           'problems': [f'receipt path must stay under {root}']}))
        return 5
    atomic_write(root, out, pretty_json(doc).encode('utf-8'))
    print(pretty_json(doc))
    print(f'receipt: {out}')
    if doc['decision'] == 'AUTHORIZED':
        print('note: authorization passed; native execution is still BLOCKED_ON_LOCAL_QUERY (Q033)')
        return 0
    return 4


if __name__ == '__main__':
    raise SystemExit(main())
