"""In-place upgrades of converted-model caches (Lab/AssetCache/sr2-to-sr1/<canonical>).

Caches written before a converter fix stay usable: each upgrade is idempotent, touches only `model.json`, and
re-stamps the cache manifest (per-file SHA-256) and the dump receipt so the runtime loader still validates them.

  py -3.12 -m bridge.content.cache_upgrade [<cache dir> ...]      (default: every cache under Lab/AssetCache/sr2-to-sr1)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from bridge.content.sr2_asset_extract import CACHE_ROOT, albedo_uv_set


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def upgrade_albedo_uv(cache_dir: Path) -> dict:
    """Add `albedo_uv_set` to every renderer (see sr2_asset_extract.albedo_uv_set). Returns a small report."""
    cache_dir = Path(cache_dir)
    model_path = cache_dir / 'model.json'
    manifest_path = cache_dir / 'cache-manifest.json'
    if not model_path.is_file() or not manifest_path.is_file():
        return {'cache': cache_dir.name, 'status': 'skipped', 'reason': 'no model.json / manifest'}
    model = json.loads(model_path.read_text(encoding='utf-8'))
    changed = 0
    parts = []
    for key in ('skinned_renderers', 'static_renderers'):
        for renderer in model.get(key, []):
            wanted = albedo_uv_set(renderer.get('role', ''), renderer, renderer.get('materials', []))
            if renderer.get('albedo_uv_set') != wanted:
                renderer['albedo_uv_set'] = wanted
                changed += 1
            parts.append(f"{renderer.get('role')}={wanted}")
    if not changed:
        return {'cache': cache_dir.name, 'status': 'current', 'parts': parts}
    model_path.write_text(json.dumps(model, separators=(',', ':')), encoding='utf-8')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['files']['model.json'] = _sha256(model_path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    receipt_path = cache_dir / 'dump-receipt.json'
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if 'model_sha256' in receipt:
            receipt['model_sha256'] = _sha256(model_path)
            receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
            manifest['files']['dump-receipt.json'] = _sha256(receipt_path)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return {'cache': cache_dir.name, 'status': 'upgraded', 'renderers_changed': changed, 'parts': parts}


def stamp_files(cache_dir: Path, names: list[str]) -> None:
    """Add (or refresh) files in the cache manifest so the runtime loader's per-file hash check covers them."""
    cache_dir = Path(cache_dir)
    manifest_path = cache_dir / 'cache-manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for name in names:
        path = cache_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        manifest['files'][name] = _sha256(path)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')


def upgrade_all(root: Path = CACHE_ROOT) -> list[dict]:
    reports = []
    for cache in sorted(p for p in Path(root).iterdir() if p.is_dir() and p.name.startswith(('slime.', 'largo.'))):
        reports.append(upgrade_albedo_uv(cache))
    return reports


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    reports = [upgrade_albedo_uv(Path(a)) for a in args] if args else upgrade_all()
    print(json.dumps(reports, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
