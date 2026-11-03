"""Production SettingsService coverage for Tk's appended, Qt-unknown action."""
import json
import test_client_settings as fixtures
import unittest


class HotkeyLayoutCompatTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.SettingsTests.setUpClass.__func__)
    setUp = fixtures.SettingsTests.setUp
    tearDown = fixtures.SettingsTests.tearDown
    service = fixtures.SettingsTests.service
    write = fixtures.SettingsTests.write
    wait = fixtures.SettingsTests.wait
    def test_ten_slots_read_edit_save_restart(self):
        original = ['control+f5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']
        self.write({'actionHotkeys': original, 'mapSelectionMode': 'manual', 'future': {'x': 1}})
        service = self.service()
        # Qt owns slot 10 (manual-map-picker) as a regular, single-validated action.
        self.assertEqual(service.values['actionHotkeys'], ['Ctrl+F5', *original[1:]])
        self.assertEqual(len(service.values['actionHotkeys']), 10)
        self.assertTrue(service.setValue('sheetOpacity', .72))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], original)
        for index in range(9):
            changed = service.values['actionHotkeys']
            changed[index] = f'Ctrl+F{index + 12}'
            self.assertTrue(service.setValue('actionHotkeys', changed))
            self.assertTrue(service.save())
            self.wait(lambda: not service.busy)
            original[index] = changed[index]
            saved = json.loads(self.path.read_text())
            self.assertEqual(saved['actionHotkeys'], original)
            self.assertEqual(saved['future'], {'x': 1})
            self.assertEqual(saved['mapSelectionMode'], 'manual')
            service.shutdown()
            service = self.service()
            self.assertEqual(service.values['actionHotkeys'][:9], original[:9])
            self.assertEqual(service.values['actionHotkeys'][9], 'Alt+1')

    def test_unknown_action_conflicts_are_not_silent(self):
        self.write({'actionHotkeys': ['F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']})
        service = self.service()
        self.assertFalse(service.setValue('hotkey', 'Alt+1'))
        keys = service.values['actionHotkeys']
        keys[0] = 'Alt+1'
        self.assertFalse(service.setValue('actionHotkeys', keys))

    def test_invalid_tail_preserved_and_hotkey_writes_blocked(self):
        for tail in (12, 'NotAKey'):
            self.write({'actionHotkeys': ['Ctrl+F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', tail]})
            service = self.service()
            self.assertEqual(service.values['actionHotkeys'][0], 'Ctrl+F5')
            self.assertIn('hotkey/actionHotkeys', service.message)
            self.assertEqual(service.values['actionHotkeys'][9], '')
            self.assertFalse(service.setValue('hotkey', 'H'))
            self.assertTrue(service.setValue('sheetOpacity', .72))
            self.assertTrue(service.save())
            self.wait(lambda: not service.busy)
            self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'][-1], tail)
            service.shutdown()

    def test_nine_slots_stay_nine_and_alt_one_is_not_invented(self):
        original = ['Ctrl+F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '']
        self.write({'actionHotkeys': original})
        service = self.service()
        self.assertEqual(service.values['actionHotkeys'], [*original, ''])
        keys = service.values['actionHotkeys']
        keys[1] = 'Alt+1'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], keys[:9])
        self.assertTrue(service.reload())
        self.assertEqual(service.values['actionHotkeys'], keys)

    def test_invalid_known_slot_repair_keeps_unknown_tail(self):
        self.write({'actionHotkeys': ['BadKey', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']})
        service = self.service()
        keys = service.values['actionHotkeys']
        keys[0] = 'Ctrl+F5'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], keys)
        self.assertEqual(keys[9], 'Alt+1')

    def test_loaded_conflict_is_reported_and_repairable(self):
        self.write({'actionHotkeys': ['Alt+1', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']})
        service = self.service()
        self.assertIn('hotkey/actionHotkeys', service.message)
        self.assertFalse(service.setValue('hotkey', 'H'))
        keys = service.values['actionHotkeys']
        keys[0] = 'Ctrl+F5'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertTrue(service.reload())
        self.assertNotIn('hotkey/actionHotkeys', service.message)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], keys)
        self.assertEqual(keys[9], 'Alt+1')

    def test_partial_save_checks_conflicts_against_persisted_map_key(self):
        self.write({'hotkey': 'G', 'actionHotkeys': ['F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']})
        service = self.service()
        self.assertTrue(service.setValue('hotkey', 'H'))
        keys = service.values['actionHotkeys']
        keys[0] = 'G'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertFalse(service.saveFields(['actionHotkeys']))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], keys)
        self.assertEqual(keys[9], 'Alt+1')

    def test_picker_slot_is_validated_once_and_editing_it_keeps_known_slots(self):
        original = ['F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'Alt+1']
        self.write({'actionHotkeys': original})
        service = self.service()
        # Validated as manual-map-picker only, not again as an opaque tail.
        self.assertNotIn('hotkey/actionHotkeys', service.message)
        keys = service.values['actionHotkeys']
        keys[9] = 'Ctrl+Alt+M'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'], [*original[:9], 'Ctrl+Alt+M'])

    def test_invalid_tail_is_repairable_by_binding_the_picker(self):
        self.write({'actionHotkeys': ['F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', '', '', 'NotAKey']})
        service = self.service()
        self.assertIn('hotkey/actionHotkeys', service.message)
        keys = service.values['actionHotkeys']
        keys[9] = 'Alt+1'
        self.assertTrue(service.setValue('actionHotkeys', keys))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertTrue(service.reload())
        self.assertNotIn('hotkey/actionHotkeys', service.message)
        self.assertEqual(json.loads(self.path.read_text())['actionHotkeys'][-1], 'Alt+1')
