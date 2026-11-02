"""Writeability checks must only remove the temporary files they actually created."""
import io
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.launcher import diagnostics, selfcheck


@pytest.mark.parametrize('kind', ['diagnostics', 'selfcheck'])
def test_writeability_probe_preserves_preexisting_names(kind):
    with tempfile.TemporaryDirectory(prefix='launcher-probe-') as tmp:
        root = Path(tmp)
        directory = root/'Lab' if kind=='diagnostics' else root/'Lab'/'Launcher'
        directory.mkdir(parents=True)
        existing = directory/('.diag-write' if kind=='diagnostics' else '.write-test')
        existing.write_bytes(b'existing user file')
        before = {p.name:p.read_bytes()for p in directory.iterdir()}
        if kind=='diagnostics':
            with patch.object(diagnostics, 'REPO_ROOT', root):
                diagnostics._system([])
        else:
            with patch.object(selfcheck, 'REPO_ROOT', root), \
                    patch.object(selfcheck.importlib, 'import_module'), \
                    patch.object(selfcheck.extract, 'locate_tool', return_value={'found':True,'path':'fixture'}), \
                    patch.object(selfcheck.plugins, 'find_source', return_value=None), \
                    patch('sys.stdout', new_callable=io.StringIO):
                assert selfcheck.run()
        after = {p.name:p.read_bytes()for p in directory.iterdir()}
        assert after==before, 'Probe overwrote/deleted an unowned file or left its own temporary file'
