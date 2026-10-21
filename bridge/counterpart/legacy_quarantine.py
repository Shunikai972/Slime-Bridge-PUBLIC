"""Exact quarantine of individually audited historical creation folders, plus retired creation IDs.

Quarantine is UNRESOLVED_LEGACY_INTENT_QUARANTINED: it never means success, finalization or
cleanup proof, and historical receipts are never rewritten. A pinned folder is accepted only if its
name, flat file inventory, sizes and SHA-256 digests match the pins exactly; any drift, reparse
point or unknown entry fails closed. The pin document itself is bound to ``PINS_SHA256``.

Retired creation IDs come from the audit union. They must never start, dispatch, reconcile or
accept a result again; matching is exact (no prefixes, no case folding).
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

STATUS = 'UNRESOLVED_LEGACY_INTENT_QUARANTINED'
PINS_PATH = Path(__file__).with_name('legacy_quarantine.json')
PINS_SHA256 = '1818b885103f9d1fbf9aced90464b72f809e161de79e35320cd53ecb8ba9c6e4'
# Duplicated deliberately: tampering with the JSON pin file can never un-retire an ID.
RETIRED_CREATION_IDS = frozenset({
    '07afb0b92e1b4461be1560b7da6b995d', '2e2a689bf0f7452eb63db918ffd4036c',
    '3209eadc2fd14b35982ac256f733091b', '56a077126cb24847b4e46f1216fb0ec4',
    '9f1fea2d47ef4594a540a5eb83805802', 'ae635bb84dde47e192ae0f2e8cfaa278',
    'b767b95d44d546cfa11bf567e46fdfb3', 'c5c48aa00cda4e378dc3a0375571f9e3',
    'ef9415e10fdf4b509039141770bd03d5',
})
_REPARSE = 0x400


class QuarantineError(RuntimeError):
    pass


class RetiredCreationError(ValueError):
    code = 'RETIRED_CREATION_ID'


def is_retired(creation_id) -> bool:
    return isinstance(creation_id, str) and creation_id in RETIRED_CREATION_IDS


def retired_endpoint_creation_id(profile, realm):
    """Known profile provenance cannot be erased by missing/NULL endpoint metadata.

    A recorded target scopes retirement to that endpoint, leaving the original source usable.
    Missing target provenance fails closed rather than guessing which endpoint is safe.
    """
    creation_id = profile.get('creation_id')
    if is_retired(creation_id) and profile.get('creation_target_realm') in (None, realm):
        return creation_id
    return None


def require_not_retired(creation_id) -> None:
    if is_retired(creation_id):
        raise RetiredCreationError(f'retired legacy creation id {creation_id} cannot be used ({STATUS})')


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


def _is_reparse(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & _REPARSE)


def load_pins(path: Path = PINS_PATH, expected_sha256: str = PINS_SHA256) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as ex:
        raise QuarantineError(f'quarantine pins unreadable: {ex}') from ex
    if (not isinstance(doc, dict) or doc.get('status') != STATUS or
            doc.get('schema') != 'slimebridge-legacy-creation-quarantine' or doc.get('version') != 1):
        raise QuarantineError('quarantine pins have an unexpected schema')
    folders, ids = doc.get('folders'), doc.get('retired_creation_ids')
    if not isinstance(folders, list) or not isinstance(ids, list):
        raise QuarantineError('quarantine pins have an unexpected schema')
    if _digest({'folders': folders, 'retired_creation_ids': ids}) != expected_sha256 or \
            doc.get('pins_sha256') != expected_sha256:
        raise QuarantineError('quarantine pins do not match the reviewed digest')
    names = [f['folder_name'] for f in folders]
    if len(set(names)) != len(names) or sorted({i for f in folders for i in f['creation_ids']}) != ids:
        raise QuarantineError('quarantine pins are internally inconsistent')
    return doc


class LegacyQuarantine:
    def __init__(self, pins: dict | None = None):
        self.pins = pins if pins is not None else load_pins()
        self.by_name = {f['folder_name']: f for f in self.pins['folders']}
        if expected := self.pins['retired_creation_ids']:
            if pins is None and frozenset(expected) != RETIRED_CREATION_IDS:
                raise QuarantineError('retired creation IDs drifted from the reviewed set')

    def is_pinned(self, name: str) -> bool:
        return name in self.by_name

    def verify_folder(self, folder: Path) -> dict:
        """Fail closed unless ``folder`` is byte-for-byte the audited, pinned evidence."""
        folder = Path(folder)
        pin = self.by_name.get(folder.name)
        if pin is None:
            raise QuarantineError(f'unknown legacy creation folder: {folder.name}')
        try:
            st = os.lstat(folder)
        except OSError as ex:
            raise QuarantineError(f'pinned legacy folder missing: {folder.name}') from ex
        if _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
            raise QuarantineError(f'pinned legacy folder is not a plain directory: {folder.name}')
        expected = {f['path']: f for f in pin['files']}
        seen = {}
        with os.scandir(folder) as entries:
            for entry in entries:
                est = entry.stat(follow_symlinks=False)
                if _is_reparse(est) or not stat.S_ISREG(est.st_mode):
                    raise QuarantineError(f'unexpected non-file entry in {folder.name}: {entry.name}')
                seen[entry.name] = est
        if set(seen) != set(expected):
            raise QuarantineError(f'inventory drift in {folder.name}: '
                                  f'extra={sorted(set(seen) - set(expected))} missing={sorted(set(expected) - set(seen))}')
        for name, pinned in sorted(expected.items()):
            data = (folder / name).read_bytes()
            if len(data) != pinned['bytes'] or seen[name].st_size != pinned['bytes'] or \
                    hashlib.sha256(data).hexdigest() != pinned['sha256']:
                raise QuarantineError(f'pinned bytes changed: {folder.name}/{name}')
        return {'folder_name': folder.name, 'status': STATUS, 'creation_ids': list(pin['creation_ids'])}

    def verify_root(self, root: Path, *, require_all: bool) -> list:
        """Verify every pinned folder under ``root``; a partial set is drift (move/rename/delete)."""
        root = Path(root)
        present = sorted(name for name in self.by_name if os.path.lexists(root / name))
        # The audited historical set is immutable, including when every folder disappears.
        # require_all is retained for existing readers; False is not a clean-clone bypass.
        if present != sorted(self.by_name):
            raise QuarantineError(f'pinned legacy folders missing: {sorted(set(self.by_name) - set(present))}')
        return [self.verify_folder(root / name) for name in present]


def creation_id_pattern_ok(creation_id) -> bool:
    return isinstance(creation_id, str) and re.fullmatch(r'[0-9a-f]{32}', creation_id) is not None
