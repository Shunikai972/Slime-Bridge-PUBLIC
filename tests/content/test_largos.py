import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.content import largos


def pair(a, b):
    return frozenset({a, b})


class RosterTests(unittest.TestCase):
    def roster(self, sr2, sr1_bases, sr1_pairs, imported):
        with mock.patch.object(largos, 'sr2_largos', return_value=sr2), \
                mock.patch.object(largos, 'sr1_species', return_value=(set(sr1_bases), set(sr1_pairs))), \
                mock.patch.object(largos, 'imported_species', return_value=set(imported)):
            return largos.largo_roster(Path('sr2'), Path('sr1'))

    def test_only_largos_sr1_lacks_are_listed(self):
        rows = self.roster({pair('pink', 'rock'): 'PinkRock', pair('angler', 'pink'): 'AnglerPink'},
                           ['pink', 'rock'], [pair('pink', 'rock')], ['angler'])
        self.assertEqual([r['canonical'] for r in rows], ['largo.angler.pink'])
        self.assertEqual(rows[0]['species'], 'AnglerPink')
        self.assertEqual(rows[0]['status'], 'READY')
        self.assertEqual(rows[0]['parents'], ['angler', 'pink'])
        self.assertEqual(rows[0]['display_name'], 'Angler Pink Largo')

    def test_a_missing_parent_species_blocks_the_largo(self):
        rows = self.roster({pair('flutter', 'pink'): 'FlutterPink'}, ['pink'], [], [])
        self.assertEqual(rows[0]['status'], 'PENDING_DATA')
        self.assertIn('flutter', rows[0]['blockers'][0])

    def test_canonical_ids_sort_their_parents(self):
        rows = self.roster({pair('sloomber', 'boom'): 'BoomSloomber'}, ['boom'], [], ['sloomber'])
        self.assertEqual(rows[0]['canonical'], 'largo.boom.sloomber')


class IdTests(unittest.TestCase):
    def test_ids_are_stable_and_not_reused_beyond_the_shared_table(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(largos, 'ID_TABLE', Path(tmp) / 'none.json'):
            ledger = Path(tmp) / 'ids.json'
            first = largos.allocate_ids(['largo.b.c', 'largo.a.b'], ledger)
            self.assertEqual(first, {'largo.b.c': largos.LARGO_ID_BASE + 1, 'largo.a.b': largos.LARGO_ID_BASE})
            second = largos.allocate_ids(['largo.a.b', 'largo.c.d', 'largo.b.c'], ledger)
            self.assertEqual(second['largo.a.b'], largos.LARGO_ID_BASE)
            self.assertEqual(second['largo.b.c'], largos.LARGO_ID_BASE + 1)
            self.assertEqual(second['largo.c.d'], largos.LARGO_ID_BASE + 2)

    def test_the_shared_table_decides_whatever_a_local_ledger_says(self):
        table = largos.shared_ids()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'ids.json'
            ledger.write_text('{"largo.angler.batty": 999999}', encoding='utf-8')
            got = largos.allocate_ids(['largo.angler.batty', 'largo.mosaic.rad'], ledger)
        self.assertEqual(got, {'largo.angler.batty': table['largo.angler.batty'], 'largo.mosaic.rad': table['largo.mosaic.rad']})

    def test_the_shared_table_is_consistent(self):
        doc = json.loads(largos.ID_TABLE.read_text(encoding='utf-8'))
        ids = doc['ids']
        self.assertEqual(len(set(ids.values())), len(ids))                       # no two Largos share an id
        self.assertTrue(all(largos.LARGO_ID_BASE <= v <= largos.LARGO_ID_LIMIT for v in ids.values()))
        for canonical in ids:
            _, a, b = canonical.split('.')
            self.assertLess(a, b, canonical)                                      # canonical names sort their parents
        species = doc['species']
        for i, a in enumerate(species):
            for b in species[i + 1:]:
                self.assertIn('largo.%s.%s' % tuple(sorted((a, b))), ids)         # every pair of the listed species has an id


class SpecTests(unittest.TestCase):
    def test_spec_lists_only_extracted_largos_and_keeps_species(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            species_dir = root / 'species-import' / '20261008-r2'
            species_dir.mkdir(parents=True)
            base = {'schema': 'bridge-imported-species-spec', 'schema_version': 1, 'realm': 'SR1',
                    'source_classification_sha256': 'x', 'source_diet_sha256': 'y',
                    'entries': [{'canonical_id': 'slime.angler', 'status': 'READY'}]}
            (species_dir / largos.SPEC_FILE).write_text(json.dumps(base), encoding='utf-8')
            cache = root / 'cache'
            (cache / 'sr2-to-sr1' / 'largo.angler.pink').mkdir(parents=True)
            for name in ('cache-manifest.json', 'model.json'):
                (cache / 'sr2-to-sr1' / 'largo.angler.pink' / name).write_text('{}', encoding='utf-8')
            rows = [
                {'canonical': 'largo.angler.pink', 'species': 'AnglerPink', 'kind': 'largo', 'parents': ['angler', 'pink'],
                 'display_name': 'Angler Pink Largo', 'status': 'READY', 'blockers': []},
                {'canonical': 'largo.angler.rock', 'species': 'AnglerRock', 'kind': 'largo', 'parents': ['angler', 'rock'],
                 'display_name': 'Angler Rock Largo', 'status': 'READY', 'blockers': []},
            ]
            with mock.patch.object(largos, 'SPEC_ROOT', root / 'species-import'), \
                    mock.patch.object(largos, 'largo_roster', return_value=rows):
                out = largos.publish_spec(cache, Path('sr2'), Path('sr1'), root / 'species-import', root / 'ids.json')
            self.assertEqual(out['largos'], 1)
            spec = json.loads((Path(out['directory']) / largos.SPEC_FILE).read_text(encoding='utf-8'))
            ids = [e['canonical_id'] for e in spec['entries']]
            self.assertEqual(ids, ['slime.angler', 'largo.angler.pink'])
            entry = spec['entries'][1]
            self.assertEqual(entry['largo']['parents'], ['angler', 'pink'])
            self.assertEqual(entry['plort']['canonical_id'], '')
            self.assertEqual(entry['template']['slime_native_ref'], largos.TEMPLATE_LARGO)
            self.assertTrue(Path(out['directory']).name.endswith('-largos'))


class PaletteTests(unittest.TestCase):
    def cache(self, root, canonical, body_colors):
        folder = Path(root) / 'sr2-to-sr1' / canonical
        folder.mkdir(parents=True)
        model = {'skinned_renderers': [
            {'path': 'slimeX/face_slime_face_LOD0', 'materials': [{'colors': {}}]},
            {'path': 'slimeX/body_slime_default_v4_LOD0', 'materials': [{'colors': body_colors}]}]}
        (folder / 'model.json').write_text(json.dumps(model), encoding='utf-8')

    def test_the_body_colors_become_the_spec_palette_so_splats_are_not_the_placeholders(self):
        colors = {'_TopColor': [1, 0, 0, 1], '_MiddleColor': [0, 1, 0, 1], '_BottomColor': [0, 0, 1, 1]}
        with tempfile.TemporaryDirectory() as tmp:
            self.cache(tmp, 'largo.a.b', colors)
            palette = largos.cache_palette('largo.a.b', Path(tmp))
            self.assertEqual(palette['palette'], {'top': [1, 0, 0, 1], 'middle': [0, 1, 0, 1], 'bottom': [0, 0, 1, 1]})
            self.assertEqual(palette['ammo'], [0, 1, 0, 1])
            self.assertIsNone(largos.cache_palette('largo.c.d', Path(tmp)))               # no cache: nothing invented
            self.cache(tmp, 'largo.e.f', {'_TopColor': [1, 0, 0, 1]})
            self.assertIsNone(largos.cache_palette('largo.e.f', Path(tmp)))               # incomplete colors are refused


if __name__ == '__main__':
    unittest.main()