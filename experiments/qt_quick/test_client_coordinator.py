"""Coordinator contracts using QObject signals and fake services only.

No native input, native window inspection, game discovery, capture, or QWindow
is constructed. Queued runtime events use the real Qt event loop.
"""
from copy import deepcopy
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QObject, Signal

from client_coordinator import ClientCoordinator


class Service(QObject):
    changed = Signal()
    saved = Signal()
    actionRequested = Signal(str, object)
    resourcesChanged = Signal()
    actionTriggered = Signal(str)
    replayOverlayRequested = Signal()
    overlayRequested = Signal()


class CoordinatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.settings, self.session, self.resources, self.input, self.backend = [Service() for _ in range(5)]
        self.settings.persistedValues = dict(gameMode="nightmare", routeProfileId="profile-one",
            modeRouteProfiles={}, hotkey="G", actionHotkeys=["F9"], sheetOpacity=.55,
            sheetVisibilityEnabled=True, referenceSheetVisible=True, orbVisible=True)
        self.settings.values = deepcopy(self.settings.persistedValues)
        self.settings.busy = False
        self.session.busy = False
        self.session.snapshot = dict(roundActive=True, mapOpen=True, resourceLocked=False,
            availableCommands=["refresh_map"], alignedVisible=False)
        self.session.message = "fake context deferred"
        self.session.reconfigure = Mock(return_value=True)
        self.session.refreshRuntime = Mock()
        self.session.dispatch = Mock()
        self.resources.busy = False
        self.resources.pendingKind = ""
        self.input.enabled = True
        self.input.configureSettings = Mock(side_effect=self._configure_input)
        self.input.enable = Mock(side_effect=self._enable_input)
        self.input.disable = Mock()
        self.backend.refresh = Mock()
        self.windows = SimpleNamespace(hide=Mock(), show=Mock(), setClickThrough=Mock(),
                                       snapshot={}, _windows={})
        self.images = SimpleNamespace(clear=Mock())
        self.runtime_state = dict(mode="nightmare", profileId="profile-one")
        self.runtime = SimpleNamespace(configure_settings=Mock(), invalidate=Mock(),
            snapshot=Mock(side_effect=lambda: dict(self.runtime_state)),
            render_payload=Mock(return_value=None), set_event_callback=Mock())
        self.native = SimpleNamespace(foreground_window=Mock(return_value=123))
        with patch("client_input.NativeWindowAPI", return_value=self.native):
            self.coordinator = ClientCoordinator(backend=self.backend, settings=self.settings,
                resources=self.resources, session=self.session, windows=self.windows,
                native_input=self.input, capture=SimpleNamespace(), runtime=self.runtime,
                images=self.images, window_catalog=Mock(side_effect=AssertionError("discovery forbidden")))
        self.coordinator._bound = "123"
        # Drive bounded callbacks explicitly; no polling timer is part of these tests.
        self.coordinator._foreground_timer.stop()
        self.coordinator._tracking_timer.stop()

    def tearDown(self):
        self.coordinator._closed = True
        for timer in (self.coordinator._foreground_timer, self.coordinator._tracking_timer,
                      self.coordinator._context_timer, self.coordinator._close_timer):
            timer.stop()
        self.app.processEvents()

    def _configure_input(self, values):
        # Mirrors the backend's important lifecycle seam: reconfigure disables
        # observation and therefore must not run for an opacity-only save.
        self.input.enabled = False
        return True

    def _enable_input(self):
        self.input.enabled = True
        return True

    def test_opacity_save_preserves_enabled_input_and_uses_persisted_settings(self):
        self.settings.persistedValues["sheetOpacity"] = .8
        self.settings.values["hotkey"] = "F12"  # unrelated open settings draft
        self.settings.saved.emit()
        self.assertTrue(self.input.enabled)
        self.input.configureSettings.assert_not_called()
        self.input.enable.assert_not_called()
        self.runtime.configure_settings.assert_called_once_with(self.settings.persistedValues)
        self.session.reconfigure.assert_not_called()
        self.runtime.invalidate.assert_not_called()

    def test_changed_bindings_reconfigure_once_and_restore_explicit_input_enable(self):
        self.settings.persistedValues["hotkey"] = "Ctrl+G"
        self.settings.saved.emit()
        self.input.configureSettings.assert_called_once_with(self.settings.persistedValues)
        self.input.enable.assert_called_once_with()
        self.assertTrue(self.input.enabled)
        self.settings.saved.emit()
        self.assertEqual(self.input.configureSettings.call_count, 1)
        self.assertEqual(self.input.enable.call_count, 1)

    def test_atomic_context_is_only_acknowledged_after_actual_runtime_matches(self):
        self.settings.persistedValues.update(gameMode="difficult", routeProfileId="fallback",
                                             modeRouteProfiles={"difficult": "profile-two"})
        def accepted(mode, profile):
            self.session.busy = True
            return True
        self.session.reconfigure.side_effect = accepted
        self.settings.saved.emit()
        self.session.reconfigure.assert_called_once_with("difficult", "profile-two")
        self.runtime.invalidate.assert_called_once_with("preferences-committed")
        self.assertEqual(self.coordinator._pending_context, ("difficult", "profile-two"))
        self.assertEqual(self.coordinator._applied_context, ("nightmare", "profile-one"))
        self.session.busy = False
        self.runtime_state.update(mode="difficult", profileId="old-profile")
        self.session.changed.emit()
        self.assertEqual(self.coordinator._applied_context, ("nightmare", "profile-one"))
        self.assertEqual(self.coordinator._pending_context, ("difficult", "profile-two"))
        self.runtime_state["profileId"] = "profile-two"
        self.session.changed.emit()
        self.assertEqual(self.coordinator._applied_context, ("difficult", "profile-two"))
        self.assertIsNone(self.coordinator._pending_context)

    def test_context_waits_for_resource_operations_and_locked_session(self):
        self.coordinator._pending_context = ("difficult", "profile-two")
        for busy, pending, locked in ((True, "", False), (False, "import", False), (False, "", True)):
            with self.subTest(busy=busy, pending=pending, locked=locked):
                self.resources.busy, self.resources.pendingKind = busy, pending
                self.session.snapshot["resourceLocked"] = locked
                self.coordinator._try_apply_context()
                self.session.reconfigure.assert_not_called()

    def test_resource_invalidation_clears_both_display_identity_and_runtime_resources(self):
        self.coordinator._reference = {"key": "old-reference"}
        self.coordinator._aligned = {"token": "old-token"}
        self.coordinator._last_render_token = "old-token"
        self.resources.resourcesChanged.emit()
        self.assertEqual(self.coordinator._reference, {})
        self.assertEqual(self.coordinator._aligned, {})
        self.assertIsNone(self.coordinator._last_render_token)
        self.windows.hide.assert_called_with("aligned")
        self.images.clear.assert_called_once_with()
        self.runtime.invalidate.assert_called_once_with("resources-changed")
        self.session.refreshRuntime.assert_called_once_with()
        self.backend.refresh.assert_called_once_with()

    def test_tracking_refresh_has_busy_resource_round_and_foreground_guards(self):
        cases = ("session_busy", "resource_busy", "pending_resource", "resource_locked",
                 "background", "map_closed", "round_inactive", "command_missing", "closing", "closed", "unbound")
        for case in cases:
            with self.subTest(case=case):
                self.session.busy = self.resources.busy = False
                self.resources.pendingKind = ""
                self.session.snapshot.update(resourceLocked=False, mapOpen=True, roundActive=True,
                                             availableCommands=["refresh_map"])
                self.coordinator._closed = self.coordinator._closing = False
                self.coordinator._bound = "123"
                self.native.foreground_window.return_value = 123
                if case == "session_busy": self.session.busy = True
                elif case == "resource_busy": self.resources.busy = True
                elif case == "pending_resource": self.resources.pendingKind = "import"
                elif case == "resource_locked": self.session.snapshot["resourceLocked"] = True
                elif case == "background": self.native.foreground_window.return_value = 456
                elif case == "map_closed": self.session.snapshot["mapOpen"] = False
                elif case == "round_inactive": self.session.snapshot["roundActive"] = False
                elif case == "command_missing": self.session.snapshot["availableCommands"] = []
                elif case == "closing": self.coordinator._closing = True
                elif case == "closed": self.coordinator._closed = True
                elif case == "unbound": self.coordinator._bound = ""
                self.coordinator._track_map()
                self.session.dispatch.assert_not_called()
        self.coordinator._bound = "123"
        self.coordinator._track_map()
        self.session.dispatch.assert_called_once_with("refresh_map")

    def test_queued_old_revoke_preserves_a_valid_new_render_token(self):
        self.coordinator._aligned = {"token": "new-token"}
        self.coordinator._last_render_token = "new-token"
        self.runtime.render_payload.side_effect = lambda token: {"liveAuthorization": True} if token == "new-token" else None
        self.coordinator.runtimeEvent.emit({"event": "render-revoked", "token": "old-token"})
        self.app.processEvents()
        self.assertEqual(self.coordinator._last_render_token, "new-token")
        self.assertEqual(self.coordinator._aligned, {"token": "new-token"})
        self.windows.hide.assert_not_called()
        self.images.clear.assert_not_called()
        self.runtime.render_payload.side_effect = None
        self.runtime.render_payload.return_value = None
        self.coordinator.runtimeEvent.emit({"event": "render-revoked", "token": "new-token"})
        self.app.processEvents()
        self.assertIsNone(self.coordinator._last_render_token)
        self.windows.hide.assert_called_once_with("aligned")


if __name__ == "__main__":
    unittest.main(verbosity=2)
