"""Real Settings QML and isolated settings file; no installed preferences/game."""
import unittest
from PySide6.QtTest import QTest
import test_settings_accessibility as fixture


class S1ManualSettingsUiTests(unittest.TestCase):
    setUpClass = classmethod(fixture.SettingsAccessibilityTests.setUpClass.__func__)
    setUp = fixture.SettingsAccessibilityTests.setUp
    tearDown = fixture.SettingsAccessibilityTests.tearDown
    service = fixture.SettingsAccessibilityTests.service
    write = fixture.SettingsAccessibilityTests.write
    wait = fixture.SettingsAccessibilityTests.wait
    create_view = fixture.SettingsAccessibilityTests.create_view
    close_view = fixture.SettingsAccessibilityTests.close_view
    item = fixture.SettingsAccessibilityTests.item

    def test_s1_has_no_manual_map_controls_and_preserves_s0_settings_on_switch(self):
        service = self.service()
        view = self.create_view(service)
        page = view.rootObject()
        for name in ('modeExpanded', 'advancedShortcuts', 'modulesExpanded'):
            page.setProperty(name, True)
        self.app.processEvents(); QTest.qWait(20)
        manual = self.item(view, 'setting_mapSelectionMode_manual')
        manual.clicked.emit()
        self.wait(lambda: not service.busy)
        self.assertEqual(service.persistedValues['mapSelectionMode'], 'manual')
        self.assertTrue(service.setValue('floorControlMode', 'fixed'))
        self.assertTrue(service.saveFields(['floorControlMode']))
        self.wait(lambda: not service.busy)
        original = self.path.read_bytes()
        keys = service.persistedValues['actionHotkeys'][:]
        floor_mode = service.values['floorControlMode']
        floor_region = service.values['floorControlRegion']
        selector = self.item(view, 'setting_gameContext')
        for season in ('s1', 's0', 's1'):
            self.assertTrue(service.setValue('gameSeason', season))
            self.app.processEvents(); QTest.qWait(20)
            self.assertIs(self.item(view, 'setting_gameContext'), selector)
            self.assertEqual(selector.property('season'), season)
            self.assertTrue(selector.isEnabled())
            for name in ('setting_mapSelectionMode_manual', 'setting_mapSelectionMode_auto',
                         'record_action_1', 'record_action_2', 'record_action_3', 'record_action_9',
                         'layout_enabled_manual_map', 'layout_enabled_recognition',
                         'floorRegionSettings', 'floorMode_auto', 'floorMode_region', 'floorMode_fixed'):
                self.assertEqual(self.item(view, name).isVisible(), season == 's0', name)
                self.assertEqual(self.item(view, name).isEnabled(), season == 's0', name)
            if season == 's1':
                self.assertTrue(self.item(view, 'settingsS1FloorObservation').isVisible())
                self.assertIn('B1～B4', self.item(view, 'settingsS1FloorObservation').property('text'))
                self.assertIn('S1 自动识别', self.item(view, 'settingsMapSelectionExplanation').property('text'))
                # A queued activation from the previous season cannot change S0's saved setting.
                self.item(view, 'setting_mapSelectionMode_auto').clicked.emit()
                self.item(view, 'setting_mapSelectionMode_manual').clicked.emit()
                self.item(view, 'record_action_9').clicked.emit()
                self.assertFalse(service.shortcutRecording)
            else:
                self.assertTrue(manual.property('selected'))
            self.assertEqual(service.persistedValues['mapSelectionMode'], 'manual')
            self.assertEqual(service.persistedValues['actionHotkeys'], keys)
            self.assertEqual(service.values['floorControlMode'], floor_mode)
            self.assertEqual(service.values['floorControlRegion'], floor_region)
            self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(view.qml_warnings, [])

    def test_switch_to_s1_cancels_uncommitted_manual_shortcut_capture_only(self):
        service = self.service()
        view = self.create_view(service)
        page = view.rootObject(); page.setProperty('advancedShortcuts', True)
        self.app.processEvents()
        before = service.persistedValues['actionHotkeys'][:]
        self.item(view, 'record_action_9').clicked.emit()
        self.assertEqual(service.shortcutTarget, 'manual-map-picker')
        self.assertTrue(service.setValue('gameSeason', 's1'))
        self.app.processEvents()
        self.assertFalse(service.shortcutRecording)
        self.assertEqual(service.persistedValues['actionHotkeys'], before)
        self.assertFalse(self.path.exists())
        # Ordinary controls are still configurable in S1.
        self.item(view, 'record_action_0').clicked.emit()
        self.assertEqual(service.shortcutTarget, 'toggle-panel')
        service.cancelShortcutCapture()
        self.assertEqual(view.qml_warnings, [])


if __name__ == '__main__':
    unittest.main()
