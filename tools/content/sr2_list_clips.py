"""List the source AnimationClips a SR2 species' controller/appearance actually references (read-only).

Used to pick the explicit `--clip-id` / `--default-clip` values that
`bridge.content.sr2_asset_extract --complete-source` requires.

  py -3.12 -m tools.content.sr2_list_clips Cotton [Angler ...]
"""
import sys
from pathlib import Path

from bridge.content.sr2_asset_extract import DEFAULT_SR2_DATA, Sr2Assets
from bridge.content.sr2_appearance import collect_source_appearance, source_object_index
from bridge.content.sr2_source_cache import resolve_species_source


def list_clips(species, data_dir=DEFAULT_SR2_DATA):
    assets = Sr2Assets(Path(data_dir))
    collect_source_appearance(assets, species, [])          # loads the generated TypeTree schemas
    resolved = resolve_species_source(assets, species)
    objects = source_object_index(assets)
    rows = []
    for pid in resolved['clip_ids']:
        rows.append((pid, objects[pid].read_typetree().get('m_Name', '?')))
    return resolved, rows


def main(argv=None):
    species_list = (argv if argv is not None else sys.argv[1:])
    if not species_list:
        print(__doc__)
        return 2
    status = 0
    for species in species_list:
        try:
            resolved, rows = list_clips(species)
        except Exception as ex:  # report and continue with the next species
            print(f'== {species}: {type(ex).__name__}: {ex}')
            status = 1
            continue
        print(f'== {species}: controller={resolved["controller_name"]} overrides={resolved["appearance_overrides"]}')
        for pid, name in rows:
            print(f'   {pid}  {name}')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
