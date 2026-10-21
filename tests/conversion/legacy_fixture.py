"""Explicit migration of the prepared cloud-fixture-v1 handoff envelope to bridge-content-source.

fixtures/cloud/README.md requires consumers to migrate explicitly; any other schema is
rejected. Synthetic payloads (geometry/pixels) are hashed, never copied into manifests.
"""

import copy

from bridge.content.canonical import sha256_json

_KIND = {'prefab': 'prefab', 'mesh': 'mesh', 'material': 'material', 'texture': 'texture', 'face': 'face',
         'animation': 'animator', 'icon': 'icon'}


def _ref(rel, target, slot=''):
    return {'relation': rel, 'target': target, 'slot': slot, 'critical': None, 'target_kind': None,
            'candidates': None, 'provenance': 'cloud-fixture-v1 migration'}


def _obj(ref, kind, refs=(), attributes=None, source=None):
    return {'ref': ref, 'kind': kind, 'hash': sha256_json({'ref': ref, 'kind': kind, 'source': source}),
            'attributes': attributes, 'provenance': 'cloud-fixture-v1 migration', 'references': list(refs)}


def apply_scenario(universe, case):
    out = copy.deepcopy(universe)
    if 'schema' in case:
        out['schema'] = case['schema']
    for asset_id in case.get('remove_assets', []):
        out['assets'] = [a for a in out['assets'] if a['id'] != asset_id]
    if 'add_capability' in case:
        for definition in out['definitions']:
            definition['behaviour_capabilities'].append(case['add_capability'])
    if 'add_edge' in case:
        source, target = case['add_edge']
        next(a for a in out['assets'] if a['id'] == source)['dependencies'].append(target)
    if 'source_build' in case:
        realm = next(r for r, b in out['builds'].items() if b.rstrip('0123456789') == case['source_build'].rstrip('0123456789'))
        out['builds'][realm] = case['source_build']
    return out


def _asset_objects(asset, kinds):
    aid, kind = asset['id'], asset['kind']
    refs, extra, attributes = [], [], None
    for dep in asset['dependencies']:
        dep_kind = kinds.get(dep)
        if kind == 'prefab' and dep_kind in ('mesh', 'material'):
            continue  # routed through the synthesized renderer below
        if kind == 'prefab' and dep_kind == 'face':
            refs.append(_ref('has_face', dep))
        elif kind == 'prefab' and dep_kind == 'animation':
            refs.append(_ref('uses_animator', dep))
        elif kind in ('material', 'icon') and dep_kind == 'texture':
            refs.append(_ref('uses_texture', dep, '_BodyTex' if kind == 'material' else ''))
        else:
            refs.append(_ref('references', dep))  # preserved, never dropped
    if kind == 'prefab':
        renderer = f'{aid}#renderer'
        refs.append(_ref('has_component', renderer))
        rrefs = [_ref('uses_mesh', d) if kinds.get(d) == 'mesh' else _ref('uses_material', d, '0')
                 for d in asset['dependencies'] if kinds.get(d) in ('mesh', 'material')]
        extra.append(_obj(renderer, 'renderer', rrefs, source=aid))
    if kind == 'material':
        semantics = asset.get('semantics', {})
        shader = semantics.get('shader_model')
        refs.append(_ref('uses_shader', f'shader:{shader}'))
        extra.append(_obj(f'shader:{shader}', 'shader', source=shader))
        texture = next((d for d in asset['dependencies'] if kinds.get(d) == 'texture'), None)
        attributes = {'shader': shader, 'properties': [
            {'name': '_BodyTex', 'type': 'texture', 'value': texture},
            {'name': '_BaseColor', 'type': 'color', 'value': semantics.get('base_color')},
            {'name': '_Roughness', 'type': 'float', 'value': semantics.get('roughness')}]}
    if kind == 'face':
        textures = [r['target'] for r in refs if r['relation'] == 'references' and kinds.get(r['target']) == 'texture']
        refs = [r for r in refs if r['target'] not in textures]
        for expression in asset.get('expressions', []):
            eid = f'{aid}#expr:{expression}'
            refs.append(_ref('has_expression', eid))
            extra.append(_obj(eid, 'face_expression', [_ref('uses_texture', t, 'face') for t in textures], source=aid))
    if kind == 'animation':
        for clip in asset.get('clips', []):
            cid = f'{aid}#clip:{clip}'
            refs.append(_ref('has_clip', cid))
            extra.append(_obj(cid, 'animation_clip', source=aid))
    # Payload (synthetic geometry/pixels) contributes to the hash only.
    return [_obj(aid, _KIND[kind], refs, attributes, source=asset), *extra]


def migrate(universe):
    """cloud-fixture-v1 -> {'SR1': bridge-content-source, 'SR2': ...}."""
    if not isinstance(universe, dict) or universe.get('schema') != 'cloud-fixture-v1':
        raise ValueError(f"REJECT_OR_EXPLICIT_MIGRATION: no migration from {universe.get('schema')!r}")
    if universe.get('synthetic') is not True:
        raise ValueError('cloud-fixture-v1 must be synthetic')
    kinds = {a['id']: a['kind'] for a in universe['assets']}
    shared = [o for asset in universe['assets'] for o in _asset_objects(asset, kinds)]
    out = {}
    for realm, build in sorted(universe['builds'].items()):
        objects, entries = list(shared), []
        for definition in universe['definitions']:
            if realm not in definition['realms']:
                continue
            slug = definition['canonical_id'].split('.', 1)[1]
            slime_ref, plort_ref = f'Fixture.{slug}', f'Fixture.Plort.{slug}'
            refs = [_ref('has_prefab', definition['prefab']), _ref('produces_plort', plort_ref),
                    _ref('has_icon', definition['icon']), _ref('has_pedia', f'{slug}#pedia'),
                    _ref('favourite', definition['diet']['favourite'])]
            for cap in definition['behaviour_capabilities']:
                refs.append(_ref('has_behaviour', f'{slug}#behaviour:{cap}'))
                objects.append(_obj(f'{slug}#behaviour:{cap}', 'behaviour', attributes={'type': cap}, source=cap))
            definition_obj = _obj(slime_ref, 'slime_definition', refs,
                                  {'food_groups': definition['diet']['food_groups']}, source=definition)
            objects += [definition_obj, _obj(definition['diet']['favourite'], 'food', source='food'),
                        _obj(plort_ref, 'plort_definition', [_ref('has_market', f'{slug}#market')],
                             source=definition['plort']),
                        _obj(f'{slug}#market', 'market', attributes=definition['plort']['market'], source=slug),
                        _obj(f'{slug}#pedia', 'pedia', attributes=definition['pedia'], source=slug)]
            evidence = ['cloud-fixture-v1 synthetic canonical id']
            entries += [{'native_ref': slime_ref, 'numeric_id': None, 'content_kind': 'slime', 'role': 'base',
                         'canonical_id': definition['canonical_id'], 'canonical_evidence': evidence,
                         'display_name': slug, 'definition_hash': definition_obj['hash'], 'produces': [plort_ref],
                         'base_slimes': [], 'quarantine': None},
                        {'native_ref': plort_ref, 'numeric_id': None, 'content_kind': 'plort', 'role': None,
                         'canonical_id': definition['plort']['canonical_id'], 'canonical_evidence': evidence,
                         'display_name': None, 'definition_hash': objects[-3]['hash'], 'produces': [],
                         'base_slimes': [], 'quarantine': None}]
        objects = sorted({o['ref']: o for o in objects}.values(), key=lambda o: o['ref'])
        out[realm] = {'schema': 'bridge-content-source', 'schema_version': 1, 'realm': realm, 'synthetic': True,
                      'data_evidence': 'SYNTHETIC', 'build': {'fingerprint': build, 'label': 'cloud-fixture-v1'},
                      'registry': {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': realm,
                                   'build': {'fingerprint': build, 'label': 'cloud-fixture-v1'},
                                   'data_evidence': 'SYNTHETIC',
                                   'traversal': {'completeness': 'COMPLETE', 'registry_identity': 'cloud-fixture-v1',
                                                 'expected_count': len(entries), 'observed_count': len(entries)},
                                   'entries': entries, 'diagnostics': []},
                      'objects': objects, 'roots': {}}
    return out


def profiles(realm):
    material = {'schema': 'bridge-material-profile', 'schema_version': 1, 'realm': realm, 'synthetic': True,
                'shaders': {'fixture-soft-lit': {
                    'family': 'slime_body', 'render_state': {'alpha_mode': 'OPAQUE', 'cull': 'BACK', 'zwrite': True},
                    'properties': {'_BodyTex': {'semantic': 'albedo.texture', 'required': True},
                                   '_BaseColor': {'semantic': 'albedo.color', 'required': False}}}}}
    behaviour = {'schema': 'bridge-behaviour-profile', 'schema_version': 1, 'realm': realm, 'synthetic': True,
                 'components': {'standard.vacpack': {'capability': 'standard.vacpack'},
                                'standard.diet': {'capability': 'standard.diet'}}}
    return material, behaviour
