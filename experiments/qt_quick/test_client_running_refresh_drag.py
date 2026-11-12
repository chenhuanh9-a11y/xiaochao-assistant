"""Running refresh cancellation cannot revoke a subsequently published drag.

The refresh executes in Session's real QThreadPool; barriers only delay return
from real capture/residual work, outside owner locks. UI pointer delivery uses
the real source mailbox and queued backend. Native hooks/OS pixels, initial fit
measurements, DIP projection and image/window sinks are the existing fixtures.
No production guard or receipt is replaced. No native input is installed.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import _AlignmentHost
import test_client_drag_probe_presentation as ingress_fixtures


class RunningRefreshDragContracts(unittest.TestCase):
    mode = "nightmare"
    # Reuse only setup/helpers, not the prior module's unrelated test methods.
    setUp = ingress_fixtures.DragProbePresentationContracts.setUp
    dispatch = ingress_fixtures.DragProbePresentationContracts.dispatch
    _stop_timers = ingress_fixtures.DragProbePresentationContracts._stop_timers
    _cleanup_input_session = ingress_fixtures.DragProbePresentationContracts._cleanup_input_session
    pointer = ingress_fixtures.DragProbePresentationContracts.pointer
    assert_withdrawn = ingress_fixtures.DragProbePresentationContracts.assert_withdrawn

    def wait_for(self, predicate, description):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail(description)

    @contextmanager
    def running_refresh(self, boundary):
        entered, release = threading.Event(), threading.Event()
        ui_thread = threading.get_ident()
        workers = []
        record = {"boundary": boundary, "uiThread": ui_thread}

        def pause(result):
            if entered.is_set():
                return result
            record["workerThread"] = threading.get_ident()
            entered.set()
            if not release.wait(4):
                raise AssertionError("test did not release the running refresh")
            return result

        if boundary == "floor-policy-before":
            original = self.runtime._observe_floor_policy
            def policy_after_cancel(*args, **kwargs):
                pause(None)
                return original(*args, **kwargs)
            barrier = patch.object(self.runtime, "_observe_floor_policy", side_effect=policy_after_cancel)
        elif boundary == "backend-capture":
            original = self.backend.capture
            def capture_in_progress(*args, **kwargs):
                pause(None)
                return original(*args, **kwargs)
            barrier = patch.object(self.backend, "capture", side_effect=capture_in_progress)
        elif boundary == "capture-return":
            original = self.runtime._capture_frame
            barrier = patch.object(self.runtime, "_capture_frame",
                side_effect=lambda cancelled, **kwargs: pause(original(cancelled, **kwargs)))
        else:
            original = _AlignmentHost._canonical_geometry_current_frame_residual
            def measured(host, *args, **kwargs):
                return pause(original(host, *args, **kwargs))
            barrier = patch.object(_AlignmentHost, "_canonical_geometry_current_frame_residual", measured)
        start = self.session._pool.start
        def start_real(work):
            workers.append(work)
            return start(work)
        with barrier, patch.object(self.session._pool, "start", side_effect=start_real):
            try:
                self.session.dispatch("refresh_map")
                self.wait_for(entered.is_set, "real refresh worker did not enter " + boundary)
                self.assertEqual(len(workers), 1)
                self.assertNotEqual(record["workerThread"], ui_thread)
                self.assertEqual(workers[0].action, "refresh_map")
                self.assertTrue(self.session.busy)
                self.assertTrue(self.runtime._capture_pending)
                yield workers[0], release, record
            finally:
                release.set()
                self.wait_for(lambda: self.session._pool.activeThreadCount() == 0,
                    "running refresh did not leave its barrier")

    def snapshot_evidence(self, phase):
        state = self.runtime.snapshot()
        render = state.get("render") or {}
        consumer = self.coordinator.alignedData
        fields = ("frameId", "token", "generation", "transformRevision", "epoch", "cycleId")
        return dict(phase=phase, uiThread=threading.get_ident(),
            runtime={key: render.get(key) for key in fields},
            consumer={key: consumer.get(key) for key in fields},
            sessionGeneration=self.session.snapshot.get("generation"),
            displayAuthorized=state["core"]["displayAuthorized"],
            coordinatorDrag=bool(self.coordinator._pointer_drag),
            runtimeDrag=bool(self.runtime._transient_drag),
            matrix=deepcopy(consumer.get("matrix")))

    def start_drag_and_cancel_refresh(self, work, rows):
        self.pointer("down", 500, 500)
        self.assertIsNotNone(self.coordinator._pointer_drag)
        self.assertFalse(work.cancelled.is_set())
        self.pointer("move", 530, 515)
        self.assertTrue(work.cancelled.is_set(), "first real move must cancel the running session work")
        self.assertIsNotNone(self.coordinator._pointer_drag)
        first = self.snapshot_evidence("first-move-published-old-refresh-still-blocked")
        rows.append(first)
        token = first["consumer"]["token"]
        self.assertTrue(token)
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.assertTrue(first["displayAuthorized"])
        return token

    def emit_evidence(self, schedule, rows):
        print("RUNNING_REFRESH_EVIDENCE", json.dumps(dict(mode=self.mode,
            schedule=schedule, states=rows, staleResults=self.session.stale_results,
            traces=[{key: row.get(key) for key in ("stage", "reason", "frame", "token", "generation", "diag")}
                    for row in self.traces if row.get("stage") in
                    {"clear", "drop", "pointer-rejected", "pointer-event", "publish-transform"}]),
            ensure_ascii=True), flush=True)

    def assert_running_refresh_preserves_drag(self, boundary):
        before = np.array(self.coordinator.alignedData["matrix"])
        rows = [self.snapshot_evidence("initial")]
        with self.running_refresh(boundary) as (work, release, schedule):
            token = self.start_drag_and_cancel_refresh(work, rows)
            self.ui.windows.hide.reset_mock()
            release.set()
            self.wait_for(lambda: self.session._pool.activeThreadCount() == 0,
                "cancelled refresh did not complete")
            after_old = self.snapshot_evidence("old-refresh-completion-delivered")
            rows.append(after_old)
            # Continue through the same real source gesture even on the broken
            # implementation, so the evidence records its downstream rejection.
            self.pointer("move", 550, 525)
            rows.append(self.snapshot_evidence("subsequent-move"))
            self.pointer("up", 550, 525)
            self.wait_for(lambda: not self.session.busy and self.session._pool.activeThreadCount() == 0,
                "gesture completion did not settle")
            rows.append(self.snapshot_evidence("up-completed"))
            self.emit_evidence(schedule, rows)
            self.assertGreaterEqual(self.session.stale_results, 1)
            self.assertTrue(after_old["displayAuthorized"],
                "cancelled old refresh revoked the current drag authorization")
            self.assertEqual(after_old["consumer"]["token"], token)
            self.assertEqual(after_old["consumer"]["transformRevision"],
                rows[1]["consumer"]["transformRevision"])
            np.testing.assert_allclose(after_old["matrix"], rows[1]["matrix"])
            self.assertTrue(rows[2]["coordinatorDrag"])
            self.assertIsNotNone(self.runtime.render_payload(token))
            self.assertEqual(self.coordinator.alignedData.get("token"), token)
            self.assertGreater(rows[3]["consumer"]["transformRevision"],
                rows[1]["consumer"]["transformRevision"])
            np.testing.assert_allclose(np.array(self.coordinator.alignedData["matrix"])[:2, 2]
                - before[:2, 2], [50, 25])
            self.assertFalse(any(call.args == ("aligned",)
                for call in self.ui.windows.hide.call_args_list))

    def test_refresh_capture_return_after_first_drag_move_preserves_current_display(self):
        self.assert_running_refresh_preserves_drag("capture-return")

    def test_refresh_native_capture_cancel_after_first_drag_move_preserves_current_display(self):
        self.assert_running_refresh_preserves_drag("backend-capture")

    def test_refresh_floor_policy_after_first_drag_move_preserves_current_display(self):
        self.assert_running_refresh_preserves_drag("floor-policy-before")

    def test_refresh_residual_return_after_first_drag_move_preserves_current_display(self):
        self.assert_running_refresh_preserves_drag("residual-return")

    def assert_boundary_revokes(self, action):
        rows = [self.snapshot_evidence("initial")]
        with self.running_refresh("capture-return") as (work, release, schedule):
            token = self.start_drag_and_cancel_refresh(work, rows)
            self.ui.windows.hide.reset_mock()
            if action == "close":
                self.keys._dispatch_action("escape", {"createdAt": 1.})
            elif action == "focus":
                self.native.foreground_window = lambda: 99
                self.coordinator._watch_foreground()
            else:
                self.session.dispatch("end_round")
            release.set()
            self.wait_for(lambda: not self.session.busy and self.session._pool.activeThreadCount() == 0,
                "lifetime change and old refresh did not settle")
            self.assert_withdrawn(token)
            rows.append(self.snapshot_evidence(action + "-old-worker-delivered"))
            if action == "round":
                old_round = self.runtime.snapshot()["roundId"]
                self.session.dispatch("start_round")
                self.wait_for(lambda: not self.session.busy and self.session._pool.activeThreadCount() == 0,
                    "new round did not settle")
                self.assertNotEqual(self.runtime.snapshot()["roundId"], old_round)
                self.assert_withdrawn(token)
                rows.append(self.snapshot_evidence("new-round-cannot-reuse-old-grant"))
            self.pointer("move", 550, 525)
            self.pointer("up", 550, 525)
            self.assert_withdrawn(token)
            self.emit_evidence(dict(schedule, invalidation=action), rows)

    def test_close_still_revokes_drag_and_old_running_refresh_cannot_restore(self):
        self.assert_boundary_revokes("close")

    def test_focus_loss_still_revokes_drag_and_old_running_refresh_cannot_restore(self):
        self.assert_boundary_revokes("focus")

    def test_round_change_still_revokes_drag_and_old_running_refresh_cannot_restore(self):
        self.assert_boundary_revokes("round")


class DifficultRunningRefreshDragContracts(RunningRefreshDragContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
