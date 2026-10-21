"""Species parity and foreign-spawn services for the launcher (read models and plans only).

Composes the reviewed parity / adapter / foreign-spawn / dev-spawn layers into launcher-shaped
read models: a validated parity report, the settings -> policy bridge for
``species.foreign_spawn`` (NATIVE_ONLY default; per-species overrides) and a dev-spawn decision
plan. Nothing here executes natively, launches a game or writes game data: invalid inputs fail
closed with explicit diagnostics instead of raising, and native execution stays BLOCKED_ON_
LOCAL_QUERY exactly as the underlying descriptors declare.
"""

import json
from pathlib import Path

from bridge.content.canonical import REALMS
from bridge.conversion import dev_spawn as dev_spawn_module
from bridge.conversion import foreign_spawn, species_parity
from bridge.core import diagnostics as D

MAX_DOC_BYTES = 8 * 1024 * 1024


def _load(path):
    """(document, None) or (None, problem). Never raises."""
    try:
        path = Path(path)
        if not path.is_file() or path.is_symlink():
            return None, f'not a readable regular file: {path}'
        if path.stat().st_size > MAX_DOC_BYTES:
            return None, f'oversized document: {path}'
        return json.loads(path.read_text(encoding='utf-8-sig')), None
    except (OSError, ValueError, RecursionError) as exc:
        return None, f'unreadable document {path}: {type(exc).__name__}'


def _species_row(row):
    if row['classification'] == 'SHARED_NATIVE':
        return {'canonical_id': row['canonical_id'], 'classification': 'SHARED_NATIVE',
                'display_name': row['display_name'],
                'native_realms': [realm for realm in REALMS if row['presence'][realm]],
                'import': 'NOT_REQUIRED_NATIVE_BOTH_REALMS'}
    states = row['states']
    spawn = row['foreign_world_spawn']
    return {'canonical_id': row['canonical_id'], 'classification': row['classification'],
            'display_name': row['display_name'],
            'native_realm': species_parity.CLASS_SOURCE[row['classification']],
            'target_realm': species_parity.CLASS_TARGET[row['classification']],
            'adapter_id': row['adapter']['adapter_id'] if row['adapter'] else None,
            'registration': states['runtime_registration']['state'],
            'inventory': states['inventory']['state'], 'release': states['release']['state'],
            'save_load': states['save_load']['state'], 'behavior': states['behavior']['classification'],
            'diet': states['diet']['state'], 'economy': states['economy']['state'],
            'plort': states['plort']['state'], 'visual': states['visual']['state'],
            'foreign_world_spawn': {'applicable': spawn['applicable'],
                                    'requested': spawn.get('requested'), 'active': spawn.get('active'),
                                    'mode': spawn.get('mode'), 'integration': spawn.get('integration')},
            'blockers': [entry['code'] for entry in row['blockers']]}


def parity_report(parity_path):
    """(report, diagnostics): validated summary of one ``bridge-species-parity`` document."""
    document, problem = _load(parity_path)
    if problem is not None:
        return None, [D.make('SPECIES_PARITY_UNREADABLE', 'warning', 'launcher', problem,
                             details={'path': str(parity_path)})]
    try:
        problems = species_parity.validate_parity(document)
    except Exception as exc:
        # The validator must be total; a crash inside it still fails closed toward the UI.
        return None, [D.make('SPECIES_PARITY_VALIDATION_ERROR', 'error', 'conversion',
                             'species parity validation crashed',
                             details={'exception': type(exc).__name__})]
    if problems:
        return None, [D.make('SPECIES_PARITY_INVALID', 'error', 'conversion',
                             'species parity document failed validation',
                             details={'problems': problems[:8], 'count': len(problems)})]
    roster = document['roster']
    report = {
        'schema': document['schema'], 'schema_version': document['schema_version'],
        'input_hashes': document['inputs']['input_hashes'],
        'builds': document['builds'],
        'roster': {key: roster[key] for key in ('counts', 'names', 'shared_native', 'sr1_only', 'sr2_only')},
        'blocked_queries': document['blocked_queries'],
        'species': [_species_row(row) for row in document['species']],
    }
    diagnostics = [D.make('SPECIES_PARITY_READY', 'info', 'conversion',
                          'species parity document validated')]
    unproven = [row['canonical_id'] for row in document['species']
                if row['classification'] != 'SHARED_NATIVE'
                and row['states']['runtime_registration'].get('execution') == 'BLOCKED_ON_LOCAL_QUERY']
    if unproven:
        diagnostics.append(D.make('SPECIES_RUNTIME_UNPROVEN', 'warning', 'conversion',
                                  'imported species are prepared but not native-registered',
                                  details={'species': unproven[:32], 'count': len(unproven)}))
    return report, D.sort(diagnostics)


def foreign_spawn_policy(species_section, *, imported_ids):
    """(policy, diagnostics): the settings ``species`` section as a fail-closed policy document.

    An absent section is the normal "defaults apply" case (no diagnostic); a present but
    malformed section is repaired toward NATIVE_ONLY with diagnostics.
    """
    section = species_section.get('foreign_spawn') if isinstance(species_section, dict) else None
    document = None if not isinstance(section, dict) else {
        'schema': foreign_spawn.POLICY_SCHEMA,
        'schema_version': foreign_spawn.POLICY_SCHEMA_VERSION,
        'mode': section.get('mode'),
        'overrides': section.get('overrides'),
    }
    policy, phase1 = foreign_spawn.normalize_policy(document, imported_ids=imported_ids)
    diagnostics = [D.from_phase1(item, 'launcher') for item in phase1]
    return policy, D.sort(diagnostics)


def species_section_from_policy(policy):
    """The settings-compatible ``species`` section for one policy document."""
    return {'foreign_spawn': {'mode': policy['mode'],
                              'overrides': {key: value for key, value in sorted(policy.get('overrides', {}).items())}}}


def foreign_spawn_effective(policy, *, imported_ids):
    """Per-species requested vs active view (active stays false behind Q034 until integrated)."""
    return foreign_spawn.effective_policy(policy, imported_ids=imported_ids)


def dev_spawn_plan(request, *, classification_path, adapters_dir, install, policy=None):
    """(decision document, diagnostics): the LAB test-spawn authorization for one request.

    ``install`` carries the verified facts ({'realm', 'root', 'lab', 'build_fingerprint'}) from
    the caller; this service never touches game directories itself.
    """
    classification, problem = _load(classification_path)
    if problem is not None:
        return None, [D.make('DEV_SPAWN_CLASSIFICATION_UNREADABLE', 'warning', 'launcher', problem)]
    adapters = []
    for path in sorted(Path(adapters_dir).glob('adapter.*.json')):
        document, problem = _load(path)
        if problem is None and isinstance(document, dict):
            adapters.append(document)
    if not adapters:
        return None, [D.make('DEV_SPAWN_ADAPTERS_MISSING', 'warning', 'launcher',
                             'no prepared adapter descriptors found',
                             details={'adapters_dir': str(adapters_dir)})]
    document = dev_spawn_module.plan_dev_spawn(request, classification, adapters, install, policy)
    problems = dev_spawn_module.validate_dev_spawn(document)
    if problems:
        return None, [D.make('DEV_SPAWN_PLAN_INVALID', 'error', 'conversion',
                             'dev spawn decision document failed validation',
                             details={'problems': problems[:8]})]
    diagnostics = [D.make('DEV_SPAWN_AUTHORIZED' if document['decision'] == 'AUTHORIZED'
                          else 'DEV_SPAWN_REFUSED',
                          'info' if document['decision'] == 'AUTHORIZED' else 'warning',
                          'launcher', f'dev spawn decision {document["decision"]}',
                          subject=document['request']['canonical_id'],
                          realm=document['request']['target_realm'],
                          details={'refusals': [entry['code'] for entry in document['refusals']],
                                   'execution': document['execution']['state']})]
    return document, D.sort(diagnostics)
