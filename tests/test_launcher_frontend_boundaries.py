"""Run actual launcher JavaScript with deferred backend responses, not a reimplemented action."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scenario', ['installed', 'search', 'carousel'])
def test_actual_frontend_boundaries(scenario):
    node = shutil.which('node')
    if not node:
        fallback = Path('C:/Program Files/nodejs/node.exe')
        node = str(fallback) if fallback.is_file() else None
    assert node, 'Node.js required for production-script frontend regression tests'
    result = subprocess.run([node, str(ROOT/'tests/LauncherFrontendHarness.cjs'),
                             str(ROOT/'apps/launcher/ui/js/app.js'), scenario],
                            capture_output=True, text=True, encoding='utf8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'PASS actual app.js ' + scenario in result.stdout
