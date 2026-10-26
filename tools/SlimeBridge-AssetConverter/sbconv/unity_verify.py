"""UNITY_VERIFIED: load a runtime package inside the *matching Unity Editor* (batch mode) with the C# builder and let Unity
skin/animate it, then compare Unity's evaluated bone poses with this tool's own curve evaluation.

Requires an editor with a license you activated yourself (nothing here activates or bypasses licensing). The game's
custom shaders do not exist in a bare editor project, so materials use a tinted Standard placeholder: this proves
loading, skeleton, skinning, animation and references - NOT the games' shading (that stays a GAME_RUNTIME_VERIFIED matter)."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np

from .ir.curves import eval_vector

HUB_EDITORS = [r'C:\Program Files\Unity\Hub\Editor', r'D:\Unity\Editor']
CSHARP = Path(__file__).resolve().parents[1] / 'csharp'


def find_editor(version):
    roots = HUB_EDITORS + [p for p in os.environ.get('SBCONV_UNITY_ROOTS', '').split(os.pathsep) if p]
    for r in roots:
        # the second form tolerates installers run with /D=...\Editor, which nest an extra Editor folder
        for exe in (Path(r) / version / 'Editor' / 'Unity.exe', Path(r) / version / 'Editor' / 'Editor' / 'Unity.exe'):
            if exe.is_file():
                return exe
    return None


def prepare_project(project):
    project = Path(project)
    (project / 'Assets' / 'Plugins').mkdir(parents=True, exist_ok=True)
    (project / 'Assets' / 'Editor').mkdir(parents=True, exist_ok=True)
    core = CSHARP / 'SbRuntime.Core' / 'bin' / 'Release' / 'netstandard2.0' / 'SbRuntime.Core.dll'
    sr1 = CSHARP / 'SbRuntime.Unity' / 'SbRuntime.Unity.SR1' / 'bin' / 'Release' / 'net472' / 'SbRuntime.Unity.SR1.dll'
    for f in (core, sr1):
        if not f.is_file():
            raise FileNotFoundError(f'{f} missing: build csharp/ first (dotnet build -c Release)')
        shutil.copy2(f, project / 'Assets' / 'Plugins' / f.name)
    shutil.copy2(CSHARP / 'UnityVerify' / 'SbVerify.cs', project / 'Assets' / 'Editor' / 'SbVerify.cs')


def _quat_angle(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a /= max(np.linalg.norm(a), 1e-12)
    b /= max(np.linalg.norm(b), 1e-12)
    return 2 * np.arccos(min(1.0, abs(float(a @ b))))


def compare_poses(pkg, report):
    """Unity-evaluated bone poses vs this tool's evaluation of the same curves."""
    an = json.loads((Path(pkg) / 'animation' / 'controller.json').read_text(encoding='utf-8'))
    clips = {}
    for meta in an['clips'].values():
        c = json.loads((Path(pkg) / meta['file']).read_text(encoding='utf-8'))
        clips[c['name']] = c
    worst_p = worst_r = 0.0
    n = 0
    for entry in report.get('animation', []):
        clip = clips.get(entry['clip'])
        if clip is None:
            continue
        pos = {c['path']: c for c in clip['positions']}
        rot = {c['path']: c for c in clip['rotations']}
        for pose in entry['poses']:
            for b in pose['bones']:
                if b['path'] in pos:
                    exp = eval_vector(pos[b['path']]['keys'], [pose['t']], 3)[0]
                    worst_p = max(worst_p, float(np.abs(exp - np.array(b['pos'])).max()))
                    n += 1
                if b['path'] in rot:
                    exp = eval_vector(rot[b['path']]['keys'], [pose['t']], 4)[0]
                    worst_r = max(worst_r, _quat_angle(exp, b['rot']))
    return {'samples': n, 'worst_position_error': worst_p, 'worst_rotation_error_rad': worst_r}


def verify(pkg_dir, unity_version, work_dir, timeout=900, screenshot=True):
    pkg_dir = Path(pkg_dir).resolve()
    exe = find_editor(unity_version)
    if exe is None:
        return {'ok': False, 'status': 'UNITY_EDITOR_MISSING', 'error': f'Unity {unity_version} not installed (Unity Hub > Installs)'}
    work = Path(work_dir).resolve()
    project = work / f'project-{unity_version}'
    work.mkdir(parents=True, exist_ok=True)
    first = not (project / 'ProjectSettings').exists()
    log = work / f'unity-{unity_version}.log'
    if first:
        subprocess.run([str(exe), '-batchmode', '-quit', '-createProject', str(project), '-logFile', str(log)], timeout=timeout,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    prepare_project(project)
    out_json = work / f'report-{Path(pkg_dir).name}.json'
    shot = work / f'shot-{Path(pkg_dir).name}.png'
    out_json.unlink(missing_ok=True)
    cmd = [str(exe), '-batchmode', '-quit', '-projectPath', str(project), '-executeMethod', 'SbVerify.Run', '-sbPackage', str(pkg_dir), '-sbOut', str(out_json), '-logFile', str(log)]
    if screenshot:
        cmd += ['-sbShot', str(shot)]
    t0 = time.time()
    proc = subprocess.run(cmd, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if not out_json.is_file():
        tail = log.read_text(encoding='utf-8', errors='replace')[-1500:] if log.is_file() else ''
        return {'ok': False, 'status': 'UNITY_RUN_FAILED', 'exit_code': proc.returncode, 'log_tail': tail}
    report = json.loads(out_json.read_text(encoding='utf-8'))
    cmp_ = compare_poses(pkg_dir, report)
    cmp_ = {k: (float(v) if not isinstance(v, int) else v) for k, v in cmp_.items()}
    ok = bool(bool(report.get('ok')) and cmp_['worst_position_error'] < 1e-3 and cmp_['worst_rotation_error_rad'] < 5e-3 and report.get('worst_rest_pose_deviation', 1) < 0.02)
    return {'ok': ok, 'status': 'UNITY_VERIFIED' if ok else 'INCOMPLETE', 'unity': report.get('unity'), 'report': report, 'pose_comparison': cmp_,
            'seconds': round(time.time() - t0, 1), 'screenshot': str(shot) if shot.is_file() else None, 'editor': str(exe)}
