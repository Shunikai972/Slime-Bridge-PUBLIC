"""Live console of both games for the launcher: new lines of SR1's Player.log, SR2's MelonLoader log and the launcher's own
crossing steps, read incrementally (each call returns only what was written since the previous one). Read-only.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

MAX_LINES = 400
MAX_BYTES = 256 * 1024
ERROR = re.compile(r'exception|error|failed|refused|\bcrash', re.IGNORECASE)
BRIDGE = re.compile(r'slimebridge|travel|portal|sanctuary|bridgehost', re.IGNORECASE)


def sr1_log() -> Path:
    base = Path(os.environ.get('USERPROFILE') or Path.home())
    return base / 'AppData' / 'LocalLow' / 'Monomi Park' / 'Slime Rancher' / 'Player.log'


def sr2_log(sr2_root) -> Path | None:
    return Path(sr2_root) / 'MelonLoader' / 'Latest.log' if sr2_root else None


class Console:
    def __init__(self, clock=time.time):
        self._clock = clock
        self._offsets: dict[str, tuple[int, float]] = {}
        self._history_seen: set[tuple] = set()

    def _read_new(self, source: str, path: Path | None):
        if path is None or not path.is_file():
            return []
        key = str(path)
        size, mtime = path.stat().st_size, path.stat().st_mtime
        offset, seen_mtime = self._offsets.get(key, (None, 0.0))
        if offset is None:
            offset = max(0, size - 16 * 1024)                     # first look: only the last few screens
        if size < offset:
            offset = 0                                            # the game restarted and rewrote its log
        if size == offset:
            self._offsets[key] = (offset, mtime)
            return []
        with path.open('rb') as f:
            f.seek(offset)
            data = f.read(min(MAX_BYTES, size - offset))
        end = data.rfind(b'\n')
        if end < 0:
            return []
        self._offsets[key] = (offset + end + 1, mtime)
        lines = data[:end].decode('utf-8', errors='replace').splitlines()
        return [self._entry(source, line) for line in lines if line.strip()][-MAX_LINES:]

    def _entry(self, source, text):
        kind = 'error' if ERROR.search(text) else ('bridge' if BRIDGE.search(text) else 'game')
        return {'source': source, 'text': text[:600], 'kind': kind}

    def poll(self, sr2_root=None, transitions=()):
        entries = self._read_new('SR1', sr1_log()) + self._read_new('SR2', sr2_log(sr2_root))
        for transition in transitions:
            for step in transition.get('history', []):
                key = (transition.get('id'), step.get('at'), step.get('text'))
                if key in self._history_seen:
                    continue
                self._history_seen.add(key)
                text = f"{transition.get('from')} -> {transition.get('to')}: {step.get('text')}"
                entries.append({'source': 'LAUNCHER', 'text': text, 'kind': 'error' if ERROR.search(text) or 'ROLLED_BACK' in text else 'bridge'})
        return entries[-MAX_LINES:]
