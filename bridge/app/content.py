"""Content manager: one catalogue over classification, conversion, packages, deployment, verification.

Rows are per canonical species. Each row says where the species is native, what conversion did per
direction, which package (id/version/hash) exists, whether it is installed/enabled/quarantined in
each realm and what verification has established (NOT_RUN unless runtime receipts exist). Actions
return *plans* (build-graph targets, deploy plans, proof plans); nothing is executed here.
"""

from bridge.deploy.planner import plan_deploy
from bridge.verification.plans import package_plan

GROUPS = ('SHARED_NATIVE', 'SR1_ONLY', 'SR2_ONLY', 'CONVERTED', 'PENDING_ADAPTER', 'QUARANTINED')


def catalogue(batches, *, inventories=None, packages=None, verifications=None):
    """``batches``: Phase 1 batch outputs (any keys; each report names its direction); ``inventories``: realm ->
    deploy inventory; ``packages``: package_id -> package; ``verifications``: package_id -> aggregate."""
    inventories, packages, verifications = inventories or {}, packages or {}, verifications or {}
    rows = {}
    for _, out in sorted(batches.items()):
        report = out['report']
        direction = report['direction']
        target = direction.split('->')[1]
        for entry in report['entries']:
            cid = entry['canonical_id']
            row = rows.setdefault(cid, {'canonical_id': cid, 'classification': entry['classification'],
                                        'directions': {}, 'groups': set()})
            package = packages.get(entry.get('package_id')) if entry.get('package_id') else None
            installed = None
            inventory = inventories.get(target)
            if inventory is not None:
                unit = inventory['units'].get(f'generated:{cid}:{target}')
                installed = unit and {'state': unit['state'], 'unit_id': unit['unit']['unit_id'],
                                      'version': unit['unit']['version']}
            verification = verifications.get(entry.get('package_id'))
            row['directions'][direction] = {
                'target_realm': target, 'conversion': entry['conversion'], 'final': entry['final'],
                'components': entry.get('components'), 'reasons': entry.get('reasons', []),
                'package_id': entry.get('package_id'),
                'package_version': package and package['converter'].get('converter_version'),
                'source_build': package and package['source']['build_fingerprint'],
                'definition_hash': package and package['source']['definition_hash'],
                'installed': installed,
                'verification': verification['verdict'] if verification else 'NOT_RUN'}
    for row in rows.values():
        cls = row['classification']
        if cls in ('SHARED_NATIVE', 'SR1_ONLY', 'SR2_ONLY'):
            row['groups'].add(cls)
        if cls in ('BLOCKED_UNKNOWN', 'QUARANTINED'):
            row['groups'].add('QUARANTINED')
        for d in row['directions'].values():
            if d['conversion'] == 'CONVERTED':
                row['groups'].add('CONVERTED')
            if d['final'] == 'PENDING_ADAPTER' or 'PENDING_ADAPTER' in (d['components'] or {}).values():
                row['groups'].add('PENDING_ADAPTER')
            if d['final'] == 'QUARANTINED' or (d['installed'] or {}).get('state') == 'QUARANTINED':
                row['groups'].add('QUARANTINED')
        row['groups'] = sorted(row['groups'])
    ordered = [rows[k] for k in sorted(rows)]
    counts = {g: sum(1 for r in ordered if g in r['groups']) for g in GROUPS}
    return {'schema': 'bridge-content-catalogue', 'schema_version': 1, 'rows': ordered, 'counts': counts}


def filter_rows(catalogue_doc, group=None, text=None):
    rows = catalogue_doc['rows']
    if group:
        rows = [r for r in rows if group in r['groups']]
    if text:
        needle = text.casefold()
        rows = [r for r in rows if needle in r['canonical_id'].casefold()]
    return rows


def rebuild_plan(canonical_ids, direction):
    """Build-graph targets for a partial rebuild (executed by ``bridge.packages.buildgraph``)."""
    return {'kind': 'REBUILD', 'direction': direction,
            'targets': [f'package:{cid}' for cid in sorted(set(canonical_ids))], 'destructive': False}


def state_change_plan(journal, realm, canonical_id, state, *, reasons=()):
    """Deploy plan to DISABLE / ENABLE / QUARANTINE / REMOVE one generated unit (checked, not executed)."""
    key = f'generated:{canonical_id}:{realm}'
    return plan_deploy(journal.inventory(realm), [{'key': key, 'state': state, 'reasons': list(reasons)}],
                       history=journal.history())


def rollback_plan(journal, realm, canonical_id):
    """Deploy plan back to the previous committed unit for this species, if one exists."""
    key = f'generated:{canonical_id}:{realm}'
    current = journal.inventory(realm)['units'].get(key)
    previous = [u for u in journal.history().get(key, []) if not current or u['unit_id'] != current['unit']['unit_id']]
    if not previous:
        return None
    return plan_deploy(journal.inventory(realm), [{'key': key, 'state': 'ENABLED', 'unit': previous[-1],
                                                   'rollback': True}], history=journal.history())


def reverify_plan(package):
    return package_plan(package)
