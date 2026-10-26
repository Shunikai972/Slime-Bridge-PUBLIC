"""The imported-content fingerprint a player presents when joining a Bridge server (``HELLO.content``).

Two players can only share a hybrid world when they registered the same imported species and Largos under the same ids. The fingerprint
is therefore built from identities only (canonical id, native id, plort id, Largo parents, display name), never from file paths, game
art or machine-local caches: the same SlimeBridge spec gives the same hash on every machine, and one missing species changes it.

  py -3.12 -m bridge.content.content_manifest         # print both realms' hashes
"""
from __future__ import annotations

import json
from pathlib import Path

from bridge.content.canonical import sha256_json

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
SCHEMA = 'bridge-content-manifest'


def entry_identity(entry: dict) -> dict:
    plort = entry.get('plort') or {}
    largo = entry.get('largo') or {}
    return {'canonical_id': entry['canonical_id'], 'native_id': int(entry['native_id']), 'display_name': entry.get('display_name', ''),
            'plort_canonical_id': plort.get('canonical_id', ''), 'plort_native_id': int(plort.get('native_id') or 0),
            'parents': list(largo.get('parents') or [])}


def from_spec(spec: dict) -> dict:
    """{'realm', 'manifest_hash', 'required_packages'} for the READY entries of one species spec."""
    entries = sorted((entry_identity(e) for e in spec.get('entries', []) if e.get('status') == 'READY'), key=lambda e: e['canonical_id'])
    realm = spec.get('realm')
    return {'realm': realm, 'manifest_hash': sha256_json({'schema': SCHEMA, 'version': 1, 'realm': realm, 'entries': entries}),
            'required_packages': [f"generated:{e['canonical_id']}:{realm}" for e in entries]}


def newest(realm: str, spec_root: Path = SPEC_ROOT) -> dict | None:
    """The fingerprint of the newest spec of a realm (the one the game is armed with), or None when there is none."""
    specs = sorted(Path(spec_root).glob(f'*/import-spec-{realm}.json'))
    if not specs:
        return None
    return from_spec(json.loads(specs[-1].read_text(encoding='utf-8')))


def hello_content(spec_root: Path = SPEC_ROOT) -> dict:
    """``{'SR1': hash, 'SR2': hash}`` for the realms that have a spec (the shape of the HELLO message's content map)."""
    result = {}
    for realm in ('SR1', 'SR2'):
        found = newest(realm, spec_root)
        if found:
            result[realm] = found['manifest_hash']
    return result


def main() -> int:
    for realm in ('SR1', 'SR2'):
        found = newest(realm)
        print(realm, found['manifest_hash'] if found else '-', len(found['required_packages']) if found else 0, 'packages')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
