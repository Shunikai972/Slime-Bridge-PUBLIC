"""Deterministic JSON, hashing, diagnostics and the shared evidence/status vocabulary.

Every content-pipeline document is hashed through ``canonical_json`` so identical
inputs give byte-identical manifests and cache keys regardless of dict order.
"""

import hashlib
import json
import math
import re
from enum import StrEnum

_SHA256 = re.compile(r'^[0-9a-f]{64}$')
REALMS = ('SR1', 'SR2')


class Evidence(StrEnum):
    """How far a claim is proven. Cloud work never exceeds UNIT_TESTED/STATICALLY_VERIFIED."""
    IMPLEMENTED = 'IMPLEMENTED'
    UNIT_TESTED = 'UNIT_TESTED'
    STATICALLY_VERIFIED = 'STATICALLY_VERIFIED'
    NEEDS_LOCAL_RUNTIME_PROOF = 'NEEDS_LOCAL_RUNTIME_PROOF'
    NEEDS_LOCAL_VISUAL_PROOF = 'NEEDS_LOCAL_VISUAL_PROOF'  # rendered/appearance claims (UI, 3D) seen only offscreen
    BLOCKED_ON_LOCAL_QUERY = 'BLOCKED_ON_LOCAL_QUERY'


class DataEvidence(StrEnum):
    """Origin of input data. SYNTHETIC data can never support a native claim."""
    SYNTHETIC = 'SYNTHETIC'
    STATIC = 'STATIC'
    RUNTIME_ANCHORED = 'RUNTIME_ANCHORED'


class Status(StrEnum):
    """Content availability, ordered from best to worst."""
    AVAILABLE = 'AVAILABLE'
    EXPERIMENTAL = 'EXPERIMENTAL'
    PENDING_ADAPTER = 'PENDING_ADAPTER'
    QUARANTINED = 'QUARANTINED'


_STATUS_RANK = {status: rank for rank, status in enumerate(Status)}


def worst_status(*statuses):
    """Combine statuses; one QUARANTINED part quarantines the whole."""
    return max((Status(s) for s in statuses), key=_STATUS_RANK.__getitem__, default=Status.AVAILABLE)


class Severity(StrEnum):
    ERROR = 'error'
    WARNING = 'warning'
    INFO = 'info'


def diag(code, severity, subject, detail=None):
    """A structured diagnostic. ``subject`` names the node/species/field concerned."""
    return {'code': code, 'severity': str(Severity(severity)), 'subject': str(subject),
            'detail': detail}


def sort_diagnostics(items):
    unique = {canonical_json(item): item for item in items}
    return [unique[key] for key in sorted(unique, key=lambda k: (
        unique[k]['severity'] != 'error', unique[k]['code'], unique[k]['subject'], k))]


def has_errors(items):
    return any(item['severity'] == Severity.ERROR for item in items)


def _reject_non_finite(value):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError('Non-finite number in canonical document')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError('Canonical documents need string keys')
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def canonical_json(value) -> str:
    """Sorted, compact, NaN-free JSON: the only form that is ever hashed."""
    _reject_non_finite(value)
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sha256_json(value) -> str:
    return sha256_text(canonical_json(value))


def is_sha256(value) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def pretty_json(value) -> str:
    """Stable human-diffable file form (same key order as the hashed form)."""
    _reject_non_finite(value)
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n'
