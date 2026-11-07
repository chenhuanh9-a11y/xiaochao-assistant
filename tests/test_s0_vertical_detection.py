"""Controlled signed S0 column pixels; old horizontal HUDs must fail closed.

Drawn HUDs are synthetic positive/negative inputs, not real game screenshots.
"""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from floor_control_region import locate_vertical_floor_controls, read_floor_in_region
from test_client_floor_region import column, LEGACY_REGION, ORDERS, REGION
import test_client_floor_region as legacy


class DetectionPixels(unittest.TestCase):
    def test_calibrated_column_participates_in_tinted_map_proof(self):
        from s0_vertical_controls import observe_s0_map
        from test_s0_map_geometry_proof import s0_tinted
        for mode, order in [('difficult', (2, 1)), ('nightmare', (2, 1, -1))]:
            with self.subTest(mode=mode):
                frame = s0_tinted(mode, center_x=1400)
                settings = dict(floorControlMode='region', floorControlRegion=dict(
                    rect=[.69, .25, .055, .27], clientSize=[1920, 1200]))
                observation, read = observe_s0_map(frame, mode, settings=settings)
                self.assertTrue(observation['detected'], observation)
                self.assertEqual(read['selectedFloor'], 1)
                self.assertEqual(read['floorSource'], 'region')
                self.assertTrue(read['controlGeometryVerified'])

    def test_legacy_near_square_tiles_are_rejected_in_auto_and_region(self):
        for pitch in (44, 48, 52, 64):
            for mode, floors in (("difficult", (1, 2)), ("nightmare", (-1, 1, 2))):
                for floor in floors:
                    with self.subTest(pitch=pitch, mode=mode, floor=floor), patch.object(legacy, "TILE", pitch):
                        frame = legacy.hud(floors, floor)
                        self.assertIsNone(locate_vertical_floor_controls(frame, mode)["selectedFloor"])
                        self.assertIsNone(read_floor_in_region(frame, LEGACY_REGION, mode)["selectedFloor"])

    def test_round_hud_with_adjacent_label_is_not_a_floor_column(self):
        for diameter in (44, 64, 84):
            frame = np.full((1080, 1920, 3), 33, np.uint8)
            center = (1620, 360)
            cv2.circle(frame, center, diameter // 2, (110,) * 3, -1)
            left = round(center[0] - diameter * .94)
            cv2.rectangle(frame, (left - 3, 350), (left + 2, 369), (225,) * 3, -1)
            with self.subTest(diameter=diameter):
                self.assertIsNone(locate_vertical_floor_controls(frame, "difficult")["selectedFloor"])
                self.assertIsNone(read_floor_in_region(frame, REGION, "difficult")["selectedFloor"])

    def test_incomplete_nightmare_column_does_not_authorize_round_hud(self):
        frame = column((2, 1, -1), 1)
        frame[465:495, 1600:1640] = 33
        cv2.circle(frame, (1810, 300), 42, (110,) * 3, -1)
        cv2.rectangle(frame, (1728, 290), (1733, 309), (225,) * 3, -1)
        # Nightmare requires every signed label. A separate filled icon cannot
        # supply the missing B1 evidence.
        self.assertIsNone(locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])
        self.assertIsNone(read_floor_in_region(frame, REGION, "nightmare")["selectedFloor"])

    def test_signed_vertical_pixels_read_each_mode_and_floor(self):
        for size in ((1920, 1080), (1600, 900), (2560, 1440)):
            for mode, order in ORDERS:
                for floor in order:
                    with self.subTest(size=size, mode=mode, floor=floor):
                        frame = column(order, floor, size=size)
                        for read in (locate_vertical_floor_controls(frame, mode),
                                     read_floor_in_region(frame, REGION, mode)):
                            self.assertEqual(read["selectedFloor"], floor, read)
                            self.assertEqual(read["controlAxis"], "vertical")
                            self.assertEqual(read["observedOrder"], list(order))
                            self.assertEqual([glyph["negative"] for glyph in read["labelEvidence"]],
                                             [value < 0 for value in order])
                            self.assertFalse(read["controlGeometryVerified"])
                            self.assertNotIn("centers", read)

    def test_mode_conflict_negative_s1_labels_and_missing_glyph_are_rejected(self):
        for mode, frame in (("difficult", column((2, 1, -1), 1)),
                            ("nightmare", column((2, 1), 1)),
                            ("difficult", column((-1, -2), -1)),
                            ("nightmare", column((-1, -2, -3), -1)),
                            ("difficult", column((1, 2), 1)),
                            ("nightmare", column((-1, 1, 2), 1))):
            with self.subTest(mode=mode):
                self.assertIsNone(locate_vertical_floor_controls(frame, mode)["selectedFloor"])
                self.assertIsNone(read_floor_in_region(frame, REGION, mode)["selectedFloor"])
        frame = column((2, 1, -1), 1)
        frame[465:495, 1600:1640] = 33
        self.assertIsNone(locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])

    def test_automatic_search_rejects_two_complete_columns(self):
        for second_floor in (1, 2):
            with self.subTest(second_floor=second_floor):
                frame = column((2, 1, -1), 1, center_x=1500)
                other = column((2, 1, -1), second_floor, center_x=1710)
                frame[:, 1660:1760] = other[:, 1660:1760]
                self.assertIsNone(locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])
                self.assertIsNone(read_floor_in_region(frame, (.75, .25, .18, .27), "nightmare")["selectedFloor"])

    def test_horizontal_pixels_do_not_override_a_valid_column(self):
        frame = column((2, 1, -1), -1)
        frame[:150] = legacy.hud((-1, 1, 2), 2)[:150]
        self.assertEqual(locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"], -1)

    def test_unregistered_mode_and_season_cannot_reuse_s0_pixels(self):
        frame = column((2, 1, -1), 1)
        for mode, season in (("nightmare", "s1"), ("difficult", "s1"),
                             ("nightmare", "s2"), ("unknown", "s0")):
            with self.subTest(mode=mode, season=season):
                self.assertIsNone(locate_vertical_floor_controls(frame, mode, season=season)["selectedFloor"])
                self.assertIsNone(read_floor_in_region(frame, REGION, mode, season=season)["selectedFloor"])


if __name__ == "__main__":
    unittest.main()
