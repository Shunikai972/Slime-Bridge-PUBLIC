import unittest
from pathlib import Path
from unittest import mock

from apps.launcher import diagnostics, sanctuary


class DiagnosticsTests(unittest.TestCase):
    def test_report_has_every_section_and_survives_a_broken_probe(self):
        with mock.patch('apps.launcher.detect.auto_detect', return_value=[]), \
             mock.patch.object(diagnostics, '_defender', side_effect=RuntimeError('no powershell')), \
             mock.patch.object(diagnostics, '_network', lambda out: out.append('  network skipped')):
            text = diagnostics.report(None, ['line a', 'line b'])
        for title in ('== System ==', '== Game installs ==', '== Island (sanctuary) ==', '== Windows security', '== Last extraction'):
            self.assertIn(title, text)
        self.assertIn('!! RuntimeError: no powershell', text)          # a failing probe is reported, not fatal
        self.assertIn('line b', text)


class BundledIslandKitTests(unittest.TestCase):
    def test_the_shipped_kit_is_used_when_lab_has_none(self):
        with mock.patch.object(sanctuary, 'KIT_ROOT', Path('does-not-exist')):
            kit = sanctuary.latest_kit()
        self.assertIsNotNone(kit)
        self.assertIn('bundled', kit['path'])
        self.assertGreater(kit['native_constructs'], 0)


if __name__ == '__main__':
    unittest.main()
