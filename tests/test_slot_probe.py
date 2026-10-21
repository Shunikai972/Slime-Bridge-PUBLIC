"""Tests use ordinary diagnostic dictionaries, not native saves or gameplay proof."""
import unittest

from tools.lab.slot_probe import validate_slot_report


class SlotProbeReportTests(unittest.TestCase):
    def setUp(self):
        self.report = dict(schema_version=1, read_only=True, save_slot_count_observed=3,
                           raw_save_count=1, summaries=[dict(raw_name='example_0', game_name='example',
                           save_name='example_0', slot_index=1, invalid=False)], occupied_slot_indices=[1],
                           group_names=['example'], errors=[], occupancy_complete=True,
                           permitted_slot_range_verified=False, creation_authorized=False)

    def test_complete_occupancy_does_not_authorize_a_gap(self):
        checked = validate_slot_report(self.report)
        self.assertEqual(checked['status'], 'OCCUPANCY_OBSERVED')
        self.assertFalse(checked['creation_authorized'])
        self.assertNotIn('free_slot', checked)

    def test_incomplete_or_unreadable_summaries_are_unknown(self):
        report = dict(self.report, raw_save_count=2, errors=['unreadable'], occupancy_complete=False)
        self.assertEqual(validate_slot_report(report)['status'], 'BLOCKED_UNKNOWN')

    def test_mismatched_occupancy_and_unlisted_group_are_refused(self):
        for changed in [dict(occupied_slot_indices=[]), dict(group_names=[]),
                        dict(raw_save_count=True), dict(creation_authorized=True),
                        dict(permitted_slot_range_verified=True)]:
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    validate_slot_report(dict(self.report, **changed))


if __name__ == '__main__':
    unittest.main()
