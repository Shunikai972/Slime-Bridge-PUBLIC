import json
import struct
import tempfile
import unittest
from pathlib import Path

from bridge.content import user_largos


def _cache(folder: Path, canonical: str, source_game='USER', top=(0.1, 0.2, 0.3, 1.0)):
    folder.mkdir(parents=True, exist_ok=True)
    model = {'skinned_renderers': [{'role': 'body', 'materials': [{'colors': {'_TopColor': list(top), '_MiddleColor': [0.4, 0.5, 0.6, 1.0],
                                                                                  '_BottomColor': [0.7, 0.8, 0.9, 1.0]}}]}]}
    (folder / 'model.json').write_text(json.dumps(model), encoding='utf-8')
    (folder / 'cache-manifest.json').write_text(json.dumps({'schema': 'bridge-asset-cache-manifest', 'schema_version': 1,
                                                             'canonical_id': canonical, 'source_game': source_game, 'files': {}}), encoding='utf-8')


class UserLargoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.models = self.root / 'models'
        self.caches = self.root / 'AssetCache'
        model = self.models / 'rad_cotton'
        _cache(model / 'cache', 'largo.cotton.rad')
        (model / 'largo.json').write_text(json.dumps({'id': 'rad_cotton', 'canonical': 'largo.cotton.rad'}), encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def test_sync_puts_the_model_in_both_games_and_keeps_an_extracted_cache_aside(self):
        _cache(self.caches / 'sr2-to-sr1' / 'largo.cotton.rad', 'largo.cotton.rad', source_game='SR2')
        out = user_largos.sync(self.models, self.caches)
        self.assertEqual(sorted(out['installed']), ['sr1-to-sr2/largo.cotton.rad', 'sr2-to-sr1/largo.cotton.rad'])
        for direction in user_largos.DIRECTIONS:
            self.assertTrue(user_largos.is_user_cache(self.caches / direction / 'largo.cotton.rad'))
        self.assertTrue((self.caches / 'sr2-to-sr1' / 'largo.cotton.rad-native').is_dir())
        self.assertEqual(user_largos.sync(self.models, self.caches)['installed'], [], 'idempotent')
        # removing the model gives the extracted cache back
        import shutil
        shutil.rmtree(self.models / 'rad_cotton')
        user_largos.sync(self.models, self.caches)
        self.assertFalse(user_largos.is_user_cache(self.caches / 'sr2-to-sr1' / 'largo.cotton.rad'))
        self.assertFalse((self.caches / 'sr1-to-sr2' / 'largo.cotton.rad').exists())

    def test_sr1_entry_uses_the_family_largo_and_the_measured_palette(self):
        user_largos.sync(self.models, self.caches)
        self.assertEqual(user_largos.user_canonicals(self.caches, 'sr2-to-sr1'), ['largo.cotton.rad'])
        entry = {'canonical_id': 'largo.cotton.rad', 'template': {'slime_native_ref': 'PINK_ROCK_LARGO'}, 'flags': {}}
        entry = user_largos.adapt_sr1_entry(entry, self.caches)
        self.assertEqual(entry['template']['slime_native_ref'], 'PINK_RAD_LARGO')
        self.assertEqual(entry['appearance']['palette']['top'], [0.1, 0.2, 0.3, 1.0])
        self.assertEqual(user_largos.row('largo.cotton.rad')['parents'], ['cotton', 'rad'])


class GlbScrubTests(unittest.TestCase):
    def test_local_paths_in_gltf_extras_are_dropped_and_the_binary_chunk_kept(self):
        from tools.release import models_branch
        doc = {'asset': {'version': '2.0', 'generator': 'x', 'extras': {'src': 'D:/' + 'Noam/Downloads/a.blend'}}, 'nodes': [{'name': 'BODY'}]}
        text = json.dumps(doc).encode()
        text += b' ' * ((4 - len(text) % 4) % 4)
        binary = b'\x01\x02\x03\x04'
        glb = struct.pack('<III', 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(binary)) + struct.pack('<II', len(text), 0x4E4F534A) + text + \
            struct.pack('<II', len(binary), 0x004E4942) + binary
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp, 'a.glb'), Path(tmp, 'b.glb')
            src.write_bytes(glb)
            self.assertEqual(models_branch.clean_glb(src, dst), 1)
            out = dst.read_bytes()
            length = struct.unpack_from('<I', out, 12)[0]
            cleaned = json.loads(out[20:20 + length])
            self.assertEqual(cleaned['asset']['extras'], {})
            self.assertEqual(cleaned['nodes'], [{'name': 'BODY'}])
            self.assertTrue(out.endswith(binary))
            self.assertEqual(struct.unpack_from('<I', out, 8)[0], len(out))


if __name__ == '__main__':
    unittest.main()


class SpecFieldTests(unittest.TestCase):
    def test_user_largo_entries_only_use_fields_the_plugins_accept(self):
        import re
        source = (Path(__file__).resolve().parents[2] / 'mods' / 'shared' / 'ImportedSpeciesSpec.cs').read_text(encoding='utf-8')
        line = next(l for l in source.splitlines() if '"native_ref_id", "plort", "template"' in l)
        start = source.index(line)
        block = source[source.rfind('{', 0, start):source.index('}', start)]
        allowed = set(re.findall(r'"([a-z_]+)"', block))
        from bridge.content import largos
        with tempfile.TemporaryDirectory() as tmp:
            entry = largos.largo_entry(user_largos.row('largo.cotton.rad'), 960500)
            entry = user_largos.adapt_sr1_entry(entry, Path(tmp))
        self.assertTrue(set(entry) <= allowed, set(entry) - allowed)
