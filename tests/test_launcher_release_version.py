"""The launcher must display the same version as its release/updater metadata."""
from pathlib import Path

from apps.launcher import desktop, updater


def test_launcher_display_uses_canonical_release_version():
    expected = (Path(__file__).resolve().parents[1] / 'VERSION').read_text(encoding='utf-8').strip()
    assert desktop.VERSION == expected
    assert desktop.VERSION == updater.current_version()
