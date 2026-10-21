"""Nonproprietary graph/decoder contract for the source-cache integrator."""
from types import SimpleNamespace
import zlib
import pytest


def ptr(pid):
    return {'m_FileID': 0, 'm_PathID': pid}


@pytest.fixture
def graph(monkeypatch):
    from bridge.content import sr2_appearance
    monkeypatch.setattr(sr2_appearance, '_definition_nodes', lambda generator: None)
    objects = {}
    trees = {
        1: ('GameObject', {'m_Name': 'slimeExample', 'm_Component': [{'component': ptr(2)}]}),
        2: ('MonoBehaviour', {'SlimeDefinition': ptr(3), 'Appearance': ptr(0), 'RootAppearanceObject': ptr(5)}),
        3: ('MonoBehaviour', {'prefab': ptr(1), 'AppearancesDefault': [ptr(4)]}),
        4: ('MonoBehaviour', {'_icon': ptr(9), '_idleAnimationOverride': ptr(8),
                             '_biteAnimationOverride': ptr(0), '_wingFlapAnimationOverride': ptr(0)}),
        5: ('GameObject', {'m_Component': [{'component': ptr(6)}]}),
        6: ('Animator', {'m_GameObject': ptr(5), 'm_Controller': ptr(7)}),
        7: ('AnimatorController', {'m_Name': 'SyntheticController', 'm_AnimationClips': [ptr(10)]}),
        9: ('Sprite', {}),
    }
    classes = {2: 'SlimeAppearanceApplicator', 3: 'SlimeDefinition', 4: 'SlimeAppearance'}
    for pid in (8, 10, 11):
        path = 'bone_root/skin'
        clip = {'m_Name': f'clip-{pid}', 'm_SampleRate': 1,
                'm_ClipBindingConstant': {'genericBindings': [{'typeID': 4, 'customType': 0,
                    'isPPtrCurve': 0, 'attribute': 1, 'path': zlib.crc32(path.encode())}]},
                'm_MuscleClip': {'m_StartTime': 0, 'm_StopTime': 1, 'm_LoopTime': True,
                    'm_Clip': {'data': {'m_StreamedClip': {'curveCount': 0, 'data': []},
                        'm_DenseClip': {'m_CurveCount': 0, 'm_FrameCount': 0, 'm_BeginTime': 0,
                            'm_SampleRate': 0, 'm_SampleArray': []},
                        'm_ConstantClip': {'data': [1, 2, 3]}}}}}
        trees[pid] = ('AnimationClip', clip)
    for pid, (kind, tree) in trees.items():
        obj = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
            read_typetree=lambda pid=pid, **kwargs: trees[pid][1],
            get_raw_data=lambda pid=pid: str(trees[pid]).encode())
        if pid in classes:
            script = SimpleNamespace(m_ClassName=classes[pid])
            obj.parse_monobehaviour_head = lambda script=script: SimpleNamespace(
                m_Script=SimpleNamespace(deref=lambda: SimpleNamespace(read=lambda: script)))
        objects[pid] = obj
    return SimpleNamespace(_objects=list(objects.values()), env=SimpleNamespace(typetree_generator=object()),
        objects=objects, trees=trees)


def test_declared_parts_refuse_name_guesses_and_resolve_authored_materials(graph):
    from bridge.content import sr2_source_cache as source
    graph.trees[4][1]['_structures'] = [{
        'Element': ptr(12), 'SupportsFaces': 0, 'DefaultMaterials': [ptr(16), ptr(17)],
        'ElementMaterials': [{'OverrideDefaults': 0, 'Materials': []}]}]
    for pid, kind, tree in [
        (12, 'MonoBehaviour', {'Prefabs': [ptr(13)]}),
        (13, 'MonoBehaviour', {'m_GameObject': ptr(14)}),
        (14, 'GameObject', {'m_Component': [{'component': ptr(15)}]}),
        (15, 'SkinnedMeshRenderer', {'m_Mesh': ptr(18)}),
        (16, 'Material', {'m_Name': 'Actual base'}),
        (17, 'Material', {'m_Name': 'Actual lure'}),
        (18, 'Mesh', {'m_Name': 'synthetic_LOD0'}),
    ]:
        graph.trees[pid] = (kind, tree)
        obj = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
            read_typetree=lambda tree=tree: tree, read=lambda tree=tree: SimpleNamespace(m_Name=tree.get('m_Name')))
        graph._objects.append(obj); graph.objects[pid] = obj
    parts = [{'role': 'bait', 'mesh_name': 'synthetic_LOD0', 'mesh_obj': graph.objects[18]},
             {'role': 'unapplied', 'mesh_name': 'name_guess', 'mesh_obj': SimpleNamespace(path_id=999)}]
    result = source.collect_declared_parts(graph, {'appearance_id': 4}, parts)
    assert len(result) == 1 and result[0]['mesh_name'] == 'synthetic_LOD0'
    assert [name for name, _ in result[0]['source_material_slots']] == ['Actual base', 'Actual lure']
    assert result[0]['source_material_ids'] == [16, 17]


@pytest.mark.parametrize('pid,key,value', [
    (4, '_icon', {'m_FileID': 1, 'm_PathID': 9}),
    (4, '_icon', {'m_FileID': 0, 'm_PathID': True}),
    (3, 'AppearancesDefault', [ptr(4), ptr(4)]),
    (2, 'Appearance', ptr(11)),
    (6, 'm_Controller', ptr(11)),
    (4, '_idleAnimationOverride', ptr(9)),
])
def test_declared_source_graph_fails_closed(graph, pid, key, value):
    from bridge.content import sr2_source_cache as source
    graph.trees[pid][1][key] = value
    with pytest.raises(ValueError):
        source.resolve_species_source(graph, 'Example')


@pytest.mark.parametrize('ids,default,bones', [
    ([11], 'clip-11', [{'name': 'bone_root', 'parent': -1}, {'name': 'skin', 'parent': 0}]),
    ([8, 8], 'clip-8', [{'name': 'bone_root', 'parent': -1}]),
    ([True], 'clip-8', [{'name': 'bone_root', 'parent': -1}]),
    ([8], 'guessed idle', [{'name': 'bone_root', 'parent': -1}, {'name': 'skin', 'parent': 0}]),
    ([8], 'clip-8', [{'name': 'bone_root', 'parent': -1}]),
    ([8], 'clip-8', [{'name': 'bone_root', 'parent': -1}, {'name': 'skin', 'parent': 1}]),
    ([8], 'clip-8', [{'name': 'bone_root', 'parent': -1}, {'name': 'skin/path', 'parent': 0}]),
    ([8], 'clip-8', [{'name': 'bone_root', 'parent': -1}, {'name': 'bone_root', 'parent': -1}]),
])
def test_explicit_clip_selection_fails_closed(graph, ids, default, bones):
    from bridge.content import sr2_source_cache as source
    refs = source.resolve_species_source(graph, 'Example')
    with pytest.raises(ValueError):
        source.extract_source_animation(graph, refs, bones, ids, default)


def test_declared_source_animation_rejects_unsupported_curve_type(graph):
    from bridge.content import sr2_source_cache as source
    refs = source.resolve_species_source(graph, 'Example')
    graph.trees[8][1]['m_ClipBindingConstant']['genericBindings'][0]['typeID'] = 137
    with pytest.raises(ValueError):
        source.extract_source_animation(graph, refs,
            [{'name': 'bone_root', 'parent': -1}, {'name': 'skin', 'parent': 0}], [8], 'clip-8')


def test_species_refs_resolve_own_icon_and_explicit_source_clips(graph):
    from bridge.content import sr2_source_cache as source
    resolved = source.resolve_species_source(graph, 'Example')
    assert resolved['appearance_id'] == 4
    assert resolved['sprite_id'] == 9
    assert resolved['animator_id'] == 6
    assert resolved['controller_id'] == 7
    assert resolved['clip_ids'] == [8, 10]
    bones = [{'name': 'bone_root', 'parent': -1}, {'name': 'skin', 'parent': 0}]
    animation, provenance = source.extract_source_animation(graph, resolved, bones, [8, 10], 'clip-8')
    assert animation['default_clip'] == 'clip-8'
    assert [c['name'] for c in animation['clips']] == ['clip-8', 'clip-10']
    assert animation['clips'][0]['channels'][0]['values'] == [[1., 2., 3.], [1., 2., 3.]]
    assert provenance['selection'] == 'explicit-source-clip-ids'
    assert provenance['runtime_graph_reproduced'] is False
    assert [c['source_path_id'] for c in provenance['clips']] == [8, 10]
