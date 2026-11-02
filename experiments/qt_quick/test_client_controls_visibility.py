"""Qt-only controls intent, real settings writer/QML; no hooks/game/user config."""
import json
import os
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QUrl, QMetaObject, QCoreApplication, QEvent
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from client_coordinator import ClientCoordinator
import test_client_settings as settings_seams
import test_client_coordinator as coordinator_seams

APP = QGuiApplication.instance() or QGuiApplication([])
KEY = 'qtControlsVisible'


class VisibilityHelpers:
    app = APP
    setUp = settings_seams.SettingsTests.setUp
    tearDown = settings_seams.SettingsTests.tearDown
    service = settings_seams.SettingsTests.service
    write = settings_seams.SettingsTests.write
    wait = settings_seams.SettingsTests.wait


class VisibilitySettingsTests(VisibilityHelpers, unittest.TestCase):
    def test_legacy_orb_is_not_qt_intent_and_unrelated_save_does_not_create_it(self):
        for orb in (False, True):
            with self.subTest(orb=orb):
                original = dict(orbVisible=orb, future={'untouched': [1, 2]},
                    actionHotkeys=['F9','F6','F7','F8','','','F11','','F10','ALT'])
                self.write(original)
                service = self.service()
                self.assertTrue(service.values[KEY])
                service.setValue('sheetOpacity', .6); service.saveFields(['sheetOpacity'])
                self.wait(lambda: not service.busy)
                self.assertEqual(json.loads(self.path.read_text()), dict(original, sheetOpacity=.6))

    def test_explicit_true_and_false_survive_restart_without_changing_tk_or_ten_slots(self):
        for visible in (True, False):
            with self.subTest(visible=visible):
                original = dict(orbVisible=not visible, future={'keep': True},
                    actionHotkeys=['F9','F6','F7','F8','','','F11','','F10','ALT'])
                self.write(original); service = self.service()
                self.assertTrue(service.persistControlsVisibility(visible))
                self.wait(lambda: not service.pendingWrites)
                self.assertEqual(json.loads(self.path.read_text()), dict(original, **{KEY: visible}))
                self.assertIs(self.service().persistedValues[KEY], visible)

    def block_write(self, service):
        entered, release = Event(), Event()
        original = service._write
        def blocked(payload, expected):
            entered.set(); release.wait(3)
            return original(payload, expected)
        service._write = blocked
        return entered, release

    def test_busy_save_queues_only_latest_controls_choice_and_preserves_other_draft(self):
        service = self.service()
        entered, release = self.block_write(service)
        service.setValue('sheetOpacity', .61); service.saveFields(['sheetOpacity'])
        self.assertTrue(entered.wait(2))
        try:
            service.setValue('referenceOpacity', .73)
            for visible in (False, True, False):
                self.assertTrue(service.persistControlsVisibility(visible))
            self.assertFalse(service.values[KEY])
            self.assertTrue(service.pendingWrites)
        finally:
            release.set()
        self.wait(lambda: not service.pendingWrites)
        disk = json.loads(self.path.read_text())
        self.assertFalse(disk[KEY]); self.assertEqual(disk['sheetOpacity'], .61)
        self.assertNotIn('referenceOpacity', disk)
        self.assertEqual(service.values['referenceOpacity'], .73)
        self.assertTrue(service.dirty)

    def test_active_visibility_save_latest_wins_even_when_it_returns_to_baseline(self):
        self.write({KEY: True, 'orbVisible': False})
        service = self.service(); entered, release = self.block_write(service)
        self.assertTrue(service.persistControlsVisibility(False)); self.assertTrue(entered.wait(2))
        try:
            service.persistControlsVisibility(True)
        finally:
            release.set()
        self.wait(lambda: not service.pendingWrites)
        self.assertTrue(json.loads(self.path.read_text())[KEY])
        self.assertTrue(service.persistedValues[KEY]); self.assertFalse(service.dirty)

    def test_invalid_qt_value_is_not_inferred_or_rewritten_until_explicit_edit(self):
        self.write({KEY: "false", 'orbVisible': False})
        service=self.service()
        self.assertTrue(service.values[KEY])
        service.setValue('sheetOpacity', .64); service.saveFields(['sheetOpacity'])
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())[KEY], "false")
        service.persistControlsVisibility(False)
        self.wait(lambda: not service.pendingWrites)
        self.assertIs(json.loads(self.path.read_text())[KEY], False)

    def test_corrupt_file_is_not_overwritten_and_failure_is_observable(self):
        self.path.write_text('{broken', encoding='utf-8')
        service = self.service(); failures=[]
        service.controlsVisibilitySaveFailed.connect(failures.append)
        self.assertFalse(service.persistControlsVisibility(False))
        self.assertFalse(service.values[KEY])
        self.assertEqual(self.path.read_text(), '{broken')
        self.assertFalse(service.pendingWrites)
        self.assertEqual(len(failures), 1); self.assertIn('原文件已保留', failures[0])

    def test_failed_write_reports_failure_keeps_draft_and_never_retries_itself(self):
        self.write({'orbVisible': True}); service=self.service(); failures=[]
        service.controlsVisibilitySaveFailed.connect(failures.append)
        with patch.object(service, '_write', side_effect=OSError('read-only fixture')) as writer:
            service.persistControlsVisibility(False)
            self.wait(lambda: not service.pendingWrites)
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text()), {'orbVisible': True})
        self.assertFalse(service.values[KEY]); self.assertTrue(service.persistedValues[KEY])
        self.assertEqual(len(failures), 1); self.assertIn('保存失败', failures[0])

    def test_tk_save_transparently_keeps_valid_qt_intent_without_changing_orb(self):
        # Execute the repository's complete production Tk save method; replace
        # only UI-owned state with a fixture, never construct Tk or native input.
        from test_reopen_lifecycle import method
        from client_settings import ACTION_ORDER
        import threading
        import ast
        source=Path(__file__).parents[2]/'app/identityv_daily_assistant.py'
        tree=ast.parse(source.read_text(encoding='utf-8-sig'))
        node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='atomic_write_text')
        namespace=dict(Path=Path, os=os, threading=threading)
        exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
        atomic=namespace['atomic_write_text']
        save=method('_save_settings')
        save.__globals__.update(json=json, ACTION_ORDER=ACTION_ORDER,
            WindowAnchor=type('UnusedAnchor',(),{}), normalize_drag_boundary=lambda value:None,
            atomic_write_text=atomic)
        for value in (True, False, None, "false", 0):
            with self.subTest(value=value):
                raw={} if value is None else {KEY:value}
                owner=SimpleNamespace(settings_path=self.path, settings=raw, hotkey_name='G',
                    action_hotkeys={name:('',0) for name in ACTION_ORDER}, compact_position=None,
                    sheet_position=None, sheet_opacity=.55, reference_opacity=.55, sheet_scale=1.,
                    orb_visibility_enabled=False, interface_opacity=.82, experimental_auto_overlay=False,
                    preferred_route_profile_id='fixture', zhan10_coffin_variant_id='variant-01',
                    onboarding_completed=True, round_end_hint_shown=False, active_confirmation=None,
                    control_layout_snapshot=lambda:{})
                self.assertTrue(save(owner))
                disk=json.loads(self.path.read_text())
                self.assertIs(disk['orbVisible'],False)
                self.assertEqual(len(disk['actionHotkeys']),10)
                if type(value) is bool:
                    self.assertIs(disk[KEY],value)
                    self.assertIs(self.service().values[KEY],value)
                else:
                    self.assertNotIn(KEY,disk)

    def test_shutdown_drains_one_queued_latest_choice(self):
        service=self.service()
        service.setValue('sheetOpacity', .67); service.saveFields(['sheetOpacity'])
        service.persistControlsVisibility(False)
        service.shutdown()
        self.assertFalse(service.pendingWrites)
        self.assertFalse(json.loads(self.path.read_text())[KEY])


class VisibilityCoordinatorTests(VisibilityHelpers, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.f=coordinator_seams.CoordinatorTests(); self.f.app=APP; self.f.setUp()
        self.f.coordinator._closed=True
        self.f.windows.snapshot={'controls': {'visible': False}}
        self.f.windows.show.side_effect=lambda name: self.f.windows.snapshot.setdefault(name, {}).update(visible=True)
        self.f.windows.hide.side_effect=lambda name: self.f.windows.snapshot.setdefault(name, {}).update(visible=False)
        self.c=None

    def tearDown(self):
        if self.c:
            self.c._closed=True
            for timer in (self.c._startup_timer, self.c._discovery_timer, self.c._foreground_timer,
                          self.c._tracking_timer, self.c._context_timer, self.c._close_timer, self.c._material_timer):
                timer.stop()
        self.f.tearDown()
        super().tearDown()

    def coordinator(self, raw):
        self.write(raw); self.s=self.service(); f=self.f
        with patch('client_input.NativeWindowAPI', return_value=f.native):
            self.c=ClientCoordinator(backend=f.backend, settings=self.s, resources=f.resources,
                session=f.session, windows=f.windows, native_input=f.input, capture=Mock(),
                runtime=f.runtime, images=f.images, window_catalog=lambda: [])
        self.c._bound=''
        self.c._foreground_timer.stop(); self.c._tracking_timer.stop()
        return self.c

    def test_start_round_and_next_round_respect_hidden_choice_and_keep_existing_guards(self):
        c=self.coordinator({KEY: False, 'orbVisible': True})
        for _ in range(2):
            c.startRound(); self.f.session.dispatch.assert_called_with('start_round')
        self.f.windows.show.assert_not_called()
        self.f.session.dispatch.reset_mock(); self.f.resources.busy=True
        c.startRound(); self.f.session.dispatch.assert_not_called()
        self.f.resources.busy=False; self.f.session.busy=True
        c.startRound(); self.f.session.dispatch.assert_not_called()

    def test_old_orb_false_defaults_to_current_start_behavior_without_writing_preference(self):
        c=self.coordinator({'orbVisible': False}); c.startRound()
        self.f.windows.show.assert_called_once_with('controls')
        self.assertEqual(json.loads(self.path.read_text()), {'orbVisible': False})
        self.s.changed.emit()
        self.assertTrue(self.f.windows.snapshot['controls']['visible'])

    def test_mouse_keyboard_use_same_intent_and_hide_hint_uses_actual_binding(self):
        c=self.coordinator({'orbVisible': False,
            'actionHotkeys':['Ctrl+F12','F6','F7','F8','','','F11','','F10','ALT']})
        notices=[]; c.notificationRequested.connect(lambda *args:notices.append(args))
        c.showControls(); self.wait(lambda: not self.s.pendingWrites)
        c.toggleControlsKeyboard(); self.wait(lambda: not self.s.pendingWrites)
        self.assertFalse(self.f.windows.snapshot['controls']['visible'])
        self.assertFalse(json.loads(self.path.read_text())[KEY])
        self.assertIn('Ctrl+F12', notices[-1][1]); self.assertNotIn('F9', notices[-1][1])
        c.startRound(); self.assertFalse(self.f.windows.snapshot['controls']['visible'])
        c.toggleControls(); self.wait(lambda: not self.s.pendingWrites)
        self.assertTrue(json.loads(self.path.read_text())[KEY])
        self.assertIs(json.loads(self.path.read_text())['orbVisible'], False)

    def test_no_shortcut_hint_offers_home_and_failed_save_never_claims_persisted(self):
        c=self.coordinator({'actionHotkeys':['','F6','F7','F8','','','F11','','F10','ALT']})
        notices=[]; c.notificationRequested.connect(lambda *args:notices.append(args))
        with patch.object(self.s, '_write', side_effect=OSError('no write')):
            c.hideControls(); self.wait(lambda: not self.s.pendingWrites)
        self.assertIn('主页', notices[0][1]); self.assertNotIn('F9', notices[0][1])
        self.assertEqual(notices[-1][2], 'warning')
        self.assertIn('未保存', notices[-1][1])
        self.assertFalse(self.f.windows.snapshot['controls']['visible'])

    def test_close_waits_for_queued_completion_then_latest_write_without_retry_loop(self):
        c=self.coordinator({})
        zero=SimpleNamespace(activeThreadCount=lambda: 0)
        self.f.session._pool=self.f.resources.pool=zero
        quits=[]; c.quitRequested.connect(lambda: quits.append(True))
        self.s.setValue('sheetOpacity', .63); self.s.saveFields(['sheetOpacity'])
        self.s.persistControlsVisibility(False)
        # Complete native work but deliberately leave the queued UI callback
        # undelivered. Thread count alone cannot authorize process teardown.
        self.s.pool.waitForDone()
        c._closing=True
        c._finish_close()
        self.assertFalse(c.closeReady); self.assertFalse(quits)
        self.wait(lambda: not self.s.pendingWrites)
        c._finish_close()
        self.assertTrue(c.closeReady); self.assertEqual(quits, [True])
        self.assertFalse(json.loads(self.path.read_text())[KEY])

    def test_qml_settings_switch_uses_real_coordinator_and_never_writes_tk_orb(self):
        c=self.coordinator({'orbVisible': False})
        from PySide6.QtQuick import QQuickView
        view=QQuickView()
        view.rootContext().setContextProperty('settingsService',self.s)
        view.rootContext().setContextProperty('clientCoordinator',c)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientSettings.qml'))))
        self.assertFalse(view.errors())
        panel=view.rootObject(); panel.setProperty('advancedDisplay',True)
        APP.processEvents()
        try:
            toggle=settings_seams.visual_item(panel,'setting_qtControlsVisible')
            self.assertIsNotNone(toggle)
            toggle.setProperty('checked',False)
            self.assertTrue(QMetaObject.invokeMethod(toggle,'toggled'))
            self.wait(lambda: not self.s.pendingWrites)
            disk=json.loads(self.path.read_text())
            self.assertFalse(disk[KEY]); self.assertIs(disk['orbVisible'],False)
            toggle.setProperty('checked',True)
            self.assertTrue(QMetaObject.invokeMethod(toggle,'toggled'))
            self.wait(lambda: not self.s.pendingWrites)
            self.assertTrue(json.loads(self.path.read_text())[KEY])
            self.assertTrue(self.f.windows.snapshot['controls']['visible'])
        finally:
            view.close(); view.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); APP.processEvents()

    def test_qml_hide_menu_calls_real_coordinator_and_persists(self):
        c=self.coordinator({})
        engine=QQmlApplicationEngine()
        warnings=[]; engine.warnings.connect(lambda rows:warnings.extend(map(str,rows)))
        for name, value in dict(clientCoordinator=c, settingsService=self.s,
                                session=self.f.session, windows=QObject()).items():
            engine.rootContext().setContextProperty(name,value)
        # A real QObject session with Qt properties is required by the panel.
        from test_client_control_actions import Session
        session=Session(); engine.rootContext().setContextProperty('session',session)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientControls.qml'))))
        self.assertEqual(len(engine.rootObjects()),1,warnings)
        window=engine.rootObjects()[0]
        try:
            menu=window.findChild(QObject,'controlHideControls')
            self.assertIsNotNone(menu)
            self.assertTrue(QMetaObject.invokeMethod(menu,'triggered'))
            self.wait(lambda: not self.s.pendingWrites)
            self.assertFalse(json.loads(self.path.read_text())[KEY])
            self.assertFalse(self.f.windows.snapshot['controls']['visible'])
        finally:
            window.deleteLater(); engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); APP.processEvents()


if __name__ == '__main__':
    unittest.main()
