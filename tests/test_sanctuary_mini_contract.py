"""Source-bound policy guards, NOT a replacement for Unity pose/bounds or visual acceptance."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_minis_enumerate_lod0_static_accessories_and_native_bone_attachments():
    code=(ROOT/'mods/sr1-bridge/SanctuaryMiniVisual.cs').read_text()
    assert 'prefab.IgnoreLODIndex || prefab.LODIndex == 0' in code
    assert 'GetComponentsInChildren<MeshRenderer>(true)' in code
    assert 'GetComponentsInChildren<SkinnedMeshRenderer>(true)' in code
    assert 'prefab.ParentBone' in code and 'mapping.BoneObject' in code
    assert 'structure.ElementMaterials[objectIndex]' in code and 'structure.FaceRules[objectIndex]' in code
    assert 'Object.Instantiate' not in code, 'Never clone a native gameplay actor to draw a badge'


def test_minis_size_in_own_local_solid_mesh_space_not_world_aabb():
    code=(ROOT/'mods/sr1-bridge/SanctuaryMiniVisual.cs').read_text()
    assert 'worldToLocalMatrix *' in code
    assert 'localToWorldMatrix' in code
    assert 'mesh.bounds' in code
    assert 'SizeReference' in code
    assert 'renderer.bounds' not in code
    builder=(ROOT/'mods/sr1-bridge/SanctuaryBuilder.cs').read_text()
    assert 'SanctuaryMiniVisual.Create' in builder
    assert 'SanctuaryMiniVisual.Fit' in builder


def test_retired_puzzle_is_rechecked_and_persisted_on_load_before_view():
    code=(ROOT/'mods/sr1-bridge/SanctuaryHost.cs').read_text()
    load=code.split('private void Build()',1)[1].split('private void WireExtension()',1)[0]
    assert load.index('EnsureManifest') < load.index('TryUnlockPortal') < load.index('Persist()') < load.index('ApplyProgress(false)')


def test_sanctuary_statue_labels_follow_active_game_language_live():
    code=(ROOT/'mods/sr1-bridge/SanctuaryBuilder.cs').read_text(encoding='utf-8')
    assert 'class SanctuaryStatueLabel' in code
    assert 'RegisterBundlesListener(OnBundlesAvailable)' in code
    assert 'UnregisterBundlesListener(OnBundlesAvailable)' in code
    assert 'GetCultureLang()' in code
    assert 'ForceMeshUpdate()' in code
    assert 'GetResourceString("l." + lower + "_slime", false)' in code
    assert 'label.AddComponent<SanctuaryStatueLabel>().Bind(' in code


def test_travel_text_uses_active_game_language_not_os_language_only():
    overlay=(ROOT/'mods/shared/TravelOverlay.cs').read_text(encoding='utf-8')
    assert 'internal static bool IsFrench()' in overlay
    assert 'dir.GetCultureLang() == MessageDirector.Lang.FR' in overlay
    assert 'LocalizationSettings.SelectedLocale' in overlay
    portal=(ROOT/'mods/sr2-bridge/Sr2ReturnPortal.cs').read_text(encoding='utf-8')
    assert 'Application.systemLanguage' not in portal
    assert 'TravelText.IsFrench()' in portal
