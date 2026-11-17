"""Own shader source/build transport; never reads or packages extracted game assets."""
import importlib
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
RESOURCE = 'SlimeBridge.SR1.ImportedBoom.Windows'


def test_sr1_embeds_only_explicit_own_shader_bundle():
    tree = ET.parse(ROOT / 'mods/sr1-bridge/SlimeBridge.SR1.csproj')
    resources = [node for node in tree.findall('.//EmbeddedResource') if node.get('LogicalName') == RESOURCE]
    assert len(resources) == 1
    assert resources[0].get('Include') == '$(BoomShaderBundle)'
    assert 'Exists' in resources[0].get('Condition', '')


def test_shader_builder_prepares_authored_only_project(tmp_path):
    assert importlib.util.find_spec('tools.release.build_boom_shader') is not None, 'normal own-shader build stage missing'
    builder = importlib.import_module('tools.release.build_boom_shader')
    project = tmp_path / 'project'
    builder.prepare_project(project)
    assert (project / 'Assets/ImportedBoom.shader').read_bytes() == (ROOT / 'mods/sr1-bridge/Shaders/ImportedBoom.shader').read_bytes()
    assert {p.name for p in (project / 'Assets').rglob('*') if p.is_file()} == {'ImportedBoom.shader', 'BuildBoomShader.cs'}
    text = (project / 'ProjectSettings/ProjectVersion.txt').read_text()
    assert '2019.4.29f1' in text
    assert json.loads((project / 'Packages/manifest.json').read_text())['dependencies'] == {'com.unity.modules.assetbundle': '1.0.0'}
    assert not any(project.rglob('*.bc7'))


def test_normal_boom_binding_has_no_lab_file_dependency():
    source = ROOT / 'mods/sr1-bridge/ImportedBoomShader.cs'
    assert source.is_file(), 'normal embedded-resource shader loader missing'
    text = source.read_text()
    assert RESOURCE in text and 'GetManifestResourceStream' in text
    assert 'Environment.GetEnvironmentVariable' not in text
    assert 'FileStream' not in text and 'LabBoomCrackSource' not in text
    binding = (ROOT / 'mods/sr1-bridge/ImportedBoomVisual.cs').read_text()
    assert 'AddComponent<ImportedBoomShader>()' in binding


def test_validator_rejects_missing_bundle_root(monkeypatch, tmp_path):
    import UnityPy
    from tools.release import build_boom_shader

    monkeypatch.setattr(UnityPy, 'load', lambda path: SimpleNamespace(objects=[SimpleNamespace(type=SimpleNamespace(name='Shader'))]))
    with pytest.raises(RuntimeError, match='no AssetBundle root'):
        build_boom_shader.validate_bundle(tmp_path / 'own-shader-without-root')
