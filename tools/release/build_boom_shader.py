"""Build only SlimeBridge's authored shader; no game/extracted textures enter this bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
UNITY_VERSION = '2019.4.29f1'
DEFAULT_EDITOR = Path('C:/Program Files/Unity/Hub/Editor') / UNITY_VERSION / 'Editor/Unity.exe'


def prepare_project(project):
    project = Path(project)
    assets = project / 'Assets'
    (assets / 'Editor').mkdir(parents=True, exist_ok=True)
    (project / 'ProjectSettings').mkdir(exist_ok=True)
    (project / 'Packages').mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'mods/sr1-bridge/Shaders/ImportedBoom.shader', assets / 'ImportedBoom.shader')
    shutil.copy2(ROOT / 'tools/release/unity/BuildBoomShader.cs', assets / 'Editor/BuildBoomShader.cs')
    (project / 'ProjectSettings/ProjectVersion.txt').write_text('m_EditorVersion: ' + UNITY_VERSION + '\n', encoding='utf-8')
    (project / 'Packages/manifest.json').write_text(json.dumps({
        'dependencies': {'com.unity.modules.assetbundle': '1.0.0'},
    }, indent=2), encoding='utf-8')
    return project


def validate_bundle(bundle):
    import UnityPy

    if not any(obj.type.name == 'AssetBundle' for obj in UnityPy.load(str(bundle)).objects):
        raise RuntimeError('Authored shader bundle has no AssetBundle root; check the built-in AssetBundle module')


def build(editor=None):
    editor = Path(editor or os.environ.get('SLIMEBRIDGE_UNITY_EDITOR') or DEFAULT_EDITOR).resolve()
    if not editor.is_file():
        raise RuntimeError('Unity ' + UNITY_VERSION + ' is required for the own-shader release build; set SLIMEBRIDGE_UNITY_EDITOR')
    project = prepare_project(ROOT / 'build/boom-shader-2019')
    output = project / 'Output'
    receipt_path = output / 'BUILD-RECEIPT.json'
    # A previous successful receipt must not authenticate an incomplete current build.
    receipt_path.unlink(missing_ok=True)
    log = project / 'unity-build.log'
    proc = subprocess.run([str(editor), '-batchmode', '-nographics', '-quit', '-projectPath', str(project),
                           '-executeMethod', 'BuildBoomShader.Run', '-logFile', str(log)], timeout=300, check=False)
    if proc.returncode != 0 or not receipt_path.is_file():
        raise RuntimeError('Authored shader build failed; see ' + str(log))
    receipt = json.loads(receipt_path.read_text(encoding='utf-8-sig'))
    if (receipt.get('unity') != UNITY_VERSION or receipt.get('shader') != 'SlimeBridge/ImportedBoom' or
            receipt.get('target') != 'StandaloneWindows64' or receipt.get('shaderErrors') is not False):
        raise RuntimeError('Unexpected authored shader build receipt')
    bundle = output / 'slimebridge-boom'
    if not bundle.is_file() or not 0 < bundle.stat().st_size <= 4194304:
        raise RuntimeError('Authored shader bundle is missing or outside its runtime size budget')
    validate_bundle(bundle)
    receipt['bundle_sha256'] = hashlib.sha256(bundle.read_bytes()).hexdigest()
    receipt['source_sha256'] = hashlib.sha256((ROOT / 'mods/sr1-bridge/Shaders/ImportedBoom.shader').read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--editor', type=Path)
    args = parser.parse_args()
    print(build(args.editor))


if __name__ == '__main__':
    main()
