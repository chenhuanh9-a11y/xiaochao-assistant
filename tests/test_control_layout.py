from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from control_layout import load_layout, validate_layout, preset_layout


class LayoutSchemaTests(unittest.TestCase):
    def test_old_future_malformed_and_unknown_preferences_recover(self):
        values = [None, [], {}, {'schemaVersion': 2}, dict(preset_layout(), extra=True)]
        invalid = preset_layout(); invalid['enabled'] = ['floor']
        values.append(invalid)
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(load_layout(value), preset_layout())
                with self.assertRaises(ValueError): validate_layout(value)

    def test_order_enable_and_suboptions_are_detached_only_layout(self):
        proposal = preset_layout('common')
        proposal['order'].reverse()
        result = validate_layout(proposal)
        self.assertEqual(result['enabled'], ['round', 'map', 'display'])
        proposal['order'].reverse()
        proposal['display']['referenceOpacity'] = False
        self.assertNotEqual(result['order'], proposal['order'])
        self.assertTrue(result['display']['referenceOpacity'])
        self.assertNotIn('floor', result)
        self.assertNotIn('gameMode', result)

    def test_duplicate_missing_order_and_nonboolean_flag_rejected(self):
        for mutation in ('order', 'flag', 'enabled', 'version'):
            value = preset_layout()
            if mutation == 'order': value['order'][-1] = 'display'
            if mutation == 'flag': value['display']['referenceOpacity'] = 1
            if mutation == 'enabled': value['enabled'].append('display')
            if mutation == 'version': value['schemaVersion'] = True
            with self.assertRaises(ValueError): validate_layout(value)


if __name__ == '__main__': unittest.main()
