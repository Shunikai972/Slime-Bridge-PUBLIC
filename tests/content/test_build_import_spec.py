"""Unit coverage for the imported-species spec builder (bridge-imported-species-spec v1).

All inputs are synthetic documents mirroring the reviewed capture shapes; nothing here reads the
LAB. Pins: roster-derived entries only, semantic group mapping across realms, crosswalk notes,
stable reserved native ids (including reuse from a previous spec), deterministic regeneration,
statuses fail closed to PENDING_DATA, and the output-dir guard.
"""

import copy
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest import mock

from bridge.content import diet_capture as DC
from tools.content import build_import_spec as BI

H = 'a' * 64
G_FRUIT = 'cbc5186d760b70b4aa60b2b5d3ca288f'
G_VEGGIES = 'e38eda722d35407448074f612639533e'
G_MEAT = '1afddc69e46b5274a9cfd80d52f2b996'


def _base(ref, display, produces, numeric_id=None):
    return {'native_ref': ref, 'numeric_id': numeric_id, 'role': 'base', 'definition_hash': H,
            'display_name': display, 'produces': produces, 'quarantine': None, 'malformed': False}


def classification_fixture():
    pink = {'canonical_id': 'slime.pink', 'classification': 'SHARED_NATIVE', 'reasons': [],
            'present': {'SR1': 1, 'SR2': 1},
            'native': {'SR1': _base('PINK_SLIME', 'Pink', ['plort.pink'], 4),
                       'SR2': _base('SlimeDefinition.Pink', 'Pink', ['plort.pink'])},
            'identity_basis': {'SR1': 'DERIVED', 'SR2': 'DERIVED'},
            'identity_evidence': {'SR1': ['slug-v1:PINK_SLIME'], 'SR2': ['slug-v1:SlimeDefinition.Pink']}}
    rad = {'canonical_id': 'slime.rad', 'classification': 'SR1_ONLY', 'reasons': [],
           'present': {'SR1': 1, 'SR2': 0},
           'native': {'SR1': _base('RAD_SLIME', 'Rad', ['plort.rad'], 1), 'SR2': None},
           'identity_basis': {'SR1': 'DERIVED'}, 'identity_evidence': {'SR1': ['slug-v1:RAD_SLIME']}}
    ringtail = {'canonical_id': 'slime.ringtail', 'classification': 'SR2_ONLY', 'reasons': [],
                'present': {'SR1': 0, 'SR2': 1},
                'native': {'SR1': None, 'SR2': _base('SlimeDefinition.Ringtail', 'Ringtail', ['plort.ringtail'])},
                'identity_basis': {'SR2': 'DERIVED'},
                'identity_evidence': {'SR2': ['slug-v1:SlimeDefinition.Ringtail']}}
    return {'schema': 'bridge-species-classification', 'schema_version': 1,
            'species': [pink, rad, ringtail], 'counts': {'SHARED_NATIVE': 1, 'SR1_ONLY': 1, 'SR2_ONLY': 1},
            '_path': 'synthetic'}


def diet_doc(realm, entries):
    return {'schema': DC.SCHEMA, 'schema_version': DC.SCHEMA_VERSION, 'realm': realm,
            'run_id': 'registry-20261007-000000-abcdefabcdef', 'collector_version': '2',
            'active_sha256': H, 'entries': entries, 'errors': [], '_path': f'synthetic-{realm}'}


def diet_entry(ref, groups, members, favorites, produces, can_largofy=True):
    return {'native_ref': ref, 'numeric_id': None, 'display_name': ref, 'is_largo': False,
            'can_largofy': can_largofy, 'major_food_groups': groups,
            'major_food_group_members': [{'group': g, 'members': members.get(g, [])} for g in groups],
            'favorites': favorites, 'favorite_production_count': 2, 'produces': produces,
            'additional_foods': []}


def diet_fixtures():
    sr1 = diet_doc('SR1', [diet_entry('RAD_SLIME', ['VEGGIES'], {'VEGGIES': ['OCAOCA_VEGGIE']},
                                      ['OCA_OCA_VEGGIE'], ['RAD_PLORT'])])
    members = {G_FRUIT: ['IdentifiableType.CuberryFruit', 'IdentifiableType.PomegraniteFruit'],
               G_VEGGIES: ['IdentifiableType.BeetVeggie'], G_MEAT: ['IdentifiableType.StonyHen']}
    sr2 = diet_doc('SR2', [diet_entry('SlimeDefinition.Ringtail', [G_FRUIT, G_VEGGIES, G_MEAT], members,
                                      ['IdentifiableType.PomegraniteFruit'], ['IdentifiableType.RingtailPlort'])])
    return sr1, sr2


def appearance_doc(realm, entries):
    return {'schema': 'bridge-appearance-colors', 'schema_version': 1, 'realm': realm,
            'run_id': 'registry-20261008-000000-abcdefabcdef', 'collector_version': '2',
            'active_sha256': H, 'entries': entries, 'errors': [], '_path': f'synthetic-appearance-{realm}'}


def appearance_entry(ref, legacy, new=None, save_set='CLASSIC'):
    return {'native_ref': ref, 'display_name': ref, 'save_set': save_set,
            'palette_legacy': legacy, 'palette_new': new}


class ImportSpecBuilderTests(unittest.TestCase):
    def build(self, previous=None, sr2_app=None, sr1_app=None):
        sr1, sr2 = diet_fixtures()
        sr1_spec, _ = BI.build_spec('SR1', classification_fixture(), sr1, sr2, previous or {},
                                    sr1_app=sr1_app, sr2_app=sr2_app)
        sr2_spec, _ = BI.build_spec('SR2', classification_fixture(), sr1, sr2, previous or {},
                                    sr1_app=sr1_app, sr2_app=sr2_app)
        return sr1_spec, sr2_spec

    def test_roster_derived_and_shared_excluded(self):
        sr1_spec, sr2_spec = self.build()
        self.assertEqual([e['canonical_id'] for e in sr1_spec['entries']], ['slime.ringtail'])
        self.assertEqual([e['canonical_id'] for e in sr2_spec['entries']], ['slime.rad'])

    def test_ringtail_entry_semantics_and_plort(self):
        sr1_spec, _ = self.build()
        entry = sr1_spec['entries'][0]
        self.assertEqual(entry['status'], 'READY')
        self.assertEqual(entry['diet']['major_food_groups'], ['FRUIT', 'MEAT', 'VEGGIES'])
        self.assertEqual(entry['plort']['canonical_id'], 'plort.ringtail')
        self.assertEqual(entry['template']['slime_native_ref'], 'TABBY_SLIME')
        self.assertFalse(entry['flags']['can_largofy'])
        self.assertIn('largos reserved', entry['blockers'][0])

    def test_rad_entry_maps_groups_and_notes_dropped_favorites(self):
        _, sr2_spec = self.build()
        entry = sr2_spec['entries'][0]
        self.assertEqual(entry['status'], 'READY')
        self.assertEqual(entry['diet']['major_food_groups'], [G_VEGGIES])
        # OCA_OCA_VEGGIE normalizes to ocaocaveggie; the SR2 vocabulary has no match -> note only.
        self.assertTrue(any('favorites without a target crosswalk' in note for note in entry['notes']))
        self.assertEqual(entry['plort']['canonical_id'], 'plort.rad')

    def test_native_ids_unique_reserved_and_stable(self):
        sr1_spec, _ = self.build()
        entry = sr1_spec['entries'][0]
        self.assertGreaterEqual(entry['native_id'], BI.NATIVE_ID_BASE)
        self.assertEqual(entry['plort']['native_id'], entry['native_id'] + 1)
        again, _ = self.build()
        self.assertEqual(json.dumps(sr1_spec, sort_keys=True), json.dumps(again, sort_keys=True))
        # The committed table (bridge/content/species_ids.json) decides before any previous spec: every install agrees.
        previous = {'slime.ringtail': (920000, 920001)}
        reused, _ = self.build(previous)
        self.assertEqual(reused['entries'][0]['native_id'], BI.committed_species_ids('SR1')['slime.ringtail'][0])
        # A species the table does not know keeps the id its previous spec gave it.
        with unittest.mock.patch.object(BI, 'committed_species_ids', return_value={}):
            reused, _ = self.build(previous)
        self.assertEqual(reused['entries'][0]['native_id'], 920000)

    def test_unmappable_source_fails_closed_to_pending(self):
        sr1, sr2 = diet_fixtures()
        sr2['entries'][0]['major_food_groups'] = ['deadbeef' * 4]
        sr2['entries'][0]['major_food_group_members'] = [{'group': 'deadbeef' * 4, 'members': []}]
        spec, _ = BI.build_spec('SR1', classification_fixture(), sr1, sr2, {})
        entry = spec['entries'][0]
        self.assertEqual(entry['status'], 'PENDING_DATA')
        self.assertTrue(any('canonical semantics' in problem for problem in entry['blockers']))

    def test_empty_source_diet_fails_closed(self):
        # SR1-004: a captured empty diet must never be READY (it would register without a native
        # EatMap while claiming functioning diet behavior).
        sr1, sr2 = diet_fixtures()
        sr2['entries'][0]['major_food_groups'] = []
        sr2['entries'][0]['major_food_group_members'] = []
        spec, _ = BI.build_spec('SR1', classification_fixture(), sr1, sr2, {})
        entry = spec['entries'][0]
        self.assertEqual(entry['status'], 'PENDING_DATA')
        self.assertTrue(any('no captured food groups' in problem for problem in entry['blockers']))

    def test_missing_diet_entry_fails_closed(self):
        sr1, sr2 = diet_fixtures()
        sr2['entries'] = []
        spec, _ = BI.build_spec('SR1', classification_fixture(), sr1, sr2, {})
        self.assertEqual(spec['entries'][0]['status'], 'PENDING_DATA')
        self.assertTrue(any('diet capture has no entry' in problem for problem in spec['entries'][0]['blockers']))

    def test_appearance_palette_embedded_from_source_capture(self):
        legacy = [0.4, 0.3, 0.2, 1.0] * 4
        new = [0.48, 0.40, 0.34, 1.0] * 4
        app = appearance_doc('SR2', [appearance_entry('SlimeDefinition.Ringtail', legacy, new)])
        sr1_spec, _ = self.build(sr2_app=app)
        block = sr1_spec['entries'][0]['appearance']
        self.assertEqual(block['palette']['top'], new[0:4])
        self.assertEqual(block['palette']['middle'], new[4:8])
        self.assertEqual(block['palette']['bottom'], new[8:12])
        self.assertEqual(block['ammo'], new[12:16])
        self.assertEqual(block['save_set'], 'CLASSIC')
        self.assertEqual(block['source'], 'new:classic')

    def test_appearance_prefers_legacy_when_new_missing(self):
        legacy = [0.1, 0.2, 0.3, 1.0] * 4
        app = appearance_doc('SR2', [appearance_entry('SlimeDefinition.Ringtail', legacy, None)])
        sr1_spec, _ = self.build(sr2_app=app)
        block = sr1_spec['entries'][0]['appearance']
        self.assertEqual(block['palette']['top'], legacy[0:4])
        self.assertEqual(block['source'], 'legacy:classic')

    def test_appearance_missing_ref_notes_but_stays_ready(self):
        app = appearance_doc('SR2', [appearance_entry('SlimeDefinition.Pink', [0.5] * 4 * 4)])
        sr1_spec, _ = self.build(sr2_app=app)
        entry = sr1_spec['entries'][0]
        self.assertEqual(entry['status'], 'READY')
        self.assertNotIn('appearance', entry)
        self.assertTrue(any('appearance capture has no entry' in note for note in entry['notes']))

    def test_appearance_absent_without_source_capture(self):
        sr1_spec, sr2_spec = self.build()
        self.assertNotIn('appearance', sr1_spec['entries'][0])
        self.assertNotIn('appearance', sr2_spec['entries'][0])

    def test_main_writes_outputs_and_guards_out_dir(self):
        sr1, sr2 = diet_fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sr1_path = root / 'sr1-diet.json'
            sr2_path = root / 'sr2-diet.json'
            sr1_path.write_text(json.dumps(sr1), encoding='utf-8')
            sr2_path.write_text(json.dumps(sr2), encoding='utf-8')
            class_dir = root / 'classification'
            class_dir.mkdir()
            fixture = classification_fixture()
            fixture.pop('_path')
            (class_dir / 'classification.json').write_text(json.dumps(fixture), encoding='utf-8')
            out_dir = root / 'generated'
            with mock.patch.object(BI, 'OUTPUT_ROOT', root):
                rc = BI.main(['--classification', str(class_dir), '--sr1-diet', str(sr1_path),
                              '--sr2-diet', str(sr2_path), '--out-dir', str(out_dir)])
            self.assertEqual(rc, 0)
            spec = json.loads((out_dir / 'import-spec-SR1.json').read_text(encoding='utf-8'))
            self.assertEqual(spec['schema'], BI.SCHEMA)
            summary = json.loads((out_dir / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['realms']['SR1']['ready'], 1)
            with mock.patch.object(BI, 'OUTPUT_ROOT', root):
                with self.assertRaises(SystemExit):
                    BI.main(['--classification', str(class_dir), '--sr1-diet', str(sr1_path),
                             '--sr2-diet', str(sr2_path), '--out-dir', str(root.parent / 'elsewhere')])


if __name__ == '__main__':
    unittest.main()
