"""Read the asset paths ("internal ids") of an Addressables 2.x binary catalog (``catalog.bin``, Slime Rancher 2 1.x).

SR2 0.6 shipped ``catalog.json`` with a plain ``m_InternalIds`` list; 1.x (Unity 6, Addressables 2) ships ``catalog.bin``,
a ``BinaryStorageBuffer``. In it a string is a buffer with its byte size in the 4 bytes before it (bit 31 of a string id
marks UTF-16), and an asset path is a *dynamic string* (bit 30 of the id): a linked list of ``(part id, next id)`` nodes,
stored last segment first and joined with ``/``. Only what the catalog scan needs is decoded: every dynamic string that
resolves to a full ``Assets/...`` path. Read-only, no game code, nothing executed.
"""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path

import numpy as np

MAGIC = b'B\x89\xe3\r'
DYNAMIC, UNICODE, MASK, NONE = 0x40000000, 0x80000000, 0x3FFFFFFF, 0xFFFFFFFF
MAX_PARTS = 64


class CatalogFormatError(ValueError):
    pass


def _decoder(data: bytes):
    size = len(data)

    def text(sid: int) -> str:
        offset = sid & MASK
        if offset < 4 or offset > size:
            raise CatalogFormatError('string offset')
        length = struct.unpack_from('<I', data, offset - 4)[0]
        if length > size - offset or length > 4096:
            raise CatalogFormatError('string size')
        raw = data[offset:offset + length]
        value = raw.decode('utf-16-le') if sid & UNICODE else raw.decode('ascii')
        if not value or not value.isprintable():
            raise CatalogFormatError('not text')
        return value

    def dynamic(sid: int) -> str:
        parts, node = [], sid & MASK
        for _ in range(MAX_PARTS):
            if node + 8 > size:
                raise CatalogFormatError('node offset')
            part, nxt = struct.unpack_from('<II', data, node)
            if part & DYNAMIC and not part & UNICODE:
                raise CatalogFormatError('nested dynamic string')
            parts.append(text(part))
            if nxt == NONE:
                return '/'.join(reversed(parts))
            node = nxt & MASK
        raise CatalogFormatError('too many parts')

    return dynamic


@lru_cache(maxsize=4)
def _internal_ids(path: str, mtime_ns: int, length: int) -> tuple[str, ...]:
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise CatalogFormatError('not an Addressables binary catalog')
    dynamic = _decoder(data)
    candidates = set()
    raw = np.frombuffer(data, dtype=np.uint8)
    for shift in range(4):                     # string ids are not 4-byte aligned in the buffer
        usable = (len(raw) - shift) // 4 * 4
        values = raw[shift:shift + usable].view('<u4')
        hits = np.nonzero(((values & 0xC0000000) == DYNAMIC) & ((values & MASK) < len(data) - 8))[0]
        candidates.update(int(v) for v in np.unique(values[hits]))
    paths = set()
    for sid in candidates:
        try:
            value = dynamic(sid)
        except (CatalogFormatError, UnicodeDecodeError, struct.error):
            continue
        if value.startswith('Assets/') and '.' in value.rsplit('/', 1)[-1]:
            paths.add(value)
    if not paths:
        raise CatalogFormatError('no asset paths decoded')
    return tuple(sorted(paths))


def internal_ids(path) -> list[str]:
    """Sorted unique asset paths of a ``catalog.bin`` (cached per file version)."""
    path = Path(path)
    stat = path.stat()
    return list(_internal_ids(str(path.resolve()), stat.st_mtime_ns, stat.st_size))


def catalog_path(aa_dir) -> Path:
    """``catalog.json`` (SR2 0.6) or ``catalog.bin`` (SR2 1.x) inside a ``StreamingAssets/aa`` folder."""
    aa_dir = Path(aa_dir)
    for name in ('catalog.json', 'catalog.bin'):
        if (aa_dir / name).is_file():
            return aa_dir / name
    return aa_dir / 'catalog.json'
