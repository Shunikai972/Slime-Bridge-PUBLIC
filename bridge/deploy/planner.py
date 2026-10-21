"""Deployment planner: current inventory + desired state -> ordered, checked operations.

Operations: INSTALL, UPDATE, REMOVE, DISABLE, ENABLE, ROLLBACK, QUARANTINE. A plan is only
``executable`` when every check passes; otherwise it lists blockers and changes nothing. Checks:
unit validity and realm, deployable status, build compatibility, final-state dependencies,
provided-id and file-path conflicts (case-insensitive), declared conflicts, downgrades (only via
an explicit ROLLBACK to a recorded unit) and removals that would orphan a dependent.

Inventory (``bridge-deploy-inventory`` v1) maps key -> {'unit', 'state': ENABLED|DISABLED|QUARANTINED}.
Disabled units keep their files under the disabled area; quarantined units keep theirs under the
quarantine area. Nothing is deleted except by an explicit REMOVE.
"""

from bridge.content.canonical import sha256_json
from bridge.core import diagnostics as D
from .units import DEPLOYABLE_STATUS, build_compatible, validate_unit

OPS = ('INSTALL', 'UPDATE', 'REMOVE', 'DISABLE', 'ENABLE', 'ROLLBACK', 'QUARANTINE')
STATES = ('ENABLED', 'DISABLED', 'QUARANTINED')
AREAS = {'ENABLED': 'live', 'DISABLED': 'disabled', 'QUARANTINED': 'quarantine'}


def empty_inventory(realm, build=None):
    return {'schema': 'bridge-deploy-inventory', 'schema_version': 1, 'realm': realm, 'build': build, 'units': {}}


def placed(unit, state):
    """Area-qualified paths a unit occupies in a given state."""
    area = AREAS[state]
    return {f"{area}/{f['path']}": f['sha256'] for f in unit['files']}


def _order(keys, units, reverse=False):
    """Dependency order among ``keys`` (requirements first); cycles fall back to key order."""
    keys = sorted(keys)
    deps = {k: [r for r in units[k]['requires'] if r in keys] for k in keys}
    done, out, visiting = set(), [], set()

    def visit(k):
        if k in done or k in visiting:
            return
        visiting.add(k)
        for r in sorted(deps[k]):
            visit(r)
        visiting.discard(k)
        done.add(k)
        out.append(k)
    for k in keys:
        visit(k)
    return list(reversed(out)) if reverse else out


def plan_deploy(inventory, desired, *, build=None, history=None):
    """Plan the change from ``inventory`` to ``desired``.

    ``desired`` is a list of {'key', 'state': ENABLED|DISABLED|REMOVED|QUARANTINED, 'unit'?: unit,
    'rollback': bool?, 'reasons': [..]?}. ``history`` maps key -> [previously committed units] and is
    the only source a ROLLBACK may target. Keys not mentioned keep their current state.
    """
    realm, current = inventory['realm'], inventory['units']
    history = history or {}
    diags, blockers = [], []

    def block(code, message, key=None, **details):
        blockers.append(D.make(code, 'error', 'deploy', message, subject=key, realm=realm, details=details or None))

    final = {k: {'unit': v['unit'], 'state': v['state']} for k, v in current.items()}
    intents = {}
    seen = set()
    for item in desired:
        key = item.get('key')
        if not isinstance(key, str) or key in seen:
            block('DESIRED_INVALID', 'desired entries need unique keys', key=str(key)[:160])
            continue
        seen.add(key)
        state = item.get('state')
        unit = item.get('unit')
        if state not in ('ENABLED', 'DISABLED', 'REMOVED', 'QUARANTINED'):
            block('DESIRED_INVALID', f'unknown desired state {state!r}', key=key)
            continue
        if unit is not None:
            problems = validate_unit(unit)
            if problems:
                blockers.extend(problems)
                continue
            if unit['key'] != key:
                block('UNIT_KEY_MISMATCH', 'unit key differs from the desired key', key=key)
                continue
            if unit['realm'] != realm:
                block('UNIT_WRONG_REALM', f"unit targets {unit['realm']}", key=key)
                continue
        existing = current.get(key)
        if state == 'REMOVED':
            if existing is None:
                diags.append(D.make('NOTHING_TO_REMOVE', 'info', 'deploy', 'key not installed', subject=key))
                continue
            final.pop(key)
            intents[key] = 'REMOVE'
            continue
        if state == 'QUARANTINED':
            if existing is None:
                block('NOTHING_TO_QUARANTINE', 'key not installed', key=key)
                continue
            final[key] = {'unit': existing['unit'], 'state': 'QUARANTINED'}
            intents[key] = 'QUARANTINE'
            continue
        target = unit or (existing and existing['unit'])
        if target is None:
            block('UNIT_REQUIRED', 'installing needs a unit', key=key)
            continue
        if state == 'ENABLED' and target['status'] not in DEPLOYABLE_STATUS:
            block('UNIT_NOT_DEPLOYABLE', f"status {target['status']} cannot be enabled", key=key)
            continue
        if existing is not None and existing['state'] == 'QUARANTINED' and unit is None:
            block('QUARANTINED_NEEDS_NEW_UNIT', 'a quarantined unit is only replaced, never re-enabled as is', key=key)
            continue
        if build is not None and state == 'ENABLED' and build_compatible(target, build) != 'COMPATIBLE':
            diags.append(D.make('UNIT_REVERIFY_REQUIRED', 'warning', 'deploy',
                                'not proven against the installed build; re-verify after install',
                                subject=key, realm=realm, evidence='NEEDS_LOCAL_RUNTIME_PROOF'))
        if existing is None:
            intents[key] = 'INSTALL'
        elif existing['unit']['unit_id'] != target['unit_id']:
            older = tuple(target['version']) < tuple(existing['unit']['version'])
            if item.get('rollback'):
                if target['unit_id'] not in {u['unit_id'] for u in history.get(key, [])}:
                    block('ROLLBACK_TARGET_UNKNOWN', 'rollback only to a previously committed unit', key=key)
                    continue
                intents[key] = 'ROLLBACK'
            elif older:
                block('DOWNGRADE_REFUSED', 'older version; use an explicit rollback', key=key,
                      installed=existing['unit']['version'], candidate=target['version'])
                continue
            else:
                intents[key] = 'UPDATE'
        elif existing['state'] != state:
            intents[key] = 'ENABLE' if state == 'ENABLED' else 'DISABLE'
        else:
            continue
        final[key] = {'unit': target, 'state': state}

    enabled = {k: v['unit'] for k, v in final.items() if v['state'] == 'ENABLED'}
    for key, unit in sorted(enabled.items()):
        for req in unit['requires']:
            if req not in enabled:
                block('DEPENDENCY_MISSING', f'requires {req}', key=key, requires=req)
        for other in unit['conflicts']:
            if other in enabled:
                block('DECLARED_CONFLICT', f'conflicts with {other}', key=key, other=other)
    providers = {}
    for key, unit in enabled.items():
        for cid in unit['provides']:
            providers.setdefault(cid, []).append(key)
    for cid, keys in sorted(providers.items()):
        if len(keys) > 1:
            block('PROVIDES_CONFLICT', f'{cid} provided twice', key=sorted(keys)[0], canonical_id=cid, keys=sorted(keys))
    paths = {}
    for key, entry in final.items():
        for path in placed(entry['unit'], entry['state']):
            paths.setdefault(path.casefold(), set()).add(key)
    for path, keys in sorted(paths.items()):
        if len(keys) > 1:
            block('FILE_CONFLICT', 'two units place the same file', key=sorted(keys)[0], path=path[:200],
                  keys=sorted(keys))

    operations = []
    teardown = [k for k, op in intents.items() if op in ('REMOVE', 'DISABLE', 'QUARANTINE')]
    setup = [k for k, op in intents.items() if k not in teardown]
    all_units = {k: (final.get(k) or current[k])['unit'] for k in intents}
    for key in _order(teardown, all_units, reverse=True) + _order(setup, all_units):
        op = intents[key]
        before = current.get(key)
        after = final.get(key)
        old_files = placed(before['unit'], before['state']) if before else {}
        new_files = placed(after['unit'], after['state']) if after else {}
        operations.append({
            'op': op, 'key': key,
            'from_unit': before and before['unit']['unit_id'], 'to_unit': after and after['unit']['unit_id'],
            'from_state': before and before['state'], 'to_state': after and after['state'],
            'put': sorted({p: h for p, h in new_files.items() if old_files.get(p) != h}.items()),
            'delete': sorted(p for p in old_files if p not in new_files),
            'preconditions': sorted(old_files.items()), 'postconditions': sorted(new_files.items()),
            'reasons': next((list(i.get('reasons', [])) for i in desired if i.get('key') == key), [])})
    plan = {'schema': 'bridge-deploy-plan', 'schema_version': 1, 'realm': realm, 'build': build,
            'base_inventory': sha256_json(inventory), 'operations': operations,
            'final_units': {k: {'unit': v['unit'], 'state': v['state']} for k, v in sorted(final.items())},
            'blockers': D.sort(blockers), 'diagnostics': D.sort(diags), 'executable': not blockers}
    plan['plan_id'] = sha256_json(plan)
    return plan
