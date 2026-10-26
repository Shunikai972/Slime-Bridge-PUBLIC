"""Regression coverage for validate_spec_destination (lexical containment + reparse refusal)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.lab.paths import validate_spec_destination  # noqa: E402


def make_junction(link: Path, target: Path) -> bool:
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    try:
        return result.returncode == 0 and link.is_junction()
    except OSError:
        return False


class SpecPathValidationTests(unittest.TestCase):
    def test_rejects_path_outside_root(self):
        with self.assertRaises(ValueError):
            validate_spec_destination(Path('C:/outside/spec.json'), Path('C:/inside/root'))

    def test_accepts_regular_path_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'root'
            sub = root / 'sub'
            sub.mkdir(parents=True)
            spec = sub / 'spec.json'
            spec.write_text('{}', encoding='utf-8')
            self.assertEqual(validate_spec_destination(spec, root), spec.resolve())

    def test_rejects_junction_root_shadowing_external_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            real = tmp / 'real-root'
            real.mkdir()
            (real / 'sub').mkdir()
            (real / 'sub' / 'spec.json').write_text('{}', encoding='utf-8')
            link = tmp / 'linked-root'
            if not make_junction(link, real):
                self.skipTest('junction creation unavailable')
            with self.assertRaises(ValueError):
                validate_spec_destination(link / 'sub' / 'spec.json', link)

    def test_rejects_junction_inside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            root = tmp / 'root'
            root.mkdir()
            outside = tmp / 'outside'
            outside.mkdir()
            (outside / 'spec.json').write_text('{}', encoding='utf-8')
            link = root / 'sub'
            if not make_junction(link, outside):
                self.skipTest('junction creation unavailable')
            with self.assertRaises(ValueError):
                validate_spec_destination(link / 'spec.json', root)


if __name__ == '__main__':
    unittest.main()
