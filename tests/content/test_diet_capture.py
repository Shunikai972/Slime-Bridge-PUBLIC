"""Tests for the additive bridge-diet-snapshot reader (LAB read-only capture)."""

import json
import tempfile
import unittest
from pathlib import Path

from bridge.content import diet_capture as DC


def entry(**overrides):
    base = {
        'native_ref': 'RAD_SLIME',
        'numeric_id': 21,
        'display_name': 'Rad',
        'is_largo': False,
        'can_largofy': True,
        'major_food_groups': ['FRUIT'],
        'favorites': ['OCA_OCA_FRUIT'],
        'favorite_production_count': 2,
        'produces': ['RAD_PLORT'],
        'additional_foods': [],
    }
    base.update(overrides)
    return base


def snapshot(**overrides):
    base = {
        'schema': DC.SCHEMA,
        'schema_version': DC.SCHEMA_VERSION,
        'realm': 'SR1',
        'run_id': 'registry-20261007-215426-0dd642482a0c',
        'collector_version': '2',
        'active_sha256': 'a' * 64,
        'entries': [entry()],
        'errors': [],
    }
    base.update(overrides)
    return base


class DietSnapshotTests(unittest.TestCase):
    def _load(self, doc):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / DC.FILE_NAME
            path.write_text(json.dumps(doc), encoding='utf-8')
            return DC.load_diet_snapshot(path)

    def test_valid_snapshot_roundtrip_and_index(self):
        doc = self._load(snapshot())
        self.assertEqual(DC.diet_by_ref(doc)['RAD_SLIME']['display_name'], 'Rad')

    def test_schema_identity_required(self):
        for bad in (snapshot(schema='other'), snapshot(schema_version=2), snapshot(realm='SR3'),
                    snapshot(run_id='short'), snapshot(active_sha256='xyz'), snapshot(entries=[]),
                    snapshot(errors=None)):
            with self.assertRaises(DC.DietCaptureError, msg=bad):
                self._load(bad)

    def test_duplicate_native_ref_refused(self):
        with self.assertRaises(DC.DietCaptureError):
            self._load(snapshot(entries=[entry(), entry()]))

    def test_entry_fields_validated(self):
        bads = [
            entry(native_ref=''), entry(display_name=3), entry(is_largo='no'),
            entry(major_food_groups='FRUIT'), entry(major_food_groups=[1]),
            entry(favorites=None), entry(produces=[None]), entry(additional_foods='{}'),
            entry(favorite_production_count=-1), entry(favorite_production_count='2'),
        ]
        for bad in bads:
            with self.assertRaises(DC.DietCaptureError, msg=bad):
                self._load(snapshot(entries=[bad]))

    def test_realm_specific_numeric_id_rules(self):
        self._load(snapshot(entries=[entry(numeric_id=None)]))
        with self.assertRaises(DC.DietCaptureError):
            self._load(snapshot(realm='SR2', entries=[entry(numeric_id=21)]))
        doc = self._load(snapshot(realm='SR2', entries=[entry(numeric_id=None)]))
        self.assertEqual(doc['realm'], 'SR2')

    def test_unreadable_and_invalid_json_fail_closed(self):
        with self.assertRaises(DC.DietCaptureError):
            DC.load_diet_snapshot(Path('missing-diet-snapshot.json'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / DC.FILE_NAME
            path.write_text('{not json', encoding='utf-8')
            with self.assertRaises(DC.DietCaptureError):
                DC.load_diet_snapshot(path)


if __name__ == '__main__':
    unittest.main()
