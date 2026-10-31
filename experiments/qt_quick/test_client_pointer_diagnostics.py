"""Real queued pointer/receipt path, fixture native capture and solver boundaries."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

from client_problems import ProblemsService
from test_client_drag_probe_presentation import DragProbePresentationContracts


class PointerDiagnosticContracts(unittest.TestCase):
    mode = "nightmare"
    setUp = DragProbePresentationContracts.setUp
    dispatch = DragProbePresentationContracts.dispatch
    _stop_timers = DragProbePresentationContracts._stop_timers
    _cleanup_input_session = DragProbePresentationContracts._cleanup_input_session
    pointer = DragProbePresentationContracts.pointer
    queued_probe_after_gesture = DragProbePresentationContracts.queued_probe_after_gesture
    finish_worker = DragProbePresentationContracts.finish_worker

    def rows(self):
        return [row for row in self.traces if row.get("stage") == "pointer-input"]

    def test_boundary_rejection_then_success_keeps_client_press_and_receipt(self):
        self.pointer("down", 120, 500)
        rejected = self.rows()
        self.assertTrue(rejected, "actual boundary rejection left no structured pointer evidence")
        self.assertEqual(rejected[-1]["reason"], "pointer-outside-drag-boundary")
        self.assertEqual(rejected[-1]["diag"]["pressClient"], [20, 300])
        self.assertEqual(rejected[-1]["status"], "rejected")
        work = self.queued_probe_after_gesture()
        self.finish_worker(work)
        rows = self.rows()
        self.assertTrue(any(row["phase"] == "down" and row["reason"] == "drag-started" for row in rows))
        self.assertTrue(any(row["phase"] == "move" and row["status"] == "accepted" for row in rows))
        up = next(row for row in rows if row["phase"] == "up")
        self.assertEqual(up["status"], "accepted")
        self.assertTrue(up["diag"]["receipt"]["probeRequired"])
        self.assertTrue(up["diag"]["display"]["token"])
        receipts = [row for row in self.traces if row.get("stage") == "action-receipt"]
        self.assertTrue(any(row["action"] == "probe_map_after_drag" and
            row["reason"] == "post-drag-observation-current" for row in receipts))

    def test_no_map_context_is_recorded_without_coordinate_guess(self):
        self.dispatch("close_map")
        self.session.refreshRuntime()
        self.pointer("down", 500, 500)
        rows = self.rows()
        self.assertTrue(rows, "map-inactive press disappeared before the coordinator")
        self.assertEqual(rows[-1]["reason"], "pointer-context-unavailable")
        self.assertFalse(rows[-1]["diag"]["state"]["mapOpen"])
        self.assertNotIn("pressClient", rows[-1]["diag"])
        self.assertIsNone(self.coordinator._pointer_drag)

    def test_expired_press_and_exception_remain_distinct(self):
        event = self.source._pointer_event(500, 500, 42, phase="down", created_at=1.)
        event["context"]["generation"] -= 1
        self.source._queue_pointer_event(event)
        self.app.processEvents()
        rows = self.rows()
        self.assertTrue(rows, "expired scope left no structured rejection")
        self.assertIn("expired", rows[-1]["reason"])
        self.assertEqual(rows[-1]["status"], "rejected")
        with patch.object(self.runtime, "observe_game_floor_click", side_effect=ValueError("PRIVATE-ACCOUNT secret")):
            self.pointer("down", 500, 500)
        last = self.rows()[-1]
        self.assertEqual(last["status"], "exception")
        self.assertEqual(last["diag"]["exceptionType"], "ValueError")
        self.assertNotIn("PRIVATE-ACCOUNT", json.dumps(self.traces))

    def test_disk_preserves_distinct_gestures_and_excludes_unrelated_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ProblemsService(SimpleNamespace(work_root=Path(folder)),
                build_fingerprint="TEST", source_commit="a" * 40)
            self.coordinator.traceReported.connect(service.recordTrace)
            # Two identical rejected presses must retain separate gesture identities.
            self.pointer("down", 20, 500)
            self.pointer("down", 20, 500)
            before = len(self.rows())
            self.source._receive_pointer(987654, 876543, 999, phase="down", created_at=1.)
            self.app.processEvents()
            self.assertEqual(len(self.rows()), before)
            service.shutdown()
            deadline = time.monotonic() + 3
            while service.pendingWrites and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.001)
            self.assertFalse(service.pendingWrites)
            self.coordinator.traceReported.disconnect(service.recordTrace)
            data = service.log_path.read_text("utf-8")
            rows = [json.loads(line) for line in data.splitlines()]
            rows = [row for row in rows if row.get("stage") == "pointer-input" and row["phase"] == "down"]
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]["diag"]["gestureId"], rows[1]["diag"]["gestureId"])
            for secret in ("987654", "876543", "clientBounds", "profileId", "hwnd", "motionSamples"):
                self.assertNotIn(secret, json.dumps(rows))

    def test_move_sampling_and_disabled_listener_preserve_recovery(self):
        self.keys._enabled = False  # Exercise the existing queued consumer guard.
        self.pointer("down", 500, 500)
        self.assertEqual(self.rows()[-1]["reason"], "pointer-listener-disabled")
        self.assertIsNone(self.coordinator._pointer_drag)
        self.keys._enabled = True
        self.pointer("down", 500, 500)
        for offset in range(1, 101):
            self.pointer("move", 500 + offset, 500)
        pending = []
        with patch.object(self.session._pool, "start", side_effect=pending.append):
            self.pointer("up", 600, 500)
        rows = self.rows()
        self.assertEqual(len([row for row in rows if row["phase"] == "move"]), 1)
        self.assertEqual(rows[-1]["phase"], "up")
        self.assertEqual(rows[-1]["diag"]["moveEvents"], 100)
        self.assertEqual(rows[-1]["status"], "accepted")
        self.assertIsNotNone(self.runtime.render_payload(self.coordinator.alignedData["token"]))
        self.finish_worker(pending[0])

    def test_old_press_and_changed_geometry_have_reason_without_stale_coordinates(self):
        context = self.runtime.game_floor_click_context()
        self.source._receive_pointer(500, 500, 42, phase="down",
            created_at=context["observationCreatedAt"] - .01)
        self.app.processEvents()
        self.assertEqual(self.rows()[-1]["reason"], "pointer-press-before-current-frame")
        self.assertNotIn("pressClient", self.rows()[-1]["diag"])
        from client_region_capture import ClientCaptureContext
        self.backend.current = ClientCaptureContext((1280, 720), (200, 200, 1480, 920), 96)
        self.pointer("down", 500, 500)
        self.assertEqual(self.rows()[-1]["reason"], "capture-context-changed")
        self.assertNotIn("pressClient", self.rows()[-1]["diag"])
        self.assertIsNone(self.coordinator._pointer_drag)


class DifficultPointerDiagnosticContracts(PointerDiagnosticContracts):
    mode = "difficult"


class DiagnosticBudgetContracts(unittest.TestCase):
    def test_source_diagnostics_cannot_evict_full_gameplay_mailbox(self):
        from client_native_keys import PassThroughKeyboardSource
        source = PassThroughKeyboardSource()  # Never start a native thread.
        source.update_pointer_target(42)
        source.set_pointer_context_provider(lambda: dict(observationCreatedAt=0))
        for _ in range(32):
            source._receive_pointer(500, 500, 42, phase="down", created_at=1.)
            source._receive_pointer(500, 500, 42, phase="up", created_at=1.)
        source.set_pointer_context_provider(lambda: None)
        signals = []
        source.pointerDiagnosticAvailable.connect(lambda: signals.append(1))
        with patch("client_native_keys.time.monotonic", side_effect=range(100)):
            for _ in range(100):
                source._receive_pointer(500, 500, 42, phase="down", created_at=1.)
        edges = source.take_pointer_events()
        self.assertEqual(len(edges), 64)
        self.assertEqual([edge["phase"] for edge in edges], ["down", "up"] * 32)
        self.assertEqual(signals, [1])
        self.assertEqual(source.take_pointer_diagnostic()["diagnosticReason"], "pointer-context-unavailable")
        self.assertIsNone(source.take_pointer_diagnostic())

    def test_bounded_flood_recovers_and_receipts_have_reserved_budget(self):
        from client_input_diagnostics import InputDiagnostics
        now, rows = [0.], []
        recorder = InputDiagnostics(rows.append, clock=lambda: now[0])
        state = dict(mapOpen=True, roundActive=True, generation=1)
        calls = []
        def snapshot():
            calls.append(1)
            return state
        for gesture in range(10000):
            recorder.pointer(dict(phase="down", gestureId=gesture), "rejected", "drag-context-expired", snapshot, {})
        self.assertEqual(len(rows), 12)
        self.assertEqual(len(calls), 12)  # No state copies after the budget is spent.
        recorder.action(dict(action="map", success=False, reason="map-open-preparation-deadline"), state, {})
        self.assertEqual(rows[-1]["reason"], "map-open-preparation-deadline")
        now[0] = .5
        recorder.pointer(dict(phase="down", gestureId=10001), "accepted", "drag-started", snapshot, {})
        self.assertEqual(rows[-1]["status"], "accepted")
        self.assertEqual(rows[-1]["diag"]["rateSuppressed"], 9988)
        self.assertEqual(set(recorder.buckets), {"pointer-input", "action-receipt"})
        self.assertEqual(len(recorder.__dict__), 8)

    def test_allowlist_drops_text_paths_outside_client_and_opaque_context(self):
        from client_input_diagnostics import InputDiagnostics
        rows = []
        recorder = InputDiagnostics(rows.append)
        event = dict(phase="down", gestureId="PRIVATE-ACCOUNT", x=10000, y=20000,
            text="private typed text", context=dict(clientBounds=[100, 200, 1380, 920],
                frameId="C:/Private/Player", token="private credential", profileId="private profile",
                account="PRIVATE-ACCOUNT", dragBoundaryNormalized=[float("nan")] * 4))
        recorder.pointer(event, "exception", "invalid-input-context", lambda: {}, {},
            error=ValueError("PRIVATE-ACCOUNT"))
        recorder.action(dict(action="map", success=False, error=True,
            reason="C:/Private/Player secret", traceback="private traceback"), {}, {})
        encoded = json.dumps(rows, allow_nan=False)
        for secret in ("PRIVATE-ACCOUNT", "C:/Private", "private", "10000", "20000", "traceback", "clientBounds"):
            self.assertNotIn(secret, encoded)
        self.assertEqual(rows[0]["diag"]["exceptionType"], "ValueError")
        self.assertEqual(rows[1]["status"], "exception")


if __name__ == "__main__":
    unittest.main()
