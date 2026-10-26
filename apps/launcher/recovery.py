"""Profiles & saves and the recovery center for the launcher (read-only), carried over from the retired Qt window.

Profiles come from the ProfileStore database opened with ``mode=ro`` (never migrated, never written). The recovery center turns what the
launcher already knows (game folders, mod loader, SlimeBridge plugin, extracted caches) into plain-language issues and can write a redacted
diagnostics bundle. Nothing here starts, stops or edits a game or a save.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from bridge.app.recovery import export_bundle, issues, summary
from bridge.app.saves import STATUS_TEXT, connect_readonly
from bridge.core import diagnostics as D
from bridge.counterpart.pair import save_pair

ROOT = Path(__file__).resolve().parents[2]
PROFILES_DB = ROOT / 'Lab' / 'Bridge' / 'profiles.sqlite3'
DIAGNOSTICS_DIR = ROOT / 'Lab' / 'Diagnostics'
REALM_NAMES = {'SR1': 'Slime Rancher', 'SR2': 'Slime Rancher 2'}
LOCAL_ADAPTERS = ('process', 'window', 'input', 'world_ready', 'counterpart creation', 'content registration', 'realm network')


def read_profiles(path=PROFILES_DB):
    """ProfileStore rows with their two endpoints (``mode=ro``): [] when the database does not exist yet."""
    path = Path(path)
    if not path.is_file():
        return []
    db = connect_readonly(path)
    db.row_factory = sqlite3.Row
    try:
        out = []
        for row in db.execute('SELECT * FROM profiles ORDER BY bridge_profile_id'):
            profile = dict(row)
            for realm in ('SR1', 'SR2'):
                endpoint = db.execute('SELECT installation_id, native_save_id, native_save_identity FROM endpoints '
                                      'WHERE bridge_profile_id=? AND realm=?', (row['bridge_profile_id'], realm)).fetchone()
                profile[realm.lower()] = dict(endpoint) if endpoint else None
            out.append(profile)
        return out
    finally:
        db.close()


def pairs(path=PROFILES_DB):
    """Save pairs for the Profiles page: label, status, both native saves."""
    out = []
    for profile in read_profiles(path):
        pair = save_pair(profile, [], {})
        label = (profile.get('sr1') or profile.get('sr2') or {}).get('native_save_id') or profile['bridge_profile_id'][:8]
        endpoints = {}
        for realm in ('SR1', 'SR2'):
            endpoint = pair['endpoints'].get(realm)
            endpoints[realm] = None if endpoint is None else {'save': endpoint['native_save_id'],
                                                              'compatibility': endpoint['compatibility']}
        out.append({'profile_id': pair['profile_id'], 'label': label, 'status': pair['status'],
                    'status_text': STATUS_TEXT[pair['status']], 'endpoints': endpoints})
    return out


def diagnostics(installations, settings_diagnostics=()):
    """Diagnostics from the launcher's own installation rows (``LauncherAPI._installations``) and the adapters still to wire."""
    items = list(settings_diagnostics)
    seen = {row['game'] for row in installations if row.get('role') != 'lab'}
    for realm in ('SR1', 'SR2'):
        if realm not in seen and not any(r['game'] == realm for r in installations):
            items.append(D.make('GAME_NOT_FOUND', 'warning', 'launcher', f'{REALM_NAMES[realm]} was not found: pick its folder on the Games page',
                                subject=realm, realm=realm))
    for row in installations:
        realm = row['game']
        if row['loader'] == 'Not configured':
            items.append(D.make('MOD_LOADER_MISSING', 'warning', 'launcher',
                                f'{REALM_NAMES[realm]} has no mod loader yet ({"SRML" if realm == "SR1" else "MelonLoader"}): install it from the Games page',
                                subject=realm, realm=realm))
        elif row.get('plugin_state') in ('missing', 'outdated'):
            items.append(D.make('BRIDGE_PLUGIN_NEEDED', 'info', 'launcher',
                                f'The SlimeBridge plugin is {row["plugin_state"]} in {REALM_NAMES[realm]} ({row["role"]} copy): Play installs it',
                                subject=realm, realm=realm))
    for adapter in LOCAL_ADAPTERS:
        items.append(D.make('LOCAL_ADAPTER_NOT_CONNECTED', 'info', 'launcher', f'{adapter} adapter is not connected yet (local integration step)',
                            subject=adapter, evidence='NEEDS_LOCAL_RUNTIME_PROOF'))
    return issues(items)


def recovery_state(installations, settings_diagnostics=()):
    found = diagnostics(installations, settings_diagnostics)
    grouped = {}
    for issue in found:
        grouped.setdefault(issue['category'], []).append({k: issue[k] for k in ('id', 'severity', 'minor', 'title', 'summary', 'subject', 'realm')})
    return {'summary': summary(found), 'groups': [{'category': c, 'issues': v} for c, v in sorted(grouped.items())], 'count': len(found)}


def export(installations, settings_diagnostics=(), directory=DIAGNOSTICS_DIR):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = export_bundle(diagnostics(installations, settings_diagnostics), directory, name='slimebridge-diagnostics',
                         context={'synthetic': False, 'launcher': 'pywebview'})
    return Path(path)
