"""Production runtime/host lifecycle with synthetic frames and controlled review.

No game pixels, capture permission or recognition accuracy is implied. The
peer-review test keeps the real host entry; correction replaces only the solver.
"""
import copy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import _AlignmentHost
import test_client_live_runtime as runtime_fixtures
import test_s1_manual_alignment as manual_fixtures
import test_shared_recovery_residual_contract as recovery_fixtures
import single_map_structure as structure


class ProgressiveDisplayContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixtures.LiveRuntimeContracts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.first = dict(copy.deepcopy(self.fixture.fit), markerOccludedWallObservation=True)

    def run_review(self, final=None, *, boundary=None, real_host=False, cancel_event=None):
        entered, release = threading.Event(), threading.Event()
        output, events, before = [], [], []
        self.runtime.set_event_callback(events.append)
        self.assertTrue(self.fixture.dispatch("start_round")["success"])

        def wait_review(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("review was not released")
            if isinstance(final, Exception):
                raise final
            return final

        def controlled_fit(host, *args, **kwargs):
            before.append((host.group, copy.deepcopy(self.runtime._identity_fields())))
            host._offer_usable_fit(self.first)
            return wait_review() or copy.deepcopy(self.first)

        patches = ([patch.object(_AlignmentHost, "fit", self.fixture.original_fit),
                    patch.object(_AlignmentHost, "_fit_observation", return_value=self.first),
                    patch.object(_AlignmentHost, "_marker_recovery_peer_rejection", side_effect=wait_review)]
                   if real_host else [patch.object(_AlignmentHost, "fit", controlled_fit)])
        for item in patches:
            item.start()
        worker = threading.Thread(target=lambda: output.append(self.runtime.dispatch("capture_now", {}, cancel_event)))
        try:
            worker.start()
            self.assertTrue(entered.wait(5), "host never reached the blocked later review")
            self.assertTrue(worker.is_alive())
            pending = self.runtime.render_payload(self.runtime._render["token"])
            self.assertIsNotNone(pending)
            self.assertEqual(pending["reviewStatus"], "pending")
            self.assertFalse(pending["poseFinalVerified"])
            self.assertEqual(self.runtime._pose_result["status"], "provisional")
            self.assertIsNone(self.runtime._group)
            self.assertEqual(self.runtime._core.pose_by_map, {})
            self.assertFalse(self.runtime.acknowledge_render_material(pending["token"])["success"])
            if before:
                self.assertEqual(before[0][1], self.runtime._identity_fields())
            if boundary is not None:
                boundary(pending)
        finally:
            release.set()
            worker.join(5)
            for item in reversed(patches):
                item.stop()
        self.assertFalse(worker.is_alive(), "review worker did not finish")
        self.assertEqual(len(output), 1)
        return pending, output[0], events

    def test_real_host_displays_before_peer_review_without_learning_pose(self):
        pending, result, events = self.run_review(real_host=True)
        self.assertTrue(result["success"], result)
        final = self.runtime.render_payload(result["renderToken"])
        self.assertEqual(final["reviewStatus"], "complete")
        self.assertTrue(final["poseFinalVerified"])
        self.assertIs(final["pixels"], pending["pixels"])
        self.assertNotEqual(final["token"], pending["token"])
        self.assertIsNotNone(self.runtime._group)
        self.assertTrue(self.runtime.acknowledge_render_material(final["token"])["success"])
        ready = [event["reason"] for event in events if event["event"] == "render-ready"]
        self.assertEqual(ready, ["current-frame-review-pending", "current-frame-pose-authorized"])

    def test_review_correction_updates_pose_and_reuses_owned_pixels(self):
        corrected = copy.deepcopy(self.first)
        corrected["matrixSourceToClient"][0, 2] += 17
        pending, result, _ = self.run_review(corrected)
        self.assertTrue(result["success"], result)
        final = self.runtime.render_payload(result["renderToken"])
        self.assertIs(final["pixels"], pending["pixels"])
        self.assertFalse(final["pixels"].flags.writeable)
        self.assertAlmostEqual(final["matrix"][0][2] - pending["matrix"][0][2], 17)
        np.testing.assert_allclose(self.runtime._group.root_pose, corrected["matrixSourceToClient"])

    def test_existing_recovery_solver_displays_point_pose_before_wall_correction(self):
        self.fixture.begin_and_capture()
        prior = self.runtime._group
        first = self.first["matrixSourceToClient"].copy()
        corrected = first.copy()
        corrected[:2, 2] += [7, -3]
        geometry = dict(structureModel="h0", h0=dict(success=True, matrixSourceToClient=first),
                        graph=dict(regionalEdgeVerified=True), acceptedPointCount=4)
        context = dict(query=np.ones(self.fixture.capture.frame.shape[:2], np.uint8), nodes=[])
        pending = []
        def walls(*args, **kwargs):
            row = self.runtime.render_payload(self.runtime._render["token"])
            self.assertEqual(row["reviewStatus"], "pending")
            self.assertIs(self.runtime._group, prior)
            np.testing.assert_allclose(row["matrix"], first)
            pending.append(row)
            return dict(success=True, matrix=corrected, diagnostics=dict(planes=[1, 2, 3, 4]))
        with patch.object(_AlignmentHost, "fit", self.fixture.original_fit), \
             patch("fast_structure_alignment.try_alignment", return_value=None), \
             patch.object(structure, "graph_point_geometry", return_value=geometry), \
             patch.object(structure, "partial_wall_geometry", side_effect=walls), \
             patch.object(structure, "current_geometry", return_value=context), \
             patch.object(structure, "selected_scale_observation", return_value=None), \
             patch("nightmare_candidate.selected_route_current_frame_residual", return_value=dict(verified=True)):
            result = self.fixture.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertEqual(len(pending), 1)
        final = self.runtime.render_payload(result["renderToken"])
        self.assertEqual(final["reviewStatus"], "complete")
        self.assertIs(final["pixels"], pending[0]["pixels"])
        np.testing.assert_allclose(final["matrix"], corrected)
        np.testing.assert_allclose(self.runtime._group.root_pose, corrected)

    def test_invalid_review_withdraws_temporary_pixels(self):
        pending, result, events = self.run_review(dict(success=False, reason="manual-components-inconsistent"),
                                                  real_host=True)
        self.assertFalse(result["success"])
        self.assertIsNone(self.runtime.render_payload(pending["token"]))
        self.assertIsNone(self.runtime._render)
        self.assertIsNone(self.runtime._group)
        self.assertEqual(self.runtime._core.pose_by_map, {})
        self.assertTrue(any(event["event"] == "render-revoked" for event in events))

    def test_review_exception_withdraws_temporary_pixels(self):
        pending, result, _ = self.run_review(ValueError("controlled-review-error"))
        self.assertFalse(result["success"])
        self.assertIsNone(self.runtime.render_payload(pending["token"]))
        self.assertIsNone(self.runtime._render)

    def test_cancellation_during_review_rejects_late_success(self):
        cancel = threading.Event()
        pending, result, _ = self.run_review(cancel_event=cancel, boundary=lambda row: cancel.set())
        self.assertFalse(result["success"])
        self.assertIsNone(self.runtime.render_payload(pending["token"]))

    def test_cancellation_at_final_publication_withdraws_its_own_grant(self):
        cancel = threading.Event()
        self.fixture.dispatch("start_round")
        def event(row):
            if row["event"] == "render-ready" and row["reason"] == "current-frame-pose-authorized":
                cancel.set()
        self.runtime.set_event_callback(event)
        result = self.runtime.dispatch("capture_now", {}, cancel)
        self.assertFalse(result["success"])
        self.assertIsNone(self.runtime._render)
        self.assertFalse(self.runtime.snapshot()["core"]["displayAuthorized"])

    def test_close_and_end_reject_late_success(self):
        for action in ("close_map", "end_round"):
            with self.subTest(action=action):
                pending, result, _ = self.run_review(boundary=lambda row: self.fixture.dispatch(action))
                self.assertFalse(result["success"], result)
                self.assertIsNone(self.runtime.render_payload(pending["token"]))
                self.assertIsNone(self.runtime._render)

    def test_resource_change_and_floor_change_reject_late_success(self):
        for reason in ("resources-changed", "floor-changed", "map-reselection"):
            with self.subTest(reason=reason):
                pending, result, _ = self.run_review(boundary=lambda row: self.runtime.invalidate(reason))
                self.assertFalse(result["success"], result)
                self.assertIsNone(self.runtime.render_payload(pending["token"]))
                self.assertIsNone(self.runtime._render)

    def test_same_path_resource_replacement_rejects_final_commit(self):
        original = self.runtime._display_layer
        replacement = [False]
        def display(*args, **kwargs):
            layer = original(*args, **kwargs)
            return dict(layer, resourceIdentity="replacement") if replacement[0] else layer
        with patch.object(self.runtime, "_display_layer", side_effect=display):
            pending, result, _ = self.run_review(boundary=lambda row: replacement.__setitem__(0, True))
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "display-resource-changed")
        self.assertIsNone(self.runtime.render_payload(pending["token"]))

    def test_author_change_during_review_expires_old_display_and_keeps_identity(self):
        entered = threading.Event()
        output = []
        original = self.runtime._reproject_route_display
        def reproject(*args, **kwargs):
            entered.set()
            return original(*args, **kwargs)
        author = threading.Thread(target=lambda: output.append(
            self.runtime.dispatch("set_route_profile", dict(profileId="another-author"))))
        def switch(row):
            author.start()
            self.assertTrue(entered.wait(5))
        with patch.object(self.runtime, "_reproject_route_display", side_effect=reproject):
            pending, result, _ = self.run_review(boundary=switch)
            author.join(5)
        self.assertFalse(author.is_alive())
        self.assertFalse(result["success"])
        self.assertTrue(output[0]["success"], output)
        self.assertIsNone(self.runtime.render_payload(pending["token"]))
        state = self.runtime.snapshot()
        self.assertEqual((state["durableMapId"], state["floor"], state["profileId"]),
                         ("fixture-map", "1", "another-author"))
        self.assertTrue(self.fixture.dispatch("capture_now")["success"])

    def test_unverified_proposal_never_reaches_first_display(self):
        self.first["selectedRouteCurrentFrameResidual"] = dict(verified=False)
        offered = []
        host = _AlignmentHost(self.fixture.engine, usable_fit_callback=offered.append)
        host._offer_usable_fit(self.first)
        self.assertEqual(offered, [])

    def test_point_proof_does_not_bypass_mandatory_walls_or_canonical_failure(self):
        for explicit_walls, raw_verified in ((True, True), (False, False)):
            with self.subTest(explicit_walls=explicit_walls, raw_verified=raw_verified):
                producer = recovery_fixtures.SharedRecoveryContractTests()
                producer.setUp()
                if explicit_walls:
                    producer.reference.known_boundary = producer.route
                    producer.reference.unknown_mask = np.zeros_like(producer.route)
                offers = []
                producer.h.usable_fit_callback = offers.append
                result = producer.solve(raw_verified=raw_verified, partial_success=False)
                self.assertFalse(result["success"])
                self.assertEqual(offers, [])

    def test_shared_submission_preserves_four_context_floor_and_live_eligibility(self):
        for season in ("s0", "s1"):
            for mode in ("nightmare", "difficult"):
                with self.subTest(season=season, mode=mode):
                    producer = (runtime_fixtures.LiveRuntimeContracts() if season == "s0"
                                else manual_fixtures.ManualAlignmentContracts())
                    producer.setUp()
                    self.addCleanup(producer.doCleanups)
                    runtime = producer.runtime
                    engine = (runtime_fixtures.fixture_engine(mode) if season == "s0"
                              else producer.registry.engine)
                    engine.pack.mode = mode
                    floor = "1" if season == "s0" else "-1"
                    source = engine.qt_shared_provider("shared-structure", "fixture-map", floor)
                    metadata = copy.deepcopy(source.metadata)
                    metadata["binding"].update(season=season, mode=mode, floor=floor)
                    metadata["structureVersionKey"] = f"synthetic-{season}-{mode}"
                    reference = replace(source, metadata=metadata)
                    requests = []
                    def shared(profile, mid, requested_floor):
                        self.assertEqual((profile, mid, str(requested_floor)),
                                         ("shared-structure", "fixture-map", floor))
                        requests.append((season, mode, str(requested_floor)))
                        return reference
                    engine.qt_shared_provider = shared
                    engine.qt_adapters.canonical_pack_id = f"synthetic-{season}-{mode}"
                    if season == "s0":
                        runtime._factories[mode] = lambda: engine
                    runtime.dispatch("set_context", dict(season=season, mode=mode))
                    fit = copy.deepcopy(self.first)
                    fit["selectedRouteCurrentFrameResidual"]["coordinatePackId"] = engine.qt_adapters.canonical_pack_id
                    fit["observationComponent"] = dict(bounds=[0, 0, 1280, 720], center=[640, 360],
                                                       width=1280, height=720, area=1280*720)
                    pending = []
                    def controlled(host, *args, **kwargs):
                        host._offer_usable_fit(fit)
                        pending.append(runtime.render_payload(runtime._render["token"]))
                        return fit
                    with patch.object(_AlignmentHost, "fit", controlled):
                        if season == "s0":
                            runtime.dispatch("start_round")
                            result = runtime.dispatch("capture_now")
                        else:
                            self.assertFalse(runtime.dispatch("start_round")["success"])
                            producer.prepare()
                            result = runtime.dispatch("confirm_route", dict(mapId="fixture-map"))
                    self.assertTrue(result["success"], result)
                    self.assertEqual(len(pending), 1)
                    self.assertTrue(requests)
                    self.assertEqual(set(requests), {(season, mode, floor)})
                    self.assertEqual((pending[0]["season"], pending[0]["mode"], pending[0]["floor"]),
                                     (season, mode, floor))
                    self.assertEqual(pending[0]["reviewStatus"], "pending")
                    self.assertFalse(pending[0]["liveAuthorization"])
                    final = runtime.render_payload(result["renderToken"])
                    self.assertEqual(final["reviewStatus"], "complete")
                    self.assertFalse(final["liveAuthorization"])
                    if season == "s1":
                        self.assertFalse(runtime.snapshot()["liveEngineAvailable"])
                        self.assertFalse(runtime.dispatch("capture_now")["success"])


if __name__ == "__main__":
    unittest.main()
