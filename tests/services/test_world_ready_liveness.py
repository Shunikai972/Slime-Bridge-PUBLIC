"""Readiness cannot authorize a transition while its own process/load facts are false."""
import pytest

from bridge.counterpart.world_ready import evaluate_world_ready
from bridge.transition.machine import TransitionMachine, make_intent

SOURCE = {'realm': 'SR1', 'installation_id': 'one', 'native_save_id': 'save-one', 'native_save_identity': 'identity-one'}
TARGET = {'realm': 'SR2', 'installation_id': 'two', 'native_save_id': 'save-two', 'native_save_identity': 'identity-two'}
SRC = {'pid': 10, 'session_id': 'source-fixture'}
DST = {'pid': 20, 'session_id': 'target-fixture'}
TRUST = {'fixture-ready': 'SR2'}


def report():
    return {'schema': 'bridge-world-ready', 'schema_version': 1, **TARGET, 'session': DST,
            'build_fingerprint': 'pinned-build', 'process_running': True, 'load_callback_returned': True,
            'scene_settled': True, 'player_usable': True, 'bridge_synchronized': True,
            'observed_at': '2026-01-01T00:00:00Z',
            'provider': {'provider_id': 'fixture-ready', 'provider_version': '1'},
            'evidence': 'RUNTIME_OBSERVATION'}


@pytest.mark.parametrize('field', ['process_running', 'load_callback_returned'])
def test_false_lifecycle_fact_is_rejected_by_the_shared_contract(field):
    expected = {**TARGET, 'session': DST, 'build_fingerprint': 'pinned-build'}
    assert evaluate_world_ready(report(), expected=expected, trust=TRUST) == (True, [])
    ready, diagnostics = evaluate_world_ready({**report(), field: False}, expected=expected, trust=TRUST)
    assert not ready
    assert 'WORLD_READY_' + field.upper() + '_FALSE' in [d['code'] for d in diagnostics]


@pytest.mark.parametrize('field', ['process_running', 'load_callback_returned'])
def test_rejected_lifecycle_report_does_not_advance_or_consume_transition_readiness(tmp_path, field):
    machine = TransitionMachine(tmp_path / 'transition.sqlite3', trust=TRUST)
    try:
        intent = make_intent(profile_id='fixture', source={'realm': 'SR1', 'session': SRC, 'endpoint': SOURCE},
                             destination={'realm': 'SR2', 'session': DST, 'endpoint': TARGET}, expected_build='pinned-build')
        transition = machine.begin(intent)
        for event in ('source_locked', 'source_synced'):
            assert machine.apply(transition, event, session=SRC)['verdict'] == 'ACCEPTED'
        assert machine.apply(transition, 'destination_started', session=DST)['verdict'] == 'ACCEPTED'
        assert machine.apply(transition, 'correct_save_confirmed', session=DST, payload=TARGET)['verdict'] == 'ACCEPTED'
        before = machine.get(transition)
        outcome = machine.apply(transition, 'world_ready', session=DST, payload={'report': {**report(), field: False}})
        assert outcome['verdict'] == 'REJECTED', outcome
        assert 'WORLD_READY_' + field.upper() + '_FALSE' in outcome['code']
        assert machine.get(transition) == before
        assert machine.apply(transition, 'world_ready', session=DST, payload={'report': report()}) == {
            'verdict': 'ACCEPTED', 'stage': 'DESTINATION_WORLD_READY'}
    finally:
        machine.close()
