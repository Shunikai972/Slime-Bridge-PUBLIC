"""Reader/validator for the additive, read-only bridge-appearance-colors v1 LAB capture.

The appearance-colors snapshot is written next to the pinned registry snapshot files and the
diet snapshot by the SR2 collector during a protected registry capture run. It carries palette
struct values only (no textures, no geometry) and is deliberately NOT part of the pinned
registry-snapshot.json pair: the pinned canonical hash format stays untouched.
"""

import json
import math
import re
from pathlib import Path

SCHEMA = 'bridge-appearance-colors'
SCHEMA_VERSION = 1
FILE_NAME = 'appearance-colors.json'
_REALMS = ('SR1', 'SR2')
_SAVE_SETS = ('NONE', 'CLASSIC', 'SECRET_STYLE')
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
_PALETTE_LENGTH = 16  # top rgba, middle rgba, bottom rgba, ammo rgba


class AppearanceCaptureError(ValueError):
    pass


def _expect(condition, message):
    if not condition:
        raise AppearanceCaptureError(message)


def load_appearance_snapshot(path):
    """Read and fully validate an appearance-colors snapshot; raises AppearanceCaptureError."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        raise AppearanceCaptureError(f'unreadable appearance snapshot: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise AppearanceCaptureError(f'appearance snapshot is not valid JSON: {exc}') from exc
    validate_appearance_snapshot(doc)
    return doc


def validate_appearance_snapshot(doc):
    _expect(isinstance(doc, dict), 'appearance snapshot must be an object')
    _expect(doc.get('schema') == SCHEMA, f'expected schema {SCHEMA}')
    _expect(doc.get('schema_version') == SCHEMA_VERSION, f'expected schema_version {SCHEMA_VERSION}')
    _expect(doc.get('realm') in _REALMS, 'realm must be SR1 or SR2')
    run_id = doc.get('run_id')
    _expect(isinstance(run_id, str) and 8 <= len(run_id) <= 64, 'run_id must be 8-64 chars')
    _expect(_SHA256_RE.match(str(doc.get('active_sha256', ''))) is not None, 'active_sha256 must be hex sha256')
    entries = doc.get('entries')
    _expect(isinstance(entries, list) and entries, 'entries must be a non-empty list')
    seen = set()
    for entry in entries:
        _validate_entry(entry)
        _expect(entry['native_ref'] not in seen, f"duplicate native_ref {entry['native_ref']}")
        seen.add(entry['native_ref'])
    errors = doc.get('errors')
    _expect(isinstance(errors, list), 'errors must be a list')
    return doc


def _validate_entry(entry):
    _expect(isinstance(entry, dict), 'entry must be an object')
    native_ref = entry.get('native_ref')
    _expect(isinstance(native_ref, str) and native_ref, 'entry needs native_ref')
    _expect(isinstance(entry.get('display_name'), str), 'entry needs display_name string')
    _expect(entry.get('save_set') in _SAVE_SETS, f"save_set must be one of {_SAVE_SETS}")
    for field in ('palette_legacy', 'palette_new'):
        value = entry.get(field)
        if value is None:
            continue
        _expect(isinstance(value, list) and len(value) == _PALETTE_LENGTH,
                f'{field} must be {_PALETTE_LENGTH} floats')
        for item in value:
            _expect(type(item) in (int, float) and math.isfinite(item), f'{field} entries must be finite numbers')


def appearance_by_ref(doc):
    """Index a validated snapshot by native_ref."""
    return {entry['native_ref']: entry for entry in doc['entries']}
