"""User-created Largos in the launcher: import, validate, version, compatibility, enable/disable.

The user designs and models every Largo. This service only reads their package folders (through
``bridge.sdk.largo``, which hashes files read-only and never rewrites them), shows parents,
version, hash, per-realm target compatibility and conflicts, and turns valid plans into deploy units
whose enable/disable/remove goes through the ordinary journaled deployment planner. It never
generates, edits, re-encodes or renames Largo geometry or textures.
"""

from bridge.deploy.planner import plan_deploy
from bridge.deploy.units import unit_from_largo_plan
from bridge.sdk.largo import import_packages


def library(root, *, ledger, known_base_species=None, target_profiles=(), parent_capabilities=None,
            target_context=None):
    """Rows for the LARGOS page plus the updated ledger the caller persists."""
    imported = import_packages(root, ledger=ledger, known_base_species=known_base_species,
                               target_profiles=target_profiles, parent_capabilities=parent_capabilities,
                               target_context=target_context)
    rows = []
    for record in imported['records']:
        plans = {p.get('target_realm'): p for p in record['plans'] if p.get('target_realm')}
        rows.append({'canonical_largo_id': record['canonical_largo_id'], 'package_dir': record['package_dir'],
                     'status': record['status'], 'decision': record['decision'],
                     'package_hash': record['package_hash'],
                     'version': next((p.get('version') for p in plans.values() if p.get('version')), None),
                     'parents': next((p.get('parents') for p in plans.values() if p.get('parents')), []),
                     'targets': {realm: {'final': plan['final'], 'registrable': plan.get('registrable', False),
                                         'reasons': plan.get('reasons', []),
                                         'evidence': plan.get('evidence', 'NEEDS_LOCAL_RUNTIME_PROOF')}
                                 for realm, plan in plans.items()},
                     'diagnostics': record['diagnostics'], 'plans': plans})
    return {'rows': rows, 'ledger': imported['ledger']}


def enable_plan(journal, row, realm):
    """Deploy plan that installs/enables one imported Largo in ``realm`` (checked, not executed)."""
    plan = row['plans'].get(realm)
    if plan is None or plan.get('final') not in ('EXPERIMENTAL', 'PENDING_ADAPTER'):
        return None
    unit = unit_from_largo_plan(plan)
    state = 'ENABLED' if plan['final'] == 'EXPERIMENTAL' else 'DISABLED'
    return plan_deploy(journal.inventory(realm), [{'key': unit['key'], 'state': state, 'unit': unit}],
                       history=journal.history())


def disable_plan(journal, canonical_largo_id, realm):
    return plan_deploy(journal.inventory(realm), [{'key': f'largo:{canonical_largo_id}:{realm}', 'state': 'DISABLED'}],
                       history=journal.history())
