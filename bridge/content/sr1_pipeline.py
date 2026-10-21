"""SR1 -> SR2 species and Largo pipeline: what SR2 lacks, and the caches the SR2 plugin imports.

The mirror of `species_pipeline` / `largos` (SR2 -> SR1). Everything is read from the player's own Slime Rancher install; the caches
(`<cache root>/sr1-to-sr2/<canonical>`: model, textures, icon, clips, palette) are local only and the SR2 plugin never ships any art.

What counts as missing: a species or Largo whose prefab the SR2 build does not carry in `resources.assets` (the base game's own content;
Mosaic, Quantum, Rad and everything mixed with them exist in SR2's bundles as unreleased data, and its runtime never registers them).

  py -3.12 -m bridge.content.sr1_pipeline            # list what SR2 lacks
  py -3.12 -m bridge.content.sr1_pipeline --build    # extract the caches
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

from bridge.content.dotnet_enum import read_nested_enum
from bridge.content.largos import LEDGER, allocate_ids, largo_entry
from bridge.content.sr2_asset_extract import DEFAULT_SR2_DATA, REPO_ROOT

DEFAULT_SR1_DATA = REPO_ROOT / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data'
CACHE_ROOT = REPO_ROOT / 'Lab' / 'AssetCache'
PREFAB_NAMES_FILE = CACHE_ROOT / 'sr2-prefab-names.json'
REPLACED_ROOT = CACHE_ROOT / 'pipeline-replaced'
COMPLETE_MARKERS = ('cache-manifest.json', 'model.json', 'icon-descriptor.json')
# SR1 species that are not ordinary playable species (drones, tarr, gold ... stay where they are)
SKIP_SPECIES = {'tarr', 'gold', 'lucky', 'puddle', 'fire'}
SPEC_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
SPEC_FILE = 'import-spec-SR2.json'
TEMPLATE_LARGO = 'SlimeDefinition.PinkBoom'      # any native SR2 Largo: its prefab is the placeholder the imported model replaces


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', name.lower())


def camel(upper_name: str) -> str:
    return ''.join(part.capitalize() for part in upper_name.split('_'))


# ------------------------------------------------------------------------------------------------------------------ the games
def sr2_prefab_names(sr2_dir: Path = DEFAULT_SR2_DATA, cache_file: Path = PREFAB_NAMES_FILE) -> set[str]:
    """Names of the `slime*` prefabs in SR2's resources.assets (cached per file size/time)."""
    sr2_dir = Path(sr2_dir)
    resources = sr2_dir / 'resources.assets'
    stamp = {'size': resources.stat().st_size, 'mtime_ns': resources.stat().st_mtime_ns}
    if Path(cache_file).is_file():
        try:
            held = json.loads(Path(cache_file).read_text(encoding='utf-8'))
            if held.get('stamp') == stamp:
                return set(held['names'])
        except (OSError, ValueError, KeyError):
            pass
    import UnityPy
    env = UnityPy.load(str(resources))
    names = set()
    for obj in env.objects:
        if obj.type.name == 'GameObject':
            name = obj.read().m_Name
            if name and name.startswith('slime'):
                names.add(name)
    Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
    Path(cache_file).write_text(json.dumps({'stamp': stamp, 'names': sorted(names)}), encoding='utf-8')
    return names


def sr1_content(sr1_dir: Path = DEFAULT_SR1_DATA) -> tuple[list[str], list[str]]:
    """(species enum names, Largo enum names) of SR1, without the _SLIME / _LARGO suffix."""
    members = read_nested_enum(Path(sr1_dir) / 'Managed' / 'Assembly-CSharp.dll', 'Identifiable', 'Id')
    names = set(members.values())
    return (sorted(n[:-len('_SLIME')] for n in names if n.endswith('_SLIME')),
            sorted(n[:-len('_LARGO')] for n in names if n.endswith('_LARGO')))


# ---------------------------------------------------------------------------------------------------------------------- roster
def roster(sr1_dir: Path = DEFAULT_SR1_DATA, sr2_dir: Path = DEFAULT_SR2_DATA, only: list[str] | None = None) -> list[dict]:
    """Every SR1 species and Largo the SR2 build lacks: [{'canonical','species','prefab','kind','parents','display_name','status'}]."""
    species_names, largo_names = sr1_content(sr1_dir)
    have = sr2_prefab_names(sr2_dir)
    rows = []
    missing_species = set()
    for name in species_names:
        if slug(name) in SKIP_SPECIES or 'TARR' in name or name.endswith('DRONE'):
            continue
        prefab = 'slime' + camel(name)
        if prefab in have:
            continue
        missing_species.add(slug(name))
        rows.append({'canonical': 'slime.' + slug(name), 'species': camel(name), 'prefab': prefab, 'kind': 'base', 'parents': [],
                     'display_name': ' '.join(w.capitalize() for w in name.split('_')), 'status': 'READY', 'blockers': []})
    for name in largo_names:
        parts = name.lower().split('_')
        if len(parts) != 2:
            continue
        prefab = 'slime' + camel(name)
        if prefab in have:
            continue
        a, b = sorted(parts)
        rows.append({'canonical': f'largo.{a}.{b}', 'species': camel(name), 'prefab': prefab, 'kind': 'largo', 'parents': [a, b],
                     'display_name': ' '.join(w.capitalize() for w in parts) + ' Largo', 'status': 'READY', 'blockers': []})
    if only:
        wanted = {o.lower() for o in only}
        rows = [r for r in rows if r['species'].lower() in wanted or r['canonical'] in wanted]
    return rows


# ---------------------------------------------------------------------------------------------------------------------- caches
def cache_dir(canonical: str, cache_root: Path = CACHE_ROOT) -> Path:
    return Path(cache_root) / 'sr1-to-sr2' / canonical


def cache_is_complete(canonical: str, cache_root: Path = CACHE_ROOT) -> bool:
    folder = cache_dir(canonical, cache_root)
    return all((folder / name).is_file() for name in COMPLETE_MARKERS)


def build_row(row: dict, sr1_dir: Path, cache_root: Path, assets, force: bool = False) -> dict:
    from bridge.content import sr1_asset_extract
    canonical = row['canonical']
    report = {'canonical': canonical, 'species': row['species'], 'notes': []}
    target = cache_dir(canonical, cache_root)
    if cache_is_complete(canonical, cache_root) and not force:
        report.update(status='UP_TO_DATE', mode='existing')
        return report
    work = Path(cache_root) / 'pipeline-tmp' / ('sr1-' + canonical)
    shutil.rmtree(work, ignore_errors=True)
    work.parent.mkdir(parents=True, exist_ok=True)
    result = sr1_asset_extract.extract_species(row['species'], canonical, sr1_dir, work, allow_nonlab=True, prefab=row['prefab'],
                                               largo=row['kind'] == 'largo', assets=assets)
    if target.exists():
        REPLACED_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(REPLACED_ROOT / f"{canonical}-sr1-{time.strftime('%Y%m%d-%H%M%S')}"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(work), str(target))
    report.update(status='BUILT', mode='sr1-offline', vertices=result.get('vertex_total'), clips=result.get('clips'))
    return report


def run(rows: list[dict], sr1_dir: Path = DEFAULT_SR1_DATA, cache_root: Path = CACHE_ROOT, force: bool = False, progress=None,
        cancelled=None, ram_gb: float | None = None) -> dict:
    """Same contract as species_pipeline.run (progress / cancelled hooks, per-row failures reported, never fatal)."""
    from bridge.content import sr1_asset_extract
    sr1_dir = Path(sr1_dir)
    if not (sr1_dir / 'Managed' / 'Assembly-CSharp.dll').is_file():
        raise ValueError(f'SR1 data folder not found: {sr1_dir}')
    assets = sr1_asset_extract.Sr1Assets(sr1_dir)
    reports = []
    for index, row in enumerate(rows):
        if cancelled and cancelled():
            raise InterruptedError('cancelled')
        if progress:
            progress(index, len(rows), f"[{row['canonical']}] working")
        try:
            report = build_row(row, sr1_dir, Path(cache_root), assets, force)
        except MemoryError:
            report = {'canonical': row['canonical'], 'species': row['species'], 'status': 'SKIPPED_RAM', 'notes': [],
                      'error': f'ran out of memory at the {ram_gb:g} GB limit' if ram_gb else 'ran out of memory'}
        except Exception as ex:  # noqa: BLE001 - one row failing must not stop the roster
            report = {'canonical': row['canonical'], 'species': row['species'], 'status': 'FAILED',
                      'error': f'{type(ex).__name__}: {ex}'[:300], 'notes': []}
        reports.append(report)
        if progress:
            progress(index + 1, len(rows), f"[{row['canonical']}] {report['status']}")
    tally: dict[str, int] = {}
    for r in reports:
        tally[r['status']] = tally.get(r['status'], 0) + 1
    return {'ok': tally.get('FAILED', 0) == 0, 'count': len(reports), 'by_status': tally, 'species': reports}


# ------------------------------------------------------------------------------------------------------------------------ spec
def newest_spec(spec_root: Path = SPEC_ROOT) -> Path | None:
    specs = sorted(Path(spec_root).glob('*/' + SPEC_FILE))
    return specs[-1] if specs else None


def cached_rows(cache_root: Path = CACHE_ROOT) -> list[dict]:
    """Rows for the caches that exist (READY by construction): needs neither game, so the launcher can ask on every Play."""
    folder = Path(cache_root) / 'sr1-to-sr2'
    rows = []
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        name = path.name
        if '-' in name or not cache_is_complete(name, cache_root):
            continue
        kind, _, rest = name.partition('.')
        if kind == 'slime':
            rows.append({'canonical': name, 'species': rest.capitalize(), 'kind': 'base', 'parents': [], 'display_name': rest.capitalize()})
        elif kind == 'largo' and rest.count('.') == 1:
            parents = rest.split('.')
            rows.append({'canonical': name, 'species': ''.join(p.capitalize() for p in parents), 'kind': 'largo', 'parents': parents,
                         'display_name': ' '.join(p.capitalize() for p in parents) + ' Largo'})
    return rows


def publish_spec(cache_root: Path = CACHE_ROOT, spec_root: Path = SPEC_ROOT, ledger_path: Path = LEDGER,
                 sr1_dir: Path = DEFAULT_SR1_DATA, sr2_dir: Path = DEFAULT_SR2_DATA) -> dict:
    """A new SR2 species spec: the newest one (ids, diets) plus every extracted SR1 Largo, and species that have no diet are held back.

    The directory name ends in `-sr1` and sorts after the others, so "the newest spec" (launcher play, LAB oracles) picks it up."""
    source = newest_spec(spec_root)
    if source is None:
        raise ValueError('no SR2 species spec to extend')
    spec = json.loads(source.read_text(encoding='utf-8'))
    rows = cached_rows(cache_root)
    largos = [r for r in rows if r['kind'] == 'largo']
    ids = allocate_ids([r['canonical'] for r in largos], ledger_path)
    entries = [e for e in spec['entries'] if not str(e.get('canonical_id', '')).startswith('largo.')]
    ready_species = {r['canonical'] for r in rows if r['kind'] == 'base'}
    for entry in entries:
        if entry.get('canonical_id') in ready_species and not entry.get('diet', {}).get('major_food_groups'):
            entry['status'] = 'PENDING_DATA'
            entry.setdefault('blockers', []).append('no SR2 diet mapping for this species yet')
    for row in largos:
        entry = largo_entry(row, ids[row['canonical']])
        entry['native_ref_id'] = 'SlimeBridge.SlimeDefinition.' + row['canonical']
        entry['template']['slime_native_ref'] = TEMPLATE_LARGO
        entries.append(entry)
    spec['entries'] = entries
    out_dir = Path(spec_root) / f"{time.strftime('%Y%m%d-%H%M%S')}-sr1"
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / SPEC_FILE).write_text(json.dumps(spec, indent=2), encoding='utf-8')
    sibling = source.parent / 'import-spec-SR1.json'
    if sibling.is_file():                                   # the SR1 side of the same pack stays beside it
        (out_dir / sibling.name).write_text(sibling.read_text(encoding='utf-8'), encoding='utf-8')
    return {'ok': True, 'directory': str(out_dir), 'largos': len(largos), 'species': len(ready_species), 'source_spec': str(source)}


def refresh_spec(repo_root: Path = REPO_ROOT) -> dict:
    """Publish a new SR2 spec when the Largo caches that exist differ from the Largos the newest one lists (the pack flow runs it)."""
    repo_root = Path(repo_root)
    sr1_dir = repo_root / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data'
    sr2_dir = repo_root / 'Lab' / 'Games' / 'SR2' / 'SlimeRancher2_Data'
    cache_root = repo_root / 'Lab' / 'AssetCache'
    spec_root = repo_root / 'Lab' / 'GeneratedContent' / 'species-import'
    source = newest_spec(spec_root)
    if source is None:
        return {'changed': False, 'largos': 0, 'reason': 'no SR2 species spec yet'}
    listed = {e['canonical_id'] for e in json.loads(source.read_text(encoding='utf-8')).get('entries', [])
              if str(e.get('canonical_id', '')).startswith('largo.')}
    wanted = {r['canonical'] for r in cached_rows(cache_root) if r['kind'] == 'largo'}
    if wanted == listed:
        return {'changed': False, 'largos': len(wanted)}
    published = publish_spec(cache_root, spec_root, repo_root / 'Lab' / 'GeneratedContent' / 'largo-ids.json', sr1_dir, sr2_dir)
    return {'changed': True, 'largos': published['largos'], 'directory': published['directory']}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--only', nargs='*')
    parser.add_argument('--sr1-dir', default=str(DEFAULT_SR1_DATA))
    parser.add_argument('--sr2-dir', default=str(DEFAULT_SR2_DATA))
    args = parser.parse_args(argv)
    if args.publish:
        print(json.dumps(publish_spec(sr1_dir=Path(args.sr1_dir), sr2_dir=Path(args.sr2_dir)), indent=2))
        return 0
    rows = roster(Path(args.sr1_dir), Path(args.sr2_dir), args.only)
    if not args.build:
        for row in rows:
            print(f"{row['canonical']:30} {row['prefab']:22} {row['kind']:6} {'cached' if cache_is_complete(row['canonical']) else 'missing'}")
        print(len(rows), 'items SR2 lacks')
        return 0
    result = run(rows, Path(args.sr1_dir), CACHE_ROOT, args.force, progress=lambda d, t, m: print(f'{d}/{t} {m}', flush=True))
    print(json.dumps({k: v for k, v in result.items() if k != 'species'}, indent=2))
    for r in result['species']:
        if r['status'] != 'BUILT' and r['status'] != 'UP_TO_DATE':
            print(r['canonical'], r['status'], r.get('error'))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
