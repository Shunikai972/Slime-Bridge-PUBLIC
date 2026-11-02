"""Exact cleanup-block regressions with inert provider stubs; no entrypoint or OS actions."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]


def function(name, symbol):
    tree = ast.parse((ROOT / 'tools/lab' / name).read_text(encoding='utf-8-sig'))
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == symbol)


def compile_nodes(nodes, env):
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<exact-cleanup-source>', 'exec'), env)


class VirtualPath:
    def __init__(self, label='synthetic', events=None):
        self.label = label
        self.events = [] if events is None else events

    def __truediv__(self, part):
        return VirtualPath(self.label + '/' + str(part), self.events)

    def write_text(self, text, **kwargs):
        self.events.append('evidence')
        return len(text)

    def __str__(self):
        return self.label


CASES = {
    'travel_oracle.py': ('stop', 'host', 'log', 'plugin-sr1', 'plugin-sr2'),
    'real_travel_oracle.py': ('stop', 'host', 'log', 'display'),
    'launcher_travel_probe.py': ('terminate', 'shutdown', 'display'),
    'sanctuary_oracle.py': ('stop', 'log', 'plugin-sr1'),
    'sr2_probe_oracle.py': ('stop', 'host', 'plugin-sr2'),
    'sr1_ops_probe.py': ('terminate', 'display'),
    'sr2_newgame_probe.py': ('terminate', 'display'),
}


@pytest.mark.parametrize('name,fault', [(name, fault) for name, faults in CASES.items() for fault in faults])
def test_cleanup_attempts_protected_finalizer_after_independent_failure(name, fault):
    events = []
    result = {'passed': True, 'started_utc': 'synthetic', 'timeline': []}

    def step(label, value=None):
        def call(*args, **kwargs):
            events.append(label)
            if label == fault:
                raise OSError('synthetic ' + label + ' failure')
            return {} if value is None else value
        return call

    class Child:
        pid = 101

        def poll(self):
            return None

        terminate = step('terminate')
        wait = step('wait')
        kill = step('kill')

    def finalize(actual, *args):
        events.append('finalize')
        actual['saves_match_backup_after_restore'] = True

    child = Child()
    stop_label = 'terminate' if name in ('launcher_travel_probe.py', 'sr1_ops_probe.py', 'sr2_newgame_probe.py') else 'stop'
    oracle = NS(_stop_owned=step(stop_label), _finalize_current_user=finalize,
                restore_plugins=step('plugin-pack'))
    env = {'result': result, 'directory': VirtualPath(events=events), 'started': {'SR1': child},
           'host': NS(__exit__=step('host')), 'roots': {'SR1': 'synthetic-sr1', 'SR2': 'synthetic-sr2'},
           'api': NS(_launcher=NS(_children={'SR1': child, 'SR2': child}), shutdown=step('shutdown')),
           'stop_started': step('stop'), 'log_lines': step('log'), 'log_excerpt': step('log'),
           'oracle': oracle, 'previous_plugin': {}, 'previous': {'SR1': {}, 'SR2': {}}, 'previous_plugins': {},
           'restore_plugin': step('plugin-sr1' if name == 'sanctuary_oracle.py' else 'plugin-sr2'),
           'sanctuary_oracle': NS(SR1_LOG=VirtualPath(), restore_plugin=step('plugin-sr1')),
           'sr2_probe_oracle': NS(restore_plugin=step('plugin-sr2')),
           'screen_guard': NS(restore=step('display')), 'snap': 'synthetic-snapshot', 'screens': {},
           'SR1_LOG': VirtualPath(), 'SR2_LOG': VirtualPath(), 'Path': VirtualPath,
           'root': VirtualPath(), 'exe': VirtualPath(), 'proc': child,
           'time': NS(sleep=lambda _: None), 'json': json}
    # New shared helpers, when present, are loaded only as exact inert source functions.
    try:
        helper = function('oracle.py', 'cleanup_steps')
    except StopIteration:
        pass
    else:
        helpers = {}
        compile_nodes([helper], helpers)
        oracle.cleanup_steps = helpers['cleanup_steps']
    if name == 'sr2_probe_oracle.py':
        env['started'] = {'SR2': child, 'host': env['host']}
    run = function(name, 'run')
    cleanup = next(n for n in run.body if isinstance(n, ast.Try) and n.finalbody).finalbody
    wrapper = ast.FunctionDef(name='cleanup', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                              kw_defaults=[], defaults=[]), body=cleanup, decorator_list=[])
    compile_nodes([wrapper], env)
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            env['cleanup']()
        except OSError:
            pass
    assert fault in events, (name, fault, events)
    assert 'finalize' in events, (name, fault, events)
    assert 'evidence' in events, (name, fault, events)
    assert result['passed'] is False, (name, fault, result)
    assert result.get('cleanup_completed') is not True
