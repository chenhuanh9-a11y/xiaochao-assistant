"""Pure direct recognition: actual index and fixed-pose held-out geometry.

Synthetic feature coordinates/pixels establish contracts, not game accuracy.
No map/pose answer is passed to the recognizer; generating transforms are only
used by fixture producers and assertions after computation returns.
"""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from entry_evidence_work import FrameSnapshot
from fast_structure_alignment import prepare
from structure_identity_index import DirectoryEntry, DirectedAnchor, MapFloorKey, StructureIdentityIndex
from structure_identity_observation import observe_structure_identity
from structure_identity_recognition import recognize_structure_identity
from structure_identity_resources import footprint_anchors
from test_structure_identity_observation import measured_records, observation, pixel_fixture


def anchors(records):
    return tuple(DirectedAnchor(row["index"], row["index"], row["points"], row["rays"]) for row in records)


def entry(name="one", features=None):
    features = anchors(measured_records()) if features is None else features
    return DirectoryEntry(MapFloorKey("fixture", name, "1"), f"fixture-source/{name}",
                          features, frozenset(row.feature_id for row in features), True, True)


def index(*entries):
    entries = entries or (entry(),)
    return StructureIdentityIndex([row.key for row in entries], entries, directory_complete=True)


class StructureIdentityRecognitionTests(unittest.TestCase):
    def test_unique_a_and_independent_b_yield_fixed_pose_proposal_without_authority(self):
        observed = observation()
        prepared = index()
        result = recognize_structure_identity(prepared, observed,
            source_qualification_issues=("real-source-qualification-pending",))
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertTrue(result.fingerprint.fingerprint_unique)
        self.assertEqual(len(result.pose_proposals), 1)
        check = result.pose_checks[0]
        self.assertTrue(check.held_out.verified)
        self.assertEqual(len({pair.source_center_id for pair in check.held_out.correspondences}), 3)
        np.testing.assert_allclose(check.proposal.matrix_source_to_query, np.eye(3), atol=1e-12)
        self.assertFalse(result.authorizesIdentity)
        self.assertFalse(result.lock)
        self.assertIn("real-source-qualification-pending", result.qualification_gaps)
        self.assertIn("current-frame-global-canonical-residual-required", result.qualification_gaps)
        self.assertEqual(result.frame_key, observed.frame_key)
        self.assertGreater(result.retrieval_ms, 0)
        self.assertGreater(result.held_out_ms, 0)

    def test_bad_b_does_not_refit_a_seed_or_relabel_failure_as_success(self):
        observed = observation()
        bad_b = tuple(replace(row, points=np.asarray(row.points) + (0, 25)) for row in observed.b)
        result = recognize_structure_identity(index(), replace(observed, b=bad_b))
        self.assertTrue(result.fingerprint.fingerprint_unique)
        self.assertEqual(result.status, "heldout-rejected", result.issues)
        self.assertEqual(result.pose_proposals, ())
        self.assertTrue(all(row.held_out.status == "rejected" for row in result.pose_checks))
        np.testing.assert_allclose(result.fingerprint.proposals[0].matrix_source_to_query, np.eye(3), atol=1e-12)

    def test_shared_support_cannot_be_certified_even_if_b_count_is_large(self):
        observed = observation()
        forged = replace(observed, b=(*observed.b, observed.a.anchors[0]))
        result = recognize_structure_identity(index(), forged)
        self.assertTrue(result.fingerprint.fingerprint_unique)
        self.assertEqual(result.status, "invalid-observation-partition")
        self.assertIn("a-b-shared-measurement-support", result.issues)
        self.assertEqual(result.pose_checks, ())

    def test_repeated_maps_remain_ambiguous_and_b_cannot_choose_a_winner(self):
        observed = observation()
        bad_b = tuple(replace(row, points=np.asarray(row.points) + (0, 25)) for row in observed.b)
        with patch("structure_identity_recognition.verify_fixed_b",
                   side_effect=AssertionError("B cannot choose between maps")):
            result = recognize_structure_identity(index(entry("one"), entry("two")), replace(observed, b=bad_b))
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual({row.key.map_id for row in result.fingerprint.proposals}, {"one", "two"})
        self.assertEqual(result.pose_checks, ())

    def test_same_map_multiple_pose_families_survive_b_without_error_ranking(self):
        original = anchors(measured_records())
        shifted = tuple(DirectedAnchor(f"copy-{row.feature_id}", f"copy-{row.center_id}",
            np.asarray(row.points) + (1000, 700), row.rays) for row in original)
        result = recognize_structure_identity(index(entry(features=original + shifted)), observation())
        self.assertEqual(result.status, "pose-proposals", result.issues)
        self.assertEqual(len(result.fingerprint.proposals), 2)
        self.assertEqual(len(result.pose_proposals), 2)
        translations = {tuple(np.round(np.asarray(row.matrix_source_to_query)[:2, 2], 8))
                        for row in result.pose_proposals}
        self.assertEqual(translations, {(0., 0.), (-1000., -700.)})

    def test_b_shortage_does_not_erase_a_unique_map_fingerprint(self):
        observed = observation(measured_records()[:6])
        self.assertTrue(any(issue.startswith("b-") for issue in observed.issues))
        result = recognize_structure_identity(index(), observed)
        self.assertEqual(result.status, "insufficient-heldout")
        self.assertTrue(result.fingerprint.fingerprint_unique)
        self.assertEqual(result.pose_checks, ())

    def test_directory_gap_work_budget_and_cancel_never_produce_authority(self):
        observed = observation()
        missing = replace(entry("unavailable"), anchors=None, eligible_feature_ids=None,
                          eligibility_complete=False, search_allowed=False, unavailable_reason="missing-resource")
        cases = [(index(entry(), missing), {}, "unresolved"),
                 (index(), dict(index_max_work=0), "unresolved"),
                 (index(), dict(b_max_work=0), "unresolved"),
                 (index(), dict(time_budget_ms=0), "unresolved"),
                 (index(), dict(cancelled=lambda: True), "cancelled")]
        for prepared, options, status in cases:
            with self.subTest(options=options):
                result = recognize_structure_identity(prepared, observed, **options)
                self.assertEqual(result.status, status)
                self.assertFalse(result.authorizesIdentity)
                self.assertFalse(result.lock)
                self.assertEqual(result.pose_proposals, ())

    def test_qualification_flags_are_not_accepted_as_proof(self):
        with self.assertRaisesRegex(TypeError, "must-be-strings"):
            recognize_structure_identity(index(), observation(), source_qualification_issues={"allQualified": True})

    def test_real_pixels_and_prepared_structure_use_actual_pure_computation_path(self):
        source, frame = pixel_fixture()
        prepared = index(entry("pixels", footprint_anchors(prepare(source))))
        observed = observe_structure_identity(FrameSnapshot(frame), observation_bounds=(0, 0, 1200, 1000))
        self.assertTrue(observed.geometry_ready, observed.issues)
        # Neither old fullfit nor resource preparation/checking is an algorithm input.
        with patch("fast_structure_alignment.prepare", side_effect=AssertionError("hot-path preparation")), \
             patch("structure_pose_solver.StructurePoseSolver.fit", side_effect=AssertionError("per-map fullfit")):
            result = recognize_structure_identity(prepared, observed, time_budget_ms=2000)
        self.assertEqual(result.status, "pose-proposals", (result.status, result.issues))
        self.assertTrue(result.fingerprint.fingerprint_unique)
        self.assertTrue(result.pose_proposals)
        self.assertIn("fog-frontier-classification-unavailable", result.qualification_gaps)
        self.assertFalse(result.authorizesIdentity)


if __name__ == "__main__":
    unittest.main()
