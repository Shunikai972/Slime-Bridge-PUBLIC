"""Reader/validator for the additive, read-only bridge-diet-snapshot v1 LAB capture.

The diet snapshot is written next to the pinned registry snapshot files by both realm
collectors during a protected registry capture run. It is deliberately NOT part of the
pinned registry-snapshot.json pair: the pinned canonical hash format stays untouched.
"""

import json
import re
from pathlib import Path

SCHEMA = 'bridge-diet-snapshot'
SCHEMA_VERSION = 1
FILE_NAME = 'diet-snapshot.json'
_REALMS = ('SR1', 'SR2')
_SHA256_RE = re.compile(r'^[0-9a-f]{64}$')


class DietCaptureError(ValueError):
    pass


def _expect(condition, message):
    if not condition:
        raise DietCaptureError(message)


def load_diet_snapshot(path):
    """Read and fully validate a diet snapshot; raises DietCaptureError on any deviation."""
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except OSError as exc:
        raise DietCaptureError(f'unreadable diet snapshot: {exc}') from exc
    except json.JSONDecodeError as exc:
        raise DietCaptureError(f'diet snapshot is not valid JSON: {exc}') from exc
    validate_diet_snapshot(doc)
    return doc


def validate_diet_snapshot(doc):
    _expect(isinstance(doc, dict), 'diet snapshot must be an object')
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
        _validate_entry(doc['realm'], entry)
        _expect(entry['native_ref'] not in seen, f"duplicate native_ref {entry['native_ref']}")
        seen.add(entry['native_ref'])
    errors = doc.get('errors')
    _expect(isinstance(errors, list), 'errors must be a list')
    return doc


def _validate_entry(realm, entry):
    _expect(isinstance(entry, dict), 'entry must be an object')
    native_ref = entry.get('native_ref')
    _expect(isinstance(native_ref, str) and native_ref, 'entry needs native_ref')
    _expect(isinstance(entry.get('display_name'), str), 'entry needs display_name string')
    _expect(isinstance(entry.get('is_largo'), bool), 'entry needs is_largo bool')
    _expect(isinstance(entry.get('can_largofy'), bool), 'entry needs can_largofy bool')
    numeric_id = entry.get('numeric_id')
    if realm == 'SR1':
        _expect(numeric_id is None or type(numeric_id) is int, 'SR1 numeric_id must be an int or null')
    else:
        _expect(numeric_id is None, 'SR2 numeric_id must be null')
    for field in ('major_food_groups', 'favorites', 'produces', 'additional_foods'):
        value = entry.get(field)
        _expect(isinstance(value, list) and all(isinstance(v, str) and v for v in value),
                f'entry {field} must be a list of non-empty strings')
    group_members = entry.get('major_food_group_members', [])
    _expect(isinstance(group_members, list), 'major_food_group_members must be a list')
    for item in group_members:
        _expect(isinstance(item, dict) and isinstance(item.get('group'), str) and item.get('group') and
                isinstance(item.get('members'), list) and all(isinstance(m, str) and m for m in item['members']),
                'major_food_group_members entries need a group and non-empty member strings')
    count = entry.get('favorite_production_count')
    _expect(type(count) is int and count >= 0, 'favorite_production_count must be an int >= 0')


def diet_by_ref(doc):
    """Index a validated snapshot by native_ref."""
    return {entry['native_ref']: entry for entry in doc['entries']}
