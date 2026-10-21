"""Mesh-authoritative skin binding: the part Mesh's own bone arrays are the truth.

Real SR2 values (resources.assets, Unity 2021.3.28f1) are reproduced here as
nonproprietary structure: LF_cotton_tail_LOD0 declares five m_BindPose rows and
five m_BoneNameHashes while SlimeAppearanceObject 37993 lists six AttachedBones;
LF_ringtail_tail_LOD0 carries one authored hash that no longer matches any
transform. mesh.m_BoneNameHashes entries are the CRC32 of the bone transform
path from bone_root (verified against the real files).
"""
import zlib

import pytest

from bridge.content import sr2_asset_extract as extract
from bridge.content.sr2_appearance import resolve_part_binding

IDENTITY = [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def bone(name, parent=-1):
    return {'name': name, 'parent': parent, 'p': [0.0, 0.0, 0.0],
            'q': [0.0, 0.0, 0.0, 1.0], 's': [1.0, 1.0, 1.0]}


def skeleton(*spec):
    """Build [{name, parent}...] rooted at bone_root.

    Each entry is a name (child of bone_root) or (name, parent_name).
    """
    bones = [bone('bone_root')]
    index = {'bone_root': 0}
    for entry in spec:
        name, parent = entry if isinstance(entry, tuple) else (entry, 'bone_root')
        bones.append(bone(name, index[parent]))
        index[name] = len(bones) - 1
    return bones


def path_hash(*names):
    return zlib.crc32('/'.join(names).encode('utf-8'))


def mesh(bindposes, hashes):
    return {'m_BindPose': [list(IDENTITY) for _ in range(bindposes)],
            'm_BoneNameHashes': list(hashes)}


def slime_core_skeleton():
    """The real SR2 slime skin chain: bone_core hosts jiggle parents and skin children."""
    return skeleton(
        'bone_attachment', 'bone_slime', ('bone_core', 'bone_slime'),
        ('bone_jiggle_rig', 'bone_core'), ('bone_skin_rig', 'bone_jiggle_rig'),
        ('bone_jiggle_fro', 'bone_core'), ('bone_skin_fro', 'bone_jiggle_fro'),
        ('bone_jiggle_bot', 'bone_core'), ('bone_skin_bot', 'bone_jiggle_bot'),
        ('bone_jiggle_lef', 'bone_core'), ('bone_skin_lef', 'bone_jiggle_lef'),
        ('bone_jiggle_bac', 'bone_core'), ('bone_skin_bac', 'bone_jiggle_bac'),
        ('bone_jiggle_top', 'bone_core'), ('bone_skin_top', 'bone_jiggle_top'),
        ('bone_jiggle_tail_base', 'bone_core'), ('bone_skin_tail_base', 'bone_jiggle_tail_base'),
        ('bone_jiggle_tail01', 'bone_skin_tail_base'), ('bone_skin_tail01', 'bone_jiggle_tail01'),
        ('bone_jiggle_tail02', 'bone_skin_tail01'), ('bone_skin_tail02', 'bone_jiggle_tail02'),
        ('bone_jiggle_tail03', 'bone_skin_tail02'), ('bone_skin_tail03', 'bone_jiggle_tail03'),
    )


CORE = ['bone_slime', 'bone_core']
RIG = CORE + ['bone_jiggle_rig', 'bone_skin_rig']
FRO = CORE + ['bone_jiggle_fro', 'bone_skin_fro']
BOT = CORE + ['bone_jiggle_bot', 'bone_skin_bot']
LEF = CORE + ['bone_jiggle_lef', 'bone_skin_lef']
BAC = CORE + ['bone_jiggle_bac', 'bone_skin_bac']
TOP = CORE + ['bone_jiggle_top', 'bone_skin_top']
TAIL = CORE + ['bone_jiggle_tail_base', 'bone_skin_tail_base']
TAIL01 = TAIL + ['bone_jiggle_tail01', 'bone_skin_tail01']
TAIL02 = TAIL01 + ['bone_jiggle_tail02', 'bone_skin_tail02']
TAIL03 = TAIL02 + ['bone_jiggle_tail03', 'bone_skin_tail03']

# Real hash values read from the shipped meshes.
REAL = {
    'rig': 2640795618, 'fro': 914979056, 'bot': 1115297637,
    'lef': 415354825, 'bac': 2697714130, 'top': 1738885032,
    'tail_base': 3208266567, 'tail01': 1483861899, 'tail02': 3177758482,
}


def test_mesh_own_bone_order_wins_over_the_appearance_order():
    bones = skeleton('bone_skin_rig', 'bone_skin_fro')
    declared = ['bone_skin_rig', 'bone_skin_fro']
    part = {'ParentBone': 0, 'RootBone': 1, 'AttachedBones': [1, 2]}
    names = {1: 'bone_skin_rig', 2: 'bone_skin_fro'}
    data = mesh(2, [path_hash('bone_root', 'bone_skin_fro'),
                    path_hash('bone_root', 'bone_skin_rig')])
    binding = resolve_part_binding(part, names, data, bones)
    assert binding['skin_bones'] == ['bone_skin_fro', 'bone_skin_rig']
    assert extract.resolve_mesh_skin_bones(data, bones, declared) == \
        ['bone_skin_fro', 'bone_skin_rig']


def test_real_cotton_tail_declares_an_extra_bone_the_mesh_does_not_weight():
    # LF_cotton_tail_LOD0: five mesh bones [rig, fro, bot, lef, top]; the appearance
    # declares six AttachedBones (extra bone_skin_bac), RootBone 10 (jiggle top).
    bones = slime_core_skeleton()
    names = {9: 'bone_skin_rig', 7: 'bone_skin_fro', 6: 'bone_skin_bot',
             8: 'bone_skin_lef', 5: 'bone_skin_bac', 10: 'bone_jiggle_top'}
    part = {'ParentBone': 0, 'RootBone': 10, 'AttachedBones': [9, 7, 6, 8, 5, 10]}
    data = mesh(5, [REAL['rig'], REAL['fro'], REAL['bot'], REAL['lef'], REAL['top']])
    result = resolve_part_binding(part, names, data, bones)
    assert result['skin_bones'] == ['bone_skin_rig', 'bone_skin_fro', 'bone_skin_bot',
                                    'bone_skin_lef', 'bone_jiggle_top']
    assert result['root_bone'] == 'bone_jiggle_top'


def test_skin_child_is_represented_by_its_declared_jiggle_parent():
    # The mesh binds bone_skin_top; the appearance declares only its jiggle parent.
    bones = slime_core_skeleton()
    declared = ['bone_skin_rig', 'bone_jiggle_top']
    data = mesh(2, [REAL['rig'], REAL['top']])
    assert extract.resolve_mesh_skin_bones(data, bones, declared) == \
        ['bone_skin_rig', 'bone_jiggle_top']


def test_collapsed_skin_and_jiggle_binding_is_refused():
    # Two different mesh bones must never collapse onto one declared name.
    bones = slime_core_skeleton()
    declared = ['bone_jiggle_top']
    data = mesh(2, [REAL['top'], path_hash(*TOP)])
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(data, bones, declared)


def test_unresolved_authored_hash_requires_positional_consistency():
    # LF_ringtail_tail_LOD0: the tail03 hash no longer matches any transform. It is
    # accepted only when the declaration is positionally consistent with the rest.
    bones = slime_core_skeleton()
    declared = ['bone_skin_tail_base', 'bone_skin_tail01', 'bone_skin_tail02',
                'bone_skin_tail03']
    data = mesh(4, [REAL['tail_base'], REAL['tail01'], REAL['tail02'], 2853378257])
    assert extract.resolve_mesh_skin_bones(data, bones, declared) == declared

    reordered = ['bone_skin_tail03', 'bone_skin_tail01', 'bone_skin_tail02',
                 'bone_skin_tail_base']
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(data, bones, reordered)


def test_unresolved_hash_is_refused_when_counts_differ():
    # An unresolved authored hash cannot be positioned when the declaration does
    # not have exactly one slot per mesh bone.
    bones = slime_core_skeleton()
    declared = ['bone_skin_tail_base', 'bone_skin_tail01', 'bone_skin_tail02',
                'bone_skin_tail03']
    data = mesh(3, [REAL['tail_base'], REAL['tail01'], 2853378257])
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(data, bones, declared)


def test_mesh_bone_and_bind_pose_arrays_must_be_parallel():
    bones = slime_core_skeleton()
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(
            {'m_BindPose': [list(IDENTITY)], 'm_BoneNameHashes': [REAL['rig'], REAL['fro']]},
            bones, ['bone_skin_rig', 'bone_skin_fro'])


def test_mesh_without_authored_bone_hashes_is_refused():
    bones = slime_core_skeleton()
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones({'m_BindPose': [list(IDENTITY)]},
                                        bones, ['bone_skin_rig'])


def test_bind_pose_must_be_an_invertible_transform():
    bones = slime_core_skeleton()
    collapsed = [0.0] * 16
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(
            {'m_BindPose': [collapsed], 'm_BoneNameHashes': [REAL['rig']]},
            bones, ['bone_skin_rig'])


def test_mesh_bone_the_appearance_never_declares_is_refused():
    bones = slime_core_skeleton()
    data = mesh(1, [REAL['bac']])
    with pytest.raises(ValueError):
        extract.resolve_mesh_skin_bones(data, bones, ['bone_skin_rig', 'bone_skin_fro'])


def test_part_binding_refuses_mesh_bone_the_appearance_does_not_represent():
    bones = slime_core_skeleton()
    part = {'ParentBone': 0, 'RootBone': 10, 'AttachedBones': [9, 10]}
    names = {9: 'bone_skin_rig', 10: 'bone_jiggle_top'}
    with pytest.raises(ValueError):
        resolve_part_binding(part, names, mesh(1, [REAL['bac']]), bones)
