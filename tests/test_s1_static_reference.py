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

    def test_reopen_keeps_static_pixels_through_intent_capture_and_fit(self):
        self.wire(); self.show(); self.idle()
        self.assertFalse(self.runtime._s1_private_pool.locked_id)
        retained = self.assert_reference()
        points = []
        original_capture = self.capture.capture
        original_display = self.runtime._display_layer.side_effect
        def observe_point(name):
            row = self.runtime.reference_payload()
            points.append((name, None if row is None else row["token"]))
        def capture(*args, **kwargs):
            observe_point("capture")
            return original_capture(*args, **kwargs)
        def display(*args, **kwargs):
            observe_point("display-preparation")
            return original_display(*args, **kwargs) if original_display else self.display
        self.runtime.set_event_callback(lambda event: observe_point(event["reason"])
            if event["event"] == "render-revoked" else None)
        with patch.object(self.capture, "capture", side_effect=capture), \
                patch.object(self.runtime, "_display_layer", side_effect=display):
            for sequence in (1, 3, 5):
                retained = self.assert_reference()
                points.clear()
                self.assertTrue(self.runtime.dispatch("map", dict(openIntent=False, inputSequence=sequence))["success"])
                result = self.runtime.dispatch("map", dict(openIntent=True, inputSequence=sequence + 1))
                self.idle()
                self.assertTrue(result["success"], result)
                self.assertTrue(points)
                self.assertTrue(all(token == retained["token"] for _, token in points), points)
                self.assert_reference()
                self.assertEqual(self.runtime._s1_private_pool.locked_id, "a")

    def test_new_floor_drops_static_pixels_before_replacement_preparation(self):
        self.show(); self.assert_reference()
        self.runtime.dispatch("close_map")
        self.floor = "-2"
        seen = []
        def display(*args, **kwargs):
            seen.append(self.runtime.reference_payload())
            return self.display
        with patch.object(self.runtime, "_display_layer", side_effect=display):
            self.show()
        self.assertEqual(seen, [None])
        self.assert_reference(floor="-2")

    def test_unknown_or_cancelled_scan_keeps_historical_sheet_but_explicit_cancel_clears(self):
        from s1_structure_selection import StructureCandidateScanner
        for cancel_scan in (False, True):
            with self.subTest(cancel_scan=cancel_scan):
                self.show()
                retained = self.assert_reference()
                self.runtime.dispatch("close_map")
                stopped = [False]
                def unknown(*args, **kwargs):
                    stopped[0] = cancel_scan
                    return dict(success=False, reason="fixture-unknown-no-positive-contradiction")
                def scan(frame, **kwargs):
                    return StructureCandidateScanner(self.registry.catalog, unknown).scan(frame, **kwargs)
                with patch.object(self.registry.engine, "qt_s1_structure_scan", side_effect=scan):
                    result = self.runtime.dispatch("capture_now", cancelled=lambda: stopped[0])
                self.assertFalse(result["success"])
                self.assertEqual(self.assert_reference()["token"], retained["token"])
                self.assertIsNone(self.current_payload())
                self.runtime.dispatch("cancel")
                self.assertIsNone(self.runtime.reference_payload())

    def test_active_s1_modes_share_static_reference_lifecycle(self):
        # S1 ordinary is retired; its negative runtime contract is tested in
        # test_s1_mode_retirement, not by granting a synthetic active provider.
        for mode in ("nightmare", "difficult"):
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
                if reason == "game-floor-control-intent" and self.runtime.snapshot()["s1FormalRoundLock"]:
                    retained = self.runtime.reference_payload()
                    self.assertIsNotNone(retained)
                    self.assertFalse(retained["referenceFloorCurrent"])
                else:
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
