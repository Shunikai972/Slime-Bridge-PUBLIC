"""Build every cross-game species adapter plus the parity matrix from the captured LAB data.

Reads only reviewed local artifacts (``classification.json`` plus the ``SR1-snapshot.json`` /
``SR2-snapshot.json`` registry captures beside it), derives the exact exclusive base-species
roster (never a hardcoded list), writes one metadata-only descriptor per exclusive species plus
``parity.json`` under the ignored ``Lab/GeneratedContent`` tree, and never touches game installs,
saves or the network. Visual content is never attached: every output stays
``VISUAL_ASSET_BLOCKED`` until the dedicated visual phase.

Usage:
  py -3.12 -m tools.content.build_species_adapter
  py -3.12 -m tools.content.build_species_adapter --classification <dir> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from bridge.cache.fs import atomic_write, confined_root
from bridge.content.canonical import pretty_json, sha256_json
from bridge.conversion.foreign_spawn import default_policy, effective_policy
from bridge.conversion.species_adapter import build_adapter, validate_adapter
from bridge.conversion.species_parity import (build_parity_matrix, derive_roster,
                                              target_identity_collisions, validate_parity)

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CLASSIFICATION = REPO / 'Lab' / 'RuntimeArtifacts' / 'classification-20261006-runtime'
OUTPUT_ROOT = REPO / 'Lab' / 'GeneratedContent'
SUMMARY_SCHEMA = 'bridge-species-adapter-run'
SUMMARY_SCHEMA_VERSION = 2


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise SystemExit(f'cannot read {path}: {exc}')


def _pipelines(roster) -> list:
    return ([('SR1', 'SR2', canonical_id) for canonical_id in roster['sr1_only']] +
            [('SR2', 'SR1', canonical_id) for canonical_id in roster['sr2_only']])


def build(classification_dir: Path, out_dir: Path) -> int:
    classification = _load(classification_dir / 'classification.json')
    snapshots = {realm: _load(classification_dir / f'{realm}-snapshot.json') for realm in ('SR1', 'SR2')}
    root = confined_root(OUTPUT_ROOT)
    out = confined_root(out_dir)
    if not out.is_relative_to(root):
        raise SystemExit(f'--out-dir must stay under {root} (ignored, never committed)')
    roster = derive_roster(classification)
    pipelines = _pipelines(roster)
    results, adapters = [], {}
    for source_realm, target_realm, canonical_id in pipelines:
        doc = build_adapter(classification, source_realm, target_realm, canonical_id,
                            snapshots[source_realm])
        problems = validate_adapter(doc)
        adapters[doc['adapter_id']] = doc
        results.append({'direction': f'{source_realm}->{target_realm}', 'canonical_id': canonical_id,
                        'adapter_id': doc['adapter_id'], 'status': doc['status'],
                        'diagnostics': doc['diagnostics'], 'validation': problems})
    expected_ids = {canonical_id for _, _, canonical_id in pipelines}
    prepared_ids = [doc['canonical_id'] for doc in adapters.values()]
    missing = sorted(canonical_id for canonical_id in expected_ids
                     if prepared_ids.count(canonical_id) != 1)
    policy = default_policy()
    effective = effective_policy(policy, imported_ids=roster['sr1_only'] + roster['sr2_only'])
    matrix = build_parity_matrix(classification, snapshots, adapters, policy=policy)
    matrix_problems = validate_parity(matrix)
    summary = {
        'schema': SUMMARY_SCHEMA,
        'schema_version': SUMMARY_SCHEMA_VERSION,
        'built_utc': datetime.now().isoformat(),
        'inputs': {name: sha256_json(_load(classification_dir / name)) for name in
                   ('classification.json', 'SR1-snapshot.json', 'SR2-snapshot.json')},
        'roster': {'shared_native': roster['shared_native'], 'sr1_only': roster['sr1_only'],
                   'sr2_only': roster['sr2_only'], 'names': roster['names'],
                   'excluded': roster['excluded'], 'counts': roster['counts']},
        'collisions': target_identity_collisions(classification, roster),
        'pipelines': results,
        'missing_adapters': missing,
        'parity': {'hash': sha256_json(matrix), 'validation': matrix_problems,
                   'blocked_queries': matrix['blocked_queries']},
        'policy': {'document_hash': sha256_json(policy), 'mode': policy['mode'],
                   'effective_hash': sha256_json(effective)},
        'blocked_visual_pipelines': sorted(adapters),
        'passed': (all(r['validation'] == [] and r['status'] == 'EXPERIMENTAL' for r in results)
                   and not missing and not matrix_problems),
    }
    atomic_write(root, out / 'parity.json', pretty_json(matrix).encode('utf-8'))
    for adapter_key, doc in sorted(adapters.items()):
        atomic_write(root, out / f'{adapter_key}.json', pretty_json(doc).encode('utf-8'))
    atomic_write(root, out / 'summary.json', pretty_json(summary).encode('utf-8'))
    print(pretty_json(summary))
    return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description='Prepare every cross-game species adapter (metadata only)')
    parser.add_argument('--classification', default=str(DEFAULT_CLASSIFICATION),
                        help='directory holding classification.json and the realm snapshots')
    parser.add_argument('--out-dir', default=None)
    args = parser.parse_args()
    out = Path(args.out_dir) if args.out_dir else OUTPUT_ROOT / 'species-adapters' / datetime.now().strftime('%Y%m%d-%H%M%S')
    raise SystemExit(build(Path(args.classification), out))


if __name__ == '__main__':
    main()
