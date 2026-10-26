"""Semantic material conversion: native material -> neutral semantics -> target construction plan.

SR1 and SR2 shaders are not assumed compatible. Each realm supplies data profiles: a
*source* profile mapping native shader property names to neutral semantics, and a *target*
profile of material templates (target shader + which semantics it can express). Native names
exist only in those locally verified profiles, never in this code. An unknown shader is
PENDING_ADAPTER; an unknown property is preserved with provenance and a warning; a required
semantic missing from source data quarantines the material; a semantic the target cannot
express either degrades (optional) or yields PENDING_ADAPTER (required/core).
"""

import copy
import math

from bridge.content.canonical import REALMS, diag, sha256_json, sort_diagnostics

SEMANTICS = {
    'albedo.texture': 'texture', 'albedo.color': 'color',
    'gradient.top': 'color', 'gradient.middle': 'color', 'gradient.bottom': 'color', 'tint.color': 'color',
    'normal.texture': 'texture', 'normal.scale': 'scalar', 'mask.texture': 'texture',
    'emission.texture': 'texture', 'emission.color': 'color', 'emission.intensity': 'scalar',
    'alpha.cutoff': 'scalar', 'alpha.opacity': 'scalar',
    'translucency.color': 'color', 'translucency.strength': 'scalar',
    'face.texture': 'texture', 'face.color': 'color', 'detail.texture': 'texture', 'detail.scale': 'scalar',
    'specular.gloss': 'scalar', 'specular.color': 'color', 'rim.color': 'color', 'rim.power': 'scalar',
    'uv.scale_offset': 'vector',
}
CORE_PREFIXES = ('albedo.', 'gradient.')
ALPHA_MODES = ('OPAQUE', 'CUTOUT', 'BLEND')
CULL_MODES = ('BACK', 'FRONT', 'OFF')
_NATIVE_TYPES = {'texture': {'texture'}, 'color': {'color'}, 'float': {'scalar'}, 'int': {'scalar'},
                 'vector': {'vector', 'color'}}
_STATUS_RANK = {'READY': 0, 'DEGRADED': 1, 'PENDING_ADAPTER': 2, 'QUARANTINED': 3}


def worst(*statuses):
    return max(statuses, key=_STATUS_RANK.__getitem__, default='READY')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _value(kind, value):
    """Normalized value or None when invalid for the semantic kind."""
    if kind == 'scalar':
        return float(value) if _number(value) else None
    if kind in ('color', 'vector') and isinstance(value, list) and all(_number(v) for v in value):
        if kind == 'color' and len(value) in (3, 4):
            return [float(v) for v in value] + ([1.0] if len(value) == 3 else [])
        if kind == 'vector' and 2 <= len(value) <= 4:
            return [float(v) for v in value]
    return None


def validate_source_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'bridge-material-profile' or \
            profile.get('schema_version') != 1 or profile.get('realm') not in REALMS or \
            type(profile.get('synthetic')) is not bool or not isinstance(profile.get('shaders'), dict):
        raise ValueError('Expected bridge-material-profile v1')
    for shader, entry in profile['shaders'].items():
        if not isinstance(shader, str) or not shader or not isinstance(entry, dict) or \
                not isinstance(entry.get('family'), str) or not isinstance(entry.get('properties'), dict):
            raise ValueError(f'Invalid shader entry {shader!r}')
        state = entry.get('render_state', {})
        if state.get('alpha_mode', 'OPAQUE') not in ALPHA_MODES or state.get('cull', 'BACK') not in CULL_MODES:
            raise ValueError(f'Invalid render state for {shader}')
        for name, prop in entry['properties'].items():
            if not isinstance(prop, dict) or prop.get('semantic') not in SEMANTICS or \
                    type(prop.get('required')) is not bool or type(prop.get('uv_set', 0)) is not int:
                raise ValueError(f'Invalid property mapping {shader}.{name}')
    return profile


def validate_target_profile(profile):
    if not isinstance(profile, dict) or profile.get('schema') != 'bridge-target-material-profile' or \
            profile.get('schema_version') != 1 or profile.get('realm') not in REALMS or \
            type(profile.get('synthetic')) is not bool or not isinstance(profile.get('templates'), list):
        raise ValueError('Expected bridge-target-material-profile v1')
    ids = set()
    for template in profile['templates']:
        if not isinstance(template, dict) or not isinstance(template.get('id'), str) or template['id'] in ids:
            raise ValueError('Templates need unique ids')
        ids.add(template['id'])
        if not isinstance(template.get('shader'), str) or not isinstance(template.get('families'), list) or \
                not set(template.get('alpha_modes', [])) <= set(ALPHA_MODES) or \
                not isinstance(template.get('supports'), dict) or not isinstance(template.get('uv_sets', [0]), list):
            raise ValueError(f"Invalid template {template['id']}")
        for semantic, slot in template['supports'].items():
            if semantic not in SEMANTICS or not isinstance(slot, dict) or not isinstance(slot.get('property'), str) \
                    or type(slot.get('required')) is not bool:
                raise ValueError(f"Invalid template slot {template['id']}.{semantic}")
            if 'default' in slot and _value(SEMANTICS[semantic], slot['default']) is None:
                raise ValueError(f"Invalid default for {template['id']}.{semantic}")
    return profile


def neutralize(material_id, description, source_profile, resolved_textures):
    """Native description {'shader', 'properties': [{name,type,value}]} -> semantic material."""
    validate_source_profile(source_profile)
    diags, channels, unknown = [], {}, []
    shader = description.get('shader') if isinstance(description, dict) else None
    raw = description.get('properties', []) if isinstance(description, dict) else []
    props = sorted((p for p in raw if isinstance(p, dict) and isinstance(p.get('name'), str)),
                   key=lambda p: p['name'])
    entry = source_profile['shaders'].get(shader) if isinstance(shader, str) else None
    result = {'id': material_id, 'source_shader': shader, 'family': None, 'channels': channels,
              'render_state': None, 'unknown_properties': unknown,
              'profile_entry_hash': sha256_json({'shader': shader, 'entry': entry})}
    if entry is None:
        code = 'MATERIAL_UNDESCRIBED' if shader is None else 'UNKNOWN_SHADER'
        diags.append(diag(code, 'error', material_id, f'no source profile entry for {shader!r}'))
        unknown.extend({'name': p['name'], 'type': p.get('type'), 'provenance': f'{shader}:{p["name"]}'}
                       for p in props)
        return {**result, 'status': 'PENDING_ADAPTER', 'diagnostics': sort_diagnostics(diags)}
    status = 'READY'
    state = entry.get('render_state', {})
    result.update(family=entry['family'], render_state={'alpha_mode': state.get('alpha_mode', 'OPAQUE'),
                                                        'cull': state.get('cull', 'BACK'),
                                                        'zwrite': state.get('zwrite', True)})
    seen = set()
    for prop in props:
        name = prop['name']
        mapping = entry['properties'].get(name)
        if mapping is None:
            unknown.append({'name': name, 'type': prop.get('type'), 'provenance': f'{shader}:{name}'})
            diags.append(diag('UNKNOWN_MATERIAL_PROPERTY', 'warning', material_id, f'{shader}.{name} preserved'))
            status = worst(status, 'DEGRADED')
            continue
        seen.add(name)
        semantic, required = mapping['semantic'], mapping['required']
        kind = SEMANTICS[semantic]
        bad = 'error' if required else 'warning'
        if kind not in _NATIVE_TYPES.get(prop.get('type'), ()):
            diags.append(diag('INVALID_PROPERTY_VALUE', bad, material_id, f'{name}: {prop.get("type")} != {kind}'))
            status = worst(status, 'QUARANTINED' if required else 'DEGRADED')
            continue
        if kind == 'texture':
            texture = prop.get('value')
            if not isinstance(texture, str) or texture not in resolved_textures:
                code = 'REQUIRED_TEXTURE_UNRESOLVED' if required else 'OPTIONAL_TEXTURE_UNRESOLVED'
                diags.append(diag(code, bad, material_id, f'{name} -> {texture!r}'))
                status = worst(status, 'QUARANTINED' if required else 'DEGRADED')
                continue
            channels[semantic] = {'kind': kind, 'texture': texture, 'texture_hash': resolved_textures[texture],
                                  'uv_set': mapping.get('uv_set', 0), 'source_property': name,
                                  'required': required}
        else:
            value = _value(kind, prop.get('value'))
            if value is None:
                diags.append(diag('INVALID_PROPERTY_VALUE', bad, material_id, f'{name} value invalid for {kind}'))
                status = worst(status, 'QUARANTINED' if required else 'DEGRADED')
                continue
            channels[semantic] = {'kind': kind, 'value': value, 'source_property': name, 'required': required}
    for name, mapping in sorted(entry['properties'].items()):
        if mapping['required'] and name not in seen:
            diags.append(diag('MISSING_REQUIRED_SEMANTIC', 'error', material_id, f'{name} -> {mapping["semantic"]}'))
            status = 'QUARANTINED'
    result['channels'] = dict(sorted(channels.items()))
    return {**result, 'status': status, 'diagnostics': sort_diagnostics(diags)}


def _template_fit(semantic, template):
    alpha = semantic['render_state']['alpha_mode']
    compatible = alpha in template.get('alpha_modes', ['OPAQUE']) and \
        ('*' in template['families'] or semantic['family'] in template['families'])
    covered = sum(name in template['supports'] for name in semantic['channels'])
    return compatible, covered


def plan_target_material(semantic, target_profile):
    """Choose a target template and produce property assignments, drops and a status."""
    validate_target_profile(target_profile)
    material = semantic['id']
    base = {'material': material, 'template': None, 'target_shader': None, 'assignments': [], 'dropped': [],
            'render_state': semantic.get('render_state'), 'template_hash': None}
    if semantic['status'] in ('QUARANTINED', 'PENDING_ADAPTER'):
        return {**base, 'status': semantic['status'],
                'diagnostics': [diag('SOURCE_MATERIAL_NOT_CONVERTIBLE', 'error', material, semantic['status'])]}
    ranked = sorted(target_profile['templates'], key=lambda t: (not _template_fit(semantic, t)[0],
                                                                -_template_fit(semantic, t)[1], t['id']))
    if not ranked or not _template_fit(semantic, ranked[0])[0]:
        return {**base, 'status': 'PENDING_ADAPTER', 'diagnostics': [diag(
            'NO_COMPATIBLE_TEMPLATE', 'error', material,
            f"family={semantic['family']} alpha={semantic['render_state']['alpha_mode']}")]}
    template = ranked[0]
    diags, assignments, dropped, status = [], [], [], 'READY'
    uv_sets = template.get('uv_sets', [0])
    carried_core = False
    for name, channel in semantic['channels'].items():
        slot = template['supports'].get(name)
        problem = None
        if slot is None:
            problem = ('SEMANTIC_UNSUPPORTED_BY_TARGET', f'{name} not expressible by {template["id"]}')
        elif channel['kind'] == 'texture' and channel['uv_set'] not in uv_sets:
            problem = ('UV_SET_UNSUPPORTED', f'{name} uses uv{channel["uv_set"]}')
        if problem:
            dropped.append(name)
            diags.append(diag(problem[0], 'error' if channel['required'] else 'warning', material, problem[1]))
            status = worst(status, 'PENDING_ADAPTER' if channel['required'] else 'DEGRADED')
            continue
        carried_core = carried_core or name.startswith(CORE_PREFIXES)
        assignment = {'property': slot['property'], 'semantic': name, 'kind': channel['kind'],
                      'semantic_source': 'source'}
        assignment.update({'texture': channel['texture'], 'texture_hash': channel['texture_hash'],
                           'uv_set': channel['uv_set']} if channel['kind'] == 'texture' else {'value': channel['value']})
        assignments.append(assignment)
    for name, slot in sorted(template['supports'].items()):
        if slot['required'] and name not in semantic['channels']:
            if 'default' in slot:
                assignments.append({'property': slot['property'], 'semantic': name, 'kind': SEMANTICS[name],
                                    'value': _value(SEMANTICS[name], slot['default']),
                                    'semantic_source': 'template-default'})
                diags.append(diag('TARGET_DEFAULT_USED', 'info', material, name))
            else:
                diags.append(diag('TARGET_REQUIRES_MISSING_SEMANTIC', 'error', material, name))
                status = worst(status, 'PENDING_ADAPTER')
    if any(n.startswith(CORE_PREFIXES) for n in semantic['channels']) and not carried_core:
        diags.append(diag('CORE_SEMANTIC_UNSUPPORTED', 'error', material, 'no albedo/gradient channel carried'))
        status = worst(status, 'PENDING_ADAPTER')
    status = worst(status, 'DEGRADED' if semantic['status'] == 'DEGRADED' else 'READY')
    return {**base, 'template': template['id'], 'target_shader': template['shader'],
            'assignments': sorted(assignments, key=lambda a: a['property']), 'dropped': sorted(dropped),
            'template_hash': sha256_json(template), 'status': status, 'diagnostics': sort_diagnostics(diags)}


class AttributeMaterialMapper:
    """SourceMaterialMapper reading material node attributes written by the dependency provider.

    Expected attributes: {'shader': native shader identity, 'properties': [{'name', 'type', 'value'}]}
    where texture values are native refs in the same realm (converted to graph node ids).
    """

    def __init__(self, realm):
        self.realm = realm

    def describe(self, material_node, graph=None):
        attributes = material_node.get('attributes') or {}
        props = []
        for prop in attributes.get('properties', []) if isinstance(attributes.get('properties'), list) else []:
            if isinstance(prop, dict):
                prop = copy.deepcopy(prop)
                if prop.get('type') == 'texture' and isinstance(prop.get('value'), str):
                    prop['value'] = f"{self.realm}:{prop['value']}"
                props.append(prop)
        shader = attributes.get('shader') if isinstance(attributes.get('shader'), str) else None
        return {'shader': shader, 'properties': props}
