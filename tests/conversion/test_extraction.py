"""Extraction plans are content-addressed and receipts are bound to the plan."""
import unittest

from bridge.content.discovery import discover
from bridge.conversion.extraction import SyntheticExtractionProvider, plan_extraction, validate_receipt
from bridge.conversion.manifest import ManifestSource
from content.test_discovery import OBJECTS, manifest


def codes(diags):
    return {d['code'] for d in diags}


class ExtractionPlanTests(unittest.TestCase):
    def setUp(self):
        self.graph = discover(ManifestSource(manifest()), ['Def.Foo'])['graph']
        self.plan = plan_extraction(self.graph, 'SR2:Def.Foo')

    def test_plan_lists_binary_nodes_only_by_content_hash(self):
        kinds = sorted(item['kind'] for item in self.plan['items'])
        self.assertEqual(kinds, ['icon', 'icon', 'mesh', 'texture', 'texture'])  # icons extract as sprites
        self.assertEqual({i['extract_as'] for i in self.plan['items'] if i['kind'] == 'icon'}, {'sprite'})
        mesh = next(i for i in self.plan['items'] if i['kind'] == 'mesh')
        self.assertTrue(mesh['required'])
        self.assertEqual(mesh['artifact'], 'mesh/' + mesh['source_hash'])
        self.assertTrue(all(not i['required'] for i in self.plan['items'] if i['kind'] == 'texture'))
        self.assertEqual(self.plan['plan_hash'], plan_extraction(self.graph, 'SR2:Def.Foo')['plan_hash'])

    def test_unresolved_required_input_is_an_error(self):
        objects = [o for o in OBJECTS if o['ref'] != 'Mesh.Foo']
        graph = discover(ManifestSource(manifest(objects)), ['Def.Foo'])['graph']
        plan = plan_extraction(graph, 'SR2:Def.Foo')
        self.assertIn(('EXTRACTION_INPUT_UNRESOLVED', 'error'),
                      {(d['code'], d['severity']) for d in plan['diagnostics']})

    def test_receipts_are_bound_to_plan_and_run_evidence(self):
        provider = SyntheticExtractionProvider('SR2')
        item = self.plan['items'][0]
        good = provider.extract(item)
        self.assertEqual(validate_receipt(self.plan, good, synthetic_run=True), [])
        self.assertIn('RECEIPT_EVIDENCE_MISMATCH', codes(validate_receipt(self.plan, good, synthetic_run=False)))
        self.assertIn('RECEIPT_SOURCE_MISMATCH',
                      codes(validate_receipt(self.plan, {**good, 'source_hash': '0' * 64}, synthetic_run=True)))
        for location in ('../escape', '/abs/file', 'C:/x', 'a\\b'):
            self.assertIn('RECEIPT_LOCATION_UNSAFE', codes(validate_receipt(
                self.plan, {**good, 'location': location}, synthetic_run=True)))
        self.assertIn('RECEIPT_NOT_IN_PLAN', codes(validate_receipt(self.plan, {**good, 'node': 'SR2:other'},
                                                                    synthetic_run=True)))


if __name__ == '__main__':
    unittest.main()
