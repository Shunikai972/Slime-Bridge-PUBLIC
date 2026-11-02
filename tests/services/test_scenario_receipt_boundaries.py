"""Scenario result/session bounds agree with its downstream verification receipts."""
import pytest

from bridge.scenarios.model import evaluate_run, scenario
from bridge.verification.receipts import validate_receipt


def report(pid):
    return {'schema': 'bridge-scenario-run', 'schema_version': 1, 'scenario_id': 'synthetic.boundaries',
            'provider': {'provider_id': 'synthetic-assertion-provider', 'provider_version': '1'},
            'realms': ['SR1'], 'build_fingerprints': {'SR1': 'synthetic-build'},
            'session': {'pid': pid, 'session_id': 'synthetic-session'},
            'observed_events': [], 'persisted_state': {}, 'artifacts': {},
            'started_at': '2026-01-01T00:00:00Z', 'finished_at': '2026-01-01T00:00:01Z',
            'synthetic': False}


@pytest.mark.parametrize('pid', [2 ** 32, 2 ** 64, 10 ** 100])
def test_out_of_range_process_session_cannot_create_runtime_success(pid):
    # Deliberately claimed data for validator testing, never actual runtime evidence.
    doc = scenario('synthetic.boundaries', 'Synthetic contract test', 'SYSTEM', ['SR1'])
    result = evaluate_run(doc, report(pid), trust={'synthetic-assertion-provider': 'SR1'})
    assert result['verdict'] == 'INVALID'
    assert result['runtime'] is False and result['receipts'] == []


@pytest.mark.parametrize('pid', [1, 2 ** 32 - 1])
def test_accepted_session_always_produces_valid_receipt(pid):
    doc = scenario('synthetic.boundaries', 'Synthetic contract test', 'SYSTEM', ['SR1'])
    result = evaluate_run(doc, report(pid), trust={'synthetic-assertion-provider': 'SR1'})
    assert result['verdict'] == 'PASSED' and result['receipts']
    assert all(not validate_receipt(r) for r in result['receipts'])
