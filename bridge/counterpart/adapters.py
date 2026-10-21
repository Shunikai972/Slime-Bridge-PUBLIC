"""Realm counterpart adapter contract + a generic adapter over the existing plugin wire format.

A ``RealmCounterpartAdapter`` owns every native detail for one realm: how a creation id becomes a
native provenance marker, how a request becomes the plugin command, how a plugin reply becomes a
realm-neutral ``bridge-counterpart-result``, and which identity a creation must have. Generic code
asks the adapter; it never builds markers or filenames itself.

``WireCounterpartAdapter`` speaks the command/result JSON that both existing plugins already use
(``mods/shared/BridgeCommands.cs``: op_id/kind/creation_id commands; ok/applied/uncertain/
persisted/verified/can_create/save_id/save_file/sha256/reason results). It is configured per realm
with a ``RealmConvention`` (see ``bridge.counterpart.realms``). Ambiguous replies map to UNCERTAIN,
never to success or to a definitive absence.
"""

import re

from bridge.core import diagnostics as D
from bridge.sdk.providers import descriptor
from .model import make_result, validate_result

COMMAND_KINDS = {'create': 'create_save', 'reconcile': 'reconcile_save'}


class RealmConvention:
    """Native naming/provenance facts for one realm, each with the evidence it rests on."""

    def __init__(self, realm, *, marker_prefix, identity_prefix, filename_pattern, slot_semantics, evidence, sources,
                 local_queries=(), features=()):
        self.realm, self.marker_prefix, self.identity_prefix = realm, marker_prefix, identity_prefix
        self.filename_pattern = filename_pattern
        self.slot_semantics, self.evidence, self.sources = slot_semantics, evidence, tuple(sources)
        self.local_queries, self.features = tuple(local_queries), tuple(features)

    def native_save_id(self, creation_id):
        return self.marker_prefix + creation_id

    def native_identity(self, creation_id):
        """The Bridge-side identity string recorded for this realm's creation (ProfileStore endpoint)."""
        return self.identity_prefix + creation_id

    def filename_ok(self, creation_id, filename):
        if filename is None:
            return True
        pattern = self.filename_pattern.replace('{save_id}', re.escape(self.native_save_id(creation_id)))
        return re.fullmatch(pattern, filename) is not None


class WireCounterpartAdapter:
    """Generic adapter for the shared plugin wire protocol (realm facts come from the convention)."""

    def __init__(self, convention, provider_id, provider_version='1.0.0'):
        self.convention = convention
        self.realm = convention.realm
        self.provider = {'provider_id': provider_id, 'provider_version': provider_version}

    def describe(self):
        return descriptor(self.provider['provider_id'], self.provider['provider_version'], ['counterpart'],
                          realm=self.realm, features={'counterpart': list(self.convention.features)},
                          evidence=self.convention.evidence, local_queries=self.convention.local_queries)

    def health(self):
        return {'status': 'UNKNOWN', 'diagnostics': []}

    def describe_slots(self, probe):
        """Map an adapter-specific slot probe to generic FREE/OCCUPIED/UNKNOWN/CONFLICT (no fixed counts)."""
        if not isinstance(probe, dict) or probe.get('complete') is not True:
            return 'UNKNOWN'
        free = probe.get('free_slots')
        if not isinstance(free, list):
            return 'UNKNOWN'
        return 'FREE' if free else 'OCCUPIED'

    def expected_identity(self, creation_id):
        return {'realm': self.realm, 'native_save_id': self.convention.native_save_id(creation_id)}

    def build_command(self, request, operation_kind, operation_id):
        if operation_kind not in COMMAND_KINDS or request['target_realm'] != self.realm:
            raise ValueError('command for another realm or unknown operation')
        return {'op_id': operation_id, 'kind': COMMAND_KINDS[operation_kind], 'creation_id': request['creation_id']}

    def interpret_result(self, request, operation_kind, operation_id, wire, *, session):
        """Realm-neutral result from a plugin reply; raises ValueError for replies that do not bind."""
        creation_id = request['creation_id']
        expected = self.convention.native_save_id(creation_id)
        if (not isinstance(wire, dict) or wire.get('realm') != self.realm or wire.get('op_id') != operation_id or
                wire.get('kind') != COMMAND_KINDS[operation_kind] or wire.get('creation_id') != creation_id or
                type(wire.get('ok')) is not bool or type(wire.get('pid')) is not int or
                wire.get('pid') != session['pid']):
            raise ValueError('plugin reply does not bind to this operation, creation, realm or process')
        for key in ('save_id', 'save_file', 'sha256', 'reason'):
            if wire.get(key) is not None and not isinstance(wire[key], str):
                raise ValueError(f'malformed {key} in plugin reply')
        for key in ('applied', 'uncertain', 'persisted', 'can_create'):
            if wire.get(key) is not None and type(wire[key]) is not bool:
                raise ValueError(f'malformed {key} flag in plugin reply')
        save_id = wire.get('save_id') or None
        if save_id not in (None, expected):
            raise ValueError('plugin reply names a save with foreign provenance')
        verified = wire.get('verified')
        if verified is not None and type(verified) is not bool:
            raise ValueError('malformed verified flag')
        filename = wire.get('save_file') or None
        digest = wire.get('sha256') or None
        diags = []
        if wire.get('reason'):
            diags.append(D.make('NATIVE_REASON', 'info', 'counterpart', str(wire['reason'])[:256], realm=self.realm))
        exists = wire['ok'] is True and verified is True and save_id == expected
        if exists and not self.convention.filename_ok(creation_id, filename):
            raise ValueError('verified reply has a filename outside the realm convention')
        if exists:
            if (not isinstance(filename, str) or not filename or
                    not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None or
                    wire.get('uncertain') is not False or type(wire.get('applied')) is not bool or
                    type(wire.get('persisted')) is not bool or
                    ((wire['applied'] or self.realm == 'SR2') and wire['persisted'] is not True) or
                    (operation_kind == 'reconcile' and wire['applied'] is not False)):
                raise ValueError('verified reply lacks complete, coherent native readback proof')
            outcome = 'CREATED' if (operation_kind == 'create' and wire.get('applied') is True) else 'ALREADY_EXISTS'
            effect, evidence = 'CONFIRMED', 'NATIVE_READBACK'
        elif operation_kind == 'reconcile' and wire['ok'] is True and wire.get('can_create') is True and \
                verified is False and wire.get('uncertain') is False:
            if (wire.get('applied') is not False or wire.get('persisted') is not False or
                    type(wire.get('before')) is not int or type(wire.get('after')) is not int or
                    wire['before'] != 0 or wire['after'] != 0 or
                    wire.get('save_file') not in (None, '') or wire.get('sha256') not in (None, '')):
                raise ValueError('native absence proof is incomplete or contradicts native effects')
            outcome, effect, evidence = 'ABSENT', 'NONE', 'NATIVE_READBACK'
        elif wire['ok'] is False and wire.get('uncertain') is False and wire.get('applied') is False and \
                operation_kind == 'create' and wire.get('persisted') is False:
            # Refused before any native call (e.g. not at an idle menu). The wire format cannot say
            # *why* (capacity vs readiness), so this stays a generic no-effect failure.
            outcome, effect, evidence = 'FAILED', 'NONE', 'NONE'
        else:
            outcome, effect, evidence = 'UNCERTAIN', 'POSSIBLE', 'CALLBACK_ONLY' if wire['ok'] else 'NONE'
        result = make_result(creation_id=creation_id, operation_id=operation_id, operation_kind=operation_kind,
                             profile_id=request['profile_id'], target_realm=self.realm,
                             installation_id=request['target_installation_id'], outcome=outcome, native_effect=effect,
                             native_save_id=save_id if exists else None,
                             native_save_identity=self.convention.native_identity(creation_id) if exists else None,
                             native_filename=filename if exists else None, save_hash=digest if exists else None,
                             provider=self.provider, session=dict(session), evidence=evidence,
                             diagnostics=D.sort(diags))
        problems = validate_result(result)
        if problems:
            raise ValueError(f'adapter produced an invalid result: {problems[:3]}')
        return result
