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
    shortcutRecordingChanged = Signal(bool)


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
        self.input.setTextEntryActive = Mock()
        self.input.setEditingBindings = Mock()
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
                      self.coordinator._context_timer, self.coordinator._close_timer,
                      self.coordinator._material_timer):
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

    def test_close_ignores_late_presentation_and_native_callbacks(self):
        self.coordinator._closing = True
        self.settings.persistedValues["hotkey"] = "Ctrl+F12"
        self.settings.saved.emit()
        self.settings.changed.emit()
        self.resources.resourcesChanged.emit()
        self.session.changed.emit()
        self.coordinator._native_action("toggle-panel")
        self.coordinator._watch_foreground()
        self.input.configureSettings.assert_not_called()
        self.input.enable.assert_not_called()
        self.runtime.configure_settings.assert_not_called()
        self.session.refreshRuntime.assert_not_called()
        self.backend.refresh.assert_not_called()
        self.native.foreground_window.assert_not_called()
        self.windows.show.assert_not_called()
        self.windows.hide.assert_not_called()
        self.assertFalse(self.coordinator._context_timer.isActive())

    def test_return_to_game_failure_is_visible_in_the_main_status(self):
        self.coordinator._game_focus = SimpleNamespace(requestFocus=Mock(return_value=dict(
            success=False, reason="foreground-denied", message="系统未允许切回游戏，请手动切换")))
        self.coordinator.returnToGame()
        self.assertEqual(self.coordinator.runtimeStatus, "系统未允许切回游戏，请手动切换")
        self.coordinator._action_requested("capture_now", {})
        self.assertNotEqual(self.coordinator.runtimeStatus, "系统未允许切回游戏，请手动切换")

    def test_changed_bindings_reconfigure_once_and_restore_explicit_input_enable(self):
        self.settings.persistedValues["hotkey"] = "Ctrl+G"
        self.settings.saved.emit()
        self.input.configureSettings.assert_called_once_with(self.settings.persistedValues)
        self.input.enable.assert_called_once_with()
        self.assertTrue(self.input.enabled)
        self.settings.saved.emit()
        self.assertEqual(self.input.configureSettings.call_count, 1)
        self.assertEqual(self.input.enable.call_count, 1)

    def test_recording_and_text_focus_guards_do_not_clear_each_other(self):
        self.coordinator.setTextEntryActive(True)
        self.settings.shortcutRecordingChanged.emit(True)
        self.coordinator.setTextEntryActive(False)
        self.assertTrue(self.coordinator.textEntryActive)
        self.input.setTextEntryActive.assert_called_with(True)
        self.input.setEditingBindings.assert_called_with(True)
        self.coordinator.setTextEntryActive(True)
        self.settings.shortcutRecordingChanged.emit(False)
        self.assertTrue(self.coordinator.textEntryActive)
        self.input.setTextEntryActive.assert_called_with(True)
        self.input.setEditingBindings.assert_called_with(False)
        self.coordinator.setTextEntryActive(False)
        self.assertFalse(self.coordinator.textEntryActive)
        self.input.setTextEntryActive.assert_called_with(False)
        self.assertTrue(self.input.enabled)

    def test_reselection_and_same_candidate_keep_current_pixels_until_real_change(self):
        self.coordinator._aligned = {"token": "current"}
        self.coordinator._last_render_token = "current"
        self.session.snapshot.update(selectedCandidateIndex=1, pendingAction="reselect_map")
        self.session.busy = True
        self.coordinator._session_changed()
        self.coordinator._action_requested("reselect_map", {})
        self.coordinator._action_requested("select_candidate", {"index": 1})
        self.assertEqual(self.coordinator._last_render_token, "current")
        self.windows.hide.assert_not_called()
        self.coordinator._action_requested("select_candidate", {"index": 0})
        self.assertIsNone(self.coordinator._last_render_token)
        self.windows.hide.assert_called_with("aligned")

    def test_return_to_game_only_uses_explicit_focus_receipt(self):
        self.coordinator._game_focus.requestFocus = Mock(return_value={
            "success": False, "reason": "foreground-denied", "message": "请手动切回游戏"})
        self.coordinator._game_focus.requestFocus.assert_not_called()
        self.coordinator.returnToGame()
        self.coordinator._game_focus.requestFocus.assert_called_once_with()
        self.assertEqual(self.coordinator.message, "请手动切回游戏")
        self.windows.show.assert_not_called()
        self.windows.hide.assert_not_called()

    def test_atomic_context_is_only_acknowledged_after_actual_runtime_matches(self):
        self.session.snapshot["roundActive"] = False
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
        self.windows.hide.assert_any_call("aligned")
        self.windows.hide.assert_any_call("reference")
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

    def test_material_ack_is_exact_and_never_changes_session_transaction(self):
        """A queued Image callback cannot acknowledge a later target or job."""
        self.session.busy = True
        before = deepcopy(self.session.snapshot)
        pending = dict(token="target", liveAuthorization=True, mode="nightmare",
                       mapId="target-map", floor="1", routeUrl="image://live/pending:target")
        self.coordinator._pending_aligned = pending
        self.runtime.acknowledge_sibling = Mock(return_value=dict(success=True, token="target"))
        with patch.object(self.coordinator, "_present_runtime_state") as present:
            self.coordinator.acceptSiblingMaterial("stale", True)
            self.runtime.acknowledge_sibling.assert_not_called()
            self.coordinator.acceptSiblingMaterial("target", True)
            self.runtime.acknowledge_sibling.assert_called_once_with("target", True, reason="target-material-ready")
            present.assert_called_once_with(self.runtime_state, trigger="sibling-material-accepted")
        self.assertEqual(self.coordinator._accepted_material, pending)
        self.assertEqual(self.session.snapshot, before)
        self.session.refreshRuntime.assert_not_called()
        self.coordinator.acceptSiblingMaterial("target", True)
        self.assertEqual(self.runtime.acknowledge_sibling.call_count, 1)

    def test_material_failure_and_timeout_keep_valid_source_surface(self):
        self.coordinator._aligned = dict(token="source", routeUrl="image://live/source")
        self.coordinator._last_render_token = "source"
        self.coordinator._pending_aligned = dict(token="target")
        self.runtime.acknowledge_sibling = Mock(return_value=dict(success=False, reason="paint-rejected", sourceRetained=True))
        self.coordinator.acceptSiblingMaterial("target", False)
        self.assertEqual(self.coordinator._aligned["token"], "source")
        self.assertEqual(self.coordinator._last_render_token, "source")
        self.windows.hide.assert_not_called()
        self.coordinator._pending_aligned = dict(token="next")
        self.coordinator._cancel_pending_material("target-material-timeout")
        self.runtime.acknowledge_sibling.assert_called_with("next", False, reason="target-material-timeout")
        self.assertEqual(self.coordinator._aligned["token"], "source")
        self.assertEqual(self.coordinator._pending_aligned, {})

    def test_recorded_pending_material_cannot_enter_game_presenter(self):
        self.runtime.pending_sibling_payload = Mock(return_value=dict(token="recorded", liveAuthorization=False))
        self.runtime.acknowledge_sibling = Mock()
        self.coordinator._prepare_sibling_material(dict(token="recorded"))
        self.assertEqual(self.coordinator._pending_aligned, {})
        self.runtime.acknowledge_sibling.assert_not_called()
        self.windows.show.assert_not_called()

    def test_queued_rollback_discards_target_material_without_losing_source(self):
        import numpy as np
        from client_coordinator import LiveImages
        self.coordinator.images = images = LiveImages()
        pixels = np.full((3, 4, 4), 255, dtype=np.uint8)
        for key in ("source", "reference:source", "pending:target"):
            images.publish(key, pixels)
        self.assertEqual(len(images._images), 3)
        self.coordinator._aligned = dict(token="source")
        self.coordinator._pending_aligned = dict(token="target")
        self.coordinator.runtimeEvent.emit(dict(event="sibling-rolled-back", token="target"))
        self.app.processEvents()
        self.assertEqual(set(images._images), {"source", "reference:source"})
        self.assertEqual(self.coordinator._aligned, dict(token="source"))
        self.assertEqual(self.coordinator.pendingAlignedData, {})
        self.windows.hide.assert_not_called()

    def test_invalid_target_geometry_does_not_keep_an_expired_source(self):
        self.coordinator._aligned = dict(token="expired-source")
        self.coordinator._last_render_token = "expired-source"
        self.runtime.pending_sibling_payload = Mock(return_value=dict(token="target", liveAuthorization=True))
        self.runtime.acknowledge_sibling = Mock(return_value=dict(success=False, sourceRetained=False))
        with patch.object(self.coordinator, "_convert_client_geometry", side_effect=ValueError("screen-changed")):
            self.coordinator._prepare_sibling_material(dict(token="target"))
        self.assertEqual(self.coordinator.alignedData, {})
        self.assertIsNone(self.coordinator._last_render_token)
        self.windows.hide.assert_called_with("aligned")

    def test_floor_click_clears_old_layer_and_queues_distinct_ordered_refresh(self):
        self.session.busy = True
        self.session.dispatchWithPayload = Mock()
        self.runtime.observe_game_floor_click = Mock(return_value=dict(success=True,
            reason="game-floor-control-intent", clickedFloorId="2", captureRequired=True))
        self.coordinator._aligned = dict(token="old-floor")
        self.coordinator._reference = dict(key="old-floor-reference")
        for sequence in (1, 2):
            self.coordinator._game_pointer_pressed(dict(hwnd=123, x=42, y=43,
                sequence=sequence, createdAt=100 + sequence, context=dict(cycleId=1)))
        self.assertEqual(self.coordinator.alignedData, {})
        self.assertEqual(self.coordinator.referenceData, {})
        self.assertEqual(self.runtime.observe_game_floor_click.call_count, 2)
        self.assertEqual([call.args[1]["floorClickSequence"] for call in self.session.dispatchWithPayload.call_args_list], [1, 2])

    def test_rejected_or_late_floor_click_does_not_capture(self):
        self.session.dispatchWithPayload = Mock()
        self.runtime.observe_game_floor_click = Mock(return_value=dict(success=False, reason="expired-click"))
        event = dict(hwnd=123, x=42, y=43, sequence=1, context={})
        self.coordinator._game_pointer_pressed(event)
        self.native.foreground_window.return_value = 456
        self.coordinator._game_pointer_pressed(event)
        self.assertEqual(self.runtime.observe_game_floor_click.call_count, 1)
        self.session.dispatchWithPayload.assert_not_called()

    def test_pointer_hook_failure_does_not_report_keyboard_disabled(self):
        errors = []
        self.coordinator.errorReported.connect(errors.append)
        self.input.snapshot = dict(status="ready", floorClickObserverError="fixture-mouse-failure")
        self.coordinator._input_changed(); self.coordinator._input_changed()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["action"], "game-floor-control")
        self.assertNotIn("快捷键暂不可用", errors[0]["title"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
