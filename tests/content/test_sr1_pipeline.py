import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.content import sr1_pipeline as pipeline


def spec_dir(root):
    folder = Path(root) / 'specs' / '20261001-000000-r2'
    folder.mkdir(parents=True)
    entries = [
        {'canonical_id': 'slime.mosaic', 'status': 'READY', 'diet': {'major_food_groups': ['g1']}, 'blockers': []},
        {'canonical_id': 'slime.glitch', 'status': 'READY', 'diet': {'major_food_groups': []}, 'blockers': []},
    ]
    (folder / 'import-spec-SR2.json').write_text(json.dumps({'realm': 'SR2', 'schema': 'bridge-imported-species-spec', 'schema_version': 1,
                                                             'entries': entries}), encoding='utf-8')
    return folder.parent


def cache(root, canonical):
    folder = Path(root) / 'caches' / 'sr1-to-sr2' / canonical
    folder.mkdir(parents=True)
    for name in pipeline.COMPLETE_MARKERS:
        (folder / name).write_text('{}', encoding='utf-8')


class Sr1PipelineTests(unittest.TestCase):
    def test_roster_lists_what_sr2_lacks_with_canonical_parents(self):
        with mock.patch.object(pipeline, 'sr1_content', return_value=(['PINK', 'RAD', 'TARR'], ['PINK_RAD', 'BOOM_PINK'])), \
                mock.patch.object(pipeline, 'sr2_prefab_names', return_value={'slimePink', 'slimeBoomPink'}):
            rows = pipeline.roster(Path('x'), Path('y'))
        self.assertEqual([r['canonical'] for r in rows], ['slime.rad', 'largo.pink.rad'])
        self.assertEqual(rows[1]['parents'], ['pink', 'rad'])
        self.assertEqual(rows[1]['prefab'], 'slimePinkRad')

    def test_publish_spec_adds_cached_largos_and_holds_back_species_without_a_diet(self):
        with tempfile.TemporaryDirectory() as tmp:
            specs = spec_dir(tmp)
            for name in ('slime.mosaic', 'slime.glitch', 'largo.mosaic.rad'):
                cache(tmp, name)
            rows = [{'canonical': 'slime.mosaic', 'species': 'Mosaic', 'kind': 'base', 'parents': [], 'display_name': 'Mosaic'},
                    {'canonical': 'slime.glitch', 'species': 'Glitch', 'kind': 'base', 'parents': [], 'display_name': 'Glitch'},
                    {'canonical': 'largo.mosaic.rad', 'species': 'MosaicRad', 'kind': 'largo', 'parents': ['mosaic', 'rad'],
                     'display_name': 'Mosaic Rad Largo'},
                    {'canonical': 'largo.quantum.rad', 'species': 'QuantumRad', 'kind': 'largo', 'parents': ['quantum', 'rad'],
                     'display_name': 'Quantum Rad Largo'}]
            with mock.patch.object(pipeline, 'roster', return_value=rows):
                result = pipeline.publish_spec(Path(tmp) / 'caches', specs, Path(tmp) / 'ids.json')
            spec = json.loads((Path(result['directory']) / pipeline.SPEC_FILE).read_text(encoding='utf-8'))
        by_id = {e['canonical_id']: e for e in spec['entries']}
        self.assertEqual(by_id['slime.mosaic']['status'], 'READY')
        self.assertEqual(by_id['slime.glitch']['status'], 'PENDING_DATA')
        self.assertIn('largo.mosaic.rad', by_id)
        self.assertNotIn('largo.quantum.rad', by_id)            # no cache yet
        largo = by_id['largo.mosaic.rad']
        self.assertEqual(largo['largo']['parents'], ['mosaic', 'rad'])
        self.assertEqual(largo['template']['slime_native_ref'], pipeline.TEMPLATE_LARGO)
        self.assertTrue(900000 <= largo['native_id'] <= 999999)
        self.assertTrue(result['directory'].endswith('-sr1'))

    def test_refresh_needs_no_game_files_and_publishes_only_when_the_caches_changed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            specs = repo / 'Lab' / 'GeneratedContent' / 'species-import'
            specs.mkdir(parents=True)
            spec_dir(repo)                                              # writes <tmp>/specs; mirror it where refresh looks
            (specs / '20261001-000000-r2').mkdir()
            (specs / '20261001-000000-r2' / 'import-spec-SR2.json').write_text(
                (repo / 'specs' / '20261001-000000-r2' / 'import-spec-SR2.json').read_text(encoding='utf-8'), encoding='utf-8')
            folder = repo / 'Lab' / 'AssetCache' / 'sr1-to-sr2' / 'largo.mosaic.rad'
            folder.mkdir(parents=True)
            for name in pipeline.COMPLETE_MARKERS:
                (folder / name).write_text('{}', encoding='utf-8')
            first = pipeline.refresh_spec(repo)
            self.assertTrue(first['changed'])
            self.assertEqual(first['largos'], 1)
            again = pipeline.refresh_spec(repo)
            self.assertFalse(again['changed'])                          # the new spec already lists it
            self.assertEqual(pipeline.refresh_spec(repo / 'nowhere')['changed'], False)

    def test_a_row_over_the_memory_limit_is_skipped_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'SlimeRancher_Data' / 'Managed'
            data.mkdir(parents=True)
            (data / 'Assembly-CSharp.dll').write_bytes(b'x')
            with mock.patch('bridge.content.sr1_asset_extract.Sr1Assets', return_value=object()), \
                    mock.patch.object(pipeline, 'build_row', side_effect=MemoryError):
                outcome = pipeline.run([{'canonical': 'slime.rad', 'species': 'Rad'}], data.parent, Path(tmp), ram_gb=4)
        self.assertEqual(outcome['by_status'], {'SKIPPED_RAM': 1})


if __name__ == '__main__':
    unittest.main()
