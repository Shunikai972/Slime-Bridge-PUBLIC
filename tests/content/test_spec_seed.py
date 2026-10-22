import json
import tempfile
import unittest
from pathlib import Path

from bridge.content import spec_seed

ROOT = Path(__file__).resolve().parents[2]


class SpecSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.specs = self.repo / 'Lab' / 'GeneratedContent' / 'species-import'

    def test_fresh_install_gets_the_bundled_specs_once(self):
        result = spec_seed.ensure_specs(self.repo)
        self.assertEqual(result['seeded'], ['SR1', 'SR2'])
        seeded = Path(result['directory'])
        self.assertTrue(seeded.name.endswith('-bundled'))
        sr1 = json.loads((seeded / 'import-spec-SR1.json').read_text(encoding='utf-8'))
        self.assertIn('slime.hyper', {e['canonical_id'] for e in sr1['entries']})
        self.assertEqual(spec_seed.ensure_specs(self.repo)['seeded'], [])          # nothing left to do

    def test_an_older_local_spec_without_a_bundled_species_is_superseded(self):
        old = self.specs / '20261018-152252-diets'
        old.mkdir(parents=True)
        bundled = json.loads((spec_seed.BUNDLED / 'import-spec-SR1.json').read_text(encoding='utf-8'))
        bundled['entries'] = [e for e in bundled['entries'] if e['canonical_id'] != 'slime.hyper']
        (old / 'import-spec-SR1.json').write_text(json.dumps(bundled), encoding='utf-8')
        (old / 'import-spec-SR2.json').write_text((spec_seed.BUNDLED / 'import-spec-SR2.json').read_text(encoding='utf-8'), encoding='utf-8')
        self.assertEqual(spec_seed.needs_seed(self.specs), ['SR1'])

    def test_bundled_specs_follow_the_committed_ids_and_carry_no_largos(self):
        ids = json.loads((ROOT / 'bridge' / 'content' / 'species_ids.json').read_text(encoding='utf-8'))
        for realm in ('SR1', 'SR2'):
            spec = json.loads((spec_seed.BUNDLED / f'import-spec-{realm}.json').read_text(encoding='utf-8'))
            self.assertEqual(set(spec), {'entries', 'realm', 'schema', 'schema_version', 'source_classification_sha256', 'source_diet_sha256'})
            for entry in spec['entries']:
                self.assertFalse(entry['canonical_id'].startswith('largo.'))
                self.assertEqual(entry['native_id'], ids[realm][entry['canonical_id']][0])


if __name__ == '__main__':
    unittest.main()
