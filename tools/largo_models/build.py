"""Adapt the finished largo models for both games and package them for the launcher's "Download largos" button.

    py -3.12 -m tools.largo_models.build --models "D:/.../modele finaux 1" --out Lab/LargoModels [--only rad_twin,quantum_hyper]
    py -3.12 -m tools.largo_models.build --out Lab/LargoModels --package --base-url https://github.com/<owner>/<repo>/releases/download/<tag>

Step 1 (default): every model of ETAT_DES_24_SLIMES.json goes through Blender (glb_to_cache.py) into <out>/caches/<canonical>/;
the source GLBs are only read. Step 2 (--package): one zip per model (cache/ + largo.json) and largo-models.json, the
slimebridge-largo-models v1 manifest the launcher reads (url, sha256, size, version per model).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLENDER = Path(os.environ.get('SLIMEBRIDGE_BLENDER', r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe'))
SCRIPT = Path(__file__).with_name('glb_to_cache.py')
VERSION = '1.0.0'
# model folder name -> slug the games use for that species
SLUG = {'slumber': 'sloomber'}
DISPLAY = {'rad': 'Rad', 'quantum': 'Quantum', 'mosaic': 'Mosaic', 'slumber': 'Sloomber', 'cotton': 'Cotton', 'flutter': 'Flutter',
           'angler': 'Angler', 'batty': 'Batty', 'ringtail': 'Ringtail', 'hyper': 'Hyper', 'twin': 'Twin'}
# measured on the 4-side renders: these two face +X in their GLB
YAW = {'rad_twin': -90.0, 'quantum_twin': -90.0}
# the author validated all 24 on 2026-10-31 ("les 24 modeles sont bons"), after the state file was written
APPROVED_LATER = {'mosaic_hyper'}


def canonical(model_id: str) -> str:
    a, b = model_id.split('_', 1)
    return 'largo.' + '.'.join(sorted(SLUG.get(x, x) for x in (a, b)))


def describe(model_id: str) -> dict:
    a, b = model_id.split('_', 1)
    return {'id': model_id, 'canonical': canonical(model_id), 'parents': sorted(SLUG.get(x, x) for x in (a, b)),
            'display_name': f'{DISPLAY[a]} {DISPLAY[b]} Largo', 'family': a, 'partner': b}


def models_from_state(models_root: Path) -> list[dict]:
    state = json.loads((models_root / 'ETAT_DES_24_SLIMES.json').read_text(encoding='utf-8'))
    rows = []
    for m in state['modeles']:
        rows.append({**describe(m['model']), 'glb': m['model_glb'], 'glb_sha256': m.get('model_glb_sha256'),
                     'approved': bool(m.get('user_appearance_approved')) or m['model'] in APPROVED_LATER})
    return rows


def convert(row: dict, out: Path, tris: int, tex: int) -> dict:
    target = out / 'caches' / row['canonical']
    if target.exists():
        shutil.rmtree(target)
    cmd = [str(BLENDER), '-b', '--factory-startup', '--python', str(SCRIPT), '--', row['glb'], str(target), row['canonical'],
           '--tris', str(tris), '--tex', str(tex), '--yaw', str(YAW.get(row['id'], 0.0))]
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=3600)
    line = next((l for l in proc.stdout.splitlines() if l.startswith('SB_ADAPTER_OK')), None)
    if proc.returncode != 0 or line is None:
        tail = (proc.stdout + proc.stderr)[-1500:]
        return {'id': row['id'], 'ok': False, 'error': tail}
    from tools.largo_models import postpass
    extra = postpass.run(target)
    return {'id': row['id'], 'ok': True, 'seconds': round(time.time() - started), **json.loads(line.split(' ', 1)[1]), 'postpass': extra}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def package(out: Path, rows: list[dict], base_url: str) -> dict:
    dist = out / 'dist'
    dist.mkdir(parents=True, exist_ok=True)
    entries = []
    for row in rows:
        cache = out / 'caches' / row['canonical']
        if not (cache / 'cache-manifest.json').is_file():
            continue
        archive = dist / f'largo-{row["id"]}.zip'
        meta = {k: row[k] for k in ('id', 'canonical', 'parents', 'display_name', 'family', 'partner', 'approved')}
        meta['version'] = VERSION
        meta['source_glb_sha256'] = row.get('glb_sha256')
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('largo.json', json.dumps(meta, indent=1))
            for f in sorted(cache.iterdir()):
                zf.write(f, 'cache/' + f.name)
        entries.append({'id': row['id'], 'url': f'{base_url.rstrip("/")}/{archive.name}', 'sha256': sha256(archive),
                        'size': archive.stat().st_size, 'version': VERSION})
    manifest = {'schema': 'slimebridge-largo-models', 'schema_version': 1, 'name': 'SlimeBridge largos', 'updated': time.strftime('%Y-%m-%d'),
                'models': entries}
    (dist / 'largo-models.json').write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    return {'packaged': len(entries), 'dist': str(dist)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', default=r'`<local folder>` rancherlargo\modele finaux 1')
    ap.add_argument('--out', default=str(ROOT / 'Lab' / 'LargoModels'))
    ap.add_argument('--only', default='')
    ap.add_argument('--tris', type=int, default=14000)
    ap.add_argument('--tex', type=int, default=1024)
    ap.add_argument('--package', action='store_true')
    ap.add_argument('--base-url', default='')
    args = ap.parse_args(argv)
    out = Path(args.out)
    rows = models_from_state(Path(args.models))
    if args.only:
        wanted = set(args.only.split(','))
        rows = [r for r in rows if r['id'] in wanted]
    if args.package:
        print(json.dumps(package(out, rows, args.base_url)))
        return 0
    results = []
    for row in rows:
        result = convert(row, out, args.tris, args.tex)
        results.append(result)
        print(json.dumps(result)[:600], flush=True)
    (out / 'build-report.json').write_text(json.dumps(results, indent=1), encoding='utf-8')
    return 0 if all(r['ok'] for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
