"""SR2 -> SR1 species model pipeline: the one code path behind the launcher's "Extract" button and the CLI.

For every species of the import roster it makes sure the converted-model cache
(`<cache root>/sr2-to-sr1/<canonical>`) is complete and current:

  * model + textures (body/ears sampled with the unique UV atlas, see sr2_asset_extract.albedo_uv_set),
  * the species icon and the SR2 source animation clips (picked automatically from the species' own Animator
    controller / appearance overrides; a clip the decoder refuses is skipped and reported, never faked),
  * the species' own plort look (palette, stripe texture, icon; see plort_assets).

Species the strict "complete-source" reader refuses (e.g. Sloomber) fall back to the mesh-only scope with the same
clips and icon. Existing caches are upgraded in place instead of rebuilt unless `force` is set; caches that carry
extra tool output (the Ringtail stone form) are therefore never discarded by a plain run.

  py -3.12 -m bridge.content.species_pipeline [--species Ringtail ...] [--force] [--cache-root <dir>]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from bridge.content import face_expressions, cache_upgrade, plort_assets
from bridge.content.sr2_asset_extract import (CACHE_ROOT, DEFAULT_SR2_DATA, REPO_ROOT, Sr2Assets, collect_bone_tree,
                                              extract_species)

SPEC_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
WORK_ROOT = REPO_ROOT / 'Lab' / 'AssetCache' / 'pipeline-tmp'
REPLACED_ROOT = REPO_ROOT / 'Lab' / 'AssetCache' / 'pipeline-replaced'
PREFERRED_CLIPS = ('Idle v4', 'Pounce v4', 'Bite v4', 'Digest v4')
MAX_CLIPS = 16
COMPLETE_MARKERS = ('icon-descriptor.json', 'source-animation-provenance.json', 'cache-manifest.json', 'model.json')


# ---------------------------------------------------------------------------------------------------------------- roster
def newest_spec(spec_root: Path = SPEC_ROOT) -> Path | None:
    specs = sorted(Path(spec_root).glob('*/import-spec-SR1.json'))
    return specs[-1] if specs else None


def roster(spec_path: Path | None = None, only: list[str] | None = None) -> list[dict]:
    """READY species of the import spec the plugin registers: [{'canonical', 'species', 'status'}]."""
    spec_path = spec_path or newest_spec()
    if spec_path is None:
        raise ValueError('no species import spec under Lab/GeneratedContent/species-import')
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    rows = []
    for entry in spec.get('entries', []):
        if entry.get('status') != 'READY' or str(entry.get('canonical_id', '')).startswith('largo.'):
            continue                    # Largos are rows of their own (bridge.content.largos.largo_roster)
        species = entry.get('display_name') or entry['canonical_id'].split('.')[-1].capitalize()
        if only and species.lower() not in {o.lower() for o in only}:
            continue
        rows.append({'canonical': entry['canonical_id'], 'species': species, 'status': entry['status']})
    return rows


def cache_is_complete(cache_dir: Path) -> bool:
    return all((Path(cache_dir) / name).is_file() for name in COMPLETE_MARKERS)


# ------------------------------------------------------------------------------------------------------------------ clips
def plan_clips(assets: Sr2Assets, species: str) -> dict:
    """Pick the source clips to bake: the species' idle override, then Idle/Pounce/Bite, then its other overrides.
    Every candidate is trial-decoded; the ones the decoder refuses are reported in `skipped`."""
    from bridge.content.sr2_appearance import collect_source_appearance, source_object_index
    from bridge.content.sr2_source_cache import extract_source_animation, resolve_species_source
    collect_source_appearance(assets, species, [])
    resolved = resolve_species_source(assets, species)
    objects = source_object_index(assets)
    names = {pid: objects[pid].read_typetree().get('m_Name', '') for pid in resolved['clip_ids']}
    by_name = {name: pid for pid, name in names.items()}
    overrides = resolved.get('appearance_overrides', {})
    candidates: list[int] = []
    for pid in [overrides.get('_idleAnimationOverride')] + [by_name.get(n) for n in PREFERRED_CLIPS] + \
            [pid for key, pid in overrides.items() if key != '_idleAnimationOverride']:
        if pid and pid not in candidates:
            candidates.append(pid)
    bones = collect_bone_tree(assets, species)
    usable, skipped = [], []
    for pid in candidates[:MAX_CLIPS]:
        try:
            extract_source_animation(assets, resolved, bones, [pid], names[pid])
            usable.append(pid)
        except Exception as ex:  # noqa: BLE001 - decoder refusals are data, reported per clip
            skipped.append({'id': pid, 'name': names[pid], 'reason': str(ex)[:160]})
    default = names[usable[0]] if usable else None
    return {'clip_ids': usable, 'default_clip': default, 'skipped': skipped, 'names': {p: names[p] for p in usable},
            'resolved': resolved, 'bones': bones}


# ---------------------------------------------------------------------------------------------------------------- build
BUNDLE_SOURCE = 'identifiabletypes_assets_'     # where the Largos (and species) that are not in resources.assets live
_BUNDLE_ASSETS: dict = {}


LEAN_EXTRAS = ('shaders_assets',)
BUNDLE_LEAN_GB = 5.5          # measured: the identifiable-types bundle opened from disk + one extraction
BUNDLE_FULL_GB = 10.5         # + the shared mesh/texture bundle
_RAM_GB: float | None = None  # the launcher's memory limit (None: unlimited), set by run()


class RamLimit(Exception):
    """A row needs more memory than the player's limit allows."""

    def __init__(self, needed_gb: float):
        super().__init__(f'needs about {needed_gb:g} GB of memory (limit {_RAM_GB:g} GB): raise the limit in the launcher')
        self.needed_gb = needed_gb


def _require_ram(needed_gb: float) -> None:
    if _RAM_GB is not None and _RAM_GB < needed_gb:
        raise RamLimit(needed_gb)
FULL_EXTRAS = ('shaders_assets', 'duplicateassetisolation_assets')      # + the shared mesh/texture bundle (~4 GB more)


def _bundle_assets(sr2_dir: Path, extras: tuple = LEAN_EXTRAS) -> Sr2Assets:
    """The identifiable-types bundle (~5 GB in memory) with the bundles its objects point into. One copy per process; asking for
    more bundles replaces the lean copy instead of holding both."""
    key = str(sr2_dir)
    held = _BUNDLE_ASSETS.get(key)
    _require_ram(BUNDLE_FULL_GB if 'duplicateassetisolation_assets' in extras else BUNDLE_LEAN_GB)
    if held is None or not set(extras) <= set(held.extras):
        _BUNDLE_ASSETS.pop(key, None)
        held = None
        import gc
        gc.collect()
        held = _BUNDLE_ASSETS[key] = Sr2Assets(Path(sr2_dir), BUNDLE_SOURCE, tuple(extras))
    return held


def _plan(row: dict, assets: Sr2Assets, sr2_dir: Path):
    """(clips plan, the assets the species is read from, bundle name or None). Largos that resources.assets does not carry
    (the ones of later SR2 updates) are read from the addressables bundle instead."""
    names = [row['species']]
    if row.get('kind') == 'largo' and len(row.get('parents', [])) == 2:
        # the prefab is not always called after the definition (TabbyTwin -> slimeTwinTabby): try the other parent order too
        a, b = [p.capitalize() for p in row['parents']]
        names += [n for n in (a + b, b + a) if n not in names]
    last = None
    for name in names:
        try:
            return plan_clips(assets, name), assets, None, name
        except ValueError as ex:
            last = ex
    if row.get('kind') != 'largo':
        raise last
    bundle = _bundle_assets(sr2_dir)
    for name in names:
        try:
            return plan_clips(bundle, name), bundle, BUNDLE_SOURCE, name
        except ValueError as ex:
            last = ex
    raise last


def _extract(species: str, canonical: str, sr2_dir: Path, work_dir: Path, clips: dict, source_bundle: str | None = None,
             assets: Sr2Assets | None = None) -> tuple[str, dict]:
    """Complete-source first, mesh-only (+ same clips/icon) when the strict reader refuses; last resort mesh-only plain."""
    attempts = []
    if clips['clip_ids']:
        attempts.append(('complete-source', dict(complete_source=True, clip_ids=clips['clip_ids'], default_clip=clips['default_clip'])))
        attempts.append(('mesh-only+clips', dict(appearance_mesh_only=True, clip_ids=clips['clip_ids'], default_clip=clips['default_clip'])))
    attempts.append(('mesh-only', dict(appearance_mesh_only=True)))
    last = None
    for mode, kwargs in attempts:
        target = work_dir / mode.replace('+', '-')
        try:
            result = extract_species(species, canonical, sr2_dir, target, allow_nonlab=True, source_bundle=source_bundle,
                                     assets=assets if source_bundle else None, **kwargs)
            return mode, {'dir': str(target), **result}
        except ValueError as ex:
            last = f'{mode}: {ex}'
    raise ValueError(last or 'no extraction mode succeeded')


def _extract_with_fallback(species, canonical, sr2_dir, work, clips, source_bundle, assets):
    """_extract; a bundle-sourced Largo that points into the shared mesh/texture bundle is retried with that bundle loaded."""
    try:
        return _extract(species, canonical, sr2_dir, work, clips, source_bundle, assets)
    except ValueError as ex:
        if not source_bundle or not any(k in str(ex) for k in ('reference', 'could not be exported', 'could not be resolved')):
            raise
        full = _bundle_assets(sr2_dir, FULL_EXTRAS)
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        return _extract(species, canonical, sr2_dir, work, clips, source_bundle, full)


def _promote(built: Path, cache_root: Path, canonical: str) -> None:
    """Swap the new cache in, keeping the previous one under pipeline-replaced (never silently destroyed)."""
    target = Path(cache_root) / 'sr2-to-sr1' / canonical
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        REPLACED_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        shutil.move(str(target), str(REPLACED_ROOT / f'{canonical}-{stamp}'))
    shutil.move(str(built), str(target))


def add_missing_clips(cache_dir: Path, assets: Sr2Assets, plan: dict) -> list[str]:
    """Append planned clips a complete cache does not have yet (e.g. Digest v4) without rebuilding it: the cache keeps
    everything else it carries (stone form, icon, textures). Re-stamps model.json and the provenance in the manifest."""
    from bridge.content.sr2_source_cache import extract_source_animation
    cache_dir = Path(cache_dir)
    model_path = cache_dir / 'model.json'
    model = json.loads(model_path.read_text(encoding='utf-8'))
    animation = model.get('source_animation')
    if not isinstance(animation, dict):
        return []
    have = {c['name'] for c in animation.get('clips', [])}
    provenance_path = cache_dir / 'source-animation-provenance.json'
    provenance = json.loads(provenance_path.read_text(encoding='utf-8')) if provenance_path.is_file() else None
    added = []
    for pid in plan['clip_ids']:
        name = plan['names'][pid]
        if name in have:
            continue
        try:
            block, prov = extract_source_animation(assets, plan['resolved'], plan['bones'], [pid], name)
        except Exception:  # noqa: BLE001 - a refused clip is simply not added
            continue
        animation['clips'].extend(block['clips'])
        if provenance is not None:
            provenance.setdefault('clips', []).extend(prov['clips'])
            refs = provenance.setdefault('source_references', {}).setdefault('clip_ids', [])
            if pid not in refs:
                refs.append(pid)
        added.append(name)
    if added:
        model_path.write_text(json.dumps(model, separators=(',', ':')), encoding='utf-8')
        names = ['model.json']
        if provenance is not None:
            provenance_path.write_text(json.dumps(provenance, indent=2, allow_nan=False), encoding='utf-8')
            names.append('source-animation-provenance.json')
        cache_upgrade.stamp_files(cache_dir, names)
        receipt = cache_dir / 'dump-receipt.json'
        if receipt.is_file():
            data = json.loads(receipt.read_text(encoding='utf-8'))
            if 'model_sha256' in data:
                data['model_sha256'] = cache_upgrade._sha256(model_path)
                receipt.write_text(json.dumps(data, indent=2), encoding='utf-8')
                cache_upgrade.stamp_files(cache_dir, ['dump-receipt.json'])
    return added


def build_species(row: dict, assets: Sr2Assets, sr2_dir: Path, cache_root: Path, force: bool = False) -> dict:
    species, canonical = row['species'], row['canonical']
    cache_dir = Path(cache_root) / 'sr2-to-sr1' / canonical
    report = {'canonical': canonical, 'species': species, 'notes': []}
    clips, assets, source_bundle, species = _plan(row, assets, sr2_dir)
    if cache_is_complete(cache_dir) and not force:
        upgrade = cache_upgrade.upgrade_albedo_uv(cache_dir)
        added = add_missing_clips(cache_dir, assets, clips)
        report.update(status='UP_TO_DATE', mode='existing', upgrade=upgrade['status'])
        if added:
            report['notes'].append('clips added: ' + ', '.join(added))
    else:
        work = WORK_ROOT / canonical
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            mode, result = _extract_with_fallback(species, canonical, sr2_dir, work, clips, source_bundle, assets)
            _promote(Path(result['dir']), cache_root, canonical)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        report.update(status='BUILT', mode=mode, clips=list(clips['names'].values()), skipped_clips=clips['skipped'],
                      vertices=result.get('vertex_total'))
        if clips['skipped']:
            report['notes'].append('clips skipped: ' + ', '.join(c['name'] for c in clips['skipped']))
    if row.get('kind') != 'largo':             # a Largo makes the plorts of its parents, it has none of its own
        plort = plort_assets.extract_into_cache(species, canonical, assets, cache_root=Path(cache_root) / 'sr2-to-sr1')
        report['plort'] = 'OK' if plort['ok'] else 'MISSING'
        report['notes'] += plort['problems']
    face_file = cache_dir / 'face-expressions.json'
    if force or not face_file.is_file():
        faces = face_expressions.extract_into_cache(species, canonical, assets, cache_root=Path(cache_root) / 'sr2-to-sr1')
        report['faces'] = f"{faces['count']} expressions" if faces['ok'] else 'MISSING'
        report['notes'] += faces['problems']
    else:
        report['faces'] = 'OK'
    return report


def run(rows: list[dict], sr2_dir: Path = DEFAULT_SR2_DATA, cache_root: Path = REPO_ROOT / 'Lab' / 'AssetCache',
        force: bool = False, progress=None, cancelled=None, ram_gb: float | None = None) -> dict:
    """Process every row; `progress(done, total, message)` and `cancelled()` are optional hooks (launcher job)."""
    global _RAM_GB
    sr2_dir = Path(sr2_dir)
    if not (sr2_dir / 'resources.assets').is_file():
        raise ValueError(f'SR2 data folder not found: {sr2_dir}')
    _RAM_GB = float(ram_gb) if ram_gb else None
    try:
        return _run_rows(rows, sr2_dir, cache_root, force, progress, cancelled)
    finally:
        _RAM_GB = None
        _BUNDLE_ASSETS.clear()          # the multi-GB game files are not kept between jobs


def _run_rows(rows, sr2_dir, cache_root, force, progress, cancelled) -> dict:
    assets = Sr2Assets(sr2_dir)
    reports = []
    for index, row in enumerate(rows):
        if cancelled and cancelled():
            raise InterruptedError('cancelled')
        if progress:
            progress(index, len(rows), f"[{row['canonical']}] working")
        try:
            report = build_species(row, assets, sr2_dir, Path(cache_root), force)
        except RamLimit as ex:
            report = {'canonical': row['canonical'], 'species': row['species'], 'status': 'SKIPPED_RAM', 'error': str(ex), 'notes': [str(ex)]}
        except MemoryError:
            report = {'canonical': row['canonical'], 'species': row['species'], 'status': 'SKIPPED_RAM', 'notes': [],
                      'error': f'ran out of memory at the {_RAM_GB:g} GB limit' if _RAM_GB else 'ran out of memory'}
        except Exception as ex:  # noqa: BLE001 - one species failing must not stop the roster
            report = {'canonical': row['canonical'], 'species': row['species'], 'status': 'FAILED',
                      'error': f'{type(ex).__name__}: {ex}'[:300], 'notes': []}
        reports.append(report)
        if progress:
            progress(index + 1, len(rows), f"[{row['canonical']}] {report['status']}" + (f" ({report.get('mode')})" if report.get('mode') else ''))
    tally: dict[str, int] = {}
    for r in reports:
        tally[r['status']] = tally.get(r['status'], 0) + 1
    return {'ok': tally.get('FAILED', 0) == 0, 'count': len(reports), 'by_status': tally, 'species': reports}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--species', nargs='*', help='only these species (display names, e.g. Ringtail Cotton)')
    parser.add_argument('--force', action='store_true', help='rebuild even when the cache is complete')
    parser.add_argument('--sr2-dir', default=str(DEFAULT_SR2_DATA))
    parser.add_argument('--cache-root', default=str(REPO_ROOT / 'Lab' / 'AssetCache'))
    parser.add_argument('--spec', default=None)
    args = parser.parse_args(argv)
    rows = roster(Path(args.spec) if args.spec else None, args.species)
    result = run(rows, Path(args.sr2_dir), Path(args.cache_root), args.force,
                 progress=lambda d, t, m: print(f'{d}/{t} {m}', flush=True))
    print(json.dumps(result, indent=2, default=str))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
