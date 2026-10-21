"""Serialized appearance binding wins over indistinguishable rest matrices."""
import sys
import zlib
from types import ModuleType, SimpleNamespace
import pytest
from bridge.content.sr2_appearance import collect_source_appearance, resolve_part_binding

_IDENTITY = [1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0]


def _ptr(pid):
    return {'m_FileID': 0, 'm_PathID': pid}


def _skin_mesh(*hashes):
    return {'m_BindPose': [list(_IDENTITY) for _ in hashes], 'm_BoneNameHashes': list(hashes)}


def _skin_bones(*spec):
    def bone(name, parent):
        return {'name': name, 'parent': parent, 'p': [0, 0, 0], 'q': [0, 0, 0, 1], 's': [1, 1, 1]}
    bones = [bone('bone_root', -1)]
    index = {'bone_root': 0}
    for entry in spec:
        name, parent = entry if isinstance(entry, tuple) else (entry, 'bone_root')
        bones.append(bone(name, index[parent]))
        index[name] = len(bones) - 1
    return bones


def _path_hash(*names):
    return zlib.crc32('/'.join(names).encode('utf-8'))


@pytest.fixture
def source_face(monkeypatch, tmp_path):
    """Nonproprietary object graph; prefab material and unused rows are decoys."""
    objects = {}

    def add(pid, kind, tree, class_name=None):
        obj = SimpleNamespace(path_id=pid, type=SimpleNamespace(name=kind),
                              assets_file=SimpleNamespace(unity_version='synthetic'))
        obj.read_typetree = lambda **kwargs: tree
        obj.read = lambda: SimpleNamespace(**tree)
        script = SimpleNamespace(read=lambda: SimpleNamespace(m_ClassName=class_name))
        obj.parse_monobehaviour_head = lambda: SimpleNamespace(
            m_Script=SimpleNamespace(deref=lambda: script))
        objects[pid] = obj
        return tree

    def go(pid, name, components):
        return add(pid, 'GameObject', {'m_Name': name, 'm_Component': [
            {'component': _ptr(c)} for c in components]})

    def transform(pid, go=None, father=0, children=()):
        tree = {'m_Father': _ptr(father),
                'm_Children': [_ptr(c) for c in children],
                'm_LocalPosition': dict(x=0, y=0, z=0),
                'm_LocalRotation': dict(x=0, y=0, z=0, w=1),
                'm_LocalScale': dict(x=1, y=1, z=1)}
        if go is not None:
            tree['m_GameObject'] = _ptr(go)
        return add(pid, 'Transform', tree)

    go(1, 'slimeExample', [2, 3])
    transform(2, go=1, children=(51,))
    applicator = add(3, 'MonoBehaviour', {
        'SlimeDefinition': _ptr(4), 'Appearance': _ptr(0), 'SlimeExpression': 7,
        'Bones': [{'Bone': 1, 'BoneObject': _ptr(9)},
                  {'Bone': 2, 'BoneObject': _ptr(55)}],
        'RootAppearanceObject': _ptr(5)}, 'SlimeAppearanceApplicator')
    definition = add(4, 'MonoBehaviour', {'m_Name': 'Example',
        'prefab': _ptr(1), 'AppearancesDefault': [_ptr(7)]}, 'SlimeDefinition')
    go(5, 'Appearance', [6])
    transform(6)
    structure = {'DefaultMaterials': [_ptr(40)], 'Element': _ptr(10),
        'ElementMaterials': [{'OverrideDefaults': 0, 'Materials': []}],
        'SupportsFaces': 1, 'FaceRules': [{'ShowEyes': 1, 'ShowMouth': 1}]}
    appearance = add(7, 'MonoBehaviour', {'_face': _ptr(8), '_structures': [structure]},
                     'SlimeAppearance')
    face = add(8, 'MonoBehaviour', {'ExpressionFaces': [
        {'SlimeExpression': 6, 'Eyes': _ptr(44), 'Mouth': _ptr(45)},
        {'SlimeExpression': 7, 'Eyes': _ptr(41), 'Mouth': _ptr(42)}]}, 'SlimeFace')
    go(9, 'bone_skin_example', [52])
    # The real skeleton chain: bone_root hosts jiggle parents and their skin children.
    go(50, 'bone_root', [51])
    transform(51, go=50, father=2, children=(52, 54))
    transform(52, go=9, father=51)
    go(55, 'bone_jiggle_example', [54])
    transform(54, go=55, father=51, children=(56,))
    go(57, 'bone_skin_child', [56])
    transform(56, go=57, father=54)
    element = add(10, 'MonoBehaviour', {'Prefabs': [_ptr(13)]}, 'SlimeAppearanceElement')
    go(11, 'synthetic_face_LOD0', [12, 13, 14])
    transform(12, go=11)
    add(13, 'MonoBehaviour', {'m_GameObject': _ptr(11), 'ParentBone': 0,
        'RootBone': 1, 'AttachedBones': [1]}, 'SlimeAppearanceObject')
    renderer = add(14, 'SkinnedMeshRenderer', {'m_GameObject': _ptr(11),
        'm_Mesh': _ptr(20), 'm_Materials': [_ptr(43)]})
    add(20, 'Mesh', _skin_mesh(_path_hash('bone_root', 'bone_skin_example')))
    go(60, 'synthetic_tail_LOD0', [61, 62, 63])
    transform(61, go=60)
    add(62, 'MonoBehaviour', {'m_GameObject': _ptr(60), 'ParentBone': 0,
        'RootBone': 2, 'AttachedBones': [1, 2]}, 'SlimeAppearanceObject')
    add(63, 'SkinnedMeshRenderer', {'m_GameObject': _ptr(60),
        'm_Mesh': _ptr(64), 'm_Materials': [_ptr(43)]})
    add(64, 'Mesh', _skin_mesh(_path_hash('bone_root', 'bone_skin_example'),
        _path_hash('bone_root', 'bone_jiggle_example', 'bone_skin_child')))
    for pid in range(40, 47):
        add(pid, 'Material', {'m_Name': f'material{pid}'})
    # Only the unavoidable schema generator boundary is stubbed; the resolver is real.
    generator_module = ModuleType('UnityPy.helpers.TypeTreeGenerator')
    generator_module.TypeTreeGenerator = lambda version: SimpleNamespace(
        load_local_game=lambda path: None, load_local_dll_folder=lambda path: None,
        get_nodes_up=lambda *args: SimpleNamespace(m_Children=[]))
    monkeypatch.setitem(sys.modules, 'UnityPy.helpers.TypeTreeGenerator', generator_module)
    assets = SimpleNamespace(env=SimpleNamespace(objects=list(objects.values())),
                             _objects=list(objects.values()), data_dir=tmp_path)
    parts = [{'role': 'face', 'mesh_name': 'synthetic_face_LOD0', 'mesh_obj': objects[20]},
             {'role': 'tail', 'mesh_name': 'synthetic_tail_LOD0', 'mesh_obj': objects[64]}]
    return SimpleNamespace(assets=assets, objects=objects, parts=parts, applicator=applicator,
        definition=definition, appearance=appearance, structure=structure, face=face,
        element=element, renderer=renderer)


def test_applied_face_uses_definition_lod0_and_serialized_expression(source_face):
    result = collect_source_appearance(source_face.assets, 'Example', source_face.parts)
    face = result['parts']['synthetic_face_LOD0']
    assert face['source_face'] is True
    assert face['source_face_expression'] == 7
    assert face['source_face_materials'] == [
        {'face_role': 'base-null', 'source_path_id': 40},
        {'face_role': 'eyes', 'source_path_id': 41},
        {'face_role': 'mouth', 'source_path_id': 42}]
    assert face['skin_bones'] == ['bone_skin_example']
    assert face['root_bone'] == 'bone_skin_example'


def test_lod0_override_replaces_defaults_and_face_rules_filter_passes(source_face):
    source_face.structure['ElementMaterials'] = [
        {'OverrideDefaults': 1, 'Materials': [_ptr(46)]},
        {'OverrideDefaults': 1, 'Materials': [_ptr(45)]}]
    source_face.structure['FaceRules'] = [
        {'ShowEyes': 0, 'ShowMouth': 1}, {'ShowEyes': 1, 'ShowMouth': 0}]
    result = collect_source_appearance(source_face.assets, 'Example', source_face.parts)
    assert result['parts']['synthetic_face_LOD0']['source_face_materials'] == [
        {'face_role': 'base-null', 'source_path_id': 46},
        {'face_role': 'mouth', 'source_path_id': 42}]


@pytest.mark.parametrize('change', [
    lambda s: s.definition.update(AppearancesDefault=[]),
    lambda s: s.definition.update(AppearancesDefault=[_ptr(7), _ptr(7)]),
    lambda s: s.applicator.update(SlimeDefinition=_ptr(0)),
    lambda s: s.applicator.update(SlimeDefinition=_ptr(999)),
    lambda s: s.applicator.update(Appearance=_ptr(999)),
    lambda s: s.appearance.update(_face=_ptr(0)),
    lambda s: s.appearance.update(_face={'m_FileID': 2, 'm_PathID': 8}),
    lambda s: s.face['ExpressionFaces'][1].update(Eyes=_ptr(0)),
    lambda s: s.face['ExpressionFaces'][1].update(Mouth=_ptr(999)),
    lambda s: s.face['ExpressionFaces'].append(dict(s.face['ExpressionFaces'][1])),
    lambda s: s.appearance['_structures'].append(dict(s.structure)),
    lambda s: s.structure.update(DefaultMaterials=[]),
    lambda s: s.structure.update(FaceRules=[]),
    lambda s: s.structure.update(ElementMaterials=[]),
    lambda s: s.element.update(Prefabs=[]),
    lambda s: s.structure.pop('SupportsFaces'),
    lambda s: s.structure.update(SupportsFaces=2),
    lambda s: s.structure['FaceRules'][0].update(ShowEyes='yes'),
    lambda s: s.structure['ElementMaterials'][0].update(OverrideDefaults=2),
    lambda s: s.face['ExpressionFaces'][1].update(SlimeExpression=7.0),
    lambda s: s.objects[11].read_typetree()['m_Component'].append({'component': _ptr(999)}),
])
def test_static_face_missing_external_ambiguous_or_invalid_metadata_refuses(source_face, change):
    change(source_face)
    with pytest.raises(ValueError):
        collect_source_appearance(source_face.assets, 'Example', source_face.parts)


@pytest.mark.parametrize('value', [False, True, 7.0, '7', None])
def test_static_face_expression_must_be_exact_integer(source_face, value):
    source_face.applicator['SlimeExpression'] = value
    with pytest.raises(ValueError):
        collect_source_appearance(source_face.assets, 'Example', source_face.parts)


@pytest.mark.parametrize('field', ['m_FileID', 'm_PathID'])
@pytest.mark.parametrize('value', [False, True, 0.0, 41.0, '41', None])
def test_static_face_pptr_identifiers_must_be_exact_integer(source_face, field, value):
    source_face.face['ExpressionFaces'][1]['Eyes'][field] = value
    with pytest.raises(ValueError):
        collect_source_appearance(source_face.assets, 'Example', source_face.parts)


class _SchemaNode:
    def __init__(self, level=0, type_name='MonoBehaviour', name='Base', byte_size=-1,
                 version=1, m_MetaFlag=0):
        self.m_Level, self.m_Type, self.m_Name = level, type_name, name
        self.m_ByteSize, self.m_Version, self.m_MetaFlag = byte_size, version, m_MetaFlag
        self.m_Children = []

    def __eq__(self, other):
        # Real TypeTreeNode compares structurally, so both registries are equal.
        return isinstance(other, _SchemaNode) and self.m_Name == other.m_Name and self.m_Type == other.m_Type


def test_definition_read_removes_duplicate_inherited_registry_not_source_fields(source_face, monkeypatch):
    # Some generated schemas put the inherited managed-reference registry BEFORE
    # derived fields as well as at the end. Unity serializes one final registry.
    node = _SchemaNode()
    child_names = [('string', 'm_Name'), ('ManagedReferencesRegistry', 'references'),
                   ('vector', 'AppearancesDefault'), ('ManagedReferencesRegistry', 'references')]
    node.m_Children = [_SchemaNode(1, kind, name) for kind, name in child_names]
    module = sys.modules['UnityPy.helpers.TypeTreeGenerator']
    module.TypeTreeGenerator = lambda version: SimpleNamespace(
        load_local_game=lambda path: None, get_nodes_up=lambda *args: node)

    def read_definition(nodes=None, **kwargs):
        if nodes is None or [c.m_Name for c in nodes.m_Children] != [
                'm_Name', 'AppearancesDefault', 'references']:
            raise EOFError('duplicate inherited registry read as derived field')
        return source_face.definition

    source_face.objects[4].read_typetree = read_definition
    result = collect_source_appearance(source_face.assets, 'Example', source_face.parts)
    assert result['parts']['synthetic_face_LOD0']['source_face_expression'] == 7
    assert len(node.m_Children) == 4  # never mutate the shared generator schema


def test_source_bone_order_and_root_are_preserved():
    # Both jiggle and skin transforms can have identical rest matrices. The mesh's
    # own ordered bone hashes decide the skin; the enum map names each bone.
    names = {9: 'bone_skin_rig', 7: 'bone_skin_fro', 6: 'bone_skin_bot',
             8: 'bone_skin_lef', 5: 'bone_skin_bac', 10: 'bone_jiggle_top'}
    part = {'ParentBone': 0, 'RootBone': 10, 'AttachedBones': [9, 7, 6, 8, 5, 10]}
    bones = _skin_bones('bone_jiggle_rig', ('bone_skin_rig', 'bone_jiggle_rig'),
                        'bone_jiggle_fro', ('bone_skin_fro', 'bone_jiggle_fro'),
                        'bone_jiggle_bot', ('bone_skin_bot', 'bone_jiggle_bot'),
                        'bone_jiggle_lef', ('bone_skin_lef', 'bone_jiggle_lef'),
                        'bone_jiggle_bac', ('bone_skin_bac', 'bone_jiggle_bac'),
                        'bone_jiggle_top', ('bone_skin_top', 'bone_jiggle_top'))
    data = _skin_mesh(*[_path_hash('bone_root', 'bone_jiggle_' + key, 'bone_skin_' + key)
                        for key in ('rig', 'fro', 'bot', 'lef', 'bac', 'top')])
    result = resolve_part_binding(part, names, data, bones)
    assert result['skin_bones'] == [names[n] for n in [9, 7, 6, 8, 5, 10]]
    assert result['root_bone'] == 'bone_jiggle_top'


def test_tail_uses_source_skin_children():
    names = {22: 'bone_skin_tail_base', 24: 'bone_skin_tail01',
             26: 'bone_skin_tail02', 28: 'bone_skin_tail03'}
    bones = _skin_bones('bone_jiggle_tail_base', ('bone_skin_tail_base', 'bone_jiggle_tail_base'),
                        ('bone_jiggle_tail01', 'bone_skin_tail_base'),
                        ('bone_skin_tail01', 'bone_jiggle_tail01'),
                        ('bone_jiggle_tail02', 'bone_skin_tail01'),
                        ('bone_skin_tail02', 'bone_jiggle_tail02'),
                        ('bone_jiggle_tail03', 'bone_skin_tail02'),
                        ('bone_skin_tail03', 'bone_jiggle_tail03'))
    chain = ['bone_root', 'bone_jiggle_tail_base', 'bone_skin_tail_base']
    data = _skin_mesh(_path_hash(*chain))
    for step in (['bone_jiggle_tail01', 'bone_skin_tail01'],
                 ['bone_jiggle_tail02', 'bone_skin_tail02'],
                 ['bone_jiggle_tail03', 'bone_skin_tail03']):
        chain = chain + step
        data['m_BoneNameHashes'].append(_path_hash(*chain))
        data['m_BindPose'].append(list(_IDENTITY))
    result = resolve_part_binding({'ParentBone': 0, 'RootBone': 22,
                                   'AttachedBones': [22, 24, 26, 28]}, names, data, bones)
    assert result['skin_bones'] == list(names.values())
    assert result['root_bone'] == 'bone_skin_tail_base'


def test_collected_part_binding_is_mesh_authoritative(source_face):
    result = collect_source_appearance(source_face.assets, 'Example', source_face.parts)
    tail = result['parts']['synthetic_tail_LOD0']
    assert tail['skin_bones'] == ['bone_skin_example', 'bone_jiggle_example']
    assert tail['root_bone'] == 'bone_jiggle_example'


@pytest.mark.parametrize('field', ['ParentBone', 'RootBone', 'AttachedBones'])
@pytest.mark.parametrize('value', [False, True, 0.0, 1.0, '1', None])
def test_bone_identifiers_must_be_exact_integers(field, value):
    part = {'ParentBone': 0, 'RootBone': 1, 'AttachedBones': [1]}
    part[field] = [value] if field == 'AttachedBones' else value
    with pytest.raises(ValueError):
        resolve_part_binding(part, {0: 'bone_root', 1: 'bone_skin_rig'},
                             _skin_mesh(_path_hash('bone_root', 'bone_skin_rig')),
                             _skin_bones('bone_skin_rig'))


@pytest.mark.parametrize('part', [
    {'ParentBone': 0, 'RootBone': 10, 'AttachedBones': [9]},
    {'ParentBone': 0, 'RootBone': 99, 'AttachedBones': [9]},
    {'ParentBone': 0, 'RootBone': 10, 'AttachedBones': [99]},
    {'ParentBone': 4, 'RootBone': 10, 'AttachedBones': [9]},
    {'ParentBone': 0, 'RootBone': 10},
])
def test_bad_or_unsupported_mapping_is_not_guessed(part):
    bones = _skin_bones('bone_jiggle_rig', ('bone_skin_rig', 'bone_jiggle_rig'),
                        'bone_jiggle_fro', ('bone_skin_fro', 'bone_jiggle_fro'))
    data = _skin_mesh(_path_hash('bone_root', 'bone_jiggle_rig', 'bone_skin_rig'),
                      _path_hash('bone_root', 'bone_jiggle_fro', 'bone_skin_fro'))
    with pytest.raises(ValueError):
        resolve_part_binding(part, {9: 'bone_skin_rig', 10: 'bone_jiggle_top'}, data, bones)
