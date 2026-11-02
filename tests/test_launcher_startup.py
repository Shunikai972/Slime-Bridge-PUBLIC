from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['news-hang', 'scan-hang', 'state-error'])
def test_normal_boot_has_no_optional_startup_deadlock(scenario):
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    result = subprocess.run([node, str(ROOT / 'tests/LauncherStartupHarness.cjs'),
                             str(ROOT / 'apps/launcher/ui/js/app.js'), scenario],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
