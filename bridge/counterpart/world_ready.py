"""WORLD_READY contract: what a local provider must prove before a realm counts as playable.

WORLD_READY is NOT: a process existing, a LoadNewGame callback returning, or scene loading having
started. A ``bridge-world-ready`` report must bind the exact realm, process session, installation,
native save identity and build, and attest that scene loading settled, the player/world is usable
and Bridge state is synchronized, with runtime evidence from a provider trusted for that realm.
Cloud defines and validates the contract; the local provider implements the detection.
"""

from bridge.core import diagnostics as D
from bridge.core import schema as S

_SPEC = S.obj({
    'schema': S.enum('bridge-world-ready'), 'schema_version': S.enum(1),
    'realm': S.enum('SR1', 'SR2'), 'session': S.obj({'pid': S.integer(minimum=1), 'session_id': S.string()}),
    'installation_id': S.string(), 'native_save_id': S.string(), 'native_save_identity': S.string(),
    'build_fingerprint': S.string(),
    'process_running': S.boolean(), 'load_callback_returned': S.boolean(),
    'scene_settled': S.boolean(), 'player_usable': S.boolean(), 'bridge_synchronized': S.boolean(),
    'observed_at': S.string(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z'),
    'provider': S.obj({'provider_id': S.string(), 'provider_version': S.string()}),
    'evidence': S.enum('RUNTIME_OBSERVATION', 'SYNTHETIC'),
})


def evaluate_world_ready(report, *, expected, trust):
    """(ready: bool, diagnostics). ``expected`` names realm, session, installation, save and build."""
    problems = S.check(_SPEC, report)
    if problems:
        return False, [D.make('WORLD_READY_MALFORMED', 'error', 'counterpart', 'malformed WORLD_READY report',
                              details={'problems': problems[:10]})]
    out = []

    def fail(code, message):
        out.append(D.make(code, 'error', 'counterpart', message, realm=report['realm'],
                          save_id=report['native_save_id']))
    for key in ('realm', 'installation_id', 'native_save_id', 'native_save_identity', 'build_fingerprint'):
        if report[key] != expected[key]:
            fail('WORLD_READY_WRONG_' + key.upper(), f'{key} differs from the expected endpoint')
    if report['session'] != expected['session']:
        fail('WORLD_READY_WRONG_SESSION', 'report from another process/session')
    for key in ('scene_settled', 'player_usable', 'bridge_synchronized'):
        if report[key] is not True:
            fail('WORLD_READY_' + key.upper() + '_FALSE', f'{key} not attested')
    if report['evidence'] != 'RUNTIME_OBSERVATION':
        fail('WORLD_READY_NOT_RUNTIME', 'synthetic readiness is never runtime readiness')
    if trust.get(report['provider']['provider_id']) != report['realm']:
        fail('WORLD_READY_UNTRUSTED', 'provider not trusted for this realm')
    return not out, D.sort(out)
