"""Real frontend modules in an inert DOM: no native windows, backend or network."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('scenario', [
    'normal-hung-optionals', 'first-consent', 'state-rejection-retry',
    'state-timeout-retry-late-response', 'autodetect-completes-background',
    'autodetect-timeout-late-result', 'startup-before-state-navigation',
    'failure-before-state-navigation', 'invalid-state-retry',
    'cached-news-cannot-replace-newer-refresh', 'cached-news-in-order-control',
    'ready-state-navigation-control', 'loading-control-and-branding',
])
def test_startup_boundary(scenario):
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    result = subprocess.run([node, str(ROOT / 'tests/LauncherStartupBoundaries.cjs'), str(ROOT), scenario],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
