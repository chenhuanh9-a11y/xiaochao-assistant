"""Static-display ownership contracts, not recognition accuracy or game QA.

Capture, solver evidence and pixels are synthetic. The real runtime commits,
invalidations and getters are exercised; Qt presentation is tested separately.
"""
import copy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import test_s1_async_evidence as fixture


class StaticReferenceContracts(unittest.TestCase):
    setUp = fixture.AsyncEvidenceContracts.setUp
    start_patch = fixture.AsyncEvidenceContracts.start_patch
    show = fixture.AsyncEvidenceContracts.show
    current_payload = fixture.AsyncEvidenceContracts.current_payload
    idle = fixture.AsyncEvidenceContracts.idle
    report = fixture.AsyncEvidenceContracts.report
    wire = fixture.AsyncEvidenceContracts.wire

    def assert_reference(self, map_id="a", floor="-1"):
        row = self.runtime.reference_payload()
        self.assertIsNotNone(row)
        self.assertEqual((row["mapId"], row["floor"]), (map_id, floor))
        self.assertTrue(row["s1CandidateReference"])
        self.assertTrue(row["referenceAuthorized"])
        self.assertTrue(row["provisional"])
        self.assertFalse(row["identityLocked"])
        self.assertFalse(row["durableMapId"])
        self.assertFalse(self.runtime._map_id)
        self.assertFalse(self.runtime._gate.confirmed_id)
        self.assertNotIn("matrix", row)
        self.assertNotIn("liveAuthorization", row)
        return row

    def test_before_and_after_private_lock_close_and_new_floor(self):
        self.wire()
        self.show(); self.idle()
        before = self.assert_reference()
        self.assertFalse(self.runtime._s1_private_pool.locked_id)
        self.runtime.dispatch("close_map")
        self.assertIsNone(self.current_payload())
        self.assertEqual(self.assert_reference()["token"], before["token"])
        self.show(); self.idle()
        self.assertEqual(self.runtime._s1_private_pool.locked_id, "a")
        self.assert_reference()
        self.runtime.dispatch("close_map")
        self.floor = "-3"
        self.show(); self.idle()
        self.assert_reference(floor="-3")

    def test_aligned_visibility_does_not_disable_static_reference(self):
        self.wire(); self.show(); self.idle()
        self.runtime.configure_settings(dict(self.runtime._settings, sheetVisibilityEnabled=False))
        self.assert_reference()

    def test_all_s1_modes_share_static_reference_lifecycle(self):
        for mode in ("nightmare", "difficult", "difficulty2"):
            with self.subTest(mode=mode):
                self.runtime.dispatch("end_round")
                self.registry.engine.pack.mode = mode
                self.registry.catalog = replace(self.registry.catalog, mode=mode)
                self.assertTrue(self.runtime.dispatch("set_context", dict(season="s1", mode=mode))["success"])
                with patch("s1_structure_selection.StructureCatalog.from_engine", return_value=self.registry.catalog):
                    self.show()
                    self.assertEqual(self.assert_reference()["mode"], mode)
                    self.runtime.dispatch("close_map")
                    self.assertEqual(self.assert_reference()["mode"], mode)
                    self.runtime.dispatch("end_round")
                    self.assertIsNone(self.runtime.reference_payload())

    def test_switch_candidate_replaces_reference_without_confirming_identity(self):
        self.wire(lambda frame, ids, floor, **kw: dict(self.report(ids, floor, ()), coordinateVerified=False))
        first = self.show(); self.idle()
        self.assert_reference()
        self.assertTrue(self.runtime.dispatch("next_candidate")["success"])
        self.idle()
        self.assert_reference("b")
        self.assertFalse(self.runtime.acknowledge_render_material(first["token"])["success"])

    def test_failed_candidate_switch_clears_previous_static_pixels(self):
        self.show()
        self.assert_reference()
        with patch.object(self.runtime, "_display_layer", side_effect=ValueError("fixture unavailable")):
            self.assertFalse(self.runtime.dispatch("next_candidate")["success"])
        self.assertIsNone(self.runtime.reference_payload())

    def test_unknown_floor_revokes_previous_reference(self):
        self.show(); self.assert_reference()
        self.floor = None
        self.assertFalse(self.runtime.dispatch("observe_candidate_floor")["success"])
        self.assertIsNone(self.runtime.reference_payload())

    def test_hard_boundaries_and_binding_generation_revoke(self):
        # The fixture registry has immutable in-memory resources only.
        self.registry.invalidate = lambda: None
        for reason in ("cancel", "s1-candidate-reselection", "game-floor-control-intent",
                       "resource-changed", "capture-context-changed"):
            with self.subTest(reason=reason):
                self.show(); self.assert_reference()
                self.runtime.invalidate(reason)
                self.assertIsNone(self.runtime.reference_payload())
        self.show(); self.assert_reference()
        self.capture.generation += 1
        self.assertIsNone(self.runtime.reference_payload())

    def test_private_recovery_revokes_closed_static_reference(self):
        self.wire(); self.show(); self.idle()
        self.runtime.dispatch("close_map")
        self.assert_reference()
        pool = self.runtime._s1_private_pool
        with self.runtime._lock:
            pool.restore()
            self.runtime._private_contradiction_recovered(pool, "a", {}, self.registry.catalog)
        self.assertIsNone(self.runtime.reference_payload())

    def test_end_during_pixel_preparation_rejects_late_reference(self):
        entered, release = threading.Event(), threading.Event()
        self.runtime.dispatch("start_round")
        def slow_display(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(4))
            return copy.deepcopy(self.display)
        results = []
        with patch.object(self.runtime, "_display_layer", side_effect=slow_display):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch("capture_now")))
            worker.start()
            try:
                self.assertTrue(entered.wait(4))
                self.runtime.dispatch("end_round")
            finally:
                release.set(); worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]["success"])
        self.assertIsNone(self.runtime.reference_payload())

    def test_closed_author_rebuild_cancelled_by_end_cannot_restore_reference(self):
        self.show(); self.runtime.dispatch("close_map")
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set(); self.assertTrue(release.wait(4))
            return dict(layer=self.display)
        results = []
        with patch("client_route_reprojection.prepare_route_reprojection", side_effect=delayed):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch(
                "set_context", dict(profileId="other-author"))))
            worker.start()
            try:
                self.assertTrue(entered.wait(4))
                self.assertIsNone(self.runtime.reference_payload())  # Old author expires immediately.
                self.runtime.dispatch("end_round")
            finally:
                release.set(); worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results[0].get("stale"), results)
        self.assertIsNone(self.runtime.reference_payload())

    def test_closed_author_failure_cannot_reuse_previous_author_pixels(self):
        self.show(); self.runtime.dispatch("close_map")
        with patch("client_route_reprojection.prepare_route_reprojection", side_effect=ValueError("fixture missing")):
            result = self.runtime.dispatch("set_context", dict(profileId="missing-author"))
        self.assertFalse(result["success"])
        self.assertIsNone(self.runtime.reference_payload())
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.runtime._map_id)
        self.assertFalse(self.runtime._gate.confirmed_id)

    def test_end_and_new_round_never_borrow_previous_static_map(self):
        self.show(); self.assert_reference()
        self.runtime.dispatch("end_round")
        self.assertIsNone(self.runtime.reference_payload())
        self.runtime.dispatch("start_round")
        self.assertIsNone(self.runtime.reference_payload())


if __name__ == "__main__":
    unittest.main()
