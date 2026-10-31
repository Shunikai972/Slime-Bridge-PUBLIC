"""Finished largo models installed from the launcher (Largos page, "Download largos"): both games play them.

A downloaded model is `<models_dir>/<id>/largo.json` + `cache/` (a bridge model cache made by tools/largo_models from the
author's GLB). `sync` copies each cache to where the two plugins look: `Lab/AssetCache/sr2-to-sr1/<canonical>` (Slime Rancher)
and `Lab/AssetCache/sr1-to-sr2/<canonical>` (Slime Rancher 2). An extracted cache of the same Largo is kept aside as
`<canonical>-native` and put back when the model is removed. The species specs then list them like any imported Largo: diet
and favourite foods = both parents', both parents' plorts, the native Largo rules of each game. On the Slime Rancher side the
template is the family's own Pink Largo (Rad, Quantum, Mosaic), on the Slime Rancher 2 side the partner's (Pink Cotton,
Pink Angler ...), so each game's own behaviour of that family comes with it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

SOURCE_GAME = 'USER'
DIRECTIONS = ('sr2-to-sr1', 'sr1-to-sr2')
SR1_TEMPLATE = {'rad': 'PINK_RAD_LARGO', 'quantum': 'PINK_QUANTUM_LARGO', 'mosaic': 'PINK_MOSAIC_LARGO'}
# Slime Rancher 2 side: the Slime Rancher 2 partner's own Pink Largo (its behaviour comes with it; a Boom template exploded)
SR2_TEMPLATE = {'angler': 'SlimeDefinition.AnglerPink', 'batty': 'SlimeDefinition.BattyPink', 'cotton': 'SlimeDefinition.CottonPink',
                'flutter': 'SlimeDefinition.FlutterPink', 'hyper': 'SlimeDefinition.HyperPink', 'ringtail': 'SlimeDefinition.RingtailPink',
                'sloomber': 'SlimeDefinition.PinkSloomber', 'twin': 'SlimeDefinition.PinkTwin'}


def _json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def is_user_cache(folder: Path) -> bool:
    manifest = _json(Path(folder) / 'cache-manifest.json')
    return bool(manifest) and manifest.get('source_game') == SOURCE_GAME


def installed_models(models_dir) -> list[dict]:
    """Models present in the launcher's models folder (a largo.json + a complete cache)."""
    out = []
    root = Path(models_dir) if models_dir else None
    if root is None or not root.is_dir():
        return out
    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith('.')):
        meta = _json(folder / 'largo.json')
        cache = folder / 'cache'
        if not isinstance(meta, dict) or not str(meta.get('canonical', '')).startswith('largo.'):
            continue
        if not ((cache / 'cache-manifest.json').is_file() and (cache / 'model.json').is_file()):
            continue
        out.append({**meta, 'cache': str(cache)})
    return out


SOURCE_MARK = '.sb-source-manifest'
# how big a Largo is in each game, as each loader reads prefab_scale: Slime Rancher scales the visual by the actor's own
# Largo scale on top (1.0 = Largo size), Slime Rancher 2 divides the actor's scale out (2.0 = Largo size)
REALM_SCALE = {'sr2-to-sr1': 1.0, 'sr1-to-sr2': 2.0}


def _same(source: Path, target: Path) -> bool:
    try:
        return (source / 'cache-manifest.json').read_bytes() == (target / SOURCE_MARK).read_bytes()
    except OSError:
        return False


def _adapt(target: Path, direction: str):
    """The game's Largo scale in model.json, the manifest re-hashed; the source manifest is kept to detect updates."""
    import hashlib
    (target / SOURCE_MARK).write_bytes((target / 'cache-manifest.json').read_bytes())
    model_path = target / 'model.json'
    model = json.loads(model_path.read_text(encoding='utf-8'))
    if model.get('prefab_scale') == REALM_SCALE[direction]:
        return
    model['prefab_scale'] = REALM_SCALE[direction]
    model_path.write_text(json.dumps(model, separators=(',', ':')), encoding='utf-8')
    manifest_path = target / 'cache-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['model.json'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, separators=(',', ':')), encoding='utf-8')


def sync(models_dir, cache_root) -> dict:
    """Make the game caches match the installed models. Idempotent; returns {'installed': [...], 'removed': [...]}."""
    cache_root = Path(cache_root)
    models = installed_models(models_dir)
    wanted = {m['canonical']: Path(m['cache']) for m in models}
    installed, removed = [], []
    for direction in DIRECTIONS:
        base = cache_root / direction
        base.mkdir(parents=True, exist_ok=True)
        for canonical, source in wanted.items():
            target = base / canonical
            if target.is_dir() and is_user_cache(target) and _same(source, target):
                continue
            if target.is_dir() and not is_user_cache(target):
                aside = base / f'{canonical}-native'
                if aside.exists():
                    shutil.rmtree(aside)
                target.rename(aside)
            staging = base / f'.{canonical}-incoming'
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source, staging)
            _adapt(staging, direction)
            if target.exists():
                shutil.rmtree(target)
            staging.rename(target)
            installed.append(f'{direction}/{canonical}')
        for folder in list(base.iterdir()):
            if folder.is_dir() and folder.name.startswith('largo.') and '-' not in folder.name and folder.name not in wanted and is_user_cache(folder):
                shutil.rmtree(folder)
                aside = base / f'{folder.name}-native'
                if aside.is_dir():
                    aside.rename(folder)
                removed.append(f'{direction}/{folder.name}')
    return {'installed': installed, 'removed': removed, 'models': len(models)}


def user_canonicals(cache_root, direction: str) -> list[str]:
    base = Path(cache_root) / direction
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir() and p.name.startswith('largo.') and '-' not in p.name and is_user_cache(p))


def palette(cache_dir) -> dict | None:
    """The spec's appearance block, from the colours the adapter measured on the body texture."""
    model = _json(Path(cache_dir) / 'model.json')
    if not isinstance(model, dict):
        return None
    for part in model.get('skinned_renderers', []):
        if part.get('role') != 'body':
            continue
        for material in part.get('materials', []):
            colors = material.get('colors', {})
            if all(len(colors.get(k, [])) == 4 for k in ('_TopColor', '_MiddleColor', '_BottomColor')):
                top, middle, bottom = (list(colors[k]) for k in ('_TopColor', '_MiddleColor', '_BottomColor'))
                return {'palette': {'top': top, 'middle': middle, 'bottom': bottom}, 'ammo': list(middle), 'save_set': '', 'source': 'user-model'}
    return None


def row(canonical: str) -> dict:
    parents = canonical.split('.')[1:]
    species = ''.join(p.capitalize() for p in parents)
    return {'canonical': canonical, 'species': species, 'kind': 'largo', 'parents': parents,
            'display_name': ' '.join(p.capitalize() for p in parents) + ' Largo'}


def family(canonical: str) -> str | None:
    return next((p for p in canonical.split('.')[1:] if p in SR1_TEMPLATE), None)


def partner(canonical: str) -> str | None:
    return next((p for p in canonical.split('.')[1:] if p in SR2_TEMPLATE), None)


def adapt_sr1_entry(entry: dict, cache_root) -> dict:
    """A Slime Rancher spec entry of a user model: the family's Pink Largo as template, the measured palette."""
    canonical = entry['canonical_id']
    fam = family(canonical)
    if fam:
        entry['template']['slime_native_ref'] = SR1_TEMPLATE[fam]
    look = palette(Path(cache_root) / 'sr2-to-sr1' / canonical)
    if look:
        entry['appearance'] = look
    entry.setdefault('flags', {})['visual_mode'] = 'imported-model'
    entry.setdefault('notes', []).append('user-model')
    return entry
