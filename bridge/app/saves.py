"""Profiles & saves service: linked save pairs, counterpart state, safe repair actions.

Normal play is automatic (the counterpart orchestrator creates/reconciles on launch); this service
is what the PROFILES/SAVES pages read. Actions are *non-destructive by construction*: reconcile,
verify, re-verify and automatic creation all go through the durable counterpart journal. Anything
destructive (unlinking, abandoning a creation id, deleting a native save) is not executable from the
launcher: it is listed as an operator-only, local, explicitly confirmed procedure.
"""

import sqlite3
from pathlib import Path

from bridge.counterpart.journal import TERMINAL

STATUS_TEXT = {
    'SR1_ONLY': 'Only a Slime Rancher save so far; its Slime Rancher 2 partner is created automatically.',
    'SR2_ONLY': 'Only a Slime Rancher 2 save so far; its Slime Rancher partner is created automatically.',
    'PENDING_COUNTERPART': 'Creating the partner save...',
    'LINKED': 'Both saves are linked and verified.',
    'CONFLICTED': 'These saves need attention before they can be used together.',
    'REVERIFY_REQUIRED': 'A game update happened; the link is being re-checked.',
}
DESTRUCTIVE = (
    {'id': 'UNLINK_PAIR', 'label': 'Unlink these saves', 'destructive': True, 'local_only': True,
     'confirmation': 'Unlinking keeps both native saves but Bridge will treat them as unrelated. Type UNLINK to confirm.'},
    {'id': 'ABANDON_CREATION', 'label': 'Abandon a failed partner creation', 'destructive': True, 'local_only': True,
     'confirmation': 'Requires ProfileStore support that does not exist yet (not implemented yet). Nothing is deleted.'},
)


def connect_readonly(path):
    """SQLite connection that can never write or migrate: a percent-encoded ``file:`` URI with ``mode=ro``
    (a raw path would let '#', '?' or '%' in a folder name truncate or redirect the URI)."""
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


class ProfileCatalog:
    def profile_ids(self):  # pragma: no cover - interface
        raise NotImplementedError


class ProfileStoreCatalog(ProfileCatalog):
    """Lists profile ids from a ProfileStore database opened strictly read-only."""

    def __init__(self, path):
        self.path = Path(path)

    def profile_ids(self):
        if not self.path.is_file():
            return []
        db = connect_readonly(self.path)
        try:
            return [row[0] for row in db.execute('SELECT bridge_profile_id FROM profiles ORDER BY bridge_profile_id')]
        finally:
            db.close()


class StaticCatalog(ProfileCatalog):
    def __init__(self, ids):
        self._ids = list(ids)

    def profile_ids(self):
        return list(self._ids)


class SaveManager:
    """``environment(realm)`` -> {'health', 'slot_probe', 'runtime_proven', 'build'} (live facts)."""

    def __init__(self, service, catalog, environment, *, content_compatibility=None):
        self.service, self.catalog, self.environment = service, catalog, environment
        self.content_compatibility = content_compatibility or (lambda profile_id: 'UNKNOWN')

    def _builds(self):
        return {realm: self.environment(realm).get('build') for realm in ('SR1', 'SR2')}

    def pair(self, profile_id):
        pair = self.service.pair(profile_id, current_builds=self._builds(),
                                 content_compatibility=self.content_compatibility(profile_id))
        return {**pair, 'status_text': STATUS_TEXT[pair['status']]}

    def pairs(self):
        return [self.pair(pid) for pid in self.catalog.profile_ids()]

    def decision(self, profile_id, target_realm):
        env = self.environment(target_realm)
        return self.service.decide(profile_id, target_realm, provider_health=env.get('health', 'UNKNOWN'),
                                   slot_probe=env.get('slot_probe'), runtime_proven=env.get('runtime_proven', False),
                                   current_build=env.get('build'))

    def detail(self, profile_id):
        pair = self.pair(profile_id)
        intents = self.service.journal.intents_for(profile_id)
        missing = [realm for realm, endpoint in pair['endpoints'].items() if endpoint is None]
        target = missing[0] if len(missing) == 1 else None
        return {'pair': pair,
                'decision': self.decision(profile_id, target) if target else None,
                'creations': [{'creation_id': i['creation_id'], 'target_realm': i['target_realm'], 'phase': i['phase'],
                               'terminal': i['phase'] in TERMINAL, 'reconcile_rounds': i['reconcile_rounds'],
                               'timeline': self.service.journal.timeline(i['creation_id'])} for i in intents],
                'actions': self.actions(profile_id, pair=pair)}

    def actions(self, profile_id, *, pair=None):
        pair = pair or self.pair(profile_id)
        out = []
        pending = pair['pending']
        if pending and pending['phase'] == 'UNCERTAIN':
            out.append({'id': 'RECONCILE', 'label': 'Check whether the partner save was created',
                        'destructive': False, 'local_only': False, 'creation_id': pending['creation_id']})
        if pending and pending['phase'] in ('NATIVE_OBSERVED', 'READBACK_PENDING'):
            out.append({'id': 'VERIFY_CANDIDATE', 'label': 'Verify the new partner save', 'destructive': False,
                        'local_only': False, 'creation_id': pending['creation_id']})
        for realm, endpoint in pair['endpoints'].items():
            if endpoint and endpoint['compatibility'] in ('REVERIFY_REQUIRED', 'UNKNOWN'):
                out.append({'id': 'REVERIFY_ENDPOINT', 'label': f'Re-check the {realm} save after an update',
                            'destructive': False, 'local_only': False, 'realm': realm})
        if pair['status'] in ('SR1_ONLY', 'SR2_ONLY') and not pending:
            out.append({'id': 'CREATE_COUNTERPART', 'label': 'Create the partner save now', 'destructive': False,
                        'local_only': False, 'automatic': True})
        if pair['status'] == 'CONFLICTED':
            out.extend(dict(a) for a in DESTRUCTIVE)
        return out
