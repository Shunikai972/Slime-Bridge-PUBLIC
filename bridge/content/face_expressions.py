"""SR2 face expressions for the converted-model cache.

SR1 drives a slime's face through `SlimeFaceAnimator` -> `SlimeAppearanceApplicator.SetExpression(expression)` (scared,
hungry, alarm, awe, blink, chomp, wince, ...). SR2's `SlimeExpression` enum starts with the same 21 values in the same order
(NONE=0 .. WINCE=20) and adds MISCHIEVOUS, DAZED, ROARING, TIRED, YAWN, ASLEEP, so an SR1 expression id is the SR2 id.
Each species' SR2 `SlimeFace.ExpressionFaces` row names an Eyes and a Mouth material whose `_FaceAtlas` texture IS the
expression; this exports those textures and writes `face-expressions.json` next to the species model, stamped into the manifest:

  {"schema": "bridge-face-expressions", "expressions": {"22": {"name": "SCARED", "eyes": {"atlas": "tex-....png", "scale": [1,1], "offset": [0,0]}, "mouth": {...}}}}

  py -3.12 -m bridge.content.face_expressions Ringtail slime.ringtail
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from bridge.content.cache_upgrade import stamp_files
from bridge.content.sr2_asset_extract import CACHE_ROOT, DEFAULT_SR2_DATA, Sr2Assets, TextureExporter

SCHEMA = 'bridge-face-expressions'
EXPRESSION_NAMES = ['NONE', 'ALARM', 'ANGRY', 'ATTACK_TELEGRAPH', 'AWE', 'BLINK', 'BLUSH', 'BLUSH_BLINK', 'CHOMP_CLOSED',
                    'CHOMP_OPEN', 'ELATED', 'FERAL', 'FRIED', 'GLITCH', 'GRIMACE', 'HAPPY', 'HUNGRY', 'INVOKE', 'SCARED',
                    'STARVING', 'WINCE', 'MISCHIEVOUS', 'DAZED', 'ROARING', 'TIRED', 'YAWN', 'ASLEEP']


def _atlas(assets, objects, exporter, pointer):
    """{'atlas', 'scale', 'offset'} of a face material's `_FaceAtlas`, or None."""
    from bridge.content.sr2_appearance import source_ref
    pid = source_ref(pointer)
    if not pid or pid not in objects or objects[pid].type.name != 'Material':
        return None
    material = objects[pid].read()
    for key, env in material.m_SavedProperties.m_TexEnvs:
        if key != '_FaceAtlas' or not env.m_Texture.path_id or env.m_Texture.path_id not in objects:
            continue
        return {'atlas': exporter.export_source(objects[env.m_Texture.path_id]),
                'scale': [float(env.m_Scale.x), float(env.m_Scale.y)], 'offset': [float(env.m_Offset.x), float(env.m_Offset.y)]}
    return None


def extract_face_expressions(assets: Sr2Assets, species: str, out_dir: Path) -> dict:
    """Write face-expressions.json (+ atlas PNGs) into `out_dir`. Returns {'ok', 'files', 'count', 'problems'}."""
    from bridge.content.sr2_appearance import collect_source_appearance, source_object_index, source_ref
    from bridge.content.sr2_source_cache import resolve_species_source
    out_dir = Path(out_dir)
    collect_source_appearance(assets, species, [])               # loads the generated TypeTree schemas
    resolved = resolve_species_source(assets, species)
    objects = source_object_index(assets)
    appearance = objects[resolved['appearance_id']].read_typetree()
    face_id = source_ref(appearance.get('_face'))
    if not face_id or face_id not in objects:
        return {'ok': False, 'files': [], 'count': 0, 'problems': ['species appearance has no face']}
    face = objects[face_id].read_typetree()
    exporter = TextureExporter(assets, out_dir)
    expressions, problems = {}, []
    for row in face.get('ExpressionFaces', []):
        value = row.get('SlimeExpression')
        if type(value) is not int or value <= 0:
            continue
        entry = {'name': EXPRESSION_NAMES[value] if value < len(EXPRESSION_NAMES) else str(value)}
        for role in ('Eyes', 'Mouth'):
            try:
                found = _atlas(assets, objects, exporter, row.get(role))
            except Exception as ex:  # noqa: BLE001 - one unreadable material must not lose the other expressions
                found = None
                problems.append(f'{entry["name"]}.{role.lower()}: {type(ex).__name__}')
            if found:
                entry[role.lower()] = found
        if 'eyes' in entry or 'mouth' in entry:
            expressions[str(value)] = entry
    if not expressions:
        return {'ok': False, 'files': [], 'count': 0, 'problems': problems or ['no expression faces']}
    (out_dir / 'face-expressions.json').write_text(json.dumps({'schema': SCHEMA, 'schema_version': 1, 'species': species,
                                                              'expressions': expressions}, indent=2), encoding='utf-8')
    files = sorted({*(e[r]['atlas'] for e in expressions.values() for r in ('eyes', 'mouth') if r in e), 'face-expressions.json'})
    return {'ok': True, 'files': files, 'count': len(expressions), 'problems': problems}


def extract_into_cache(species: str, canonical: str, assets: Sr2Assets | None = None, cache_root: Path = CACHE_ROOT) -> dict:
    cache_dir = Path(cache_root) / canonical
    if not (cache_dir / 'cache-manifest.json').is_file():
        return {'ok': False, 'files': [], 'count': 0, 'problems': [f'no model cache for {canonical}']}
    assets = assets or Sr2Assets(Path(DEFAULT_SR2_DATA))
    result = extract_face_expressions(assets, species, cache_dir)
    if result['ok']:
        stamp_files(cache_dir, [f for f in result['files'] if (cache_dir / f).is_file()])
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
