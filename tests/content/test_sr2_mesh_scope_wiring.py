"""Opt-in appearance mesh-scope extraction wiring tests (no UnityPy, no game, no writes outside tmp).

Verifies the mesh-only extraction mode end to end on a synthetic appearance mesh-scope result:
it publishes a bridge-native-mesh v2 cache (skinned body/face + a static cloud attached under the
authored skeleton bone path), never invokes the strict complete-source path, enforces the fresh
output contract and the renderer budget, and fails closed without writing a manifest.
"""

import hashlib
import json
import struct
import sys
import zlib
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.content import sr2_asset_extract as extract
from bridge.content import sr2_icon, sr2_mesh_scope, sr2_source_cache, sr2_appearance


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _png(pixel=(9, 9, 9, 255)):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack(
            '>I', zlib.crc32(kind + payload) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00' + bytes(pixel))) + chunk(b'IEND', b''))


def _geometry(skin_bones=0):
    geometry = {'vertex_count': 3, 'vertices': [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                'normals': [[0, 0, 1]] * 3, 'tangents': [[1, 0, 0, 1]] * 3,
                'uv': [[0, 0], [1, 0], [0, 1]], 'uv2': [], 'colors': [],
                'triangles': [[0, 1, 2]], 'bind_poses': [], 'bone_weights': []}
    if skin_bones:
        geometry['bind_poses'] = [[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]] * skin_bones
        geometry['bone_weights'] = [[1, 0, 0, 0, 0, 0, 0, 0]] * 3
    return geometry


def _forbidden(label):
    def sentinel(*args, **kwargs):
        raise AssertionError(label + ' must not run in appearance mesh-only mode')
    return sentinel


@pytest.fixture
def mesh_scope_extraction(tmp_path, monkeypatch):
    """Synthetic three-part scope (body + face skinned, cloud static on bone_slime)."""
    state = {}

    skeleton = [{'name': 'bone_root', 'parent': -1, 'p': [0, 0, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]},
                {'name': 'bone_slime', 'parent': 0, 'p': [0, 1, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]},
                {'name': 'bone_jiggle_top', 'parent': 1, 'p': [0, 0.5, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]}]

    body_shader = SimpleNamespace(read_typetree=lambda: {'m_ParsedForm': {'m_Name': 'SR/AMP/Slime/Body'}})
    cloud_shader = SimpleNamespace(read_typetree=lambda: {'m_ParsedForm': {'m_Name': 'SR/FX/Smoke Sloomber'}})
    body_material = SimpleNamespace(
        m_Name='slimeExampleBase',
        m_SavedProperties=SimpleNamespace(
            m_Colors=[('_TopColor', SimpleNamespace(r=0.9, g=0.8, b=0.1, a=1.0))], m_TexEnvs=[],
            m_Floats=[('_LightingUVContribution', 0.0), ('_BodyLightingContribution', 1.0),
                      ('_LightingUVHorizontalAdjust', 0.125)]),
        m_Shader=SimpleNamespace(deref=lambda: body_shader))
    cloud_material = SimpleNamespace(
        m_Name='FX_ExampleCloudPuff',
        m_SavedProperties=SimpleNamespace(m_Colors=[], m_TexEnvs=[], m_Floats=[]),
        m_Shader=SimpleNamespace(deref=lambda: cloud_shader))

    body_binding = {'local_position': [0.0, 0.0, 0.0], 'local_rotation': [0.0, 0.0, 0.0, 1.0],
                    'local_scale': [1.0, 1.0, 1.0], 'skin_bones': ['bone_slime', 'bone_jiggle_top'],
                    'root_bone': 'bone_jiggle_top', 'source_parent_bone': 0, 'source_root_bone': 2,
                    'source_attached_bones': [1, 2], 'source_transform_id': 11, 'source_transform_parent_id': 0}
    face_binding = dict(body_binding, source_face=True, source_face_expression=15,
                        source_face_materials=[{'face_role': role, 'source_path_id': pid}
                                               for pid, role in [(101, 'base-null'), (102, 'eyes'), (103, 'mouth')]])
    cloud_binding = {'local_position': [0.0, 0.0, 0.0], 'local_rotation': [0.0, 0.0, 0.0, 1.0],
                     'local_scale': [1.0, 1.100000023841858, 1.0], 'parent_bone': 'bone_slime',
                     'source_parent_bone': 1, 'source_parent_bone_object_id': 10498,
                     'source_root_bone': 0, 'source_attached_bones': [],
                     'source_transform_id': 33, 'source_transform_parent_id': 0}

    scope = {
        'extraction_scope': 'appearance-lod0-mesh-only-v1', 'coverage': 'partial',
        'source_particles_ported': False,
        'visual_transform': {'local_position': [0.0, -0.5, 0.0], 'local_rotation': [0.0, 0.0, 0.0, 1.0],
                             'local_scale': [1.0, 1.0, 1.0]},
        'parts': [
            {'role': 'body', 'mesh_name': 'slime_example_v4_LOD0', 'renderer_kind': 'skinned',
             'status': 'included', 'vertex_count': 3, 'index_count': 3, 'source_material_ids': [105],
             'source_material_slots': [('slimeExampleBase', body_material)],
             'geometry': _geometry(skin_bones=2), 'appearance_binding': body_binding},
            {'role': 'face', 'mesh_name': 'synthetic_face_LOD0', 'renderer_kind': 'skinned',
             'status': 'included', 'vertex_count': 3, 'index_count': 3,
             'source_material_ids': [101, 102, 103], 'source_material_slots': [],
             'geometry': _geometry(skin_bones=2), 'appearance_binding': face_binding},
            {'role': 'attachment', 'mesh_name': 'example_cloud_LOD0', 'renderer_kind': 'static',
             'status': 'included', 'vertex_count': 3, 'index_count': 3, 'source_material_ids': [107],
             'source_material_slots': [('FX_ExampleCloudPuff', cloud_material)],
             'geometry': _geometry(), 'appearance_binding': cloud_binding},
        ],
        'inventory': [], 'structures': [],
        'exclusions': [{'status': 'excluded', 'reason': 'source-particle-system-not-ported'}],
    }
    state['scope'] = scope
    state['body_binding'] = body_binding
    state['face_binding'] = face_binding
    state['cloud_binding'] = cloud_binding
    state['skeleton'] = skeleton

    monkeypatch.setattr(extract, 'Sr2Assets', lambda path: SimpleNamespace())
    monkeypatch.setattr(extract, 'collect_species_meshes', lambda assets, species: [
        {'role': 'body', 'mesh_name': 'slime_example_v4_LOD0', 'mesh': object()},
        {'role': 'face', 'mesh_name': 'synthetic_face_LOD0', 'mesh': object()},
    ])
    monkeypatch.setattr(extract, 'collect_bone_tree', lambda assets, species: deepcopy(skeleton))
    monkeypatch.setattr(sr2_appearance, 'collect_source_appearance', lambda *args, **kwargs: {'visual_transform': scope['visual_transform'], 'parts': {}})
    monkeypatch.setattr(sr2_source_cache, 'resolve_species_source', lambda assets, species: {'appearance_id': 31307})
    monkeypatch.setattr(sr2_mesh_scope, 'collect_appearance_mesh_scope',
                        lambda assets, resolved, candidates, bones: deepcopy(scope))
    monkeypatch.setattr(sr2_source_cache, 'collect_declared_parts', _forbidden('collect_declared_parts'))
    monkeypatch.setattr(extract, 'part_material_slots', _forbidden('part_material_slots'))

    class FakeExporter:
        def __init__(self, assets, out_dir):
            self.assets = assets
            self.out_dir = out_dir
            self.written = []
            self.exported = []

        def export(self, texture_name):
            self.exported.append(texture_name)
            raise AssertionError('unexpected name-based texture export: ' + str(texture_name))

        def export_source(self, obj):
            raise AssertionError('unexpected source object texture export')

    monkeypatch.setattr(extract, 'TextureExporter', FakeExporter)

    face_calls = []

    def fake_face(assets, binding, exporter):
        face_calls.append(binding)
        target = exporter.out_dir / 'tex-faceatlas.png'
        target.write_bytes(_png((1, 2, 3, 255)))
        return [
            {'face_role': 'base-null', 'name': 'Null Render', 'source_path_id': 101,
             'shader': 'SR/Null Render', 'colors': {}, 'floats': {}, 'render_queue': 2473,
             'keywords': [], 'textures': []},
            {'face_role': 'eyes', 'name': 'eyeHappy', 'source_path_id': 102,
             'shader': 'SR/AMP/Slime/Face/Eyes Default', 'colors': {}, 'floats': {}, 'render_queue': 2473,
             'keywords': [], 'textures': [{'property': '_FaceAtlas', 'file': 'tex-faceatlas.png',
                                            'source_name': 'face_default_happy'}]},
            {'face_role': 'mouth', 'name': 'mouthHappy', 'source_path_id': 103,
             'shader': 'SR/AMP/Slime/Face/Mouth Default', 'colors': {}, 'floats': {}, 'render_queue': 2473,
             'keywords': [], 'textures': []},
        ]

    monkeypatch.setattr(extract, 'export_source_face_materials', fake_face)
    state['face_calls'] = face_calls

    baked_calls = []
    monkeypatch.setattr(extract, 'bake_display_albedo',
                        lambda *args, **kwargs: (baked_calls.append(args[2]), None)[1])
    state['baked_calls'] = baked_calls

    data_dir = tmp_path / 'SlimeRancher2_Data'
    data_dir.mkdir()
    (data_dir / 'resources.assets').write_bytes(b'nonproprietary synthetic fixture')
    state['data_dir'] = data_dir
    state['output'] = tmp_path / 'output'
    state['tmp_path'] = tmp_path
    return state


def _extract(state, out_dir=None, **kwargs):
    return extract.extract_species('Example', 'slime.example', state['data_dir'],
                                   out_dir if out_dir is not None else state['output'],
                                   allow_nonlab=True, appearance_mesh_only=True, **kwargs)


def _load(out_dir):
    return (json.loads((out_dir / 'model.json').read_text()),
            json.loads((out_dir / 'dump-receipt.json').read_text()),
            json.loads((out_dir / 'cache-manifest.json').read_text()))


def test_mesh_only_publishes_skinned_parts_and_bone_attached_static_cloud(mesh_scope_extraction):
    s = mesh_scope_extraction
    summary = _extract(s)
    model, receipt, manifest = _load(s['output'])

    # Three renderer rows: body + face skinned, cloud static (never on the skinned list).
    assert len(model['skinned_renderers']) == 2
    assert len(model['static_renderers']) == 1
    assert [r['role'] for r in model['skinned_renderers']] == ['body', 'face']
    assert summary['ok'] is True and summary['static_renderers'] == 1
    assert summary['extraction_scope'] == 'appearance-lod0-mesh-only-v1'

    cloud = model['static_renderers'][0]
    # Attachment schema: the authored bone path places the node under the real bone_slime
    # transform the loader rebuilds from the skeleton (EnsurePath finds existing names).
    assert cloud['path'] == 'bone_root/bone_slime/attachment_example_cloud_LOD0'
    assert cloud['role'] == 'attachment'
    assert cloud['parent_bone'] == 'bone_slime'
    assert cloud['source_parent_bone_object_id'] == 10498
    assert cloud['local_scale'] == [1.0, 1.100000023841858, 1.0]
    assert cloud['local_position'] == [0.0, 0.0, 0.0]
    assert cloud['local_rotation'] == [0.0, 0.0, 0.0, 1.0]
    # A static part carries no skin data at all.
    for skin_key in ('bind_poses', 'bone_weights', 'skin_bones', 'root_bone'):
        assert skin_key not in cloud
    assert 'source_face' not in cloud
    assert cloud['materials'][0]['name'] == 'FX_ExampleCloudPuff'
    assert cloud['materials'][0]['shader'] == 'SR/FX/Smoke Sloomber'
    assert [r['mesh_name'] for r in model['skinned_renderers']] == ['slime_example_v4_LOD0', 'synthetic_face_LOD0']

    # Skinned rows keep skin arrays and resolve materials by PPtr (never by name guess).
    body, face = model['skinned_renderers']
    assert body['skin_bones'] == ['bone_slime', 'bone_jiggle_top']
    assert body['root_bone'] == 'bone_jiggle_top'
    assert len(body['bind_poses']) == 2 and len(body['bone_weights']) == 3
    assert body['materials'][0]['name'] == 'slimeExampleBase'
    assert body['materials'][0]['floats'] == {'_LightingUVContribution': 0.0,
                                             '_BodyLightingContribution': 1.0,
                                             '_LightingUVHorizontalAdjust': 0.125}
    assert body['materials'][0]['colors'] == {'_TopColor': [0.9, 0.8, 0.1, 1.0]}
    assert face['source_face'] is True and face['source_face_expression'] == 15
    assert 'source_face_materials' not in face
    assert [m['face_role'] for m in face['materials']] == ['base-null', 'eyes', 'mouth']
    assert s['face_calls'] == [s['face_binding']]
    # Display baking runs for the body and cloud materials only, never for the source face.
    assert s['baked_calls'] == ['slimeExampleBase', 'FX_ExampleCloudPuff']

    # Document metadata: skeleton, visual transform, scope provenance, no declared animation.
    assert model['schema'] == 'bridge-native-mesh' and model['schema_version'] == 2
    assert model['skeleton']['bone_count'] == 3
    assert model['visual_transform'] == s['scope']['visual_transform']
    assert model['source_extraction_scope'] == 'appearance-lod0-mesh-only-v1'
    assert 'source_animation' not in model

    # Receipt covers all three rows and the total vertex budget.
    assert receipt['skinned_renderers'] == 2 and receipt['static_renderers'] == 1
    assert receipt['vertex_total'] == 9
    assert [p['role'] for p in receipt['parts']] == ['body', 'face', 'attachment']
    assert receipt['materials'] == ['slimeExampleBase', 'Null Render', 'FX_ExampleCloudPuff']

    # Manifest self-consistency: every non-manifest file is covered with the right hash.
    assert manifest['canonical_id'] == 'slime.example'
    assert manifest['converter_version'] == extract.CONVERTER_VERSION
    assert set(manifest['files']) == {p.name for p in s['output'].iterdir()} - {'cache-manifest.json'}
    assert set(manifest['files']) == {'model.json', 'dump-receipt.json', 'tex-faceatlas.png'}
    assert all(_sha(s['output'] / name) == digest for name, digest in manifest['files'].items())
    assert receipt['model_sha256'] == _sha(s['output'] / 'model.json')


def test_mesh_only_never_touches_the_strict_path_or_canonical_cache(mesh_scope_extraction, monkeypatch):
    s = mesh_scope_extraction
    monkeypatch.setattr(extract, 'CACHE_ROOT', s['tmp_path'] / 'canonical')
    # The sentinel patches installed by the fixture raise if the strict declared-parts path,
    # the name-guessed material fallback, or any reverse texture lookup runs.
    _extract(s)
    assert not (s['tmp_path'] / 'canonical').exists()  # canonical cache is never a target
    assert not (s['tmp_path'] / 'canonical' / 'slime.example').exists()


def test_mesh_only_requires_fresh_explicit_output(mesh_scope_extraction, tmp_path):
    s = mesh_scope_extraction
    with pytest.raises(ValueError, match='fresh explicit output'):
        extract.extract_species('Example', 'slime.example', s['data_dir'], None,
                                allow_nonlab=True, appearance_mesh_only=True)
    # Clip selection is an opt-in extension of the mesh-only scope, but it must be complete and well formed.
    with pytest.raises(ValueError, match='needs both'):
        _extract(s, clip_ids=[8])
    with pytest.raises(ValueError, match='needs both'):
        _extract(s, default_clip='Idle')
    with pytest.raises(ValueError, match='malformed or duplicated'):
        _extract(s, clip_ids=[8, 8], default_clip='Idle')
    existing = tmp_path / 'already-there'
    existing.mkdir()
    with pytest.raises(ValueError, match='already exists'):
        _extract(s, out_dir=existing)
    assert list(existing.iterdir()) == []  # nothing may be written into an existing directory


def test_mesh_only_and_complete_source_are_mutually_exclusive(tmp_path):
    data = tmp_path / 'data'
    data.mkdir()
    with pytest.raises(ValueError, match='mutually exclusive'):
        extract.extract_species('Example', 'slime.example', data, tmp_path / 'out',
                                allow_nonlab=True, complete_source=True, appearance_mesh_only=True)


def test_mesh_only_lab_mode_reuses_the_existing_fresh_output_guard(mesh_scope_extraction, monkeypatch, tmp_path):
    s = mesh_scope_extraction
    # A non-allow-nonlab run must flow through the same strict private-output guard the
    # complete-source cache uses (never the default canonical cache directory).
    monkeypatch.setattr(extract, 'REPO_ROOT', tmp_path)
    lab_data = tmp_path / 'Lab' / 'Games' / 'SR2' / 'SlimeRancher2_Data'
    lab_data.mkdir(parents=True)
    (lab_data / 'resources.assets').write_bytes(b'synthetic')
    out = tmp_path / 'Lab' / 'AssetCache' / 'slime.example'
    calls = []

    def fake_fresh(path):
        calls.append(path)
        path.mkdir(parents=True)
        return path

    monkeypatch.setattr(sr2_icon, '_fresh_private_output', fake_fresh)
    summary = extract.extract_species('Example', 'slime.example', lab_data, out, appearance_mesh_only=True)
    assert calls == [out]
    assert summary['ok'] is True and (out / 'cache-manifest.json').is_file()


def test_mesh_only_refuses_traversal_before_source_read(mesh_scope_extraction, tmp_path, monkeypatch):
    s = mesh_scope_extraction
    monkeypatch.setattr(extract, 'Sr2Assets',
                        lambda *args: (_ for _ in ()).throw(AssertionError('source read before output validation')))
    with pytest.raises(ValueError, match='traversal'):
        _extract(s, out_dir=tmp_path / 'lab' / '..' / 'escape')


def test_mesh_only_refuses_unexpected_scope_version(mesh_scope_extraction):
    s = mesh_scope_extraction
    s['scope']['extraction_scope'] = 'appearance-lod0-mesh-only-v2'
    with pytest.raises(ValueError, match='unexpected appearance mesh scope'):
        _extract(s)
    assert not (s['output'] / 'cache-manifest.json').exists()


def test_mesh_only_refuses_renderer_budget(mesh_scope_extraction):
    s = mesh_scope_extraction
    s['scope']['parts'] = [{'role': 'attachment', 'mesh_name': f'part_{i}_LOD0', 'renderer_kind': 'static'}
                           for i in range(257)]
    with pytest.raises(ValueError, match='renderer budget'):
        _extract(s)
    assert not (s['output'] / 'cache-manifest.json').exists()


def test_mesh_only_refuses_missing_body_and_empty_parts(mesh_scope_extraction):
    s = mesh_scope_extraction
    s['scope']['parts'] = [p for p in s['scope']['parts'] if p['role'] != 'body']
    with pytest.raises(ValueError, match='no body mesh found'):
        _extract(s)
    s['scope']['parts'] = []
    with pytest.raises(ValueError, match='no appearance mesh parts'):
        _extract(s)


def test_mesh_only_fails_closed_on_unresolved_attachment_bone(mesh_scope_extraction):
    s = mesh_scope_extraction
    s['cloud_binding']['parent_bone'] = 'bone_missing'
    with pytest.raises(ValueError, match='parent bone'):
        _extract(s)
    assert not (s['output'] / 'cache-manifest.json').exists()
    assert not (s['output'] / 'model.json').exists()


def test_mesh_only_fails_closed_without_a_usable_geometry_or_materials(mesh_scope_extraction):
    s = mesh_scope_extraction
    s['scope']['parts'][2]['geometry']['vertices'] = []
    s['scope']['parts'][2]['geometry']['vertex_count'] = 0
    with pytest.raises(ValueError, match='no usable geometry'):
        _extract(s)
    assert not (s['output'] / 'cache-manifest.json').exists()

    s2 = mesh_scope_extraction
    s2['output'] = s2['tmp_path'] / 'output2'  # the first attempt already consumed 'output'
    cloud_geometry = s2['scope']['parts'][2]['geometry']
    cloud_geometry['vertices'] = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
    cloud_geometry['triangles'] = [[0, 1, 2], [0, 2, 1]]
    with pytest.raises(ValueError, match='does not cover mesh submeshes'):
        _extract(s2)
    assert not (s2['output'] / 'cache-manifest.json').exists()


def test_cli_threads_appearance_mesh_only_flag(tmp_path, monkeypatch):
    captured = {}

    def fake_extract(species, canonical, sr2_dir, out_dir, allow_nonlab=False, **kwargs):
        captured.update({'species': species, 'canonical': canonical, 'out_dir': out_dir,
                         'allow_nonlab': allow_nonlab, **kwargs})
        return {'ok': True}

    monkeypatch.setattr(extract, 'extract_species', fake_extract)
    code = extract.main(['--species', 'Example', '--canonical', 'slime.example',
                         '--sr2-dir', str(tmp_path), '--out', str(tmp_path / 'out'),
                         '--allow-nonlab', '--appearance-mesh-only'])
    assert code == 0
    assert captured['appearance_mesh_only'] is True

    captured.clear()
    code = extract.main(['--species', 'Example', '--canonical', 'slime.example',
                         '--sr2-dir', str(tmp_path), '--out', str(tmp_path / 'out'),
                         '--allow-nonlab'])
    assert code == 0 and captured['appearance_mesh_only'] is False
