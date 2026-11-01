"""Real Qt owner/provider/region path with injected OS build and synthetic pixels.

No OS version is changed: only the exclusion module's version-query dependency
is replaced. DLL loading is forbidden on unsupported builds, and all windows,
foreground checks and pixels are fixtures. No desktop/game/input IO occurs.
"""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from client_capture import ClientCaptureProvider
from client_capture_exclusion import QtCaptureExclusion, _DisplayAffinity
from client_problem_messages import operation_problem
import test_client_capture_exclusion as exclusion_fixtures
import test_client_preparation_diagnostics as preparation_fixtures
from test_client_capture_full import Backend
from test_client_input import Native


CODE = "capture-safe-fallback-unsupported"


class CaptureCompatibilityTests(unittest.TestCase):
    run_worker = exclusion_fixtures.CaptureExclusionTests.run_worker

    def setUp(self):
        self.windows = Mock(side_effect=AssertionError("unsupported fallback enumerated windows"))
        self.screen = Mock(side_effect=AssertionError("unsupported fallback captured the screen"))
        self.helper = QtCaptureExclusion(windows=self.windows, capture=self.screen, timeout=.3)
        self.addCleanup(self.helper.shutdown)
        self.backend, self.native = Backend(), Native()
        self.provider = ClientCaptureProvider(self.native, self.backend, full_capture=self.helper.full_capture)
        self.addCleanup(self.provider.shutdown)
        self.provider.bindFullClient(42)
        self.assertTrue(self.provider.enable())

    def old_windows(self, build):
        return patch("client_capture_exclusion.sys", SimpleNamespace(
            getwindowsversion=lambda: SimpleNamespace(build=build)))

    def fail_dc(self, trigger):
        if trigger == "blank":
            self.backend.capture = lambda hwnd, roi: np.zeros((80, 100, 3), np.uint8)
        else:
            def failed(hwnd, roi):
                raise OSError(5, "PRIVATE-DC-DETAIL")
            self.backend.capture = failed

    def test_old_windows_normal_dc_is_usable_without_loading_exclusion(self):
        with self.old_windows(17763), patch("client_capture_exclusion.ctypes.WinDLL") as dll:
            result = self.run_worker(self.helper, target=self.provider.capture)
        self.assertNotIn("error", result)
        self.assertEqual(result["value"].acquisition, "client-dc-roi")
        self.assertEqual(result["value"].context, self.backend.current)
        self.assertEqual(self.provider.snapshot["captureCapability"]["clientDc"], "captured")
        self.windows.assert_not_called()
        self.screen.assert_not_called()
        dll.assert_not_called()
        observed = self.provider.snapshot["captureCapability"]
        observed["clientDc"] = "external-mutation"
        self.assertEqual(self.provider.snapshot["captureCapability"]["clientDc"], "captured")
        self.provider.bindFullClient(42)
        self.assertEqual(self.provider.snapshot["captureCapability"]["clientDc"], "not-probed")

    def test_unsupported_fallback_has_code_capability_trigger_and_can_recover_via_dc(self):
        for build in (17763, 19040):
            for trigger in ("blank", "native-error"):
                with self.subTest(build=build, trigger=trigger):
                    self.fail_dc(trigger)
                    generation = self.provider.generation
                    with self.old_windows(build), patch("client_capture_exclusion.ctypes.WinDLL") as dll:
                        result = self.run_worker(self.helper, target=self.provider.capture)
                    error = result["error"]
                    self.assertEqual(getattr(error, "code", None), CODE)
                    self.assertEqual(str(error), CODE)
                    self.assertNotIn("value", result)
                    cap = error.capture_diagnostics["captureCapability"]
                    self.assertEqual(cap["windowsBuild"], build)
                    self.assertEqual(cap["minimumWindowsBuild"], 19041)
                    self.assertEqual(cap["safeFallback"], "unsupported")
                    self.assertEqual(cap["clientDc"], "failed")
                    self.assertTrue(cap["fallbackRequired"])
                    self.assertEqual(error.capture_diagnostics["fullClientSize"], [100, 80])
                    self.assertEqual(error.capture_diagnostics["clientBounds"], [10, 20, 110, 100])
                    self.assertEqual(error.capture_diagnostics["dpi"], 96)
                    cause = error.capture_diagnostics["fallbackTrigger"]
                    self.assertEqual(cause["reason"], "game-client-dc-returned-blank-frame" if trigger == "blank" else "client-dc-native-error")
                    self.assertIsInstance(error.__context__, OSError)
                    self.assertNotIn("PRIVATE-DC-DETAIL", json.dumps(error.capture_diagnostics))
                    self.assertEqual(self.provider.snapshot["captureCapability"], cap)
                    self.assertTrue(self.provider.snapshot["enabled"])
                    self.assertEqual(self.provider.generation, generation)
                    self.assertFalse(self.helper.pendingWrites)
                    self.windows.assert_not_called()
                    self.screen.assert_not_called()
                    dll.assert_not_called()
                    self.backend.capture = Backend.capture.__get__(self.backend, Backend)
                    recovered = self.run_worker(self.helper, target=self.provider.capture)
                    self.assertNotIn("error", recovered)
                    self.assertEqual(recovered["value"].acquisition, "client-dc-roi")
                    self.assertEqual(self.provider.snapshot["captureCapability"]["clientDc"], "captured")
                    self.assertFalse(self.provider.snapshot["captureCapability"]["fallbackRequired"])

    def test_wrong_foreground_never_attempts_fallback_or_changes_focus(self):
        self.fail_dc("blank")
        self.native.foreground = 77
        with self.old_windows(17763), patch("client_capture_exclusion.ctypes.WinDLL") as dll:
            result = self.run_worker(self.helper, target=self.provider.capture)
        self.assertIn("foreground", str(result["error"]))
        self.assertEqual(self.native.foreground, 77)
        self.assertEqual(self.backend.captures, [])
        self.screen.assert_not_called()
        dll.assert_not_called()

    def test_19041_version_gate_accepts_native_api_fixture(self):
        # Only signatures are bound here; no fake native operation is invoked.
        with self.old_windows(19041), patch("client_capture_exclusion.ctypes.WinDLL", return_value=Mock()) as dll:
            affinity = _DisplayAffinity()
        self.assertEqual(affinity.capability["safeFallback"], "supported")
        self.assertEqual(affinity.capability["windowsBuild"], 19041)
        self.assertEqual(dll.call_count, 2)

    def test_chinese_problem_is_specific_and_preserves_capture_diagnostics(self):
        receipt = dict(reason="CaptureFallbackUnsupported:" + CODE, error=True, action="map",
            errorPhase="capture", captureDiagnostics=dict(captureCapability=dict(windowsBuild=17763)))
        problem = operation_problem(receipt, dict(mode="difficult", mapId="fixture", floor="1"))
        self.assertEqual(problem["title"], "当前系统不支持安全画面回退")
        self.assertIn("19041", problem["summary"])
        self.assertIn("客户区", problem["summary"])
        self.assertEqual(problem["phase"], "capture")
        self.assertEqual(problem["raw"], receipt["reason"])
        self.assertEqual(problem["captureDiagnostics"], receipt["captureDiagnostics"])
        self.assertEqual((problem["mode"], problem["mapId"], problem["floor"]), ("difficult", "fixture", "1"))


class CaptureCompatibilityPreparationTests(unittest.TestCase):
    """Queued map input -> session -> provider -> exclusion -> default problems.

The shared fixture uses fake input/native windows, synthetic pixels and solver
measurements. The Qt queue, runtime, preparation and diagnostic consumers run.
"""
    mode = "nightmare"
    dispatch = preparation_fixtures.PreparationDiagnosticContracts.dispatch
    _stop_timers = preparation_fixtures.PreparationDiagnosticContracts._stop_timers
    _cleanup_input_session = preparation_fixtures.PreparationDiagnosticContracts._cleanup_input_session
    open_first_map = preparation_fixtures.PreparationDiagnosticContracts.open_first_map
    rows = preparation_fixtures.PreparationDiagnosticContracts.rows
    old_windows = CaptureCompatibilityTests.old_windows

    def setUp(self):
        preparation_fixtures.PreparationDiagnosticContracts.setUp(self)
        self.screen = Mock(side_effect=AssertionError("unsupported platform captured the screen"))
        self.helper = QtCaptureExclusion(windows=lambda: self.fail("unsupported platform enumerated windows"),
            capture=self.screen, timeout=.3)
        self.addCleanup(self.helper.shutdown)
        self.capture._full_capture = self.helper.full_capture

    def press_map(self):
        self.keys._dispatch_action("map", dict(createdAt=1.))
        deadline = time.monotonic() + 4
        while (self.session.busy or self.session._pool.activeThreadCount()) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.app.processEvents()
        self.assertFalse(self.session.busy)
        return self.session.snapshot["lastReceipt"]

    def test_capability_failure_reaches_default_problem_and_trace_without_deadline(self):
        from client_problems import ProblemsService
        from diagnostic_export import export_safe_diagnostic_bundle
        import zipfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = ProblemsService(SimpleNamespace(work_root=root / "work"),
                build_fingerprint="TEST", source_commit="a" * 40)
            self.coordinator.traceReported.connect(service.recordTrace)
            self.coordinator.errorReported.connect(service.record)
            try:
                with self.old_windows(17763), \
                        patch("client_capture_exclusion.ctypes.WinDLL") as dll, \
                        patch.object(self.backend, "capture", side_effect=OSError(5, "PRIVATE-DC-DETAIL")):
                    receipt = self.open_first_map()
                self.assertFalse(receipt["success"])
                self.assertEqual(receipt["reason"], "CaptureFallbackUnsupported:" + CODE)
                self.assertEqual(receipt["errorPhase"], "capture")
                self.assertEqual(receipt["captureDiagnostics"]["captureCapability"]["windowsBuild"], 17763)
                self.assertEqual(receipt["captureDiagnostics"]["fallbackTrigger"]["errno"], 5)
                self.assertEqual(receipt["captureDiagnostics"]["fullClientSize"], [1280, 720])
                errors = [row for row in self.rows() if row["phase"] == "capture-result"]
                self.assertEqual(len(errors), 1, "a known unsupported fallback must not spin until deadline")
                self.assertEqual(errors[0]["diag"]["exception"]["reason"], CODE)
                self.assertEqual(errors[0]["diag"]["capture"]["captureCapability"]["windowsBuild"], 17763)
                self.assertNotEqual(self.rows()[-1]["reason"], "map-open-preparation-deadline")
                self.assertFalse(any(row.get("stage") == "publish" for row in self.traces))
                self.assertEqual(self.runtime.snapshot()["mode"], self.mode)
                self.assertTrue(self.runtime.snapshot()["roundActive"])
                self.assertTrue(self.capture.snapshot["enabled"])
                self.assertFalse(self.helper.pendingWrites)
                self.screen.assert_not_called()
                dll.assert_not_called()
                self.assertTrue(service.recent)
                self.assertEqual(service.recent[0]["title"], "当前系统不支持安全画面回退")
                self.assertEqual(service.recent[0]["captureDiagnostics"]["captureCapability"]["safeFallback"], "unsupported")
            finally:
                self.coordinator.traceReported.disconnect(service.recordTrace)
                self.coordinator.errorReported.disconnect(service.record)
                service.shutdown()
                deadline = time.monotonic() + 3
                while service.pendingWrites and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.001)
                self.assertFalse(service.pendingWrites)
            bundle = export_safe_diagnostic_bundle(root, service.log_path.parent,
                app_version="fixture", build_fingerprint="TEST", destination=root / "diagnostic.zip")
            with zipfile.ZipFile(bundle["path"]) as archive:
                texts = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist()
                    if name.startswith("logs/events-"))
                self.assertIn(CODE, texts)
                self.assertIn('"windowsBuild": 17763', texts)
                self.assertNotIn("PRIVATE-DC-DETAIL", texts)
                self.assertNotIn("map-open-preparation-deadline", texts)
            # The failed opening retains the user's map intent. Close/reopen
            # through the same input path and round when ordinary DC recovers.
            with self.old_windows(17763), patch("client_capture_exclusion.ctypes.WinDLL") as dll:
                closed = self.press_map()
                self.assertEqual(closed["action"], "close_map")
                self.assertTrue(closed["success"])
                recovered = self.press_map()
            self.assertTrue(recovered["success"], recovered)
            self.assertEqual(recovered["action"], "map")
            self.assertEqual(recovered["resultContext"]["roundId"], receipt["resultContext"]["roundId"])
            self.assertEqual(self.capture.snapshot["captureCapability"]["clientDc"], "captured", recovered)
            self.assertFalse(self.capture.snapshot["captureCapability"]["fallbackRequired"])
            dll.assert_not_called()


class DifficultCaptureCompatibilityPreparationTests(CaptureCompatibilityPreparationTests):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
