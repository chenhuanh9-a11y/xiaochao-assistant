"""Synthetic G integration, actual ROI hook and process-only enable contract."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import numpy as np
import test_client_live_runtime as fixtures
from client_g_cycle_evidence import _Trace
from g_cycle_evidence import GCycleEvidenceRecorder
from g_cycle_observation import emit, observe_inputs


class _Recorder:
    accepting = True

    def __init__(self):
        self.rows = []

    def submit(self, roi, metadata):
        self.rows.append((None if roi is None else roi.copy(), metadata.copy()))
        return dict(status="accepted")


class GCycleIntegration(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LiveRuntimeContracts()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.recorder = _Recorder()
        self.f.runtime.g_cycle_evidence = self.recorder
        self.f.runtime.configure_failure_sampling(event_path="events-qt-ring.jsonl",
            build_fingerprint="ABCDEF12", source_commit="a" * 40)
        self.f.dispatch("start_round")

    def _emit_fit(self, *args, **kwargs):
        frame = args[0]
        # Exact synthetic solver input, no capture from the evidence path.
        emit("structure-input", frame, dict(observationBounds=(10, 20, 40, 45)))
        return dict(self.f.fit)

    def test_success_failure_close_share_original_ids_and_do_not_add_capture(self):
        with patch.object(fixtures._AlignmentHost, "fit", side_effect=self._emit_fit):
            first = self.f.dispatch("map", openIntent=True, inputSequence=1)
            count = self.f.capture.count
            close = self.f.dispatch("map", openIntent=False, inputSequence=2)
            self.assertEqual(self.f.capture.count, count)
            self.f.fit = dict(success=False, reason="structure-current-geometry-failed")
            failed = self.f.dispatch("map", openIntent=True, inputSequence=3)
        self.assertTrue(first["success"], first)
        self.assertTrue(close["success"], close)
        self.assertFalse(failed["success"], failed)
        self.assertEqual(len(self.recorder.rows), 3)
        good, closing, bad = self.recorder.rows
        self.assertEqual(good[0].shape, (25, 30, 3))
        self.assertIsNone(closing[0])
        self.assertEqual(bad[0].shape, (25, 30, 3))
        self.assertEqual(good[1]["outcome"], "success")
        self.assertEqual(bad[1]["outcome"], "failure")
        self.assertEqual(good[1]["opening"], first["mapAttempt"]["openingId"])
        self.assertEqual(bad[1]["attempt"], failed["mapAttempt"]["attemptId"])
        self.assertEqual([r[1]["input_sequence"] for r in self.recorder.rows], [1, 2, 3])
        self.assertNotEqual(good[1]["cycle"], bad[1]["cycle"])
        self.assertEqual(good[1]["floor"], "1")

    def test_real_backend_persists_bounded_roi_and_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            recorder = GCycleEvidenceRecorder(Path(temp) / "evidence", enabled=True)
            self.assertTrue(recorder.start())
            self.f.runtime.g_cycle_evidence = recorder
            with patch.object(fixtures._AlignmentHost, "fit", side_effect=self._emit_fit):
                result = self.f.dispatch("map", openIntent=True, inputSequence=1)
            recorder.shutdown(wait=True)
            self.assertTrue(result["success"], result)
            files = list((Path(temp) / "evidence").glob("case-*.json"))
            self.assertEqual(len(files), 1, recorder.snapshot())
            row = json.loads(files[0].read_text())
            self.assertIn("structure-input", json.dumps(row))
            self.assertEqual(len(list((Path(temp) / "evidence").glob("case-*.png"))), 1)

    def test_foreign_same_shape_input_cannot_supply_pixels(self):
        def foreign(*args, **kwargs):
            emit("structure-input", np.full_like(args[0], 231),
                 dict(observationBounds=(10, 20, 40, 45)))
            return dict(self.f.fit)
        with patch.object(fixtures._AlignmentHost, "fit", side_effect=foreign):
            result = self.f.dispatch("map", openIntent=True, inputSequence=1)
        self.assertTrue(result["success"], result)
        roi, meta = self.recorder.rows[0]
        self.assertNotEqual(meta["sample_role"], "structure-input")
        if roi is not None:
            self.assertFalse(np.any(roi == 231))

    def test_disabled_path_never_instantiates_trace(self):
        self.recorder.accepting = False
        with patch("client_g_cycle_evidence._Trace", side_effect=AssertionError("disabled observer")):
            self.f.dispatch("map", openIntent=False, inputSequence=1)
        self.assertFalse(self.recorder.rows)

    def test_trace_does_not_change_runtime_result_on_writer_error(self):
        self.recorder.submit = lambda *a: (_ for _ in ()).throw(OSError("private/path"))
        result = self.f.dispatch("map", openIntent=False, inputSequence=1)
        self.assertTrue(result["success"])

    def test_scope_change_drops_pixels_but_keeps_original_attempt(self):
        trace = _Trace(self.f.runtime, self.recorder, "map", {"inputSequence": 1})
        self.f.capture.enable()
        captured = self.f.capture.capture()
        trace("capture", captured, None, self.f.runtime._failure_capture_scope(), "a"*32)
        trace("structure-input", captured.frame_bgr, dict(observationBounds=(10, 20, 40, 45)))
        trace("receipt", "map", {"_diagnosticAttemptId": "a"*32}, False, "fit-failed", {})
        self.f.runtime.invalidate("map-closed")
        trace.finish()
        self.assertIsNone(self.recorder.rows[0][0])
        self.assertEqual(self.recorder.rows[0][1]["attempt"], "a"*32)

    def test_crop_snapshot_is_not_changed_by_mutable_transport(self):
        trace = _Trace(self.f.runtime, self.recorder, "map", {})
        self.f.capture.enable()
        captured = self.f.capture.capture()
        trace("capture", captured, None, self.f.runtime._failure_capture_scope(), "a"*32)
        trace("structure-input", captured.frame_bgr, dict(observationBounds=(10, 20, 40, 45)))
        captured.frame_bgr[:] = 250
        self.assertFalse(np.any(trace.roi))

    def test_second_capture_cannot_reuse_old_roi_after_failure(self):
        trace = _Trace(self.f.runtime, self.recorder, "map", {})
        self.f.capture.enable()
        captured = self.f.capture.capture()
        trace("capture", captured, None, self.f.runtime._failure_capture_scope(), "a"*32)
        trace("structure-input", captured.frame_bgr, dict(observationBounds=(10, 20, 40, 45)))
        trace("capture-start")
        self.assertIsNone(trace.roi)
        self.assertNotIn("crop_bounds", trace.metadata)
        self.assertIsNone(trace.generation)
        self.assertIsNone(trace.capture_scope)
        self.assertEqual(trace.source_state['frame'], 'unknown')
        self.assertFalse(trace._known_frame(None))

    def test_actual_structure_extractor_emits_existing_bounds_without_reextract(self):
        from map_observation import structure_current_map_evidence
        frame = np.zeros((20, 30, 3), np.uint8)
        visible = (frame[2:12, 4:14], (4, 2, 14, 12), {})
        rows = []
        with observe_inputs(lambda *a: rows.append(a)), patch("map_observation.map_reuse_evidence",
                return_value=dict(translationMask=(np.ones((20, 30)), 1))) as extract:
            result = structure_current_map_evidence(frame, visible_override=visible)
        extract.assert_called_once()
        self.assertEqual(rows[0][0], "structure-input")
        self.assertIs(rows[0][1], frame)
        self.assertIs(rows[0][2], result)

    def test_context_does_not_leak_to_background_or_after_exit(self):
        rows = []
        with observe_inputs(lambda *a: rows.append(a)):
            thread = threading.Thread(target=lambda: emit("other-worker"))
            thread.start(); thread.join()
            emit("owner")
        emit("after")
        self.assertEqual(rows, [("owner",)])

    def test_observer_failure_is_noninterfering(self):
        with observe_inputs(lambda *a: (_ for _ in ()).throw(ValueError("bad observer"))):
            emit("anything")


if __name__ == "__main__":
    unittest.main()
