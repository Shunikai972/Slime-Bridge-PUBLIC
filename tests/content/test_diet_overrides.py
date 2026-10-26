import json
import tempfile
import unittest
from pathlib import Path

from bridge.content import diet_overrides


def pending(canonical, extra=()):
    return {'canonical_id': canonical, 'status': 'PENDING_DATA', 'diet': {'major_food_groups': []},
            'blockers': ['can_largofy source=True target=false (largos reserved)',
                         'no target-native food group carries one of the source semantics', *extra]}


class DietOverrideTests(unittest.TestCase):
    def test_flutter_eats_fruit_and_shadow_yolky_eat_nothing(self):
        entries = [pending('slime.flutter'), pending('slime.shadow'), pending('slime.yolky'), pending('slime.glitch')]
        changed = diet_overrides.apply(entries, 'SR1')
        by_id = {e['canonical_id']: e for e in entries}
        self.assertEqual(changed, ['slime.flutter', 'slime.shadow', 'slime.yolky'])
        self.assertEqual(by_id['slime.flutter']['diet']['major_food_groups'], ['FRUIT'])
        self.assertEqual(by_id['slime.flutter']['status'], 'READY')
        self.assertEqual(by_id['slime.shadow']['diet']['major_food_groups'], [])
        self.assertEqual(by_id['slime.yolky']['status'], 'READY')
        self.assertEqual(by_id['slime.glitch']['status'], 'PENDING_DATA')          # not curated, still held back
        self.assertTrue(by_id['slime.flutter']['notes'][0].startswith('curated diet'))
        self.assertFalse([b for b in by_id['slime.flutter']['blockers'] if b.startswith('no target-native')])

    def test_another_blocker_keeps_the_species_pending(self):
        entries = [pending('slime.flutter', extra=['plort relation unknown'])]
        diet_overrides.apply(entries, 'SR1')
        self.assertEqual(entries[0]['status'], 'PENDING_DATA')
        self.assertIn('plort relation unknown', entries[0]['blockers'])

    def test_applying_twice_changes_nothing_more(self):
        entries = [pending('slime.flutter')]
        diet_overrides.apply(entries, 'SR1')
        self.assertEqual(diet_overrides.apply(entries, 'SR1'), [])

    def test_publish_writes_a_base_spec_that_sorts_after_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / '20261008-r2'
            old.mkdir()
            for realm in ('SR1', 'SR2'):
                (old / f'import-spec-{realm}.json').write_text(json.dumps({'entries': [pending('slime.flutter')]}), encoding='utf-8')
            result = diet_overrides.publish(Path(tmp))
            out = Path(result['directory'])
            self.assertTrue(out.name.endswith('-diets'))
            spec = json.loads((out / 'import-spec-SR1.json').read_text(encoding='utf-8'))
            self.assertEqual(spec['entries'][0]['status'], 'READY')
            sr2 = json.loads((out / 'import-spec-SR2.json').read_text(encoding='utf-8'))
            self.assertEqual(sr2['entries'][0]['status'], 'PENDING_DATA')             # no SR2 override: untouched


if __name__ == '__main__':
    unittest.main()
