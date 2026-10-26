"""Build the SlimeBridge release zip for GitHub Releases.

    py -3.12 -m tools.release.make_release                      build plugins, stage, scan, zip (dist/)
    py -3.12 -m tools.release.make_release --sr1-ref <SR1 game folder> --sr2-ref <SR2 game folder>
    py -3.12 -m tools.release.make_release --publish            ... then create the GitHub release with the gh CLI

The two plugins compile against the game's own assemblies, which are never committed: point --sr1-ref / --sr2-ref at
a Slime Rancher 1 install with SRML and a Slime Rancher 2 install with MelonLoader 0.7 (already run once, so that
MelonLoader/Il2CppAssemblies exists). Default: Lab/Games/SR1 and Lab/Games/SR2.

The zip contains the tracked source (minus tests), the two prebuilt plugins in plugins/SR1 and plugins/SR2 and the
bundled extractor. A scan refuses the release if any game file, extracted asset, 3D model, image or audio file slipped in.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'dist'
EXCLUDED_DIRS = ('tests/', '.github/')
EXCLUDED_FILES = {'requirements-dev.txt'}
# Never in a release: game data, Unity serialized files, extracted/converted assets, models, textures, audio, saves.
FORBIDDEN = re.compile(r'\.(assets|bundle|resS|resource|unity3d|assetbundle|sav|save|glb|gltf|fbx|obj|blend|dds|png|jpe?g|tga|'
                       r'psd|exr|wav|ogg|mp3|bank|sbmesh|exe|zip|7z|rar)$', re.IGNORECASE)
ALLOWED_BINARIES = {'plugins/SR1/SlimeBridge.SR1.dll', 'plugins/SR2/SlimeBridge.SR2.dll', 'plugins/SRMP/SRMP.dll',
                    'plugins/SR1/SlimeBridge.SR1.pdb', 'plugins/SR2/SlimeBridge.SR2.pdb'}
PLUGINS = {
    'SR1': ('mods/sr1-bridge/SlimeBridge.SR1.csproj', 'SR1LabPath', 'net472', ('SlimeBridge.SR1.dll', 'SlimeBridge.SR1.pdb', 'modinfo.json')),
    'SR2': ('mods/sr2-bridge/SlimeBridge.SR2.csproj', 'SR2LabPath', 'net6.0', ('SlimeBridge.SR2.dll', 'SlimeBridge.SR2.pdb')),
}


def die(message):
    print(f'ERROR: {message}')
    sys.exit(1)


def version():
    text = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?', text):
        die(f'VERSION is not semantic: {text!r}')
    modinfo = json.loads((ROOT / 'mods/sr1-bridge/modinfo.json').read_text(encoding='utf-8'))
    if modinfo.get('version') != text.split('-')[0]:
        die(f'mods/sr1-bridge/modinfo.json version {modinfo.get("version")} != VERSION {text}')
    return text


def tracked_files():
    try:
        out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        die('this must run from the SlimeBridge git repository')
    files = [f for f in out.decode('utf-8').split('\0') if f]
    return [f for f in files if not f.startswith(EXCLUDED_DIRS) and f not in EXCLUDED_FILES and (ROOT / f).is_file()]


def dirty():
    out = subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def dotnet():
    env = dict(os.environ)
    home = Path(env.get('DOTNET_ROOT') or Path.home() / '.dotnet')
    if (home / 'dotnet.exe').is_file():
        env['DOTNET_ROOT'] = str(home)
        env['PATH'] = str(home) + os.pathsep + env.get('PATH', '')
    env.setdefault('DOTNET_CLI_TELEMETRY_OPTOUT', '1')
    env.setdefault('DOTNET_NOLOGO', '1')
    exe = shutil.which('dotnet', path=env['PATH'])
    if not exe:
        die('the .NET SDK (8 or newer) is required to build the plugins: https://dot.net')
    return exe, env


def build_plugins(refs, stage):
    exe, env = dotnet()
    for realm, (project, prop, tfm, names) in PLUGINS.items():
        ref = Path(refs[realm]).resolve()
        print(f'== build {realm} plugin against {ref}')
        # Deterministic build with the source root mapped to /_/ so no local path (user name, folders) ends up in the DLL/PDB.
        proc = subprocess.run([exe, 'build', str(ROOT / project), '-c', 'Release', '-nologo', '-v', 'q', '--no-incremental',
                               f'-p:{prop}={ref}', '-p:Deterministic=true', '-p:ContinuousIntegrationBuild=true',
                               f'-p:PathMap={ROOT}=/_/'],
                              cwd=ROOT, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if proc.returncode != 0:
            print((proc.stdout or '')[-4000:], (proc.stderr or '')[-2000:])
            die(f'{realm} plugin build failed (is {ref} a game folder with its mod loader installed?)')
        out = ROOT / Path(project).parent / 'bin' / 'Release' / tfm
        target = stage / 'plugins' / realm
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            if not (out / name).is_file():
                if name.endswith('.pdb'):
                    continue
                die(f'missing build output {out / name}')
            shutil.copy2(out / name, target / name)


SRMP_SOURCE_URL = 'https://github.com/Shunikai972/Slime-rancher-multiplayer-public-mode'


def strip_pdb_path(data: bytes) -> bytes:
    """Replace the build machine's PDB path in a PE CodeView (RSDS) record with the bare file name, same length."""
    out = bytearray(data)
    start = 0
    while True:
        at = out.find(b'RSDS', start)
        if at < 0:
            return bytes(out)
        path_at = at + 4 + 16 + 4                      # signature, GUID, age
        end = out.find(b'\x00', path_at)
        if end < 0 or end - path_at > 1024 or end == path_at:
            start = at + 4
            continue
        name = bytes(out[path_at:end]).replace(b'/', b'\\').rsplit(b'\\', 1)[-1]
        out[path_at:end] = name + b'\x00' * (end - path_at - len(name))
        start = end


def add_srmp(dll, stage):
    """Bundle SRMP Revival (GPL-3.0) for the launcher's Multi page: binary, its license and where its source is."""
    dll = Path(dll)
    license_file = next((p / 'LICENSE' for p in dll.parents if (p / 'LICENSE').is_file()), None)
    if license_file is None:
        die(f'SRMP license (GPL-3.0) not found above {dll}: SRMP cannot be shipped without it')
    target = stage / 'plugins' / 'SRMP'
    target.mkdir(parents=True, exist_ok=True)
    (target / 'SRMP.dll').write_bytes(strip_pdb_path(dll.read_bytes()))
    shutil.copy2(license_file, target / 'LICENSE-GPL-3.0.txt')
    (target / 'README.md').write_text(
        '# SRMP Revival\n\nSlime Rancher multiplayer mod (a maintained fork of SRMP), installed by the SlimeBridge launcher from its '
        'Multi page. It is a separate program under the GNU GPL v3 (LICENSE-GPL-3.0.txt), not covered by the SlimeBridge license.\n\n'
        f'Complete corresponding source: {SRMP_SOURCE_URL}\n', encoding='utf-8')
    print(f'== SRMP Revival bundled from {dll}')


def scan(stage):
    problems = []
    home = Path.home().name.encode('utf-8').lower()
    for rel in ALLOWED_BINARIES:
        path = stage / rel
        data = path.read_bytes().lower() if path.is_file() else b''
        if os.fsencode(str(ROOT.parent)).lower() in data or (len(home) > 2 and b'\\users\\' + home in data):
            problems.append(f'{rel} (contains a local build path)')
    for path in stage.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(stage).as_posix()
        if rel in ALLOWED_BINARIES:
            continue
        if FORBIDDEN.search(rel) or rel.lower().endswith(('.dll', '.pdb')):
            problems.append(rel)
        elif re.search(r'(^|/)(Lab/(?!README\.md$)|\.venv/|output[^/]*/|AssetCache/|GeneratedContent/)', rel):
            problems.append(rel)
    if problems:
        die('files that must never be released were staged:\n  ' + '\n  '.join(sorted(problems)[:50]))


def notes(ver):
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    match = re.search(rf'^## \[?v?{re.escape(ver)}\]?.*?$(.*?)(?=^## |\Z)', text, re.M | re.S)
    body = match.group(1).strip() if match else 'See CHANGELOG.md.'
    return (f'{body}\n\n### Install\n1. Download `SlimeBridge-v{ver}-win64-standalone.zip` (or the source zip `SlimeBridge-v{ver}-win64.zip`), '
            'extract it to a normal folder (not Program Files).\n'
            '2. Run `SlimeBridge.exe` from the standalone zip (everything inside), or from the source zip run '
            '`Install-SlimeBridge.cmd` once, then `SlimeBridge.cmd`.\n'
            '3. Full guide: README.md (section *Installation*).\n\n'
            'SlimeBridge ships no Slime Rancher files: the extractor reads your own installed games on your PC.\n'
            'SlimeBridge is unofficial Fan Content permitted under the Fan Content Policy. Not approved/endorsed by Monomi Park. '
            'Portions of the materials used are property of Monomi Park, LLC. ©Monomi Park LLC.\n')


def local_ref(prop):
    """SR1LabPath / SR2LabPath from the git-ignored mods/local.props (the same file the plugin projects import)."""
    props = ROOT / 'mods' / 'local.props'
    if not props.is_file():
        return None
    match = re.search(rf'<{prop}>([^<]+)</{prop}>', props.read_text(encoding='utf-8'))
    return match.group(1).strip() if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sr1-ref', default=os.environ.get('SLIMEBRIDGE_SR1_REF') or local_ref('SR1LabPath') or str(ROOT / 'Lab' / 'Games' / 'SR1'))
    parser.add_argument('--sr2-ref', default=os.environ.get('SLIMEBRIDGE_SR2_REF') or local_ref('SR2LabPath') or str(ROOT / 'Lab' / 'Games' / 'SR2'))
    parser.add_argument('--srmp-dll', default=os.environ.get('SLIMEBRIDGE_SRMP_DLL', str(ROOT.parent / 'srmp-public' / 'Builds' / 'SRMP' / 'SRMP.dll')),
                        help='SRMP Revival build to bundle for online play (skipped with a warning when missing)')
    parser.add_argument('--allow-dirty', action='store_true', help='release even with uncommitted changes')
    parser.add_argument('--publish', action='store_true', help='create the GitHub release v<VERSION> with gh')
    parser.add_argument('--draft', action='store_true', help='with --publish: create it as a draft')
    args = parser.parse_args()

    ver = version()
    changes = dirty()
    if changes and not args.allow_dirty:
        die('uncommitted changes (commit first, or --allow-dirty):\n  ' + '\n  '.join(changes[:20]))
    name = f'SlimeBridge-v{ver}'
    stage = DIST / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    files = tracked_files()
    for rel in files:
        target = stage / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    build_plugins({'SR1': args.sr1_ref, 'SR2': args.sr2_ref}, stage)
    if Path(args.srmp_dll).is_file():
        add_srmp(args.srmp_dll, stage)
    else:
        print(f'WARNING: no SRMP build at {args.srmp_dll}: this release has no Slime Rancher online')
    scan(stage)

    archive = DIST / f'{name}-win64.zip'
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(stage.rglob('*')):
            if path.is_file():
                zf.write(path, f'SlimeBridge/{path.relative_to(stage).as_posix()}')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (DIST / 'SHA256SUMS.txt').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    notes_file = DIST / f'release-notes-v{ver}.md'
    notes_file.write_text(notes(ver), encoding='utf-8')
    print(f'\nOK  {archive}  ({archive.stat().st_size / 1e6:.1f} MB, {len(files)} source files + plugins)')
    print(f'    sha256 {digest}')
    print(f'    notes  {notes_file}')

    command = ['gh', 'release', 'create', f'v{ver}', str(archive), str(DIST / 'SHA256SUMS.txt'),
               '--title', f'SlimeBridge v{ver}', '--notes-file', str(notes_file)]
    if args.draft:
        command.append('--draft')
    if not args.publish:
        print('\nTo publish (tag + GitHub release):\n  git tag v' + ver + ' && git push origin v' + ver + '\n  ' + ' '.join(
            f'"{c}"' if ' ' in c else c for c in command))
        return
    if not shutil.which('gh'):
        die('the GitHub CLI (gh) is required for --publish: https://cli.github.com')
    subprocess.run(['git', 'tag', '-a', f'v{ver}', '-m', f'SlimeBridge v{ver}'], cwd=ROOT, check=False)
    subprocess.run(['git', 'push', 'origin', f'v{ver}'], cwd=ROOT, check=True)
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
