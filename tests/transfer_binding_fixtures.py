"""Explicit synthetic fixtures for profile/pair and local endpoint-provider contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal
from bridge.profile.store import ProfileStore
from bridge.transfer.binding import TransferBinding, TransferBindingResolver


class PairFixture:
    def __init__(self, root):
        root = Path(root)
        self.profiles_path = root / 'synthetic-profiles.sqlite3'
        self.journal_path = root / 'synthetic-counterpart.sqlite3'
        self.builds = {'SR1': 'synthetic-build-SR1', 'SR2': 'synthetic-build-SR2'}
        self.loaded = {}
        self.trust = {'unit-only.SR1': 'SR1', 'unit-only.SR2': 'SR2'}
        with ProfileStore(self.profiles_path), CounterpartJournal(self.journal_path):
            pass
        self.resolver = TransferBindingResolver(self.profiles_path, self.journal_path,
                                                current_builds=lambda: dict(self.builds))

    def linked(self, suffix='1', sender_save='a', receiver_save='b'):
        with ProfileStore(self.profiles_path) as store:
            profile = store.find_or_create('SR1', 'synthetic-install-SR1-' + suffix,
                                           sender_save, 'synthetic-native-SR1-' + suffix)
            pid = profile['bridge_profile_id']
            cid = store.reserve_counterpart(pid, 'SR2')['creation_id']
            store.claim_creation(pid, cid)
            profile = store.confirm_counterpart(pid, cid, 'synthetic-install-SR2-' + suffix,
                                                receiver_save, 'synthetic-native-SR2-' + suffix)
        with CounterpartJournal(self.journal_path) as journal:
            for realm in ('SR1', 'SR2'):
                journal.observe_live_endpoint(pid, {'realm': realm, **profile[realm.lower()]}, self.builds[realm])
        return pid

    def load(self, profile_id, realm=None):
        with ProfileStore(self.profiles_path, read_only=True) as store:
            profile = store.get(profile_id)
        for key in (('SR1', 'SR2') if realm is None else (realm,)):
            self.loaded[key] = {'realm': key, **profile[key.lower()]}

    def report(self, realm, session):
        endpoint = self.loaded.get(realm)
        if endpoint is None:
            return None
        # Deliberately a fake trusted provider for unit tests. Never installed in a LAB host.
        return {'schema': 'bridge-world-ready', 'schema_version': 1, **endpoint,
                'session': {'pid': session[0], 'session_id': str(session[1])},
                'build_fingerprint': self.builds[realm], 'process_running': True,
                'load_callback_returned': True, 'scene_settled': True, 'player_usable': True,
                'bridge_synchronized': True, 'observed_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                'provider': {'provider_id': 'unit-only.' + realm, 'provider_version': '1'},
                'evidence': 'RUNTIME_OBSERVATION'}


def synthetic_binding(sender='SR1', sender_save='a', receiver='SR2', receiver_save='b'):
    """Structural binding for isolated ledger tests only; not verified native/profile evidence."""
    return TransferBinding('unit-only-profile', sender, sender_save, 'unit-install-' + sender,
                           'unit-native-' + sender_save, receiver, receiver_save,
                           'unit-install-' + receiver, 'unit-native-' + receiver_save)
