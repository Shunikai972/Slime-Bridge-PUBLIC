"""Launcher settings: schema, defaults, validation, versioned migration and atomic persistence.

Settings are user preferences, not secrets. Unknown keys are dropped with a diagnostic, invalid
values fall back to defaults with a diagnostic (a corrupt file never blocks the launcher and is
preserved beside the new one). Developer options live under ``advanced`` and are hidden by default.
"""

import json
import os
from pathlib import Path

from bridge.cache.fs import atomic_write, confined_root
from bridge.core import schema as S
from bridge.core.launcher_settings import (
    DEFAULTS, MAX_BYTES, MIGRATIONS, SECTIONS, SETTINGS_SCHEMA, SPEC, VERSION,
    _PATH, _copy, _migrate_v1, normalize,
)


def update(settings, section, changes):
    """New settings with ``changes`` applied to one section, or ValueError listing the problems."""
    if section not in SECTIONS:
        raise ValueError(f'unknown settings section {section!r}')
    candidate = _copy(settings)
    if isinstance(candidate[section], dict):
        unknown = set(changes) - set(DEFAULTS[section])
        if unknown:
            raise ValueError(f'unknown keys {sorted(unknown)}')
        candidate[section].update(changes)
    else:
        candidate[section] = changes
    problems = S.check(SPEC, candidate)
    if problems:
        raise ValueError('; '.join(problems[:5]))
    return candidate


class SettingsStore:
    def __init__(self, root, name='settings.json'):
        self.root = confined_root(root)
        self.path = self.root / name

    def load(self):
        if not self.path.is_file() or self.path.is_symlink():
            return _copy(DEFAULTS), []
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError('settings file too large')
            doc = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError, RecursionError):
            doc = None
        settings, diags = normalize(doc)
        if doc is None:
            # Keep the unreadable file for inspection by renaming it (bounded, no copy into memory).
            os.replace(self.path, self.path.with_name(self.path.name + '.corrupt'))
        return settings, diags

    def save(self, settings):
        problems = S.check(SPEC, settings)
        if problems:
            raise ValueError('; '.join(problems[:5]))
        atomic_write(self.root, self.path, (json.dumps(settings, indent=2, sort_keys=True) + '\n').encode())


def visible_sections(settings):
    """Sections the settings page shows; ``advanced`` only when developer tools are enabled."""
    return [s for s in SECTIONS if s != 'advanced' or settings['advanced']['show_developer_tools']]


def default_root():
    return Path(__file__).resolve().parents[2] / 'Lab' / 'Launcher'
