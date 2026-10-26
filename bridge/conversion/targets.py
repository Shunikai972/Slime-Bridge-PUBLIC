"""Bidirectional target side: realm facts, direction rules, packages, registration and verification plans.

Both directions (SR2 -> SR1 and SR1 -> SR2) use the same generic code; only data differs:
realm facts (loader/runtime/Unity version from the verified lab recon), per-kind asset
transforms, target material/behaviour profiles and the registration operation table. Real
registration operations carry ``api: None`` and a LOCAL_QUERY until the local provider verifies the exact
SRML / MelonLoader calls; nothing here invents a native API. Plans are deterministic, contain
no target numeric ids (allocation belongs to the local registration provider) and never
register a canonical id that is native in the target.
"""

import copy

from bridge.content.canonical import REALMS, Evidence, diag, sha256_json, sort_diagnostics

# Verified lab facts (docs/RECON.md, docs/native-contracts). Other builds must override them.
REALM_FACTS = {
    'SR1': {'loader': 'SRML', 'runtime': 'Mono', 'unity': '2019.4.29f1', 'evidence': 'docs/RECON.md lab build'},
    'SR2': {'loader': 'MelonLoader', 'runtime': 'IL2CPP', 'unity': '2021.3.28f1', 'evidence': 'docs/RECON.md lab build'},
}
REGISTRATION_OPS = ('RESERVE_CANONICAL_IDENTITY', 'REGISTER_PLORT', 'REFERENCE_NATIVE_PLORT', 'REGISTER_ICON',
                    'BUILD_MATERIALS', 'BUILD_PREFAB', 'REGISTER_SLIME_DEFINITION', 'REGISTER_DIET',
                    'ATTACH_BEHAVIOUR', 'REGISTER_LOCALIZATION', 'REGISTER_PEDIA', 'REGISTER_MARKET',
                    'BIND_PERSISTENCE')
_TRANSFORMS = {'mesh': 'rebuild_mesh_for_target_unity', 'texture': 'reencode_texture_for_target',
               'animation_clip': 'rebind_clip_to_target_hierarchy', 'audio': 'copy_audio_metadata_checked',
               'animator_controller': 'rebuild_animator_controller', 'vfx': 'rebuild_vfx_optional',
               'sprite': 'rebuild_sprite_for_target'}
_FORBIDDEN_PAYLOAD_KEYS = {'numeric_id', 'native_id', 'slot_index', 'save_id'}


def _registration_table(realm, query):
    return {op: {'api': None, 'evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY), 'local_query': query}
            for op in REGISTRATION_OPS}


def builtin_target_profile(realm):
    """Real-target profile with only verified facts: no material templates, core behaviour only."""
    if realm not in REALMS:
        raise ValueError(realm)
    query = {'SR1': 'Q007', 'SR2': 'Q008'}[realm]
    core = {cap: {'mode': 'NATIVE', 'evidence': str(Evidence.NEEDS_LOCAL_RUNTIME_PROOF)}
            for cap in ('standard.vacpack', 'standard.diet', 'standard.plort_production')}
    return {'schema': 'bridge-target-profile', 'schema_version': 1, 'realm': realm, 'synthetic': False,
            **{k: v for k, v in REALM_FACTS[realm].items() if k != 'evidence'},
            'facts_evidence': REALM_FACTS[realm]['evidence'], 'build_fingerprint': None,
            'material_profile': {'schema': 'bridge-target-material-profile', 'schema_version': 1, 'realm': realm,
                                 'synthetic': False, 'templates': [],
                                 'local_query': {'SR1': 'Q003', 'SR2': 'Q006'}[realm]},
            'behaviour_profile': {'schema': 'bridge-target-behaviour-profile', 'schema_version': 1, 'realm': realm,
                                  'synthetic': False, 'capabilities': core},
            'registration': _registration_table(realm, query)}


def validate_target_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'bridge-target-profile' or \
            profile.get('schema_version') != 1 or profile.get('realm') not in REALMS or \
            type(profile.get('synthetic')) is not bool:
        raise ValueError('Expected bridge-target-profile v1')
    for key in ('loader', 'runtime', 'unity'):
        if not isinstance(profile.get(key), str) or not profile[key]:
            raise ValueError(f'Target profile needs {key}')
    table = profile.get('registration')
    if not isinstance(table, dict) or set(table) != set(REGISTRATION_OPS):
        raise ValueError('Registration table must describe every registration operation')
    for op, row in table.items():
        if not isinstance(row, dict) or (row.get('api') is not None and not isinstance(row['api'], str)):
            raise ValueError(f'Invalid registration entry {op}')
        if row.get('api') is None and not row.get('local_query'):
            raise ValueError(f'{op} without a verified api needs a local_query')
    fingerprint = profile.get('build_fingerprint')
    if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint):
        raise ValueError('build_fingerprint must be a non-empty string or None')
    return profile


def direction(source, target):
    if source not in REALMS or target not in REALMS or source == target:
        raise ValueError(f'Invalid direction {source}->{target}')
    return f'{source}->{target}'


def asset_transforms(extraction_plan, source, target_profile):
    """Per-artifact direction-specific transform steps (Unity version/runtime aware)."""
    src = REALM_FACTS[source]
    out = []
    for item in extraction_plan['items']:
        out.append({'artifact': item['artifact'], 'kind': item['kind'], 'source_node': item['node'],
                    'source_hash': item['source_hash'], 'required': item['required'],
                    'transform': _TRANSFORMS[item['extract_as']],
                    'from_unity': src['unity'], 'to_unity': target_profile['unity'],
                    'to_runtime': target_profile['runtime'],
                    'evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY), 'local_query': 'Q011'})
    return out


def assemble_package(*, ir, source, target_profile, extraction_plan, material_plans, behaviour_plan,
                     plort_targets, converter, child_artifacts=None, synthetic=None):
    """Metadata-only target package; artifacts are content-addressed references to local files.

    ``child_artifacts`` maps a node to the artifacts of its direct extractable dependencies
    (e.g. a face expression's textures) so the package is self-sufficient for the local adapter.
    """
    artifacts = asset_transforms(extraction_plan, source, target_profile)
    by_node = {a['source_node']: a['artifact'] for a in artifacts}
    children = child_artifacts or {}
    plans = {p['material']: p for p in material_plans}

    def material_ref(mid):
        return None if mid is None else {'material': mid, 'template': plans.get(mid, {}).get('template')}

    visual = ir['visual']
    renderers = [{'id': r['id'], 'kind': r['kind'], 'node': r['node'], 'mesh_artifact': by_node.get(r['mesh']),
                  'materials': [material_ref(m) for m in r['materials']], 'bones': list(r['bones']),
                  'root_bone': r['root_bone']} for r in visual['renderers']]
    materials = []
    for plan in sorted(material_plans, key=lambda p: p['material']):
        plan = copy.deepcopy(plan)
        for assignment in plan['assignments']:
            if 'texture' in assignment:
                assignment['texture_artifact'] = by_node.get(assignment['texture'])
        materials.append(plan)
    diet = ir['gameplay']['diet']
    package = {
        'schema': 'bridge-target-package', 'schema_version': 1,
        'canonical_id': ir['canonical_id'], 'direction': direction(source, target_profile['realm']),
        'source_realm': source, 'target_realm': target_profile['realm'],
        'synthetic': bool(synthetic) if synthetic is not None else (ir['provenance']['synthetic'] or
                                                                    target_profile['synthetic']),
        'source': {'build_fingerprint': ir['identity']['source_build_fingerprint'],
                   'definition_hash': ir['identity']['native_definition']['hash'],
                   'closure_hash': ir['provenance']['dependency_closure_hash'],
                   'extraction_plan_hash': extraction_plan['plan_hash']},
        'target': {'realm': target_profile['realm'], 'loader': target_profile['loader'],
                   'runtime': target_profile['runtime'], 'unity': target_profile['unity'],
                   'build_fingerprint': target_profile.get('build_fingerprint')},
        'artifacts': artifacts,
        'prefab': {'root': visual['root'], 'hierarchy': copy.deepcopy(visual['hierarchy']), 'renderers': renderers,
                   'face': visual['face'] and {'node': visual['face']['id'], 'expressions': [
                       {'node': e, 'textures': children.get(e, [])} for e in visual['face']['expressions']]},
                   'animator': visual['animator'] and {'node': visual['animator']['id'],
                                                       'artifact': by_node.get(visual['animator']['id']),
                                                       'clips': [by_node.get(c) for c in visual['animator']['clips']]},
                   'vfx': [{'node': v['id'], 'artifact': by_node.get(v['id'])} for v in visual['vfx']],
                   'audio': [{'node': a['id'], 'artifact': by_node.get(a['id'])} for a in visual['audio']]},
        'materials': materials,
        'behaviour': copy.deepcopy(behaviour_plan),
        'gameplay': {'diet': diet and {
            'food_groups': [f'food_group.{g}' for g in diet['food_groups']],
            'favourite_foods': list(diet['favourite_foods']),
            'identity_evidence': 'DERIVED', 'local_query': 'Q017'},
            'plorts': plort_targets},
        'associated': {'icon': ir['associated']['icon'] and {
            'node': ir['associated']['icon']['id'], 'artifact': by_node.get(ir['associated']['icon']['id']),
            'textures': children.get(ir['associated']['icon']['id'], [])},
            'pedia': ir['associated']['pedia'], 'localization': ir['associated']['localization'],
            'market': ir['associated']['market'], 'spawn': ir['associated']['spawn']},
        'converter': converter,
    }
    package['package_id'] = sha256_json(package)
    return package


def _op(canonical, kind, payload, depends, table, optional=False):
    row = table[kind]
    op = {'kind': kind, 'canonical_id': canonical, 'payload': payload, 'depends_on': sorted(depends),
          'optional': optional, 'native_api': row['api'],
          'evidence': str(Evidence.NEEDS_LOCAL_RUNTIME_PROOF) if row['api'] else str(Evidence.BLOCKED_ON_LOCAL_QUERY),
          'local_query': row.get('local_query')}
    op['op_id'] = sha256_json({'kind': kind, 'canonical_id': canonical, 'payload': payload,
                               'depends_on': op['depends_on']})[:32]
    return op


def plan_registration(package, target_profile, target_native_canonicals):
    """Ordered, idempotent registration operations for one converted species."""
    table = target_profile['registration']
    cid = package['canonical_id']
    ops = []
    reserve = _op(cid, 'RESERVE_CANONICAL_IDENTITY', {'package_id': package['package_id']}, [], table)
    ops.append(reserve)
    plort_ops = []
    for plort in package['gameplay']['plorts']:
        if plort['canonical_id'] in target_native_canonicals:
            plort_ops.append(_op(cid, 'REFERENCE_NATIVE_PLORT', {'plort': plort['canonical_id']}, [reserve['op_id']], table))
        else:
            reg = _op(cid, 'REGISTER_PLORT', {'plort': plort['canonical_id'], 'source_node': plort['node']},
                      [reserve['op_id']], table)
            plort_ops.append(reg)
            plort_ops.append(_op(cid, 'REGISTER_MARKET', {'plort': plort['canonical_id'],
                                                          'market': plort.get('market')}, [reg['op_id']], table,
                                 optional=True))
    ops += plort_ops
    deferred, blockers = [], []
    convertible = {'READY', 'DEGRADED'}
    essential = {m['material'] for r in package['prefab']['renderers'] for m in r['materials'] if m}
    buildable = []
    for plan in package['materials']:
        if plan['status'] in convertible:
            buildable.append(plan['material'])
            continue
        deferred.append({'material': plan['material'], 'status': plan['status']})
        if plan['material'] in essential:
            blockers.append(f"MATERIAL:{plan['material']}:{plan['status']}")
    icon = package['associated']['icon']
    icon_op = _op(cid, 'REGISTER_ICON', {'artifact': icon['artifact']}, [reserve['op_id']], table) \
        if icon and icon.get('artifact') else None
    if icon and not icon.get('artifact'):
        deferred.append({'asset': 'icon', 'node': icon['node']})
        blockers.append(f"ICON_ARTIFACT_MISSING:{icon['node']}")
    materials = _op(cid, 'BUILD_MATERIALS', {'materials': sorted(buildable)}, [reserve['op_id']], table)
    prefab = _op(cid, 'BUILD_PREFAB', {'root': package['prefab']['root'],
                                       'meshes': sorted({r['mesh_artifact'] for r in package['prefab']['renderers']
                                                         if r['mesh_artifact']})},
                 [materials['op_id']], table)
    definition = _op(cid, 'REGISTER_SLIME_DEFINITION', {'package_id': package['package_id']},
                     [prefab['op_id'], *([icon_op['op_id']] if icon_op else []),
                      *(o['op_id'] for o in plort_ops if not o['optional'])], table)
    ops += [op for op in (icon_op, materials, prefab, definition) if op]
    if package['gameplay']['diet']:
        ops.append(_op(cid, 'REGISTER_DIET', {'diet': package['gameplay']['diet'],
                                              'produces': [p['canonical_id'] for p in package['gameplay']['plorts']]},
                       [definition['op_id']], table))
    for cap in package['behaviour']['capabilities']:
        if cap['status'] != 'READY':
            # An unimplemented adapter or unsupported capability is never an executable operation.
            deferred.append({'capability': cap['id'], 'mode': cap['mode'], 'policy': cap['policy']})
            if cap['policy'] in ('REQUIRED', 'UNKNOWN'):
                blockers.append(f"CAPABILITY:{cap['id']}:{cap['mode']}")
            continue
        ops.append(_op(cid, 'ATTACH_BEHAVIOUR', {'capability': cap['id'], 'mode': cap['mode'],
                                                 'adapter': cap['adapter']},
                       [definition['op_id']], table, optional=cap['policy'] == 'DEGRADABLE'))
    for kind, key in (('REGISTER_LOCALIZATION', 'localization'), ('REGISTER_PEDIA', 'pedia')):
        if package['associated'][key]:
            ops.append(_op(cid, kind, {key: package['associated'][key]}, [definition['op_id']], table, optional=True))
    ops.append(_op(cid, 'BIND_PERSISTENCE', {'canonical_id': cid}, [definition['op_id']], table))
    blocked = any(o['evidence'] == Evidence.BLOCKED_ON_LOCAL_QUERY for o in ops)
    plan = {'schema': 'bridge-registration-plan', 'schema_version': 1, 'canonical_id': cid,
            'package_id': package['package_id'], 'target_realm': package['target_realm'], 'operations': ops,
            'synthetic': package['synthetic'], 'deferred': deferred, 'blockers': sorted(blockers),
            'registrable': not blockers,
            'evidence': str(Evidence.BLOCKED_ON_LOCAL_QUERY if blocked else Evidence.NEEDS_LOCAL_RUNTIME_PROOF)}
    plan['plan_hash'] = sha256_json(plan)
    return plan


def validate_registration_plan(plan, package, target_native_canonicals) -> list:
    """Safety gate: refuse duplicate native content, foreign writes, unordered deps, numeric ids."""
    out = []
    cid = plan['canonical_id']
    if cid in target_native_canonicals:
        out.append(diag('WOULD_DUPLICATE_NATIVE', 'error', cid, 'canonical id already native in target'))
    seen = set()
    artifacts = {a['artifact'] for a in package['artifacts']}
    for op in plan['operations']:
        if op['op_id'] in seen:
            out.append(diag('DUPLICATE_OPERATION', 'error', cid, op['op_id']))
        for dep in op['depends_on']:
            if dep not in seen:
                out.append(diag('DEPENDENCY_ORDER', 'error', cid, f"{op['kind']} before {dep}"))
        seen.add(op['op_id'])
        if op['canonical_id'] != cid or op['kind'] not in REGISTRATION_OPS:
            out.append(diag('FOREIGN_OPERATION', 'error', cid, op['kind']))
        stack = [op['payload']]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                bad = _FORBIDDEN_PAYLOAD_KEYS & set(item)
                if bad:
                    out.append(diag('TARGET_NATIVE_ID_IN_PLAN', 'error', cid, ','.join(sorted(bad))))
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        if op['kind'] == 'REGISTER_PLORT' and op['payload']['plort'] in target_native_canonicals:
            out.append(diag('WOULD_DUPLICATE_NATIVE', 'error', cid, op['payload']['plort']))
        if op['kind'] == 'REGISTER_ICON' and not op['payload'].get('artifact'):
            out.append(diag('NULL_ARTIFACT', 'error', cid, 'REGISTER_ICON without an artifact'))
        if op['kind'] == 'ATTACH_BEHAVIOUR':
            cap = next((c for c in package['behaviour']['capabilities'] if c['id'] == op['payload']['capability']),
                       None)
            if cap is None or cap['status'] != 'READY':
                out.append(diag('UNREADY_ADAPTER_PLANNED', 'error', cid, op['payload']['capability']))
    for renderer in package['prefab']['renderers']:
        if renderer['mesh_artifact'] is not None and renderer['mesh_artifact'] not in artifacts:
            out.append(diag('ARTIFACT_NOT_IN_PACKAGE', 'error', cid, renderer['mesh_artifact']))
    if plan['package_id'] != package['package_id']:
        out.append(diag('PACKAGE_MISMATCH', 'error', cid, 'plan built for another package'))
    return sort_diagnostics(out)


def plan_verification(package, registration_plan):
    """Local LAB checks required before any runtime claim. Cloud never marks these passed."""
    cid = package['canonical_id']
    checks = [('TARGET_LOADS_PACKAGE', 'LAB target boots with the package and no loader errors'),
              ('REGISTRY_CONTAINS_SPECIES', f'{cid} resolves through the target registry provider'),
              ('SPAWN_AND_RENDER', 'spawned instance renders with expected meshes/material slots'),
              ('VACPACK_ROUNDTRIP', 'vacuum, store and release'),
              ('SAVE_RELOAD_PERSISTS', 'save, reload and verify identity (no orphaned save data)')]
    if package['gameplay']['diet']:
        checks.append(('DIET_FEED_PRODUCES_PLORT', 'feed a favourite/food group and observe the plort'))
    for cap in package['behaviour']['capabilities']:
        if cap['mode'] != 'UNSUPPORTED':
            checks.append((f"CAPABILITY:{cap['id']}", f"{cap['mode']} realization behaves as described"))
    for plan in package['materials']:
        checks.append((f"MATERIAL:{plan['material']}", f"template {plan['template']} visually matches source"))
    rows = [{'check': name, 'description': text, 'evidence': str(Evidence.NEEDS_LOCAL_RUNTIME_PROOF),
             'passed': None} for name, text in sorted(checks)]
    return {'schema': 'bridge-verification-plan', 'schema_version': 1, 'canonical_id': cid,
            'package_id': package['package_id'], 'registration_plan_hash': registration_plan['plan_hash'],
            'checks': rows}
