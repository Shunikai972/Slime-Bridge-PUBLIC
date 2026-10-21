"""Parity check: the compiled C# core (csharp/SbRuntime.Tests) must read runtime packages to the same numbers as Python.
Usage: python tests/check_csharp_parity.py <dotnet.exe> <SbRuntime.Tests.dll> <package dir> [...]
Not part of unittest discovery (needs the .NET SDK and built packages)."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sbconv.ir.store import unpack_mesh  # noqa: E402


def python_numbers(pkg):
    pkg = Path(pkg)
    parts = json.loads((pkg / 'parts.json').read_text(encoding='utf-8'))
    verts = tris = meshes = skinned = 0
    possum = 0.0
    for part in parts:
        lod0 = next((l for l in part['lods'] if l['lod'] == 0), None)
        for r in (lod0['renderers'] if lod0 else []):
            if not r.get('mesh_file'):
                continue
            h, a = unpack_mesh((pkg / r['mesh_file']).read_bytes())
            meshes += 1
            verts += h['vertex_count']
            tris += sum(len(a[s['index_array']]) // 3 for s in h['submeshes'] if s['topology'] == 0)
            possum += float(np.sum(a['positions'].astype(np.float64)))
            skinned += 'bone_indices' in a
    an = json.loads((pkg / 'animation' / 'controller.json').read_text(encoding='utf-8'))
    keys, keysum = 0, 0.0
    for meta in an['clips'].values():
        clip = json.loads((pkg / meta['file']).read_text(encoding='utf-8'))
        for g in ('positions', 'rotations', 'scales'):
            for c in clip[g]:
                for k in c['keys']:
                    keys += 1
                    keysum += k['t']
    rig = json.loads((pkg / 'rig.json').read_text(encoding='utf-8'))
    mats = json.loads((pkg / 'materials.json').read_text(encoding='utf-8'))
    face = json.loads((pkg / 'face.json').read_text(encoding='utf-8'))
    return {'rig': len(rig['nodes']), 'parts': len(parts), 'meshes': meshes, 'skinned': skinned, 'verts': verts, 'tris': tris,
            'possum': round(possum, 4), 'recipes': len(mats['recipes']), 'expr': len(face['expressions']), 'clips': len(an['clips']),
            'keys': keys, 'keysum': round(keysum, 4)}


def main(dotnet, dll, packages):
    out = subprocess.run([dotnet, dll] + packages, capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    bad = [l for l in lines if l.startswith(('FAIL', 'NEGATIVE-FAIL'))]
    for l in bad:
        print('C# reported:', l)
    rows = [l for l in lines if not l.startswith(('FAIL', 'NEGATIVE-FAIL'))]
    ok = not bad and out.returncode == 0 and len(rows) == len(packages)
    for row, pkg in zip(rows, packages):
        cs = dict(kv.split('=') for kv in row.split('|')[3:] if '=' in kv)
        py = python_numbers(pkg)
        diffs = {k: (cs.get(k), v) for k, v in py.items() if (abs(float(cs[k]) - float(v)) > 1e-3 if k in ('possum', 'keysum') else int(cs[k]) != int(v))}
        print(row.split('|')[0], 'MATCH' if not diffs else f'MISMATCH {diffs}')
        ok = ok and not diffs
    print('PARITY OK' if ok else 'PARITY FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
