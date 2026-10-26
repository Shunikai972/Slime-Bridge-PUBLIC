"""Curated diets for the species whose source diet cannot be translated automatically.

The import spec is built from captured diets; three SR2 species have none that SR1 can use:

* Flutter eats Nectar only (Moondew Nectar is its favourite). SR1 has no nectar food group, so the closest SR1 group is used: FRUIT.
* Shadow and Yolky have no diet in SR2 at all (Yolky's plorts come from the Hen Hen, a Shadow slime drops plorts when it is hit with
  food). They are imported as non-feeding species: an empty diet, which a Largo made with them simply inherits from its other parent.

Sources: the Slime Rancher wiki (wiki.gg) pages of the three species and the in-game Slimepedia text. `apply` is used by the spec
builder and by `publish` below, which writes a new base spec directory next to the existing ones.
"""

import argparse
import json
import re
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / 'Lab' / 'GeneratedContent' / 'species-import'
SPEC_FILE = 'import-spec-{realm}.json'

OVERRIDES = {
    'SR1': {
        'slime.flutter': {'groups': ['FRUIT'], 'why': 'SR2 diet is Nectar (Moondew Nectar favourite); SR1 has no nectar group, FRUIT is the closest'},
        'slime.shadow': {'groups': [], 'why': 'SR2 lists no diet for the Shadow slime: imported as non-feeding'},
        'slime.yolky': {'groups': [], 'why': 'SR2 lists no diet for the Yolky slime (plorts come from the Hen Hen): imported as non-feeding'},
    },
    'SR2': {},
}

# Blockers that only say "the diet could not be translated" (and the informational Largo note): the override answers them.
DIET_BLOCKERS = (
    re.compile(r'^no target-native food group'),
    re.compile(r'^source diet has no captured food groups'),
    re.compile(r'^no SR2 diet mapping'),
    re.compile(r'^diet groups could not be mapped'),
    re.compile(r'^can_largofy source='),
)


def apply(entries: list[dict], realm: str) -> list[str]:
    """Give the curated diet to the matching entries and mark them READY when nothing else blocks them; returns what changed."""
    changed = []
    table = OVERRIDES.get(realm, {})
    for entry in entries:
        rule = table.get(entry.get('canonical_id'))
        if rule is None or entry.get('status') == 'READY' and entry.get('diet', {}).get('major_food_groups') == rule['groups']:
            continue
        diet = entry.setdefault('diet', {})
        diet['major_food_groups'] = list(rule['groups'])
        remaining = [b for b in entry.get('blockers', []) if not any(p.match(b) for p in DIET_BLOCKERS)]
        entry['blockers'] = [b for b in entry.get('blockers', []) if any(p.match(b) for p in DIET_BLOCKERS[-1:])] + remaining
        entry['status'] = 'READY' if not remaining else 'PENDING_DATA'
        notes = entry.setdefault('notes', [])
        notes.append('curated diet: ' + rule['why'])
        changed.append(entry['canonical_id'])
    return changed


def _newest(spec_root: Path, realm: str, skip_suffix: str | None = None) -> Path | None:
    found = [p for p in sorted(Path(spec_root).glob('*/' + SPEC_FILE.format(realm=realm)))
             if not (skip_suffix and p.parent.name.endswith(skip_suffix))]
    return found[-1] if found else None


def publish(spec_root: Path = SPEC_ROOT) -> dict:
    """Write `<stamp>-diets/`: the newest species spec of each realm with the curated diets applied.

    The SR1 side starts from the newest spec that is not a Largo extension (Largos are appended again by the Largo publisher); the
    name sorts after the others, so everything that picks "the newest spec" uses it."""
    spec_root = Path(spec_root)
    out_dir = spec_root / f"{time.strftime('%Y%m%d-%H%M%S')}-diets"
    changed = {}
    sources = {}
    for realm in ('SR1', 'SR2'):
        source = _newest(spec_root, realm, '-largos')
        if source is None:
            continue
        spec = json.loads(source.read_text(encoding='utf-8'))
        changed[realm] = apply(spec['entries'], realm)
        sources[realm] = source
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / source.name).write_text(json.dumps(spec, indent=2), encoding='utf-8')
    if not sources:
        raise ValueError('no species import spec to extend')
    return {'ok': True, 'directory': str(out_dir), 'changed': changed, 'sources': {k: str(v) for k, v in sources.items()}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--publish', action='store_true', help='write a new base spec directory with the curated diets')
    parser.parse_args(argv)
    print(json.dumps(publish(), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
