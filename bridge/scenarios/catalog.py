"""The LAB runtime scenario catalogue (all NOT_RUN) and synthetic rehearsals of the ones Cloud can simulate.

Event vocabulary (what a LAB harness reports, in order):
  counterpart.intent_persisted / dispatched / native_observed / verified / linked / failed / uncertain /
  reconciled / duplicate_created (forbidden) / overwrite (forbidden) / linked_unverified (forbidden)
  transition.started / world_ready / source_hidden / destination_visible / committed / rolled_back /
  visible_before_ready (forbidden) / player_stranded (forbidden)
  content.package_built / deployed / registered / verified / quarantined / rolled_back / partial_install (forbidden)
  net.connected / resumed / rejected / duplicate_applied (forbidden)
  system.restarted / game_restarted / build_changed / reverify_required

``python3.12 -m bridge.scenarios.catalog --write`` regenerates docs/runtime-scenarios.json (a test
checks the committed file matches).
"""

import json
import sys
import tempfile
from pathlib import Path

from .model import scenario, step, validate_scenario

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / 'docs' / 'runtime-scenarios.json'
_NO_DUP = ('counterpart.duplicate_created', 'counterpart.overwrite', 'counterpart.linked_unverified')
_SAFE_SWITCH = ('transition.visible_before_ready', 'transition.player_stranded')
_PROTECT = ('Lab copies only; source installs untouched', 'native saves backed up and hash-verified before the run')


def _counterpart(id_, title, source, target, *, setup=(), actions=(), expected, forbidden=_NO_DUP, state, proofs,
                 rehearsal=None, rollback=('restore native saves from the pre-run backup',)):
    return scenario(id_, title, 'COUNTERPART', sorted({source, target}), preconditions=_PROTECT + (
        f'a profile with exactly one {source} save', f'{target} Lab copy with the Bridge plugin'),
        setup=[step('lab.snapshot_saves'), step('lab.launch', realm=source), *setup],
        actions=[step('bridge.open_profile', realm=source), *actions], expected=expected, forbidden=forbidden,
        state=state, cleanup=[step('lab.close_games'), step('lab.restore_saves')], proofs=proofs,
        rollback=rollback, rehearsal=rehearsal and ('counterpart', rehearsal))


CATALOG = [
    _counterpart('counterpart.sr1_to_sr2', 'SR1 save gets an automatic SR2 counterpart', 'SR1', 'SR2',
                 actions=[step('bridge.await_counterpart', target='SR2')],
                 expected=['counterpart.intent_persisted', 'counterpart.dispatched', 'counterpart.native_observed',
                           'counterpart.verified', 'counterpart.linked'],
                 state={'profile.state': 'LINKED', 'journal.phase': 'LINKED'},
                 proofs=['host-log', 'sr2-melon-log', 'save-readback', 'profile-db-dump'], rehearsal='sr1_to_sr2'),
    _counterpart('counterpart.sr2_to_sr1', 'SR2 save gets an automatic SR1 counterpart', 'SR2', 'SR1',
                 actions=[step('bridge.await_counterpart', target='SR1')],
                 expected=['counterpart.intent_persisted', 'counterpart.dispatched', 'counterpart.native_observed',
                           'counterpart.verified', 'counterpart.linked'],
                 state={'profile.state': 'LINKED', 'journal.phase': 'LINKED'},
                 proofs=['host-log', 'sr1-player-log', 'save-readback', 'profile-db-dump'], rehearsal='sr2_to_sr1'),
    _counterpart('counterpart.slots_full', 'Destination has no free save slot', 'SR1', 'SR2',
                 setup=[step('lab.fill_save_slots', realm='SR2')], actions=[step('bridge.await_policy')],
                 expected=['counterpart.failed'], state={'profile.state': 'UNLINKED'},
                 proofs=['host-log', 'slot-probe'], rehearsal='no_capacity'),
    _counterpart('counterpart.slot_race', 'A slot is taken between selection and creation', 'SR2', 'SR1',
                 setup=[step('lab.arm_slot_race', realm='SR1')], actions=[step('bridge.await_counterpart', target='SR1')],
                 expected=['counterpart.dispatched', 'counterpart.failed'], state={'journal.phase': 'FAILED'},
                 proofs=['host-log', 'save-listing-before-after'], rehearsal='slot_race'),
    _counterpart('counterpart.wrong_save', 'Plugin reports a save with foreign provenance', 'SR1', 'SR2',
                 setup=[step('lab.inject_reply', field='save_id', value='foreign')],
                 actions=[step('bridge.await_counterpart', target='SR2')],
                 expected=['counterpart.dispatched', 'counterpart.reconciled', 'counterpart.linked'],
                 state={'profile.state': 'LINKED'}, proofs=['host-log', 'rejection-log'],
                 rehearsal='wrong_target_save'),
    _counterpart('counterpart.wrong_profile', 'A result for another profile is rejected', 'SR1', 'SR2',
                 setup=[step('lab.inject_reply', field='profile_id', value='other')],
                 actions=[step('bridge.await_counterpart', target='SR2')],
                 expected=['counterpart.dispatched'], state={'other_profile.state': 'UNLINKED'},
                 proofs=['host-log', 'rejection-log']),
    _counterpart('counterpart.lost_ack', 'Native creation succeeds but the acknowledgement is lost', 'SR2', 'SR1',
                 setup=[step('lab.drop_next_reply', realm='SR1')], actions=[step('bridge.await_counterpart', target='SR1')],
                 expected=['counterpart.dispatched', 'counterpart.uncertain', 'counterpart.reconciled',
                           'counterpart.linked'], state={'profile.state': 'LINKED'},
                 proofs=['host-log', 'save-listing-before-after'], rehearsal='lost_ack'),
    _counterpart('counterpart.bridge_restart', 'BridgeHost restarts right after dispatch', 'SR1', 'SR2',
                 actions=[step('bridge.kill_host_after', event='counterpart.dispatched'), step('bridge.start_host')],
                 expected=['counterpart.dispatched', 'system.restarted', 'counterpart.uncertain',
                           'counterpart.reconciled', 'counterpart.linked'], state={'profile.state': 'LINKED'},
                 proofs=['host-log', 'journal-dump'], rehearsal='host_crash_after_dispatch'),
    _counterpart('counterpart.game_restart', 'Destination game crashes after writing the save', 'SR1', 'SR2',
                 actions=[step('lab.kill_game_after', event='native_write', realm='SR2'), step('lab.launch', realm='SR2')],
                 expected=['counterpart.dispatched', 'system.game_restarted', 'counterpart.reconciled',
                           'counterpart.linked'], state={'profile.state': 'LINKED'},
                 proofs=['host-log', 'save-readback'], rehearsal='plugin_crash_after_create'),
    _counterpart('counterpart.system_restart', 'Machine restarts with a creation in flight', 'SR2', 'SR1',
                 actions=[step('lab.power_cycle_vm_after', event='counterpart.dispatched')],
                 expected=['system.restarted', 'counterpart.uncertain', 'counterpart.reconciled'],
                 state={'journal.terminal_or_linked': True}, proofs=['host-log', 'journal-dump', 'save-readback']),
    scenario('system.game_update', 'A game update marks linked endpoints for re-verification', 'SYSTEM', ['SR1', 'SR2'],
             preconditions=_PROTECT + ('a LINKED profile',), setup=[step('lab.swap_build_fingerprint', realm='SR2')],
             actions=[step('bridge.open_profile', realm='SR1')],
             expected=['system.build_changed', 'system.reverify_required', 'counterpart.verified'],
             forbidden=('counterpart.duplicate_created', 'content.partial_install'),
             state={'pair.status': 'LINKED'}, proofs=['host-log', 'endpoint-meta-dump']),
    scenario('content.package_incompatible', 'A package built for another build is not enabled', 'CONTENT', ['SR1'],
             preconditions=_PROTECT, actions=[step('content.deploy', package='fixture.incompatible')],
             expected=['content.quarantined'], forbidden=('content.registered', 'content.partial_install'),
             state={'inventory.unit_state': 'QUARANTINED'}, proofs=['deploy-journal']),
    scenario('content.provider_missing', 'Registry provider unavailable: nothing converts, nothing breaks', 'CONTENT',
             ['SR2'], preconditions=_PROTECT, actions=[step('content.batch', direction='SR2->SR1')],
             expected=['content.quarantined'], forbidden=('content.registered',), state={},
             proofs=['batch-report']),
    scenario('content.conversion_failure', 'A broken species is isolated from the batch', 'CONTENT', ['SR1', 'SR2'],
             preconditions=_PROTECT, actions=[step('content.batch', direction='SR2->SR1')],
             expected=['content.package_built', 'content.quarantined'], forbidden=('content.partial_install',),
             state={}, proofs=['batch-report']),
    scenario('content.material_failure', 'An unmappable material keeps the species pending', 'CONTENT', ['SR1'],
             preconditions=_PROTECT, actions=[step('content.convert', species='fixture.unknown_shader')],
             expected=['content.package_built'], forbidden=('content.registered',),
             state={'package.status': 'PENDING_ADAPTER'}, proofs=['package-manifest']),
    scenario('content.registration_failure', 'Runtime registration fails and the package rolls back', 'CONTENT', ['SR1'],
             preconditions=_PROTECT, actions=[step('content.deploy', package='fixture.first_species'),
                                              step('lab.fail_registration')],
             expected=['content.deployed', 'content.rolled_back'], forbidden=('content.partial_install',),
             state={'inventory.unit_state': 'ABSENT'}, proofs=['deploy-journal', 'game-log']),
    scenario('transition.destination_fails_to_load', 'Destination never becomes ready: player stays home',
             'TRANSITION', ['SR1', 'SR2'], preconditions=_PROTECT + ('LINKED profile',),
             actions=[step('portal.enter', portal='portal.sr1.sanctuary_gate'), step('lab.block_world_load', realm='SR2')],
             expected=['transition.started', 'transition.rolled_back'], forbidden=_SAFE_SWITCH + ('transition.committed',),
             state={'transition.stage': 'ROLLED_BACK'}, proofs=['host-log', 'overlay-capture'],
             rehearsal=('transition', 'world_ready_never')),
    scenario('transition.world_ready_wrong_save', 'WORLD_READY for the wrong save is rejected', 'TRANSITION',
             ['SR1', 'SR2'], preconditions=_PROTECT + ('LINKED profile',),
             actions=[step('portal.enter', portal='portal.sr1.sanctuary_gate'), step('lab.load_other_save', realm='SR2')],
             expected=['transition.started', 'transition.rolled_back'], forbidden=_SAFE_SWITCH + ('transition.committed',),
             state={'transition.stage': 'ROLLED_BACK'}, proofs=['host-log'],
             rehearsal=('transition', 'destination_wrong_save')),
    scenario('transition.sr1_to_sr2_warm', 'Warm switch SR1 -> SR2 through the sanctuary gate', 'TRANSITION',
             ['SR1', 'SR2'], preconditions=_PROTECT + ('LINKED profile', 'SR2 prepared in the background'),
             actions=[step('portal.enter', portal='portal.sr1.sanctuary_gate')],
             expected=['transition.started', 'transition.world_ready', 'transition.source_hidden',
                       'transition.destination_visible', 'transition.committed'], forbidden=_SAFE_SWITCH,
             state={'transition.stage': 'COMMITTED'}, proofs=['host-log', 'overlay-capture', 'screen-recording'],
             rehearsal=('transition', 'sr1_to_sr2'), evidence='NEEDS_LOCAL_VISUAL_PROOF'),
    scenario('transition.fails_mid_switch', 'Destination crashes after the source was hidden', 'TRANSITION',
             ['SR1', 'SR2'], preconditions=_PROTECT + ('LINKED profile',),
             actions=[step('portal.enter', portal='portal.sr1.sanctuary_gate'),
                      step('lab.kill_game_after', event='transition.source_hidden', realm='SR2')],
             expected=['transition.started', 'transition.source_hidden', 'transition.rolled_back'],
             forbidden=_SAFE_SWITCH + ('transition.committed',), state={'transition.stage': 'ROLLED_BACK'},
             proofs=['host-log', 'screen-recording'], rehearsal=('transition', 'failure_after_source_hidden')),
    scenario('transition.cancel_rollback', 'Player cancels before commit', 'TRANSITION', ['SR1', 'SR2'],
             preconditions=_PROTECT + ('LINKED profile',),
             actions=[step('portal.enter', portal='portal.sr2.return_portal'), step('overlay.cancel')],
             expected=['transition.started', 'transition.rolled_back'], forbidden=_SAFE_SWITCH + ('transition.committed',),
             state={'transition.stage': 'ROLLED_BACK'}, proofs=['host-log'], rehearsal=('transition', 'cancel_before_commit')),
    scenario('net.reconnect', 'Multiplayer client reconnects and resumes without duplicates', 'MULTIPLAYER', ['SR1', 'SR2'],
             preconditions=('hybrid server running', 'two clients in different realms'),
             actions=[step('net.drop_connection', client='a'), step('net.send_chat', client='b'),
                      step('net.reconnect', client='a')],
             expected=['net.connected', 'net.resumed'], forbidden=('net.duplicate_applied',),
             state={'server.players': 2}, proofs=['server-log']),
    scenario('net.server_mismatch', 'Content mismatch restricts the realm, not membership', 'MULTIPLAYER', ['SR2'],
             preconditions=('server requires a content manifest the client lacks',),
             actions=[step('net.join', realm='SR2')], expected=['net.connected', 'net.rejected'],
             forbidden=('net.duplicate_applied',), state={'client.realm': None}, proofs=['server-log']),
    scenario('content.package_hash_mismatch', 'A tampered artifact is refused before any change', 'DEPLOY', ['SR1'],
             preconditions=_PROTECT, actions=[step('content.deploy', package='fixture.tampered')],
             expected=['content.rolled_back'], forbidden=('content.deployed', 'content.partial_install'),
             state={'inventory.unchanged': True}, proofs=['deploy-journal']),
    scenario('content.portal_model_visual', 'Procedural gateway loads and reads well in both games', 'CONTENT',
             ['SR1', 'SR2'], preconditions=('portal GLB generated by bridge.meshgen',),
             actions=[step('lab.import_model', format='glb'), step('lab.capture_views', angles='front,back,side,approach')],
             expected=['content.registered'], forbidden=('content.partial_install',), state={},
             proofs=['view-front', 'view-back', 'view-side', 'view-approach'], evidence='NEEDS_LOCAL_VISUAL_PROOF'),
]


def catalogue():
    return list(CATALOG)


def problems():
    out = {}
    ids = [s['id'] for s in CATALOG]
    if len(ids) != len(set(ids)):
        out['catalog'] = ['duplicate scenario ids']
    for doc in CATALOG:
        found = validate_scenario(doc)
        if found:
            out[doc['id']] = found
    return out


def export_text():
    return json.dumps({'schema': 'bridge-runtime-scenario-catalog', 'schema_version': 1,
                       'scenarios': CATALOG}, indent=2, sort_keys=True) + '\n'


def rehearse(scenario_id):
    """Run the synthetic simulator mapped to a scenario and translate it into the event vocabulary."""
    doc = next(s for s in CATALOG if s['id'] == scenario_id)
    if doc['rehearsal'] is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        if doc['rehearsal']['simulator'] == 'counterpart':
            from bridge.counterpart.simulator import SCENARIOS, CounterpartSimulation
            sim = CounterpartSimulation(Path(tmp), **SCENARIOS[doc['rehearsal']['scenario']][0])
            try:
                report = sim.run()
            finally:
                sim.close()
            events = _counterpart_events(report)
            state = {'profile.state': report['store_state'], 'journal.phase': report['phase']}
        else:
            from bridge.transition.simulator import SCENARIOS, TransitionSimulation
            sim = TransitionSimulation(Path(tmp), **SCENARIOS[doc['rehearsal']['scenario']][0])
            try:
                report = sim.run()
            finally:
                sim.close()
            events = _transition_events(report)
            state = {'transition.stage': report['stage']}
    return {'schema': 'bridge-scenario-run', 'schema_version': 1, 'scenario_id': scenario_id,
            'provider': {'provider_id': 'sim.rehearsal', 'provider_version': '1.0.0'}, 'realms': doc['realms'],
            'build_fingerprints': {}, 'session': None, 'observed_events': events, 'persisted_state': state,
            'artifacts': {}, 'started_at': '2026-01-01T00:00:00Z', 'finished_at': '2026-01-01T00:00:00Z',
            'synthetic': True}


def _counterpart_events(report):
    events = []
    if report['creation_id']:
        events.append('counterpart.intent_persisted')
    if report['create_commands']:
        events.append('counterpart.dispatched')
    if 'RECONCILE' in report['actions']:
        events += ['counterpart.uncertain', 'counterpart.reconciled']
    if report['phase'] in ('NATIVE_OBSERVED', 'READBACK_PENDING', 'VERIFIED', 'LINK_PENDING', 'LINKED', 'CONFLICT'):
        events.append('counterpart.native_observed')
    if report['phase'] in ('VERIFIED', 'LINK_PENDING', 'LINKED'):
        events.append('counterpart.verified')
    if report['phase'] == 'LINKED':
        events.append('counterpart.linked')
    if report['phase'] in ('FAILED', 'FAILED_ABSENT') or (report['creation_id'] is None and report['store_state'] == 'UNLINKED'):
        events.append('counterpart.failed')
    if report['host_restarts']:
        events.insert(min(2, len(events)), 'system.restarted')
    if report['realm_restarts']:
        events.insert(min(2, len(events)), 'system.game_restarted')
    if not report['invariants']['zero_duplicate']:
        events.append('counterpart.duplicate_created')
    if not report['invariants']['zero_overwrite']:
        events.append('counterpart.overwrite')
    if not report['invariants']['link_sound']:
        events.append('counterpart.linked_unverified')
    return events


def _transition_events(report):
    seen = [event for event, verdict in report['events'] if verdict == 'ACCEPTED']
    mapping = {'begin': 'transition.started', 'world_ready': 'transition.world_ready',
               'source_hidden': 'transition.source_hidden', 'destination_visible': 'transition.destination_visible',
               'commit': 'transition.committed'}
    events = [mapping[e] for e in seen if e in mapping]
    if report['stage'] == 'ROLLED_BACK':
        events.append('transition.rolled_back')
    if not report['invariants']['ready_before_visible']:
        events.append('transition.visible_before_ready')
    if not report['invariants']['never_stranded']:
        events.append('transition.player_stranded')
    return events


if __name__ == '__main__':
    if sys.argv[1:] == ['--write']:
        EXPORT.write_text(export_text(), encoding='utf-8')
    else:
        sys.stdout.write(export_text())
