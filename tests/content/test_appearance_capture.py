"""Tests for the additive bridge-appearance-colors reader (LAB read-only capture)."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from bridge.content import appearance_capture as AC


def palette(seed=0.1):
    return [seed, seed + 0.1, seed + 0.2, 1.0] * 4


def entry(**overrides):
    base = {
        'native_ref': 'SlimeDefinition.Ringtail',
        'display_name': 'Ringtail',
        'save_set': 'CLASSIC',
        'palette_legacy': palette(0.1),
        'palette_new': palette(0.2),
    }
    base.update(overrides)
    return base


def snapshot(**overrides):
    base = {
        'schema': AC.SCHEMA,
        'schema_version': AC.SCHEMA_VERSION,
        'realm': 'SR2',
        'run_id': 'registry-20261008-020250-df917e084eeb',
        'collector_version': '2',
        'active_sha256': 'b' * 64,
        'entries': [entry()],
        'errors': [],
    }
    base.update(overrides)
    return base


class AppearanceCaptureTests(unittest.TestCase):
    def test_valid_snapshot_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'appearance-colors.json'
            path.write_text(json.dumps(snapshot()), encoding='utf-8')
            doc = AC.load_appearance_snapshot(path)
            by_ref = AC.appearance_by_ref(doc)
            self.assertIn('SlimeDefinition.Ringtail', by_ref)

    def test_rejects_wrong_schema(self):
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(schema='bridge-diet-snapshot'))

    def test_rejects_unknown_save_set(self):
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(entries=[entry(save_set='FANCY')]))

    def test_rejects_wrong_palette_length(self):
        for field in ('palette_legacy', 'palette_new'):
            bad = copy.deepcopy(entry())
            bad[field] = bad[field][:12]
            with self.assertRaises(AC.AppearanceCaptureError):
                AC.validate_appearance_snapshot(snapshot(entries=[bad]))

    def test_rejects_non_finite_and_non_numeric_components(self):
        for value in (float('nan'), float('inf'), '0.5', None, True):
            bad = copy.deepcopy(entry())
            bad['palette_legacy'][0] = value
            with self.assertRaises(AC.AppearanceCaptureError):
                AC.validate_appearance_snapshot(snapshot(entries=[bad]))

    def test_allows_missing_new_palette(self):
        doc = snapshot(entries=[entry(palette_new=None)])
        AC.validate_appearance_snapshot(doc)

    def test_rejects_duplicate_refs_and_empty_entries(self):
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(entries=[entry(), entry()]))
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(entries=[]))

    def test_rejects_bad_run_id_and_sha(self):
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(run_id='short'))
        with self.assertRaises(AC.AppearanceCaptureError):
            AC.validate_appearance_snapshot(snapshot(active_sha256='xyz'))


if __name__ == '__main__':
    unittest.main()
