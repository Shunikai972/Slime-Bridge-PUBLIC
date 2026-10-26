"""slimebridge-meshgen: generate the SlimeBridge gateway (``python -m bridge.meshgen.cli``).

    python3.12 -m bridge.meshgen.cli --variant meadow --out Lab/GeneratedContent/portal3d --preview

Writes ``<name>.glb`` (all LODs, collision/trigger/socket empties), ``<name>.obj`` + ``.mtl``
(one LOD), ``<name>.portal.json`` (sidecar) and optional ``previews/<name>_<view>.png``. ``--out``
is a relative path under ``Lab/GeneratedContent/`` (git-ignored) resolved against the repository;
absolute paths, ``..`` and symlinked components are refused and every write is atomic and
confined to that root. Generated files are never committed.

Exit codes: 0 ok, 2 usage, 4 invalid parameters or failed self-check, 5 unsafe output path.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from bridge.cache.fs import atomic_write, confined_root
from bridge.core import diagnostics as D
from bridge.core.paths import resolve_confined, safe_relative

from .export_gltf import export_glb, validate_glb
from .export_obj import export_obj
from .portal import VARIANTS, VISUAL_EVIDENCE, generate_portal, params_from_dict, variant_params
from .sidecar import sidecar_bytes

REPO = Path(__file__).resolve().parents[2]
OUTPUT_PREFIX = 'Lab/GeneratedContent'
DEFAULT_OUT = f'{OUTPUT_PREFIX}/portal3d'
EXIT_INPUT, EXIT_PATH = 4, 5
_NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')


class OutputPathError(ValueError):
    pass


def resolve_output(base, out):
    """``(root, out_dir)`` for ``out`` relative to ``base``, confined to ``base/Lab/GeneratedContent``.

    Refuses absolute paths, '..'/'.' segments, backslashes, paths outside the prefix and any
    symlink among the root's ancestors or the output path's components.
    """
    if not isinstance(out, str) or not safe_relative(out):
        raise OutputPathError('--out must be a relative POSIX path without "..", "." or absolute parts')
    if out != OUTPUT_PREFIX and not out.startswith(OUTPUT_PREFIX + '/'):
        raise OutputPathError(f'--out must stay under {OUTPUT_PREFIX}/ (git-ignored, never committed)')
    base = Path(os.path.abspath(base))
    try:
        root = confined_root(base / OUTPUT_PREFIX)
    except ValueError as exc:
        raise OutputPathError(str(exc)) from None
    target = resolve_confined(base, out)
    if target is None:
        raise OutputPathError('output path traverses a symlink')
    if target.exists() and not target.is_dir():
        raise OutputPathError('output path exists and is not a directory')
    return root, target


def write_bundle(root, out_dir, files):
    """Atomically write ``{relative name: bytes}`` under ``out_dir`` (inside ``root``)."""
    written = []
    for name in sorted(files):
        if not safe_relative(name):
            raise OutputPathError(f'unsafe output file name: {name!r}')
        try:
            atomic_write(root, out_dir / name, files[name])
        except ValueError as exc:
            raise OutputPathError(str(exc)) from None
        written.append(name)
    return written


def build_files(model, name, *, preview=False, preview_lod=1, size=(480, 360), supersample=2, split_lods=False,
                obj_lod=0):
    """All output files as ``{relative name: bytes}`` (pure; nothing is written)."""
    glb = export_glb(model)
    problems = validate_glb(glb)
    if problems:
        raise ValueError(f'GLB self-check failed: {[p["code"] for p in problems]}')
    obj, mtl = export_obj(model, level=obj_lod, mtl_name=f'{name}.mtl')
    files = {f'{name}.glb': glb, f'{name}.obj': obj.encode('utf-8'), f'{name}.mtl': mtl.encode('utf-8')}
    if split_lods:
        for lod in model.lods:
            data = export_glb(model, levels=[lod['level']], name=f'{name}_LOD{lod["level"]}')
            if validate_glb(data):
                raise ValueError(f'GLB self-check failed for LOD{lod["level"]}')
            files[f'{name}_LOD{lod["level"]}.glb'] = data
    if preview:
        from .preview import render_views  # only needed for previews
        for view, png in render_views(model, level=preview_lod, width=size[0], height=size[1],
                                      supersample=supersample).items():
            files[f'previews/{name}_{view}.png'] = png
    files[f'{name}.portal.json'] = sidecar_bytes(model, files)
    return files


def _size(text):
    match = re.fullmatch(r'(\d{2,4})x(\d{2,4})', text)
    if not match:
        raise argparse.ArgumentTypeError('expected WIDTHxHEIGHT, e.g. 480x360')
    return int(match.group(1)), int(match.group(2))


def parser():
    p = argparse.ArgumentParser(prog='slimebridge-meshgen', description=__doc__.split('\n\n')[0])
    p.add_argument('--variant', choices=sorted(VARIANTS), default='meadow')
    p.add_argument('--params', help='JSON object of PortalParams overrides applied on top of the variant')
    p.add_argument('--out', default=DEFAULT_OUT, help=f'relative output directory under {OUTPUT_PREFIX}/')
    p.add_argument('--name', help='output file stem (default slimebridge_gateway_<variant>)')
    p.add_argument('--preview', action='store_true', help='also render PNG previews (pure Python, slow-ish)')
    p.add_argument('--preview-lod', type=int, default=1)
    p.add_argument('--preview-size', type=_size, default=(480, 360))
    p.add_argument('--supersample', type=int, choices=(1, 2, 3), default=2)
    p.add_argument('--split-lods', action='store_true', help='also write one GLB per LOD')
    p.add_argument('--obj-lod', type=int, default=0)
    p.add_argument('--base', default=str(REPO), help=argparse.SUPPRESS)  # repository root (tests)
    return p


def _emit(payload):
    print(json.dumps(payload, sort_keys=True))


def main(argv=None):
    args = parser().parse_args(argv)
    name = args.name or f'slimebridge_gateway_{args.variant}'
    if not _NAME.fullmatch(name):
        _emit({'status': 'INVALID_INPUT', 'error': 'name must match [a-z0-9][a-z0-9_-]{0,63}'})
        return EXIT_INPUT
    try:
        root, out_dir = resolve_output(args.base, args.out)
    except OutputPathError as exc:
        _emit({'status': 'UNSAFE_OUTPUT_PATH', 'error': str(exc)})
        return EXIT_PATH
    values = variant_params(args.variant).to_dict()
    if args.params:
        try:
            raw = json.loads(Path(args.params).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            _emit({'status': 'INVALID_INPUT', 'error': f'cannot read --params: {exc}'})
            return EXIT_INPUT
        if not isinstance(raw, dict):
            _emit({'status': 'INVALID_INPUT', 'error': '--params must hold a JSON object'})
            return EXIT_INPUT
        values.update(raw)
    params, diags = params_from_dict(values)
    model = generate_portal(params) if params is not None else None
    if model is None or not model.ok:
        _emit({'status': 'INVALID_PARAMS', 'diagnostics': D.sort(diags + (model.diagnostics if model else []))})
        return EXIT_INPUT
    if not 0 <= args.obj_lod < len(model.lods) or not 0 <= args.preview_lod < len(model.lods):
        _emit({'status': 'INVALID_INPUT', 'error': f'LOD index must be within 0..{len(model.lods) - 1}'})
        return EXIT_INPUT
    try:
        files = build_files(model, name, preview=args.preview, preview_lod=args.preview_lod, size=args.preview_size,
                            supersample=args.supersample, split_lods=args.split_lods, obj_lod=args.obj_lod)
    except ValueError as exc:
        _emit({'status': 'SELF_CHECK_FAILED', 'error': str(exc)})
        return EXIT_INPUT
    try:
        written = write_bundle(root, out_dir, files)
    except OutputPathError as exc:
        _emit({'status': 'UNSAFE_OUTPUT_PATH', 'error': str(exc)})
        return EXIT_PATH
    _emit({'status': 'OK', 'output': str(out_dir), 'files': written, 'params_hash': model.params_hash,
           'triangle_counts': model.metadata['triangle_counts'], 'glb_self_check': 'PASSED',
           'warnings': [d['code'] for d in model.diagnostics], 'evidence': VISUAL_EVIDENCE})
    return 0


if __name__ == '__main__':
    sys.exit(main())
