"""Installation self-check (run by scripts/install.ps1): ``python -m apps.launcher.selfcheck``.

Exit code 0 when the launcher, BridgeHost and the extractor can run; missing prebuilt plugins are only a warning
(a source checkout builds them itself, a release zip ships them in ``plugins/``).
"""
import importlib
import sys
from pathlib import Path

from apps.launcher import extract, plugins

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = (('webview', 'pywebview (launcher window)'), ('UnityPy', 'UnityPy (reads your game files)'),
           ('TypeTreeGeneratorAPI', 'TypeTreeGeneratorAPI'), ('dnfile', 'dnfile'), ('numpy', 'numpy'),
           ('PIL', 'Pillow'), ('pygltflib', 'pygltflib'), ('texture2ddecoder', 'texture2ddecoder'), ('yaml', 'PyYAML'),
           ('bridge.host.server', 'BridgeHost'), ('bridge.multiplayer.server', 'multiplayer server core'),
           ('apps.launcher.desktop', 'launcher backend'))


def run():
    ok = True
    if sys.version_info[:2] != (3, 12):
        print(f'  [WARN] Python {sys.version_info[0]}.{sys.version_info[1]}: SlimeBridge is tested with 3.12')
    for name, label in MODULES:
        try:
            importlib.import_module(name)
            print(f'  [OK]   {label}')
        except Exception as exc:  # any import failure makes the install unusable
            ok = False
            print(f'  [FAIL] {label}: {type(exc).__name__}: {exc}')
    tool = extract.locate_tool()
    if tool['found']:
        print(f'  [OK]   extractor (SlimeBridge-AssetConverter) in {tool["path"]}')
    else:
        ok = False
        print(f'  [FAIL] extractor: {tool["reason"]}')
    for realm in ('SR1', 'SR2'):
        source = plugins.find_source(realm)
        if source:
            print(f'  [OK]   {realm} plugin ready to install ({source})')
        else:
            print(f'  [WARN] no prebuilt {realm} plugin: use the release zip, or build it (py -3.12 build_all.py --plugins-only)')
    try:
        lab = REPO_ROOT / 'Lab' / 'Launcher'
        lab.mkdir(parents=True, exist_ok=True)
        probe = lab / '.write-test'
        probe.write_text('ok', encoding='utf-8')
        probe.unlink()
        print(f'  [OK]   settings folder writable ({lab})')
    except OSError as exc:
        ok = False
        print(f'  [FAIL] cannot write the settings folder: {exc} (do not install SlimeBridge under Program Files)')
    return ok


if __name__ == '__main__':
    sys.exit(0 if run() else 1)
