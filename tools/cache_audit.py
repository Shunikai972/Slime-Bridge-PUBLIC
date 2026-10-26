"""Deep sanity audit of every extracted model cache (read-only): the checks a bad extraction would trip long before the game does.

    py -3.12 -m tools.cache_audit [--root Lab/AssetCache] [--only largo.]

Per cache: manifest hashes, finite numbers, triangle and bone indices in range, bind poses and skin bones agreeing, material slots covering
the submeshes, every referenced texture present and decodable, an icon descriptor with an existing image. Exit code 1 when anything fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIRECTIONS = ('sr2-to-sr1', 'sr1-to-sr2')


def finite(values) -> bool:
    for v in values:
        if isinstance(v, (list, tuple)):
            if not finite(v):
                return False
        elif isinstance(v, float) and not math.isfinite(v):
            return False
    return True


def audit_cache(folder: Path) -> list[str]:
    problems: list[str] = []
    try:
        manifest = json.loads((folder / 'cache-manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [f'manifest unreadable: {exc}']
    files = manifest.get('files', {})
    for name, expected in files.items():
        path = folder / name
        if not path.is_file():
            problems.append(f'manifest file missing: {name}')
        elif hashlib.sha256(path.read_bytes()).hexdigest().lower() != str(expected).lower():
            problems.append(f'hash mismatch: {name}')
    try:
        model = json.loads((folder / 'model.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return problems + [f'model unreadable: {exc}']
    if model.get('schema') != 'bridge-native-mesh':
        problems.append('model schema is not bridge-native-mesh')
    for group in ('skinned_renderers', 'static_renderers'):
        for index, part in enumerate(model.get(group) or []):
            tag = f"{group}[{index}] {part.get('role')}"
            vertices = part.get('vertices') or []
            count = len(vertices)
            if count == 0:
                problems.append(f'{tag}: no vertices')
                continue
            if not finite([vertices, part.get('normals') or [], part.get('uv') or [], part.get('tangents') or []]):
                problems.append(f'{tag}: non-finite vertex data')
            for key in ('normals', 'uv', 'tangents', 'colors', 'uv2'):
                size = len(part.get(key) or [])
                if size and size != count:
                    problems.append(f'{tag}: {key} has {size} rows for {count} vertices')
            submeshes = part.get('triangles') or []
            for s, rows in enumerate(submeshes):
                flat = [i for row in rows for i in (row if isinstance(row, (list, tuple)) else [row])]
                if flat and (min(flat) < 0 or max(flat) >= count):
                    problems.append(f'{tag}: submesh {s} indexes outside the {count} vertices')
                if len(flat) % 3:
                    problems.append(f'{tag}: submesh {s} is not whole triangles')
            if not submeshes:
                problems.append(f'{tag}: no triangles')
            materials = part.get('materials') or []
            if len(materials) < max(1, len(submeshes)) and not (materials and len(submeshes) > len(materials)):
                problems.append(f'{tag}: {len(materials)} material slots for {len(submeshes)} submeshes')
            for slot, material in enumerate(materials):
                for texture in material.get('textures') or []:
                    name = texture.get('file')
                    if name not in files:
                        problems.append(f'{tag}: material {slot} texture {name} is not in the manifest')
            if group == 'skinned_renderers':
                skin = part.get('skin_bones') or []
                if len(part.get('bind_poses') or []) != len(skin):
                    problems.append(f'{tag}: {len(part.get("bind_poses") or [])} bind poses for {len(skin)} skin bones')
                weights = part.get('bone_weights') or []
                if len(weights) != count:
                    problems.append(f'{tag}: {len(weights)} bone weight rows for {count} vertices')
                else:
                    for row in weights:
                        w, b = row[:4], row[4:8]
                        if any(x < -1e-6 for x in w) or abs(sum(w) - 1) > 1e-2 and sum(w) > 1e-6:
                            problems.append(f'{tag}: bone weights do not sum to 1')
                            break
                        if any(w[i] > 0 and not 0 <= b[i] < len(skin) for i in range(4)):
                            problems.append(f'{tag}: bone index outside the skin bones')
                            break
                bones = {bone['name'] for bone in (model.get('skeleton') or {}).get('bones', [])}
                missing = [name for name in skin if name not in bones]
                if missing:
                    problems.append(f'{tag}: skin bones missing from the skeleton: {missing[:3]}')
                if part.get('root_bone') not in bones:
                    problems.append(f'{tag}: root bone {part.get("root_bone")} is not in the skeleton')
    if not any(p.get('enabled') for p in (model.get('skinned_renderers') or []) + (model.get('static_renderers') or [])):
        problems.append('no enabled renderer')
    icon = folder / 'icon-descriptor.json'
    if icon.is_file():
        try:
            descriptor = json.loads(icon.read_text(encoding='utf-8'))
            image = descriptor.get('file')
            if image and not (folder / image).is_file():
                problems.append(f'icon image missing: {image}')
        except (OSError, ValueError):
            problems.append('icon descriptor unreadable')
    elif model.get('source_game') == 'SR2':
        problems.append('no icon descriptor')
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--root', default=str(REPO_ROOT / 'Lab' / 'AssetCache'))
    parser.add_argument('--only', default='')
    args = parser.parse_args(argv)
    bad = total = 0
    for direction in DIRECTIONS:
        base = Path(args.root) / direction
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir() and p.name.startswith(args.only) and '-' not in p.name):   # '-backup' / '-v3' folders are hand-kept variants
            if not (folder / 'cache-manifest.json').is_file():
                continue
            total += 1
            problems = audit_cache(folder)
            if problems:
                bad += 1
                print(f'FAIL {direction}/{folder.name}')
                for problem in problems[:6]:
                    print('   ', problem)
    print(f'{total} caches audited, {bad} with problems')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
