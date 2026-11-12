"""Production runtime/host lifecycle with synthetic frames and controlled review.

No game pixels, capture permission or recognition accuracy is implied. The
peer-review test keeps the real host entry; correction replaces only the solver.
"""
import copy
from dataclasses import replace
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import _AlignmentHost
import test_client_live_runtime as runtime_fixtures
import test_s1_manual_alignment as manual_fixtures
import test_shared_recovery_residual_contract as recovery_fixtures
import single_map_structure as structure
import round_layer_group_commit as group_commit


class ProgressiveDisplayContracts(unittest.TestCase):
    def setUp(self):
        self.fixture = runtime_fixtures.LiveRuntimeContracts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.first = dict(copy.deepcopy(self.fixture.fit), markerOccludedWallObservation=True)

    def real_reference_provider(self):
        """Use the production guarded cache; only its decoded input is fixture data."""
        from client_providers import SharedReferenceProvider
        reference = self.fixture.engine.qt_shared_provider('shared-structure', 'fixture-map', '1')
        key = ('fixture', 'fixture-map', 'variant-01', '1')
        version = dict(path=reference.path.resolve(), binding=copy.deepcopy(reference.metadata['binding']),
            canvas=copy.deepcopy(reference.metadata['canvas']),
            expected=dict(structureVersionKey=reference.metadata['structureVersionKey']))
        provider = SharedReferenceProvider(reference.path.parent,
            SimpleNamespace(load=lambda *args, **kwargs: reference), {key: version})
        self.fixture.engine.qt_shared_provider = provider
        return provider, key

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

    def test_explicit_shared_material_is_generated_once_for_both_publications(self):
        from shared_structure_display import structure_display_layer
        from client_route_preferences import shared_structure_display_current
        self.real_reference_provider()
        with patch('shared_structure_display.structure_display_layer', wraps=structure_display_layer) as build, \
             patch('client_route_preferences.shared_structure_display_current',
                 wraps=shared_structure_display_current) as validate:
            pending, result, _ = self.run_review()
        self.assertTrue(result['success'], result)
        self.assertEqual(build.call_count, 1, 'each publication must validate without regenerating pixels')
        self.assertEqual(validate.call_count, 2, 'provisional and final publication each validate the source')
        final = self.runtime.render_payload(result['renderToken'])
        self.assertIs(final['pixels'], pending['pixels'])
        self.assertFalse(final['pixels'].flags.writeable)

    def test_shared_publication_rechecks_the_original_resource_guard(self):
        available = [True]
        def current():
            if not available[0]:
                raise ValueError('controlled-display-resource-superseded')
        self.fixture.engine.qt_assert_resource_current = current
        pending, result, _ = self.run_review(boundary=lambda row: available.__setitem__(0, False))
        self.assertFalse(result['success'], result)
        self.assertIn('controlled-display-resource-superseded', result['reason'])
        self.assertIsNone(self.runtime.render_payload(pending['token']))

    def test_author_structural_fallback_keeps_full_policy_rechecks(self):
        self.fixture.dispatch('set_route_profile', profileId='another-author')
        original = self.runtime._display_layer
        with patch.object(self.runtime, '_display_layer', wraps=original) as prepare:
            pending, result, _ = self.run_review()
        self.assertTrue(result['success'], result)
        self.assertEqual(prepare.call_count, 3)
        final = self.runtime.render_payload(result['renderToken'])
        self.assertEqual(final['profileId'], 'another-author')
        self.assertEqual(final['effectiveProfileId'], 'shared-structure')
        self.assertTrue(final['fallback'])

    def test_custom_source_keeps_its_original_recheck_and_rejects_replacement(self):
        identity = ['custom-v1']
        pixels = np.full((20, 24, 4), 255, np.uint8)
        self.fixture.engine.qt_custom_display = lambda *args: dict(pixels=pixels,
            matrixDisplayToReference=np.eye(3), resourceIdentity=identity[0],
            displayKind='custom-gallery', displayLabel='fixture custom', routeProfileId='shared-structure')
        self.fixture.dispatch('set_route_profile', profileId='custom:fixture')
        original = self.runtime._display_layer
        with patch.object(self.runtime, '_display_layer', wraps=original) as prepare:
            pending, result, _ = self.run_review(boundary=lambda row: identity.__setitem__(0, 'custom-v2'))
        self.assertEqual(prepare.call_count, 3)
        self.assertFalse(result['success'], result)
        self.assertEqual(result['reason'], 'display-resource-changed')
        self.assertIsNone(self.runtime.render_payload(pending['token']))

    def test_mutable_reference_binding_cannot_change_the_prepared_identity(self):
        reference = self.fixture.engine.qt_shared_provider('shared-structure', 'fixture-map', '1')
        pending, result, _ = self.run_review(boundary=lambda row:
            reference.metadata['binding'].__setitem__('variant', 'replacement'))
        self.assertFalse(result['success'], result)
        self.assertEqual(result['reason'], 'display-resource-changed')
        self.assertIsNone(self.runtime.render_payload(pending['token']))

    def test_prepared_display_bridge_is_detached_from_mutable_provider_result(self):
        layers = []
        original = self.runtime._display_layer
        def display(*args, **kwargs):
            layer = original(*args, **kwargs)
            layers.append(layer)
            return layer
        def mutate(row):
            layers[0]['matrixDisplayToReference'][0, 2] += 50
        with patch.object(self.runtime, '_display_layer', side_effect=display):
            pending, result, _ = self.run_review(boundary=mutate)
        self.assertTrue(result['success'], result)
        final = self.runtime.render_payload(result['renderToken'])
        np.testing.assert_allclose(final['matrix'], pending['matrix'])

    def test_peer_correction_reuses_pixel_features_but_reprojects_player(self):
        frame = self.fixture.capture.frame
        frame[200:470, 300:650] = 120
        frame[330:336, 470:476] = (0, 220, 220)
        for y in range(250, 290, 5):
            frame[y:y + 2, 500:520] = 190
        corrected = copy.deepcopy(self.first)
        corrected["matrixSourceToClient"][0, 2] += 17
        original_stage = self.runtime._stage_round_group_commit
        tracking = []

        def stage(*args, **kwargs):
            group = original_stage(*args, **kwargs)
            tracking.append(group.tracking_for_floor("1"))
            return group

        with patch.object(group_commit, "map_reuse_evidence", wraps=group_commit.map_reuse_evidence) as reuse, \
             patch.object(group_commit, "live_stair_screen_candidates", wraps=group_commit.live_stair_screen_candidates) as stairs, \
             patch.object(group_commit, "map_anchor_focus_point", wraps=group_commit.map_anchor_focus_point) as marker, \
             patch.object(self.runtime, "_stage_round_group_commit", side_effect=stage):
            _, result, _ = self.run_review(corrected)
            self.assertTrue(result["success"], result)
            self.assertEqual((reuse.call_count, stairs.call_count, marker.call_count), (1, 1, 1))
            self.assertEqual(len(tracking), 2)
            for row, fit in zip(tracking, (self.first, corrected)):
                expected = np.linalg.inv(fit["matrixSourceToClient"]) @ np.array([472.5, 332.5, 1.])
                np.testing.assert_allclose(row["verifiedPlayerAuthorPoint"], expected[:2])
                np.testing.assert_array_equal(row["translationMaskMatrix"], fit["matrixSourceToClient"])
            self.assertGreaterEqual(tracking[1]["verifiedPlayerCommittedMonotonicAt"],
                                    tracking[0]["verifiedPlayerCommittedMonotonicAt"])
            self.assertIsNot(tracking[0]["translationMask"][0], tracking[1]["translationMask"][0])
            np.testing.assert_array_equal(tracking[0]["translationMask"][0], tracking[1]["translationMask"][0])

            # A later solve must use its newly captured pixels even when the
            # map, floor and component rectangle are unchanged.
            self.fixture.dispatch("end_round")
            frame[330:336, 470:476] = 120
            frame[350:356, 490:496] = (0, 220, 220)
            self.fixture.begin_and_capture()
            self.assertEqual((reuse.call_count, stairs.call_count, marker.call_count), (2, 2, 2))
            expected = np.linalg.inv(self.fixture.fit["matrixSourceToClient"]) @ np.array([492.5, 352.5, 1.])
            np.testing.assert_allclose(self.runtime._group.tracking["verifiedPlayerAuthorPoint"], expected[:2])

    def test_same_bounds_changed_component_center_recomputes_marker(self):
        frame = np.full((720, 1280, 3), 120, np.uint8)
        frame[300:306, 300:306] = (0, 220, 220)
        frame[300:306, 600:606] = (0, 220, 220)
        frame.setflags(write=False)
        scope = group_commit.RoundPoseObservationScope(frame)
        component = dict(bounds=(0, 0, 1280, 720), center=(300, 300),
                         width=1280, height=720, area=1280 * 720)
        first = scope.prepare(frame, component_override=component,
                              map_anchor_focus_point=group_commit.map_anchor_focus_point)
        component["center"] = (600, 300)
        second = scope.prepare(frame, component_override=component,
                               map_anchor_focus_point=group_commit.map_anchor_focus_point)
        np.testing.assert_array_equal(first.marker_client_point, [302.5, 302.5])
        np.testing.assert_array_equal(second.marker_client_point, [602.5, 302.5])
        with self.assertRaises(ValueError):
            scope.prepare(frame.copy(), component_override=component,
                          map_anchor_focus_point=group_commit.map_anchor_focus_point)

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
        provider, key = self.real_reference_provider()
        expected = provider.binding_metadata('shared-structure', 'fixture-map', '1')
        def replacement(row):
            # Replace the actual cache result at the same physical path. The
            # versions metadata lookup deliberately still reports the old ID.
            reference = provider._cache[key]
            metadata = copy.deepcopy(reference.metadata)
            metadata['structureVersionKey'] = 'replacement'
            provider._cache[key] = replace(reference, metadata=metadata)
            self.assertEqual(provider.binding_metadata('shared-structure', 'fixture-map', '1'), expected)
        pending, result, _ = self.run_review(boundary=replacement)
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
                    final_fit = copy.deepcopy(fit)
                    if season == "s1":
                        final_fit["observationComponent"].update(bounds=[0, 0, 640, 720],
                                                                 center=[320, 360], width=640, area=640 * 720)
                    def controlled(host, *args, **kwargs):
                        host._offer_usable_fit(fit)
                        pending.append(runtime.render_payload(runtime._render["token"]))
                        return final_fit
                    with patch.object(_AlignmentHost, "fit", controlled), \
                         patch.object(group_commit, "live_stair_screen_candidates",
                                      wraps=group_commit.live_stair_screen_candidates) as stairs:
                        if season == "s0":
                            runtime.dispatch("start_round")
                            result = runtime.dispatch("capture_now")
                        else:
                            self.assertFalse(runtime.dispatch("start_round")["success"])
                            producer.prepare()
                            result = runtime.dispatch("confirm_route", dict(mapId="fixture-map"))
                    self.assertEqual(stairs.call_count, 2 if season == "s1" else 1)
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
                        self.assertEqual(runtime._group.tracking["component"]["bounds"], (0, 0, 640, 720))
                        self.assertFalse(runtime.snapshot()["liveEngineAvailable"])
                        self.assertFalse(runtime.dispatch("capture_now")["success"])


if __name__ == "__main__":
    unittest.main()
