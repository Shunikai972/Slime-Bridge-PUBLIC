"""Read-only list of the native save groups of each game (the launcher never writes, moves or deletes a save).

A "save group" is the native game name the plugins report as ``save_id``: files are ``<group>_<n>.sav`` (``n`` = autosave number).
Slime Rancher keeps them in ``LocalLow/Monomi Park/Slime Rancher``; Slime Rancher 2 in ``LocalLow/MonomiPark/SlimeRancher2/<store>/<id>``.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

SAVE_FILE = re.compile(r'^(?P<group>[A-Za-z0-9_-]{1,128}?)_(?P<n>\d+)\.sav$')
STAMP = re.compile(r'^(?P<stamp>\d{14})_(?P<name>.+)$')
LIMIT = 40


def default_roots(home=None):
    low = Path(home or os.environ.get('USERPROFILE') or Path.home()) / 'AppData' / 'LocalLow'
    return {'SR1': low / 'Monomi Park' / 'Slime Rancher', 'SR2': low / 'MonomiPark' / 'SlimeRancher2'}


def _label(group):
    match = STAMP.match(group)
    if not match:
        return group
    stamp = datetime.strptime(match['stamp'], '%Y%m%d%H%M%S')
    name = match['name']
    # SR2 groups look like <stamp>_<slot>; SR1 groups carry the name the player typed.
    return f"{name} ({stamp:%Y-%m-%d %H:%M})" if not name.isdigit() else f"{stamp:%Y-%m-%d %H:%M} (#{name})"


def list_groups(realm, roots=None):
    """Newest first: ``[{group, label, modified}]``; an absent or unreadable folder is just an empty list."""
    root = (roots or default_roots())[realm]
    if not root.is_dir():
        return []
    newest = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in ('unity', 'srml', 'backup', 'backups')]
        for name in files:
            match = SAVE_FILE.match(name)
            if not match:
                continue
            try:
                modified = (Path(base) / name).stat().st_mtime
            except OSError:
                continue
            group = match['group']
            newest[group] = max(newest.get(group, 0.0), modified)
    rows = [{'group': group, 'label': _label(group), 'modified': datetime.fromtimestamp(m, timezone.utc).isoformat(timespec='seconds')}
            for group, m in sorted(newest.items(), key=lambda item: item[1], reverse=True)]
    return rows[:LIMIT]
