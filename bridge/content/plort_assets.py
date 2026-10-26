"""SR2 plort visuals for the converted-model cache: palette, optional stripe texture and the plort icon.

In SR2 every plort shares one mesh (`plort`) and the `SR/AMP/Slime/Plort` shader; what makes a plort the species' own
is the material `plort<Species>Base` (`_TopColor/_MiddleColor/_BottomColor`, sometimes a `_StripeTexture`) and the
sprite `iconPlort<Species>`. SR1's plort shader (`SR/Slime/Plort`) uses the same colour properties, so the runtime only
needs these values to stop imported plorts from looking like the Pink plort they are cloned from.

Writes `plort.json` (+ PNG files) next to the species' model cache and stamps them into its manifest:
  py -3.12 -m bridge.content.plort_assets Ringtail slime.ringtail
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bridge.content.cache_upgrade import stamp_files
from bridge.content.sr2_asset_extract import (CACHE_ROOT, DEFAULT_SR2_DATA, Sr2Assets, TextureExporter,
                                              material_colors)

PLORT_SCHEMA = 'bridge-plort'


def _pick(colors: dict, key: str, fallback: str):
    return colors.get(key) or colors.get(fallback)


def extract_plort(assets: Sr2Assets, species: str, out_dir: Path) -> dict:
    """Write plort.json (+ icon / stripe PNGs) into `out_dir`. Returns {'ok', 'files', 'problems'}."""
    out_dir = Path(out_dir)
    problems: list[str] = []
    _, material = assets.by_name('Material', f'plort{species}Base')
    if material is None:
        return {'ok': False, 'files': [], 'problems': [f'no SR2 material plort{species}Base']}
    colors = material_colors(material)
    top, middle, bottom = (_pick(colors, '_TopColor', '_MiddleColor'), _pick(colors, '_MiddleColor', '_TopColor'),
                           _pick(colors, '_BottomColor', '_MiddleColor'))
    if not (top and middle and bottom):
        return {'ok': False, 'files': [], 'problems': ['plort material has no top/middle/bottom colours']}
    exporter = TextureExporter(assets, out_dir)
    files: list[str] = []
    document = {
        'schema': PLORT_SCHEMA, 'schema_version': 1, 'species': species,
        'source_material': f'plort{species}Base',
        'palette': {'top': top, 'middle': middle, 'bottom': bottom},
        'icon': None, 'stripe_texture': None,
    }
    # the plort's own sale price in SR2 (base value + how many sales saturate the market)
    try:
        from bridge.content import sr2_market
        rows = sr2_market.load_or_extract()['plorts']
        row = rows.get(species.lower())
        if row:
            # pink_value: SR2's own Pink price, so the plugin can keep the price *relative to Pink* in SR1's economy
            document['market'] = {'value': row['value'], 'full_saturation': row['full_saturation'],
                                  'pink_value': (rows.get('pink') or {}).get('value')}
        else:
            problems.append('no SR2 market value for this plort')
    except Exception as ex:  # noqa: BLE001 - a missing price must never lose the palette
        problems.append(f'market value skipped: {type(ex).__name__}')
    # optional stripe texture (the Ringtail plort carries one)
    try:
        objects = {o.path_id: o for o in assets._objects}
        for key, env in material.m_SavedProperties.m_TexEnvs:
            if key == '_StripeTexture' and env.m_Texture.path_id in objects:
                name = exporter.export_source(objects[env.m_Texture.path_id])
                document['stripe_texture'] = name
                files.append(name)
    except Exception as ex:  # noqa: BLE001 - a missing stripe must never lose the palette
        problems.append(f'stripe texture skipped: {type(ex).__name__}')
    icon_name = exporter.export(f'iconPlort{species}')
    if icon_name:
        document['icon'] = icon_name
        files.append(icon_name)
    else:
        problems.append(f'no SR2 sprite texture iconPlort{species}')
    (out_dir / 'plort.json').write_text(json.dumps(document, indent=2), encoding='utf-8')
    files.append('plort.json')
    return {'ok': True, 'files': files, 'problems': problems, 'palette': document['palette']}


def extract_into_cache(species: str, canonical: str, assets: Sr2Assets | None = None,
                       cache_root: Path = CACHE_ROOT) -> dict:
    cache_dir = Path(cache_root) / canonical
    if not (cache_dir / 'cache-manifest.json').is_file():
        return {'ok': False, 'files': [], 'problems': [f'no model cache for {canonical}']}
    assets = assets or Sr2Assets(Path(DEFAULT_SR2_DATA))
    result = extract_plort(assets, species, cache_dir)
    if result['ok']:
        stamp_files(cache_dir, result['files'])
    return result


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__)
        return 2
    print(json.dumps(extract_into_cache(args[0], args[1]), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
