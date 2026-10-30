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
    mapInputRequested = Signal(object)
    gamePointerEvent = Signal(object)


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
        self.session.dispatchWithPayload = Mock()
        self.session.cancelPending = Mock()
        self.resources.busy = False
        self.resources.pendingKind = ""
        self.input.enabled = True
        self.input.configureSettings = Mock(side_effect=self._configure_input)
        self.input.enable = Mock(side_effect=self._enable_input)
        self.input.disable = Mock()
        self.input.setTextEntryActive = Mock()
        self.input.setEditingBindings = Mock()
        self.input.setOrderedMapInputEnabled = Mock()
        self.input.resetMapInput = Mock()
        self.input.setGamePointerContextProvider = Mock()
        self.backend.refresh = Mock()
        self.windows = SimpleNamespace(hide=Mock(), show=Mock(), setClickThrough=Mock(),
                                       snapshot={}, _windows={})
        self.images = SimpleNamespace(clear=Mock())
        self.runtime_state = dict(mode="nightmare", profileId="profile-one")
        self.runtime = SimpleNamespace(configure_settings=Mock(), invalidate=Mock(),
            snapshot=Mock(side_effect=lambda: dict(self.runtime_state)),
            render_payload=Mock(return_value=None), set_event_callback=Mock())
        self.runtime.game_floor_click_context = Mock(return_value=None)
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

    def test_author_context_change_keeps_current_display_during_reprojection(self):
        self.coordinator._aligned = dict(token="old-valid")
        self.coordinator._reference = dict(key="reference:old-valid")
        self.settings.persistedValues["routeProfileId"] = "profile-two"
        self.session.reconfigure.side_effect = lambda *args: setattr(self.session, "busy", True) or True
        self.settings.saved.emit()
        self.runtime.invalidate.assert_not_called()
        self.assertEqual(self.coordinator._aligned["token"], "old-valid")
        self.assertEqual(self.coordinator._reference["key"], "reference:old-valid")
        self.session.snapshot["pendingAction"] = "set_context"
        self.coordinator._session_changed()
        self.windows.hide.assert_not_called()

    def map_edge(self, sequence, opening):
        self.input.mapInputRequested.emit(dict(sequence=sequence, openIntent=opening,
            createdAt=100. + sequence, hwnd=123, source="hotkey"))

    def pointer_fixture(self):
        context = dict(epoch=1, mode="nightmare", profileId="profile-one", roundId=1,
            cycleId=2, frameId="1:2", generation=2, captureGeneration=1,
            clientBounds=[0, 0, 1000, 1000], fullClientSize=[1000, 1000], dpi=96)
        self.runtime.game_floor_click_context.return_value = context
        self.runtime.observe_game_floor_click = Mock(return_value=dict(success=False, reason="floor-click-not-a-control"))
        return context

    def test_ordered_rapid_map_edges_close_urgently_keep_reference_and_reject_late_sequence(self):
        self.input.setOrderedMapInputEnabled.assert_called_once_with(True)
        self.coordinator._reference = dict(key="correct-static-map")
        order = []
        self.runtime.invalidate.side_effect = lambda reason: order.append(("revoke", reason))
        self.session.cancelPending.side_effect = lambda reason: order.append(("cancel", reason))
        self.session.dispatchWithPayload.side_effect = lambda action, data: order.append(("queue", action))
        self.map_edge(1, True)
        self.session.busy = True
        self.map_edge(2, False)
        self.map_edge(3, True)
        self.map_edge(2, False)
        calls = self.session.dispatchWithPayload.call_args_list
        self.assertEqual([c.args[0] for c in calls], ["map", "close_map", "map"])
        self.assertEqual([c.args[1]["inputSequence"] for c in calls], [1, 2, 3])
        self.assertLess(order.index(("revoke", "map-closed")), order.index(("cancel", "map-closed")))
        self.assertLess(order.index(("cancel", "map-closed")), order.index(("queue", "close_map")))
        self.assertEqual(self.coordinator.referenceData["key"], "correct-static-map")

    def test_preparation_is_allowed_without_game_and_toggle_cancels_preparation(self):
        self.coordinator._bound = ""
        self.session.snapshot.update(roundActive=False, roundPreparing=False)
        self.coordinator.startRound()
        self.session.dispatch.assert_called_with("start_round")
        self.input.enable.assert_not_called()
        self.session.snapshot["roundPreparing"] = True
        self.coordinator._native_action("toggle-round")
        self.session.dispatch.assert_called_with("end_round")

    def test_new_physical_edge_prevents_late_focus_return_or_bind_receipt_from_resetting_intent(self):
        self.native.foreground_window.return_value = 456
        self.coordinator._watch_foreground()
        self.input.resetMapInput.assert_called_with(False)
        self.native.foreground_window.return_value = 123
        self.map_edge(1, True)
        count = self.input.resetMapInput.call_count
        self.coordinator._watch_foreground()
        self.session.snapshot["lastReceipt"] = dict(action="bind_capture", success=True)
        self.coordinator._session_changed()
        self.assertEqual(self.input.resetMapInput.call_count, count)

    def test_end_rebind_and_mouse_close_discard_previous_focus_return_plan_without_new_g(self):
        for boundary in ("end_round", "bind_capture", "mouse-close"):
            with self.subTest(boundary=boundary):
                self.native.foreground_window.return_value = 456
                self.session.snapshot.update(roundActive=True, mapOpen=True)
                self.coordinator._watch_foreground()
                self.assertTrue(self.coordinator._waiting_foreground)
                self.assertTrue(self.coordinator._map_was_open)
                sequence = self.coordinator._map_input_sequence
                if boundary == "mouse-close":
                    self.native.foreground_window.return_value = 123
                    self.coordinator._close_map_from_input(dict(inputSequence=sequence, inputSource=boundary))
                else:
                    self.coordinator._action_requested(boundary, {})
                self.assertFalse(self.coordinator._waiting_foreground)
                self.assertFalse(self.coordinator._map_was_open)
                self.assertIsNone(self.coordinator._focus_loss_map_sequence)
                self.assertEqual(self.coordinator._map_input_sequence, sequence)
                self.session.dispatch.reset_mock()
                self.native.foreground_window.return_value = 123
                self.coordinator._watch_foreground()
                self.session.dispatch.assert_not_called()

    def test_background_ticks_during_end_or_rebind_cannot_rearm_old_session_snapshot(self):
        for boundary in ("end_round", "bind_capture"):
            with self.subTest(boundary=boundary):
                self.session.busy = False
                self.session.snapshot.update(roundActive=True, mapOpen=True)
                self.runtime_state.update(roundActive=True, mapOpen=True)
                self.native.foreground_window.return_value = 456
                self.coordinator._watch_foreground()
                self.assertTrue(self.coordinator._waiting_foreground)
                self.coordinator._action_requested(boundary, {})
                resets = self.input.resetMapInput.call_count
                self.session.busy = True
                self.session.snapshot["pendingAction"] = boundary
                for _ in range(3):
                    self.coordinator._watch_foreground()
                self.assertFalse(self.coordinator._waiting_foreground)
                self.assertFalse(self.coordinator._map_was_open)
                self.assertEqual(self.input.resetMapInput.call_count, resets)
                # Real runtime finishes while Session's applied state is old.
                self.runtime_state.update(roundActive=False, mapOpen=False)
                self.session.busy = False
                self.coordinator._watch_foreground()
                self.assertIsNone(self.coordinator._focus_loss_map_sequence)
                # A newly prepared/started round has no new physical G edge.
                self.runtime_state.update(roundActive=False, roundPreparing=True)
                self.coordinator._watch_foreground()
                self.runtime_state.update(roundActive=True, roundPreparing=False)
                self.native.foreground_window.return_value = 123
                self.session.dispatch.reset_mock()
                self.coordinator._watch_foreground()
                self.session.dispatch.assert_not_called()

    def test_mouse_x_uses_urgent_close_but_rejected_native_context_cannot_close(self):
        context = self.pointer_fixture()
        self.coordinator._reference = dict(key="correct-static-map")
        event = dict(phase="down", gestureId=1, sequence=1, hwnd=123, x=950, y=50,
            createdAt=10., context=context)
        self.input.gamePointerEvent.emit(event)
        self.runtime.invalidate.assert_called_with("map-closed")
        self.assertEqual(self.session.dispatchWithPayload.call_args.args[0], "close_map")
        self.assertEqual(self.coordinator.referenceData["key"], "correct-static-map")
        self.session.dispatchWithPayload.reset_mock()
        self.runtime.observe_game_floor_click.return_value = dict(success=False, reason="capture-context-changed")
        self.input.gamePointerEvent.emit(dict(event, sequence=2, gestureId=2))
        self.session.dispatchWithPayload.assert_not_called()

    def test_drag_fails_closed_without_runtime_api_and_uses_approved_calibrated_path_when_available(self):
        context = self.pointer_fixture()
        down = dict(phase="down", gestureId=1, sequence=1, hwnd=123, x=300, y=300,
            createdAt=10., context=context)
        self.input.gamePointerEvent.emit(down)
        self.assertIsNone(self.coordinator._pointer_drag)
        self.runtime.invalidate.assert_not_called()
        self.runtime.observe_game_drag = Mock(return_value=dict(success=True))
        self.settings.persistedValues["dragBoundaryNormalized"] = [.2, .2, .8, .8]
        self.coordinator._present_runtime_state = Mock()
        self.input.gamePointerEvent.emit(dict(down, sequence=2, gestureId=2))
        self.input.gamePointerEvent.emit(dict(down, phase="move", sequence=3, gestureId=2, x=700,
            motionSamples=[dict(x=x, y=300) for x in (350, 900, 750, 700)]))
        self.input.gamePointerEvent.emit(dict(down, phase="up", sequence=4, gestureId=2, x=720))
        calls = [c.args[0] for c in self.runtime.observe_game_drag.call_args_list]
        self.assertEqual([c["phase"] for c in calls], ["down", "move", "up"])
        self.assertEqual(calls[1]["totalDelta"], [0., 0.])
        self.assertEqual(calls[2]["totalDelta"], [20., 0.])
        self.assertNotIn("matrix", calls[2])
        self.session.cancelPending.assert_called_once_with("mouse-drag-motion")
        self.assertIsNone(self.coordinator._pointer_drag)

    def test_drag_context_change_cancels_and_tracking_is_paused_until_end(self):
        context = self.pointer_fixture()
        self.runtime.observe_game_drag = Mock(return_value=dict(success=True))
        self.input.gamePointerEvent.emit(dict(phase="down", gestureId=1, sequence=1,
            hwnd=123, x=300, y=300, context=context, createdAt=10.))
        self.coordinator._track_map()
        self.session.dispatch.assert_not_called()
        self.runtime.game_floor_click_context.return_value = dict(context, epoch=2)
        self.input.gamePointerEvent.emit(dict(phase="move", gestureId=1, sequence=2,
            hwnd=123, x=330, y=300, context=context, createdAt=11.))
        self.assertEqual(self.runtime.observe_game_drag.call_args.args[0]["phase"], "cancel")
        self.assertIsNone(self.coordinator._pointer_drag)

    def test_mouseup_requests_only_scoped_observation_probe_when_runtime_requires_it(self):
        context = self.pointer_fixture()
        self.runtime.observe_game_drag = Mock(side_effect=lambda event:
            dict(success=True, probeRequired=event["phase"] == "up"))
        self.coordinator._present_runtime_state = Mock()
        down = dict(phase="down", gestureId=7, sequence=1, hwnd=123, x=300, y=300,
            context=context, createdAt=10.)
        self.input.gamePointerEvent.emit(down)
        self.input.gamePointerEvent.emit(dict(down, phase="up", sequence=2, x=320))
        self.session.refreshRuntime.assert_called_once()
        self.session.dispatchWithPayload.assert_called_once_with("probe_map_after_drag",
            dict(gestureId=7, pointerSequence=2, context=context))
        self.session.dispatch.assert_not_called()

    def test_material_ack_and_missing_route_notice_require_current_loaded_token(self):
        payload = dict(token="material-1", mode="nightmare", mapId="map-1", floor="F1",
            roundId=1, cycleId=1, frameId="1:1", generation=1, resourceIdentity=["v1"],
            liveAuthorization=True, clientWidth=800, clientHeight=600, deviceScale=1,
            originX=-800, originY=100, displayKind="base-map", authorStatus="missing")
        self.coordinator._aligned = deepcopy(payload)
        self.runtime.render_payload.return_value = deepcopy(payload)
        self.runtime.acknowledge_render_material = Mock()
        notices = []
        self.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
        details = dict(token="stale", visible=True, materialReady=True,
            sourceMatchesPayload=True, validPayload=True, geometry=[-800,100,800,600])
        self.coordinator.reportPresentation("aligned", details)
        self.runtime.acknowledge_render_material.assert_not_called()
        details["token"] = payload["token"]
        details["materialReady"] = False
        self.coordinator.reportPresentation("aligned", details)
        self.runtime.acknowledge_render_material.assert_not_called()
        details["materialReady"] = True
        self.coordinator.reportPresentation("aligned", details)
        self.coordinator.reportPresentation("aligned", details)
        self.runtime.acknowledge_render_material.assert_called_once_with("material-1")
        self.assertEqual(len(notices), 1)
        self.assertIn("基础地图", notices[0][1])
        self.runtime.render_payload.return_value = None
        self.coordinator.reportPresentation("aligned", details)
        self.assertEqual(len(notices), 1)

    def test_preview_ready_enters_edit_without_session_or_pose_authorization(self):
        class Provider(QObject):
            ready = Signal(object)
            failed = Signal(str)
        provider = Provider()
        provider.request, provider.cancel = Mock(), Mock()
        self.coordinator.setReferencePreviewProvider(provider)
        self.images.publish = Mock(return_value="image://live/reference:preview:1")
        self.images.clear_reference = Mock()
        self.windows._windows["reference"] = SimpleNamespace(x=lambda:9000, y=lambda:-500,
            width=lambda:520, height=lambda:520, setX=Mock(), setY=Mock(),
            property=lambda name: None, setProperty=Mock(), setScreen=Mock())
        self.windows.snapshot["reference"] = dict(clickThrough=True)
        self.coordinator.beginReferenceEdit()
        provider.ready.emit(dict(context=self.coordinator._reference_preview_context,
            requestId=1, pixels=object(), previewOnly=True, referenceAuthorized=False,
            liveAuthorization=False, mode="nightmare", mapId="map-1", floor="F1"))
        self.assertTrue(self.coordinator.referenceEditing)
        self.assertTrue(self.coordinator.referenceData["previewOnly"])
        self.assertFalse(self.coordinator.referenceData["referenceAuthorized"])
        self.windows.show.assert_called_with("reference")
        self.session.dispatch.assert_not_called()
        self.coordinator.cancelReferenceEdit()
        self.assertFalse(self.coordinator.referenceData)
        self.windows.hide.assert_called_with("reference")

    def test_automatic_bind_and_startup_respect_existing_legacy_instance(self):
        self.input.enableManagement = Mock()
        self.coordinator._catalog = Mock(return_value=[])
        self.coordinator._automatic_live = True
        with patch("client_coordinator.another_assistant_running", return_value=True):
            self.coordinator.startLiveServices()
            self.input.enableManagement.assert_not_called()
            self.coordinator._catalog.assert_not_called()
            self.session.snapshot["lastReceipt"] = dict(action="bind_capture", success=True)
            self.input.enabled = False
            self.coordinator._session_changed()
            self.input.enable.assert_not_called()
        self.coordinator._discovery_timer.stop()

    def test_reference_uses_one_focus_policy_for_publish_and_editing(self):
        self.coordinator._reference = {"key": "reference:current"}
        self.native.foreground_window.return_value = 456
        self.native.window_process_id = Mock(return_value=os.getpid())
        self.coordinator._show_reference_current()
        self.windows.show.assert_not_called()
        self.windows.hide.assert_called_with("reference")
        self.coordinator._edit = SimpleNamespace(cancel=Mock())
        self.coordinator._show_reference_current()
        self.windows.show.assert_called_with("reference")
        self.windows.show.reset_mock()
        self.native.window_process_id.return_value = os.getpid() + 1
        self.coordinator._show_reference_current()
        self.windows.show.assert_not_called()
        self.coordinator._edit = None
        self.native.foreground_window.return_value = 123
        self.coordinator._show_reference_current()
        self.windows.show.assert_called_with("reference")

    def test_focus_round_trip_revokes_worker_lifetime_once_before_return(self):
        self.session.cancelPending = Mock()
        self.native.foreground_window.return_value = 456
        self.coordinator._watch_foreground()
        self.runtime.invalidate.assert_called_once_with("foreground-lost")
        self.session.cancelPending.assert_called_once_with("foreground-lost")
        self.coordinator._watch_foreground()
        self.assertEqual(self.runtime.invalidate.call_count, 1)
        self.native.foreground_window.return_value = 123
        self.session.busy = True
        self.coordinator._watch_foreground()
        self.session.dispatch.assert_not_called()
        self.session.busy = False
        self.coordinator._watch_foreground()
        self.session.dispatch.assert_called_once_with("capture_now")

    def test_position_preview_is_display_only_and_late_reply_is_cancelled(self):
        class Provider(QObject):
            ready = Signal(object)
            failed = Signal(str)
        provider = Provider()
        provider.request, provider.cancel = Mock(), Mock()
        self.coordinator.setReferencePreviewProvider(provider)
        self.coordinator.beginReferenceEdit()
        context = self.coordinator._reference_preview_context
        self.assertEqual(context[:2], self.coordinator._applied_context)
        self.coordinator.cancelReferenceEdit()
        provider.cancel.assert_called_once_with()
        provider.ready.emit(dict(context=context, requestId=1, pixels=object(),
            previewOnly=True, referenceAuthorized=False, liveAuthorization=False))
        self.assertFalse(self.coordinator._reference)
        self.session.dispatch.assert_not_called()
        self.runtime.render_payload.assert_not_called()

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
