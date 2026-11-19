"""Synthetic geometry and real pixel extraction, not game acceptance."""
import copy
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from entry_evidence_work import FrameSnapshot
from structure_identity_observation import (independence_violations,
    observe_structure_identity, partition_observed_chains)


A_CENTRES = ((40, 40), (240, 40), (140, 140), (340, 140), (40, 240), (240, 240))
B_CENTRES = ((140, 40), (40, 140), (340, 240))


def measured_records(centres=None):
    centres = (*A_CENTRES, *B_CENTRES) if centres is None else centres
    return [dict(index=i, points=np.asarray((np.asarray(point) + (-12, 0), point,
                 np.asarray(point) + (0, 12)), dtype=float), rays=np.asarray(((-1., 0.), (0., 1.))))
            for i, point in enumerate(centres)]


def observation(records=None, **kwargs):
    return partition_observed_chains(measured_records() if records is None else records,
        frame_key="fixture-frame", frame_shape=(400, 400), epsilon=1,
        **kwargs)


def pixel_fixture():
    mask = np.zeros((650, 650), np.uint8)
    mask[65:520, 75:510] = 1
    for x, y, w, h in ((113, 102, 53, 71), (225, 100, 77, 49), (362, 110, 61, 87),
                       (130, 245, 66, 58), (276, 228, 51, 111), (388, 290, 73, 67),
                       (190, 390, 89, 55), (352, 408, 57, 73)):
        mask[y:y+h, x:x+w] = 0
    mask[65:133, 440:510] = 0
    mask[430:520, 75:125] = 0
    # The renderer is a fixture producer; no generating pose enters extraction.
    gray = cv2.warpAffine(mask * 90, np.asarray(((1., 0, 210), (0, 1., 130))), (1200, 1000))
    return mask, np.repeat(gray[:, :, None], 3, axis=2)


class StructureIdentityObservationTests(unittest.TestCase):
    def test_fixed_source_independent_pools_make_disjoint_distributed_a_and_b(self):
        result = observation()
        self.assertTrue(result.geometry_ready, result.issues)
        self.assertEqual((len(result.a.anchors), len(result.b)), (6, 3))
        self.assertEqual({row.feature_id for row in result.a.anchors}, set(range(6)))
        self.assertEqual({row.feature_id for row in result.b}, {6, 7, 8})
        self.assertTrue(result.a.source_independent)
        self.assertEqual(independence_violations(result.a.anchors, result.b, result.frame_shape), ())
        self.assertIn("fog-frontier-classification-unavailable", result.qualification_gaps)

    def test_b_geometry_cannot_change_a_selection(self):
        records = measured_records()
        first = observation(records)
        for row in records[6:]:
            row["points"] += (5, 5)  # Remains in the same preassigned B cell.
        second = observation(records)
        self.assertEqual(first.a, second.a)
        self.assertNotEqual(first.b, second.b)

    def test_two_five_pixel_halos_are_checked_over_entire_arms(self):
        centres = ((90, 40), *A_CENTRES[1:], *B_CENTRES, (101, 60))
        result = observation(measured_records(centres))
        self.assertEqual(result.b_pool_count, 4)
        self.assertIn((9, "shared-measurement-support-halos"), result.rejected_b)
        self.assertEqual({row.feature_id for row in result.b}, {6, 7, 8})
        a = next(row for row in result.a.anchors if row.feature_id == 0)
        rejected = measured_records(centres)[9]
        self.assertFalse(set(a.points) & {tuple(point) for point in rejected["points"]})
        # Centres are over 20px apart; their arms' radius-5 supports still overlap.
        self.assertGreater(np.linalg.norm(np.asarray(a.points[1]) - rejected["points"][1]), 20)

    def test_crop_unknown_support_is_excluded_without_repairing_a_from_b(self):
        records = measured_records(((10, 40), *A_CENTRES[1:], *B_CENTRES))
        result = observation(records)
        self.assertIn((0, "measurement-halo-crosses-observation-boundary"), result.excluded_measurements)
        self.assertIn("a-physical-centres-insufficient", result.issues)
        self.assertEqual(len(result.a.anchors), 5)
        self.assertEqual({row.feature_id for row in result.b}, {6, 7, 8})

    def test_input_records_and_frame_are_not_modified(self):
        records = measured_records()
        before = copy.deepcopy(records)
        observation(records)
        for actual, saved in zip(records, before):
            np.testing.assert_array_equal(actual["points"], saved["points"])
            np.testing.assert_array_equal(actual["rays"], saved["rays"])
        _, frame = pixel_fixture()
        snapshot = FrameSnapshot(frame)
        pixels = snapshot.pixels.copy()
        observe_structure_identity(snapshot, observation_bounds=(0, 0, 1200, 1000))
        np.testing.assert_array_equal(snapshot.pixels, pixels)
        self.assertFalse(snapshot.pixels.flags.writeable)

    def test_real_pixel_channel_produces_measured_partition_with_qualification_gaps(self):
        _, frame = pixel_fixture()
        with patch("single_map_structure.marker_occluded_corner_proposals",
                   side_effect=AssertionError("marker proposal is not measured evidence")):
            result = observe_structure_identity(FrameSnapshot(frame), observation_bounds=(0, 0, 1200, 1000))
        self.assertGreaterEqual(result.measured_chain_count, 9)
        self.assertTrue(result.geometry_ready, (result.issues, result.a_pool_count, result.b_pool_count, result.rejected_b))
        self.assertGreater(result.extraction_ms, 0)
        self.assertIn("screen-colour-boundary-versus-static-alpha-footprint-unverified", result.qualification_gaps)

    def test_marker_pixels_do_not_supply_measured_chains(self):
        _, frame = pixel_fixture()
        initial = observe_structure_identity(FrameSnapshot(frame), observation_bounds=(0, 0, 1200, 1000))
        x, y = np.rint(initial.a.anchors[0].points[1]).astype(int)
        frame[y-5:y+6, x-5:x+6] = (0, 0, 255)
        marked = observe_structure_identity(FrameSnapshot(frame), observation_bounds=(0, 0, 1200, 1000))
        for anchor in (*marked.a.anchors, *marked.b):
            self.assertFalse(abs(anchor.points[1][0] - x) <= 5 and abs(anchor.points[1][1] - y) <= 5)

    def test_no_structure_and_cancellation_remain_explicit_failures(self):
        blank = observe_structure_identity(FrameSnapshot(np.zeros((400, 400, 3), np.uint8)))
        self.assertFalse(blank.geometry_ready)
        self.assertEqual(blank.measured_chain_count, 0)
        self.assertIn("current-geometry-unavailable", blank.issues)
        cancelled = observation(cancelled=lambda: True)
        self.assertIn("observation-cancelled", cancelled.issues)
        self.assertFalse(cancelled.geometry_ready)

    def test_caller_cannot_reduce_measurement_operator_halo(self):
        with self.assertRaisesRegex(ValueError, "at-least-five"):
            observation(support_halo=4)


if __name__ == "__main__":
    unittest.main()
