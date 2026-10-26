"""Source-free stone-form graphs; only the Unity binary geometry boundary is doubled.

Contract (bridge-stone-form v1): resolve the statue prefab from the species appearance
or from explicit report ids, walk the LODGroup-authorized children, decode exactly three
static statue meshes, resolve the shared stone material subset with both textures, and
refuse every ambiguity. Never touches the live game or the canonical extractor.
"""
import copy
import importlib
import json
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def ptr(pid, file_id=0):
    return {'m_FileID': file_id, 'm_PathID': pid}


def png_payload():
    """A real 1x1 PNG payload so exported texture stand-ins stay loadable."""
    import struct

    def chunk(tag, payload):
        return (struct.pack('>I', len(payload)) + tag + payload
                + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr)
            + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\x00')) + chunk(b'IEND', b''))


REQUIRED_FLOATS = [('_Gloss', 0.3), ('_GlossPower', 0.4), ('_Outline', 0.005),
                   ('_RimPower', 2.375), ('_RimStrength', 0.24), ('_Shininess', 0.5954557)]
REQUIRED_COLORS = [('_TopColor', (0.84, 0.74, 0.58, 1.0)), ('_MiddleColor', (0.66, 0.45, 0.29, 1.0)),
                   ('_BottomColor', (0.41, 0.28, 0.19, 1.0)), ('_RedTopColor', (0.46, 0.4, 0.36, 1.0)),
                   ('_RedMiddleColor', (0.4, 0.29, 0.25, 1.0)), ('_RedBottomColor', (0.26, 0.19, 0.16, 1.0)),
                   ('_GreenTopColor', (1.0, 0.84, 0.77, 1.0)), ('_GreenMiddleColor', (0.66, 0.45, 0.35, 1.0)),
                   ('_GreenBottomColor', (0.67, 0.54, 0.48, 1.0)), ('_BlueTopColor', (0.3, 0.26, 0.25, 1.0)),
                   ('_BlueMiddleColor', (0.22, 0.16, 0.13, 1.0)), ('_BlueBottomColor', (0.12, 0.09, 0.08, 1.0))]


def mesh_tree(name, vertex_count, index_count):
    return {'m_Name': name, 'm_MeshCompression': 0, 'm_IndexFormat': 0,
            'm_StreamData': {'offset': 0, 'size': 0, 'path': ''},
            'm_VertexData': {'m_VertexCount': vertex_count, 'm_Channels': [{}] * 14,
                             'm_DataSize': bytes(vertex_count * 32)},
            'm_IndexBuffer': bytes(index_count * 2),
            'm_SubMeshes': [{'firstByte': 0, 'indexCount': index_count, 'topology': 0,
                             'baseVertex': 0, 'firstVertex': 0, 'vertexCount': vertex_count}],
            'm_BindPose': [], 'm_BoneNameHashes': []}


@pytest.fixture
def graph(monkeypatch):
    objects = {}

    def add(pid, kind, tree, class_name=None):
        obj = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind))
        obj.read_typetree = lambda **kwargs: tree
        obj.read = lambda: SimpleNamespace(**tree)
        script = SimpleNamespace(read=lambda: SimpleNamespace(m_ClassName=class_name))
        obj.parse_monobehaviour_head = lambda: SimpleNamespace(
            m_Script=SimpleNamespace(deref=lambda: script))
        objects[pid] = obj
        return obj

    def go(pid, name, components, active=1):
        return add(pid, 'GameObject', {'m_Name': name, 'm_IsActive': active,
            'm_Component': [{'component': ptr(c)} for c in components]})

    def transform(pid, gid, father=0, children=(), position=(0, 0, 0)):
        return add(pid, 'Transform', {'m_GameObject': ptr(gid), 'm_Father': ptr(father),
            'm_Children': [ptr(c) for c in children],
            'm_LocalPosition': dict(zip('xyz', position)),
            'm_LocalRotation': dict(zip('xyzw', (0, 0, 0, 1))),
            'm_LocalScale': dict(zip('xyz', (1, 1, 1)))})

    def texenv(pid):
        return {'m_Texture': ptr(pid), 'm_Scale': {'x': 1.0, 'y': 1.0},
                'm_Offset': {'x': 0.0, 'y': 0.0}}

    # statue prefab root: transform + LODGroup + colliders/rigidbody + MonoBehaviours
    go(100, 'synthetic_statue_root', [101, 102, 103, 104, 110, 111])
    transform(101, 100, children=(141, 142, 143, 145))
    add(102, 'LODGroup', {'m_GameObject': ptr(100), 'm_LODs': [
        {'screenRelativeHeight': 0.5, 'fadeTransitionWidth': 0.0, 'renderers': [{'renderer': ptr(161)}]},
        {'screenRelativeHeight': 0.25, 'fadeTransitionWidth': 0.0, 'renderers': [{'renderer': ptr(162)}]},
        {'screenRelativeHeight': 0.0, 'fadeTransitionWidth': 0.0, 'renderers': [{'renderer': ptr(163)}]}]})
    add(103, 'SphereCollider', {'m_GameObject': ptr(100)})
    add(104, 'BoxCollider', {'m_GameObject': ptr(100)})
    add(110, 'MonoBehaviour', {'m_GameObject': ptr(100), 'RingtailMetadata': ptr(112),
        'StatueFx': ptr(113)}, 'RingtailStatue')
    add(111, 'MonoBehaviour', {'m_GameObject': ptr(100)}, 'SyncLODMaterials')
    add(112, 'MonoBehaviour', {'m_GameObject': ptr(0)}, 'RingtailMetadata')
    go(113, 'FX', [])
    # three LOD children: Transform + MeshFilter + MeshRenderer, all sharing material 180
    for lod, (gid, tid, fid, rid, mid, name, count, indices) in enumerate([
            (121, 141, 151, 161, 171, 'synthetic_stone_LOD0', 5, 9),
            (122, 142, 152, 162, 172, 'synthetic_stone_LOD1', 4, 6),
            (123, 143, 153, 163, 173, 'synthetic_stone_LOD2', 3, 3)]):
        go(gid, name, [tid, fid, rid])
        transform(tid, gid, father=101, position=(0, -0.5, 0))
        add(fid, 'MeshFilter', {'m_GameObject': ptr(gid), 'm_Mesh': ptr(mid)})
        add(rid, 'MeshRenderer', {'m_GameObject': ptr(gid), 'm_Materials': [ptr(180)],
            'm_Enabled': True})
        add(mid, 'Mesh', mesh_tree(name, count, indices))
    # meshless attachment child (real DelaunchTrigger analogue, deliberately inactive)
    go(125, 'synthetic_trigger', [145, 155], active=0)
    transform(145, 125, father=101)
    add(155, 'MonoBehaviour', {'m_GameObject': ptr(125)}, 'VacDelaunchTrigger')
    # shared stone material with the documentable property subset and two textures
    add(180, 'Material', {'m_Name': 'synthetic_stone_material', 'm_Shader': ptr(190),
        'm_CustomRenderQueue': 2225, 'm_SavedProperties': {
            'm_Floats': [[n, v] for n, v in REQUIRED_FLOATS] + [['_ExtraFloat', 1.0]],
            'm_Colors': [[n, dict(zip('rgba', v))] for n, v in REQUIRED_COLORS]
                        + [['_ExtraColor', {'r': 0.0, 'g': 0.0, 'b': 0.0, 'a': 0.0}]],
            'm_TexEnvs': [['_MainTex', texenv(200)], ['_Normal', texenv(201)],
                          ['_texcoord', texenv(0)], ['unity_Lightmaps', texenv(0)]]}})
    add(190, 'Shader', {'m_ParsedForm': {'m_Name': 'Synthetic/Stone/StaticUV'}, 'm_Name': ''})
    add(200, 'Texture2D', {'m_Name': 'synthetic_stone_alb', 'm_Width': 8, 'm_Height': 8})
    add(201, 'Texture2D', {'m_Name': 'synthetic_stone_nrm', 'm_Width': 4, 'm_Height': 4})
    # appearance -> stone form -> statue prefab chain
    add(300, 'MonoBehaviour', {'m_Name': 'SyntheticDefault', '_stoneFormAppearance': ptr(310)},
        'SlimeAppearance')
    add(310, 'MonoBehaviour', {'m_Name': 'StoneFormSynthetic', 'StoneFormPrefab': ptr(110),
        'StoneFormChangeFx': ptr(113)}, 'StoneFormAppearance')

    geometry = {
        171: {'vertex_count': 5,
              'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0],
                           [0.5, 0.5, 0.0]],
              'normals': [[0.0, 0.0, 1.0]] * 5, 'tangents': [],
              'uv': [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.5, 0.5]],
              'triangles': [[0, 1, 2, 2, 1, 3, 0, 3, 4]]},
        172: {'vertex_count': 4,
              'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]],
              'normals': [], 'tangents': [],
              'uv': [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
              'triangles': [[0, 1, 2, 0, 2, 3]]},
        173: {'vertex_count': 3,
              'vertices': [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
              'normals': [], 'tangents': [], 'uv': [], 'triangles': [[0, 1, 2]]},
    }

    def decode(mesh, mesh_obj=None):
        result = copy.deepcopy(geometry[mesh_obj.path_id])
        result.update(uv2=[], colors=[], bind_poses=[], bone_weights=[])
        return result

    from bridge.content import sr2_asset_extract
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    return SimpleNamespace(objects=objects, decode=decode,
        assets=SimpleNamespace(_objects=list(objects.values())), add=add, go=go, transform=transform)


def module():
    try:
        return importlib.import_module('bridge.content.sr2_stone_form')
    except ModuleNotFoundError:
        pytest.fail('opt-in stone-form reader is not implemented')


def collect(graph, **kwargs):
    if not any(name in kwargs for name in ('appearance_id', 'stone_form_id', 'statue_prefab_id')):
        kwargs['appearance_id'] = 300
    return module().collect_stone_form(graph.assets, **kwargs)


def exported(graph, form, out_dir):
    return module().export_stone_form(graph.assets, form, out_dir)


def test_three_lods_material_textures_and_source_transforms(graph):
    form = collect(graph)
    data = form.to_dict()
    assert data['schema'] == 'bridge-stone-form' and data['version'] == 1
    assert data['entry'] == 'appearance'
    assert data['provenance']['appearance_id'] == 300
    assert data['provenance']['stone_form_id'] == 310
    assert data['provenance']['statue_id'] == 110
    assert data['provenance']['statue_game_object_id'] == 100
    assert data['provenance']['lod_group_id'] == 102
    assert data['root']['name'] == 'synthetic_statue_root'
    assert data['root']['transform'] == {'local_position': [0.0, 0.0, 0.0],
                                         'local_rotation': [0.0, 0.0, 0.0, 1.0],
                                         'local_scale': [1.0, 1.0, 1.0]}
    assert [m['lod'] for m in data['meshes']] == [0, 1, 2]
    assert [m['path_id'] for m in data['meshes']] == [171, 172, 173]
    assert [m['name'] for m in data['meshes']] == ['synthetic_stone_LOD0', 'synthetic_stone_LOD1',
                                                   'synthetic_stone_LOD2']
    assert [m['vertex_count'] for m in data['meshes']] == [5, 4, 3]
    assert [m['index_count'] for m in data['meshes']] == [9, 6, 3]
    assert [m['triangle_count'] for m in data['meshes']] == [3, 2, 1]
    assert data['meshes'][0]['vertices'][1] == [1.0, 0.0, 0.0]
    assert len(data['meshes'][0]['normals']) == 5 and data['meshes'][1]['normals'] == []
    assert data['meshes'][0]['uv'][4] == [0.5, 0.5] and data['meshes'][2]['uv'] == []
    assert data['meshes'][2]['tangents'] == []
    assert data['meshes'][0]['triangles'] == [[0, 1, 2, 2, 1, 3, 0, 3, 4]]
    assert data['meshes'][0]['transform'] == {'local_position': [0.0, -0.5, 0.0],
                                              'local_rotation': [0.0, 0.0, 0.0, 1.0],
                                              'local_scale': [1.0, 1.0, 1.0]}
    assert [m['game_object_name'] for m in data['meshes']] == [
        'synthetic_stone_LOD0', 'synthetic_stone_LOD1', 'synthetic_stone_LOD2']
    assert data['meshes'][0]['renderer_enabled'] is True
    assert [m['mesh_filter_id'] for m in data['meshes']] == [151, 152, 153]
    assert [m['mesh_renderer_id'] for m in data['meshes']] == [161, 162, 163]
    assert data['material']['path_id'] == 180 and data['material']['name'] == 'synthetic_stone_material'
    assert data['material']['shader_path_id'] == 190
    assert data['material']['shader_name'] == 'Synthetic/Stone/StaticUV'
    assert data['material']['render_queue'] == 2225
    assert data['material']['floats'] == dict(REQUIRED_FLOATS)
    assert data['material']['colors']['_TopColor'] == [0.84, 0.74, 0.58, 1.0]
    assert set(data['material']['colors']) == {name for name, _ in REQUIRED_COLORS}
    assert data['material']['textures']['_MainTex'] == {
        'path_id': 200, 'name': 'synthetic_stone_alb', 'width': 8, 'height': 8, 'file': None}
    assert data['material']['textures']['_Normal'] == {
        'path_id': 201, 'name': 'synthetic_stone_nrm', 'width': 4, 'height': 4, 'file': None}
    json.dumps(data)  # the typed result must be JSON-clean without further conversion
    assert form.schema == 'bridge-stone-form' and len(form.meshes) == 3


@pytest.mark.parametrize('kwargs', [
    dict(appearance_id=300), dict(stone_form_id=310), dict(statue_prefab_id=110)])
def test_report_entry_points_resolve_the_same_statue(graph, kwargs):
    form = collect(graph, **kwargs)
    data = form.to_dict()
    assert [m['path_id'] for m in data['meshes']] == [171, 172, 173]
    assert data['provenance']['statue_id'] == 110
    if 'appearance_id' in kwargs:
        assert data['provenance']['appearance_id'] == 300
        assert data['provenance']['stone_form_id'] == 310
    elif 'stone_form_id' in kwargs:
        assert data['provenance']['appearance_id'] is None
        assert data['provenance']['stone_form_id'] == 310
    else:
        assert data['provenance']['appearance_id'] is None
        assert data['provenance']['stone_form_id'] is None


@pytest.mark.parametrize('kwargs', [dict(appearance_id=None),
                                    dict(appearance_id=300, stone_form_id=310),
                                    dict(appearance_id=0), dict(appearance_id=True),
                                    dict(appearance_id='300'), dict(statue_prefab_id=-1)])
def test_entry_point_selection_is_exact(graph, kwargs):
    with pytest.raises(ValueError):
        module().collect_stone_form(graph.assets, **kwargs)


@pytest.mark.parametrize('pid', [300, 310, 110])
def test_entry_object_class_must_match(graph, pid):
    def wrong_head():
        return SimpleNamespace(m_Script=SimpleNamespace(deref=lambda: SimpleNamespace(
            read=lambda: SimpleNamespace(m_ClassName='SomethingElse'))))
    graph.objects[pid].parse_monobehaviour_head = wrong_head
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[102].read_typetree().update(m_LODs=[]),
    lambda g: g.objects[102].read_typetree().update(m_LODs=g.objects[102].read_typetree()['m_LODs'][:2]),
    lambda g: g.objects[102].read_typetree().update(m_LODs=g.objects[102].read_typetree()['m_LODs']
                                                        + [copy.deepcopy(g.objects[102].read_typetree()['m_LODs'][2])]),
    lambda g: g.objects[102].read_typetree()['m_LODs'][1].update(renderers=[]),
    lambda g: g.objects[102].read_typetree()['m_LODs'][1].update(
        renderers=[{'renderer': ptr(132)}, {'renderer': ptr(131)}]),
    lambda g: g.objects[102].read_typetree()['m_LODs'][2].update(renderers=[{'renderer': ptr(142)}]),
    lambda g: g.objects[102].read_typetree()['m_LODs'][2].update(renderers=[{'renderer': ptr(173)}]),
    lambda g: g.objects[102].read_typetree()['m_LODs'][0].update(renderers=[{'renderer': ptr(999)}]),
])
def test_incomplete_duplicate_or_misreferenced_lod_set_is_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[102].read_typetree()['m_LODs'][0]['renderers'][0].update(renderer=ptr(131, 2)),
    lambda g: g.objects[102].read_typetree()['m_LODs'][0]['renderers'][0].update(renderer=ptr(-1)),
    lambda g: g.objects[102].read_typetree()['m_LODs'][0]['renderers'][0].update(renderer=ptr(True)),
    lambda g: g.objects[102].read_typetree()['m_LODs'][1].update(renderers=[{}]),
])
def test_malformed_lod_renderer_references_are_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[102].read_typetree()['m_LODs'][1]['renderers'][0].update(renderer=ptr(161)),
    lambda g: g.objects[102].read_typetree()['m_LODs'][2]['renderers'][0].update(renderer=ptr(162)),
    lambda g: g.objects[152].read_typetree().update(m_Mesh=ptr(171)),
    lambda g: g.objects[101].read_typetree()['m_Children'].append(ptr(141)),
    lambda g: g.objects[100].read_typetree()['m_Component'].append({'component': ptr(103)}),
    lambda g: g.objects[142].read_typetree().update(m_Father=ptr(121)),
    lambda g: g.objects[141].read_typetree().update(m_Children=[ptr(142)]),
])
def test_duplicate_lod_go_mesh_child_or_component_is_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


def test_two_filters_or_renderers_on_a_lod_object_are_refused(graph):
    graph.add(118, 'MeshFilter', {'m_GameObject': ptr(121), 'm_Mesh': ptr(172)})
    graph.objects[121].read_typetree()['m_Component'].append({'component': ptr(118)})
    with pytest.raises(ValueError):
        collect(graph)
    graph.objects[121].read_typetree()['m_Component'].remove({'component': ptr(118)})
    graph.add(119, 'MeshRenderer', {'m_GameObject': ptr(121), 'm_Materials': [ptr(180)],
        'm_Enabled': True})
    graph.objects[121].read_typetree()['m_Component'].append({'component': ptr(119)})
    with pytest.raises(ValueError):
        collect(graph)


def test_extra_mesh_child_not_registered_in_lod_group_is_refused(graph):
    graph.add(126, 'SkinnedMeshRenderer', {'m_GameObject': ptr(125), 'm_Materials': [ptr(180)]})
    graph.objects[125].read_typetree()['m_Component'].append({'component': ptr(126)})
    with pytest.raises(ValueError):
        collect(graph)


def test_unsupported_renderer_component_on_the_statue_root_is_refused(graph):
    graph.add(136, 'ParticleSystemRenderer', {'m_GameObject': ptr(100)})
    graph.objects[100].read_typetree()['m_Component'].append({'component': ptr(136)})
    with pytest.raises(ValueError):
        collect(graph)


def test_unsupported_component_on_an_attachment_child_is_refused(graph):
    graph.add(137, 'ParticleSystem', {'m_GameObject': ptr(125)})
    graph.objects[125].read_typetree()['m_Component'].append({'component': ptr(137)})
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[171].read_typetree().update(m_MeshCompression=1),
    lambda g: g.objects[171].read_typetree().update(m_MeshCompression=True),
    lambda g: g.objects[171].read_typetree().update(m_IndexFormat=1),
    lambda g: g.objects[171].read_typetree().update(m_IndexFormat=True),
    lambda g: g.objects[171].read_typetree().update(m_SubMeshes=[]),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(indexCount=4),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(indexCount=True),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(indexCount=600_003),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(topology=1),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(baseVertex=1),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(firstByte=2),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(firstVertex=1),
    lambda g: g.objects[171].read_typetree()['m_SubMeshes'][0].update(vertexCount=4),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_VertexCount=0),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_VertexCount=2049),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_VertexCount=True),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_Channels=[]),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_Channels=[{}] * 17),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_DataSize=b''),
    lambda g: g.objects[171].read_typetree()['m_VertexData'].update(m_DataSize=bytes(3)),
    lambda g: g.objects[171].read_typetree().update(m_IndexBuffer=bytes(20)),
    lambda g: g.objects[171].read_typetree().update(m_StreamData={'offset': 1, 'size': 0, 'path': ''}),
    lambda g: g.objects[171].read_typetree().update(m_StreamData={'offset': 0, 'size': 1, 'path': ''}),
    lambda g: g.objects[171].read_typetree().update(
        m_StreamData={'offset': 0, 'size': 100, 'path': '../secret.resS'}),
    lambda g: g.objects[171].read_typetree().update(
        m_StreamData={'offset': 0, 'size': 100, 'path': 'other.resS'}),
    lambda g: g.objects[171].read_typetree().update(
        m_StreamData={'offset': 0, 'size': 100_000_000, 'path': 'resources.assets.resS'}),
    lambda g: g.objects[171].read_typetree().update(
        m_StreamData={'offset': True, 'size': 160, 'path': 'resources.assets.resS'}),
    lambda g: g.objects[171].read_typetree().update(m_BindPose=[[1.0] * 16]),
    lambda g: g.objects[171].read_typetree().update(m_BoneNameHashes=[1, 2]),
    lambda g: g.objects[171].read_typetree().update(m_Name=''),
])
def test_mesh_metadata_is_refused_before_the_geometry_decoder(graph, monkeypatch, change):
    change(graph)
    called = []
    def decode(*args, **kwargs):
        called.append(True)
        return graph.decode(*args)
    from bridge.content import sr2_asset_extract
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)
    assert called == []


def test_declared_streaming_slice_is_accepted(graph):
    graph.objects[171].read_typetree().update(
        m_StreamData={'offset': 16, 'size': 5 * 32, 'path': 'resources.assets.resS'},
        m_VertexData={'m_VertexCount': 5, 'm_Channels': [{}] * 14, 'm_DataSize': b''})
    data = collect(graph).to_dict()
    assert data['meshes'][0]['vertex_count'] == 5


@pytest.mark.parametrize('budget,value', [('MAX_OBJECTS', 1), ('MAX_COMPONENTS', 3),
                                          ('MAX_CHILDREN', 2), ('MAX_NODES', 2), ('MAX_DEPTH', 0),
                                          ('MAX_LODS', 2), ('MAX_VERTICES', 4), ('MAX_TRIANGLES', 2)])
def test_declared_budgets_are_hard_refusals(graph, monkeypatch, budget, value):
    monkeypatch.setattr(module(), budget, value)
    with pytest.raises(ValueError, match='budget|oversized'):
        collect(graph)


@pytest.mark.parametrize('channel,size', [('normals', 3), ('uv', 2), ('tangents', 4)])
def test_bad_optional_geometry_channels_are_refused(graph, monkeypatch, channel, size):
    def decode(mesh, mesh_obj):
        result = graph.decode(mesh, mesh_obj)
        result[channel] = [[float('nan')] * size] * 5
        return result
    from bridge.content import sr2_asset_extract
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('field,value', [
    ('vertices', []), ('vertices', [[0, 0, 0], [1, 0, 0], [0, float('inf'), 0], [1, 1, 0], [0.5, 0.5, 0]]),
    ('vertex_count', 4), ('triangles', [[]]), ('triangles', [[0, 1, 7]]),
    ('triangles', [[0, 1, True]]), ('triangles', [[0, 1, 2, 0]]), ('triangles', []),
    ('bind_poses', [[1.0] * 16]), ('bone_weights', [[1, 0, 0, 0, 0, 0, 0, 0]]),
])
def test_bad_decoded_geometry_is_refused(graph, monkeypatch, field, value):
    def decode(mesh, mesh_obj):
        result = graph.decode(mesh, mesh_obj)
        result[field] = value
        return result
    from bridge.content import sr2_asset_extract
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[310].read_typetree().update(StoneFormPrefab=ptr(110, 2)),
    lambda g: g.objects[310].read_typetree().update(StoneFormPrefab=ptr(-1)),
    lambda g: g.objects[310].read_typetree().update(StoneFormPrefab=ptr(0)),
    lambda g: g.objects[310].read_typetree().update(StoneFormPrefab=ptr(111)),
    lambda g: g.objects[300].read_typetree().update(_stoneFormAppearance=ptr(0)),
    lambda g: g.objects[300].read_typetree().update(_stoneFormAppearance=ptr(310, 4)),
    lambda g: g.objects[110].read_typetree().update(m_GameObject=ptr(0)),
    lambda g: g.objects[110].read_typetree().update(m_GameObject=ptr(110)),
    lambda g: g.objects[151].read_typetree().update(m_Mesh=ptr(171, 1)),
    lambda g: g.objects[161].read_typetree().update(m_Materials=[]),
    lambda g: g.objects[161].read_typetree().update(m_Materials=[ptr(180), ptr(180)]),
    lambda g: g.objects[162].read_typetree().update(m_Materials=[ptr(181)]),
    lambda g: g.objects[163].read_typetree().update(m_Enabled=2),
    lambda g: g.objects[121].read_typetree().update(m_IsActive='yes'),
    lambda g: g.objects[102].read_typetree().update(m_LODs='solo'),
])
def test_malformed_or_external_references_are_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('field,value', [
    ('m_FileID', True), ('m_FileID', False), ('m_FileID', 1.0), ('m_FileID', '0'),
    ('m_FileID', None), ('m_FileID', -1), ('m_PathID', True), ('m_PathID', 1.0),
    ('m_PathID', '310'), ('m_PathID', None),
])
def test_active_pptr_fields_are_exact_local_nonnegative_integers(graph, field, value):
    graph.objects[310].read_typetree()['StoneFormPrefab'][field] = value
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][1][1].update(
        m_Texture=ptr(0)),
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][1][1].update(
        m_Texture=ptr(202)),
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][1][1].update(
        m_Texture=ptr(201, 3)),
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][1][1].update(
        m_Texture=ptr(190)),
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][0][1].update(
        m_Scale={'x': float('nan'), 'y': 1.0}),
    lambda g: g.objects[183].read_typetree()['m_SavedProperties']['m_TexEnvs'][0][1].update(
        m_Offset={'x': 0.0, 'y': 'zero'}),
    lambda g: g.objects[200].read_typetree().update(m_Width=0),
    lambda g: g.objects[201].read_typetree().update(m_Name=''),
])
def test_missing_or_unsupported_textures_are_refused(graph, change):
    graph.add(183, 'Material', copy.deepcopy(graph.objects[180].read_typetree()))
    for rid in (161, 162, 163):
        graph.objects[rid].read_typetree().update(m_Materials=[ptr(183)])
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_TexEnvs'].append(
        ['_MainTex', {'m_Texture': ptr(200), 'm_Scale': {'x': 1.0, 'y': 1.0},
                      'm_Offset': {'x': 0.0, 'y': 0.0}}]),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_TexEnvs'].append(
        ['_BumpMap', {'m_Texture': ptr(201), 'm_Scale': {'x': 1.0, 'y': 1.0},
                      'm_Offset': {'x': 0.0, 'y': 0.0}}]),
])
def test_duplicate_or_extra_nonnull_texture_slots_are_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Floats'].pop(0),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Floats'].append(['_Gloss', 0.5]),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Floats'][0].__setitem__(1, float('nan')),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Floats'][1].__setitem__(1, True),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Colors'].pop(0),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Colors'][0][1].pop('b'),
    lambda g: g.objects[180].read_typetree()['m_SavedProperties']['m_Colors'][1][1].__setitem__('a', float('inf')),
    lambda g: g.objects[180].read_typetree().update(m_Shader=ptr(0)),
    lambda g: g.objects[190].read_typetree()['m_ParsedForm'].update(m_Name=''),
    lambda g: g.objects[180].read_typetree().update(m_CustomRenderQueue=True),
])
def test_incomplete_or_malformed_material_subset_is_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda g: g.objects[101].read_typetree()['m_LocalScale'].update(x=0.0),
    lambda g: g.objects[101].read_typetree()['m_LocalRotation'].update(w=0.0),
    lambda g: g.objects[101].read_typetree()['m_LocalPosition'].update(y=float('nan')),
    lambda g: g.objects[141].read_typetree()['m_LocalRotation'].update(w=0.5),
    lambda g: g.objects[142].read_typetree()['m_LocalPosition'].update(z=float('inf')),
    lambda g: g.objects[101].read_typetree().update(m_Father=ptr(99)),
])
def test_singular_or_nonunit_source_transforms_are_refused(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


def test_export_writes_only_a_new_folder_with_receipt(graph, monkeypatch, tmp_path):
    form = collect(graph)
    written = []

    class FakeExporter:
        def __init__(self, assets, out_dir):
            self.out_dir = Path(out_dir)

        def export_source(self, obj):
            payload = obj.read()
            name = 'tex-source-{0}-{1}.png'.format(obj.path_id, payload.m_Name)
            (self.out_dir / name).write_bytes(png_payload())
            written.append(name)
            return name

    from bridge.content import sr2_asset_extract
    monkeypatch.setattr(sr2_asset_extract, 'TextureExporter', FakeExporter)
    out_dir = tmp_path / 'stone-form-out'
    report = exported(graph, form, out_dir)
    assert sorted(written) == ['tex-source-200-synthetic_stone_alb.png',
                               'tex-source-201-synthetic_stone_nrm.png']
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == ['stone-form-receipt.json', 'stone-form.json',
                     'tex-source-200-synthetic_stone_alb.png', 'tex-source-201-synthetic_stone_nrm.png']
    payload = json.loads((out_dir / 'stone-form.json').read_text(encoding='utf-8'))
    assert payload['material']['textures']['_MainTex']['file'] == 'tex-source-200-synthetic_stone_alb.png'
    assert payload['material']['textures']['_Normal']['file'] == 'tex-source-201-synthetic_stone_nrm.png'
    receipt = json.loads((out_dir / 'stone-form-receipt.json').read_text(encoding='utf-8'))
    assert receipt['schema'] == 'bridge-stone-form' and receipt['version'] == 1
    assert receipt['source_read_only'] is True
    assert sorted(entry['name'] for entry in receipt['files']) == [
        'stone-form.json', 'tex-source-200-synthetic_stone_alb.png',
        'tex-source-201-synthetic_stone_nrm.png']
    assert all(len(entry['sha256']) == 64 and entry['size'] > 0 for entry in receipt['files'])
    assert receipt['counts'] == {'meshes': 3, 'vertices': 12, 'triangles': 6}
    assert report['folder'] == str(out_dir)
    # never overwrite: an existing folder (even empty) is refused
    with pytest.raises(ValueError):
        exported(graph, form, out_dir)
    empty = tmp_path / 'existing'
    empty.mkdir()
    with pytest.raises(ValueError):
        exported(graph, form, empty)
    # the typed result in memory is not mutated by the export
    assert form.to_dict()['material']['textures']['_MainTex']['file'] is None


def test_export_of_incomplete_form_is_refused_before_writing(graph, tmp_path):
    form = collect(graph)
    broken = form.to_dict()
    broken['meshes'] = broken['meshes'][:2]
    with pytest.raises(ValueError):
        module().export_stone_form(graph.assets, broken, tmp_path / 'nope')
    assert not (tmp_path / 'nope').exists()
