"""Put the bundled base species specs (``bridge/content/specs``) where the launcher and the plugins look for specs.

On a fresh install ``Lab/GeneratedContent/species-import`` is empty: the specs that say which species SlimeBridge imports
(ids, names, diets, palettes) used to be produced only by the developer LAB. They are data, not game assets, so they ship
with SlimeBridge and are copied into ``<stamp>-bundled/`` whenever the newest local spec of a game lacks a bundled species
or gives one another id. The Largo publishers then extend that newest spec with the Largos the player extracted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent / 'specs'
REALMS = ('SR1', 'SR2')


def _identity(spec: dict) -> dict:
    return {e['canonical_id']: int(e.get('native_id', 0)) for e in spec.get('entries', [])
            if not str(e.get('canonical_id', '')).startswith('largo.')}


def _newest(spec_root: Path, realm: str):
    specs = sorted(Path(spec_root).glob(f'*/import-spec-{realm}.json'))
    return specs[-1] if specs else None


def needs_seed(spec_root: Path, bundled: Path = BUNDLED) -> list[str]:
    """The realms whose newest local spec is missing or disagrees with the bundled species (missing species / other ids)."""
    stale = []
    for realm in REALMS:
        source = bundled / f'import-spec-{realm}.json'
        if not source.is_file():
            continue
        wanted = _identity(json.loads(source.read_text(encoding='utf-8')))
        local = _newest(spec_root, realm)
        have = _identity(json.loads(local.read_text(encoding='utf-8'))) if local else {}
        if any(have.get(canonical) != native for canonical, native in wanted.items()):
            stale.append(realm)
    return stale


def ensure_specs(repo_root: Path, bundled: Path = BUNDLED) -> dict:
    """Seed ``<stamp>-bundled`` when needed. {'seeded': [realms], 'directory'?}. Both files go together so the newest
    directory always carries a spec for each game."""
    spec_root = Path(repo_root) / 'Lab' / 'GeneratedContent' / 'species-import'
    stale = needs_seed(spec_root, bundled)
    if not stale:
        return {'seeded': []}
    out = spec_root / (time.strftime('%Y%m%d-%H%M%S') + '-bundled')
    out.mkdir(parents=True, exist_ok=False)
    for realm in REALMS:
        source = bundled / f'import-spec-{realm}.json'
        if source.is_file():
            (out / source.name).write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    return {'seeded': stale, 'directory': str(out)}
