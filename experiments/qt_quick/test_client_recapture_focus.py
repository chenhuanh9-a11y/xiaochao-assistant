"""Production QML -> coordinator -> session -> runtime -> capture, fake native IO.

No OS focus/input or game pixels: only capture backend, observation and fit are
fixtures. Actual foreground guard, Qt click, request worker and lifecycle run.
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from pathlib import Path
import time
import threading
import sys
import cv2  # Preload before the bounded native-module fixture restores sys.modules.
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from client_capture import ClientCaptureProvider
from client_region_capture import ClientCaptureContext
from client_session import SessionService
from client_windows import WindowController
from test_client_capture_full import Backend
import test_client_coordinator as coordinator_fixtures
import test_client_live_runtime as live_fixtures
from test_game_window_focus import FakeFocus

APP = QGuiApplication.instance() or QGuiApplication([])

class RecaptureFocusTests(unittest.TestCase):
    def setUp(self):
        if sys.platform != 'win32':
            # Native module imports Win32 eagerly. Only its blank-frame reader
            # is needed by this fixture backend; no recognition function used.
            native_module = patch.dict(sys.modules, identityv_live_mvp=SimpleNamespace(
                client_frame_is_blank=lambda pixels: not pixels.any()))
            native_module.start(); self.addCleanup(native_module.stop)
        self.live = live_fixtures.LiveRuntimeContracts('test_ui_snapshot_does_not_copy_solver_evidence_or_change_candidate_identity')
        self.live.setUp()
        self.addCleanup(self.live.doCleanups)
        self.native = FakeFocus()
        self.native.foreground_window = self.native.GetForegroundWindow
        self.native.is_window = self.native.IsWindow
        self.backend = Backend()
        self.backend.current = ClientCaptureContext((1280, 720), (100, 200, 1380, 920), 96)
        self.capture = ClientCaptureProvider(self.native, self.backend)
        self.capture.bindFullClient(42)
        self.addCleanup(self.capture.shutdown)
        self.runtime = self.live.runtime
        self.runtime._capture = self.capture
        self.assertTrue(self.live.dispatch('start_round')['success'])
        self.native.foreground = 42
        receipt = self.live.dispatch('capture_now')
        self.assertTrue(receipt['success'], receipt)
        self.session = SessionService(self.runtime)
        self.addCleanup(self.session.shutdown)
        self.fixture = coordinator_fixtures.CoordinatorTests('test_foreground_diagnostics_identify_owned_role_without_activation')
        self.fixture.app = APP
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.coordinator = self.fixture.coordinator
        self.coordinator.session = self.session
        self.coordinator.runtime = self.runtime
        self.coordinator.capture = self.capture
        self.coordinator._bound = '42'
        self.coordinator._native_windows = self.native
        self.coordinator._game_focus._native = self.native
        # Production presentation compares the complete (mode, profile, season)
        # context; omitting season silently drops every real runtime event.
        self.coordinator._applied_context = ('nightmare', 'shared-structure', 's0')
        self.fixture.settings.values['routeProfileId'] = 'shared-structure'
        self.fixture.settings.persistedValues['routeProfileId'] = 'shared-structure'
        self.session.actionRequested.connect(self.coordinator._action_requested)
        self.session.changed.connect(self.coordinator._session_changed)
        self.runtime.set_event_callback(self.coordinator.runtimeEvent.emit)
        self.addCleanup(self.runtime.set_event_callback, None)
        self.qml = QQmlEngine()
        self.qml.rootContext().setContextProperty('session', self.session)
        self.qml.rootContext().setContextProperty('clientCoordinator', self.coordinator)
        self.component = QQmlComponent(self.qml, QUrl.fromLocalFile(str(Path(__file__).with_name('HomeMapActions.qml'))))
        self.page = self.component.create()
        self.assertIsNotNone(self.page, str(self.component.errors()))
        self.window = QQuickWindow()
        self.window.resize(900, 680)
        self.page.setParentItem(self.window.contentItem())
        self.page.setWidth(900); self.page.setHeight(680)
        self.window.show(); APP.processEvents()
        self.native.foreground = 77  # Clicking the actual main page owns focus.
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.hide(); self.page.setParentItem(None)
        self.page.deleteLater(); self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.component.deleteLater(); self.qml.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def click(self):
        def find(item, name):
            if item.objectName() == name: return item
            for child in item.childItems():
                result = find(child, name)
                if result is not None: return result
        for name in ('homeMapMore', 'homeRecapture'):
            item = find(self.window.contentItem(), name)
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, point)
            APP.processEvents(); QTest.qWait(180)

    def settle(self):
        deadline = time.monotonic() + 5
        while self.session.busy and time.monotonic() < deadline:
            APP.processEvents(); time.sleep(.002)
        self.assertFalse(self.session.busy)
        APP.processEvents()

    def test_main_qml_click_returns_game_then_captures_via_real_guard(self):
        before = len(self.backend.captures)
        self.click(); self.settle()
        receipt = self.session.snapshot['lastReceipt']
        self.assertTrue(receipt.get('success'), receipt)
        self.assertEqual(len(self.backend.captures), before + 1)
        self.assertEqual(self.native.mutations(), [('SetForegroundWindow', 42)])
        self.assertTrue(self.runtime.snapshot()['displayAuthorized'])

    def test_focus_denial_does_not_enqueue_capture_or_revoke_current_state(self):
        self.native.confirm = False
        before, token = len(self.backend.captures), self.runtime.snapshot()['pendingRenderToken']
        self.click(); self.settle()
        self.assertEqual(self.session.snapshot['lastReceipt'], {})
        self.assertEqual(len(self.backend.captures), before)
        self.assertEqual(self.runtime.snapshot()['pendingRenderToken'], token)
        self.assertIn('手动', self.coordinator.message)

    def test_focus_reentrant_binding_or_round_change_never_dispatches(self):
        for change in (lambda: self.capture.bindFullClient(42),
                       lambda: setattr(self.coordinator, '_bound', '99'),
                       lambda: self.runtime.dispatch('end_round')):
            with self.subTest(change=change):
                before = len(self.backend.captures)
                self.native.foreground = 77
                self.native.on_focus = change
                self.click(); self.settle()
                self.assertEqual(self.session.snapshot['lastReceipt'], {})
                self.assertEqual(len(self.backend.captures), before)
                self.coordinator._bound = '42'

    def test_difficult_mode_uses_same_explicit_focus_and_capture_path(self):
        self.native.foreground = 42
        self.assertTrue(self.live.dispatch('end_round')['success'])
        self.assertTrue(self.live.dispatch('set_context', mode='difficult')['success'])
        self.assertTrue(self.live.dispatch('start_round')['success'])
        self.assertTrue(self.live.dispatch('capture_now')['success'])
        self.session.refreshRuntime()
        self.native.foreground = 77
        before = len(self.backend.captures)
        self.click(); self.settle()
        self.assertTrue(self.session.snapshot['lastReceipt'].get('success'))
        self.assertEqual(len(self.backend.captures), before + 1)
        self.assertEqual(self.runtime.snapshot()['mode'], 'difficult')

    def test_focus_loss_policy_still_revokes_and_return_plan_is_not_duplicated(self):
        self.coordinator._watch_foreground()
        self.assertEqual(self.runtime.snapshot()['pendingRenderToken'], '')
        self.session.refreshRuntime()
        before = len(self.backend.captures)
        self.click(); self.settle()
        self.coordinator._watch_foreground()
        self.settle()
        self.assertEqual(len(self.backend.captures), before + 1)
        self.assertFalse(self.coordinator._waiting_foreground)

    def return_to_game(self):
        self.coordinator._watch_foreground()
        self.session.refreshRuntime()
        self.native.foreground = 42
        self.coordinator._watch_foreground()

    def test_foreground_return_retries_unobserved_frames_before_fresh_authorization(self):
        old = self.runtime.snapshot()['pendingRenderToken']
        before = len(self.backend.captures)
        with patch('map_interface_observation.detect_map_interface', side_effect=[
                dict(detected=False), dict(detected=False), dict(detected=True)]):
            self.return_to_game(); self.settle()
        receipt = self.session.snapshot['lastReceipt']
        self.assertTrue(receipt.get('success'), receipt)
        self.assertEqual(len(self.backend.captures), before + 3)
        self.assertIsNone(self.runtime.render_payload(old))
        self.assertTrue(self.runtime.snapshot()['displayAuthorized'])
        self.coordinator._watch_foreground(); self.settle()
        self.assertEqual(len(self.backend.captures), before + 3)

    def test_foreground_return_retries_capture_unavailable_before_fresh_frame(self):
        before = len(self.backend.captures)
        failures = [True, True, False]
        def unavailable():
            if failures.pop(0):
                raise OSError('game-client-dc-returned-blank-frame')
        self.backend.on_capture = unavailable
        self.return_to_game(); self.settle()
        receipt = self.session.snapshot['lastReceipt']
        self.assertTrue(receipt.get('success'), receipt)
        self.assertEqual(len(self.backend.captures), before + 3)
        self.assertTrue(self.runtime.snapshot()['displayAuthorized'])

    def test_foreground_return_deadline_is_not_a_physical_map_close(self):
        events = []
        original = self.runtime._notify
        def notify(name, **values):
            events.append((name, values))
            return original(name, **values)
        self.runtime._notify = notify
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.return_to_game(); self.settle()
        receipt = self.session.snapshot['lastReceipt']
        self.assertFalse(receipt.get('success'), receipt)
        self.assertEqual(receipt['reason'], 'map-open-preparation-deadline')
        self.assertGreater(len(self.backend.captures), 2)
        self.assertFalse(any(values.get('mapClosed') for name, values in events))
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.fixture.input.resetMapInput.assert_called_with(True)

    def test_difficult_foreground_return_uses_same_multiframe_preparation(self):
        self.native.foreground = 42
        self.live.dispatch('end_round')
        self.live.dispatch('set_context', mode='difficult')
        self.live.dispatch('start_round')
        self.assertTrue(self.live.dispatch('capture_now')['success'])
        self.session.refreshRuntime()
        self.coordinator._applied_context = ('difficult', 'shared-structure', 's0')
        self.fixture.settings.values['mode'] = 'difficult'
        self.native.foreground = 77
        before = len(self.backend.captures)
        with patch('map_interface_observation.detect_map_interface', side_effect=[
                dict(detected=False), dict(detected=False), dict(detected=True)]):
            self.return_to_game(); self.settle()
        receipt = self.session.snapshot['lastReceipt']
        self.assertTrue(receipt.get('success'), receipt)
        self.assertEqual(len(self.backend.captures), before + 3)
        self.assertEqual(self.runtime.snapshot()['mode'], 'difficult')

    def test_foreground_return_interruption_never_authorizes_old_frame(self):
        # Real worker/capture lane, paused before the first return pixels.
        entered, release = threading.Event(), threading.Event()
        def blocked_capture():
            entered.set()
            self.assertTrue(release.wait(3))
        self.backend.on_capture = blocked_capture
        old = self.runtime.snapshot()['pendingRenderToken']
        self.return_to_game()
        self.assertTrue(entered.wait(3))
        self.native.foreground = 77
        self.coordinator._watch_foreground()
        release.set(); self.settle()
        self.assertIsNone(self.runtime.render_payload(old))
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def interrupted_return(self, interrupt):
        entered, release = threading.Event(), threading.Event()
        def blocked_capture():
            entered.set()
            self.assertTrue(release.wait(3))
        self.backend.on_capture = blocked_capture
        old = self.runtime.snapshot()['pendingRenderToken']
        self.return_to_game()
        self.assertTrue(entered.wait(3))
        try:
            interrupt()
        finally:
            self.backend.on_capture = None
            release.set()
        self.settle()
        self.assertIsNone(self.runtime.render_payload(old))
        return self.runtime.snapshot()

    def test_return_worker_cannot_reauthorize_after_explicit_close(self):
        state = self.interrupted_return(lambda: self.coordinator._map_input_requested(dict(
            sequence=1, openIntent=False, createdAt=101., hwnd=42, source='hotkey')))
        self.assertFalse(state['mapOpen'])
        self.assertFalse(state['displayAuthorized'])

    def test_return_worker_cannot_reauthorize_after_round_end(self):
        state = self.interrupted_return(lambda: self.session.dispatch('end_round'))
        self.assertFalse(state['roundActive'])
        self.assertFalse(state['displayAuthorized'])

    def test_return_worker_cannot_reauthorize_after_rebinding(self):
        state = self.interrupted_return(lambda: self.capture.bindFullClient(42))
        self.assertFalse(state['displayAuthorized'])

    def test_return_worker_yields_to_rapid_close_open_sequence(self):
        def keys():
            for sequence, opening in ((1, False), (2, True)):
                self.coordinator._map_input_requested(dict(sequence=sequence, openIntent=opening,
                    createdAt=100. + sequence, hwnd=42, source='hotkey'))
        state = self.interrupted_return(keys)
        receipt = self.session.snapshot['lastReceipt']
        self.assertTrue(receipt.get('success'), receipt)
        self.assertEqual(receipt['action'], 'map')
        self.assertEqual(self.coordinator._map_input_sequence, 2)
        self.assertTrue(state['mapOpen'])
        self.assertTrue(state['displayAuthorized'])

    def test_reentrant_click_and_busy_repeat_enqueue_only_once(self):
        self.native.on_focus = self.coordinator.recaptureFromMain
        self.click()
        self.coordinator.recaptureFromMain()
        self.settle()
        self.assertEqual(len(self.backend.captures), 2)
        self.assertEqual(self.native.mutations(), [('SetForegroundWindow', 42)])

    def assert_guard_revokes(self):
        receipt = self.live.dispatch('capture_now')
        self.assertFalse(receipt['success'])
        self.assertEqual(self.runtime.snapshot()['pendingRenderToken'], '')

    def test_guard_cannot_retain_after_map_close(self):
        self.live.dispatch('close_map')
        self.assert_guard_revokes()

    def test_guard_cannot_retain_rebound_or_disabled_provider(self):
        self.capture.bindFullClient(42); self.capture.enable()
        self.assert_guard_revokes()

    def test_disabled_preflight_preserves_capture_error_phase_and_revokes(self):
        self.capture.disable()
        receipt = self.live.dispatch('capture_now')
        self.assertFalse(receipt['success'])
        self.assertEqual(receipt.get('errorPhase'), 'capture')
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_guard_cannot_retain_changed_geometry(self):
        from client_region_capture import ClientCaptureContext
        self.backend.current = ClientCaptureContext((100,80), (20,30,120,110), 144)
        self.assert_guard_revokes()

    def test_guard_cannot_retain_revoked_authorization(self):
        self.runtime._core.revoke_display(reason='fixture-authority-expired')
        self.assert_guard_revokes()

    def test_guard_cannot_retain_expired_continuity(self):
        token = self.runtime._core.display_authorization
        token.observation_id += 1  # A subsequent observation needs continuity.
        token.continuity_until = .5  # Runtime fixture monotonic clock is 1.
        self.assert_guard_revokes()

    def test_post_acquisition_error_still_revokes(self):
        self.native.foreground = 42
        self.backend.on_capture = lambda: setattr(self.native, 'foreground', 77)
        self.assert_guard_revokes()
        self.assertEqual(len(self.backend.captures), 2)

    def test_queued_ready_during_blocked_capture_retains_current_completed_token(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        self.runtime._evidence_kind = 'live-client-capture'
        self.native.foreground = 42
        with patch.object(self.coordinator, '_convert_client_geometry'), \
                patch.object(self.coordinator, '_sync_reference_payload'), \
                patch.object(self.coordinator.images, 'publish', create=True, return_value='image://fixture/old'):
            # This queues the real runtime's ready event; do not consume it yet.
            self.assertTrue(self.live.dispatch('capture_now')['success'])
            old = self.runtime.snapshot()['pendingRenderToken']
            self.session.refreshRuntime()
            self.coordinator.windows.show.reset_mock()
            self.backend.on_capture = lambda: (entered.set(), release.wait(5))
            self.session.dispatch('capture_now')
            try:
                self.assertTrue(entered.wait(2), 'real capture backend did not start')
                APP.processEvents()  # Deliver the real queued ready callback.
                self.assertTrue(self.session.busy)
                self.assertTrue(self.runtime.snapshot()['captureInProgress'])
                self.assertIsNotNone(self.runtime.render_payload(old))
                self.assertEqual(self.coordinator._last_render_token, old)
                self.assertTrue(any(call.args == ('aligned',)
                    for call in self.coordinator.windows.show.call_args_list))
            finally:
                release.set()
                self.settle()

    def test_active_recognition_navigation_preserves_real_qquick_focus(self):
        main, game = QQuickWindow(), QQuickWindow()
        main.setObjectName('assistant-main'); game.setObjectName('fixture-game')
        windows = WindowController()
        windows.bindWindow('main', main)
        self.coordinator.windows = windows
        try:
            main.hide(); game.show(); game.requestActivate(); APP.processEvents()
            self.assertIs(APP.focusWindow(), game)
            self.coordinator.navigate('recognition'); APP.processEvents()
            self.assertIs(APP.focusWindow(), game)
            self.assertFalse(main.isVisible())
            main.hide(); game.requestActivate(); APP.processEvents()
            self.coordinator.navigate('settings'); APP.processEvents()
            self.assertTrue(main.isVisible())
            self.assertIs(APP.focusWindow(), main)
        finally:
            main.hide(); game.hide()
            main.deleteLater(); game.deleteLater(); windows.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_navigation_during_round_does_not_request_recognition_activation(self):
        window = Mock(); self.coordinator.windows._windows['main'] = window
        self.coordinator.navigate('recognition')
        window.requestActivate.assert_not_called()
        self.coordinator.navigate('settings')
        window.requestActivate.assert_called_once()

    def test_direct_pre_capture_foreground_rejection_does_not_erase_valid_token(self):
        token = self.runtime.snapshot()['pendingRenderToken']
        receipt = self.live.dispatch('capture_now')
        self.assertFalse(receipt['success'])
        self.assertEqual(self.runtime.snapshot()['pendingRenderToken'], token)
        self.assertEqual(len(self.backend.captures), 1)
        # Retention is not authorization while another window is foreground.
        self.assertIsNone(self.runtime.render_payload(token))
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

if __name__ == '__main__': unittest.main()
