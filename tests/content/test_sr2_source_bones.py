"""Bone hierarchy lookup must remain bound to original serialized-file objects."""
from types import SimpleNamespace
from bridge.content.sr2_asset_extract import collect_bone_tree


def test_bone_lookup_ignores_lazily_loaded_dependency_path_id_collisions():
    def obj(pid, kind, tree):
        data = SimpleNamespace(**tree)
        return SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
                               read=lambda: data, read_typetree=lambda: tree)
    ptr = lambda pid: {'m_FileID': 0, 'm_PathID': pid}
    root = obj(1, 'GameObject', {'m_Name': 'slimeExample', 'm_Component': [{'component': ptr(2)}]})
    root_t = obj(2, 'Transform', {'m_GameObject': ptr(1), 'm_Children': [ptr(4)]})
    bone_go = obj(3, 'GameObject', {'m_Name': 'bone_root'})
    bone_t = obj(4, 'Transform', {'m_GameObject': ptr(3), 'm_Children': [],
        'm_LocalPosition': dict(x=1.,y=2.,z=3.), 'm_LocalRotation': dict(x=0.,y=0.,z=0.,w=1.),
        'm_LocalScale': dict(x=1.,y=1.,z=1.)})
    foreign = obj(4, 'Transform', {'m_GameObject': ptr(3), 'm_Children': [],
        'm_LocalPosition': dict(x=999.,y=999.,z=999.), 'm_LocalRotation': dict(x=0.,y=0.,z=0.,w=1.),
        'm_LocalScale': dict(x=1.,y=1.,z=1.)})
    originals = [root, root_t, bone_go, bone_t]
    assets = SimpleNamespace(_objects=originals, env=SimpleNamespace(objects=[*originals,foreign]))
    bones = collect_bone_tree(assets, 'Example')
    assert bones[0]['name'] == 'bone_root'
    assert bones[0]['p'] == [1.,2.,3.]
