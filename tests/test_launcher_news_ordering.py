"""Real startup/manual-news response ordering with deterministic inert DOM and API boundaries."""
from pathlib import Path
import itertools
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ['matrix-' + '-'.join(parts) for parts in itertools.product(
    ('cache-first', 'refresh-first'), ('nonempty', 'empty', 'null', 'reject'), ('fresh', 'empty', 'null', 'reject'))]
SCENARIOS += ['retry-pending-empty-cache-after-null', 'retry-pending-empty-cache-after-reject',
              'overlapping-refresh-older-first', 'overlapping-refresh-newer-first']

@pytest.mark.parametrize('scenario', SCENARIOS)
def test_news_request_ordering(scenario):
    node = shutil.which('node') or 'C:/Program Files/nodejs/node.exe'
    proc = subprocess.run([node, str(ROOT/'tests/LauncherNewsOrderingHarness.cjs'), str(ROOT), scenario],
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stdout + proc.stderr
