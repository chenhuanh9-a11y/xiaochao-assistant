"""S1 manual identity transport and QML contracts using synthetic services.

Real coordinator callbacks and offscreen QML are exercised. No native input,
capture, installed resources, game windows or live-game claims are involved.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from copy import deepcopy
import unittest
from unittest.mock import Mock, patch

# Initialize a GUI application before coordinator fixtures request QCoreApplication.
import test_client_recognition as qml_fixture
import test_client_coordinator as coordinator_fixture


class S1FloorCoordinatorTests(unittest.TestCase):
    app = qml_fixture.APP
    tearDown = coordinator_fixture.CoordinatorTests.tearDown
    map_edge = coordinator_fixture.CoordinatorTests.map_edge
    _configure_input = coordinator_fixture.CoordinatorTests._configure_input
    _enable_input = coordinator_fixture.CoordinatorTests._enable_input

    def setUp(self):
        coordinator_fixture.CoordinatorTests.setUp(self)
        self.session.snapshot.update(season="s1", mode="nightmare", mapId="s1-fixture-map",
            roundId=1, floor="-1", observedFloor="-1", targetFloor="-1", floorSource="automatic-current-frame",
            manualAlignmentSession=True, automaticFloorObservationAvailable=True,
            savedFrameCapabilities={"nightmare": dict(available=True, liveReady=False)},
            availableCommands=["fit_manual_frame", "observe_manual_floor", "close_map"])
        self.runtime_state.update(deepcopy(self.session.snapshot))
        self.coordinator._applied_context = ("nightmare", "profile-one", "s1")

    def sync(self, **values):
        self.session.snapshot.update(values)
        self.runtime_state.update(values)

    def test_b1_periodic_observation_close_and_physical_reopen_chain(self):
        with patch("client_coordinator.time.monotonic", return_value=10.):
            self.coordinator._track_map()
            self.coordinator._track_map()
        self.session.dispatch.assert_called_once_with("observe_manual_floor")
        # An independently open canvas on unknown B3 remains eligible to probe.
        self.sync(observedFloor="", floorSource="unknown", rawObservedFloor="-3")
        with patch("client_coordinator.time.monotonic", return_value=10.6):
            self.coordinator._track_map()
        self.assertEqual(self.session.dispatch.call_count, 2)
        self.map_edge(1, False)
        self.assertEqual(self.session.dispatchWithPayload.call_args.args[0], "close_map")
        self.sync(mapOpen=False)
        for now in (11., 12., 50.):
            with patch("client_coordinator.time.monotonic", return_value=now):
                self.coordinator._track_map()
        self.assertEqual(self.session.dispatch.call_count, 2, "passive observations cannot reopen")
        self.map_edge(2, True)
        action, payload = self.session.dispatchWithPayload.call_args.args
        self.assertEqual(action, "fit_manual_frame")
        self.assertIs(payload["s1OpenEdge"], True)
        self.assertEqual(payload["inputSequence"], 2)
        self.assertIsNotNone(self.coordinator._map_resource_problem(), "global S1 live gate stays closed")

    def test_observation_guard_matrix_in_every_mode(self):
        baseline = deepcopy(self.session.snapshot)
        mutations = [dict(roundActive=False), dict(manualAlignmentSession=False),
            dict(mapId=""), dict(automaticFloorObservationAvailable=False), dict(mapOpen=False),
            dict(resourceLocked=True), dict(contextPending=True), dict(availableCommands=[])]
        for mode in ("difficulty2", "difficult", "nightmare"):
            for mutation in mutations:
                with self.subTest(mode=mode, mutation=mutation):
                    self.session.snapshot = dict(baseline, mode=mode, **mutation)
                    self.session.dispatch.reset_mock()
                    self.coordinator._track_map()
                    self.session.dispatch.assert_not_called()
        self.session.snapshot = baseline
        for owner, attribute, value in [(self.session, "busy", True),
                (self.resources, "busy", True), (self.resources, "pendingKind", "import"),
                (self.coordinator, "_pending_context", ("difficult", "profile-one", "s1")),
                (self.coordinator, "_pointer_drag", {}),
                (self.coordinator, "_binding_suspended", True), (self.coordinator, "_bound", "")]:
            with self.subTest(attribute=attribute), patch.object(owner, attribute, value):
                self.session.dispatch.reset_mock()
                self.coordinator._track_map()
                self.session.dispatch.assert_not_called()
        self.native.foreground_window.return_value = 999
        self.coordinator._track_map()
        self.session.dispatch.assert_not_called()

    def test_s1_manual_open_never_bypasses_resource_or_context_validation(self):
        for change in (dict(mapId=""), dict(manualAlignmentSession=False),
                dict(automaticFloorObservationAvailable=False), dict(roundActive=False),
                dict(resourceLocked=True), dict(contextPending=True),
                dict(savedFrameCapabilities={"nightmare": dict(available=False, liveReady=False)})):
            with self.subTest(change=change):
                before = deepcopy(self.session.snapshot)
                self.session.snapshot.update(change)
                self.session.dispatchWithPayload.reset_mock()
                self.map_edge(self.coordinator._map_input_sequence + 1, True)
                self.session.dispatchWithPayload.assert_not_called()
                self.session.snapshot = before

    def test_only_due_explicit_open_recovery_can_observe_a_closed_cycle(self):
        self.sync(mapOpen=False, mapRecoveryPending=True)
        self.runtime.map_open_recovery_status = Mock(return_value=dict(pending=True, due=False, id=7))
        self.runtime.cancel_map_open_recovery = Mock()
        self.coordinator._track_map()
        self.session.dispatchWithPayload.assert_not_called()
        self.runtime.map_open_recovery_status.return_value["due"] = True
        with patch("client_coordinator.time.monotonic", return_value=15.):
            self.coordinator._track_map()
            self.coordinator._track_map()
        self.session.dispatchWithPayload.assert_called_once_with("observe_manual_floor", {"recoveryId": 7})
        self.session.dispatch.assert_not_called()
        self.runtime.map_open_recovery_status.return_value = dict(pending=False, reason="expired")
        self.coordinator._track_map()
        self.runtime.cancel_map_open_recovery.assert_called_once_with("expired")
        self.session.refreshRuntime.assert_called_once()
        self.sync(mapRecoveryPending=False)
        self.session.dispatchWithPayload.reset_mock()
        self.coordinator._track_map()
        self.session.dispatchWithPayload.assert_not_called()

    def test_initial_manual_pick_carries_input_sequence_without_inventing_map_or_floor(self):
        self.sync(mapId="", observedFloor="", floorSource="unknown", manualMapPickAvailable=True)
        self.coordinator._map_input_sequence = 9
        self.assertTrue(self.coordinator.confirmManualMap("chosen-map"))
        self.session.dispatchWithPayload.assert_called_once_with("confirm_route",
            {"mapId": "chosen-map", "inputObservationSequence": 9})
        self.assertEqual(self.runtime_state["observedFloor"], "")

    def test_binding_resume_uses_fresh_manual_fit_and_session_lease_blocks_return(self):
        self.coordinator._binding_suspended = True
        self.coordinator.capture.enable = Mock(return_value=True)
        self.coordinator._resume_game_binding()
        self.session.dispatchWithPayload.assert_called_once_with("fit_manual_frame", {
            "inputObservationSequence": 0, "foregroundReturn": True, "bindingResume": True})
        self.coordinator._binding_suspended = False
        self.native.foreground_window.return_value = 999
        self.coordinator._watch_foreground()
        self.native.foreground_window.return_value = 123
        self.session.dispatchWithPayload.reset_mock()
        # Session owns the resource lease; Runtime's snapshot has no such flag.
        self.session.snapshot["resourceLocked"] = True
        self.coordinator._watch_foreground()
        self.session.dispatchWithPayload.assert_not_called()

    def test_s0_observation_uses_existing_refresh_and_map_actions(self):
        self.sync(season="s0", availableCommands=["refresh_map"],
                  savedFrameCapabilities={"nightmare": dict(available=True, liveReady=True)})
        self.coordinator._track_map()
        self.session.dispatch.assert_called_once_with("refresh_map")
        self.map_edge(1, True)
        self.assertEqual(self.session.dispatchWithPayload.call_args.args[0], "map")
        self.assertNotIn("s1OpenEdge", self.session.dispatchWithPayload.call_args.args[1])

    def test_foreground_return_uses_manual_fit_once_and_respects_close(self):
        self.native.foreground_window.return_value = 999
        self.coordinator._watch_foreground()
        self.native.foreground_window.return_value = 123
        self.coordinator._watch_foreground()
        self.session.dispatchWithPayload.assert_called_once_with("fit_manual_frame", {"foregroundReturn": True})
        self.coordinator._watch_foreground()
        self.assertEqual(self.session.dispatchWithPayload.call_count, 1)
        self.native.foreground_window.return_value = 999
        self.coordinator._watch_foreground()
        self.native.foreground_window.return_value = 123
        self.map_edge(1, False)
        self.sync(mapOpen=False)
        self.session.dispatchWithPayload.reset_mock()
        self.coordinator._watch_foreground()
        self.session.dispatchWithPayload.assert_not_called()

    def test_focus_loss_during_first_open_preparation_preserves_open_intent(self):
        self.sync(mapOpen=False)
        self.map_edge(1, True)
        self.session.busy = True
        self.coordinator._requested_action = "fit_manual_frame"
        self.native.foreground_window.return_value = 999
        self.coordinator._watch_foreground()
        self.assertTrue(self.coordinator._map_was_open)
        self.session.busy = False
        self.session.dispatchWithPayload.reset_mock()
        self.native.foreground_window.return_value = 123
        self.coordinator._watch_foreground()
        self.input.resetMapInput.assert_called_with(True)
        self.session.dispatchWithPayload.assert_called_once_with("fit_manual_frame", {"foregroundReturn": True})

    def test_foreground_return_waits_for_context_and_resources(self):
        self.native.foreground_window.return_value = 999
        self.coordinator._watch_foreground()
        self.native.foreground_window.return_value = 123
        self.resources.busy = True
        self.coordinator._watch_foreground()
        self.session.dispatchWithPayload.assert_not_called()
        self.assertTrue(self.coordinator._waiting_foreground)
        self.resources.busy = False
        self.coordinator._watch_foreground()
        self.session.dispatchWithPayload.assert_called_once_with("fit_manual_frame", {"foregroundReturn": True})

    def test_expected_unknown_floor_observation_does_not_emit_poll_failure_notices(self):
        self.coordinator._last_round_active = True
        notices, errors = [], []
        self.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
        self.coordinator.errorReported.connect(errors.append)
        for cycle in range(5):
            self.coordinator._notify_session_transitions(dict(self.session.snapshot, cycleId=cycle,
                lastReceipt=dict(action="observe_manual_floor", success=False, reason="current-floor-unavailable")))
        self.assertEqual(notices, [])
        self.assertEqual(errors, [])


class S1FloorQmlTests(unittest.TestCase):
    def test_native_cap_runtime_snapshot_reaches_both_qml_floor_labels(self):
        """Use Runtime's real source values through Session, never invented enums.

        NativeCap resources, pixels and fitting remain synthetic fixtures; this
        checks the production consumer seam, not live-game detector accuracy.
        """
        from pathlib import Path
        from PySide6.QtCore import QObject, Property, QCoreApplication, QEvent, QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        from client_session import SessionService
        from season_context import season_options
        import test_client_control_actions as controls_fixture
        from test_s1_manual_floor_runtime import ManualS1FloorRuntime

        class Settings(controls_fixture.Settings):
            seasonOptions = Property("QVariantList", lambda s: season_options(), constant=True)

        runtime_case = ManualS1FloorRuntime()
        runtime_case.setUp()
        page_case = qml_fixture.RecognitionTests()
        page_case.setUp()
        session = SessionService(runtime_case.runtime)
        page_case.engine.rootContext().setContextProperty("session", session)
        settings = Settings()
        coordinator = controls_fixture.Coordinator(settings)
        windows = QObject()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        for name, value in dict(settingsService=settings, session=session,
                clientCoordinator=coordinator, windows=windows).items():
            engine.rootContext().setContextProperty(name, value)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientControls.qml"))))
        self.assertEqual(len(engine.rootObjects()), 1, warnings)
        window = engine.rootObjects()[0]

        def items(item):
            yield item
            for child in item.childItems():
                yield from items(child)

        def check(recognition_text, controls_text):
            session.refreshRuntime()
            qml_fixture.APP.processEvents()
            self.assertEqual(page_case.item("recognitionCurrentFloor").property("text"), recognition_text)
            label = next(item for item in items(window.contentItem())
                         if item.objectName() == "controlMapFloorText")
            self.assertTrue(label.property("text").endswith(controls_text), label.property("text"))
            self.assertFalse(page_case.item("recognitionFloor_-3").property("visible"))
            self.assertFalse(page_case.item("recognitionCycleFloor").property("visible"))
            self.assertFalse(any(item.objectName() == "controlManualFloor_-3"
                                 for item in items(window.contentItem())))

        try:
            runtime_case.prepare()
            runtime_case.pick()
            check("自动观察当前 B1", "观测楼层 B1")
            runtime_case.raw = -2
            self.assertTrue(runtime_case.runtime.dispatch("observe_manual_floor")["success"])
            check("自动观察当前 B2", "观测楼层 B2")
            for floor in (-3, -4):
                runtime_case.raw = floor
                self.assertTrue(runtime_case.runtime.dispatch("observe_manual_floor")["success"])
                label = "自动观察当前 B" + str(abs(floor)) + "（实机待验）"
                check(label, "观测楼层 B" + str(abs(floor)))
            runtime_case.raw = None
            self.assertFalse(runtime_case.runtime.dispatch("observe_manual_floor")["success"])
            check("当前楼层未知", "楼层待核对")
            self.assertEqual(page_case.warnings, [])
            self.assertEqual(warnings, [])
        finally:
            window.hide()
            window.deleteLater()
            engine.deleteLater()
            page_case.tearDown()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            qml_fixture.APP.processEvents()
            session.shutdown()
            runtime_case.doCleanups()

    def test_current_observation_and_fallback_labels_do_not_treat_default_as_observed(self):
        case = qml_fixture.RecognitionTests()
        case.setUp()
        try:
            state = case.session.state
            state.update(season="s1", manualAlignmentSession=True, automaticFloorObservationAvailable=True,
                manualFloor="", targetFloor="-1", observedFloor="", floorSource="unknown",
                availableFloors=["-1", "-2", "-3", "-4"])
            case.session.changed.emit()
            qml_fixture.APP.processEvents()
            label = case.page.findChild(qml_fixture.QObject, "recognitionCurrentFloor")
            self.assertIn("当前楼层未知", label.property("text"))
            self.assertNotIn("自动观察当前", label.property("text"))
            state.update(floorSource="automatic-current-frame", observedFloor="-2", targetFloor="-2")
            case.session.changed.emit(); qml_fixture.APP.processEvents()
            self.assertEqual(label.property("text"), "自动观察当前 B2")
            state.update(floorSource="manual-unvalidated-fallback", observedFloor="", manualFloor="-3", targetFloor="-3")
            case.session.changed.emit(); qml_fixture.APP.processEvents()
            self.assertEqual(label.property("text"), "当前楼层未知 · 人工回退 B3")
            button = case.item("recognitionFloor_-3")
            self.assertIn("实机待验", button.property("text"))
            auto = case.item("recognitionFloor_auto")
            self.assertTrue(auto.property("visible"))
            self.assertFalse(auto.property("selected"))
            from PySide6.QtCore import QMetaObject
            self.assertTrue(QMetaObject.invokeMethod(auto, "clicked"))
            self.assertEqual(case.session.calls[-1], ("set_floor", {"floor": "auto"}))
            self.assertEqual(case.warnings, [])
        finally:
            case.tearDown()


if __name__ == "__main__":
    unittest.main()
