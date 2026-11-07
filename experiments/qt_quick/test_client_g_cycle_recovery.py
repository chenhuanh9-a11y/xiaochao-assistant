"""Actual runtime recovery/cancellation; native capture and fitting are synthetic."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_client_live_runtime as fixtures
import numpy as np
from g_cycle_evidence import GCycleEvidenceRecorder
from g_cycle_observation import emit
from s0_test_fixtures import synthetic_read


class _Recorder:
    accepting = True

    def __init__(self):
        self.rows = []

    def submit(self, roi, metadata):
        self.rows.append((None if roi is None else roi.copy(), deepcopy(metadata)))
        return dict(status="queued", case_id="a" * 32)


class RecoveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.LiveRuntimeContracts()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.runtime = self.f.runtime
        self.recorder = _Recorder()
        self.runtime.g_cycle_evidence = self.recorder
        self.runtime.configure_failure_sampling(event_path="events-qt-ring.jsonl",
            build_fingerprint="ABCDEF12", source_commit="a" * 40)
        self.assertTrue(self.f.dispatch("start_round")["success"])
        self.enterContext(patch.object(fixtures._AlignmentHost, "fit", side_effect=self.fit))

    def fit(self, frame, *args, **kwargs):
        emit("structure-input", frame, dict(observationBounds=(10, 20, 40, 45)))
        return deepcopy(self.f.fit)

    def assert_receipt_identity(self, row, receipt):
        self.assertEqual(row[1]["opening"], receipt.get("mapAttempt", {}).get("openingId", "none"))
        self.assertEqual(row[1]["attempt"], receipt.get("mapAttempt", {}).get("attemptId", "none"))
        self.assertEqual(row[1]["reason"], receipt["reason"])

    def test_actual_recovery_keeps_opening_but_uses_new_attempt(self):
        first = self.f.dispatch("map", openIntent=True, inputSequence=1)
        self.assertTrue(first["success"], first)
        first_row = self.recorder.rows[-1]
        self.assertIsNotNone(first_row[0])
        self.now = 1.
        self.runtime._monotonic = lambda: self.now
        payload = dict(_diagnosticOpeningId=first["mapAttempt"]["openingId"], inputSequence=1)
        self.assertTrue(self.runtime._arm_map_open_recovery(None, self.runtime._epoch, payload))
        ticket = self.runtime.map_open_recovery_status()["id"]
        self.now += .3
        before = self.f.capture.count
        with patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False)), \
                patch("floor_control_region.locate_vertical_floor_controls", return_value=dict(selectedFloor=None)):
            pending = self.f.dispatch("recover_map_open", recoveryId=ticket)
        self.assertFalse(pending["success"], pending)
        self.assertEqual(pending["reason"], "map-open-recovery-pending")
        self.assertEqual(self.f.capture.count, before + 1)
        pending_row = self.recorder.rows[-1]
        self.assert_receipt_identity(pending_row, pending)
        self.assertEqual(pending_row[1]["opening"], first_row[1]["opening"])
        self.assertNotEqual(pending_row[1]["attempt"], first_row[1]["attempt"])
        self.now += .3
        recovered = self.f.dispatch("recover_map_open", recoveryId=ticket)
        self.assertTrue(recovered["success"], recovered)
        row = self.recorder.rows[-1]
        self.assert_receipt_identity(first_row, first)
        self.assert_receipt_identity(row, recovered)
        self.assertEqual(row[1]["opening"], first_row[1]["opening"])
        self.assertNotEqual(row[1]["attempt"], first_row[1]["attempt"])
        self.assertNotEqual(row[1]["attempt"], pending_row[1]["attempt"])
        self.assertEqual(row[1]["input_sequence"], 1)
        self.assertIsNotNone(row[0])
        self.assertFalse(self.runtime.snapshot()["mapRecoveryPending"])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])

    def _pending_after_success(self, *, with_control_roi):
        first = self.f.dispatch('map', openIntent=True, inputSequence=1)
        self.assertTrue(first['success'], first)
        previous_frame = self.runtime._frame_id
        self.now = 1.
        self.runtime._monotonic = lambda: self.now
        self.assertTrue(self.runtime._arm_map_open_recovery(None, self.runtime._epoch,
            dict(_diagnosticOpeningId=first['mapAttempt']['openingId'], inputSequence=1)))
        ticket = self.runtime.map_open_recovery_status()['id']
        self.now += .3
        self.f.capture.frame[:] = 17
        before = self.f.capture.count
        controls = dict(synthetic_read(), selectedFloor=None) if with_control_roi else dict(selectedFloor=None)
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)), \
                patch('floor_control_region.locate_vertical_floor_controls', return_value=controls):
            pending = self.f.dispatch('recover_map_open', recoveryId=ticket)
        self.assertEqual(pending['reason'], 'map-open-recovery-pending')
        self.assertEqual(self.f.capture.count, before + 1)
        self.assertEqual(self.runtime._frame_id, previous_frame, 'diagnostics must not commit a runtime frame')
        return first, pending, previous_frame

    def test_saved_failed_capture_does_not_claim_previous_successful_frame(self):
        from replay_s1_crop_observation import load_g_cycle_case
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'evidence'
            recorder = GCycleEvidenceRecorder(root, enabled=True)
            self.assertTrue(recorder.start())
            self.runtime.g_cycle_evidence = recorder
            try:
                first, pending, previous_frame = self._pending_after_success(with_control_roi=True)
                self.assertTrue(recorder.idle())
                cases = [json.loads(path.read_text()) for path in root.glob('case-*.json')]
                good = next(row for row in cases if row['metadata']['attempt'] == first['mapAttempt']['attemptId'])
                self.assertEqual(good['metadata']['frame'], previous_frame)
                bad = next(row for row in cases if row['metadata']['attempt'] == pending['mapAttempt']['attemptId'])
                image, meta, full, _ = load_g_cycle_case(root/f"case-{bad['case_id']}.json")
                self.assertEqual(meta['sample_role'], 'floor-control-input')
                self.assertEqual(image.shape, (324, 231, 3))
                self.assertTrue(np.all(image == 17))
                self.assertFalse(full)
                self.assertNotEqual(previous_frame.rsplit(':', 1)[-1],
                    hashlib.sha256(self.f.capture.frame.tobytes()).hexdigest()[:16])
                self.assertEqual(meta['frame'], 'unknown')
            finally:
                recorder.shutdown(wait=True)

    def test_valid_failed_capture_without_roi_is_not_capture_unavailable(self):
        _, pending, _ = self._pending_after_success(with_control_roi=False)
        image, meta = self.recorder.rows[-1]
        self.assertIsNone(image)
        self.assertEqual(meta['attempt'], pending['mapAttempt']['attemptId'])
        self.assertEqual(meta['missing_reason'], 'no-algorithm-roi')
        self.assertEqual(meta['frame'], 'unknown')

    def _worker(self, action, **payload):
        results, errors = [], []
        def work():
            try:
                results.append(self.f.dispatch(action, **payload))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        return thread, results, errors

    def test_close_during_actual_fit_cancels_old_attempt_and_discards_roi(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_fit(*args, **kwargs):
            result = self.fit(*args, **kwargs)
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture fit was not released")
            return result
        with patch.object(fixtures._AlignmentHost, "fit", side_effect=blocked_fit):
            thread, results, errors = self._worker("map", openIntent=True, inputSequence=1)
            try:
                self.assertTrue(entered.wait(3))
                old_cycle = self.runtime._cycle
                closed = self.f.dispatch("close_map", inputSequence=2)
                self.assertTrue(closed["success"], closed)
            finally:
                release.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        result = results[0]
        self.assertFalse(result["success"], result)
        self.assertTrue(result.get("stale") or result.get("cancelled"), result)
        old = next(row for row in self.recorder.rows if row[1].get("input_sequence") == 1)
        self.assert_receipt_identity(old, result)
        self.assertEqual(old[1]["cycle"], old_cycle)
        self.assertEqual(old[1]["outcome"], "cancelled")
        self.assertIsNone(old[0])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        newer = self.f.dispatch("map", openIntent=True, inputSequence=3)
        self.assertTrue(newer["success"], newer)
        self.assertNotEqual(old[1]["opening"], self.recorder.rows[-1][1]["opening"])
        self.assertNotEqual(old[1]["attempt"], self.recorder.rows[-1][1]["attempt"])

    def test_late_finish_retains_old_receipt_ids_after_new_open(self):
        entered, release = threading.Event(), threading.Event()
        original = self.runtime._dispatch_after_arrival
        original_results = []
        def delayed_return(action, payload, cancelled, **arrival):
            result = original(action, payload, cancelled, **arrival)
            if action == "map" and payload.get("inputSequence") == 1:
                original_results.append(result)
                entered.set()
                if not release.wait(3):
                    raise AssertionError("fixture receipt tail was not released")
            return result
        with patch.object(self.runtime, "_dispatch_after_arrival", side_effect=delayed_return):
            thread, results, errors = self._worker("map", openIntent=True, inputSequence=1)
            try:
                self.assertTrue(entered.wait(3))
                old_cycle = self.runtime._cycle
                self.assertTrue(self.f.dispatch("close_map", inputSequence=2)["success"])
                newer = self.f.dispatch("map", openIntent=True, inputSequence=3)
                self.assertTrue(newer["success"], newer)
                newer_row = self.recorder.rows[-1]
            finally:
                release.set()
                thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors, errors)
        self.assertIs(results[0], original_results[0], "evidence must return the original business receipt")
        self.assertTrue(results[0]["success"])
        old = next(row for row in self.recorder.rows if row[1].get("input_sequence") == 1)
        self.assert_receipt_identity(old, results[0])
        self.assertEqual(old[1]["cycle"], old_cycle)
        self.assertNotEqual(old[1]["cycle"], newer_row[1]["cycle"])
        self.assertNotEqual(old[1]["opening"], newer_row[1]["opening"])
        self.assertNotEqual(old[1]["attempt"], newer_row[1]["attempt"])
        self.assertIsNone(old[0], "late evidence cannot keep pixels after scope revocation")
        self.assertEqual(old[1]["missing_reason"], "cancelled-or-no-current-capture")
        self.assertIsNotNone(newer_row[0])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])

    def test_end_and_restart_during_fit_preserves_source_state_on_stale_receipt(self):
        self.assertTrue(self.f.dispatch("map", openIntent=True, inputSequence=1)["success"])
        self.assertTrue(self.f.dispatch("close_map", inputSequence=2)["success"])
        entered, release = threading.Event(), threading.Event()
        source = {}
        def blocked_fit(*args, **kwargs):
            result = self.fit(*args, **kwargs)
            source.update(round=self.runtime._round, cycle=self.runtime._cycle,
                          floor=str(self.runtime._floor) or "none")
            entered.set()
            if not release.wait(3):
                raise AssertionError("fixture fit was not released")
            return result
        restart = None
        with patch.object(fixtures._AlignmentHost, "fit", side_effect=blocked_fit):
            thread, results, errors = self._worker("map", openIntent=True, inputSequence=3)
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(self.f.dispatch("end_round")["success"])
                self.assertNotEqual(str(self.runtime._floor) or "none", source["floor"])
                # start_round must wait for the old dispatch's work lock. Queue
                # the real request rather than bypassing runtime serialization.
                restart, restarted, restart_errors = self._worker("start_round")
            finally:
                release.set()
                thread.join(4)
                if restart is not None:
                    restart.join(4)
        self.assertFalse(thread.is_alive())
        self.assertFalse(restart.is_alive())
        self.assertFalse(errors, errors)
        self.assertFalse(restart_errors, restart_errors)
        self.assertTrue(restarted[0]["success"], restarted)
        self.assertFalse(results[0]["success"], results)
        self.assertTrue(results[0].get("stale") or results[0].get("cancelled"), results)
        old = next(row for row in self.recorder.rows if row[1].get("input_sequence") == 3)
        self.assert_receipt_identity(old, results[0])
        self.assertEqual({key: old[1][key] for key in source}, source)
        self.assertEqual(old[1]["outcome"], "cancelled")
        self.assertIsNone(old[0])
        self.assertGreater(self.runtime._round, source["round"])


if __name__ == "__main__":
    unittest.main()
