"""Normalized native registry snapshots (``bridge-registry-snapshot``) for reconciliation.

A snapshot lists what one realm's native registry contains. Snapshot-level problems
(schema, realm) raise; a malformed *entry* is kept as a quarantined row with diagnostics so
one bad definition cannot abort a batch. Self-reported COMPLETE traversals whose counts
disagree are downgraded to PARTIAL. ``snapshot_from_scan`` reuses the existing scanner proof
gate (``checked_runtime_proof``) rather than duplicating it.
"""

import copy

from bridge.content.canonical import REALMS, DataEvidence, diag, is_sha256, sha256_json, sort_diagnostics
from bridge.content.scan import checked_runtime_proof

SNAPSHOT_SCHEMA = 'bridge-registry-snapshot'
SNAPSHOT_SCHEMA_VERSION = 1
COMPLETENESS = ('COMPLETE', 'PARTIAL', 'FAILED')
_ANCHOR_TOKEN = object()


class AnchoredSnapshot(dict):
    """A snapshot built in-process from a scan that passed ``checked_runtime_proof``.

    Only ``snapshot_from_scan`` can create one. RUNTIME_ANCHORED evidence is honoured only for
    an intact instance (content hash unchanged since creation); anything read back from JSON
    is a plain dict and is downgraded, matching the existing rule that a snapshot's own
    attestation is never trusted.
    """

    def __init__(self, data, token=None):
        if token is not _ANCHOR_TOKEN:
            raise TypeError('AnchoredSnapshot is created only by snapshot_from_scan')
        super().__init__(data)
        self._anchor_hash = sha256_json(dict(self))

    def intact(self):
        return sha256_json(dict(self)) == getattr(self, '_anchor_hash', None)
_ENTRY_FIELDS = {'native_ref', 'numeric_id', 'content_kind', 'role', 'canonical_id', 'canonical_evidence',
                 'display_name', 'definition_hash', 'produces', 'base_slimes', 'quarantine'}


def _ref(value):
    return (type(value) is int and value >= 0) or (isinstance(value, str) and bool(value))


def _entry_problems(row):
    problems = []
    if not isinstance(row, dict):
        return ['entry is not an object']
    missing = _ENTRY_FIELDS - set(row)
    extra = set(row) - _ENTRY_FIELDS
    if missing:
        problems.append('missing ' + ','.join(sorted(missing)))
    if extra:
        problems.append('unknown ' + ','.join(sorted(extra)))
    if not isinstance(row.get('native_ref'), str) or not row.get('native_ref'):
        problems.append('native_ref')
    if row.get('content_kind') not in ('slime', 'plort'):
        problems.append('content_kind')
    if row.get('content_kind') == 'slime' and row.get('role', 'x') not in ('base', 'largo', None):
        problems.append('role')
    if row.get('numeric_id') is not None and not (type(row.get('numeric_id')) is int and row['numeric_id'] >= 0):
        problems.append('numeric_id')
    if not is_sha256(row.get('definition_hash')):
        problems.append('definition_hash')
    for key in ('produces', 'base_slimes'):
        if not isinstance(row.get(key), list) or not all(_ref(v) for v in row[key]):
            problems.append(key)
    evidence = row.get('canonical_evidence')
    if not isinstance(evidence, list) or not all(isinstance(e, str) and e for e in evidence):
        problems.append('canonical_evidence')
    if row.get('canonical_id') is not None and not isinstance(row.get('canonical_id'), str):
        problems.append('canonical_id')
    if row.get('quarantine') is not None and (not isinstance(row['quarantine'], str) or not row['quarantine']):
        problems.append('quarantine')
    return problems


def _salvage(row):
    """Keep whatever identity survives so a malformed entry can still be quarantined by name."""
    native_ref = row.get('native_ref') if isinstance(row, dict) else None
    kind = row.get('content_kind') if isinstance(row, dict) else None
    numeric = row.get('numeric_id') if isinstance(row, dict) else None
    return {'native_ref': native_ref if isinstance(native_ref, str) and native_ref else None,
            'numeric_id': numeric if type(numeric) is int and numeric >= 0 else None,
            'content_kind': kind if kind in ('slime', 'plort') else None,
            'role': None, 'canonical_id': None, 'canonical_evidence': [], 'display_name': None,
            'definition_hash': None, 'produces': [], 'base_slimes': [], 'quarantine': None,
            'malformed': True}


def normalize_snapshot(data) -> dict:
    """Validate and canonicalize; raises only for snapshot-level errors."""
    if not isinstance(data, dict) or data.get('schema') != SNAPSHOT_SCHEMA:
        raise ValueError(f'Expected {SNAPSHOT_SCHEMA}')
    if type(data.get('schema_version')) is not int or data['schema_version'] != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f'Unsupported {SNAPSHOT_SCHEMA} version {data.get("schema_version")!r}')
    if data.get('realm') not in REALMS:
        raise ValueError('Snapshot realm must be SR1 or SR2')
    build = data.get('build')
    if not isinstance(build, dict) or not isinstance(build.get('fingerprint'), str) or not build['fingerprint']:
        raise ValueError('Snapshot needs a build fingerprint')
    if data.get('data_evidence') not in tuple(DataEvidence):
        raise ValueError('Snapshot needs data_evidence')
    traversal = data.get('traversal')
    if not isinstance(traversal, dict) or traversal.get('completeness') not in COMPLETENESS:
        raise ValueError('Snapshot needs traversal completeness')
    if not isinstance(data.get('entries'), list):
        raise ValueError('Snapshot entries must be a list')
    realm = data['realm']
    diags = [d for d in data.get('diagnostics', []) if isinstance(d, dict)]
    entries = []
    for index, raw in enumerate(data['entries']):
        problems = _entry_problems(raw)
        if problems:
            row = _salvage(raw)
            subject = f"{realm}:{row['native_ref'] or f'#{index}'}"
            diags.append(diag('MALFORMED_ENTRY', 'error', subject, '; '.join(problems)))
        else:
            row = copy.deepcopy(raw)
            row['canonical_evidence'] = sorted(set(row['canonical_evidence']))
            row['malformed'] = False
        entries.append(row)
    seen = {}
    for row in entries:
        if row['native_ref'] is not None:
            seen[row['native_ref']] = seen.get(row['native_ref'], 0) + 1
    for ref, count in sorted(seen.items()):
        if count > 1:
            diags.append(diag('DUPLICATE_NATIVE_REF', 'error', f'{realm}:{ref}', f'{count} entries'))
    completeness = traversal['completeness']
    evidence = DataEvidence(data['data_evidence'])
    if evidence == DataEvidence.RUNTIME_ANCHORED and not (isinstance(data, AnchoredSnapshot) and data.intact()):
        diags.append(diag('RUNTIME_CLAIM_UNANCHORED', 'warning', realm,
                          'self-declared runtime evidence downgraded to STATIC'))
        evidence = DataEvidence.STATIC
    if evidence == DataEvidence.STATIC and completeness == 'COMPLETE':
        diags.append(diag('STATIC_TRAVERSAL_NOT_COMPLETE', 'warning', realm,
                          'static/unanchored data cannot prove a complete registry'))
        completeness = 'PARTIAL'
    expected, observed = traversal.get('expected_count'), traversal.get('observed_count')
    if completeness == 'COMPLETE' and not (type(expected) is int and expected == observed == len(entries)):
        diags.append(diag('TRAVERSAL_COUNT_MISMATCH', 'error', realm,
                          f'expected={expected!r} observed={observed!r} entries={len(entries)}'))
        completeness = 'PARTIAL'
    key = lambda r: (r['native_ref'] is None, r['native_ref'] or '', sha256_json(r))
    return {'schema': SNAPSHOT_SCHEMA, 'schema_version': SNAPSHOT_SCHEMA_VERSION, 'realm': realm,
            'build': {'fingerprint': build['fingerprint'], 'label': build.get('label')},
            'data_evidence': str(evidence),
            'traversal': {'completeness': completeness, 'registry_identity': traversal.get('registry_identity'),
                          'expected_count': expected, 'observed_count': observed},
            'entries': sorted(entries, key=key), 'diagnostics': sort_diagnostics(diags)}


def snapshot_from_scan(scan: dict) -> dict:
    """Adapt a ``bridge.content.scan`` result; COMPLETE only when its runtime proof checks."""
    proven = checked_runtime_proof(scan)
    entries = []
    for row in scan.get('definitions', []):
        reference = row.get('reference_id')
        native_ref = reference if isinstance(reference, str) and reference else row.get('object_ref')
        native_id = row.get('native_id')
        evidence = list(row.get('canonical_evidence') or []) if row.get('canonical_proven') else []
        entries.append({
            'native_ref': native_ref, 'numeric_id': native_id if type(native_id) is int else None,
            'content_kind': 'slime',
            'role': {False: 'base', True: 'largo'}.get(row.get('is_largo')),
            'canonical_id': row.get('canonical_id'), 'canonical_evidence': evidence,
            'display_name': row.get('name'), 'definition_hash': sha256_json(row),
            'produces': list(row.get('produced_plorts') or []), 'base_slimes': list(row.get('base_slimes') or []),
            'quarantine': None})
    traversal = scan.get('traversal_proof') or {}
    completeness = 'COMPLETE' if proven else ('FAILED' if scan.get('completeness') == 'FAILED' else 'PARTIAL')
    fingerprint = (scan.get('build_proof') or {}).get('active_sha256') or scan.get('game_build') or \
        f"unverified:{scan.get('source_hash', 'unknown')}"
    snapshot = {'schema': SNAPSHOT_SCHEMA, 'schema_version': SNAPSHOT_SCHEMA_VERSION, 'realm': scan['realm'],
            'build': {'fingerprint': fingerprint, 'label': scan.get('game_build')},
            'data_evidence': str(DataEvidence.RUNTIME_ANCHORED if proven else DataEvidence.STATIC),
            'traversal': {'completeness': completeness,
                          'registry_identity': traversal.get('registry_identity'),
                          'expected_count': traversal.get('expected_count', len(entries)) if proven else None,
                          'observed_count': traversal.get('observed_count', len(entries)) if proven else None},
            'entries': entries,
            'diagnostics': [diag('SCAN_ERROR', 'error', f"{scan['realm']}:{e.get('source')}", e.get('code'))
                            for e in scan.get('errors', []) if isinstance(e, dict)]}
    return AnchoredSnapshot(snapshot, _ANCHOR_TOKEN) if proven else snapshot
