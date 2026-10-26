"""The shared C# layout reader and progress rules, fed a layout written by the Python generator (synthetic, no game)."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from bridge.sanctuary import layout as L
from tests.sanctuary.fixtures import copy_kit

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / 'tests' / 'SanctuaryHarness' / 'SanctuaryHarness.csproj'


class SanctuaryHarnessTests(unittest.TestCase):
    def test_reader_and_progress_rules(self):
        dotnet = os.environ.get('SANCTUARY_DOTNET') or shutil.which('dotnet')
        self.assertTrue(dotnet, 'dotnet SDK required')
        with tempfile.TemporaryDirectory(prefix='sanctuary-layout-') as temporary:
            layout_path = Path(temporary) / 'sanctuary-layout.json'
            layout_path.write_bytes(L.layout_bytes(L.build_layout(copy_kit())))
            command = [dotnet, 'run', '--project', str(HARNESS), '-c', 'Release', '--', str(layout_path)]
            result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding='utf-8', errors='replace', timeout=240)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('failures=0', result.stdout)
            self.assertNotIn('FAIL ', result.stdout)


if __name__ == '__main__':
    unittest.main()
