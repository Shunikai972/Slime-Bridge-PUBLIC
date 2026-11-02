"""Reject non-LAB roots before even requesting save backup or plugin installation."""
import ast
import importlib.util
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audit_lab_paths', ROOT / 'tools/lab/paths.py')
paths = importlib.util.module_from_spec(spec)
spec.loader.exec_module(paths)


@pytest.mark.parametrize('name,args', [
    ('real_travel_oracle.py', lambda outside: (outside, outside, 0, True)),
    ('launcher_travel_probe.py', lambda outside: (outside, outside, 0)),
    ('sr1_ops_probe.py', lambda outside: (outside, [], 0, [])),
    ('sr2_newgame_probe.py', lambda outside: (outside, outside, 0)),
])
def test_original_install_roots_refused_before_protected_side_effects(tmp_path, name, args):
    tree = ast.parse((ROOT / 'tools/lab' / name).read_text(encoding='utf-8-sig'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    events = []
    outside = tmp_path / 'outside-install'
    outside.mkdir()
    for exe in ('SlimeRancher.exe', 'SlimeRancher2.exe'):
        (outside / exe).write_bytes(b'synthetic inert marker, never executable')

    def forbidden_backup(*args, **kwargs):
        events.append('snapshot-requested')
        raise RuntimeError('synthetic protected-side-effect marker; no backup performed')

    env = {'Path': Path, 'datetime': datetime, 'timezone': timezone, 'uuid': uuid,
           'EXE': {'SR1': 'SlimeRancher.exe', 'SR2': 'SlimeRancher2.exe'},
           'REPO_ROOT': tmp_path / 'synthetic-repo',
           'assert_no_other_game_processes': lambda: None,
           'save_guard': NS(snapshot=forbidden_backup),
           'oracle': NS(LAB=tmp_path / 'synthetic-repo' / 'Lab' / 'Games'),
           'validate_lab_destination': paths.validate_lab_destination}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), '<exact-run-preflight>', 'exec'), env)
    with pytest.raises(ValueError, match='Lab/Games'):
        env['run'](*args(outside))
    assert not events
