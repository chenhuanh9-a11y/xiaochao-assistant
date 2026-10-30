import unittest
from control_layout import preset_layout as old_preset
from client_control_layout import load_layout, validate_layout


class QtLayoutMigrationTests(unittest.TestCase):
    def test_legacy_map_block_splits_without_losing_order_visibility_or_display_options(self):
        old = old_preset('common')
        old['order'] = ['round', 'map', 'display', 'recognition']
        old['display']['referencePosition'] = True
        current = load_layout(old)
        self.assertEqual(current['order'], ['round', 'mode', 'manual_map', 'display', 'recognition'])
        self.assertEqual(current['enabled'], ['round', 'mode', 'manual_map', 'display'])
        self.assertEqual(current['display'], old['display'])
        current['enabled'].remove('mode')
        self.assertIn('manual_map', validate_layout(current)['enabled'])
        self.assertNotIn('mode', validate_layout(current)['enabled'])
        self.assertEqual(old['schemaVersion'], 1)


if __name__ == '__main__':
    unittest.main()
