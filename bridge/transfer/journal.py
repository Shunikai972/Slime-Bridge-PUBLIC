"""Strict reader for the native flat, fsynced operation journal; no file is never proof of absence."""
from __future__ import annotations

import json
import re
from pathlib import Path

_OP = re.compile(r'[A-Za-z0-9_.:-]{1,160}\Z', re.ASCII)
_STRINGS = {'message_type', 'realm', 'op_id', 'kind', 'item', 'creation_id', 'save_file',
            'sha256', 'save_id', 'reason'}
_INTS = {'protocol_version', 'pid', 'before', 'after', 'count'}
_BOOLS = {'ok', 'applied', 'uncertain', 'persisted', 'can_add', 'verified', 'can_create'}
_KINDS = {'query', 'remove', 'add', 'lab_give', 'create_save', 'reconcile_save'}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate receipt key')
        value[key] = item
    return value


def _receipt(op_id: str, text: str) -> dict:
    value = json.loads(text, object_pairs_hook=_unique_object)
    if type(value) is not dict:
        raise ValueError('receipt must be a flat object')
    for key, item in value.items():
        allowed = ((key in _STRINGS and type(item) is str)
                   or (key in _INTS and type(item) is int and -(2**31) <= item < 2**31)
                   or (key in _BOOLS and type(item) is bool))
        if not allowed:
            raise ValueError('unknown or mistyped receipt field')
    if (type(value.get('protocol_version')) is not int or value['protocol_version'] != 1
            or value.get('message_type') != 'command_result' or value.get('op_id') != op_id
            or value.get('realm') not in ('SR1', 'SR2') or value.get('kind') not in _KINDS
            or type(value.get('pid')) is not int or value['pid'] <= 0
            or type(value.get('ok')) is not bool or type(value.get('applied')) is not bool):
        raise ValueError('invalid journal receipt provenance')
    if value['kind'] in ('query', 'remove', 'add', 'lab_give'):
        if (not isinstance(value.get('item'), str) or not value['item']
                or not isinstance(value.get('save_id'), str) or not value['save_id']
                or type(value.get('count')) is not int or not 0 <= value['count'] <= 30):
            raise ValueError('incomplete inventory receipt')
    return value


def read_operations(path: Path | str) -> tuple[set[str], dict[str, dict]] | None:
    """Return validated intents/results, or None for missing, corrupt or ambiguous evidence."""
    try:
        path = Path(path)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
            return None
        raw = path.read_bytes()
        if raw and not raw.endswith(b'\n'):
            return None  # includes a torn final append
        intents, done, encoded = set(), {}, {}
        for line in raw.decode('utf-8', errors='strict').splitlines():
            if not line:
                return None
            parts = line.split('\t')
            if len(parts) not in (2, 3) or not _OP.fullmatch(parts[1]):
                return None
            op_id = parts[1]
            if parts[0] == 'I' and len(parts) == 2:
                if op_id in intents or op_id in done:
                    return None
                intents.add(op_id)
            elif parts[0] == 'D' and len(parts) == 3:
                receipt = _receipt(op_id, parts[2])
                if op_id in encoded:
                    return None
                encoded[op_id], done[op_id] = parts[2], receipt
            else:
                return None
        return intents, done
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
