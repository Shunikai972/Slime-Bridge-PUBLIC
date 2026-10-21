"""Write deterministic metadata-only content manifests into ignored worktree Lab."""

import argparse
import json
import os
import uuid
from pathlib import Path

from bridge.content.coverage import species_coverage
from bridge.content.scan import build_ir, read_runtime_snapshot, scan_sr1_export, scan_sr2_catalog
from tools.lab.paths import lexical_path, refuse_reparse


def _refuse_linked_output(path):
    refuse_reparse(path)
    if path.exists() and path.stat().st_nlink > 1:
        raise ValueError(f'Hardlink in content output: {path}')


def _write(path, value):
    path = lexical_path(path)
    _refuse_linked_output(path)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')
    created = False
    try:
        with open(tmp, 'xb') as f:
            created = True
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        refuse_reparse(tmp)
        _refuse_linked_output(path)
        os.replace(tmp, path)
    finally:
        if created and tmp.exists():
            refuse_reparse(tmp)
            tmp.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    first = parser.add_mutually_exclusive_group(required=True)
    first.add_argument('--sr1-export', type=Path, help='AssetRipper ExportedProject/Assets root')
    first.add_argument('--sr1-runtime', type=Path, help='normalized SR1 registry snapshot JSON')
    second = parser.add_mutually_exclusive_group(required=True)
    second.add_argument('--sr2-catalog', type=Path, help='SR2 Addressables catalog.json')
    second.add_argument('--sr2-runtime', type=Path, help='normalized SR2 registry snapshot JSON')
    parser.add_argument('--sr1-traversal-receipt', type=Path, help='independent native registry receipt')
    parser.add_argument('--sr2-traversal-receipt', type=Path, help='independent native registry receipt')
    parser.add_argument('--sr1-export-build', help='export version label; active DLL match stays unverified')
    parser.add_argument('--out-dir', type=Path, help='directory under this worktree Lab/GeneratedContent')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    allowed = lexical_path(root / 'Lab' / 'GeneratedContent')
    out = lexical_path(args.out_dir or allowed / 'content-scan')
    refuse_reparse(out)
    if not out.is_relative_to(allowed):
        parser.error('output must remain under this worktree Lab/GeneratedContent')
    sr1 = (read_runtime_snapshot(args.sr1_runtime,
               active_build=root / 'Lab/Games/SR1/SlimeRancher_Data/Managed/Assembly-CSharp.dll',
               traversal_anchor=json.loads(args.sr1_traversal_receipt.read_text(encoding='utf-8'))
                   if args.sr1_traversal_receipt else None)
           if args.sr1_runtime else scan_sr1_export(args.sr1_export, args.sr1_export_build))
    sr2 = (read_runtime_snapshot(args.sr2_runtime,
               active_build=root / 'Lab/Games/SR2/GameAssembly.dll',
               traversal_anchor=json.loads(args.sr2_traversal_receipt.read_text(encoding='utf-8'))
                   if args.sr2_traversal_receipt else None)
           if args.sr2_runtime else scan_sr2_catalog(args.sr2_catalog))
    if sr1['realm'] != 'SR1' or sr2['realm'] != 'SR2':
        parser.error('snapshot realm does not match its command line slot')
    ir = build_ir([sr1, sr2])
    species = species_coverage(sr1, sr2)
    out.mkdir(parents=True, exist_ok=True)
    refuse_reparse(out)
    _write(out / 'sr1-content-manifest.json', sr1)
    _write(out / 'sr2-content-manifest.json', sr2)
    _write(out / 'bridge-content-manifest.json', {'schema_version': 1, 'species': species, 'ir': ir})
    print(json.dumps({'sr1_definitions': len(sr1['definitions']),
                      'sr1_bases': sum(d['is_largo'] is False for d in sr1['definitions']),
                      'sr1_largos': sum(d['is_largo'] is True for d in sr1['definitions']),
                      'sr1_errors': len(sr1['errors']), 'sr1_completeness': sr1['completeness'],
                      'sr2_definitions': len(sr2['definitions']),
                      'sr2_bases': sum(d['is_largo'] is False for d in sr2['definitions']),
                      'sr2_largos': sum(d['is_largo'] is True for d in sr2['definitions']),
                      'sr2_errors': len(sr2['errors']), 'sr2_completeness': sr2['completeness'],
                      'ir_nodes': len(ir['nodes']), 'ir_edges': len(ir['edges']),
                      'ir_errors': len(ir['errors']), 'output': str(out)}, sort_keys=True))


if __name__ == '__main__':
    main()
