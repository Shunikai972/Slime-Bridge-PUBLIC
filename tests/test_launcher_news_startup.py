"""Terminal news ordering through the real startup and home modules; no browser/network."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('scenario', ['late-cache-after-empty-refresh', 'cache-before-empty-refresh-control',
    'cache-rejection-after-fresh-control', 'null-refresh-then-late-cache-control',
    'rejected-refresh-then-late-cache-control'])
def test_terminal_news_order(scenario):
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    result = subprocess.run([node, str(ROOT/'tests/LauncherNewsStartupHarness.cjs'), str(ROOT), scenario],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
