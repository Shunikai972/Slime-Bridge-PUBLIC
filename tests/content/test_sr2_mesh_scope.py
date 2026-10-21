"""Source-free LOD0 graphs; only the Unity binary geometry boundary is doubled."""
import copy
import importlib
import zlib
from types import SimpleNamespace

import pytest


def ptr(pid, file_id=0):
    return {'m_FileID': file_id, 'm_PathID': pid}


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
        return tree

    def go(pid, name, components):
        return add(pid, 'GameObject', {'m_Name': name, 'm_IsActive': 1,
            'm_Component': [{'component': ptr(c)} for c in components]})

    def transform(pid, gid, father=0, children=(), position=(0, 0, 0)):
        return add(pid, 'Transform', {'m_GameObject': ptr(gid), 'm_Father': ptr(father),
            'm_Children': [ptr(c) for c in children],
            'm_LocalPosition': dict(zip('xyz', position)),
            'm_LocalRotation': dict(zip('xyzw', (0, 0, 0, 1))),
            'm_LocalScale': dict(zip('xyz', (1, 1, 1)))})

    def structure(element, material, face=False):
        return {'Element': ptr(element), 'SupportsFaces': int(face),
            'DefaultMaterials': [ptr(material)],
            'ElementMaterials': [{'OverrideDefaults': 0, 'Materials': []}],
            'FaceRules': [{'ShowEyes': 1, 'ShowMouth': 1}]}

    go(1, 'synthetic_actor', [2, 3])
    transform(2, 1, children=(6,))
    add(3, 'MonoBehaviour', {'m_GameObject': ptr(1), 'RootAppearanceObject': ptr(5),
        'Appearance': ptr(7), 'SlimeExpression': 15,
        'Bones': [{'Bone': 1, 'BoneObject': ptr(9)}, {'Bone': 3, 'BoneObject': ptr(50)}]},
        'SlimeAppearanceApplicator')
    go(5, 'Appearance', [6])
    transform(6, 5, father=2, children=(51,), position=(0, -.5, 0))
    appearance = add(7, 'MonoBehaviour', {'_face': ptr(8), '_structures': [
        structure(10, 40), structure(20, 41, True), structure(30, 44), structure(60, 45)]},
        'SlimeAppearance')
    add(8, 'MonoBehaviour', {'ExpressionFaces': [
        {'SlimeExpression': 14, 'Eyes': ptr(44), 'Mouth': ptr(44)},
        {'SlimeExpression': 15, 'Eyes': ptr(42), 'Mouth': ptr(43)}]}, 'SlimeFace')
    go(9, 'skin', [52])
    transform(52, 9, father=51)
    go(50, 'bone_root', [51])
    transform(51, 50, father=6, children=(52,))
    for element, gid, tid, sao, rid, mid, role in [
        (10, 11, 12, 13, 14, 15, 'body'), (20, 21, 22, 23, 24, 25, 'face')]:
        add(element, 'MonoBehaviour', {'Prefabs': [ptr(sao)]}, 'SlimeAppearanceElement')
        go(gid, 'decoy_' + role, [tid, sao, rid])
        transform(tid, gid)
        add(sao, 'MonoBehaviour', {'m_GameObject': ptr(gid), 'ParentBone': 0,
            'RootBone': 1, 'AttachedBones': [1]}, 'SlimeAppearanceObject')
        add(rid, 'SkinnedMeshRenderer', {'m_GameObject': ptr(gid), 'm_Mesh': ptr(mid),
            'm_Materials': [ptr(49)], 'm_Enabled': 1})
        add(mid, 'Mesh', {'m_Name': 'synthetic_' + role, 'm_BindPose': [
            [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]],
            'm_BoneNameHashes': [zlib.crc32(b'bone_root/skin')]})
    add(30, 'MonoBehaviour', {'Prefabs': [ptr(33)]}, 'SlimeAppearanceElement')
    go(31, 'not_named_like_species', [32, 33, 34, 35])
    transform(32, 31, position=(1, 2, 3))
    add(33, 'MonoBehaviour', {'m_GameObject': ptr(31), 'ParentBone': 3,
        'RootBone': 0, 'AttachedBones': []}, 'SlimeAppearanceObject')
    add(34, 'MeshRenderer', {'m_GameObject': ptr(31), 'm_Materials': [ptr(49)], 'm_Enabled': 1})
    add(35, 'MeshFilter', {'m_GameObject': ptr(31), 'm_Mesh': ptr(36)})
    add(36, 'Mesh', {'m_Name': 'synthetic_cloud', 'm_BindPose': [], 'm_BoneNameHashes': []})
    add(60, 'MonoBehaviour', {'Prefabs': [ptr(63)]}, 'SlimeAppearanceElement')
    go(61, 'particles', [62, 63, 64, 65])
    transform(62, 61, children=(72,))
    add(63, 'MonoBehaviour', {'m_GameObject': ptr(61), 'ParentBone': 3,
        'RootBone': 0, 'AttachedBones': []}, 'SlimeAppearanceObject')
    add(64, 'ParticleSystemRenderer', {'m_GameObject': ptr(61), 'm_Enabled': 1,
        'm_RenderMode': 0, 'm_Materials': [ptr(49)], 'm_Mesh': ptr(999, 2)})
    add(65, 'ParticleSystem', {'m_GameObject': ptr(61)})
    go(71, 'child', [72, 74, 75])
    transform(72, 71, father=62, position=(0, 4, 0))
    add(74, 'ParticleSystemRenderer', {'m_GameObject': ptr(71), 'm_Enabled': 0,
        'm_RenderMode': 0, 'm_Materials': [ptr(46)], 'm_Mesh': ptr(0)})
    add(75, 'ParticleSystem', {'m_GameObject': ptr(71)})
    for pid in range(40, 50):
        add(pid, 'Material', {'m_Name': 'material_' + str(pid), 'm_Shader': ptr(90)})
    add(90, 'Shader', {'m_ParsedForm': {'m_Name': 'synthetic/shader'}})
    skeleton = [dict(name='bone_root', parent=-1, p=[0, 0, 0], q=[0, 0, 0, 1], s=[1, 1, 1]),
                dict(name='skin', parent=0, p=[0, 0, 0], q=[0, 0, 0, 1], s=[1, 1, 1])]
    candidates = [{'role': role, 'mesh_name': objects[mid].read().m_Name,
        'mesh': objects[mid].read(), 'mesh_obj': objects[mid]} for mid, role in [(15, 'body'), (25, 'face')]]
    from bridge.content import sr2_asset_extract

    def decode(mesh, mesh_obj=None):
        skinned = mesh_obj.path_id != 36
        return {'vertex_count': 3, 'vertices': [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                'triangles': [[0, 1, 2]], 'normals': [], 'uv': [], 'uv2': [], 'colors': [],
                'tangents': [], 'bind_poses': copy.deepcopy(mesh_obj.read_typetree()['m_BindPose']),
                'bone_weights': [[1, 0, 0, 0, 0, 0, 0, 0]] * 3 if skinned else []}

    for mid in (15, 25, 36):
        objects[mid].read_typetree().update(
            m_VertexData={'m_VertexCount': 3, 'm_Channels': [{}] * 14, 'm_DataSize': bytes(36)},
            m_IndexBuffer=bytes(6), m_IndexFormat=0, m_MeshCompression=0,
            m_StreamData={'offset': 0, 'size': 0, 'path': ''},
            m_SubMeshes=[{'firstByte': 0, 'indexCount': 3, 'topology': 0, 'baseVertex': 0,
                          'firstVertex': 0, 'vertexCount': 3}])
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    return SimpleNamespace(objects=objects, assets=SimpleNamespace(_objects=list(objects.values())),
        resolved={'appearance_id': 7, 'applicator_id': 3, 'prefab_id': 1},
        candidates=candidates, skeleton=skeleton, appearance=appearance, add=add, go=go,
        transform=transform, decode=decode)


def collect(graph):
    try:
        module = importlib.import_module('bridge.content.sr2_mesh_scope')
    except ModuleNotFoundError:
        pytest.fail('opt-in appearance mesh-only reader is not implemented')
    return module.collect_appearance_mesh_scope(graph.assets, graph.resolved, graph.candidates, graph.skeleton)


def test_mesh_only_includes_static_geometry_without_species_name_guessing(graph):
    result = collect(graph)
    assert result['extraction_scope'] == 'appearance-lod0-mesh-only-v1'
    assert result['coverage'] == 'partial'
    assert result['source_particles_ported'] is False
    assert [(p['mesh_obj'].path_id, p['role'], p['renderer_kind']) for p in result['parts']] == [
        (15, 'body', 'skinned'), (25, 'face', 'skinned'), (36, 'attachment', 'static')]
    assert sum(p['geometry']['vertex_count'] for p in result['parts']) == 9
    assert sum(len(t) for p in result['parts'] for t in p['geometry']['triangles']) == 9
    assert graph.candidates[0].keys() == {'role', 'mesh_name', 'mesh', 'mesh_obj'}


def test_particle_exclusions_cover_validated_child_with_exact_source_ids(graph):
    result = collect(graph)
    assert [(r['source_renderer_id'], r['source_particle_system_id'], r['source_material_ids'])
            for r in result['exclusions']] == [(64, 65, [45]), (74, 75, [46])]
    assert [r['source_path'] for r in result['exclusions']] == ['particles', 'particles/child']
    assert all(r['reason'] == 'source-particle-system-not-ported' for r in result['exclusions'])
    assert result['exclusions'][0]['source_particle_mesh_ref'] == ptr(999, 2)
    assert result['exclusions'][1]['renderer_enabled'] is False
    assert result['exclusions'][1]['source_transform_parent_id'] == 62
    assert len(result['inventory']) == 5


def test_unknown_renderer_is_an_error_not_a_particle_exclusion(graph):
    graph.objects[74].type.name = 'TrailRenderer'
    with pytest.raises(ValueError, match='unsupported'):
        collect(graph)


def test_effective_materials_and_exact_skin_or_rigid_bindings(graph):
    graph.appearance['_structures'][2]['ElementMaterials'][0] = {
        'OverrideDefaults': 1, 'Materials': [ptr(47)]}
    result = collect(graph)
    body, face, cloud = result['parts']
    assert body['source_material_ids'] == [40]
    assert face['source_material_ids'] == [41, 42, 43]
    assert face['appearance_binding']['source_face_expression'] == 15
    assert [r['face_role'] for r in face['appearance_binding']['source_face_materials']] == [
        'base-null', 'eyes', 'mouth']
    assert body['appearance_binding']['skin_bones'] == ['skin']
    assert body['appearance_binding']['root_bone'] == 'skin'
    assert cloud['source_material_ids'] == [47]
    assert cloud['source_material_slots'][0][0] == 'material_47'
    assert cloud['source_material_provenance'][0]['shader_path_id'] == 90
    assert cloud['appearance_binding'] == {
        'parent_bone': 'bone_root', 'source_parent_bone': 3, 'source_parent_bone_object_id': 50,
        'source_root_bone': 0, 'source_attached_bones': [],
        'local_position': [1, 2, 3], 'local_rotation': [0, 0, 0, 1], 'local_scale': [1, 1, 1],
        'source_transform_id': 32, 'source_transform_parent_id': 0}
    assert result['visual_transform']['local_position'] == [0, -.5, 0]


def test_existing_complete_source_path_still_refuses_static_cloud(graph):
    from bridge.content.sr2_source_cache import collect_declared_parts
    with pytest.raises(ValueError, match='appearance LOD0 has no unique skinned renderer'):
        collect_declared_parts(graph.assets, graph.resolved, graph.candidates)
    collect(graph)
    with pytest.raises(ValueError, match='appearance LOD0 has no unique skinned renderer'):
        collect_declared_parts(graph.assets, graph.resolved, graph.candidates)


@pytest.mark.parametrize('change', [
    lambda g: g.appearance.update(_structures=[]),
    lambda g: g.appearance.update(_structures=[g.appearance['_structures'][0]] * 65),
    lambda g: g.appearance['_structures'].append(copy.deepcopy(g.appearance['_structures'][0])),
    lambda g: g.candidates.append(dict(g.candidates[0])),
    lambda g: g.objects[30].read_typetree().update(Prefabs=[]),
    lambda g: g.objects[30].read_typetree().update(Prefabs=[ptr(33)] * 9),
    lambda g: g.objects[35].read_typetree().update(m_Mesh=ptr(0)),
    lambda g: g.objects[31].read_typetree()['m_Component'].remove({'component': ptr(35)}),
    lambda g: g.objects[31].read_typetree()['m_Component'].append({'component': ptr(35)}),
    lambda g: g.objects[31].read_typetree()['m_Component'].append({'component': ptr(14)}),
    lambda g: g.objects[34].read_typetree().update(m_GameObject=ptr(11)),
    lambda g: g.objects[14].read_typetree().update(m_Mesh=ptr(36)),
    lambda g: g.appearance['_structures'][0].update(SupportsFaces=1),
    lambda g: g.appearance['_structures'][2].update(SupportsFaces=1),
    lambda g: g.appearance['_structures'][2].update(DefaultMaterials=[]),
    lambda g: g.appearance['_structures'][2].update(DefaultMaterials=[ptr(999)]),
    lambda g: g.appearance['_structures'][2].update(DefaultMaterials=[ptr(44)] * 17),
    lambda g: g.appearance['_structures'][2].update(ElementMaterials=[]),
    lambda g: g.appearance['_structures'][2]['ElementMaterials'][0].update(OverrideDefaults=2),
    lambda g: g.objects[44].read_typetree().update(m_Shader=ptr(0)),
    lambda g: g.objects[33].read_typetree().update(ParentBone=999),
    lambda g: g.objects[33].read_typetree().update(ParentBone=True),
    lambda g: g.objects[33].read_typetree().update(RootBone=1),
    lambda g: g.objects[33].read_typetree().update(AttachedBones=[1]),
    lambda g: g.objects[32].read_typetree().update(m_Father=ptr(62)),
    lambda g: g.objects[32].read_typetree()['m_LocalPosition'].update(x=float('nan')),
    lambda g: g.objects[32].read_typetree()['m_LocalScale'].update(x=0),
    lambda g: g.objects[32].read_typetree()['m_LocalRotation'].update(w=0),
    lambda g: g.objects[3].read_typetree()['Bones'].append({'Bone': 1, 'BoneObject': ptr(50)}),
    lambda g: g.objects[3].read_typetree()['Bones'][1].update(BoneObject=ptr(9)),
    lambda g: g.skeleton.append(dict(g.skeleton[0])),
    lambda g: g.skeleton[1].update(parent=True),
    lambda g: g.skeleton[1].update(parent=1),
    lambda g: g.objects[8].read_typetree()['ExpressionFaces'].append(
        copy.deepcopy(g.objects[8].read_typetree()['ExpressionFaces'][1])),
    lambda g: g.objects[8].read_typetree()['ExpressionFaces'][1].update(Eyes=ptr(0)),
    lambda g: g.appearance['_structures'][1].update(FaceRules=[]),
    lambda g: g.objects[62].read_typetree()['m_Children'].append(ptr(72)),
    lambda g: g.objects[72].read_typetree().update(m_Father=ptr(0)),
    lambda g: g.objects[72].read_typetree()['m_Children'].append(ptr(62)),
    lambda g: g.objects[71].read_typetree()['m_Component'].remove({'component': ptr(75)}),
    lambda g: g.objects[74].read_typetree().update(m_RenderMode=4),
    lambda g: g.objects[74].read_typetree().update(m_Materials=[]),
    lambda g: g.objects[74].read_typetree().update(m_Enabled=2),
    lambda g: g.objects[61].read_typetree().update(m_IsActive='yes'),
    lambda g: g.objects[31].read_typetree()['m_Component'].remove({'component': ptr(34)}),
    lambda g: g.assets._objects.append(g.objects[36]),
])
def test_malformed_ambiguous_or_unsupported_graph_fails_closed(graph, change):
    change(graph)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('field', ['m_PathID', 'm_FileID'])
@pytest.mark.parametrize('value', [True, False, 1.0, '1', None, -1])
def test_active_graph_pptrs_are_exact_nonnegative_local_integers(graph, field, value):
    graph.objects[35].read_typetree()['m_Mesh'][field] = value
    with pytest.raises(ValueError):
        collect(graph)


def test_external_active_reference_is_refused_but_particle_mesh_is_inert(graph):
    graph.objects[35].read_typetree()['m_Mesh']['m_FileID'] = 2
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('budget', ['MAX_OBJECTS', 'MAX_NODES', 'MAX_DEPTH', 'MAX_COMPONENTS',
                                   'MAX_BONES', 'MAX_VERTICES', 'MAX_INDICES'])
def test_declared_work_budgets_are_hard_refusals(graph, monkeypatch, budget):
    module = importlib.import_module('bridge.content.sr2_mesh_scope')
    monkeypatch.setattr(module, budget, 1 if budget != 'MAX_DEPTH' else 0)
    with pytest.raises(ValueError, match='budget|oversized'):
        collect(graph)


@pytest.mark.parametrize('field,value', [
    ('vertices', []), ('triangles', [[]]), ('triangles', [[0, 1, 3]]),
    ('triangles', [[0, 1, True]]), ('triangles', [[0, 1, 2, 0]]),
    ('vertices', [[0, 0, 0], [1, 0, 0], [0, float('inf'), 0]]),
    ('bone_weights', []), ('bind_poses', []),
])
def test_bad_decoded_geometry_is_never_relabelled_as_particle_exclusion(graph, monkeypatch, field, value):
    from bridge.content import sr2_asset_extract
    def decode(mesh, mesh_obj=None):
        result = graph.decode(mesh, mesh_obj)
        if mesh_obj.path_id == 15:
            result[field] = value
        return result
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)


@pytest.mark.parametrize('change', [
    lambda t: t['m_VertexData'].update(m_VertexCount=100_001),
    lambda t: t['m_VertexData'].update(m_VertexCount=True),
    lambda t: t['m_VertexData'].update(m_VertexCount=0),
    lambda t: t['m_VertexData'].update(m_Channels=[]),
    lambda t: t['m_VertexData'].update(m_Channels=[{}] * 17),
    lambda t: t.update(m_MeshCompression=1),
    lambda t: t.update(m_MeshCompression=True),
    lambda t: t.update(m_IndexFormat=2),
    lambda t: t.update(m_IndexFormat=1),
    lambda t: t.update(m_IndexFormat=True),
    lambda t: t.update(m_IndexBuffer=b''),
    lambda t: t.update(m_SubMeshes=[]),
    lambda t: t['m_SubMeshes'][0].update(firstByte=2),
    lambda t: t['m_SubMeshes'][0].update(indexCount=600_003),
    lambda t: t['m_SubMeshes'][0].update(indexCount=4),
    lambda t: t['m_SubMeshes'][0].update(indexCount=True),
    lambda t: t['m_SubMeshes'][0].update(topology=2),
    lambda t: t['m_SubMeshes'][0].update(baseVertex=1),
    lambda t: t['m_SubMeshes'][0].update(vertexCount=4),
    lambda t: t['m_SubMeshes'][0].update(firstVertex=-1),
    lambda t: t['m_StreamData'].update(size=1),
    lambda t: t['m_StreamData'].update(path='external'),
    lambda t: t['m_StreamData'].update(offset=True),
])
def test_source_mesh_metadata_refused_before_geometry_decoder(graph, monkeypatch, change):
    from bridge.content import sr2_asset_extract
    change(graph.objects[15].read_typetree())
    called = []
    def decode(*args):
        called.append(True)
        return graph.decode(*args)
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)
    assert called == []


@pytest.mark.parametrize('channel,size', [('normals', 3), ('uv', 2), ('uv2', 2), ('colors', 4), ('tangents', 4)])
def test_bad_optional_geometry_channels_are_refused(graph, monkeypatch, channel, size):
    from bridge.content import sr2_asset_extract
    def decode(mesh, mesh_obj):
        result = graph.decode(mesh, mesh_obj)
        result[channel] = [[float('nan')] * size] * 3
        return result
    monkeypatch.setattr(sr2_asset_extract, 'decode_mesh', decode)
    with pytest.raises(ValueError):
        collect(graph)


def test_source_binding_provenance_and_root_visual_ownership(graph):
    body = collect(graph)['parts'][0]['appearance_binding']
    assert body['source_parent_bone'] == 0
    assert body['source_root_bone'] == 1
    assert body['source_attached_bones'] == [1]
    graph.objects[6].read_typetree()['m_Father'] = ptr(62)
    with pytest.raises(ValueError):
        collect(graph)
