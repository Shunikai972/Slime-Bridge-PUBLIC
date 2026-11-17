"""Regression checks for private .NET homes; these checks never execute .NET."""
import ast
import os
from pathlib import Path
import unittest
from unittest import mock

from bridge.app import conversion_jobs, hostlife
from tools.release import make_release
import test_sr1_background_intro


class DotnetPrivateHomeEnvironmentTests(unittest.TestCase):
    def test_lab_child_disables_global_tool_path_even_if_parent_enables_it(self):
        parent = {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}
        root = Path('synthetic-lab-root')
        with mock.patch.object(hostlife, 'confined_root', return_value=root), \
                mock.patch.object(hostlife, 'ensure_dir'):
            env = hostlife.child_env(root, base=parent)
        self.assertEqual(env['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
        self.assertEqual(parent['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')

    def test_conversion_child_disables_global_tool_path_without_changing_parent(self):
        job = conversion_jobs.ConversionJobs.__new__(conversion_jobs.ConversionJobs)
        job.root = Path('synthetic-conversion-root')
        with mock.patch.dict(os.environ, {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}), \
                mock.patch.object(conversion_jobs, 'ensure_dir'):
            env = job._env()
            self.assertEqual(env['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
            self.assertEqual(os.environ['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')

    def test_sr1_fixture_passes_the_guard_to_dotnet_without_changing_parent(self):
        fixture = test_sr1_background_intro.SR1BackgroundIntroTests
        temporary = mock.Mock(name='synthetic temporary directory')
        temporary.name = 'synthetic-scratch'
        completed = mock.Mock(returncode=0)
        with mock.patch.dict(os.environ, {'TMPDIR': 'synthetic-scratch',
                                         'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}), \
                mock.patch.object(test_sr1_background_intro, 'DOTNET', 'synthetic-dotnet'), \
                mock.patch.object(test_sr1_background_intro.tempfile, 'TemporaryDirectory', return_value=temporary), \
                mock.patch.object(Path, 'mkdir'), \
                mock.patch.object(test_sr1_background_intro.subprocess, 'run', return_value=completed) as run:
            fixture.setUpClass()
            self.assertEqual(run.call_args.kwargs['env']['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
            self.assertEqual(os.environ['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')

    def test_release_build_environment_disables_the_global_tool_path(self):
        with mock.patch.dict(os.environ, {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}), \
                mock.patch.object(Path, 'is_file', return_value=False), \
                mock.patch.object(make_release.shutil, 'which', return_value='synthetic-dotnet'):
            _, env = make_release.dotnet()
            self.assertEqual(env['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
            self.assertEqual(os.environ['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')

    def test_temporary_dotnet_homes_are_guarded_in_test_environment_builders(self):
        test_root = Path(__file__).resolve().parent
        checked = 0
        for path in test_root.rglob('*.py'):
            tree = ast.parse(path.read_bytes(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and any(k.arg == 'DOTNET_CLI_HOME' for k in node.keywords):
                    checked += 1
                    guards = [k.value for k in node.keywords if k.arg == 'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH']
                    self.assertEqual(len(guards), 1, f'{path}:{node.lineno}')
                    self.assertIsInstance(guards[0], ast.Constant, f'{path}:{node.lineno}')
                    self.assertEqual(guards[0].value, 'false', f'{path}:{node.lineno}')
        self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()
