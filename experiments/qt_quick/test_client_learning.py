"""Human-label parity using synthetic pixels and isolated original memory stores."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from test_client_live_runtime import LiveRuntimeContracts
from client_live_runtime import _AlignmentHost
from nightmare_candidate import NightmareMemoryStore


class ManualLearningContracts(unittest.TestCase):
    setUp = LiveRuntimeContracts.setUp
    dispatch = LiveRuntimeContracts.dispatch
    unconfirmed_candidates = LiveRuntimeContracts.unconfirmed_candidates
    begin_and_capture = LiveRuntimeContracts.begin_and_capture

    def live_fixture(self, mode="nightmare"):
        # Trust is fixed at construction in production; tests explicitly model
        # that composition with pixels that never leave a TemporaryDirectory.
        self.runtime._evidence_kind = "live-client-capture"
        self.capture.generation = 1
        self.capture.is_current = lambda context, generation: generation == self.capture.generation
        self.engine.pack.mode = mode
        self.engine.pack.pack_id = "isolated-fixture"
        self.engine.pack.maps[0].name = "Fixture"
        self.runtime._mode = mode
        self.runtime._factories[mode] = lambda: self.engine
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = NightmareMemoryStore(self.engine.pack, Path(self.temp.name),
            portable_seed_path=Path(self.temp.name) / "absent-seeds.json")
        self.engine.memory_store = self.store
        self.engine.reload_memory = Mock()

    def selected(self):
        self.unconfirmed_candidates()
        self.dispatch("select_candidate", index=1)

    def test_both_modes_preserve_original_record_and_bounds_once(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                self.live_fixture(mode)
                self.selected()
                result = self.dispatch("confirm_route")
                self.assertEqual(result["learning"]["status"], "saved", result)
                rows = self.store.records()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["mapId"], "fixture-map")
                self.assertEqual(rows[0]["floor"], "1")
                self.assertEqual(rows[0]["clientBounds"], [100, 200, 1380, 920])
                self.assertTrue(rows[0]["samplePath"])
                self.assertTrue((Path(self.temp.name) / rows[0]["samplePath"]).is_file())
                self.assertFalse(self.dispatch("confirm_route")["success"])
                self.dispatch("capture_now")
                self.assertEqual(len(self.store.records()), 1)
                self.engine.reload_memory.assert_called_once()
                self.dispatch("end_round")
                # Restore the fixture list before the next independent round.
                self.engine.pack.maps = self.engine.pack.maps[:1]
                self.engine.pack.by_id = {x.id:x for x in self.engine.pack.maps}

    def test_pose_failure_still_learns_human_identity(self):
        self.live_fixture(); self.selected()
        self.fit.update(success=False, reason="fixture-pose-rejected")
        result = self.dispatch("confirm_route")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "fixture-pose-rejected")
        self.assertEqual(result["learning"]["status"], "saved")
        self.assertEqual(len(self.store.records()), 1)
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_pose_exception_still_learns_without_masking_pose_error(self):
        self.live_fixture(); self.selected()
        with patch.object(_AlignmentHost, "fit", side_effect=ValueError("fixture-pose-error")):
            result = self.dispatch("confirm_route")
        self.assertFalse(result["success"])
        self.assertIn("fixture-pose-error", result["reason"])
        self.assertEqual(len(self.store.records()), 1)

    def test_automatic_confirm_never_learns(self):
        self.live_fixture(); self.begin_and_capture()
        self.assertEqual(self.store.records(), [])
        self.engine.reload_memory.assert_not_called()

    def test_non_live_and_payload_cannot_enable_learning(self):
        self.live_fixture()
        for kind in ("synthetic", "recorded", "unverified"):
            with self.subTest(kind=kind):
                self.runtime._evidence_kind = kind
                self.selected()
                result = self.dispatch("confirm_route", evidenceKind="live-client-capture", recordMemory=True)
                self.assertEqual(result["learning"]["reason"], "non-live-evidence")
                self.assertEqual(self.store.records(), [])
                self.dispatch("end_round")
                self.engine.pack.maps = self.engine.pack.maps[:1]
                self.engine.pack.by_id = {x.id:x for x in self.engine.pack.maps}

    def test_late_work_after_invalidation_never_learns(self):
        self.live_fixture()
        for reason in ("map-closed", "floor-changed", "candidate-selected", "resources-changed", "end_round"):
            with self.subTest(reason=reason):
                self.selected()
                def obsolete(*args):
                    self.runtime.invalidate(reason)
                    return copy.deepcopy(self.fit)
                with patch.object(_AlignmentHost, "fit", side_effect=obsolete):
                    result = self.dispatch("confirm_route")
                self.assertFalse(result["success"])
                self.assertEqual(self.store.records(), [])
                self.dispatch("end_round")
                self.engine.pack.maps = self.engine.pack.maps[:1]
                self.engine.pack.by_id = {x.id:x for x in self.engine.pack.maps}

    def test_cancel_and_capture_rebind_never_learn(self):
        self.live_fixture(); self.selected()
        cancelled = threading.Event()
        def cancel(*args):
            cancelled.set()
            return copy.deepcopy(self.fit)
        with patch.object(_AlignmentHost, "fit", side_effect=cancel):
            result = self.runtime.dispatch("confirm_route", cancelled=cancelled)
        self.assertFalse(result["success"])
        self.assertEqual(self.store.records(), [])
        self.dispatch("reselect_map")
        def rebound(*args):
            self.capture.generation += 1
            return copy.deepcopy(self.fit)
        with patch.object(_AlignmentHost, "fit", side_effect=rebound):
            result = self.dispatch("confirm_route")
        self.assertFalse(result["success"])
        self.assertEqual(self.store.records(), [])

    def test_worker_finishing_after_end_round_cannot_append(self):
        self.live_fixture(); self.selected()
        entered, release = threading.Event(), threading.Event()
        results = []
        def blocked(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture-worker-timeout")
            return copy.deepcopy(self.fit)
        with patch.object(_AlignmentHost, "fit", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(self.dispatch("confirm_route")))
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.dispatch("end_round")
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]["success"])
        self.assertEqual(self.store.records(), [])
        self.engine.reload_memory.assert_not_called()

    def test_accepted_write_drains_without_blocking_snapshot_or_end_round(self):
        self.live_fixture(); self.selected()
        entered, release, owner_done = threading.Event(), threading.Event(), threading.Event()
        results, owner_results = [], []
        original = self.store.record_confirmation
        def blocked_store(*args):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("fixture-store-timeout")
            return original(*args)
        def owner_actions():
            try:
                owner_results.append(self.runtime.snapshot())
                owner_results.append(self.dispatch("end_round"))
            finally:
                owner_done.set()
        with patch.object(self.store, "record_confirmation", side_effect=blocked_store):
            worker = threading.Thread(target=lambda: results.append(self.dispatch("confirm_route")))
            worker.start()
            owner = None
            try:
                self.assertTrue(entered.wait(3))
                owner = threading.Thread(target=owner_actions)
                owner.start()
                # The accepted write is still held at the actual store boundary.
                self.assertTrue(owner_done.wait(1), "store IO held the hot state lock")
                self.assertTrue(owner_results[1]["success"])
                self.assertFalse(self.runtime.snapshot()["roundActive"])
                self.assertEqual(self.store.records(), [])
            finally:
                release.set()
                worker.join(3)
                if owner is not None:
                    owner.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["learning"]["status"], "saved")
        self.assertEqual(results[0]["learning"]["mapId"], "fixture-map")
        self.assertEqual(len(self.store.records()), 1)
        self.assertEqual(self.store.records()[0]["mapId"], "fixture-map")
        self.engine.reload_memory.assert_called_once()

    def test_missing_fresh_frame_and_floor_never_learn(self):
        self.live_fixture(); self.selected()
        self.runtime._last_observation = None
        result = self.dispatch("confirm_route")
        self.assertTrue(result["posePending"])
        self.assertEqual(result["learning"]["status"], "skipped")
        self.assertEqual(self.store.records(), [])

    def test_pose_failure_and_memory_error_keep_independent_diagnostics(self):
        self.live_fixture(); self.selected()
        self.fit.update(success=False, reason="fixture-pose-rejected")
        with patch.object(self.store, "record_confirmation", side_effect=OSError("fixture-disk-error")):
            result = self.dispatch("confirm_route")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "fixture-pose-rejected")
        self.assertIn("fixture-pose-rejected", result["message"])
        self.assertIn("学习标签保存失败", result["message"])
        self.assertIn("fixture-disk-error", result["learning"]["error"])
        self.assertFalse(result["learning"]["saved"])
        self.assertTrue(self.runtime.snapshot()["identityLocked"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])

    def test_write_and_reload_errors_are_reported_without_automatic_retry(self):
        self.live_fixture(); self.selected()
        with patch.object(self.store, "record_confirmation", side_effect=OSError("fixture-disk-error")):
            result = self.dispatch("confirm_route")
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "current-frame-pose-authorized")
        self.assertIn("学习标签保存失败", result["message"])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
        self.assertFalse(result["learning"]["saved"])
        self.engine.reload_memory.assert_not_called()
        self.dispatch("reselect_map")
        self.engine.reload_memory.side_effect = RuntimeError("fixture-reload-error")
        result = self.dispatch("confirm_route")
        self.assertTrue(result["success"])
        self.assertEqual(result["reason"], "current-frame-pose-authorized")
        self.assertIn("标签已存，但记忆刷新失败", result["message"])
        self.assertTrue(self.runtime.snapshot()["displayAuthorized"])
        self.assertTrue(result["learning"]["saved"])
        self.assertFalse(result["learning"]["reloaded"])
        self.assertEqual(len(self.store.records()), 1)
        self.dispatch("capture_now")
        self.assertEqual(len(self.store.records()), 1)


if __name__ == "__main__":
    unittest.main()
