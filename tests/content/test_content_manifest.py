import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from bridge.content import content_manifest as manifest
from tests.services.test_multiplayer import World


def species(canonical, native_id, plort_id=0, status='READY'):
    return {'canonical_id': canonical, 'display_name': canonical.title(), 'status': status, 'native_id': native_id,
            'plort': {'canonical_id': f'plort.{canonical.split(".")[1]}' if plort_id else '', 'native_id': plort_id}}


def largo(canonical, native_id, parents):
    entry = species(canonical, native_id)
    entry['largo'] = {'parents': parents, 'sr2_name': 'X'}
    return entry


SPEC = {'realm': 'SR1', 'entries': [species('slime.cotton', 900020, 900021), largo('largo.cotton.pink', 960040, ['cotton', 'pink']),
                                    species('slime.shadow', 900050, 900051, status='PENDING_DATA')]}


class ContentManifestTests(unittest.TestCase):
    def test_hash_is_deterministic_and_order_independent(self):
        shuffled = copy.deepcopy(SPEC)
        shuffled['entries'].reverse()
        first, second = manifest.from_spec(SPEC), manifest.from_spec(shuffled)
        self.assertEqual(first['manifest_hash'], second['manifest_hash'])
        self.assertRegex(first['manifest_hash'], r'^[0-9a-f]{64}$')

    def test_only_ready_entries_count_and_packages_name_them(self):
        result = manifest.from_spec(SPEC)
        self.assertEqual(result['required_packages'], ['generated:largo.cotton.pink:SR1', 'generated:slime.cotton:SR1'])

    def test_any_identity_change_changes_the_hash(self):
        base = manifest.from_spec(SPEC)['manifest_hash']
        for mutate in (lambda s: s['entries'][0].update(native_id=900022),
                       lambda s: s['entries'][1]['largo'].update(parents=['pink', 'cotton']),
                       lambda s: s['entries'].pop(1),
                       lambda s: s['entries'][2].update(status='READY')):
            changed = copy.deepcopy(SPEC)
            mutate(changed)
            self.assertNotEqual(manifest.from_spec(changed)['manifest_hash'], base)

    def test_realm_is_part_of_the_hash(self):
        other = dict(copy.deepcopy(SPEC), realm='SR2')
        self.assertNotEqual(manifest.from_spec(other)['manifest_hash'], manifest.from_spec(SPEC)['manifest_hash'])

    def test_newest_reads_the_spec_a_game_is_armed_with(self):
        with tempfile.TemporaryDirectory() as tmp:
            for stamp, spec in (('20261001-a', dict(SPEC, entries=SPEC['entries'][:1])), ('20261002-b', SPEC)):
                folder = Path(tmp) / stamp
                folder.mkdir()
                (folder / 'import-spec-SR1.json').write_text(json.dumps(spec), encoding='utf-8')
            self.assertEqual(manifest.newest('SR1', Path(tmp))['manifest_hash'], manifest.from_spec(SPEC)['manifest_hash'])
            self.assertIsNone(manifest.newest('SR2', Path(tmp)))
            self.assertEqual(set(manifest.hello_content(Path(tmp))), {'SR1'})

    def test_a_server_admits_the_same_content_and_restricts_a_different_one(self):
        host = manifest.from_spec(SPEC)['manifest_hash']
        missing = manifest.from_spec(dict(SPEC, entries=SPEC['entries'][:1]))['manifest_hash']
        world = World(required_content={'SR1': host})
        same = world.join('alice', 'SR1', content={'SR1': host})
        behind = world.join('bob', 'SR1', content={'SR1': missing})
        self.assertTrue(same.welcome['realms']['SR1']['allowed'])
        self.assertEqual(behind.welcome['realms']['SR1'], {'allowed': False, 'reason': 'CONTENT_INCOMPATIBLE'})


if __name__ == '__main__':
    unittest.main()
