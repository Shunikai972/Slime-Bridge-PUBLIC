"""Canonical cross-game identifiers; never use native array slots as identity."""

import json
from dataclasses import dataclass
from pathlib import Path


class UnverifiedNativeMapping(ValueError):
    """A candidate native mapping cannot be used for transfers until runtime-validated."""


@dataclass(frozen=True)
class NativeMapping:
    identifier: int | str
    verified: bool


@dataclass(frozen=True)
class ContentEntry:
    canonical_id: str
    content_kind: str
    native: dict[str, NativeMapping]


class ContentRegistry:
    def __init__(self, entries: list[ContentEntry]):
        self.entries: dict[str, ContentEntry] = {}
        self._by_native: dict[tuple[str, int | str], ContentEntry] = {}
        for entry in entries:
            if entry.canonical_id in self.entries:
                raise ValueError(f'Duplicate canonical ID: {entry.canonical_id}')
            self.entries[entry.canonical_id] = entry
            for realm, mapping in entry.native.items():
                key = (realm, mapping.identifier)
                if key in self._by_native:
                    raise ValueError(f'Duplicate native content ID: {key}')
                self._by_native[key] = entry

    @classmethod
    def load(cls, path: Path) -> 'ContentRegistry':
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
            raise ValueError('Unsupported content registry schema')
        rows = data.get('entries')
        if not isinstance(rows, list):
            raise ValueError('Content entries must be a list')
        entries = []
        for row in rows:
            if not isinstance(row, dict) or set(row) != {'canonical_id', 'content_kind', 'native'}:
                raise ValueError('Invalid content entry')
            canonical = row['canonical_id']
            kind = row['content_kind']
            if (not isinstance(canonical, str) or not canonical or
                    kind not in ('slime', 'plort') or not canonical.startswith(kind + '.')):
                raise ValueError('Invalid canonical content identity')
            raw_native = row['native']
            if not isinstance(raw_native, dict) or not raw_native or any(r not in ('SR1', 'SR2') for r in raw_native):
                raise ValueError('Invalid native realm mapping')
            native = {}
            for realm, value in raw_native.items():
                if not isinstance(value, dict) or set(value) != {'id', 'verified'}:
                    raise ValueError('Invalid native content mapping')
                identifier = value['id']
                if type(value['verified']) is not bool or (type(identifier) is not int and
                    (not isinstance(identifier, str) or not identifier)) or (type(identifier) is int and identifier < 0):
                    raise ValueError('Invalid native ID or verification state')
                native[realm] = NativeMapping(identifier, value['verified'])
            entries.append(ContentEntry(canonical, kind, native))
        return cls(entries)

    def resolve_native(self, realm: str, identifier: int | str, require_verified: bool = True) -> ContentEntry | None:
        entry = self._by_native.get((realm, identifier))
        if entry is not None and require_verified and not entry.native[realm].verified:
            return None
        return entry

    def native_id(self, canonical_id: str, realm: str, require_verified: bool = True) -> int | str:
        mapping = self.entries[canonical_id].native[realm]
        if require_verified and not mapping.verified:
            raise UnverifiedNativeMapping(f'{canonical_id} in {realm} requires runtime verification')
        return mapping.identifier
