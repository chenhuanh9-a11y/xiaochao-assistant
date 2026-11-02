"""Owned Qt controls and native packet fixtures; never registers OS listeners."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, QUrl, Qt, QEvent, QCoreApplication
from PySide6.QtGui import QKeyEvent
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from client_input import NativeShortcutBackend
from client_native_keys import PassThroughKeyboardSource
from client_coordinator import ClientCoordinator
from test_client_native_mouse import Native
import test_client_settings as settings_fixtures
from test_client_settings import visual_item
import test_client_coordinator as coordinator_fixtures


class FixtureSource(PassThroughKeyboardSource):
    """Use production decode/queue paths, only replace OS thread lifecycle."""
    active = False

    @property
    def running(self):
        return self.active

    def start(self):
        self.active = self.installed = self.pointer_installed = True
        return True

    def stop(self):
        self.active = self.installed = self.pointer_installed = False
        return True


class ShortcutDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings_fixtures.SettingsTests.setUpClass()
        cls.app = settings_fixtures.SettingsTests.app

    def setUp(self):
        guard = patch('client_coordinator.another_assistant_running', return_value=False)
        guard.start(); self.addCleanup(guard.stop)
        self.storage = settings_fixtures.SettingsTests()
        self.storage.setUp()
        self.addCleanup(self.storage.tearDown)
        self.settings = self.storage.service()
        self.foreground = 42
        self.native = SimpleNamespace(is_window=lambda hwnd: hwnd == 42,
            foreground_window=lambda: self.foreground,
            window_process_id=lambda hwnd: os.getpid() if hwnd == 77 else 99999)
        self.source = FixtureSource()
        self.backend = NativeShortcutBackend(self.native, source=self.source)
        self.addCleanup(self.backend.shutdown)
        self.backend.configureSettings(self.settings.persistedValues)
        self.backend.bindWindow(42)
        self.backend.enableManagement()
        self.backend.enable()
        self.actions, self.maps, self.pointers = [], [], []
        self.backend.actionTriggered.connect(self.actions.append)
        self.backend.mapInputRequested.connect(self.maps.append)
        self.backend.gamePointerEvent.connect(self.pointers.append)
        f = self.fixture = coordinator_fixtures.CoordinatorTests()
        f.setUpClass(); f.setUp(); f.tearDown()
        with patch('client_input.NativeWindowAPI', return_value=self.native):
            self.coordinator = f.coordinator = ClientCoordinator(backend=f.backend, settings=self.settings,
                resources=f.resources, session=f.session, windows=f.windows, native_input=self.backend,
                capture=SimpleNamespace(disable=Mock()), runtime=f.runtime, images=f.images,
                window_catalog=Mock(return_value=[]))
        self.addCleanup(f.tearDown)
        self.coordinator._foreground_timer.stop(); self.coordinator._tracking_timer.stop()
        self.coordinator._bound = '42'
        self.coordinator._automatic_live = False
        self.raw = Native()
        self.raw.GetForegroundWindow.function = lambda: self.foreground
        self.observer = self.source._make_mouse_observer(self.raw, ticks=lambda: 1000)
        self.observer.start()
        self.addCleanup(self.observer.stop)

    def mouse(self, flags, data=0):
        self.raw.mouse.buttonUnion.buttons.flags = flags
        self.raw.mouse.buttonUnion.buttons.data = data
        self.observer.handle(W.MSG(hWnd=321, message=0xff, wParam=1, lParam=999,
                                  time=1000, pt=W.POINT(12, 15)))
        self.app.processEvents()

    def key(self, vk, down=True):
        from client_native_keys import NativeKey
        packet = NativeKey(vk, 0, 0, 0, 0)
        self.source._receive_key_message(0x100 if down else 0x101,
                                        ctypes.addressof(packet), self.foreground)
        self.app.processEvents()

    def configure(self, values):
        self.assertTrue(self.backend.configure(values))
        self.backend.enableManagement(); self.backend.enable()

    def test_mouse_native_packets_scope_repeat_release_and_pointer_separation(self):
        self.configure({'map': 'MOUSE5', 'toggle-panel': 'MOUSE4'})
        self.coordinator.toggleControlsKeyboard = Mock()
        for flags in (0x40, 0x40, 0x80, 0x40, 0x80): self.mouse(flags)
        self.assertEqual(self.actions, ['toggle-panel', 'toggle-panel'])
        self.coordinator.toggleControlsKeyboard.assert_called()
        self.assertEqual(self.pointers, [])
        self.mouse(0x100); self.mouse(0x200)
        self.assertEqual(len(self.maps), 1)
        self.foreground = 77
        self.mouse(0x40); self.mouse(0x80)
        self.mouse(0x100); self.mouse(0x200)
        self.assertEqual(self.actions.count('toggle-panel'), 3)
        self.assertEqual(len(self.maps), 1)
        self.backend.bindWindow(0)
        self.mouse(0x40); self.mouse(0x80)
        self.assertEqual(self.actions.count('toggle-panel'), 4)
        for flags in (1, 2, 0x400, 0x800): self.mouse(flags, 0x40)
        self.assertEqual(len(self.actions), 4)
        self.assertEqual(self.source.held & {5, 6}, set())

    def test_all_supported_mouse_buttons_and_modifiers(self):
        for name, down, up in [('MOUSE2',4,8), ('MOUSE3',16,32), ('XBUTTON1',64,128), ('XBUTTON2',256,512)]:
            self.configure({'toggle-panel': 'Alt+' + name})
            self.mouse(down); self.mouse(up)
            self.assertEqual(self.actions, [])
            self.key(0x12)
            self.mouse(down); self.mouse(down); self.mouse(up)
            self.key(0x12, False)
            self.assertEqual(self.actions, ['toggle-panel'])
            self.actions.clear()

    def test_recording_focus_suspend_disable_and_generation(self):
        self.configure({'map': 'MOUSE5', 'toggle-panel': 'MOUSE4'})
        self.settings.beginShortcutCapture('hotkey')
        self.mouse(0x40); self.mouse(0x100)
        self.settings.cancelShortcutCapture()
        self.mouse(0x40); self.mouse(0x100)
        self.assertFalse(self.actions or self.maps)
        self.mouse(0x80); self.mouse(0x200)
        self.backend.setTextEntryActive(True)
        self.mouse(0x40); self.mouse(0x80)
        self.backend.setTextEntryActive(False)
        self.backend.suspendGameBinding()
        self.mouse(0x100); self.mouse(0x200)
        self.backend.resumeGameBinding()
        self.mouse(0x100); self.mouse(0x200)
        self.assertEqual(len(self.maps), 1)
        self.backend.disable()
        self.mouse(0x100); self.mouse(0x200)
        self.assertEqual(len(self.maps), 1)
        self.mouse(0x40); self.mouse(0x80)
        self.assertEqual(self.actions, ['toggle-panel'])

    def test_controls_hints_follow_committed_shortcuts_through_failed_save_unbind_restart(self):
        from PySide6.QtCore import QObject
        from PySide6.QtQml import QQmlApplicationEngine
        from test_client_control_stability import State
        state=State();windows=QObject();engine=QQmlApplicationEngine()
        for name,value in dict(settingsService=self.settings,session=state,
                clientCoordinator=self.coordinator,windows=windows).items():
            engine.rootContext().setContextProperty(name,value)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientControls.qml'))))
        self.assertTrue(engine.rootObjects())
        window=engine.rootObjects()[0];window.show();self.app.processEvents()
        try:
            self.assertEqual(window.shortcut('toggle-sheet-visibility'),'F11')
            actions=self.settings.values['actionHotkeys'];actions[6]='F4';actions[8]='F3'
            self.assertTrue(self.settings.setValue('actionHotkeys',actions));self.app.processEvents()
            self.assertEqual(window.shortcut('toggle-sheet-visibility'),'F11')
            self.assertEqual(window.shortcut('toggle-round'),'F10')
            with patch.object(self.settings,'_write',side_effect=OSError('fixture full disk')):
                self.settings.save();self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(window.shortcut('toggle-sheet-visibility'),'F11')
            self.assertEqual(self.backend.snapshot['bindings']['toggle-sheet-visibility'],'F11')
            self.settings.save();self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(window.shortcut('toggle-sheet-visibility'),'F4')
            self.assertEqual(self.backend.snapshot['bindings']['toggle-sheet-visibility'],'F4')
            hint=visual_item(window.contentItem(),'controlRoundKeyHint')
            self.assertEqual(hint.property('text'),'F3')
            self.assertTrue(hint.property('visible'))
            actions[6]='';actions[8]='';actions[5]='F2'
            self.settings.setValue('actionHotkeys',actions);self.settings.save()
            self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(window.shortcut('toggle-sheet-visibility'),'')
            self.assertEqual(hint.property('text'),'F2')
            actions[5]='';self.settings.setValue('actionHotkeys',actions);self.settings.save()
            self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(hint.property('text'),'')
            self.assertFalse(hint.property('visible'))
            restored=self.storage.service()
            engine.rootContext().setContextProperty('settingsService',restored);self.app.processEvents()
            self.assertEqual(window.shortcut('toggle-round'),'')
            self.assertEqual(hint.property('text'),'')
            self.assertEqual(restored.persistedValues,self.settings.persistedValues)
        finally:
            window.close();engine.deleteLater();self.app.processEvents()

    def test_mouse_alias_and_oem_conflicts_share_native_identity(self):
        self.assertFalse(self.backend.configure({'map':'XBUTTON1','toggle-panel':'MOUSE4'}))
        self.assertFalse(self.backend.configure({'map':';','toggle-panel':'OEM_1'}))
        self.assertEqual(self.backend.snapshot['status'],'conflict')
        self.mouse(0x40);self.mouse(0x80)
        self.assertFalse(self.actions or self.maps)

    def test_expired_mouse_release_clears_repeat_state_without_replaying_press(self):
        self.configure({'toggle-panel': 'MOUSE4'})
        self.mouse(0x40)
        self.observer.ticks=lambda: 5000
        self.mouse(0x80)
        self.mouse(0x40)
        self.assertEqual(self.actions,['toggle-panel'])
        self.assertNotIn(5,self.source.held)
        self.observer.ticks=lambda:1000
        self.mouse(0x40);self.mouse(0x80)
        self.assertEqual(self.actions,['toggle-panel','toggle-panel'])

    def test_actual_onboarding_record_confirm_advance_and_native_delivery(self):
        from PySide6.QtCore import QObject
        from PySide6.QtQml import QQmlApplicationEngine
        from client_onboarding import OnboardingService
        from test_client_onboarding import Fake
        windows=Fake();windows.snapshot={'controls':{'visible':False}}
        onboarding=OnboardingService(settings=self.settings,windows=windows,coordinator=self.coordinator)
        engine=QQmlApplicationEngine()
        engine.rootContext().setContextProperty('settingsService',self.settings)
        engine.rootContext().setContextProperty('onboardingService',onboarding)
        source=b'import QtQuick; import QtQuick.Controls.Basic; ApplicationWindow { width:650; height:470; visible:true; ClientOnboarding {} }'
        engine.loadData(source,QUrl.fromLocalFile(str(Path(__file__).parent/'OnboardingFixture.qml')))
        self.assertTrue(engine.rootObjects())
        window=engine.rootObjects()[0];self.app.processEvents()
        popup=window.findChild(QObject,'clientOnboarding')
        try:
            popup.findChild(QObject,'onboardingRecord').clicked.emit();self.app.processEvents()
            recorder=popup.findChild(QObject,'onboardingRecorder')
            self.assertTrue(recorder.hasActiveFocus())
            QTest.keyClick(window,Qt.Key_J);self.app.processEvents()
            self.assertEqual(self.settings.shortcutCandidate,'J')
            popup.findChild(QObject,'onboardingAdvance').clicked.emit()
            self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(onboarding.step,2)
            self.assertEqual(self.backend.snapshot['bindings']['map'],'J')
            self.assertFalse(self.backend.snapshot['editingBindings'])
            count=len(self.maps);self.key(ord('J'));self.key(ord('J'),False)
            self.assertEqual(len(self.maps),count+1)
            popup.findChild(QObject,'onboardingRecord').clicked.emit();self.app.processEvents()
            self.assertTrue(recorder.hasActiveFocus())
            pos=recorder.mapToScene(recorder.boundingRect().center()).toPoint()
            QTest.mouseClick(window,Qt.ForwardButton,Qt.NoModifier,pos)
            self.assertEqual(self.settings.shortcutCandidate,'MOUSE5')
            popup.findChild(QObject,'onboardingCaptureConfirm').clicked.emit()
            self.assertTrue(self.settings.busy)
            self.assertFalse(popup.findChild(QObject,'onboardingCaptureCancel').property('enabled'))
            self.storage.wait(lambda:not self.settings.busy)
            popup.findChild(QObject,'onboardingAdvance').clicked.emit()
            self.storage.wait(lambda:not self.settings.busy)
            self.foreground=77;self.mouse(0x100);self.mouse(0x200)
            self.assertEqual(self.actions,['toggle-panel'])
        finally:
            self.settings.cancelShortcutCapture();window.close();engine.deleteLater();self.app.processEvents()

    def test_owned_qml_oem_capture_uses_native_identity_not_layout_text(self):
        view = QQuickView()
        view.rootContext().setContextProperty('settingsService', self.settings)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientSettings.qml'))))
        view.resize(720, 740); view.show(); self.app.processEvents()
        panel = view.rootObject()
        try:
            # US Shift+semicolon, German oe on that VK, and French semicolon
            # on the M virtual key must not all become US VK_OEM_1.
            for qtkey, text, vk, modifiers, expected in [
                    (Qt.Key_Colon, ':', 0xBA, Qt.ShiftModifier, 'Shift+OEM_1'),
                    (Qt.Key_Odiaeresis, 'ö', 0xBA, Qt.NoModifier, 'OEM_1'),
                    (Qt.Key_Semicolon, ';', 0x4D, Qt.NoModifier, 'M'),
                    (Qt.Key_Plus, '+', 0xBB, Qt.ShiftModifier, 'Shift+OEM_PLUS')]:
                visual_item(panel,'record_hotkey').clicked.emit(); self.app.processEvents()
                event=QKeyEvent(QEvent.KeyPress, qtkey, modifiers, 0, vk, 0, text)
                QCoreApplication.sendEvent(view,event)
                self.assertEqual(self.settings.shortcutCandidate,expected)
                visual_item(panel,'shortcutCaptureConfirm').clicked.emit()
                visual_item(panel,'settingsSave').clicked.emit()
                self.storage.wait(lambda:not self.settings.busy)
                self.assertEqual(self.storage.service().persistedValues['hotkey'],expected)
                self.assertEqual(self.backend.snapshot['bindings']['map'],expected)
                count=len(self.maps)
                if modifiers == Qt.ShiftModifier:self.key(0x10)
                self.key(vk);self.key(vk,False)
                if modifiers == Qt.ShiftModifier:self.key(0x10,False)
                self.assertEqual(len(self.maps),count+1)
            # A missing native identity must not manufacture a US OEM key.
            visual_item(panel,'record_hotkey').clicked.emit();self.app.processEvents()
            QCoreApplication.sendEvent(view,QKeyEvent(QEvent.KeyPress,Qt.Key_Semicolon,Qt.NoModifier,';'))
            self.assertFalse(self.settings.shortcutCandidateReady)
            self.settings.cancelShortcutCapture()
            actions=self.settings.values['actionHotkeys'];actions[0]='OEM_1'
            self.settings.setValue('actionHotkeys',actions);self.settings.save()
            self.storage.wait(lambda:not self.settings.busy)
            self.assertEqual(self.settings.applicationPanelShortcut,'')
            self.foreground=77;self.key(0xBA);self.key(0xBA,False)
            self.assertEqual(self.actions,['toggle-panel'])
        finally:
            self.settings.cancelShortcutCapture();view.close();view.deleteLater();self.app.processEvents()

    def test_layout_letter_and_digit_panel_binding_has_one_native_owner_in_assistant(self):
        view = QQuickView()
        view.rootContext().setContextProperty('settingsService', self.settings)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientSettings.qml'))))
        view.resize(720, 740); view.show(); self.app.processEvents()
        panel = view.rootObject()
        try:
            for qtkey, text, vk, expected, mods in [(Qt.Key_Semicolon, ';', 0x4D, 'M', Qt.NoModifier),
                    (Qt.Key_Ampersand, '&', 0x31, '1', Qt.NoModifier),
                    (Qt.Key_Comma, ',', 0x6E, 'NUM.', Qt.KeypadModifier),
                    (Qt.Key_1, '1', 0x61, 'NUMPAD1', Qt.KeypadModifier)]:
                visual_item(panel, 'record_action_0').clicked.emit(); self.app.processEvents()
                QCoreApplication.sendEvent(view, QKeyEvent(QEvent.KeyPress, qtkey, mods, 0, vk, 0, text))
                self.assertEqual(self.settings.shortcutCandidate, expected)
                visual_item(panel, 'shortcutCaptureConfirm').clicked.emit()
                self.storage.wait(lambda: not self.settings.busy)
                self.assertEqual(self.storage.service().persistedValues['actionHotkeys'][0], expected)
                # Client.qml uses this property directly. Qt's logical ';'/'&'
                # must not compete with the saved Win32 M/1 physical identity.
                self.assertEqual(self.settings.applicationPanelShortcut, '')
                self.foreground = 77
                count = len(self.actions)
                self.key(vk); self.key(vk); self.key(vk, False)
                self.assertEqual(self.actions[count:], ['toggle-panel'])
        finally:
            self.settings.cancelShortcutCapture(); view.close(); view.deleteLater(); self.app.processEvents()

    def test_actual_settings_controls_save_hot_reload_restart_and_dispatch(self):
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('settingsService', self.settings)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientSettings.qml'))))
        self.assertFalse(view.errors())
        view.resize(720,740); view.show(); self.app.processEvents()
        panel = view.rootObject()
        try:
            for target, key, expected in [('hotkey',Qt.Key_J,'J'), ('hotkey',Qt.Key_K,'K'),
                                         ('cycle-floor',Qt.Key_F4,'F4'), ('toggle-round',Qt.Key_F3,'F3'),
                                         ('toggle-panel',Qt.BackButton,'MOUSE4')]:
                visual_item(panel, 'record_hotkey' if target == 'hotkey' else 'record_action_' + str(settings_fixtures.ACTION_ORDER.index(target))).clicked.emit()
                self.app.processEvents()
                area = visual_item(panel, 'shortcutCaptureArea')
                self.assertTrue(area.hasActiveFocus())
                if expected == 'MOUSE4':
                    pos = area.mapToScene(area.boundingRect().center()).toPoint()
                    QTest.mouseClick(view, Qt.BackButton, Qt.NoModifier, pos)
                else:
                    QTest.keyClick(view, key)
                self.app.processEvents()
                self.assertEqual(self.settings.shortcutCandidate, expected)
                visual_item(panel, 'shortcutCaptureConfirm').clicked.emit()
                self.storage.wait(lambda: not self.settings.busy)
                self.app.processEvents()
                action = 'map' if target == 'hotkey' else target
                self.assertEqual(self.backend.snapshot['bindings'][action], expected)
                self.assertFalse(self.backend.snapshot['editingBindings'])
                self.assertFalse(self.coordinator._shortcut_recording)
                self.assertEqual(self.storage.service().persistedValues, self.settings.persistedValues)
                if action == 'map':
                    self.assertIn(ord(expected), self.source._keys)
                    self.assertNotIn(ord('G'), self.source._keys)
                    count = len(self.maps)
                    self.key(ord(expected)); self.key(ord(expected), False)
                    self.assertEqual(len(self.maps), count+1)
                    self.key(ord('G')); self.key(ord('G'), False)
                    self.assertEqual(len(self.maps), count+1)
            self.foreground = 77
            self.mouse(0x40); self.mouse(0x80)
            self.assertEqual(self.actions.count('toggle-panel'), 1)
            self.foreground = 42
            self.key(0x73); self.key(0x73,False)
            self.key(0x72); self.key(0x72,False)
            self.assertIn('cycle-floor', self.actions)
            self.assertIn('toggle-round', self.actions)
            # Fresh service and native backend, as on relaunch; no old draft,
            # held-state or in-memory binding object participates.
            restored = self.storage.service()
            source2 = FixtureSource()
            backend2 = NativeShortcutBackend(self.native, source=source2)
            self.addCleanup(backend2.shutdown)
            self.assertTrue(backend2.configureSettings(restored.persistedValues))
            backend2.bindWindow(42);backend2.enable();backend2.enableManagement()
            delivered=[];backend2.actionTriggered.connect(delivered.append)
            raw2=Native();raw2.GetForegroundWindow.function=lambda:self.foreground
            observer2=source2._make_mouse_observer(raw2,ticks=lambda:1000)
            observer2.start();self.addCleanup(observer2.stop)
            from client_native_keys import NativeKey
            packet=NativeKey(ord('K'),0,0,0,0)
            source2._receive_key_message(0x100,ctypes.addressof(packet),42)
            source2._receive_key_message(0x101,ctypes.addressof(packet),42)
            self.foreground=77
            for flags in (0x40,0x40,0x80):
                raw2.mouse.buttonUnion.buttons.flags=flags
                observer2.handle(W.MSG(hWnd=321,message=0xff,wParam=1,lParam=999,time=1000))
            self.app.processEvents()
            self.assertEqual(delivered,['toggle-panel'])  # Old game packet is stale after focus changes.
            self.foreground=42
            source2._receive_key_message(0x100,ctypes.addressof(packet),42)
            source2._receive_key_message(0x101,ctypes.addressof(packet),42)
            self.app.processEvents()
            self.assertEqual(delivered,['toggle-panel','map'])
        finally:
            self.settings.cancelShortcutCapture()
            view.close(); view.deleteLater(); self.app.processEvents()


if __name__ == '__main__': unittest.main()
