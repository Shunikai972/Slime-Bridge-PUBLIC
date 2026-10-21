"""Counterpart coordinator: journal + link store + realm adapters + transport, realm-neutral.

Ordering that makes crashes safe:
1. ``request``: ProfileStore reserves (creation id) -> journal persists the realm-neutral intent.
2. ``dispatch``: transport preflight (live session) -> journal fence (DISPATCH_PENDING, once)
   -> ProfileStore fence (claim_creation, once) -> send. A crash after step 2's first fence is
   recovered as UNCERTAIN and reconciled; nothing is ever re-sent as a create.
3. ``on_wire_result``: adapter translates and binds -> journal accepts strictly.
4. ``verify``: static consistency + independent receipts from a provider trusted for the target
   realm -> VERIFIED or CONFLICT.
5. ``link``: journal LINK_PENDING -> ProfileStore.confirm_counterpart (idempotent) -> LINKED.

Transport contract (implemented locally over BridgeHost): ``preflight(realm, session) -> bool``
and ``send(realm, command, session)`` which raises on any failure (treated as uncertain delivery).
"""

import re

from bridge.core import diagnostics as D
from bridge.verification.plans import STATIC_VERIFIER
from .journal import JournalError
from .legacy_quarantine import is_retired, retired_endpoint_creation_id
from .model import CREATION_ID, OPERATION_ID, make_request
from .pair import endpoint_compatibility, save_pair
from .policy import decide
from .verification import counterpart_plan, endpoint_plan, evaluate, static_receipts

SYNTHETIC_PREFIX = 'sim.'  # simulator providers; never trusted by a production service


class CounterpartService:
    def __init__(self, journal, links, adapters, transport, *, trust, allow_synthetic=False):
        if not allow_synthetic and any(str(provider).startswith(SYNTHETIC_PREFIX) for provider in trust):
            raise ValueError('synthetic providers can only be trusted inside a simulation')
        self.journal, self.links, self.adapters, self.transport = journal, links, adapters, transport
        self.trust = {**trust, STATIC_VERIFIER['provider_id']: '*'}

    def request(self, profile_id, target_realm, target_installation_id, *, slot_policy=None, creation_policy=None):
        source = self.links.source_endpoint(profile_id)
        creation_id = self.links.reserve(profile_id, target_realm)
        request = make_request(creation_id=creation_id, operation_id=f'{creation_id}:create', profile_id=profile_id,
                               source=source, target_realm=target_realm,
                               target_installation_id=target_installation_id, slot_policy=slot_policy,
                               creation_policy=creation_policy)
        self.journal.open_intent(request)
        return creation_id

    def dispatch(self, creation_id, session):
        intent = self.journal.get(creation_id)
        realm = intent['target_realm']
        if is_retired(creation_id):
            return {'dispatched': False, 'reason': 'retired legacy creation id: never re-create or reconcile-mutate'}
        if intent['phase'] != 'INTENT_PERSISTED':
            return {'dispatched': False, 'reason': f"phase {intent['phase']}: reconcile, never re-create"}
        if not self.transport.preflight(realm, session):
            return {'dispatched': False, 'reason': 'target session not live; nothing fenced'}
        if not self.journal.begin_dispatch(creation_id, session):
            return {'dispatched': False, 'reason': 'journal fence already used'}
        try:
            claimed = self.links.claim(intent['profile_id'], creation_id)
        except (KeyError, ValueError) as exc:  # the store no longer holds this creation: never send
            self.journal.mark_conflict(creation_id, f'profile store refused the fence: {str(exc)[:120]}')
            return {'dispatched': False, 'reason': 'profile store refused the creation fence'}
        if not claimed:
            self.journal.mark_uncertain(creation_id, 'profile store fence already issued')
            return {'dispatched': False, 'reason': 'profile store fence already issued; reconcile'}
        command = self.adapters[realm].build_command(intent['request'], 'create', intent['operation_id'])
        try:
            self.transport.send(realm, command, session)
        except Exception as exc:  # delivery unknown: never retried as a create
            self.journal.mark_uncertain(creation_id, f'send failed: {type(exc).__name__}')
            return {'dispatched': False, 'reason': 'delivery uncertain; reconcile'}
        self.journal.mark_dispatched(creation_id)
        return {'dispatched': True, 'operation_id': intent['operation_id']}

    def reconcile(self, creation_id, session):
        intent = self.journal.get(creation_id)
        realm = intent['target_realm']
        if is_retired(creation_id):
            return {'sent': False, 'reason': 'retired legacy creation id: never re-create or reconcile-mutate'}
        if not self.transport.preflight(realm, session):
            return {'sent': False, 'reason': 'target session not live'}
        op = self.journal.begin_reconcile(creation_id, session)
        command = self.adapters[realm].build_command(intent['request'], 'reconcile', op)
        try:
            self.transport.send(realm, command, session)
        except Exception as exc:
            return {'sent': False, 'operation_id': op, 'reason': f'send failed: {type(exc).__name__}'}
        return {'sent': True, 'operation_id': op}

    def timeout(self, creation_id, reason='no result before deadline'):
        return self.journal.mark_uncertain(creation_id, reason)

    def on_wire_result(self, realm, wire, *, session):
        """Translate and strictly bind one plugin reply. Returns a structured verdict, never raises."""
        creation_id = wire.get('creation_id') if isinstance(wire, dict) else None
        op_id = wire.get('op_id') if isinstance(wire, dict) else None
        if not isinstance(creation_id, str) or not re.fullmatch(CREATION_ID, creation_id) or \
                not isinstance(op_id, str) or not re.fullmatch(OPERATION_ID, op_id):
            return {'verdict': 'REJECTED', 'code': 'MALFORMED_REPLY'}
        if is_retired(creation_id):
            return {'verdict': 'REJECTED', 'code': 'RETIRED_CREATION_ID'}
        try:
            intent = self.journal.get(creation_id)
        except JournalError as exc:
            return {'verdict': 'REJECTED', 'code': exc.code}
        if realm != intent['target_realm'] or realm not in self.adapters:
            return {'verdict': 'REJECTED', 'code': 'WRONG_REALM'}
        kind = 'create' if op_id == intent['operation_id'] else 'reconcile'
        try:
            result = self.adapters[realm].interpret_result(intent['request'], kind, op_id, wire, session=session)
            verdict, phase = self.journal.accept_result(result, adapter=self.adapters[realm])
        except JournalError as exc:
            return {'verdict': 'REJECTED', 'code': exc.code}
        except (ValueError, TypeError) as exc:
            return {'verdict': 'REJECTED', 'code': 'UNBINDABLE_REPLY', 'detail': str(exc)[:200]}
        return {'verdict': verdict, 'phase': phase}

    def plan_verification(self, creation_id, *, build_fingerprint=None, require_world_ready=None):
        if is_retired(creation_id):
            raise JournalError('RETIRED_CREATION_ID', 'retired legacy creation evidence cannot be promoted')
        intent = self.journal.get(creation_id)
        adapter = self.adapters[intent['target_realm']]
        features = adapter.describe()['features'].get('counterpart', [])
        if require_world_ready is None:
            require_world_ready = intent['request']['creation_policy']['require_world_ready']
        plan = counterpart_plan(intent, adapter_features=features, build_fingerprint=build_fingerprint,
                                require_world_ready=require_world_ready)
        self.journal.attach_verification_plan(creation_id, plan)
        return plan

    def verify(self, creation_id, receipts):
        if is_retired(creation_id):
            raise JournalError('RETIRED_CREATION_ID', 'retired legacy creation evidence cannot be promoted')
        intent = self.journal.get(creation_id)
        plan = intent['verification_plan']
        if plan is None:
            raise JournalError('NO_PLAN', 'plan verification first')
        adapter = self.adapters[intent['target_realm']]
        receipts = list(receipts) + static_receipts(plan, intent, adapter, STATIC_VERIFIER)
        result = evaluate(plan, receipts, trust=self.trust)
        phase = self.journal.record_verification(creation_id, result)
        return {'phase': phase, 'verdict': result['verdict'], 'rejected': result['rejected'],
                'requirements': result['requirements']}

    def link(self, creation_id, *, build_fingerprint=None):
        if is_retired(creation_id):
            raise JournalError('RETIRED_CREATION_ID', 'retired legacy creation evidence cannot be promoted')
        intent = self.journal.get(creation_id)
        self.journal.begin_link(creation_id)
        observed = intent['observed']
        endpoint = {k: observed[k] for k in ('realm', 'installation_id', 'native_save_id', 'native_save_identity')}
        try:
            self.links.confirm(intent['profile_id'], creation_id, endpoint)
        except ValueError as exc:  # ProfileStore refuses cross-linking / conflicting identity
            return {'phase': self.journal.finish_link(creation_id, linked=False, reason=str(exc)[:200]),
                    'diagnostics': [D.make('COUNTERPART_LINK_REFUSED', 'error', 'counterpart', str(exc)[:200],
                                           profile_id=intent['profile_id'], realm=intent['target_realm'])]}
        return {'phase': self.journal.finish_link(creation_id, linked=True, build_fingerprint=build_fingerprint),
                'diagnostics': []}

    def finish_link(self, creation_id, *, build_fingerprint=None):
        """Complete one half-finished link (LINK_PENDING) without touching any other creation."""
        if is_retired(creation_id):
            raise JournalError('RETIRED_CREATION_ID', 'retired legacy creation evidence cannot be promoted')
        intent = self.journal.get(creation_id)
        if intent['phase'] != 'LINK_PENDING':
            return {'phase': intent['phase'], 'diagnostics': []}
        current = self.links.snapshot(intent['profile_id'])[intent['target_realm'].lower()]
        observed = intent['observed']
        if current is not None and (current['native_save_id'], current['native_save_identity'],
                                    current['installation_id']) != (observed['native_save_id'],
                                                                    observed['native_save_identity'],
                                                                    observed['installation_id']):
            return {'phase': self.journal.finish_link(creation_id, linked=False, reason='store holds another endpoint'),
                    'diagnostics': [D.make('COUNTERPART_LINK_REFUSED', 'error', 'counterpart',
                                           'the profile store holds another endpoint', profile_id=intent['profile_id'],
                                           realm=intent['target_realm'])]}
        return self.link(creation_id, build_fingerprint=build_fingerprint)

    def recover(self, *, build_fingerprints=None):
        """After a host restart only: uncertain in-flight work is flagged; half-finished links are completed."""
        report = self.journal.recover()
        for creation_id in report['link_pending']:
            realm = self.journal.get(creation_id)['target_realm']
            self.finish_link(creation_id, build_fingerprint=(build_fingerprints or {}).get(realm))
        return report

    # -- automatic policy, pair view and build changes -----------------------------------------

    def decide(self, profile_id, target_realm, *, provider_health, slot_probe=None, runtime_proven,
               current_build=None, supported_builds=('*',)):
        """Counterpart policy decision for launching ``profile_id`` with ``target_realm`` as the other side."""
        profile = self.links.snapshot(profile_id)
        adapter = self.adapters.get(target_realm)
        if adapter is None:
            return {'decision': 'BLOCK_PROVIDER_UNAVAILABLE', 'reasons': ['NO_REALM_ADAPTER'], 'creation_id': None,
                    'next_step': None, 'details': {}}
        features = adapter.describe()['features'].get('counterpart', [])
        meta = self.journal.endpoint_meta(profile_id).get(target_realm)
        if meta is not None and is_retired(meta.get('creation_id')):
            return {'decision': 'BLOCK_CONFLICT', 'reasons': ['RETIRED_CREATION_ID'],
                    'creation_id': meta['creation_id'], 'next_step': None, 'details': {}}
        compat = endpoint_compatibility(meta and meta['build_fingerprint'], current_build,
                                        supported_builds=supported_builds,
                                        verdict=meta['verdict'] if meta else 'REVERIFY_REQUIRED')
        return decide(profile=profile, intents=self.journal.intents_for(profile_id), target_realm=target_realm,
                      provider_health=provider_health, slot_status=adapter.describe_slots(slot_probe),
                      runtime_proven=runtime_proven, endpoint_compatibility=compat,
                      slot_probe_supported='slot_probe' in features,
                      last_outcomes=self.journal.last_outcomes(profile_id))

    def pair(self, profile_id, *, current_builds=None, supported_builds=None, content_compatibility='UNKNOWN'):
        return save_pair(self.links.snapshot(profile_id), self.journal.intents_for(profile_id),
                         self.journal.endpoint_meta(profile_id), current_builds=current_builds,
                         supported_builds=supported_builds, content_compatibility=content_compatibility)

    def _endpoint_profile(self, profile_id, realm):
        profile = self.links.snapshot(profile_id)
        if retired_endpoint_creation_id(profile, realm) is not None:
            raise JournalError('RETIRED_CREATION_ID', 'retired profile provenance cannot be adopted or verified')
        return profile

    def observe_live(self, profile_id, realm, *, build_fingerprint):
        """A live session identified this profile's save for ``realm`` under ``build_fingerprint``."""
        endpoint = self._endpoint_profile(profile_id, realm)[realm.lower()]
        if endpoint is None:
            raise JournalError('NO_ENDPOINT', f'profile has no {realm} endpoint')
        return self.journal.observe_live_endpoint(profile_id, {'realm': realm, **endpoint}, build_fingerprint)

    def reverify_endpoint(self, profile_id, realm, receipts, *, build_fingerprint, require_world_loaded=False):
        """Evaluate fresh evidence for a linked endpoint under a new build; never relinks or deletes."""
        endpoint = self._endpoint_profile(profile_id, realm)[realm.lower()]
        if endpoint is None:
            raise JournalError('NO_ENDPOINT', f'profile has no {realm} endpoint')
        features = self.adapters[realm].describe()['features'].get('counterpart', [])
        plan = endpoint_plan(profile_id, realm, {'realm': realm, **endpoint}, build_fingerprint=build_fingerprint,
                             adapter_features=features, require_world_loaded=require_world_loaded)
        result = evaluate(plan, receipts, trust=self.trust)
        verdict = {'VERIFIED': 'VERIFIED', 'FAILED': 'FAILED'}.get(result['verdict'], 'REVERIFY_REQUIRED')
        self.journal.record_endpoint_verification(profile_id, realm, verdict=verdict,
                                                  build_fingerprint=build_fingerprint, plan_id=plan['plan_id'],
                                                  endpoint={'realm': realm, **endpoint})
        return {'verdict': verdict, 'plan': plan, 'evaluation': result}
