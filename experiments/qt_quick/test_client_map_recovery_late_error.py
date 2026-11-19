"""A cancelled recovery's late error must not revoke a newer drag owner.

Real source mailbox/Qt delivery, recovery worker, preparation, solving, runtime
grants and final alignedData/window sinks execute. The existing offline fixture
replaces OS capture/pixels, fit measurements, DIP projection and window/image
objects. A barrier after the real solve tail injects a late worker exception;
no production receipt or authorization check is replaced with fake success.
Native API access and source-thread installation are explicitly forbidden.
"""
from copy import deepcopy
import json
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from client_input import NativeWindowAPI
from client_native_keys import PassThroughKeyboardSource
import test_client_map_open_recovery as recovery_fixtures


class MapRecoveryLateErrorContracts(unittest.TestCase):
    mode = "nightmare"
    dispatch = recovery_fixtures.MapOpenRecoveryContracts.dispatch
    _stop_timers = recovery_fixtures.MapOpenRecoveryContracts._stop_timers
    _cleanup_input_session = recovery_fixtures.MapOpenRecoveryContracts._cleanup_input_session
    not_ready = recovery_fixtures.MapOpenRecoveryContracts.not_ready
    miss_initial_preparation = recovery_fixtures.MapOpenRecoveryContracts.miss_initial_preparation
    settle = recovery_fixtures.MapOpenRecoveryContracts.settle

    def setUp(self):
        for target, attribute in ((NativeWindowAPI, "_api"), (PassThroughKeyboardSource, "_run")):
            guard = patch.object(target, attribute, side_effect=AssertionError("native IO forbidden"))
            guard.start()
            self.addCleanup(guard.stop)
        recovery_fixtures.MapOpenRecoveryContracts.setUp(self)

    def wait_for(self, predicate, message):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                self.app.processEvents()
                return
            time.sleep(.001)
        self.fail(message)

    def pointer(self, phase, x, y):
        self.source._receive_pointer(x, y, 42, phase=phase, created_at=self.now)
        self.app.processEvents()

    def state_evidence(self, phase):
        state = self.runtime.snapshot()
        consumer = self.coordinator.alignedData
        render = state.get("render") or {}
        keys = ("token", "frameId", "generation", "transformRevision", "epoch", "cycleId")
        return dict(phase=phase, consumer={key: consumer.get(key) for key in keys},
            runtime={key: render.get(key) for key in keys},
            displayAuthorized=state["core"]["displayAuthorized"],
            matrix=deepcopy(consumer.get("matrix")),
            coordinatorDrag=self.coordinator._pointer_drag is not None,
            runtimeDrag=self.runtime._transient_drag is not None,
            recoveryPending=state.get("mapRecoveryPending"))

    def assert_tail_without_new_owner(self, *, cancel):
        self.miss_initial_preparation()
        entered, release = threading.Event(), threading.Event()
        original_tail = self.runtime._retry_desired_group_member
        original_start = self.session._pool.start
        workers = []

        def complete_tail(*args, **kwargs):
            receipt = original_tail(*args, **kwargs)
            entered.set()
            if not release.wait(4):
                raise AssertionError("recovery terminal test barrier was not released")
            if cancel:
                return receipt  # A real success prepared before cancellation.
            raise ValueError("controlled current recovery tail failure")

        def start_real(work):
            workers.append(work)
            return original_start(work)

        with patch.object(self.runtime, "_retry_desired_group_member", side_effect=complete_tail), \
                patch.object(self.session._pool, "start", side_effect=start_real):
            try:
                self.now += .3
                self.coordinator._tracking_timer.timeout.emit()
                self.wait_for(entered.is_set, "recovery never reached its real post-solve tail")
                self.assertEqual(len(workers), 1)
                self.assertEqual(workers[0].action, "recover_map_open")
                before = self.state_evidence("published-before-terminal")
                token = before["consumer"]["token"]
                self.assertTrue(token)
                self.assertTrue(before["displayAuthorized"])
                material = self.runtime.render_payload(token)
                self.assertIsNotNone(material)
                self.assertTrue(material['poseFinalVerified'])
                self.assertEqual(material['reviewStatus'], 'complete')
                self.assertFalse(material.get('requiresMaterialAck'))
                self.assertIsNone(self.coordinator._pointer_drag)
                self.ui.windows.hide.reset_mock()
                if cancel:
                    self.session.stop()
                    self.assertTrue(workers[0].cancelled.is_set())
                else:
                    self.assertFalse(workers[0].cancelled.is_set())
                release.set()
                self.settle()
                after = self.state_evidence("terminal-delivered-no-new-owner")
                receipt = self.session.snapshot.get("lastReceipt", {})
                print("RECOVERY_TERMINAL_EVIDENCE", json.dumps(dict(mode=self.mode,
                    cancelled=cancel, states=[before, after], staleResults=self.session.stale_results,
                    receipt={key: receipt.get(key) for key in ("action", "success", "stale", "error", "reason")},
                    revocations=[row.get("reason") for row in self.traces
                        if row.get("stage") == "render-revoked"]), ensure_ascii=True), flush=True)
                self.assertTrue(after["displayAuthorized"],
                    "a completed current material must survive a later worker terminal")
                self.assertIsNotNone(self.runtime.render_payload(token))
                self.assertEqual(self.coordinator.alignedData.get('token'), token)
                self.assertFalse(any(call.args == ("aligned",)
                    for call in self.ui.windows.hide.call_args_list))
                if cancel:
                    self.assertGreaterEqual(self.session.stale_results, 1)
                else:
                    self.assertTrue(receipt.get("error"), "a current recovery exception was mislabeled stale")
                    self.assertFalse(receipt.get("stale", False))
                    self.assertEqual(receipt["action"], "recover_map_open")
                    self.assertTrue(any(row.get("stage") == "action-receipt"
                        and row.get("action") == "recover_map_open"
                        and row.get("status") == "exception" for row in self.traces))
                self.keys._dispatch_action('escape', dict(createdAt=self.now))
                self.settle()
                self.assertFalse(self.runtime.snapshot()['mapOpen'])
                self.assertFalse(self.runtime.snapshot()['core']['displayAuthorized'])
                self.assertIsNone(self.runtime.render_payload(token))
                self.assertFalse(self.coordinator.alignedData)
            finally:
                release.set()
                self.wait_for(lambda: self.session._pool.activeThreadCount() == 0,
                    "recovery terminal worker did not exit")

    def test_current_recovery_tail_error_keeps_complete_material_and_error_receipt(self):
        self.assert_tail_without_new_owner(cancel=False)

    def test_cancelled_recovery_without_new_drag_keeps_its_completed_material(self):
        self.assert_tail_without_new_owner(cancel=True)

    def test_late_recovery_error_after_current_drag_cannot_revoke_new_owner(self):
        self.miss_initial_preparation()
        self.assertTrue(self.runtime.snapshot().get("mapRecoveryPending"))
        entered, release = threading.Event(), threading.Event()
        workers, schedule = [], {"uiThread": threading.get_ident()}
        original_tail = self.runtime._retry_desired_group_member
        original_start = self.session._pool.start

        def late_error(*args, **kwargs):
            receipt = original_tail(*args, **kwargs)
            schedule.update(workerThread=threading.get_ident(),
                realTailSuccess=receipt.get("success"), realTailReason=receipt.get("reason"))
            entered.set()
            if not release.wait(4):
                raise AssertionError("late recovery test barrier was not released")
            raise ValueError("controlled late recovery tail failure")

        def start_real(work):
            workers.append(work)
            return original_start(work)

        with patch.object(self.runtime, "_retry_desired_group_member", side_effect=late_error), \
                patch.object(self.session._pool, "start", side_effect=start_real):
            try:
                self.now += .3
                self.coordinator._tracking_timer.timeout.emit()
                self.wait_for(entered.is_set, "real recovery did not reach its completed solve tail")
                self.assertEqual(len(workers), 1)
                work = workers[0]
                self.assertEqual(work.action, "recover_map_open")
                self.assertNotEqual(schedule["uiThread"], schedule["workerThread"])
                self.assertTrue(schedule["realTailSuccess"])
                self.assertTrue(self.session.busy)
                self.assertTrue(self.runtime._capture_pending)
                self.assertFalse(self.runtime.snapshot().get("mapRecoveryPending"))
                states = [self.state_evidence("recovery-published-worker-still-running")]
                self.assertTrue(states[0]["displayAuthorized"])
                token = states[0]["consumer"]["token"]
                self.assertTrue(token)
                before = np.array(states[0]["matrix"])

                self.pointer("down", 500, 500)
                self.assertIsNotNone(self.coordinator._pointer_drag)
                self.assertFalse(work.cancelled.is_set())
                self.pointer("move", 540, 520)
                self.assertTrue(work.cancelled.is_set(), "real drag must cancel the old recovery work")
                states.append(self.state_evidence("new-drag-published-old-worker-blocked"))
                self.assertEqual(states[-1]["consumer"]["token"], token)
                self.assertGreater(states[-1]["consumer"]["transformRevision"], 0)
                self.assertIsNotNone(self.runtime.render_payload(token))
                np.testing.assert_allclose(np.array(states[-1]["matrix"])[:2, 2] - before[:2, 2], [40, 20])
                self.ui.windows.hide.reset_mock()

                release.set()
                self.settle()
                states.append(self.state_evidence("cancelled-worker-late-error-delivered"))
                print("RECOVERY_LATE_ERROR_EVIDENCE", json.dumps(dict(mode=self.mode,
                    schedule=schedule, states=states, staleResults=self.session.stale_results,
                    revocations=[row.get("reason") for row in self.traces
                        if row.get("stage") == "render-revoked"]), ensure_ascii=True), flush=True)
                self.assertGreaterEqual(self.session.stale_results, 1)
                self.assertTrue(states[-1]["displayAuthorized"],
                    "cancelled recovery's late error revoked the newer drag grant")
                self.assertEqual(states[-1]["consumer"]["token"], token)
                self.assertEqual(states[-1]["consumer"]["transformRevision"],
                    states[-2]["consumer"]["transformRevision"])
                np.testing.assert_allclose(states[-1]["matrix"], states[-2]["matrix"])
                self.assertIsNotNone(self.runtime.render_payload(token))
                self.assertFalse(any(call.args == ("aligned",)
                    for call in self.ui.windows.hide.call_args_list))

                self.pointer("move", 550, 525)
                self.assertGreater(self.coordinator.alignedData["transformRevision"],
                    states[-1]["consumer"]["transformRevision"])
                self.pointer("up", 550, 525)
                self.settle()
                self.assertEqual(self.coordinator.alignedData.get("token"), token)
                np.testing.assert_allclose(np.array(self.coordinator.alignedData["matrix"])[:2, 2]
                    - before[:2, 2], [50, 25])
            finally:
                release.set()
                self.wait_for(lambda: self.session._pool.activeThreadCount() == 0,
                    "late recovery worker did not exit")


class DifficultMapRecoveryLateErrorContracts(MapRecoveryLateErrorContracts):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
