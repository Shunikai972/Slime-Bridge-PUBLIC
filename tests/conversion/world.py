"""Synthetic world builder: fictional species -> bridge-content-source manifests and profiles.

Every name, shader, component type and hash here is invented for tests. Nothing describes
real Slime Rancher content. ``python -m conversion.world --write`` (run from ``tests/``)
regenerates fixtures/cloud/pipeline/*.json; a test asserts the committed files match.
"""

import copy
import json
import re
import sys
from pathlib import Path

from bridge.content.canonical import pretty_json, sha256_text

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / 'fixtures' / 'cloud' / 'pipeline'


def _snake(name):
    return re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', name).upper()


def native_names(realm, name):
    if realm == 'SR1':
        return f'{_snake(name)}_SLIME', f'{_snake(name)}_PLORT'
    return f'SlimeDefinition.{name}', f'IdentifiableType.{name}Plort'


def _ref(rel, target, slot='', **extra):
    return {'relation': rel, 'target': target, 'slot': slot, 'critical': None, 'target_kind': None,
            'candidates': None, 'provenance': 'synthetic fixture', **extra}


def _obj(ref, kind, refs=(), attributes=None, salt=''):
    return {'ref': ref, 'kind': kind, 'hash': sha256_text(f'{kind}:{ref}{salt}'), 'attributes': attributes,
            'provenance': 'synthetic fixture', 'references': list(refs)}


def species(realm, name, *, materials=1, skinned=False, bones=0, vfx=False, face=True, clips=1, behaviours=(),
            drop=(), shader='Fixture/SlimeBody', unknown_property=False, cycle=False, salt=None, numeric_id=None,
            quarantine=None, malformed=False, role='base'):
    """Objects + registry entries for one fictional species."""
    salt = salt or {}
    slime_ref, plort_ref = native_names(realm, name)
    base = f'Fixture/{name}'
    o = {}

    def add(key, ref, kind, refs=(), attributes=None):
        o[key] = _obj(ref, kind, refs, attributes, salt.get(key, ''))

    renderer_refs = [_ref('uses_mesh', f'{base}/Mesh')]
    for i in range(materials):
        renderer_refs.append(_ref('uses_material', f'{base}/Material{i}', str(i)))
        props = [{'name': '_BodyTex', 'type': 'texture', 'value': f'{base}/Body{i}'},
                 {'name': '_TopColor', 'type': 'color', 'value': [0.9, 0.4 + 0.1 * i, 0.5, 1.0]},
                 {'name': '_Mask', 'type': 'texture', 'value': f'{base}/Mask'}]
        if unknown_property:
            props.append({'name': '_Iridescence', 'type': 'float', 'value': 0.7})
        add(f'mat{i}', f'{base}/Material{i}', 'material',
            [_ref('uses_shader', f'Fixture/Shader/{shader}'), _ref('uses_texture', f'{base}/Body{i}', '_BodyTex'),
             _ref('uses_texture', f'{base}/Mask', '_Mask')], {'shader': shader, 'properties': props})
        add(f'body{i}', f'{base}/Body{i}', 'texture')
    add('mask', f'{base}/Mask', 'texture')
    prefab_refs = [_ref('has_component', f'{base}/Renderer', target_kind='skinned_renderer' if skinned else 'renderer')]
    if skinned:
        prefab_refs.append(_ref('has_child', f'{base}/Bone0'))
        renderer_refs += [_ref('uses_bone', f'{base}/Bone0', 'root')]
        for b in range(max(bones, 1)):
            renderer_refs.append(_ref('uses_bone', f'{base}/Bone{b}', str(b)))
            nxt = [_ref('has_child', f'{base}/Bone{b + 1}')] if b + 1 < max(bones, 1) else []
            add(f'bone{b}', f'{base}/Bone{b}', 'transform', nxt)
    add('renderer', f'{base}/Renderer', 'skinned_renderer' if skinned else 'renderer', renderer_refs)
    add('mesh', f'{base}/Mesh', 'mesh', [_ref('references', f'{base}/Prefab')] if cycle else [],
        {'submeshes': materials, 'vertex_count': 120 + materials, 'skinned': skinned})
    if face:
        prefab_refs.append(_ref('has_face', f'{base}/Face'))
        add('face', f'{base}/Face', 'face', [_ref('has_expression', f'{base}/Face/Neutral'),
                                             _ref('has_expression', f'{base}/Face/Blink')])
        for expr in ('Neutral', 'Blink'):
            add(f'face_{expr}', f'{base}/Face/{expr}', 'face_expression',
                [_ref('uses_texture', f'{base}/Face/{expr}Tex', 'face')])
            add(f'face_{expr}_tex', f'{base}/Face/{expr}Tex', 'texture')
    if clips:
        prefab_refs.append(_ref('uses_animator', f'{base}/Animator'))
        add('animator', f'{base}/Animator', 'animator',
            [_ref('has_clip', f'{base}/Clip{c}') for c in range(clips)])
        for c in range(clips):
            add(f'clip{c}', f'{base}/Clip{c}', 'animation_clip')
    if vfx:
        prefab_refs.append(_ref('has_vfx', f'{base}/Vfx'))
        add('vfx', f'{base}/Vfx', 'vfx', [_ref('uses_material', f'{base}/VfxMaterial')])
        add('vfx_mat', f'{base}/VfxMaterial', 'material', [_ref('uses_shader', 'Fixture/Shader/Fixture/Vfx'),
                                                          _ref('uses_texture', f'{base}/VfxTex', '_MainTex')],
            {'shader': 'Fixture/Vfx', 'properties': [{'name': '_MainTex', 'type': 'texture',
                                                       'value': f'{base}/VfxTex'}]})
        add('vfx_tex', f'{base}/VfxTex', 'texture')
    for i, native_type in enumerate(behaviours):
        prefab_refs.append(_ref('has_behaviour', f'{base}/Behaviour{i}'))
        add(f'behaviour{i}', f'{base}/Behaviour{i}', 'behaviour', attributes={'type': native_type})
    add('prefab', f'{base}/Prefab', 'prefab', prefab_refs)
    add('icon', f'{base}/Icon', 'icon', [_ref('uses_texture', f'{base}/IconTex')])
    add('icon_tex', f'{base}/IconTex', 'texture')
    add('pedia', f'{base}/Pedia', 'pedia', [_ref('has_localization', f'{base}/Loc')],
        {'title_key': f'fixture.{name.lower()}.title', 'intro_key': f'fixture.{name.lower()}.intro'})
    add('loc', f'{base}/Loc', 'localization', attributes={'table': 'fixture', 'keys': 2})
    add('plort', plort_ref, 'plort_definition', [_ref('has_icon', f'{base}/PlortIcon'),
                                                 _ref('has_market', f'{base}/Market')])
    add('plort_icon', f'{base}/PlortIcon', 'icon')
    add('market', f'{base}/Market', 'market', attributes={'base_value': 12, 'saturation': 4})
    add('def', slime_ref, 'slime_definition',
        [_ref('has_prefab', f'{base}/Prefab'), _ref('produces_plort', plort_ref), _ref('has_icon', f'{base}/Icon'),
         _ref('has_pedia', f'{base}/Pedia'), _ref('favourite', 'Fixture/Food/Berry')],
        {'food_groups': ['fixture_fruit']})
    for key in drop:
        o.pop(key)
    slime_entry = {'native_ref': slime_ref, 'numeric_id': numeric_id, 'content_kind': 'slime', 'role': role,
                   'canonical_id': None, 'canonical_evidence': [], 'display_name': name,
                   'definition_hash': o['def']['hash'] if 'def' in o else sha256_text(slime_ref),
                   'produces': [plort_ref], 'base_slimes': [], 'quarantine': quarantine}
    if malformed:
        slime_entry['definition_hash'] = 'not-a-hash'
    plort_entry = {'native_ref': plort_ref, 'numeric_id': None, 'content_kind': 'plort', 'role': None,
                   'canonical_id': None, 'canonical_evidence': [], 'display_name': f'{name} plort',
                   'definition_hash': o['plort']['hash'] if 'plort' in o else sha256_text(plort_ref),
                   'produces': [], 'base_slimes': [], 'quarantine': None}
    return list(o.values()), [slime_entry, plort_entry]


def shared_objects(realm):
    return [_obj('Fixture/Food/Berry', 'food'), _obj('Fixture/Shader/Fixture/SlimeBody', 'shader'),
            _obj('Fixture/Shader/Fixture/Vfx', 'shader'), _obj('Fixture/Shader/Fixture/Mystery', 'shader')]


def manifest(realm, specs, *, build=None, completeness='COMPLETE'):
    objects, entries = shared_objects(realm), []
    for spec in specs:
        spec = dict(spec)
        objs, rows = species(realm, spec.pop('name'), **spec)
        objects += objs
        entries += rows
    objects = sorted({o['ref']: o for o in objects}.values(), key=lambda o: o['ref'])
    fingerprint = build or f'synthetic-{realm.lower()}-build-1'
    return {'schema': 'bridge-content-source', 'schema_version': 1, 'realm': realm, 'synthetic': True,
            'data_evidence': 'SYNTHETIC', 'build': {'fingerprint': fingerprint, 'label': 'synthetic fixture'},
            'registry': {'schema': 'bridge-registry-snapshot', 'schema_version': 1, 'realm': realm,
                         'build': {'fingerprint': fingerprint, 'label': 'synthetic fixture'},
                         'data_evidence': 'SYNTHETIC',
                         'traversal': {'completeness': completeness, 'registry_identity': f'{realm}:synthetic',
                                       'expected_count': len(entries), 'observed_count': len(entries)},
                         'entries': entries, 'diagnostics': []},
            'objects': objects, 'roots': {}}


def source_material_profile(realm):
    return {'schema': 'bridge-material-profile', 'schema_version': 1, 'realm': realm, 'synthetic': True,
            'shaders': {
                'Fixture/SlimeBody': {'family': 'slime_body',
                                      'render_state': {'alpha_mode': 'BLEND', 'cull': 'BACK', 'zwrite': False},
                                      'properties': {'_BodyTex': {'semantic': 'albedo.texture', 'required': True},
                                                     '_TopColor': {'semantic': 'gradient.top', 'required': False},
                                                     '_Mask': {'semantic': 'mask.texture', 'required': False}}},
                'Fixture/Vfx': {'family': 'vfx', 'render_state': {'alpha_mode': 'BLEND', 'cull': 'OFF', 'zwrite': False},
                                'properties': {'_MainTex': {'semantic': 'albedo.texture', 'required': False}}}}}


def source_behaviour_profile(realm):
    return {'schema': 'bridge-behaviour-profile', 'schema_version': 1, 'realm': realm, 'synthetic': True,
            'components': {'Fixture.Wings': {'capability': 'movement.flight'},
                           'Fixture.Glow': {'capability': None},
                           'Fixture.Embers': {'capability': 'diet.fire_feeding'},
                           'Fixture.Sparkle': {'capability': 'hazard.aura'}}}


def target_profile(realm):
    """Synthetic target: fictional shader templates and fictional registration api names."""
    from bridge.conversion.targets import REALM_FACTS, REGISTRATION_OPS
    caps = {'standard.vacpack': {'mode': 'NATIVE'}, 'standard.diet': {'mode': 'NATIVE'},
            'standard.plort_production': {'mode': 'NATIVE'},
            'movement.flight': {'mode': 'BRIDGE_ADAPTER', 'adapter': 'bridge.adapter.flight', 'implemented': False},
            'hazard.aura': {'mode': 'NATIVE'}}
    if realm == 'SR2':
        caps['diet.fire_feeding'] = {'mode': 'UNSUPPORTED'}
    return {'schema': 'bridge-target-profile', 'schema_version': 1, 'realm': realm, 'synthetic': True,
            'loader': REALM_FACTS[realm]['loader'], 'runtime': REALM_FACTS[realm]['runtime'],
            'unity': REALM_FACTS[realm]['unity'], 'facts_evidence': 'synthetic copy of lab facts',
            'build_fingerprint': f'synthetic-{realm.lower()}-target-build-1',
            'material_profile': {'schema': 'bridge-target-material-profile', 'schema_version': 1, 'realm': realm,
                                 'synthetic': True, 'templates': [
                                     {'id': 'fixture_slime', 'shader': f'Fixture/{realm}/Slime',
                                      'families': ['slime_body'], 'alpha_modes': ['BLEND', 'OPAQUE'], 'uv_sets': [0],
                                      'supports': {'albedo.texture': {'property': '_FixtureAlbedo', 'required': True},
                                                   'gradient.top': {'property': '_FixtureTop', 'required': False},
                                                   'mask.texture': {'property': '_FixtureMask', 'required': False}}},
                                     {'id': 'fixture_vfx', 'shader': f'Fixture/{realm}/Particle', 'families': ['vfx'],
                                      'alpha_modes': ['BLEND'], 'uv_sets': [0],
                                      'supports': {'albedo.texture': {'property': '_FixtureMain', 'required': False}}}]},
            'behaviour_profile': {'schema': 'bridge-target-behaviour-profile', 'schema_version': 1, 'realm': realm,
                                  'synthetic': True, 'capabilities': caps},
            'registration': {op: {'api': f'synthetic.fixture.{realm}.{op}', 'evidence': 'SYNTHETIC',
                                  'local_query': None} for op in REGISTRATION_OPS}}


# The acceptance universe: every scenario the mission lists, fictional names only.
SR1_SPECS = [
    {'name': 'Pink'},                                                     # shared with SR2
    {'name': 'Amber'},                                                    # SR1-only, simplest
    {'name': 'Quartz', 'materials': 2, 'skinned': True, 'bones': 3},      # SR1-only skinned multi-material
    {'name': 'Cinder', 'behaviours': ['Fixture.Embers']},                # SR1-only REQUIRED special capability
    {'name': 'Loop', 'cycle': True},                                      # SR1-only with a dependency cycle
    {'name': 'Broken', 'malformed': True},                                # malformed registry entry
    {'name': 'Cursed', 'quarantine': 'synthetic quarantine: source flagged unsafe'},
]
SR2_SPECS = [
    {'name': 'Pink'},                                                     # shared with SR1
    {'name': 'Azure', 'skinned': True, 'bones': 2},                      # SR2-only skinned
    {'name': 'Prism', 'materials': 3},                                    # multi-material
    {'name': 'Ember', 'vfx': True, 'behaviours': ['Fixture.Sparkle', 'Fixture.Glow']},  # optional VFX + aura
    {'name': 'Gale', 'behaviours': ['Fixture.Wings']},                   # degradable capability, no adapter
    {'name': 'Moss', 'drop': ['mask']},                                   # missing optional texture
    {'name': 'Hollow', 'drop': ['mesh']},                                 # missing critical mesh
    {'name': 'Flicker', 'behaviours': ['Fixture.Blink']},                # unknown behaviour
    {'name': 'Opal', 'unknown_property': True},                          # unknown material property
    {'name': 'Murk', 'shader': 'Fixture/Mystery'},                       # unknown shader
    {'name': 'Twin'}, {'name': 'Twin_'},                                  # canonical collision
]


def universe():
    return {'SR1': manifest('SR1', SR1_SPECS), 'SR2': manifest('SR2', SR2_SPECS)}


def fixture_files():
    worlds = universe()
    files = {f'source-{r.lower()}.json': worlds[r] for r in worlds}
    for realm in ('SR1', 'SR2'):
        files[f'material-profile-{realm.lower()}.json'] = source_material_profile(realm)
        files[f'behaviour-profile-{realm.lower()}.json'] = source_behaviour_profile(realm)
        files[f'target-profile-{realm.lower()}.json'] = target_profile(realm)
    return files


def mutate(world, realm, ref, **changes):
    """Copy of a world with one object's fields changed (used by cache/rebuild tests)."""
    out = copy.deepcopy(world)
    for obj in out[realm]['objects']:
        if obj['ref'] == ref:
            obj.update(changes)
            return out
    raise KeyError(ref)


if __name__ == '__main__' and '--write' in sys.argv:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, data in fixture_files().items():
        (FIXTURE_DIR / filename).write_text(pretty_json(data), encoding='utf-8')
    print(json.dumps(sorted(fixture_files())))
