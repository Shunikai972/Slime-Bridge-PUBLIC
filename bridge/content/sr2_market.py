"""SR2 plort market values: the base sale price and the saturation each plort has in Slime Rancher 2.

SR2 keeps them in one lookup asset (`Plort Market Default Values`: a list of {plort type, base value, full saturation}); the plort
types are separate assets, so the rows are resolved through the container paths of the identifiable-types bundle:

    py -3.12 -m bridge.content.sr2_market            # writes Lab/AssetCache/sr2-market-values.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bridge.content.sr2_asset_extract import DEFAULT_SR2_DATA, REPO_ROOT

OUTPUT = REPO_ROOT / 'Lab' / 'AssetCache' / 'sr2-market-values.json'
BUNDLE_DIR = Path('StreamingAssets') / 'aa' / 'StandaloneWindows64'


def _bundle(data_dir: Path, prefix: str) -> Path:
    found = sorted((Path(data_dir) / BUNDLE_DIR).glob(prefix + '*.bundle'))
    if not found:
        raise ValueError(f'SR2 bundle {prefix}* not found')
    return found[0]


def extract_market_values(data_dir: Path = DEFAULT_SR2_DATA) -> dict:
    import UnityPy
    values_env = UnityPy.load(str(_bundle(data_dir, 'scriptableobjectlookupdata_assets_all_')))
    market = next(obj for path, obj in values_env.container.items() if path.lower().endswith('plort market default values.asset'))
    tree = market.read_typetree()
    types_env = UnityPy.load(str(_bundle(data_dir, 'identifiabletypes_assets_')))
    names = {}
    for path, obj in types_env.container.items():
        match = re.search(r'/Actor/Plort/IdentifiableType/(\w+)Plort\.asset$', path)
        if match:
            names[obj.path_id] = match.group(1)
    rows = {}
    for entry in tree['Entries']:
        name = names.get(entry['Type']['m_PathID'])
        if name:
            rows[name.lower()] = {'value': float(entry['Value']), 'full_saturation': float(entry['FullSaturation'])}
    if not rows:
        raise ValueError('no plort market rows could be resolved')
    return {'schema': 'bridge-sr2-market-values', 'schema_version': 1, 'source': 'Plort Market Default Values', 'plorts': rows}


def load_or_extract(data_dir: Path = DEFAULT_SR2_DATA, path: Path = OUTPUT) -> dict:
    path = Path(path)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('schema') == 'bridge-sr2-market-values':
                return data
        except (OSError, ValueError):
            pass
    data = extract_market_values(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def main(argv=None) -> int:
    data = load_or_extract()
    for name, row in sorted(data['plorts'].items(), key=lambda kv: kv[1]['value']):
        print(f"{name:10} value {row['value']:6.1f}  full saturation {row['full_saturation']:8.1f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
