"""Load a plugin-written runtime registry capture (snapshot + separate traversal receipt).

The plugin's own claims are never trusted alone: the registry hash is recomputed from the rows,
the receipt must agree field by field, the build is re-hashed by the caller-selected file, and the
final gate is the generic ``read_runtime_snapshot``/``checked_runtime_proof``. This module does not
attest that a game ran; it checks the recorded evidence for internal and build consistency.
"""

import hashlib
import json
from pathlib import Path

from bridge.content.scan import read_runtime_snapshot

SNAPSHOT_FILE = 'registry-snapshot.json'
RECEIPT_FILE = 'traversal-receipt.json'
RECEIPT_SCHEMA = 'bridge-traversal-receipt'


class CaptureError(ValueError):
    """The capture is inconsistent or cannot be bound to the selected realm/build."""


def _line(row: dict) -> str:
    return json.dumps([row['object_ref'], row['native_id'], row['reference_id'], row['name'],
                       row['is_largo'], row['can_largofy'], list(row['base_slimes']),
                       list(row['produced_plorts']), list(row['prefab_refs'])],
                      separators=(',', ':'), ensure_ascii=True) + '\n'


def canonical_registry_sha256(rows: list[dict]) -> str:
    """Byte-identical to mods/shared/RegistrySnapshotFormat.RegistrySha256."""
    text = ''.join(_line(row) for row in sorted(rows, key=lambda r: r['object_ref'].encode('utf-16-be', 'surrogatepass')))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as ex:
        raise CaptureError(f'{path.name} unreadable: {type(ex).__name__}') from None
    if not isinstance(data, dict):
        raise CaptureError(f'{path.name} is not an object')
    return data


def _check_rows(rows: list) -> None:
    if not rows:
        raise CaptureError('Empty registry traversal can never prove a registry')
    native_ids, references = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise CaptureError('Definition row is not an object')
        native_id, reference = row.get('native_id'), row.get('reference_id')
        if native_id is not None:
            if type(native_id) is not int or native_id in native_ids:
                raise CaptureError(f'Invalid or duplicate native_id {native_id!r}')
            native_ids.add(native_id)
        if not isinstance(reference, str) or not reference or reference in references:
            raise CaptureError(f'Missing or duplicate reference_id {reference!r}')
        references.add(reference)
    base_references = {row['reference_id'] for row in rows if row.get('is_largo') is False}
    for row in rows:
        if row.get('is_largo') is True:
            bases = row.get('base_slimes') or []
            if len(bases) != 2 or len(set(bases)) != 2 or any(base not in base_references for base in bases):
                raise CaptureError(f'Largo {row["reference_id"]!r} bases do not resolve in this registry')


def load_capture(directory: Path, *, active_build: Path, realm: str, expected_run: str | None = None) -> dict:
    """Return the ``read_runtime_snapshot`` scan for one capture directory, or raise CaptureError."""
    directory = Path(directory)
    snapshot_path = directory / SNAPSHOT_FILE
    snapshot, receipt = _json(snapshot_path), _json(directory / RECEIPT_FILE)
    if realm not in ('SR1', 'SR2') or snapshot.get('realm') != realm or receipt.get('realm') != realm:
        raise CaptureError('Capture realm does not match the requested realm')
    if receipt.get('schema') != RECEIPT_SCHEMA or receipt.get('schema_version') != 1:
        raise CaptureError('Unsupported traversal receipt schema')
    collector = snapshot.get('collector') or {}
    if (not isinstance(collector.get('run_id'), str) or not collector['run_id'] or
            receipt.get('run_id') != collector['run_id'] or
            receipt.get('collector_version') != collector.get('version')):
        raise CaptureError('Receipt is not from the same collector run')
    if expected_run is not None and collector['run_id'] != expected_run:
        raise CaptureError('Capture is not from the expected runtime invocation')
    rows = snapshot.get('definitions')
    if not isinstance(rows, list):
        raise CaptureError('Definitions must be a list')
    _check_rows(rows)
    try:
        recomputed = canonical_registry_sha256(rows)
    except (KeyError, TypeError) as ex:
        raise CaptureError(f'Definition row missing canonical field: {ex}') from None
    traversal = snapshot.get('traversal_proof') or {}
    build = snapshot.get('build_proof') or {}
    if traversal.get('registry_sha256') != recomputed or receipt.get('registry_sha256') != recomputed:
        raise CaptureError('Registry hash does not match the recorded definitions')
    for field in ('registry_identity', 'expected_count', 'observed_count'):
        if receipt.get(field) != traversal.get(field):
            raise CaptureError(f'Receipt {field} disagrees with snapshot')
    counts = collector.get('counts')
    if (not isinstance(counts, dict) or set(counts) != {'slimes_array', 'by_identifiable'} or
            receipt.get('counts') != counts):
        raise CaptureError('Receipt independent counts disagree with snapshot')
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise CaptureError('Independent counts must be nonnegative integers')
    if type(traversal.get('observed_count')) is not int or traversal['observed_count'] != len(rows):
        raise CaptureError('Observed count must match serialized rows')
    if snapshot.get('completeness') == 'COMPLETE' and any(
            value != traversal.get('expected_count') for value in counts.values()):
        raise CaptureError('COMPLETE independent registry counts disagree')
    if receipt.get('active_sha256') != build.get('active_sha256'):
        raise CaptureError('Receipt build hash disagrees with snapshot')
    anchor = {'realm': realm, 'active_sha256': receipt.get('active_sha256'),
              'registry_identity': receipt.get('registry_identity'),
              'registry_sha256': receipt.get('registry_sha256'),
              'object_refs': receipt.get('object_refs')}
    return read_runtime_snapshot(snapshot_path, active_build=Path(active_build), traversal_anchor=anchor)
