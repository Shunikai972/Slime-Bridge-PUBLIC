"""Compile the whole SlimeBridge project in one command.

    py -3.12 build_all.py                  python syntax check + both plugins + every C# test harness
    py -3.12 build_all.py --deploy         ... then copy the built plugins into Lab/Games (never an original install)
    py -3.12 build_all.py --plugins-only   only SlimeBridge.SR1 / SlimeBridge.SR2 (fast)
    py -3.12 build_all.py --tests          ... then run the Python unit-test suite (tools/run_tests.cmd equivalent)
    py -3.12 build_all.py --play           ... then everything linked: build, species caches, plugin, island + portal to SR2,
                                           BridgeHost and the portal-crossing broker, then the LAB Slime Rancher 1 (--play BOTH
                                           also starts SR2). Logs: Lab/LauncherDev/play.log
    py -3.12 build_all.py --launch-sr1     ... or just the LAB Slime Rancher 1 with the imported SR2 species armed (dev session)
                                           (see tools/lab/species_dev_session.py; add --species slime.ringtail to choose the first one)

Exit code is 0 only when every requested step succeeded. Nothing here writes outside this repository and its Lab/ folder.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGINS = [ROOT / 'mods' / 'sr1-bridge' / 'SlimeBridge.SR1.csproj', ROOT / 'mods' / 'sr2-bridge' / 'SlimeBridge.SR2.csproj']
PY_DIRS = ['bridge', 'apps', 'tools']
# Orphaned harness: no test references it and mods/sr2-bridge/InventoryAgent.cs now depends on BackgroundIntroSkipper, which it does not include.
STALE_HARNESSES = {'SR2AdapterHarness.csproj'}
SR1_MANAGED = ROOT / 'Lab' / 'Games' / 'SR1' / 'SlimeRancher_Data' / 'Managed'
SKIP_PARTS = {'bin', 'obj', 'node_modules', '.worktrees', 'vendor', 'Lab'}


def dotnet_env():
    env = dict(os.environ)
    root = env.get('DOTNET_ROOT') or str(Path.home() / '.dotnet')
    if (Path(root) / 'dotnet.exe').is_file():
        env['DOTNET_ROOT'] = root
        env['PATH'] = root + os.pathsep + env.get('PATH', '')
    env.setdefault('DOTNET_NOLOGO', '1')
    env.setdefault('DOTNET_CLI_TELEMETRY_OPTOUT', '1')
    env.setdefault('TMPDIR', env.get('TEMP', str(ROOT / 'Lab' / 'Cache')))
    return env


def run(cmd, label, env=None, quiet=True):
    if env and cmd and cmd[0] == 'dotnet':
        cmd = [shutil.which('dotnet', path=env['PATH']) or 'dotnet', *cmd[1:]]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=quiet, text=True, encoding='utf-8', errors='replace')
    seconds = time.monotonic() - started
    ok = proc.returncode == 0
    print(f'  [{"OK  " if ok else "FAIL"}] {label}  ({seconds:.1f}s)', flush=True)
    if not ok and quiet:
        tail = (proc.stdout or '') + (proc.stderr or '')
        errors = [line for line in tail.splitlines() if ' error ' in line or 'error:' in line.lower()]
        for line in (errors or tail.splitlines())[-12:]:
            print('        ' + line.strip(), flush=True)
    return ok


def python_syntax():
    print('== Python syntax check')
    ok = True
    for name in PY_DIRS:
        folder = ROOT / name
        if folder.is_dir():
            for path in folder.rglob('*.py'):
                if SKIP_PARTS & set(path.relative_to(ROOT).parts):
                    continue
                try:
                    compile(path.read_text(encoding='utf-8'), str(path), 'exec', dont_inherit=True)
                except SyntaxError as exc:
                    ok = False
                    print(f'  [FAIL] {path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}')
    print(f'  [{"OK  " if ok else "FAIL"}] python files under {", ".join(PY_DIRS)}')
    return ok


def build_projects(projects, label):
    print(f'== {label} ({len(projects)})')
    env = dotnet_env()
    ok = True
    for project in projects:
        rel = project.relative_to(ROOT)
        if project.name in STALE_HARNESSES:
            print(f'  [SKIP] {rel}  (orphaned harness, see STALE_HARNESSES in build_all.py)')
            continue
        extra = ['-p:SR1ManagedPath=' + str(SR1_MANAGED)] if SR1_MANAGED.is_dir() else []
        ok &= run(['dotnet', 'build', str(project), '-c', 'Release', '--nologo', '-v', 'q', '-clp:ErrorsOnly', *extra], str(rel), env)
    return ok


def harness_projects():
    return sorted(p for p in (ROOT / 'tests').rglob('*.csproj') if not (SKIP_PARTS & set(p.relative_to(ROOT).parts[1:])))


def deploy():
    print('== Deploy into Lab/Games')
    return run([sys.executable, '-m', 'tools.lab.deploy', 'SR1', 'SR2'], 'tools.lab.deploy (SR1 + SR2)', quiet=False)


def unit_tests():
    print('== Python unit tests')
    env = dotnet_env()
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], 'unittest discover -s tests', env, quiet=False)


def latest_spec():
    runs = sorted((ROOT / 'Lab' / 'GeneratedContent' / 'species-import').glob('*/import-spec-SR1.json'))
    return runs[-1] if runs else None


def launch_sr1(species):
    print('== Launch Slime Rancher 1 (LAB copy) with the imported SR2 species')
    spec = latest_spec()
    if spec is None:
        print('  [FAIL] no species-import spec under Lab/GeneratedContent/species-import')
        return False
    cmd = [sys.executable, '-m', 'tools.lab.species_dev_session', '--realm', 'SR1', '--spec', str(spec),
           '--species', species, '--ops', '[]', '--current-user-authorized']
    print('  spec:', spec.relative_to(ROOT))
    print('  The game stays open after this command returns; in the in-game console type:  slimebridge list  /  slimebridge spawn <id>')
    return subprocess.run(cmd, cwd=ROOT, env=dotnet_env()).returncode == 0


def species_caches():
    print('== Imported species caches (model, icon, animation, plorts)')
    return run([sys.executable, '-m', 'bridge.content.species_pipeline'], 'bridge.content.species_pipeline', quiet=False)


def play(target):
    """Everything linked: plugin + island/portal + imported species + BridgeHost + crossing broker (apps.launcher.headless)."""
    print(f'== Play {target}: plugin, island + portal, imported species, BridgeHost, portal crossings')
    log_dir = ROOT / 'Lab' / 'LauncherDev'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'play.log'
    flags = 0
    if os.name == 'nt':
        flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with log_path.open('w', encoding='utf-8') as log:
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'apps.launcher.headless', '--target', target], cwd=ROOT, env=dotnet_env(),
                                 stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        text = log_path.read_text(encoding='utf-8', errors='replace')
        if 'serving portal crossings' in text or child.poll() is not None:
            break
        time.sleep(1)
    text = log_path.read_text(encoding='utf-8', errors='replace')
    print('\n'.join('  ' + line for line in text.splitlines()[-25:]))
    ok = 'serving portal crossings' in text and child.poll() is None
    print(f'  [{"OK  " if ok else "FAIL"}] launcher backend pid {child.pid} (log: {log_path.relative_to(ROOT)})')
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--play', nargs='?', const='SR1', choices=('SR1', 'SR2', 'BOTH'),
                        help='build, make everything ready and start the game(s) through the launcher backend (default SR1)')
    parser.add_argument('--plugins-only', action='store_true', help='skip the C# test harnesses')
    parser.add_argument('--deploy', action='store_true', help='copy the built plugins into Lab/Games')
    parser.add_argument('--tests', action='store_true', help='run the Python unit-test suite afterwards')
    parser.add_argument('--launch-sr1', action='store_true', help='start the LAB Slime Rancher 1 afterwards (implies --deploy)')
    parser.add_argument('--species', default='slime.ringtail', help='species used to arm the session (default slime.ringtail)')
    args = parser.parse_args()

    started = time.monotonic()
    results = {}
    results['python'] = python_syntax()
    results['plugins'] = build_projects(PLUGINS, 'Plugins (Release)')
    if not args.plugins_only:
        results['harnesses'] = build_projects(harness_projects(), 'C# test harnesses (compile only)')
    if args.deploy or args.launch_sr1 or args.play:
        results['deploy'] = results['plugins'] and deploy()
    if args.tests:
        results['tests'] = unit_tests()
    if args.launch_sr1:
        results['launch'] = results.get('deploy', False) and launch_sr1(args.species)
    if args.play:
        results['species'] = species_caches()
        results['play'] = results.get('deploy', False) and play(args.play)

    print('\n== Summary (%.0fs)' % (time.monotonic() - started))
    for name, ok in results.items():
        print(f'  {"OK  " if ok else "FAIL"}  {name}')
    return 0 if all(results.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
