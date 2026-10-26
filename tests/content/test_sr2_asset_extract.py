"""Offline SR2 asset-extraction tests: deterministic pure-python surface (no UnityPy, no game).

Verifies the naming/derivation helpers, the extraction error contract (untrusted paths refused),
and that a synthetic cache tree validates exactly the way the runtime loader expects it
(manifest schema + per-file hashes), so a corrupted or mismatched cache fails closed.
"""

import json
import hashlib
from pathlib import Path
import sys
import struct
import zlib
from copy import deepcopy
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.content import sr2_asset_extract as extract


def test_validate_canonical_rejects_traversal():
    for bad in ('../evil', 'a/b', 'a\\b', '.hidden', '', 'a..b'):
        try:
            extract.validate_canonical(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'canonical {bad!r} was not refused')
    assert extract.validate_canonical('slime.ringtail') == 'slime.ringtail'
    assert extract.validate_canonical('slime.glitch_tarr') == 'slime.glitch_tarr'


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_canonical_derivation():
    assert extract.canonical_from_reference('SlimeDefinition.Ringtail') == 'slime.ringtail'
    assert extract.canonical_from_reference('SlimeDefinition.GlitchTarr') == 'slime.glitch_tarr'
    assert extract.species_from_canonical('slime.ringtail') == 'Ringtail'
    assert extract.species_from_canonical('slime.glitch_tarr') == 'GlitchTarr'
    assert extract.snake('GlitchTarr') == 'glitch_tarr'


def test_extract_refuses_untrusted_paths(tmp_path):
    # A fake SR2 dir outside the repository Lab tree must be refused by default.
    fake = tmp_path / 'SlimeRancher2_Data'
    fake.mkdir()
    (fake / 'resources.assets').write_bytes(b'')
    for kwargs in ({}, {'out_dir': Path(__file__).resolve().parents[1] / 'Lab' / 'AssetCache' / 'x'}):
        try:
            extract.extract_species('Ringtail', 'slime.ringtail', fake, kwargs.get('out_dir'))
        except ValueError as ex:
            assert 'outside' in str(ex) or 'not found' in str(ex)
        else:
            raise AssertionError('untrusted path was not refused')


def test_extract_refuses_missing_data_dir(tmp_path):
    try:
        extract.extract_species('Ringtail', None, tmp_path / 'nope', None)
    except ValueError as ex:
        assert 'not found' in str(ex)
    else:
        raise AssertionError('missing data dir was not refused')


def test_complete_source_rejects_raw_traversal_before_source_read(tmp_path, monkeypatch):
    data = tmp_path / 'source'; data.mkdir()
    monkeypatch.setattr(extract, 'Sr2Assets', lambda *args: (_ for _ in ()).throw(AssertionError('source read before output validation')))
    with pytest.raises(ValueError):
        extract.extract_species('Example', 'slime.example', data,
            extract.REPO_ROOT / 'Lab/RuntimeArtifacts/unused/../escape', allow_nonlab=True,
            complete_source=True, clip_ids=[8], default_clip='Authored idle')


def test_complete_source_cache_hashes_actual_icon_descriptor_and_animation(synthetic_extraction, monkeypatch):
    s = synthetic_extraction
    from bridge.content import sr2_source_cache, sr2_icon
    # Separate graph tests exercise full source references/decoder; this exercises
    # integration/publication without requiring UnityPy or proprietary objects.
    monkeypatch.setattr(sr2_source_cache, 'resolve_species_source', lambda *args: {'sprite_id': 9})
    def declared(assets, resolved, parts):
        return [dict(p, source_material_slots=[('slimeExampleBase', s.objects[105].read())],
                     source_material_ids=[105]) for p in parts]
    monkeypatch.setattr(sr2_source_cache, 'collect_declared_parts', declared)
    monkeypatch.setattr(extract, 'part_material_slots', lambda *args: (_ for _ in ()).throw(AssertionError('material name guess used')))
    animation = {'default_clip': 'Authored idle', 'clips': [{'name': 'Authored idle'}]}
    monkeypatch.setattr(sr2_source_cache, 'extract_source_animation', lambda *args: (animation, {'selection': 'explicit-source-clip-ids'}))
    descriptor = {'file': 'iconSlimeExample.source.png', 'schema': 'bridge-source-sprite',
                  'mandatory': True, 'source_sprite_path_id': 9}
    def export_icon(assets, resolved, output):
        (output / descriptor['file']).write_bytes(_png((4, 5, 6, 255)))
        return descriptor
    monkeypatch.setattr(sr2_icon, 'export_source_icon', export_icon, raising=False)
    monkeypatch.setattr(sr2_icon, '_fresh_private_output', lambda path: (path.mkdir(), path)[1])
    extract.extract_species('Example', 'slime.example', s.data_dir, s.output, allow_nonlab=True,
                            complete_source=True, clip_ids=[8], default_clip='Authored idle')
    model = json.loads((s.output / 'model.json').read_text())
    manifest = json.loads((s.output / 'cache-manifest.json').read_text())
    receipt = json.loads((s.output / 'dump-receipt.json').read_text())
    assert model['source_animation'] == animation
    assert 'utc' not in model  # Repeating the same source inputs must yield the same model hash.
    assert manifest['icon'] == descriptor
    assert json.loads((s.output / 'icon-descriptor.json').read_text()) == descriptor
    assert receipt['model_sha256'] == _sha(s.output / 'model.json')
    assert manifest['converter_version'] == 5
    assert set(manifest['files']) == {p.name for p in s.output.iterdir()} - {'cache-manifest.json'}
    assert all(_sha(s.output / name) == digest for name, digest in manifest['files'].items())
    assert manifest['files']['icon-descriptor.json'] == _sha(s.output / 'icon-descriptor.json')
    assert manifest['files'][descriptor['file']] == _sha(s.output / descriptor['file'])
    monkeypatch.setattr(extract.time, 'strftime', lambda *args: 'different-publication-time')
    second = s.output.parent / 'second-complete-cache'
    extract.extract_species('Example', 'slime.example', s.data_dir, second, allow_nonlab=True,
                            complete_source=True, clip_ids=[8], default_clip='Authored idle')
    repeated = json.loads((second / 'cache-manifest.json').read_text())
    assert repeated['published_utc'] != manifest['published_utc']
    assert repeated['files'] == manifest['files']
    assert repeated['icon'] == manifest['icon']


def test_cache_tree_validates_and_hashes_bind(tmp_path):
    """A cache tree shaped like the extractor's output validates; corruption is detected."""
    cache = tmp_path / 'slime.ringtail'
    cache.mkdir()
    model = {'schema': extract.SCHEMA, 'schema_version': extract.SCHEMA_VERSION,
             'canonical_probe': True}
    (cache / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    receipt = {'schema': 'bridge-native-mesh-receipt', 'schema_version': 2, 'target': 'Ringtail'}
    (cache / 'dump-receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    manifest = {
        'schema': extract.CACHE_SCHEMA,
        'schema_version': extract.CACHE_SCHEMA_VERSION,
        'canonical_id': 'slime.ringtail',
        'converter_version': extract.CONVERTER_VERSION,
        'files': {name: _sha(cache / name) for name in ('model.json', 'dump-receipt.json')},
    }
    (cache / 'cache-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    # The manifest hashes must match the on-disk bytes exactly (this is what the C# loader checks).
    for name, digest in manifest['files'].items():
        assert _sha(cache / name) == digest

    # Corruption is detectable.
    (cache / 'model.json').write_text('{"tampered": true}', encoding='utf-8')
    assert _sha(cache / 'model.json') != manifest['files']['model.json']


def _png(pixel):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack(
            '>I', zlib.crc32(kind + payload) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(pixel))) + chunk(b'IEND', b''))


@pytest.fixture
def synthetic_extraction(tmp_path, monkeypatch):
    """Exercise real exporter loop with synthetic mesh/material/texture objects."""
    objects, named = {}, {}

    def pointer(pid):
        return {'m_FileID': 0, 'm_PathID': pid}

    def add(pid, kind, name, tree):
        data = SimpleNamespace(m_Name=name)
        obj = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
                              read=lambda: data, read_typetree=lambda: tree)
        objects[pid] = obj
        named.setdefault(kind, []).append((obj, data))
        return obj, data

    texture_pixels = {}
    for pid, name, pixel in [(301, 'face_default_happy', (33, 77, 123, 255)),
                             (302, 'face_tabby_happy', (222, 111, 44, 17))]:
        obj, data = add(pid, 'Texture2D', name, {
            'm_MipCount': 1, 'm_ColorSpace': 1, 'm_TextureSettings': {
                'm_FilterMode': 1, 'm_Aniso': 1, 'm_MipBias': 0.0,
                'm_WrapU': 0, 'm_WrapV': 0, 'm_WrapW': 0}})
        payload = _png(pixel)
        data.image = SimpleNamespace(save=lambda path, payload=payload: Path(path).write_bytes(payload))
        texture_pixels[pid] = payload
    material_trees = {}
    shaders = {'base-null': 'SR/Null Render', 'eyes': 'SR/AMP/Slime/Face/Eyes Default',
               'mouth': 'SR/AMP/Slime/Face/Mouth Default'}
    for pid, role, name, tex in [(101, 'base-null', 'Null Render', 0),
                                (102, 'eyes', 'eyeHappy', 301),
                                (103, 'mouth', 'mouthTabby', 302)]:
        shader_pid = pid + 100
        add(shader_pid, 'Shader', shaders[role], {'m_ParsedForm': {'m_Name': shaders[role]}})
        textures = [] if not tex else [('_FaceAtlas', {'m_Texture': pointer(tex),
            'm_Scale': dict(x=0.625, y=0.75), 'm_Offset': dict(x=0.1875, y=0.3125)})]
        tree = {'m_Name': name, 'm_Shader': pointer(shader_pid), 'm_CustomRenderQueue': 2473,
            'm_ValidKeywords': ['TEST_KEYWORD'], 'm_InvalidKeywords': ['LEGACY_KEYWORD'],
            'm_SavedProperties': {'m_Colors': [('_Tint', dict(r=0.123456789, g=2.75, b=-0.25, a=0.987654321))],
                'm_Floats': [('_Expression', 7.125), ('_LegacyHDRP', 0.123456789)], 'm_TexEnvs': textures}}
        obj, data = add(pid, 'Material', name, tree)
        material_trees[pid] = tree
        # Real legacy helper boundary: the old face path derives slots by names.
        data.m_Shader = SimpleNamespace(deref=lambda shader_pid=shader_pid: objects[shader_pid])
        data.m_SavedProperties = SimpleNamespace(m_Colors=[], m_TexEnvs=[
            (prop, SimpleNamespace(m_Texture=SimpleNamespace(path_id=env['m_Texture']['m_PathID'],
                deref=lambda tex=tex: objects[tex]))) for prop, env in textures])
    _, decoy = add(104, 'Material', 'mouthHappy', material_trees[103])
    decoy.m_SavedProperties = objects[103].read().m_SavedProperties
    body_obj, body_data = add(105, 'Material', 'slimeExampleBase', {})
    body_data.m_SavedProperties = SimpleNamespace(m_Colors=[], m_TexEnvs=[])
    body_data.m_Shader = SimpleNamespace(deref=lambda: objects[201])
    assets = SimpleNamespace(env=SimpleNamespace(objects=list(objects.values())),
        _objects=list(objects.values()),
        by_name=lambda kind, name: next(((o, d) for o, d in named.get(kind, []) if d.m_Name == name), (None, None)),
        all_named=lambda kind: named.get(kind, []))
    monkeypatch.setattr(extract, 'Sr2Assets', lambda path: assets)
    geometry = {'vertices': [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 'triangles': [[0, 1, 2]],
        'normals': [[0, 0, 1]] * 3, 'uv': [[0, 0], [1, 0], [0, 1]],
        'bind_poses': [], 'bone_weights': [], 'tangents': [[1, 0, 0, 1]] * 3,
        'uv2': [], 'colors': []}
    parts = [{'mesh_name': 'body_LOD0', 'role': 'body', 'mesh': object()},
             {'mesh_name': 'synthetic_face_LOD0', 'role': 'face', 'mesh': object()}]
    monkeypatch.setattr(extract, 'collect_species_meshes', lambda *args: parts)
    monkeypatch.setattr(extract, 'collect_bone_tree', lambda *args: [])
    monkeypatch.setattr(extract, 'decode_mesh', lambda *args: deepcopy(geometry))
    binding = {'visual_transform': {'local_position': [0, 0, 0], 'local_rotation': [0, 0, 0, 1],
                                  'local_scale': [1, 1, 1]}, 'parts': {
        'body_LOD0': {}, 'synthetic_face_LOD0': {'source_face': True, 'source_face_expression': 7,
        'source_face_materials': [{'face_role': role, 'source_path_id': pid}
            for pid, role in [(101, 'base-null'), (102, 'eyes'), (103, 'mouth')]]}}}
    from bridge.content import sr2_appearance
    monkeypatch.setattr(sr2_appearance, 'collect_source_appearance',
                        lambda *args, **kwargs: binding)
    baked_calls = []
    monkeypatch.setattr(extract, 'bake_display_albedo',
        lambda *args, **kwargs: baked_calls.append(args[2]))
    data_dir = tmp_path / 'SlimeRancher2_Data'
    data_dir.mkdir()
    (data_dir / 'resources.assets').write_bytes(b'nonproprietary synthetic fixture')
    return SimpleNamespace(assets=assets, objects=objects, material_trees=material_trees,
        binding=binding, data_dir=data_dir, output=tmp_path / 'output', geometry=geometry,
        texture_pixels=texture_pixels, baked_calls=baked_calls)


def _extract_synthetic(s):
    extract.extract_species('Example', 'slime.example', s.data_dir, s.output, allow_nonlab=True)
    return json.loads((s.output / 'model.json').read_text())


def test_source_face_references_are_local_to_frozen_source_file_not_loaded_dependencies(synthetic_extraction):
    s = synthetic_extraction
    # UnityPy lazily loads script dependencies whose local path IDs can overlap.
    # Local material PPtrs must stay bound to the resources.assets snapshot.
    s.assets.env.objects.append(SimpleNamespace(path_id=102, type=SimpleNamespace(name='Material'),
                                               read_typetree=lambda: {}))
    model = _extract_synthetic(s)
    face = next(r for r in model['skinned_renderers'] if r['role'] == 'face')
    assert [m['source_path_id'] for m in face['materials']] == [101, 102, 103]


def test_export_preserves_source_body_lighting_without_rounding(synthetic_extraction):
    s = synthetic_extraction
    shader_name = 'SR/AMP/Slime/Body'
    shader = SimpleNamespace(
        read=lambda: SimpleNamespace(m_Name=shader_name),
        read_typetree=lambda: {'m_ParsedForm': {'m_Name': shader_name}})
    body = s.objects[105].read()
    body.m_Shader = SimpleNamespace(deref=lambda: shader)
    lighting = [('_LightingUVContribution', 0), ('_BodyLightingContribution', 1),
                ('_LightingUVHorizontalAdjust', 0.123456789)]
    body.m_SavedProperties.m_Floats = lighting
    model = _extract_synthetic(s)
    row = next(r for r in model['skinned_renderers'] if r['role'] == 'body')['materials'][0]
    assert row['shader'] == shader_name
    assert row['floats'] == dict(lighting)
    assert model['skinned_renderers'][1]['source_face'] is True


@pytest.mark.parametrize('bad', [True, '0', float('nan'), float('inf'), float('-inf'), -0.01, 1.01])
def test_body_lighting_export_refuses_malformed_declared_control(bad):
    shader = SimpleNamespace(read_typetree=lambda: {'m_ParsedForm': {'m_Name': 'SR/AMP/Slime/Body'}})
    material = SimpleNamespace(m_Shader=SimpleNamespace(deref=lambda: shader),
        m_SavedProperties=SimpleNamespace(m_Floats=[('_LightingUVContribution', bad),
            ('_BodyLightingContribution', 1), ('_LightingUVHorizontalAdjust', 0)]))
    with pytest.raises(ValueError, match='body lighting property'):
        extract.material_body_properties(material)


def test_body_lighting_export_refuses_incomplete_or_duplicate_controls():
    shader = SimpleNamespace(read_typetree=lambda: {'m_ParsedForm': {'m_Name': 'SR/AMP/Slime/Body'}})
    floats = [('_LightingUVContribution', 0), ('_BodyLightingContribution', 1), ('_LightingUVHorizontalAdjust', 0)]
    for entries in (floats[:2], floats + [floats[0]]):
        material = SimpleNamespace(m_Shader=SimpleNamespace(deref=lambda: shader),
                                  m_SavedProperties=SimpleNamespace(m_Floats=entries))
        with pytest.raises(ValueError, match='body lighting'):
            extract.material_body_properties(material)


def test_body_lighting_export_version_identifies_new_cache_contract():
    assert extract.CONVERTER_VERSION == 5


def test_export_preserves_authored_face_pass_count_and_native_properties(synthetic_extraction):
    s = synthetic_extraction
    model = _extract_synthetic(s)
    face = next(r for r in model['skinned_renderers'] if r['role'] == 'face')
    assert len(face['triangles']) == 1
    assert len(face['materials']) == 3  # Unity repeats the last submesh for extra passes.
    assert face['source_face'] is True and face['source_face_expression'] == 7
    assert [m['face_role'] for m in face['materials']] == ['base-null', 'eyes', 'mouth']
    assert [m['name'] for m in face['materials']] == ['Null Render', 'eyeHappy', 'mouthTabby']
    for m, pid in zip(face['materials'], [101, 102, 103]):
        tree = s.material_trees[pid]
        assert m['source_path_id'] == pid
        assert m['shader'] == s.objects[pid + 100].read_typetree()['m_ParsedForm']['m_Name']
        assert m['colors'] == {'_Tint': [0.123456789, 2.75, -0.25, 0.987654321]}
        assert m['floats'] == {'_Expression': 7.125, '_LegacyHDRP': 0.123456789}
        assert m['render_queue'] == 2473
        assert m['keywords'] == ['TEST_KEYWORD', 'LEGACY_KEYWORD']
        for texture in m['textures']:
            assert texture['property'] == '_FaceAtlas'
            assert texture['scale'] == [0.625, 0.75] and texture['offset'] == [0.1875, 0.3125]
            source = 301 if pid == 102 else 302
            assert texture['source_name'] == s.objects[source].read().m_Name
            assert texture['source_texture_settings'] == s.objects[source].read_typetree()['m_TextureSettings']
            assert texture['source_color_space'] == 1 and texture['mip_count'] == 1
            assert (s.output / texture['file']).read_bytes() == s.texture_pixels[source]
    assert s.baked_calls == ['slimeExampleBase']  # Never synthesize _MainTex/_FaceAtlasBaked for a source face.
    for channel, value in s.geometry.items():
        assert face[channel] == value
    assert extract.CONVERTER_VERSION == 5
    assert 'source_face_materials' not in face


def _face_binding(s):
    return s.binding['parts']['synthetic_face_LOD0']


@pytest.mark.parametrize('change', [
    lambda s: _face_binding(s).update(source_face=False),
    lambda s: _face_binding(s).update(source_face_expression=7.0),
    lambda s: _face_binding(s).update(source_face_materials=[]),
    lambda s: _face_binding(s)['source_face_materials'].__setitem__(0, None),
    lambda s: _face_binding(s)['source_face_materials'][1].update(face_role='eyes-glow'),
    lambda s: _face_binding(s)['source_face_materials'][1].update(face_role=[]),
    lambda s: _face_binding(s)['source_face_materials'].reverse(),
    lambda s: _face_binding(s)['source_face_materials'][1].update(source_path_id=False),
    lambda s: _face_binding(s)['source_face_materials'][1].update(source_path_id=999),
    lambda s: s.material_trees[102].update(m_Shader={'m_FileID': 1, 'm_PathID': 202}),
    lambda s: s.material_trees[102].update(m_Shader={'m_FileID': 0, 'm_PathID': 0}),
    lambda s: s.objects[202].read_typetree()['m_ParsedForm'].update(m_Name='SR/AMP/Slime/Face/Eyes Unknown'),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_TexEnvs'][0][1].update(
        m_Texture={'m_FileID': 0, 'm_PathID': 999}),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_TexEnvs'][0][1].update(
        m_Texture={'m_FileID': 1, 'm_PathID': 301}),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_TexEnvs'][0][1].update(
        m_Texture={'m_FileID': 0, 'm_PathID': 0}),
    lambda s: s.material_trees[102]['m_SavedProperties'].update(m_TexEnvs=[]),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_TexEnvs'].append(
        ('_UnknownRequired', {'m_Texture': {'m_FileID': 0, 'm_PathID': 0}})),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_Floats'].append(('_Nonfinite', float('nan'))),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_Colors'][0][1].update(r=True),
    lambda s: s.material_trees[102].update(m_ValidKeywords='TEST', m_InvalidKeywords='INVALID'),
    lambda s: s.material_trees[102].update(m_CustomRenderQueue=True),
    lambda s: s.material_trees[102].update(m_Name=None),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_Floats'].append(('_Expression', 99)),
    lambda s: s.assets._objects.append(s.objects[102]),
    lambda s: s.objects[301].read_typetree()['m_TextureSettings'].update(m_WrapU=1),
    lambda s: s.objects[301].read_typetree().update(m_MipCount=2),
])
def test_source_face_export_refuses_unsupported_or_unresolved_metadata(synthetic_extraction, change):
    s = synthetic_extraction
    change(s)
    with pytest.raises(ValueError):
        _extract_synthetic(s)
    assert not (s.output / 'cache-manifest.json').exists()


@pytest.mark.parametrize('change', [
    lambda s: _face_binding(s)['source_face_materials'].insert(
        0, deepcopy(_face_binding(s)['source_face_materials'][0])),
    lambda s: _face_binding(s)['source_face_materials'].pop(1),
    lambda s: _face_binding(s).update(
        source_face_materials=[_face_binding(s)['source_face_materials'][0]]),
    lambda s: s.material_trees[102].update(m_CustomRenderQueue=-2),
    lambda s: s.material_trees[102].update(m_CustomRenderQueue=5001),
    lambda s: s.material_trees[102]['m_SavedProperties']['m_TexEnvs'].append(
        ('_MainTex', deepcopy(s.material_trees[102]['m_SavedProperties']['m_TexEnvs'][0][1]))),
    lambda s: s.material_trees[101]['m_SavedProperties']['m_TexEnvs'].append(
        deepcopy(s.material_trees[102]['m_SavedProperties']['m_TexEnvs'][0])),
], ids=['duplicate-base-null', 'missing-eyes', 'base-null-only', 'queue-below',
        'queue-above', 'extra-maintex', 'textured-null-pass'])
def test_source_face_export_refuses_unsupported_consumer_shapes(synthetic_extraction, change):
    s = synthetic_extraction
    change(s)
    with pytest.raises(ValueError):
        _extract_synthetic(s)
    assert not (s.output / 'cache-manifest.json').exists()


@pytest.mark.parametrize('queue', [-1, 0, 5000])
def test_source_face_export_preserves_supported_queue_boundaries(synthetic_extraction, queue):
    s = synthetic_extraction
    s.material_trees[102]['m_CustomRenderQueue'] = queue
    model = _extract_synthetic(s)
    face = next(r for r in model['skinned_renderers'] if r['role'] == 'face')
    assert face['materials'][1]['render_queue'] == queue


def test_only_null_builtin_lightmap_metadata_is_omitted(synthetic_extraction):
    s = synthetic_extraction
    envs = s.material_trees[102]['m_SavedProperties']['m_TexEnvs']
    for property_name in ('unity_Lightmaps', 'unity_LightmapsInd', 'unity_ShadowMasks'):
        envs.append((property_name, {'m_Texture': {'m_FileID': 0, 'm_PathID': 0}}))
    model = _extract_synthetic(s)
    eyes = next(r for r in model['skinned_renderers'] if r['role'] == 'face')['materials'][1]
    assert [t['property'] for t in eyes['textures']] == ['_FaceAtlas']


def test_exact_texture_reference_wins_when_names_collide(synthetic_extraction):
    s = synthetic_extraction
    # Both atlas objects deliberately have the same name and different pixels.
    s.objects[302].read().m_Name = s.objects[301].read().m_Name
    model = _extract_synthetic(s)
    face = next(r for r in model['skinned_renderers'] if r['role'] == 'face')
    eye_file = face['materials'][1]['textures'][0]['file']
    mouth_file = face['materials'][2]['textures'][0]['file']
    assert eye_file != mouth_file
    assert (s.output / eye_file).read_bytes() == s.texture_pixels[301]
    assert (s.output / mouth_file).read_bytes() == s.texture_pixels[302]
