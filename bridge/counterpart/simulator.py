"""Synthetic counterpart simulator: fake native realms/plugins driving the REAL generic stack.

Everything generic is real: ProfileStore, CounterpartJournal, CounterpartService, the wire
adapters and the orchestrator. Only the native side is fake: ``SimulatedRealm`` stores saves in
memory and answers the existing plugin wire protocol, ``SimTransport`` routes commands/replies and
``SimEvidence`` produces readback receipts from the fake native state under ``sim.`` provider ids
(trusted only because the simulation opts in with ``allow_synthetic``). Faults are deterministic
(``bridge.sim.faults``). The simulator proves orchestration invariants, never native behaviour.

Invariants every scenario reports:
- zero_duplicate: at most one native save ever carries the creation marker
- zero_overwrite: no pre-existing native save is modified or replaced
- link_sound: a LINKED profile points at exactly one native save that exists, and only after
  verification; nothing else is ever linked
"""

from bridge.content.canonical import sha256_text
from bridge.core import diagnostics as D
from bridge.profile.store import ProfileStore
from bridge.sim.faults import Fault, FaultPlan
from bridge.verification.receipts import make_receipt
from .journal import TERMINAL, CounterpartJournal
from .links import ProfileStoreLinks
from .orchestrator import CounterpartOrchestrator
from .realms import CONVENTIONS, wire_adapter
from .service import CounterpartService

SIM_BUILD = 'sim-build-1'
SIM_CLOCK = '2026-01-01T00:00:00Z'
STATIC_CHECKS = ('counterpart.realm_identity', 'counterpart.installation', 'counterpart.creation_operation')


def evidence_provider(realm):
    return {'provider_id': f'sim.readback.{realm.lower()}', 'provider_version': '1.0.0'}


class SimulatedRealm:
    """In-memory native save store + plugin for one realm installation (synthetic)."""

    def __init__(self, realm, installation_id, *, capacity, faults, existing=(), first_pid=1000):
        self.convention = CONVENTIONS[realm]
        self.realm, self.installation_id, self.capacity, self.faults = realm, installation_id, capacity, faults
        self.saves = {slot: dict(save, origin='foreign') for slot, save in existing}
        self.original = {slot: dict(save) for slot, save in self.saves.items()}
        self.native_writes, self.overwrites = [], 0
        self.session, self._pid = None, first_pid

    def start(self):
        self._pid += 1
        self.session = {'pid': self._pid, 'session_id': f'{self.realm.lower()}-session-{self._pid}'}
        return self.session

    def crash(self):
        self.session = None

    def free_slots(self):
        return [slot for slot in range(self.capacity) if slot not in self.saves]

    def probe(self):
        if self.faults.check(f'{self.realm}.slots.incomplete'):
            return {'complete': False}
        return {'complete': True, 'free_slots': self.free_slots()}

    def marker_saves(self, creation_id):
        marker = self.convention.native_save_id(creation_id)
        return sorted((slot, save) for slot, save in self.saves.items() if save['save_id'] == marker)

    def _write(self, slot, save):
        if slot in self.saves:
            self.overwrites += 1
        self.saves[slot] = save
        self.native_writes.append({'slot': slot, 'save_id': save['save_id']})

    def _save_for(self, creation_id, slot):
        save_id = self.convention.native_save_id(creation_id)
        return {'save_id': save_id, 'filename': f'{save_id}_{slot}',
                'sha256': sha256_text(f'{self.realm}:{save_id}:{slot}'), 'origin': 'bridge'}

    def handle(self, command, session):
        """Plugin behaviour for one command; returns the wire replies it sends (possibly none)."""
        if self.session is None or session != self.session:
            return []
        cid, kind = command['creation_id'], command['kind']
        base = {'realm': self.realm, 'op_id': command['op_id'], 'kind': kind, 'creation_id': cid,
                'pid': session['pid']}
        found = self.marker_saves(cid)
        if found:
            slot, save = found[0]
            return [dict(base, ok=True, applied=False, verified=True, uncertain=False, persisted=True,
                         save_id=save['save_id'], save_file=save['filename'], sha256=save['sha256'])]
        if kind == 'reconcile_save':
            return [dict(base, ok=True, can_create=True, verified=False, uncertain=False,
                         applied=False, persisted=False, before=0, after=0)]

        def refuse(reason):
            return [dict(base, ok=False, applied=False, verified=False, uncertain=False, persisted=False, save_id='',
                         reason=reason)]
        if self.faults.check(f'{self.realm}.plugin.not_ready'):
            return refuse('not at an idle menu')
        free = self.free_slots()
        if not free:
            return refuse('no free native save slot')
        slot = free[0]  # selection only; revalidated right before the native write
        if self.faults.check(f'{self.realm}.slot.race'):
            self.saves[slot] = {'save_id': 'player-made-save', 'filename': 'player-made-save', 'origin': 'foreign',
                                'sha256': sha256_text('player-made-save')}
            self.original[slot] = dict(self.saves[slot])
        if slot in self.saves:
            return refuse('selected slot became occupied; nothing written')
        if self.faults.check(f'{self.realm}.plugin.crash_before_create'):
            self.crash()
            return []
        save = self._save_for(cid, slot)
        self._write(slot, save)
        if self.faults.check(f'{self.realm}.native.duplicate_marker') and self.free_slots():
            other = self.free_slots()[0]  # a native anomaly the plugin did not cause
            self._write(other, dict(self._save_for(cid, other), origin='injected'))
        if self.faults.check(f'{self.realm}.plugin.crash_after_create'):
            self.crash()
            return []
        return [dict(base, ok=True, applied=True, verified=True, uncertain=False, persisted=True,
                     save_id=save['save_id'], save_file=save['filename'], sha256=save['sha256'])]


class SimTransport:
    def __init__(self, realms, faults):
        self.realms, self.faults = realms, faults
        self.outbox, self.delayed, self.commands = [], [], []

    def preflight(self, realm, session):
        return session is not None and self.realms[realm].session == session

    def send(self, realm, command, session):
        self.commands.append((realm, dict(command)))
        if self.faults.check('transport.send_before', kind=command['kind']):
            raise ConnectionError('pipe broke before delivery')
        for reply in self.realms[realm].handle(command, session):
            self.outbox.append((realm, reply, session))
        if self.faults.check('transport.send_after', kind=command['kind']):
            raise ConnectionError('acknowledgement lost after delivery')

    def deliver(self, service):
        verdicts, pending, self.outbox = [], self.outbox, []
        for realm, reply, session in pending:
            if self.faults.check('result.drop'):
                continue
            if self.faults.check('result.delay'):
                self.delayed.append((realm, reply, session))
                continue
            mutation = self.faults.check('result.mutate')
            if mutation:
                reply = {**reply, **mutation.payload}
            verdicts.append(service.on_wire_result(realm, reply, session=session))
            if self.faults.check('result.duplicate'):
                verdicts.append(service.on_wire_result(realm, reply, session=session))
        return verdicts

    def release_delayed(self, service):
        held, self.delayed = self.delayed, []
        return [service.on_wire_result(realm, reply, session=session) for realm, reply, session in held]


class SimEvidence:
    """Readback/runtime receipts computed from the fake native state (``sim.`` providers)."""

    def __init__(self, realms, faults):
        self.realms, self.faults = realms, faults

    def receipts(self, plan, subject):
        realm = plan['realm']
        native = self.realms[realm]
        binding = plan.get('binding') or {}
        bound = binding.get('observed') or binding.get('endpoint') or {}
        if self.faults.check(f'{realm}.readback.unavailable'):
            return []
        failing = self.faults.check(f'{realm}.readback.fail')
        matches = [save for _, save in native.saves.items() if save['save_id'] == bound.get('native_save_id')]
        exact = [save for save in matches if bound.get('save_hash') in (None, save['sha256']) and
                 bound.get('native_filename') in (None, save['filename'])]
        provider = evidence_provider(realm)
        out = []
        for req in plan['requirements']:
            check = req['check_id']
            if check in STATIC_CHECKS:
                continue
            build = req['build'] or SIM_BUILD
            if req['accepted_evidence'] == ['RUNTIME_OBSERVATION']:
                if not req['blocking'] or native.session is None or self.faults.check(f'{realm}.world_ready.timeout'):
                    continue
                out.append(make_receipt(check, plan['subject_kind'], plan['subject_id'], 'PASSED', 'RUNTIME_OBSERVATION',
                                        realm=realm, build_fingerprint=build, provider=provider, observed_at=SIM_CLOCK,
                                        session=native.session,
                                        details={'native_save_id': bound.get('native_save_id'), 'synthetic': True}))
                continue
            if check == 'counterpart.slot_matches' and bound.get('slot') is None:
                out.append(make_receipt(check, plan['subject_kind'], plan['subject_id'], 'NOT_APPLICABLE', 'NONE',
                                        realm=realm, provider=provider,
                                        diagnostics=[D.make('SLOT_NOT_REPORTED', 'info', 'counterpart',
                                                            'the wire result carries no slot')]))
                continue
            ok = {'counterpart.native_save_exists': bool(exact), 'endpoint.native_save_exists': bool(matches),
                  'counterpart.no_conflicting_candidate': len(matches) == 1,
                  'endpoint.identity_matches': len(matches) == 1}.get(check, len(exact) == 1)
            status = 'FAILED' if failing or not ok else 'PASSED'
            hashes = {'save': exact[0]['sha256']} if exact and not failing else {}
            out.append(make_receipt(check, plan['subject_kind'], plan['subject_id'], status, 'NATIVE_READBACK',
                                    realm=realm, build_fingerprint=build, provider=provider, observed_at=SIM_CLOCK,
                                    artifact_hashes=hashes,
                                    details={'native_save_id': bound.get('native_save_id'), 'synthetic': True}))
        return out


class _FaultyLinks(ProfileStoreLinks):
    def __init__(self, store, faults):
        super().__init__(store)
        self.faults = faults

    def confirm(self, profile_id, creation_id, endpoint):
        if self.faults.check('link.persist_crash'):
            raise OSError('host died before the link was persisted')
        if self.faults.check('link.foreign_owner'):
            # Another profile adopted the native save between verification and link.
            self.store.find_or_create(endpoint['realm'], endpoint['installation_id'], endpoint['native_save_id'],
                                      'foreign-identity')
        return super().confirm(profile_id, creation_id, endpoint)


class CounterpartSimulation:
    """One profile, one direction, a deterministic fault plan; ``run`` drives it to quiescence."""

    def __init__(self, root, *, source, target, faults=(), capacity=3, existing_target=(), runtime_proven=True,
                 require_world_ready=False, restart_crashed_realm=True):
        self.root, self.source, self.target = root, source, target
        self.faults = FaultPlan(faults)
        self.runtime_proven, self.require_world_ready = runtime_proven, require_world_ready
        self.restart_crashed_realm = restart_crashed_realm
        self.store = ProfileStore(root / 'profiles.sqlite3')
        self.realms = {realm: SimulatedRealm(realm, f'{realm.lower()}-install', capacity=capacity, faults=self.faults,
                                             existing=existing_target if realm == target else (),
                                             first_pid=1000 if realm == 'SR1' else 2000)
                       for realm in ('SR1', 'SR2')}
        for realm in self.realms.values():
            realm.start()
        self.transport = SimTransport(self.realms, self.faults)
        self.evidence = SimEvidence(self.realms, self.faults)
        self.profile_id = self.store.find_or_create(source, f'{source.lower()}-install', 'native-source-save',
                                                    'source-save-identity')['bridge_profile_id']
        self.host_restarts = self.realm_restarts = 0
        self._open_host()

    def _open_host(self):
        self.journal = CounterpartJournal(self.root / 'journal.sqlite3')
        self.service = CounterpartService(
            self.journal, _FaultyLinks(self.store, self.faults), {r: wire_adapter(r) for r in ('SR1', 'SR2')},
            self.transport, trust={evidence_provider(r)['provider_id']: r for r in ('SR1', 'SR2')},
            allow_synthetic=True)
        self.orchestrator = CounterpartOrchestrator(
            self.service, sessions=lambda realm: self.realms[realm].session,
            health=lambda realm: 'HEALTHY' if self.realms[realm].session else 'UNAVAILABLE',
            slots=lambda realm, installation: self.realms[realm].probe(), evidence=self.evidence,
            runtime_proven=lambda realm: self.runtime_proven, builds=lambda realm: SIM_BUILD)

    def restart_host(self):
        """Host process dies: in-flight replies are lost, the journal is reopened and recovered."""
        self.transport.outbox.clear()
        self.journal.close()
        self.host_restarts += 1
        self._open_host()
        return self.service.recover(build_fingerprints={self.target: SIM_BUILD})

    def close(self):
        self.journal.close()
        self.store.__exit__(None, None, None)

    def _intent(self):
        intents = self.journal.intents_for(self.profile_id)
        return intents[-1] if intents else None

    def _signature(self):
        intent = self._intent()
        snap = self.store.get(self.profile_id)
        return (intent and intent['phase'], snap['state'], len(self.transport.outbox), len(self.transport.delayed),
                tuple(sorted((r, len(s.saves)) for r, s in self.realms.items())),
                tuple(r.session and r.session['pid'] for r in self.realms.values()))

    def run(self, max_rounds=10):
        installation = f'{self.target.lower()}-install'
        if self.require_world_ready:
            self._policy_hook()
        trace, verdicts = [], []
        for round_no in range(max_rounds):
            before = self._signature()
            progress = 0  # actions that change something; a re-tried incomplete VERIFY does not count
            try:
                steps = self.orchestrator.run(self.profile_id, self.target, installation, max_steps=8)
                trace.extend(steps)
                progress += sum(1 for step in steps if step['action'] not in ('NONE', 'VERIFY'))
            except OSError:  # the host process died mid-action
                self.restart_host()
                progress += 1
            if self.faults.check('host.crash', round=round_no):
                self.restart_host()
                progress += 1
            delivered = self.transport.deliver(self.service)
            verdicts.extend(delivered)
            progress += len(delivered)
            intent = self._intent()
            if intent and intent['phase'] in ('DISPATCH_PENDING', 'DISPATCHED', 'RECONCILING') and not delivered:
                self.service.timeout(intent['creation_id'], 'simulated deadline elapsed')
                progress += 1
            target_realm = self.realms[self.target]
            if target_realm.session is None and self.restart_crashed_realm:
                target_realm.start()
                self.realm_restarts += 1
                progress += 1
            if not progress and self._signature() == before:
                break
        verdicts.extend(self.transport.release_delayed(self.service))
        return self.report(trace, verdicts)

    def transport_creates(self, creation_id):
        return sum(1 for realm, c in self.transport.commands
                   if c['kind'] == 'create_save' and c['creation_id'] == creation_id)

    def _policy_hook(self):
        original = self.service.request

        def request(profile_id, target_realm, target_installation_id, **kwargs):
            kwargs['creation_policy'] = {'trigger': 'AUTOMATIC', 'require_world_ready': True}
            return original(profile_id, target_realm, target_installation_id, **kwargs)
        self.service.request = request

    def report(self, trace=(), verdicts=()):
        intent = self._intent()
        snap = self.store.get(self.profile_id)
        native = self.realms[self.target]
        cid = intent['creation_id'] if intent else snap.get('creation_id')
        marker_saves = native.marker_saves(cid) if cid else []
        linked = snap[self.target.lower()]
        foreign_intact = all(native.saves.get(slot) == {**save} for slot, save in native.original.items())
        link_sound = linked is None or (
            intent is not None and intent['phase'] == 'LINKED' and len(marker_saves) == 1 and
            linked['native_save_id'] == marker_saves[0][1]['save_id'] and
            (intent['verification'] or {}).get('verdict') == 'VERIFIED')
        return {'phase': intent and intent['phase'], 'terminal': bool(intent and intent['phase'] in TERMINAL),
                'store_state': snap['state'], 'linked': linked, 'creation_id': cid,
                'decisions': [entry['decision'] for entry in trace], 'actions': [entry['action'] for entry in trace],
                'verdicts': list(verdicts), 'native_writes': list(native.native_writes),
                'create_commands': sum(1 for realm, c in self.transport.commands if c['kind'] == 'create_save'),
                'faults_fired': self.faults.fired_points(), 'host_restarts': self.host_restarts,
                'realm_restarts': self.realm_restarts,
                'invariants': {'zero_duplicate': self.transport_creates(cid) <= 1 and
                               sum(1 for _, save in marker_saves if save['origin'] == 'bridge') <= 1,
                               'zero_overwrite': native.overwrites == 0 and foreign_intact,
                               'link_sound': link_sound}}


def _f(point, on_hit=1, times=1, **payload):
    return Fault(point, on_hit=on_hit, times=times, payload=payload)


# name -> (simulation kwargs, expected final journal phase, expected store state)
SCENARIOS = {
    'sr1_to_sr2': (dict(source='SR1', target='SR2'), 'LINKED', 'LINKED'),
    'sr2_to_sr1': (dict(source='SR2', target='SR1'), 'LINKED', 'LINKED'),
    'no_capacity': (dict(source='SR1', target='SR2', capacity=1,
                         existing_target=((0, {'save_id': 'player-save', 'filename': 'player-save',
                                               'sha256': sha256_text('player-save')}),)), None, 'UNLINKED'),
    'slot_race': (dict(source='SR2', target='SR1', faults=(_f('SR1.slot.race'),)), 'FAILED', 'CREATING'),
    'native_failure': (dict(source='SR1', target='SR2', faults=(_f('SR2.plugin.not_ready'),)), 'FAILED', 'CREATING'),
    'lost_result': (dict(source='SR1', target='SR2', faults=(_f('result.drop'),)), 'LINKED', 'LINKED'),
    'lost_ack': (dict(source='SR2', target='SR1', faults=(_f('transport.send_after'),)), 'LINKED', 'LINKED'),
    'send_failed_before_delivery': (dict(source='SR1', target='SR2', faults=(_f('transport.send_before'),),
                                         restart_crashed_realm=True), 'UNCERTAIN', 'CREATING'),
    'host_crash_after_dispatch': (dict(source='SR1', target='SR2', faults=(_f('host.crash'),)), 'LINKED', 'LINKED'),
    'plugin_crash_before_create': (dict(source='SR2', target='SR1', faults=(_f('SR1.plugin.crash_before_create'),)),
                                   'FAILED_ABSENT', 'CREATING'),
    'plugin_crash_after_create': (dict(source='SR1', target='SR2', faults=(_f('SR2.plugin.crash_after_create'),)),
                                  'LINKED', 'LINKED'),
    'duplicate_result': (dict(source='SR1', target='SR2', faults=(_f('result.duplicate'),)), 'LINKED', 'LINKED'),
    'old_delayed_result': (dict(source='SR2', target='SR1', faults=(_f('result.delay'),)), 'LINKED', 'LINKED'),
    'wrong_creation_reply': (dict(source='SR1', target='SR2', faults=(_f('result.mutate', creation_id='e' * 32),)),
                             'LINKED', 'LINKED'),
    'wrong_operation_reply': (dict(source='SR1', target='SR2', faults=(_f('result.mutate', op_id='forged:op'),)),
                              'LINKED', 'LINKED'),
    'wrong_realm_reply': (dict(source='SR2', target='SR1', faults=(_f('result.mutate', realm='SR2'),)),
                          'LINKED', 'LINKED'),
    'wrong_process_reply': (dict(source='SR1', target='SR2', faults=(_f('result.mutate', pid=7),)), 'LINKED', 'LINKED'),
    'wrong_target_save': (dict(source='SR1', target='SR2', faults=(_f('result.mutate', save_id='player-save'),)),
                          'LINKED', 'LINKED'),
    'wrong_readback': (dict(source='SR2', target='SR1', faults=(_f('SR1.readback.fail'),)), 'CONFLICT', 'CREATING'),
    'ambiguous_candidates': (dict(source='SR1', target='SR2', faults=(_f('SR2.native.duplicate_marker'),)),
                             'CONFLICT', 'CREATING'),
    'readback_unavailable': (dict(source='SR1', target='SR2', faults=(_f('SR2.readback.unavailable', times=99),)),
                             'READBACK_PENDING', 'CREATING'),
    'world_never_ready': (dict(source='SR2', target='SR1', require_world_ready=True,
                               faults=(_f('SR1.world_ready.timeout', times=99),)), 'READBACK_PENDING', 'CREATING'),
    'world_ready': (dict(source='SR2', target='SR1', require_world_ready=True), 'LINKED', 'LINKED'),
    'link_persist_crash': (dict(source='SR1', target='SR2', faults=(_f('link.persist_crash'),)), 'LINKED', 'LINKED'),
    'link_refused_foreign_owner': (dict(source='SR2', target='SR1', faults=(_f('link.foreign_owner'),)),
                                   'CONFLICT', 'CREATING'),
    'not_runtime_proven': (dict(source='SR1', target='SR2', runtime_proven=False), None, 'UNLINKED'),
    'slot_probe_incomplete': (dict(source='SR1', target='SR2', faults=(_f('SR2.slots.incomplete', times=99),)),
                              None, 'UNLINKED'),
}
