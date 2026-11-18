"""Real Qt settings -> coordinator -> queued session -> S1 runtime contracts.

QML's author selector/save handlers are source-traced, not clicked here. Capture,
floor readings, native fits, author pixels/bridges, window/input and image upload
are explicitly synthetic. No game, native windows or real author assets are used.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / "app", ROOT / "tests"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

import numpy as np
from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal

from client_coordinator import ClientCoordinator
from client_paths import WORK_ROOT
from client_session import SessionService
from client_settings import SettingsService
import test_s1_async_evidence as synthetic_evidence


class SyntheticPeripheral(QObject):
    changed = Signal()
    resourcesChanged = Signal()
    actionTriggered = Signal(str)


class S1AuthorSettingsChainTests(unittest.TestCase):
    selection_mode = "auto"

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def until(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            self.app.processEvents()
            if self.slot_errors:
                self.fail("Qt slot exception: " + repr(self.slot_errors))
            if predicate():
                return
            time.sleep(.002)
        self.fail("Timed out waiting for the real Qt settings/session queues")

    def setUp(self):
        self.slot_errors = []
        self.enterContext(patch.object(sys, "excepthook", side_effect=lambda *e: self.slot_errors.append(e)))
        WORK_ROOT.mkdir(exist_ok=True)
        folder = self.enterContext(tempfile.TemporaryDirectory(prefix="s1-author-chain-", dir=WORK_ROOT))
        sandbox = Path(folder)
        (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.settings = SettingsService(sandbox)
        self.addCleanup(self.stop_settings)
        self.assertTrue(self.settings.setValue("gameSeason", "s1"))
        self.assertTrue(self.settings.setValue("gameMode", "nightmare"))
        self.assertTrue(self.settings.setValue("mapSelectionMode", self.selection_mode))
        profiles = dict(self.settings.values["contextRouteProfiles"])
        profiles["s1:nightmare"] = "shared-structure"
        self.assertTrue(self.settings.setValue("contextRouteProfiles", profiles))
        self.assertTrue(self.settings.save())
        self.until(lambda: not self.settings.busy)

        # Reuse only this fixture's synthetic capture/structure evidence setup.
        self.fx = synthetic_evidence.AsyncEvidenceContracts()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.fx.registry.capability["observedFloors"] = (-1, -2, -3, -4)
        self.runtime = self.fx.runtime
        self.fx.floor = "-3"
        self.runtime._display_layer.side_effect = self.synthetic_display_layer
        self.enterContext(patch("client_route_reprojection.prepare_display_layer",
                                side_effect=self.synthetic_display_layer))
        self.runtime.configure_settings(self.settings.persistedValues)
        self.assertTrue(self.runtime.dispatch("set_context", dict(
            season="s1", mode="nightmare", profileId="shared-structure"))["success"])
        self.fx.wire()
        self.fx.show()
        self.fx.idle()
        self.runtime.dispatch("close_map")
        self.fx.show()
        self.fx.idle()
        self.pool = self.runtime._s1_private_pool
        self.assertEqual(self.pool.locked_id, "a")
        self.pool_before = self.pool.snapshot()
        self.revision_before = self.pool.revision
        # Do not let reopen derive a lost private lock again and hide the bug.
        self.fx.registry.engine.qt_s1_private_coordinate_prepare = lambda frame, ids, floor, **kw: dict(
            self.fx.report(ids, floor, bad=()), coordinateVerified=False)

        self.session = SessionService(self.runtime)
        self.addCleanup(self.stop_session)
        resources, native_input, backend = (SyntheticPeripheral() for _ in range(3))
        resources.busy, resources.pendingKind = False, ""
        native_input.enabled = True
        native_input.snapshot = {}
        for name in ("configureSettings", "enable", "disable", "setTextEntryActive", "setEditingBindings"):
            setattr(native_input, name, Mock(return_value=True))
        backend.refresh = Mock()
        self.windows = SimpleNamespace(hide=Mock(), show=Mock(), setClickThrough=Mock(),
                                       snapshot={}, _windows={})
        self.images = SimpleNamespace(clear=Mock(), publish=Mock(side_effect=lambda key, pixels, **kw:
                                                               "image://synthetic/" + key))
        native_windows = SimpleNamespace(foreground_window=Mock(return_value=123))
        with patch("client_input.NativeWindowAPI", return_value=native_windows):
            self.coordinator = ClientCoordinator(backend=backend, settings=self.settings,
                resources=resources, session=self.session, windows=self.windows,
                native_input=native_input, capture=self.fx.capture, runtime=self.runtime,
                images=self.images, window_catalog=Mock(side_effect=AssertionError("native discovery forbidden")))
        self.addCleanup(self.stop_coordinator)
        self.coordinator._bound = "123"
        self.coordinator._foreground_timer.stop()
        self.coordinator._tracking_timer.stop()
        # Physical monitor/DIP conversion is outside this settings-chain test.
        self.enterContext(patch.object(self.coordinator, "_convert_client_geometry",
            side_effect=lambda payload: payload.update(deviceScale=1.0)))
        self.session.refreshRuntime()
        self.assertEqual(self.coordinator.alignedData["profileId"], "shared-structure")
        self.configure_threads, self.dispatch_threads = [], []
        original_configure, original_dispatch = self.runtime.configure_settings, self.runtime.dispatch

        def configure(values):
            self.configure_threads.append(QThread.currentThread())
            return original_configure(values)

        def dispatch(action, payload=None, cancelled=None):
            if action == "set_context":
                self.dispatch_threads.append(QThread.currentThread())
            return original_dispatch(action, payload, cancelled)

        self.enterContext(patch.object(self.runtime, "configure_settings", side_effect=configure))
        self.enterContext(patch.object(self.runtime, "dispatch", side_effect=dispatch))

    def stop_coordinator(self):
        self.coordinator._closed = True
        for name in ("_foreground_timer", "_tracking_timer", "_context_timer", "_close_timer",
                     "_material_timer", "_discovery_timer", "_startup_timer"):
            getattr(self.coordinator, name).stop()
        self.runtime.set_event_callback(None)
        self.app.processEvents()

    def stop_session(self):
        self.session.shutdown()
        self.assertTrue(self.session._pool.waitForDone(5000))
        self.app.processEvents()

    def stop_settings(self):
        self.settings.shutdown()
        self.assertTrue(self.settings.pool.waitForDone(5000))
        self.app.processEvents()

    def synthetic_display_layer(self, engine, map_id, floor, profile, **kwargs):
        from shared_structure_display import structure_display_identity
        author = profile == "s1-jiaye-handnotes"
        identity = ("synthetic-display:" + profile if author else
                    structure_display_identity(engine.qt_shared_provider("shared-structure", map_id, floor)))
        bridge = np.array([[1., 0., 3. if author else 0.],
                           [0., 1., -2. if author else 0.], [0., 0., 1.]])
        return dict(pixels=np.full((20, 24, 4), 123 if author else 255, np.uint8),
                    resourceIdentity=identity,
                    displayKind="author" if author else "shared-structure", displayLabel=profile,
                    routeProfileId=profile, effectiveProfileId=profile,
                    authorStatus="bound" if author else "none", matrixDisplayToReference=bridge)

    def save_author(self, profile, wait=True):
        # Same public API as ClientSettings.qml onActivated and settingsSave.
        profiles = dict(self.settings.values["contextRouteProfiles"])
        profiles["s1:nightmare"] = profile
        self.assertTrue(self.settings.setValue("contextRouteProfiles", profiles))
        self.assertTrue(self.settings.save())
        if wait:
            self.wait_saved(profile)

    def wait_saved(self, profile):
        self.until(lambda: not self.settings.busy and not self.session.busy
                   and not self.session._jobs and self.coordinator._pending_context is None)
        self.assertEqual(self.session.appliedProfile, profile, self.session.message)
        self.assertEqual(self.settings.persistedValues["contextRouteProfiles"]["s1:nightmare"], profile)
        stored = json.loads(self.settings.path.read_text(encoding="utf-8"))
        self.assertEqual(stored["contextRouteProfiles"]["s1:nightmare"], profile)
        self.assertEqual(self.coordinator._applied_context, ("nightmare", profile, "s1"))
        self.assertTrue(self.configure_threads)
        self.assertTrue(all(thread == self.app.thread() for thread in self.configure_threads))
        self.assertTrue(self.dispatch_threads)
        self.assertTrue(all(thread != self.app.thread() for thread in self.dispatch_threads))

    def assert_identity_unchanged(self):
        self.assertIs(self.runtime._s1_private_pool, self.pool)
        self.assertEqual(self.pool.snapshot(), self.pool_before)
        self.assertEqual(self.pool.revision, self.revision_before)
        self.assertEqual(self.runtime._floor, "-3")
        self.assertEqual(self.runtime._map_id, "")
        self.assertEqual(self.runtime._durable_map_id, "")
        self.assertFalse(self.runtime._gate.confirmed_id)
        state = self.runtime.snapshot()
        self.assertFalse(state["identityLocked"])
        self.assertFalse(state["automaticIdentityAvailable"])
        self.assertFalse(state["liveEngineAvailable"])

    def test_saved_author_roundtrip_reprojects_current_pixels_without_identity_or_pose_change(self):
        initial = deepcopy(self.fx.current_payload())
        captures, fits = self.fx.capture.count, list(self.fx.calls)
        previous = initial
        for profile in ("s1-jiaye-handnotes", "shared-structure"):
            with self.subTest(profile=profile):
                self.save_author(profile)
                self.assert_identity_unchanged()
                current = self.fx.current_payload()
                self.assertIsNotNone(current)
                self.assertTrue(current["liveAuthorization"])
                self.assertEqual(current["displayAuthorizationScope"], "current-native-frame-candidate")
                self.assertEqual(current["routeProfileId"], profile)
                self.assertEqual(current["profileId"], profile)
                self.assertEqual(current["floor"], "-3")
                self.assertEqual(current["mapId"], "a")
                for field in ("canonicalMatrix", "frameId", "cycleId", "roundId", "generation"):
                    self.assertEqual(current[field], initial[field], field)
                expected_bridge = (np.array([[1., 0., 3.], [0., 1., -2.], [0., 0., 1.]])
                    if profile == "s1-jiaye-handnotes" else np.eye(3))
                np.testing.assert_allclose(current["matrix"],
                    np.asarray(initial["canonicalMatrix"]) @ expected_bridge)
                self.assertTrue(np.all(current["pixels"] == (123 if profile == "s1-jiaye-handnotes" else 255)))
                self.assertNotEqual(current["token"], previous["token"])
                self.assertIsNone(self.runtime.render_payload(previous["token"]))
                self.assertEqual(self.coordinator.alignedData["token"], current["token"])
                self.assertEqual(self.coordinator.alignedData["profileId"], profile)
                # Static candidate pixels share display ownership, never the
                # legacy confirmed/durable identity or an aligned pose grant.
                reference = self.runtime.reference_payload()
                self.assertTrue(reference["s1CandidateReference"])
                self.assertFalse(reference["identityLocked"])
                self.assertEqual(reference["profileId"], profile)
                np.testing.assert_array_equal(reference["pixels"], current["pixels"])
                self.assertEqual(self.coordinator.referenceData["profileId"], profile)
                previous = current
        self.assertEqual(self.fx.capture.count, captures)
        self.assertEqual(self.fx.calls, fits)

    def test_saved_author_while_closed_retains_lock_and_waits_for_fresh_frame(self):
        old = self.fx.current_payload()
        self.runtime.dispatch("close_map")
        self.session.refreshRuntime()
        self.assertIsNone(self.fx.current_payload())
        self.save_author("s1-jiaye-handnotes")
        self.assert_identity_unchanged()
        self.assertIsNone(self.fx.current_payload())
        self.assertFalse(self.coordinator.alignedData)
        reference = self.runtime.reference_payload()
        self.assertTrue(reference["s1CandidateReference"])
        self.assertEqual(reference["profileId"], "s1-jiaye-handnotes")
        self.assertTrue(np.all(reference["pixels"] == 123))
        self.assertEqual(self.coordinator.referenceData["profileId"], "s1-jiaye-handnotes")
        reopened = self.fx.show()
        self.fx.idle()
        self.session.refreshRuntime()
        self.assert_identity_unchanged()
        self.assertNotEqual(reopened["frameId"], old["frameId"])
        self.assertEqual(reopened["profileId"], "s1-jiaye-handnotes")
        self.assertEqual(reopened["routeProfileId"], "s1-jiaye-handnotes")
        self.assertEqual(self.coordinator.alignedData["token"], reopened["token"])

    def test_display_only_saved_setting_preserves_lock_current_token_and_floor(self):
        old = self.fx.current_payload()
        self.assertTrue(self.settings.setValue("referenceSheetVisible", False))
        self.assertTrue(self.settings.save())
        self.until(lambda: not self.settings.busy)
        self.assert_identity_unchanged()
        self.assertEqual(self.fx.current_payload()["token"], old["token"])
        self.assertEqual(self.coordinator.alignedData["token"], old["token"])
        self.assertFalse(self.dispatch_threads)

    def test_static_reference_revocation_reaches_view_without_session_completion(self):
        self.session.refreshRuntime()
        self.assertTrue(self.coordinator.referenceData["s1CandidateReference"])
        self.assertNotIn("暂用", self.coordinator.referenceData["title"])
        self.assertNotIn("已锁定", self.coordinator.referenceData["title"])
        self.assertTrue(self.coordinator.referenceData["provisional"])
        self.runtime.dispatch("close_map")
        self.app.processEvents()
        self.assertTrue(self.coordinator.referenceData)
        self.runtime.invalidate("game-floor-control-intent")
        self.until(lambda: not self.coordinator.referenceData)
        self.assertIsNone(self.runtime.reference_payload())

    def test_static_owner_poll_rejects_changed_binding_without_pose(self):
        self.runtime.dispatch("close_map")
        self.session.refreshRuntime()
        self.assertTrue(self.coordinator.referenceData)
        self.fx.capture.generation += 1
        self.coordinator._watch_foreground()
        self.assertFalse(self.coordinator.referenceData)

    def test_queued_old_revocation_keeps_new_current_static_reference(self):
        old = self.runtime.reference_payload()
        self.runtime.dispatch("close_map")
        self.fx.show(); self.fx.idle()
        self.session.refreshRuntime()
        current = self.runtime.reference_payload()
        self.assertNotEqual(old["token"], current["token"])
        shown = deepcopy(self.coordinator.referenceData)
        self.assertTrue(shown)
        self.coordinator._on_runtime_event(dict(event="render-revoked", reason="old-candidate-switch",
                                               token=old["token"], epoch=-1))
        # Identical resource/profile pixels intentionally reuse their image key.
        self.assertEqual(self.coordinator.referenceData, shown)
        self.assertEqual(self.runtime.reference_payload()["token"], current["token"])

    def test_normal_reopen_never_hides_static_view_while_capture_is_blocked(self):
        self.runtime.dispatch("map", dict(openIntent=False, inputSequence=1))
        self.session.refreshRuntime()
        self.app.processEvents()
        shown = deepcopy(self.coordinator.referenceData)
        self.assertTrue(shown)
        self.windows.hide.reset_mock()
        entered, release = threading.Event(), threading.Event()
        original = self.fx.capture.capture
        def capture(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise AssertionError("capture release deadline")
            return original(*args, **kwargs)
        results = []
        with patch.object(self.fx.capture, "capture", side_effect=capture):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch(
                "map", dict(openIntent=True, inputSequence=2))))
            worker.start()
            try:
                self.until(entered.is_set)
                self.coordinator._watch_foreground()
                self.assertEqual(self.coordinator.referenceData, shown)
                self.assertFalse(any(call.args == ("reference",) for call in self.windows.hide.call_args_list))
            finally:
                release.set(); worker.join(4)
        self.fx.idle()
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0]["success"], results)
        self.session.refreshRuntime()
        self.assertTrue(self.coordinator.referenceData)
        self.assertFalse(any(call.args == ("reference",) for call in self.windows.hide.call_args_list))

    def test_session_recapture_keeps_runtime_authorized_material_until_replacement(self):
        old = self.fx.current_payload()
        self.assertTrue(old["liveAuthorization"])
        self.assertEqual(self.coordinator.alignedData["token"], old["token"])
        uploads = self.images.publish.call_count
        self.windows.hide.reset_mock()
        entered, release = threading.Event(), threading.Event()
        original = self.fx.capture.capture

        def capture(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise AssertionError("recapture fixture not released")
            return original(*args, **kwargs)

        with patch.object(self.fx.capture, "capture", side_effect=capture):
            try:
                self.session.dispatch("capture_now")
                self.until(entered.is_set)
                self.assertTrue(self.session.busy)
                self.assertIsNotNone(self.runtime.render_payload(old["token"]))
                self.assertEqual(self.coordinator.alignedData.get("token"), old["token"])
                self.assertEqual(self.images.publish.call_count, uploads)
                self.assertFalse(any(call.args == ("aligned",)
                    for call in self.windows.hide.call_args_list))
            finally:
                release.set()
            self.until(lambda: not self.session.busy and not self.session._jobs)
        self.fx.idle()
        self.session.refreshRuntime()
        self.assertTrue(self.coordinator.alignedData)
        self.assertFalse(any(call.args == ("aligned",)
            for call in self.windows.hide.call_args_list))

    def test_queued_saved_author_cannot_revive_display_or_private_lock_after_end_round(self):
        entered, release = threading.Event(), threading.Event()
        original = self.runtime.dispatch

        def delayed(action, payload=None, cancelled=None):
            if action == "set_context":
                entered.set()
                if not release.wait(8):
                    raise AssertionError("author worker fixture not released")
            return original(action, payload, cancelled)

        with patch.object(self.runtime, "dispatch", side_effect=delayed):
            try:
                self.save_author("s1-jiaye-handnotes", wait=False)
                self.until(entered.is_set)
                self.assertTrue(self.runtime.dispatch("end_round")["success"])
                self.assertIsNone(self.runtime._s1_private_pool)
                self.assertIsNone(self.fx.current_payload())
            finally:
                release.set()
            self.wait_saved("s1-jiaye-handnotes")
        self.assertFalse(self.runtime.snapshot()["roundActive"])
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertIsNone(self.fx.current_payload())
        self.assertFalse(self.coordinator.alignedData)
        self.assertEqual(self.runtime._durable_map_id, "")


if __name__ == "__main__":
    unittest.main()
