"""Real live-endpoint attestation for the protected LAB transfer path.

The reviewed host accepts a local ``bridge-world-ready`` report only from a provider the local
controller trusts for that realm (``endpoint_trust``). Unit tests use a synthetic fixture; this
module is the real provider: every reported field is grounded in an observation of the owned LAB
child process, its live plugin heartbeat, the verified LAB installation and the linked pair that
was recorded by the creation flow. Any missing or mismatched observation makes the provider
return ``None`` and host validation fails closed.

Evidence grounds (this module never upgrades them):
  process_running        - the owned child handle is still alive and its image is the verified
                           LAB executable for that realm (independent OS probe).
  load_callback_returned - the plugin heartbeat session for this realm exists at call time; the
                           host only delivers heartbeats under the realm token after boot.
  scene_settled          - the main-thread status sampler reports a loaded save (``save_id``).
  player_usable          - the same sampled status reports ``player_alive``.
  bridge_synchronized    - the heartbeat is live in the host's own freshness window (the public
                           ``heartbeats()`` view only exposes sessions younger than the host's
                           ``HEARTBEAT_STALE_AFTER``).
  native_save_id         - the live heartbeat ``save_id`` equals the linked endpoint's recorded
                           save. A different loaded save can never be attested as the pair.
  native_save_identity   - the linked endpoint's recorded Bridge identity for that save; the
                           live check above is what ties it to this process.
  installation_id        - derived from the verified LAB executable path (same formula as the
                           creation flow) and required to equal the recorded endpoint.
  build_fingerprint      - canonical hash of the LAB manifest key files computed once per run;
                           the resolver independently refuses pairs recorded under other builds.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.lab.paths import refuse_reparse
from tools.lab.process_guard import same_image

PROVIDER_PREFIX = 'slimebridge.lab-live'
ENDPOINT_FIELDS = ('installation_id', 'native_save_id', 'native_save_identity')


def lab_installation_id(realm: str) -> str:
    """Same formula as ``tools.lab.creation_oracle.installation_id`` (verified LAB path)."""
    from tools.lab import oracle
    path = str(oracle.GAMES[realm].resolve()).casefold()
    return realm.lower() + '-lab-' + hashlib.sha256(path.encode()).hexdigest()


def canonical_build_fingerprints(realms) -> dict:
    """One canonical build string per realm from the LAB manifest key-file hashes."""
    from tools.lab import oracle
    fingerprints = oracle.lab_fingerprints(list(realms))
    out = {}
    for realm, files in fingerprints.items():
        payload = json.dumps(files, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        out[realm] = 'lab-files-sha256:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return out


def read_linked_pair(profiles_path, profile_id: str) -> dict:
    """Read-only preflight of a linked profile; shared by the oracle and the transfer tooling.

    Returns ``{'profile': {...}, 'endpoints': {realm: {realm, installation_id, native_save_id,
    native_save_identity}}}``. Raises ValueError for a missing, unlinked or incomplete pair so
    callers can fail before any filesystem side effect.
    """
    from bridge.profile.store import ProfileStore
    try:
        path = Path(profiles_path)
    except TypeError as exc:
        raise ValueError('linked profile database path is invalid') from exc
    try:
        with ProfileStore(path, read_only=True) as profiles:
            profile = profiles.get(profile_id)
    except KeyError as exc:
        raise ValueError(f'linked profile {profile_id} does not exist') from exc
    if profile.get('state') != 'LINKED':
        raise ValueError(f'linked profile {profile_id} is not LINKED')
    endpoints = {}
    for realm in ('SR1', 'SR2'):
        endpoint = profile[realm.lower()]
        if (not isinstance(endpoint, dict) or
                any(not isinstance(endpoint.get(key), str) or not endpoint[key]
                    for key in ENDPOINT_FIELDS)):
            raise ValueError(f'linked profile {profile_id} is missing the {realm} endpoint')
        endpoints[realm] = {'realm': realm, **{key: endpoint[key] for key in ENDPOINT_FIELDS}}
    return {'profile': profile, 'endpoints': endpoints}


def validate_run_write_paths(transfer_authority: dict) -> None:
    """journal/store and their SQLite-derived sidecars must never be redirected file targets.

    Reparse points redirect path *resolution*; a hard link is not a reparse point but still
    makes an existing in-run-dir name refer to file content outside the evidence directory.
    SQLite creates derived sidecar files (``-wal``, ``-shm``, ``-journal``) for a database
    path, so those names are examined too. Call this immediately before anything opens the
    databases, not only at parameter preflight: a link inserted while games are loading would
    otherwise be followed by the later open.
    """
    for key in ('journal_path', 'store_path'):
        try:
            primary = Path(transfer_authority[key])
        except TypeError as exc:
            raise ValueError(f'transfer authority {key} is invalid') from exc
        for candidate in (primary, Path(str(primary) + '-wal'), Path(str(primary) + '-shm'),
                          Path(str(primary) + '-journal')):
            try:
                refuse_reparse(candidate)
            except ValueError as exc:
                raise ValueError(f'transfer authority {key} must not traverse reparse points') from exc
            if candidate.exists() and candidate.lstat().st_nlink > 1:
                raise ValueError(f'transfer authority {key} must not be a hardlinked file')


class LabRuntimeHandles:
    """Mutable handles the oracle fills once the owned LAB children are launched.

    The provider is constructed before launch (the host needs it at construction) but must only
    attest live state, so it reads these lazily on every call and fails closed while unset.
    """

    def __init__(self):
        self.procs = None
        self.host = None


class LiveEndpointAttestation:
    """``endpoint_provider`` for the real oracle path; trust it only for local LAB runs."""

    PROVIDER_VERSION = '1.0.0'

    def __init__(self, handles, endpoints, builds, *, installation_ids=lab_installation_id,
                 game_paths=None, image_probe=same_image):
        if not isinstance(handles, LabRuntimeHandles):
            raise ValueError('live endpoint attestation requires the oracle runtime handles')
        if not callable(installation_ids):
            raise ValueError('installation id provider must be callable')
        if not callable(image_probe):
            raise ValueError('image probe must be callable')
        self.handles = handles
        self.endpoints = {realm: dict(endpoint) for realm, endpoint in (endpoints or {}).items()}
        self.builds = dict(builds or {})
        self.installation_ids = installation_ids
        self.game_paths = dict(game_paths) if game_paths is not None else None
        self.image_probe = image_probe

    def provider_id(self, realm: str) -> str:
        return f'{PROVIDER_PREFIX}.{realm}'

    def trust(self) -> dict:
        """Provider identity map for ``BridgeHost(endpoint_trust=...)``."""
        return {self.provider_id(realm): realm for realm in ('SR1', 'SR2')}

    def __call__(self, realm, session):
        try:
            return self._report(realm, session)
        except Exception:
            return None

    def _game_path(self, realm):
        if self.game_paths is not None:
            return self.game_paths[realm]
        from tools.lab import oracle
        return oracle.GAMES[realm]

    def _report(self, realm, session):
        if realm not in ('SR1', 'SR2') or not isinstance(session, tuple) or len(session) != 2:
            return None
        pid, epoch = session
        if type(pid) is not int or pid <= 0:
            return None
        endpoint = self.endpoints.get(realm)
        build = self.builds.get(realm)
        if (not isinstance(endpoint, dict) or not isinstance(build, str) or not build or
                any(not isinstance(endpoint.get(field), str) or not endpoint[field]
                    for field in ENDPOINT_FIELDS)):
            return None
        procs, host = self.handles.procs, self.handles.host
        if not isinstance(procs, dict) or host is None:
            return None
        proc = procs.get(realm)
        if proc is None or proc.poll() is not None or proc.pid != pid:
            return None
        if not self.image_probe(pid, self._game_path(realm)):
            return None
        beat = (host.heartbeats() or {}).get(realm)
        if not isinstance(beat, dict) or beat.get('pid') != pid:
            return None
        status = beat.get('status')
        if (not isinstance(status, dict) or status.get('player_alive') is not True or
                status.get('save_id') != endpoint['native_save_id']):
            return None
        if self.installation_ids(realm) != endpoint['installation_id']:
            return None
        observed = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return {
            'schema': 'bridge-world-ready', 'schema_version': 1, 'realm': realm,
            'installation_id': endpoint['installation_id'],
            'native_save_id': endpoint['native_save_id'],
            'native_save_identity': endpoint['native_save_identity'],
            'session': {'pid': pid, 'session_id': str(epoch)},
            'build_fingerprint': build,
            'process_running': True, 'load_callback_returned': True,
            'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True,
            'observed_at': observed,
            'provider': {'provider_id': self.provider_id(realm),
                         'provider_version': self.PROVIDER_VERSION},
            'evidence': 'RUNTIME_OBSERVATION',
        }
