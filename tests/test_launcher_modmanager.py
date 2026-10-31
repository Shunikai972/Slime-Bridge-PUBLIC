import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from apps.launcher import modmanager as mm


def _zip(tmp, name, members):
    path = Path(tmp) / name
    with zipfile.ZipFile(path, 'w') as zf:
        for member, data in members.items():
            zf.writestr(member, data)
    return path


class ModManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'game'
        (self.root / 'Mods').mkdir(parents=True)
        (self.root / 'Mods' / 'SlimeBridge.SR2.dll').write_bytes(b'bridge')

    def tearDown(self):
        self.tmp.cleanup()

    def test_sr2_zip_with_a_dll_goes_to_mods_and_comes_out_again(self):
        z = _zip(self.tmp.name, 'MoreVaccables.zip', {'MoreVaccables/MoreVaccables.dll': b'x'})
        rec = mm.install_file('SR2', self.root, z, {'name': 'More Vaccables', 'nexus_id': 42, 'version': '1.3.4'})
        self.assertEqual(rec['files'], ['Mods/MoreVaccables.dll'])
        listed = {m['name']: m for m in mm.installed('SR2', self.root)}
        self.assertIn('More Vaccables', listed)
        self.assertTrue(listed['SlimeBridge.SR2']['protected'])
        out = mm.remove('SR2', self.root, '42')
        self.assertEqual(out['removed'], ['Mods/MoreVaccables.dll'])
        self.assertFalse((self.root / 'Mods' / 'MoreVaccables.dll').exists())
        self.assertTrue(Path(out['kept_in'], 'Mods', 'MoreVaccables.dll').is_file(), 'removed mods are kept, never deleted')

    def test_archives_that_mirror_the_game_layout_keep_it(self):
        z = _zip(self.tmp.name, 'pack.zip', {'Mods/A.dll': b'a', 'UserLibs/Lib.dll': b'l'})
        rec = mm.install_file('SR2', self.root, z)
        self.assertEqual(rec['files'], ['Mods/A.dll', 'UserLibs/Lib.dll'])

    def test_sr1_mod_gets_its_own_folder_under_srml_mods(self):
        z = _zip(self.tmp.name, 'Tarr.zip', {'TarrRancher.dll': b't', 'readme.txt': b'r'})
        rec = mm.install_file('SR1', self.root, z)
        self.assertIn('SRML/Mods/TarrRancher/TarrRancher.dll', rec['files'])

    def test_protected_and_unsafe_files_are_refused(self):
        with self.assertRaises(mm.ModError):
            mm.remove('SR2', self.root, 'file:Mods/SlimeBridge.SR2.dll')
        z = _zip(self.tmp.name, 'evil.zip', {'../../evil.dll': b'e'})
        with self.assertRaises(mm.ModError) as cm:
            mm.install_file('SR2', self.root, z)
        self.assertEqual(cm.exception.code, 'BAD_ARCHIVE')

    def test_search_reads_the_nexus_catalogue(self):
        payload = {'data': {'mods': {'totalCount': 1, 'nodes': [{'modId': 42, 'name': 'More Vaccables', 'summary': 's', 'version': '1.3.4',
                   'author': 'A', 'endorsements': 1, 'downloads': 9, 'updatedAt': 'x', 'thumbnailUrl': None}]}}}

        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False
        seen = {}

        def opener(req, timeout=0):
            seen['body'] = json.loads(req.data)
            return R(json.dumps(payload).encode())
        out = mm.search('SR2', 'vacc', opener=opener)
        self.assertEqual(out['mods'][0]['url'], 'https://www.nexusmods.com/slimerancher2/mods/42')
        self.assertEqual(seen['body']['variables']['filter']['gameDomainName'][0]['value'], 'slimerancher2')


if __name__ == '__main__':
    unittest.main()
