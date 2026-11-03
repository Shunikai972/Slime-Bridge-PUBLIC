"""Environment-only regressions; no SDK command, game or profile operation."""
import os
from pathlib import Path
import unittest
from unittest import mock

from bridge.app import conversion_jobs, hostlife
from tools.release import make_release


class DotnetChildEnvironmentTests(unittest.TestCase):
    def test_host_controller_overrides_parent_opt_in_without_mutating_it(self):
        parent = {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true', 'PATH': 'parent-path'}
        root = Path('synthetic-host-root')
        with mock.patch.object(hostlife, 'confined_root', return_value=root), \
                mock.patch.object(hostlife, 'ensure_dir'):
            child = hostlife.child_env(root, base=parent)
        self.assertEqual(child['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
        self.assertEqual(parent, {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true', 'PATH': 'parent-path'})

    def test_conversion_overrides_parent_opt_in_without_mutating_it(self):
        job = conversion_jobs.ConversionJobs.__new__(conversion_jobs.ConversionJobs)
        job.root = Path('synthetic-conversion-root')
        with mock.patch.dict(os.environ, {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}), \
                mock.patch.object(conversion_jobs, 'ensure_dir'):
            child = job._env()
            self.assertEqual(child['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
            self.assertEqual(os.environ['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')

    def test_build_helper_overrides_parent_opt_in_without_executing_sdk(self):
        with mock.patch.dict(os.environ, {'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'true'}), \
                mock.patch.object(Path, 'is_file', return_value=False), \
                mock.patch.object(make_release.shutil, 'which', return_value='synthetic-dotnet'), \
                mock.patch.object(make_release.subprocess, 'run', side_effect=AssertionError('SDK execution forbidden')):
            executable, child = make_release.dotnet()
            self.assertEqual(executable, 'synthetic-dotnet')
            self.assertEqual(child['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'false')
            self.assertEqual(os.environ['DOTNET_ADD_GLOBAL_TOOLS_TO_PATH'], 'true')


if __name__ == '__main__':
    unittest.main()
