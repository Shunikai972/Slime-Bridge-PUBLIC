"""Largos that exist in Slime Rancher 2 but not in Slime Rancher 1: which they are, and the import-spec entries that register them.

Nothing here (and nothing the plugin loads) ships the game's art: the Largos' models, icons and faces are extracted from the player's
own SR2 install into the converted-model cache (`species_pipeline`, kind `largo`), and the SR1 plugin builds the Largos from that cache
plus a spec this module writes. A Largo is only listed in the spec when its cache exists, so the plugin never refers to art that has
not been extracted.

  py -3.12 -m bridge.content.largos              # list what is missing from SR1
  py -3.12 -m bridge.content.largos --publish    # write the spec (species spec + a Largo entry per extracted cache)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from bridge.content.dotnet_enum import read_nested_enum
from bridge.content.scan import scan_sr2_catalog
from bridge.content.sr2_asset_extract import CACHE_ROOT, DEFAULT_SR2_DATA, REPO_ROOT

DEFAULT_SR1_DATA = REPO_ROOT / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data'
SPEC_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
LEDGER = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'largo-ids.json'
ID_TABLE = Path(__file__).with_name('largo_ids.json')
LARGO_ID_BASE = 960000            # inside the reserved 900000-999999 range, above the species ids
LARGO_ID_LIMIT = 999999
TEMPLATE_LARGO = 'PINK_ROCK_LARGO'
CATALOG = Path('StreamingAssets') / 'aa' / 'catalog.json'
SPEC_FILE = 'import-spec-SR1.json'


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', name.lower())


# ------------------------------------------------------------------------------------------------------------------ the games
def sr2_largos(sr2_dir: Path = DEFAULT_SR2_DATA) -> dict[frozenset, str]:
    """{frozenset({slug_a, slug_b}): SR2 definition name} read from the addressables catalog."""
    scan = scan_sr2_catalog(Path(sr2_dir) / CATALOG)
    bases = {row['name']: slug(row['name']) for row in scan['definitions'] if row['is_largo'] is False}
    found: dict[frozenset, str] = {}
    for row in scan['definitions']:
        if row['is_largo'] is not True:
            continue
        name = row['name']
        options = [(a, name[len(a):]) for a in bases if name.startswith(a) and name[len(a):] in bases]
        if len(options) != 1:
            continue                                  # an ambiguous or unknown name is never guessed
        a, b = options[0]
        found[frozenset({bases[a], bases[b]})] = name
    return found


def sr1_species(sr1_dir: Path = DEFAULT_SR1_DATA) -> tuple[set[str], set[frozenset]]:
    """(native base slime slugs, native Largo pairs) from SR1's own Identifiable enum."""
    members = read_nested_enum(Path(sr1_dir) / 'Managed' / 'Assembly-CSharp.dll', 'Identifiable', 'Id')
    names = set(members.values())
    bases = {n[:-len('_SLIME')].lower() for n in names if n.endswith('_SLIME') and '_' not in n[:-len('_SLIME')]}
    pairs = set()
    for n in names:
        if n.endswith('_LARGO'):
            parts = n[:-len('_LARGO')].lower().split('_')
            if len(parts) == 2:
                pairs.add(frozenset(parts))
    return bases, pairs


# ---------------------------------------------------------------------------------------------------------------- the roster
def imported_species(spec_path: Path | None = None) -> set[str]:
    """Slugs of the species the newest spec registers (READY entries named slime.<slug>)."""
    spec_path = spec_path or _newest_species_spec()
    if spec_path is None:
        return set()
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    return {e['canonical_id'].split('.', 1)[1] for e in spec.get('entries', [])
            if e.get('status') == 'READY' and str(e.get('canonical_id', '')).startswith('slime.')}


def _newest_species_spec() -> Path | None:
    """Newest spec of the species themselves (the ones the Largo generator has not appended to)."""
    specs = [p for p in sorted(SPEC_ROOT.glob('*/' + SPEC_FILE)) if not p.parent.name.endswith('-largos')]
    return specs[-1] if specs else None


def largo_roster(sr2_dir: Path = DEFAULT_SR2_DATA, sr1_dir: Path = DEFAULT_SR1_DATA, spec_path: Path | None = None,
                 only: list[str] | None = None) -> list[dict]:
    """Every SR2 Largo SR1 lacks: [{'canonical', 'species', 'kind', 'parents', 'display_name', 'status', 'blockers'}].

    READY when both parents exist in SR1 (native slime or imported species); otherwise PENDING_DATA with the reason."""
    theirs = sr2_largos(sr2_dir)
    bases, native_pairs = sr1_species(sr1_dir)
    imported = imported_species(spec_path)
    rows = []
    for pair, name in sorted(theirs.items(), key=lambda kv: kv[1]):
        if pair in native_pairs:
            continue
        a, b = sorted(pair)
        blockers = [f'{s} is neither a native SR1 slime nor an imported species' for s in (a, b) if s not in bases and s not in imported]
        display = ' '.join(re.findall(r'[A-Z][a-z]+', name)) + ' Largo'
        row = {'canonical': f'largo.{a}.{b}', 'species': name, 'kind': 'largo', 'parents': [a, b], 'display_name': display,
               'status': 'READY' if not blockers else 'PENDING_DATA', 'blockers': blockers}
        if only and name.lower() not in {o.lower() for o in only}:
            continue
        rows.append(row)
    return rows


# ------------------------------------------------------------------------------------------------------------------- the spec
def shared_ids() -> dict[str, int]:
    """The id table every install shares (committed): canonical Largo -> native id."""
    return {k: int(v) for k, v in json.loads(ID_TABLE.read_text(encoding='utf-8'))['ids'].items()}


def allocate_ids(canonicals: list[str], ledger_path: Path = LEDGER) -> dict[str, int]:
    """Stable native ids: the same Largo has the same id on every install (saves and online play store the number).

    The committed table decides first; the local ledger only holds what the table does not know (a species added after it was
    written), appended after everything already used."""
    table = shared_ids() if ID_TABLE.is_file() else {}
    ledger = {}
    if Path(ledger_path).is_file():
        ledger = {k: int(v) for k, v in json.loads(Path(ledger_path).read_text(encoding='utf-8')).items()}
    used = set(table.values()) | set(ledger.values())
    changed = False
    result = {}
    for canonical in sorted(canonicals):
        if canonical in table:
            result[canonical] = table[canonical]
            continue
        if canonical not in ledger:
            candidate = max(used | {LARGO_ID_BASE - 1}) + 1
            if candidate > LARGO_ID_LIMIT:
                raise ValueError('the reserved Largo id range is exhausted')
            ledger[canonical] = candidate
            used.add(candidate)
            changed = True
        result[canonical] = ledger[canonical]
    if changed:
        Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
        Path(ledger_path).write_text(json.dumps(dict(sorted(ledger.items())), indent=2), encoding='utf-8')
    return {c: result[c] for c in canonicals}


def cache_ready(canonical: str, cache_root: Path = REPO_ROOT / 'Lab' / 'AssetCache') -> bool:
    folder = Path(cache_root) / 'sr2-to-sr1' / canonical
    return all((folder / name).is_file() for name in ('cache-manifest.json', 'model.json'))


def cache_palette(canonical: str, cache_root: Path = REPO_ROOT / 'Lab' / 'AssetCache') -> dict | None:
    """The spec's `appearance` block of a Largo, read from its cache: the body's top/middle/bottom colors (the slime's splats, eating
    effects, vac and ammo slot colors come from it; without it they keep the placeholder species' colors). None when the cache has no body."""
    try:
        model = json.loads((Path(cache_root) / 'sr2-to-sr1' / canonical / 'model.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    for renderer in model.get('skinned_renderers', []):
        if '/body' not in '/' + str(renderer.get('path', '')).split('/', 1)[-1].lower():
            continue
        for material in renderer.get('materials', []):
            colors = material.get('colors', {})
            if all(k in colors and len(colors[k]) == 4 for k in ('_TopColor', '_MiddleColor', '_BottomColor')):
                top, middle, bottom = (list(colors[k]) for k in ('_TopColor', '_MiddleColor', '_BottomColor'))
                return {'palette': {'top': top, 'middle': middle, 'bottom': bottom}, 'ammo': list(middle), 'save_set': '', 'source': 'cache:body'}
    return None


def largo_entry(row: dict, native_id: int, palette: dict | None = None) -> dict:
    entry = {
        'canonical_id': row['canonical'], 'display_name': row['display_name'], 'status': 'READY', 'blockers': [],
        'native_id': native_id, 'native_ref_id': 'SlimeDefinition.' + row['species'],
        'plort': {'canonical_id': '', 'display_name': '', 'native_id': 0, 'native_ref_id': '', 'template_native_ref': ''},
        'template': {'slime_native_ref': TEMPLATE_LARGO, 'plort_native_ref': ''},
        'diet': {'major_food_groups': [], 'favorites': [], 'additional_foods': [], 'favorite_production_count': 2},
        'flags': {'can_largofy': False, 'visual_mode': 'imported-model', 'behavior_classification': 'largo'},
        'largo': {'parents': list(row['parents']), 'sr2_name': row['species']},
    }
    if palette:
        entry['appearance'] = palette
    return entry


def publish_spec(cache_root: Path = REPO_ROOT / 'Lab' / 'AssetCache', sr2_dir: Path = DEFAULT_SR2_DATA,
                 sr1_dir: Path = DEFAULT_SR1_DATA, spec_root: Path = SPEC_ROOT, ledger_path: Path = LEDGER) -> dict:
    """Write a new species spec dir: the newest species spec plus a Largo entry for every extracted, READY Largo.

    The directory name ends in `-largos` and sorts after the species specs, so everything that picks "the newest spec" (the
    launcher's play, the LAB oracles) registers the Largos too. Species stay exactly as in the source spec."""
    source = _newest_species_spec()
    if source is None:
        raise ValueError('no species import spec to extend')
    spec = json.loads(source.read_text(encoding='utf-8'))
    rows = [r for r in largo_roster(sr2_dir, sr1_dir, source) if r['status'] == 'READY' and cache_ready(r['canonical'], cache_root)]
    ids = allocate_ids([r['canonical'] for r in rows], ledger_path)
    entries = [e for e in spec['entries'] if not str(e.get('canonical_id', '')).startswith('largo.')]
    entries += [largo_entry(r, ids[r['canonical']], cache_palette(r['canonical'], cache_root)) for r in rows]
    spec['entries'] = entries
    stamp = time.strftime('%Y%m%d-%H%M%S')
    out_dir = Path(spec_root) / f'{stamp}-largos'
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / SPEC_FILE).write_text(json.dumps(spec, indent=2), encoding='utf-8')
    sibling = source.parent / 'import-spec-SR2.json'
    if sibling.is_file():
        (out_dir / sibling.name).write_text(sibling.read_text(encoding='utf-8'), encoding='utf-8')
    return {'ok': True, 'directory': str(out_dir), 'largos': len(rows), 'source_spec': str(source)}


def refresh_spec(repo_root: Path = REPO_ROOT) -> dict:
    """Publish a new spec when the Largo caches that exist differ from the Largos the newest spec lists.

    Cheap and deterministic (SR2 catalog + SR1 enum + the cache folders); the pack flow runs it before every start so a finished
    Extract is picked up without any manual step. {'changed', 'largos', 'directory'?}."""
    repo_root = Path(repo_root)
    sr2_dir = repo_root / 'Lab' / 'Games' / 'SR2' / 'SlimeRancher2_Data'
    sr1_dir = repo_root / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data'
    cache_root = repo_root / 'Lab' / 'AssetCache'
    spec_root = repo_root / 'Lab' / 'GeneratedContent' / 'species-import'
    specs = sorted(spec_root.glob('*/' + SPEC_FILE))
    if not specs or not (sr2_dir / CATALOG).is_file() or not (sr1_dir / 'Managed' / 'Assembly-CSharp.dll').is_file():
        return {'changed': False, 'largos': 0, 'reason': 'game data or species spec not available'}
    listed = {e['canonical_id'] for e in json.loads(specs[-1].read_text(encoding='utf-8')).get('entries', [])
              if str(e.get('canonical_id', '')).startswith('largo.')}
    wanted = {r['canonical'] for r in largo_roster(sr2_dir, sr1_dir) if r['status'] == 'READY' and cache_ready(r['canonical'], cache_root)}
    if wanted == listed:
        return {'changed': False, 'largos': len(wanted)}
    published = publish_spec(cache_root, sr2_dir, sr1_dir, spec_root, repo_root / 'Lab' / 'GeneratedContent' / 'largo-ids.json')
    return {'changed': True, 'largos': published['largos'], 'directory': published['directory']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--sr2-dir', default=str(DEFAULT_SR2_DATA))
    parser.add_argument('--sr1-dir', default=str(DEFAULT_SR1_DATA))
    args = parser.parse_args(argv)
    if args.publish:
        print(json.dumps(publish_spec(sr2_dir=Path(args.sr2_dir), sr1_dir=Path(args.sr1_dir)), indent=2))
        return 0
    rows = largo_roster(Path(args.sr2_dir), Path(args.sr1_dir))
    tally = {}
    for row in rows:
        tally[row['status']] = tally.get(row['status'], 0) + 1
        print(f"{row['canonical']:32} {row['species']:22} {row['status']:12} {'; '.join(row['blockers'])}")
    print(json.dumps(tally))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
