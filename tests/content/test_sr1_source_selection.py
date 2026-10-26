"""Synthetic applied appearance references, deliberately not source names."""
from types import SimpleNamespace
import pytest

from bridge.content.sr1_asset_extract import Sr1Assets, collect_parts


def pointer(pid):
    return {'m_FileID': 0, 'm_PathID': pid}


def assets_fixture():
    objects = {}
    def obj(pid, kind, tree, cls=''):
        o = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind), tree=tree,
                            class_name=cls, assets_file=SimpleNamespace(name='synthetic'))
        objects[pid] = o
        return o
    appearance = obj(1, 'MonoBehaviour', {'Structures': [{
        'Element': pointer(2), 'DefaultMaterials': [pointer(8)],
        'ElementMaterials': [{'OverrideDefaults': 0, 'Materials': []}],
        'SupportsFaces': 1, 'FaceRules': [{'ShowEyes': 1, 'ShowMouth': 1}]}],
        'Face': pointer(3)}, 'SlimeAppearance')
    obj(2, 'MonoBehaviour', {'m_Name': 'SyntheticBody', 'Prefabs': [pointer(4), pointer(11)]}, 'SlimeAppearanceElement')
    obj(3, 'MonoBehaviour', {'ExpressionFaces': [{'SlimeExpression': 7, 'Eyes': pointer(9), 'Mouth': pointer(10)}]}, 'SlimeFace')
    obj(4, 'MonoBehaviour', {'m_GameObject': pointer(5), 'LODIndex': 0, 'IgnoreLODIndex': 0}, 'SlimeAppearanceObject')
    obj(5, 'GameObject', {'m_Component': [{'component': pointer(6)}]})
    obj(6, 'SkinnedMeshRenderer', {'m_Mesh': pointer(100)})
    applicator = obj(7, 'MonoBehaviour', {'SlimeExpression': 7}, 'SlimeAppearanceApplicator')
    for pid in (8, 9, 10):
        obj(pid, 'Material', {'m_Name': 'arbitrary-name-' + str(pid)})
    obj(11, 'MonoBehaviour', {'m_GameObject': pointer(12), 'LODIndex': 1, 'IgnoreLODIndex': 0}, 'SlimeAppearanceObject')
    assets = Sr1Assets.__new__(Sr1Assets)
    assets.tree = lambda o: o.tree
    assets.class_name = lambda o: o.class_name
    def follow(owner, p, kind=None, cls=None):
        o = objects[p['m_PathID']]
        assert kind is None or kind == o.type.name
        assert cls is None or cls == o.class_name
        return o
    assets.follow = follow
    return assets, dict(appearance=appearance, applicator=applicator), objects


def test_lod0_and_face_passes_are_selected_by_applied_references():
    assets, resolved, objects = assets_fixture()
    rows = collect_parts(assets, resolved)
    assert len(rows) == 1
    assert rows[0]['part'] is objects[4]
    assert [(m.path_id, role) for m, role in rows[0]['materials']] == [(8, None), (9, 'eyes'), (10, 'mouth')]


def test_element_override_wins_without_changing_face_passes():
    assets, resolved, objects = assets_fixture()
    structure = objects[1].tree['Structures'][0]
    structure['ElementMaterials'][0] = {'OverrideDefaults': 1, 'Materials': [pointer(10)]}
    rows = collect_parts(assets, resolved)
    assert [(m.path_id, role) for m, role in rows[0]['materials']] == [(10, None), (9, 'eyes'), (10, 'mouth')]


@pytest.mark.parametrize('mutate', [
    lambda o: o[11].tree.update(LODIndex=0),
    lambda o: o[7].tree.update(SlimeExpression=999),
    lambda o: o[5].tree['m_Component'].append({'component': pointer(6)}),
])
def test_ambiguous_lod_face_or_renderer_fails_closed(mutate):
    assets, resolved, objects = assets_fixture()
    mutate(objects)
    with pytest.raises(ValueError):
        collect_parts(assets, resolved)
