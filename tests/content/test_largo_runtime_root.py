"""Largo publication must use the requested runtime home, not module-global data."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bridge.content import largos
from apps.launcher import pack


class RuntimeRootTests(unittest.TestCase):
    def test_refresh_and_player_pointer_keep_twin_from_requested_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            foreign = root / 'foreign/Lab/GeneratedContent/species-import'
            foreign_base = foreign / '20990101-r1' / largos.SPEC_FILE
            foreign_base.parent.mkdir(parents=True)
            foreign_base.write_text(json.dumps({'owner': 'foreign', 'entries': [
                {'canonical_id': 'slime.angler', 'status': 'READY'}]}), encoding='utf-8')
            foreign_hash = hashlib.sha256(foreign_base.read_bytes()).hexdigest()
            home = root / 'requested'
            specs = home / 'Lab/GeneratedContent/species-import'
            base = specs / '20260101-r1' / largos.SPEC_FILE
            base.parent.mkdir(parents=True)
            species = {'canonical_id': 'slime.twin', 'status': 'READY', 'native_id': 900071}
            base.write_text(json.dumps({'owner': 'requested', 'entries': [species]}), encoding='utf-8')
            cache = home / 'Lab/AssetCache/sr2-to-sr1/largo.pink.twin'
            cache.mkdir(parents=True)
            for name in ('cache-manifest.json', 'model.json'):
                (cache / name).write_text('{}', encoding='utf-8')
            sr2 = home / 'sr2'
            (sr2 / largos.CATALOG).parent.mkdir(parents=True)
            (sr2 / largos.CATALOG).write_text('{}', encoding='utf-8')
            sr1 = home / 'sr1'
            (sr1 / 'Managed').mkdir(parents=True)
            (sr1 / 'Managed/Assembly-CSharp.dll').write_bytes(b'inert external-enum fixture')
            with mock.patch.object(largos, 'SPEC_ROOT', foreign), \
                    mock.patch.object(largos, 'sr2_largos', return_value={frozenset(('pink', 'twin')): 'PinkTwin'}), \
                    mock.patch.object(largos, 'sr1_species', return_value=({'pink'}, set())):
                result = largos.refresh_spec(home, sr2, sr1)
                self.assertTrue(result['changed'])
                self.assertEqual(result['largos'], 1)
                selected = Path(result['directory']) / largos.SPEC_FILE
                doc = json.loads(selected.read_text(encoding='utf-8'))
                self.assertEqual(doc['owner'], 'requested')
                self.assertEqual(doc['entries'][0], species)
                twin = next(e for e in doc['entries'] if e['canonical_id'] == 'largo.pink.twin')
                self.assertEqual(twin['native_id'], largos.shared_ids()['largo.pink.twin'])
                self.assertEqual(largos.refresh_spec(home, sr2, sr1), {'changed': False, 'largos': 1})
                # Exercise the actual normal-install pointer producer against a disposable folder.
                pointer = pack.write_player_pointer('SR1', home / 'fake-game', home)
                assert pointer is not None
                payload = json.loads(pointer.read_text(encoding='utf-8'))
                self.assertEqual(Path(payload['home']), home.resolve())
                self.assertEqual(Path(payload['spec']), selected.resolve())
            self.assertEqual(hashlib.sha256(foreign_base.read_bytes()).hexdigest(), foreign_hash)
            self.assertEqual(list(foreign.glob('*/' + largos.SPEC_FILE)), [foreign_base])


if __name__ == '__main__':
    unittest.main()
