"""Profile/pair authority for the existing inventory ledger (not another transaction engine).

Bindings snapshot ownership, not live game readiness. The host must separately attest each
installation/native identity and process session before any native command is delivered.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from bridge.counterpart.journal import CounterpartJournal, JournalError
from bridge.counterpart.pair import save_pair
from bridge.profile.store import ProfileStore

BINDING_COLUMNS = ('bridge_profile_id', 'sender_installation_id', 'sender_native_save_identity',
                   'receiver_installation_id', 'receiver_native_save_identity')


class TransferBindingError(ValueError):
    """No verified ownership authority; leave the ledger and native inventory unchanged."""


def _identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise TransferBindingError('profile/endpoint identity must be a non-empty string of at most 128 characters')


@dataclass(frozen=True)
class TransferBinding:
    bridge_profile_id: str
    sender_realm: str
    sender_save_id: str
    sender_installation_id: str
    sender_native_save_identity: str
    receiver_realm: str
    receiver_save_id: str
    receiver_installation_id: str
    receiver_native_save_identity: str

    def __post_init__(self):
        if self.sender_realm not in ('SR1', 'SR2') or self.receiver_realm not in ('SR1', 'SR2') or \
                self.sender_realm == self.receiver_realm:
            raise TransferBindingError('binding requires two distinct supported realms')
        for value in (self.bridge_profile_id, self.sender_save_id, self.sender_installation_id,
                      self.sender_native_save_identity, self.receiver_save_id,
                      self.receiver_installation_id, self.receiver_native_save_identity):
            _identity(value)

    @property
    def ledger_values(self):
        return tuple(getattr(self, column) for column in BINDING_COLUMNS)

    def require_arguments(self, sender, sender_save, receiver, receiver_save):
        if (sender, sender_save, receiver, receiver_save) != \
                (self.sender_realm, self.sender_save_id, self.receiver_realm, self.receiver_save_id):
            raise TransferBindingError('save arguments differ from the resolved profile/pair')

    def endpoint(self, realm):
        if realm not in (self.sender_realm, self.receiver_realm):
            raise TransferBindingError('realm is not an endpoint of this binding')
        prefix = 'sender' if realm == self.sender_realm else 'receiver'
        return {'realm': realm, 'installation_id': getattr(self, prefix + '_installation_id'),
                'native_save_id': getattr(self, prefix + '_save_id'),
                'native_save_identity': getattr(self, prefix + '_native_save_identity')}


def binding_for_row(row):
    if row is None or any(getattr(row, column, None) is None for column in BINDING_COLUMNS):
        raise TransferBindingError('legacy/unbound transfer: independent ownership reconciliation required')
    return TransferBinding(row.bridge_profile_id, row.sender_realm, row.sender_save_id,
                           row.sender_installation_id, row.sender_native_save_identity,
                           row.receiver_realm, row.receiver_save_id,
                           row.receiver_installation_id, row.receiver_native_save_identity)


class TransferBindingResolver:
    """Read fresh authority on every call, including from HTTP worker threads.

    Connections are opened read-only per call. Resolving never creates/migrates either store,
    mutates a link, verifies an endpoint, or selects whichever profile happens to be active.
    """
    def __init__(self, profiles_path, journal_path, *, current_builds, supported_builds=None):
        if not callable(current_builds):
            raise ValueError('current build provider is required')
        self.profiles_path = Path(profiles_path)
        self.journal_path = Path(journal_path)
        self.current_builds = current_builds
        self.supported_builds = supported_builds

    def resolve(self, profile_id, sender, sender_save, receiver, receiver_save):
        _identity(profile_id)
        if sender not in ('SR1', 'SR2') or receiver not in ('SR1', 'SR2') or sender == receiver:
            raise TransferBindingError('transfer requires two distinct supported realms')
        try:
            with ProfileStore(self.profiles_path, read_only=True) as profiles:
                profile = profiles.get(profile_id)
            with CounterpartJournal(self.journal_path, read_only=True) as journal:
                intents, metadata = journal.pair_records(profile_id)
            pair = save_pair(profile, intents, metadata, current_builds=self.current_builds(),
                             supported_builds=self.supported_builds)
        except (KeyError, sqlite3.Error, JournalError) as exc:
            raise TransferBindingError('profile/pair authority unavailable') from exc
        if pair['status'] != 'LINKED' or pair['pending'] is not None:
            raise TransferBindingError('profile/pair is not linked, verified and free of pending creation')
        source, destination = pair['endpoints'][sender], pair['endpoints'][receiver]
        binding = TransferBinding(profile_id, sender, source['native_save_id'], source['installation_id'],
                                  source['native_save_identity'], receiver, destination['native_save_id'],
                                  destination['installation_id'], destination['native_save_identity'])
        binding.require_arguments(sender, sender_save, receiver, receiver_save)
        return binding

    def validate_row(self, row):
        recorded = binding_for_row(row)
        current = self.resolve(recorded.bridge_profile_id, recorded.sender_realm, recorded.sender_save_id,
                               recorded.receiver_realm, recorded.receiver_save_id)
        if current != recorded:
            raise TransferBindingError('current pair differs from the durable transfer ownership snapshot')
        return recorded
