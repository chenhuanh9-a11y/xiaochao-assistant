"""Default map input -> worker -> preparation -> standard log/export, no native IO."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from client_problems import ProblemsService
from diagnostic_export import export_safe_diagnostic_bundle
from test_client_drag_probe_presentation import DragProbePresentationContracts


class PreparationDiagnosticContracts(unittest.TestCase):
    mode = "nightmare"
    setUp = DragProbePresentationContracts.setUp
    dispatch = DragProbePresentationContracts.dispatch
    _stop_timers = DragProbePresentationContracts._stop_timers
    _cleanup_input_session = DragProbePresentationContracts._cleanup_input_session

    def open_first_map(self):
        self.dispatch("end_round")
        self.dispatch("start_round")
        self.session.refreshRuntime()
        self.app.processEvents()
        self.assertEqual(self.runtime.snapshot()["generation"], 0)
        self.traces.clear()
        self.keys._dispatch_action("map", dict(createdAt=1.))
        deadline = time.monotonic() + 4
        while (self.session.busy or self.session._pool.activeThreadCount()) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.001)
        self.app.processEvents()
        self.assertFalse(self.session.busy)
        return self.session.snapshot["lastReceipt"]

    def rows(self):
        return [row for row in self.traces if row.get("stage") == "map-preparation"]

    def test_capture_oserror_survives_default_log_and_standard_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = ProblemsService(SimpleNamespace(work_root=root / "work"),
                build_fingerprint="TEST", source_commit="a" * 40)
            self.coordinator.traceReported.connect(service.recordTrace)
            with patch.object(self.backend, "capture", side_effect=OSError(5, "PRIVATE-ACCOUNT C:/Private/User")):
                receipt = self.open_first_map()
            self.assertFalse(receipt["success"])
            self.assertEqual(receipt["reason"], "map-open-preparation-deadline")
            self.assertFalse(self.runtime._failure_samples.enabled)
            rows = self.rows()
            self.assertTrue(rows, "production preparation exceptions disappeared before the default trace")
            errors = [row for row in rows if row["phase"] == "capture-result"]
            self.assertTrue(errors)
            self.assertEqual(errors[0]["diag"]["exception"]["errno"], 5)
            self.assertEqual(errors[0]["diag"]["capture"]["foreground"], "verified-before-capture")
            self.assertEqual(rows[-1]["reason"], "map-open-preparation-deadline")
            self.assertGreater(rows[-1]["diag"]["captureErrors"], 0)
            self.assertEqual(len({row["diag"]["openingId"] for row in rows}), 1)
            self.assertLessEqual(len(rows), 72)
            service.shutdown()
            deadline = time.monotonic() + 3
            while service.pendingWrites and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.001)
            self.coordinator.traceReported.disconnect(service.recordTrace)
            self.assertFalse(service.pendingWrites)
            bundle = export_safe_diagnostic_bundle(root, service.log_path.parent,
                app_version="fixture", build_fingerprint="TEST", destination=root / "diagnostic.zip")
            with zipfile.ZipFile(bundle["path"]) as archive:
                texts = [archive.read(name).decode("utf-8") for name in archive.namelist()
                    if name.startswith("logs/events-")]
                data = "\n".join(texts)
                self.assertIn("map-preparation", data)
                self.assertIn('"errno": 5', data)
                self.assertIn("map-open-preparation-deadline", data)
                self.assertNotIn("PRIVATE-ACCOUNT", data)
                self.assertNotIn("C:/Private", data)
                self.assertFalse(any(name.lower().endswith((".png", ".jpg")) for name in archive.namelist()))

    def test_negative_observation_is_distinct_from_capture_failure(self):
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False, reason="map-ui-not-observed")), \
                patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None, reason="no-floor-highlight")), \
                patch.object(self.engine.qt_map_ui, "observe", return_value=dict(mapOpen=False, floor=None, reason="map-ui-not-observed")):
            receipt = self.open_first_map()
        self.assertEqual(receipt["reason"], "map-open-preparation-deadline")
        rows = self.rows()
        self.assertTrue(rows, "map visibility rejection disappeared into deadline")
        observed = [row for row in rows if row["phase"] == "observation-result"]
        self.assertTrue(observed)
        self.assertEqual(observed[0]["reason"], "map-not-ready")
        self.assertFalse(observed[0]["diag"]["observation"]["mapReady"])
        self.assertEqual(observed[0]["diag"]["observation"]["mapReason"], "map-ui-not-observed")
        captures = [row for row in rows if row["phase"] == "capture-result"]
        self.assertEqual(captures[0]["diag"]["capture"]["bounds"], [100, 200, 1380, 920])
        self.assertEqual(captures[0]["diag"]["capture"]["dpi"], 96)
        self.assertEqual(rows[-1]["diag"]["captureErrors"], 0)

    def test_ready_frame_has_capture_observation_and_prepared_terminal(self):
        receipt = self.open_first_map()
        self.assertTrue(receipt["success"], receipt)
        rows = self.rows()
        self.assertTrue(rows, "ready preparation has no opening correlation")
        self.assertEqual([row["phase"] for row in rows],
            ["begin", "capture-start", "capture-result", "observation-result", "finished"])
        self.assertEqual(rows[-1]["reason"], "prepared-current-frame")
        self.assertTrue(rows[3]["diag"]["observation"]["mapReady"])
        self.assertTrue(any(row.get("stage") == "publish" for row in self.traces))

    def test_context_change_is_retained_separately_from_final_deadline(self):
        from client_region_capture import ClientCaptureContext
        def moved_observation(*args, **kwargs):
            self.backend.current = ClientCaptureContext((1280, 720), (200, 200, 1480, 920), 96)
            return dict(detected=False, mapOpen=False, floor=None, reason="map-ui-not-observed")
        with patch("map_interface_observation.detect_map_interface", side_effect=moved_observation), \
                patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None)), \
                patch.object(self.engine.qt_map_ui, "observe", side_effect=moved_observation):
            receipt = self.open_first_map()
        self.assertEqual(receipt["reason"], "map-open-preparation-deadline")
        terminal = self.rows()[-1]
        self.assertEqual(terminal["diag"]["lastProbeOutcome"], "capture-context-changed")
        self.assertEqual(terminal["diag"]["loopStopReason"], "capture-context-changed")
        self.assertEqual(terminal["diag"]["probe"], 2)

    def test_visible_map_unknown_floor_keeps_its_distinct_terminal(self):
        with patch("map_interface_observation.detect_selected_floor", return_value=dict(selectedFloor=None, reason="no-floor-highlight")), \
                patch.object(self.engine.qt_map_ui, "observe", return_value=dict(mapOpen=True, floor=None, reason="floor-highlight-ambiguous")):
            receipt = self.open_first_map()
        self.assertEqual(receipt["reason"], "current-floor-unavailable")
        terminal = self.rows()[-1]
        self.assertEqual(terminal["reason"], "current-floor-unavailable")
        self.assertEqual(terminal["diag"]["lastProbeOutcome"], "current-floor-unavailable")
        self.assertFalse(any(row.get("stage") == "publish" for row in self.traces))

    def test_cancelled_running_capture_records_exception_and_terminal_without_publish(self):
        entered, release = threading.Event(), threading.Event()
        confirmed = []
        original = self.backend.capture
        def held_capture(*args):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test capture release missing")
            return original(*args)
        def cancel_on_start(row):
            if row.get("stage") == "map-preparation" and row.get("phase") == "capture-start":
                confirmed.append(entered.wait(2))
                self.session.cancelPending("controlled-preparation-cancel")
                release.set()
        self.coordinator.traceReported.connect(cancel_on_start)
        try:
            with patch.object(self.backend, "capture", side_effect=held_capture):
                self.open_first_map()
        finally:
            release.set()
            self.coordinator.traceReported.disconnect(cancel_on_start)
        rows = self.rows()
        self.assertEqual(confirmed, [True], "cancellation must follow actual backend entry")
        self.assertEqual(rows[-1]["reason"], "preparation-exception")
        errors = [row for row in rows if row["reason"] == "capture-exception"]
        self.assertEqual(errors[0]["diag"]["exception"]["reason"], "capture-superseded-or-cancelled")
        self.assertFalse(any(row.get("stage") == "publish" for row in self.traces))


class DifficultPreparationDiagnosticContracts(PreparationDiagnosticContracts):
    mode = "difficult"


class PreparationBudgetContracts(unittest.TestCase):
    def test_one_opening_has_fixed_cap_and_keeps_terminal(self):
        from client_preparation_diagnostics import PreparationDiagnostics
        rows = []
        runtime = SimpleNamespace(_notify=lambda name, **values: rows.append(values))
        diagnostic = PreparationDiagnostics(runtime, dict(inputSequence=1, generation=2))
        for _ in range(10000):
            diagnostic.emit("capture-start", "capture-requested")
        diagnostic.finish("map-open-preparation-deadline")
        self.assertEqual(len(rows), 72)
        self.assertEqual(rows[-1]["reason"], "map-open-preparation-deadline")
        self.assertEqual(rows[-1]["preparation"]["suppressed"], 9929)


if __name__ == "__main__":
    unittest.main()
