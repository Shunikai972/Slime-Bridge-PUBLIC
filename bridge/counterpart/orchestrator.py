"""Automatic counterpart orchestration: evaluate the policy, perform exactly one safe action.

The host calls ``step`` whenever something changes (a session heartbeat, a plugin reply, a
deadline, a restart). Each call re-reads durable state, asks the pure policy what to do and does
one thing: request, dispatch (once), reconcile, plan/collect verification, link, finish a link or
re-verify an endpoint. Blocking decisions perform nothing. No user slot choice is involved: the
realm adapter/plugin selects and revalidates natively.

Collaborators (implemented locally over BridgeHost; synthetic ones live in ``simulator``):
- ``sessions(realm) -> session | None``: the live, PID-verified plugin session for a realm
- ``health(realm) -> HEALTHY|DEGRADED|UNAVAILABLE|UNKNOWN``
- ``slots(realm, installation_id) -> adapter-specific probe`` (may be None)
- ``evidence.receipts(plan, intent_or_endpoint) -> [receipt]``: independent readback/runtime evidence
- ``runtime_proven(realm) -> bool``: local proof that native creation works for that realm/build
"""

from .journal import JournalError
from .verification import endpoint_plan

ACTIONS = ('NONE', 'REQUEST', 'DISPATCH', 'RECONCILE', 'PLAN_VERIFICATION', 'VERIFY', 'LINK', 'FINISH_LINK',
           'REVERIFY_ENDPOINT')


class CounterpartOrchestrator:
    def __init__(self, service, *, sessions, health, slots, evidence, runtime_proven, builds=None):
        self.service, self.sessions, self.health, self.slots = service, sessions, health, slots
        self.evidence, self.runtime_proven = evidence, runtime_proven
        self.builds = builds or (lambda realm: None)

    def decision(self, profile_id, target_realm, target_installation_id):
        return self.service.decide(profile_id, target_realm, provider_health=self.health(target_realm),
                                   slot_probe=self.slots(target_realm, target_installation_id),
                                   runtime_proven=self.runtime_proven(target_realm),
                                   current_build=self.builds(target_realm))

    def step(self, profile_id, target_realm, target_installation_id):
        decision = self.decision(profile_id, target_realm, target_installation_id)
        action, result = self._act(decision, profile_id, target_realm, target_installation_id)
        return {'decision': decision['decision'], 'reasons': decision['reasons'], 'creation_id': decision['creation_id'],
                'action': action, 'result': result}

    def _act(self, decision, profile_id, realm, installation_id):
        svc, cid, step = self.service, decision['creation_id'], decision['next_step']
        session = self.sessions(realm)
        if decision['decision'] == 'CREATE_COUNTERPART' and step == 'request':
            return 'REQUEST', {'creation_id': svc.request(profile_id, realm, installation_id)}
        if decision['decision'] == 'CREATE_COUNTERPART' and step == 'dispatch':
            if session is None:
                return 'NONE', {'reason': 'no live target session'}
            return 'DISPATCH', svc.dispatch(cid, session)
        if decision['decision'] == 'RECONCILE_PENDING_CREATION':
            if session is None:
                return 'NONE', {'reason': 'no live target session'}
            if step == 'adopt_and_reconcile':
                # An issued creation without a journal record: persist the intent; dispatch() then
                # sees the ProfileStore fence already used and moves to UNCERTAIN without sending.
                cid = svc.request(profile_id, realm, installation_id)
                return 'DISPATCH', svc.dispatch(cid, session)
            return 'RECONCILE', svc.reconcile(cid, session)
        if decision['decision'] == 'VERIFY_EXISTING_CANDIDATE':
            build = self.builds(realm)
            if step == 'plan_verification':
                plan = svc.plan_verification(cid, build_fingerprint=build)
                return 'PLAN_VERIFICATION', {'plan_id': plan['plan_id']}
            if step == 'verify':
                intent = svc.journal.get(cid)
                return 'VERIFY', svc.verify(cid, self.evidence.receipts(intent['verification_plan'], intent))
            if step == 'link':
                return 'LINK', svc.link(cid, build_fingerprint=build)
            if step == 'finish_link':
                return 'FINISH_LINK', svc.finish_link(cid, build_fingerprint=build)  # never a global restart
            if step == 'reverify_endpoint':
                endpoint = svc.links.snapshot(profile_id)[realm.lower()]
                features = svc.adapters[realm].describe()['features'].get('counterpart', [])
                plan = endpoint_plan(profile_id, realm, {'realm': realm, **endpoint}, build_fingerprint=build,
                                     adapter_features=features)
                receipts = self.evidence.receipts(plan, {'realm': realm, **endpoint})
                return 'REVERIFY_ENDPOINT', svc.reverify_endpoint(profile_id, realm, receipts, build_fingerprint=build)
        return 'NONE', {}

    def run(self, profile_id, target_realm, target_installation_id, *, max_steps=12, on_step=None):
        """Step until nothing more can be done right now (no action or a terminal decision)."""
        trace = []
        for _ in range(max_steps):
            try:
                entry = self.step(profile_id, target_realm, target_installation_id)
            except JournalError as exc:
                trace.append({'decision': 'ERROR', 'action': 'NONE', 'result': {'code': exc.code}})
                break
            stalled = bool(trace) and trace[-1] == entry  # e.g. verification still incomplete
            trace.append(entry)
            if on_step is not None:
                on_step(entry)
            if entry['action'] == 'NONE' or entry['decision'] == 'USE_EXISTING_LINK' or stalled:
                break
        return trace
