"""``bridge-runtime-scenario`` v1 and evaluation of a LAB run report against it.

A scenario names its preconditions, setup, actions, the events that must be observed (in order),
events that must never be observed, the persisted state expected afterwards, cleanup, the proof
artifacts a run must produce, its timeout policy and rollback requirements. Every scenario starts
NOT_RUN. ``evaluate_run`` checks a run report (untrusted) and only produces runtime evidence when the
report comes from a provider trusted for every realm the scenario touches; a synthetic rehearsal
(the Cloud simulators) is always labelled SYNTHETIC and never satisfies a runtime requirement.
"""

from bridge.core import schema as S
from bridge.verification.receipts import make_receipt

AREAS = ('COUNTERPART', 'TRANSITION', 'CONTENT', 'MULTIPLAYER', 'DEPLOY', 'UPDATE', 'SYSTEM')
_EVENT = r'[a-z_]+\.[a-z0-9_]+'
_STEP = S.obj({'action': S.string(r'[a-z_]+\.[a-z0-9_]+'), 'args': S.map_of(S.string(r'[a-z_]{1,32}'), S.any_json())})
SCENARIO = S.obj({
    'schema': S.enum('bridge-runtime-scenario'), 'schema_version': S.enum(1),
    'id': S.string(r'[a-z0-9][a-z0-9_.-]{2,80}'), 'title': S.string(max_length=160), 'area': S.enum(*AREAS),
    'realms': S.list_of(S.enum('SR1', 'SR2'), max_items=2, unique=True),
    'preconditions': S.list_of(S.string(max_length=200), max_items=16),
    'setup': S.list_of(_STEP, max_items=32), 'actions': S.list_of(_STEP, max_items=64),
    'expected_events': S.list_of(S.string(_EVENT), max_items=64),
    'forbidden_events': S.list_of(S.string(_EVENT), max_items=64),
    'expected_state': S.map_of(S.string(r'[a-z_]+(\.[a-z0-9_]+)*'), S.any_json()),
    'cleanup': S.list_of(_STEP, max_items=32), 'proof_artifacts': S.list_of(S.string(r'[a-z0-9_.-]{1,64}'), max_items=32),
    'timeout_policy': S.obj({'total_seconds': S.integer(minimum=1, maximum=7200),
                             'step_seconds': S.integer(minimum=1, maximum=3600),
                             'on_timeout': S.enum('ROLLBACK', 'FAIL', 'RECONCILE')}),
    'rollback_requirements': S.list_of(S.string(max_length=200), max_items=16),
    'rehearsal': S.optional(S.obj({'simulator': S.enum('counterpart', 'transition'),
                                   'scenario': S.string(r'[a-z0-9_]{1,64}')})),
    'status': S.enum('NOT_RUN'), 'evidence': S.enum('NEEDS_LOCAL_RUNTIME_PROOF', 'NEEDS_LOCAL_VISUAL_PROOF'),
})
RUN_REPORT = S.obj({
    'schema': S.enum('bridge-scenario-run'), 'schema_version': S.enum(1), 'scenario_id': S.string(max_length=80),
    'provider': S.obj({'provider_id': S.string(max_length=128), 'provider_version': S.string(max_length=64)}),
    'realms': S.list_of(S.enum('SR1', 'SR2'), max_items=2, unique=True),
    'build_fingerprints': S.map_of(S.enum('SR1', 'SR2'), S.string(max_length=128)),
    'session': S.optional(S.obj({'pid': S.integer(minimum=1), 'session_id': S.string(max_length=128)})),
    'observed_events': S.list_of(S.string(_EVENT), max_items=10000),
    'persisted_state': S.map_of(S.string(max_length=128), S.any_json()),
    'artifacts': S.map_of(S.string(r'[a-z0-9_.-]{1,64}'), S.sha256()),
    'started_at': S.string(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z'),
    'finished_at': S.string(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z'),
    'synthetic': S.boolean(),
})


def step(action, **args):
    return {'action': action, 'args': args}


def scenario(id_, title, area, realms, *, preconditions=(), setup=(), actions=(), expected=(), forbidden=(),
             state=None, cleanup=(), proofs=(), timeout=(900, 300, 'ROLLBACK'), rollback=(), rehearsal=None,
             evidence='NEEDS_LOCAL_RUNTIME_PROOF'):
    return {'schema': 'bridge-runtime-scenario', 'schema_version': 1, 'id': id_, 'title': title, 'area': area,
            'realms': list(realms), 'preconditions': list(preconditions), 'setup': list(setup), 'actions': list(actions),
            'expected_events': list(expected), 'forbidden_events': list(forbidden), 'expected_state': dict(state or {}),
            'cleanup': list(cleanup), 'proof_artifacts': list(proofs),
            'timeout_policy': {'total_seconds': timeout[0], 'step_seconds': timeout[1], 'on_timeout': timeout[2]},
            'rollback_requirements': list(rollback),
            'rehearsal': rehearsal and {'simulator': rehearsal[0], 'scenario': rehearsal[1]},
            'status': 'NOT_RUN', 'evidence': evidence}


def validate_scenario(doc):
    problems = S.check(SCENARIO, doc)
    if not problems:
        clash = set(doc['expected_events']) & set(doc['forbidden_events'])
        if clash:
            problems.append(f'$.forbidden_events: also expected: {sorted(clash)}')
    return problems


def _in_order(expected, observed):
    it = iter(observed)
    return all(any(event == seen for seen in it) for event in expected)


def evaluate_run(scenario_doc, report, *, trust):
    """{'verdict': PASSED|PASSED_SYNTHETIC|FAILED|INCOMPLETE|INVALID, 'reasons', 'runtime', 'receipts'}.

    PASSED (with one runtime receipt per realm) only for a non-synthetic report from a provider
    trusted for every realm of the scenario, bound to a session and build fingerprints."""
    problems = S.check(RUN_REPORT, report)
    if problems or report['scenario_id'] != scenario_doc['id']:
        return {'verdict': 'INVALID', 'reasons': problems[:5] or ['WRONG_SCENARIO'], 'runtime': False, 'receipts': []}
    reasons = []
    observed = report['observed_events']
    missing = [e for e in scenario_doc['expected_events'] if e not in observed]
    if missing:
        reasons.append('MISSING_EVENTS:' + ','.join(missing[:5]))
    elif not _in_order(scenario_doc['expected_events'], observed):
        reasons.append('EVENTS_OUT_OF_ORDER')
    forbidden = sorted(set(scenario_doc['forbidden_events']) & set(observed))
    if forbidden:
        reasons.append('FORBIDDEN_EVENTS:' + ','.join(forbidden[:5]))
    for key, value in scenario_doc['expected_state'].items():
        if report['persisted_state'].get(key) != value:
            reasons.append(f'STATE_MISMATCH:{key}')
    absent = [a for a in scenario_doc['proof_artifacts'] if a not in report['artifacts']]
    if absent:
        reasons.append('MISSING_ARTIFACTS:' + ','.join(absent[:5]))
    provider = report['provider']['provider_id']
    runtime = (not report['synthetic'] and set(report['realms']) >= set(scenario_doc['realms']) and
               all(trust.get(provider) in (realm, 'BOTH') for realm in scenario_doc['realms']) and
               report['session'] is not None and
               all(realm in report['build_fingerprints'] for realm in scenario_doc['realms']))
    if forbidden or any(r.startswith(('STATE_MISMATCH', 'EVENTS_OUT_OF_ORDER')) for r in reasons):
        verdict = 'FAILED'
    elif reasons:
        verdict = 'INCOMPLETE'
    else:
        verdict = 'PASSED'
    receipts = []
    if runtime and verdict in ('PASSED', 'FAILED'):
        kind = 'transition' if scenario_doc['area'] == 'TRANSITION' else 'counterpart'
        receipts = [make_receipt(f"scenario.{scenario_doc['id']}", kind, scenario_doc['id'], verdict,
                                 'RUNTIME_OBSERVATION', realm=realm, build_fingerprint=report['build_fingerprints'][realm],
                                 provider=report['provider'], observed_at=report['finished_at'],
                                 session=report['session'], artifact_hashes=report['artifacts'])
                    for realm in scenario_doc['realms']]
    if verdict == 'PASSED' and not runtime:
        verdict = 'PASSED_SYNTHETIC'  # consistent, but not runtime evidence
    return {'verdict': verdict, 'reasons': reasons, 'runtime': runtime, 'receipts': receipts}
