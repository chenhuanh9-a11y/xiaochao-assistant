"""Centre ablation of the real interval/DFS/fixed-B path, not game accuracy."""
from dataclasses import replace
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import structure_identity_index as identity_index
from structure_identity_index import ConstellationA, DirectedAnchor, StructureIdentityIndex
from structure_identity_observation import observe_structure_identity
from structure_identity_recognition import recognize_structure_identity, verify_fixed_b
from entry_evidence_work import FrameSnapshot
from test_structure_identity_index import entry, independent_corners, polygon_anchors, transform
from test_structure_identity_observation import measured_records, observation, pixel_fixture
from test_structure_identity_recognition import anchors, entry as recognition_entry


KIND = "corner-centre"
CENTRES = ((0, 0), (100, 0), (0, 100), (100, 100), (30, 20), (60, 70))


def centre_index(*entries, **options):
    return StructureIdentityIndex([row.key for row in entries], entries,
                                  directory_complete=True, constraint_kind=KIND, **options)


def centre_query(features):
    return ConstellationA(tuple(features), True, constraint_kind=KIND)


def change_arms(features, factors=(4., 7.)):
    result = []
    for anchor in features:
        points = np.asarray(anchor.points).copy()
        points[[0, 2]] = points[1] + (points[[0, 2]] - points[1]) * np.asarray(factors)[:, None]
        result.append(replace(anchor, points=points,
                              rays=np.asarray(anchor.rays) * np.sign(factors)[:, None]))
    return tuple(result)


class CornerCentreIndexTests(unittest.TestCase):
    def test_real_interval_path_keeps_centres_when_arm_lengths_and_ratios_differ(self):
        source = independent_corners(CENTRES)
        changed = change_arms(source)
        full = StructureIdentityIndex([entry("one").key], [entry("one", source)], directory_complete=True)
        self.assertEqual(full.retrieve(ConstellationA(changed, True), epsilon=.1).proposals, ())
        result = centre_index(entry("one", source)).retrieve(centre_query(changed), epsilon=.1)
        self.assertTrue(result.retrieval_complete, result.unresolved_reasons)
        self.assertTrue(result.fingerprint_unique)
        proposal = result.proposals[0]
        self.assertEqual(proposal.constraint_kind, KIND)
        self.assertEqual(len(proposal.pose_region.source_points), 6)
        self.assertEqual(len(proposal.correspondences[0].source_points), 3)
        np.testing.assert_allclose(proposal.matrix_source_to_query, np.eye(3), atol=1e-12)
        self.assertFalse(result.authorizesIdentity)
        self.assertFalse(result.lock)

    def test_first_centre_has_natural_unbounded_partial_interval(self):
        empty = np.empty((0, 2))
        interval = identity_index._extend_interval((0., math.inf, .1), empty, empty,
            np.asarray(((3., 5.),)), np.asarray(((20., 40.),)))
        self.assertEqual(interval, (0., math.inf, .1))
        tightened = identity_index._extend_interval(interval, np.asarray(((3., 5.),)),
            np.asarray(((20., 40.),)), np.asarray(((13., 15.),)), np.asarray(((40., 60.),)))
        self.assertLessEqual(tightened[0], 2.)
        self.assertGreaterEqual(tightened[1], 2.)
        self.assertTrue(math.isfinite(tightened[1]))

    def test_unbounded_interval_does_not_hide_nan_or_arithmetic_overflow(self):
        empty = np.empty((0, 2))
        point = np.asarray(((3., 5.),))
        for interval in ((0., math.inf, math.nan), (0., math.inf, 1e308),
                         (0., math.nan, .1)):
            with self.subTest(interval=interval), self.assertRaises(identity_index._Unfinished):
                identity_index._extend_interval(interval, empty, empty, point, point)
        with self.assertRaises(identity_index._Unfinished):
            identity_index._extend_interval((0., math.inf, .1), np.asarray(((-1e308, 0.),)),
                point, np.asarray(((1e308, 0.),)), point)

    def test_both_arm_orders_survive_but_reversed_rays_do_not(self):
        source = independent_corners(CENTRES)
        reversed_order = tuple(replace(row, points=tuple(reversed(row.points)),
            rays=tuple(reversed(row.rays))) for row in source)
        result = centre_index(entry("one", reversed_order)).retrieve(centre_query(source), epsilon=.1)
        self.assertTrue(result.retrieval_complete)
        self.assertTrue(result.proposals)
        self.assertTrue(all(pair.source_arm_order == (2, 1, 0)
                            for proposal in result.proposals for pair in proposal.correspondences))
        reversed_rays = change_arms(source, (-1., -1.))
        rejected = centre_index(entry("one", source)).retrieve(centre_query(reversed_rays), epsilon=.1)
        self.assertTrue(rejected.retrieval_complete)
        self.assertEqual(rejected.proposals, ())

    def test_endpoint_connections_do_not_prune_the_centre_ablation(self):
        source = change_arms(polygon_anchors(), (.5, .5))
        with patch.object(identity_index, "_positive_connections",
                          side_effect=AssertionError("endpoint connection consumed by centre constraint")):
            result = centre_index(entry("one", source)).retrieve(centre_query(polygon_anchors()), epsilon=.1)
        self.assertTrue(result.retrieval_complete, result.unresolved_reasons)
        self.assertTrue(result.proposals)

    def test_unused_far_arms_do_not_overflow_or_inflate_centre_slack(self):
        ordinary = independent_corners(CENTRES)
        huge = tuple(DirectedAnchor(row.feature_id, row.center_id,
            ((-1e308, row.points[1][1]), row.points[1],
             (row.points[1][0], row.points[1][1] + 1e-308 if row.points[1][1] == 0 else row.points[1][1] + 1.)),
            ((-1., 0.), (0., 1.))) for row in ordinary)
        result = centre_index(entry("ordinary", ordinary), entry("huge", huge)).retrieve(
            centre_query(huge), epsilon=.1, max_work=None)
        reference = centre_index(entry("ordinary", ordinary)).retrieve(centre_query(ordinary), epsilon=.1)
        self.assertTrue(result.retrieval_complete, (result.directory_gaps, result.unresolved_reasons))
        self.assertEqual({row.key.map_id for row in result.proposals}, {"ordinary", "huge"})
        self.assertFalse(result.fingerprint_unique)
        self.assertEqual(result.proposals[0].pose_region.roundoff_slack,
                         reference.proposals[0].pose_region.roundoff_slack)

    def test_budget_cancel_gap_and_legacy_binding_cannot_become_unique(self):
        source = independent_corners(CENTRES)
        prepared = centre_index(entry("one", source))
        for options in (dict(max_work=0), dict(time_budget_ms=0), dict(cancelled=lambda: True)):
            with self.subTest(options=options):
                result = prepared.retrieve(centre_query(source), epsilon=.1, **options)
                self.assertFalse(result.retrieval_complete)
                self.assertFalse(result.fingerprint_unique)
        for selected in (ConstellationA(source, True), SimpleNamespace(anchors=source, source_independent=True)):
            with self.subTest(selected=type(selected)):
                result = prepared.retrieve(selected, epsilon=.1)
                self.assertFalse(result.fingerprint_unique)
                self.assertIn("constraint-kind-mismatch", result.unresolved_reasons)
        missing = entry("missing", None, eligibility_complete=False)
        result = centre_index(entry("one", source), missing).retrieve(centre_query(source), epsilon=.1)
        self.assertTrue(result.proposals)
        self.assertFalse(result.fingerprint_unique)

    def test_six_distributed_physical_centres_are_still_required(self):
        prepared = centre_index(entry("one", independent_corners(CENTRES)))
        for features in (independent_corners(CENTRES[:5]), independent_corners([(i * 20, 0) for i in range(6)])):
            result = prepared.retrieve(centre_query(features), epsilon=.1)
            self.assertFalse(result.retrieval_complete)
            self.assertFalse(result.fingerprint_unique)
            self.assertEqual(result.proposals, ())

    def test_numerically_unresolved_centre_pose_stays_a_competitor(self):
        source = independent_corners(CENTRES)
        result = centre_index(entry("ordinary", source), entry("large", transform(source, offset=(1e15, 1e15)))).retrieve(
            centre_query(transform(source, 1.37)), epsilon=0, max_work=None)
        self.assertFalse(result.retrieval_complete)
        self.assertFalse(result.fingerprint_unique)
        self.assertEqual({row.map_id for row in result.compatible_map_floors}, {"ordinary", "large"})
        self.assertTrue(result.unresolved_proposals)
        self.assertTrue(all(row.constraint_kind == KIND for row in result.unresolved_proposals))


class CornerCentreObservationAndBTests(unittest.TestCase):
    def test_full_chain_b_retains_endpoint_epsilon_without_a_fixed_ray_cutoff(self):
        records = measured_records()
        for row in records[6:]:
            row["points"] = row["points"][1] + np.asarray(((-2., 0.), (0., 0.), (0., 2.)))
        source = recognition_entry(features=anchors(records))
        records[6]["points"][0, 1] += .9
        delta = records[6]["points"][[0, 2]] - records[6]["points"][1]
        records[6]["rays"] = delta / np.hypot(delta[:, 0], delta[:, 1])[:, None]
        self.assertLess(float(np.dot(records[6]["rays"][0], source.anchors[6].rays[0])), .95)
        prepared = StructureIdentityIndex((source.key,), (source,), directory_complete=True)
        result = recognize_structure_identity(prepared, observation(records))
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertTrue(result.pose_checks[0].held_out.verified)
        self.assertAlmostEqual(result.pose_checks[0].held_out.maximum_residual, .9, places=12)

    def test_observation_binding_is_derived_from_a_and_support_is_still_complete(self):
        measured = observation(constraint_kind=KIND)
        self.assertEqual(measured.constraint_kind, KIND)
        self.assertEqual(measured.a.constraint_kind, KIND)
        self.assertEqual(len(measured.a.anchors[0].points), 3)
        changed = measured_records(((90, 40), (240, 40), (140, 140), (340, 140),
                                    (40, 240), (240, 240), (140, 40), (40, 140), (340, 240), (101, 60)))
        checked = observation(changed, constraint_kind=KIND)
        self.assertIn((9, "shared-measurement-support-halos"), checked.rejected_b)
        _, frame = pixel_fixture()
        actual = observe_structure_identity(FrameSnapshot(frame), constraint_kind=KIND)
        self.assertEqual(actual.constraint_kind, KIND)
        self.assertEqual(actual.a.constraint_kind, KIND)

    def test_fixed_b_uses_centres_and_keeps_complete_source_support(self):
        observed = observation(constraint_kind=KIND)
        source = change_arms(anchors(measured_records()), (2., 2.))
        result = recognize_structure_identity(centre_index(recognition_entry(features=source)), observed)
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertTrue(result.pose_checks[0].held_out.verified)
        self.assertTrue(all(len(pair.source_points) == 3 for pair in result.pose_checks[0].held_out.correspondences))
        # A long source B arm crossing an A vertex is unavailable to independent B.
        original = list(anchors(measured_records()))
        original[6] = replace(original[6], points=((40., 40.), original[6].points[1], original[6].points[2]))
        rejected = recognize_structure_identity(centre_index(recognition_entry(features=tuple(original))), observed)
        self.assertEqual(rejected.status, "heldout-rejected", rejected.issues)

    def test_unused_far_a_endpoint_keeps_independent_b_without_float_overflow(self):
        source = list(anchors(measured_records()))
        source[0] = replace(source[0], points=((-1e308, 40.), source[0].points[1], source[0].points[2]))
        # This ray ends at x=40; all B supports at y=40 lie strictly to its
        # right. Their complete arms are disjoint despite huge cross products.
        result = recognize_structure_identity(centre_index(recognition_entry(features=tuple(source))),
                                              observation(constraint_kind=KIND))
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertEqual(result.pose_checks[0].held_out.roundoff_slack,
                         result.pose_checks[0].proposal.pose_region.roundoff_slack)

    def test_b_reversed_directions_fail_at_the_unchanged_a_seed(self):
        observed = observation(constraint_kind=KIND)
        bad_b = tuple(DirectedAnchor(row.feature_id, row.center_id,
            2 * np.asarray(row.points[1]) - row.points, -np.asarray(row.rays)) for row in observed.b)
        result = recognize_structure_identity(centre_index(recognition_entry()), replace(observed, b=bad_b))
        self.assertEqual(result.status, "heldout-rejected", result.issues)
        np.testing.assert_allclose(result.fingerprint.proposals[0].matrix_source_to_query, np.eye(3), atol=1e-12)

    def test_b_endpoint_connection_differences_do_not_constrain_centres(self):
        observed = observation(constraint_kind=KIND)
        source = list(anchors(measured_records()))
        b = list(observed.b)
        b[0] = DirectedAnchor(b[0].feature_id, b[0].center_id,
            (b[0].points[0], b[0].points[1], b[1].points[1]), ((-1., 0.), (-1., 1.)))
        source[6] = DirectedAnchor(source[6].feature_id, source[6].center_id,
            (source[6].points[0], source[6].points[1], (90., 90.)), ((-1., 0.), (-1., 1.)))
        with patch("structure_identity_recognition._connections", side_effect=AssertionError("B endpoint connection")):
            result = recognize_structure_identity(centre_index(recognition_entry(features=tuple(source))),
                                                 replace(observed, b=tuple(b)))
        self.assertEqual(result.status, "pose-proposals", result.issues)

    def test_b_failure_does_not_refit_and_b_cannot_choose_between_a_maps(self):
        observed = observation(constraint_kind=KIND)
        bad = replace(observed, b=tuple(replace(row, points=np.asarray(row.points) + (0, 25)) for row in observed.b))
        result = recognize_structure_identity(centre_index(recognition_entry()), bad)
        self.assertEqual(result.status, "heldout-rejected", result.issues)
        np.testing.assert_allclose(result.fingerprint.proposals[0].matrix_source_to_query, np.eye(3), atol=1e-12)
        with patch("structure_identity_recognition.verify_fixed_b", side_effect=AssertionError("B selected A")):
            ambiguous = recognize_structure_identity(centre_index(recognition_entry("one"), recognition_entry("two")), bad)
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertEqual(ambiguous.pose_checks, ())

    def test_fixed_b_keeps_a_ray_threshold_and_constraint_binding(self):
        observed = observation(constraint_kind=KIND)
        source = recognition_entry()
        proposal = centre_index(source).retrieve(observed.a, epsilon=observed.epsilon,
                                                 minimum_ray_dot=.999).proposals[0]
        angle = .1
        rotation = np.asarray(((math.cos(angle), -math.sin(angle)), (math.sin(angle), math.cos(angle))))
        rotated = tuple(DirectedAnchor(row.feature_id, row.center_id,
            (np.asarray(row.points) - row.points[1]) @ rotation.T + row.points[1],
            np.asarray(row.rays) @ rotation.T) for row in observed.b)
        checked = verify_fixed_b(proposal, source, replace(observed, b=rotated))
        self.assertEqual(checked.status, "rejected")
        wrong = replace(observed, a=replace(observed.a, constraint_kind="full-chain"))
        self.assertEqual(verify_fixed_b(proposal, source, wrong).reason, "constraint-kind-mismatch")
        self.assertEqual(recognize_structure_identity(centre_index(source), wrong).status, "unresolved")

    def test_b_budgets_cancel_and_closed_numeric_boundary_remain_unresolved(self):
        observed = observation(constraint_kind=KIND)
        source = recognition_entry()
        proposal = centre_index(source).retrieve(observed.a, epsilon=observed.epsilon).proposals[0]
        for options, status in ((dict(max_work=0), "unresolved"),
                                (dict(cancelled=lambda: True), "cancelled"),
                                (dict(deadline=0.), "unresolved")):
            with self.subTest(options=options):
                self.assertEqual(verify_fixed_b(proposal, source, observed, **options).status, status)
        accepted = verify_fixed_b(proposal, source, observed)
        at_boundary = tuple(replace(row, points=np.asarray(row.points)
            + (0, accepted.epsilon + accepted.roundoff_slack)) for row in observed.b)
        checked = verify_fixed_b(proposal, source, replace(observed, b=at_boundary))
        self.assertEqual(checked.status, "unresolved")
        self.assertEqual(checked.reason, "b-fixed-matrix-boundary-numeric-unresolved")

    def test_all_a_pose_families_survive_and_b_shortage_does_not_choose_one(self):
        original = anchors(measured_records())
        shifted = tuple(DirectedAnchor(f"copy-{row.feature_id}", f"copy-{row.center_id}",
            np.asarray(row.points) + (1000, 700), row.rays) for row in original)
        prepared = centre_index(recognition_entry(features=original + shifted))
        result = recognize_structure_identity(prepared, observation(constraint_kind=KIND))
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertEqual(len(result.fingerprint.proposals), 2)
        self.assertEqual(result.pose_proposals, result.fingerprint.proposals)
        short = recognize_structure_identity(prepared, observation(measured_records()[:6], constraint_kind=KIND))
        self.assertEqual(short.status, "insufficient-heldout")
        self.assertEqual(len(short.fingerprint.proposals), 2)
        self.assertEqual(short.pose_checks, ())


if __name__ == "__main__":
    unittest.main()
