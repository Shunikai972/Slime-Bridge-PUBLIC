import tempfile
import unittest
from pathlib import Path

from bridge.registry.content import ContentRegistry, UnverifiedNativeMapping


class RegistryTests(unittest.TestCase):
    def test_pink_seed_has_one_shared_canonical_identity_per_kind(self):
        seed = Path(__file__).resolve().parents[1] / 'bridge' / 'registry' / 'pink.json'
        registry = ContentRegistry.load(seed)
        self.assertEqual(set(registry.entries), {'slime.pink', 'plort.pink'})
        self.assertEqual(registry.resolve_native('SR1', 3).canonical_id, 'slime.pink')
        self.assertEqual(registry.resolve_native('SR1', 6).canonical_id, 'plort.pink')
        self.assertIsNone(registry.resolve_native('SR2', 'SlimeDefinition.Pink'))
        self.assertEqual(registry.resolve_native('SR2', 'SlimeDefinition.Pink', require_verified=False).canonical_id, 'slime.pink')
        with self.assertRaises(UnverifiedNativeMapping):
            registry.native_id('slime.pink', 'SR2')
        self.assertEqual(registry.native_id('slime.pink', 'SR1'), 3)
        self.assertIsNone(registry.resolve_native('SR2', 'SlimeDefinition.Unknown'))

    def test_duplicate_native_id_or_canonical_id_is_rejected(self):
        import json
        first = {'canonical_id': 'slime.pink', 'content_kind': 'slime',
                 'native': {'SR1': {'id': 3, 'verified': True}}}
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'registry.json'
            for second in ({**first, 'canonical_id': 'slime.other'}, first):
                file.write_text(json.dumps({'schema_version': 1, 'entries': [first, second]}))
                with self.assertRaises(ValueError):
                    ContentRegistry.load(file)

    def test_invalid_unverified_mapping_never_becomes_transferable(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'registry.json'
            file.write_text(json.dumps({'schema_version': 1, 'entries': [
                {'canonical_id': 'plort.pink', 'content_kind': 'plort',
                 'native': {'SR2': {'id': 'IdentifiableType.PinkPlort', 'verified': False}}}]}))
            registry = ContentRegistry.load(file)
            with self.assertRaises(UnverifiedNativeMapping):
                registry.native_id('plort.pink', 'SR2')


if __name__ == '__main__':
    unittest.main()
