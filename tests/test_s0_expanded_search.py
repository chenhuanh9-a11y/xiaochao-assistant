"""Synthetic S0 search coverage and fail-closed probes, not game acceptance."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import floor_control_region as detector
from client_space_geometry import centered_safe_viewport
from recognition_diagnostics import control_band
from s0_vertical_controls import observe_s0_map
from test_client_floor_region import column, ORDERS


def moved_column(order, floor, *, size=(1920, 1080), x=1200, y=700):
    hud = column(order, floor, center_x=x, top=y)
    viewport = centered_safe_viewport(size)
    frame = np.full((size[1], size[0], 3), 33, np.uint8)
    frame[viewport.top:viewport.bottom, viewport.left:viewport.right] = cv2.resize(
        hud, (viewport.width, viewport.height), interpolation=cv2.INTER_NEAREST)
    return frame


def automatic(frame, mode, **settings):
    current = detector.locate_vertical_floor_controls(frame, mode)
    return detector.apply_floor_region(settings, mode, frame, current)


class ExpandedS0Search(unittest.TestCase):
    def test_moved_controls_all_floors_modes_and_client_shapes(self):
        for size in ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440),
                     (1920, 1200), (1280, 960), (3440, 1440)):
            for x, y in ((1200, 700), (1840, 100), (1840, 820)):
                for mode, order in ORDERS:
                    for floor in order:
                        with self.subTest(size=size, x=x, y=y, mode=mode, floor=floor):
                            frame = moved_column(order, floor, size=size, x=x, y=y)
                            self.assertIsNone(detector.locate_vertical_floor_controls(frame, mode)["selectedFloor"])
                            read = automatic(frame, mode)
                            self.assertEqual(read["selectedFloor"], floor, read)
                            self.assertEqual(read["observedOrder"], list(order))
                            self.assertEqual(len(read["labelEvidence"]), len(order))
                            self.assertFalse(read["controlGeometryVerified"])

    def test_default_success_does_not_pay_for_expanded_search(self):
        frame = column((2, 1, -1), 1)
        current = detector.locate_vertical_floor_controls(frame, "nightmare")
        with patch.object(detector, "locate_vertical_floor_controls", side_effect=AssertionError("unneeded search")):
            self.assertEqual(detector.apply_floor_region({}, "nightmare", frame, current)["selectedFloor"], 1)

    def test_fixed_and_saved_region_semantics_are_unchanged(self):
        frame = moved_column((2, 1, -1), -1)
        fixed = automatic(frame, "nightmare", floorControlMode="fixed")
        self.assertIsNone(fixed["selectedFloor"])
        saved = dict(rect=[.60, .60, .06, .20], clientSize=[1920, 1080])
        read = automatic(frame, "nightmare", floorControlMode="region", floorControlRegion=saved)
        self.assertEqual((read["selectedFloor"], read["floorSource"]), (-1, "region"))
        for region in (None, dict(rect=[.80, .20, .10, .30], clientSize=[1920, 1080]),
                       dict(saved, clientSize=[1280, 960])):
            read = automatic(frame, "nightmare", floorControlMode="region", floorControlRegion=region)
            self.assertEqual((read["selectedFloor"], read["floorSource"]), (-1, "auto-located"))

    def test_wrong_counts_signs_order_partial_and_two_highlights_fail_closed(self):
        for mode, order in (("difficult", (2, 1, -1)), ("nightmare", (2, 1)),
                            ("difficult", (-1, -2)), ("nightmare", (-1, -2, -3, -4)),
                            ("difficult", (1, 2)), ("nightmare", (-1, 1, 2))):
            with self.subTest(mode=mode, order=order):
                self.assertIsNone(automatic(moved_column(order, order[0]), mode)["selectedFloor"])
        for mutation in ("missing-row", "missing-sign", "two-highlights"):
            frame = moved_column((2, 1, -1), 1)
            if mutation == "missing-row":
                frame[800:840, 1180:1220] = 33
            elif mutation == "missing-sign":
                frame[817:823, 1185:1195] = 33
            else:
                other = moved_column((2, 1, -1), 2)
                frame[668:732, 1170:1230] = other[668:732, 1170:1230]
            with self.subTest(mutation=mutation):
                self.assertIsNone(automatic(frame, "nightmare")["selectedFloor"])

    def test_two_complete_new_domain_columns_are_ambiguous(self):
        for floor in (1, 2):
            frame = moved_column((2, 1, -1), 1)
            other = moved_column((2, 1, -1), floor, x=1400)
            frame[:, 1360:1440] = other[:, 1360:1440]
            self.assertIsNone(automatic(frame, "nightmare")["selectedFloor"])

    def test_noise_light_ui_text_and_outside_domain_do_not_supply_floor(self):
        frames = [np.full((1080, 1920, 3), value, np.uint8) for value in (0, 33, 80, 150, 230)]
        frames.append(np.random.default_rng(41).integers(0, 256, (720, 1280, 3), dtype=np.uint8))
        text = frames[1].copy()
        for index, label in enumerate(("SCORE 2", "TIME 1", "-1  -2", "1234")):
            cv2.putText(text, label, (1150, 650 + index * 60), cv2.FONT_HERSHEY_SIMPLEX,
                        .7, (225,) * 3, 2)
        frames.append(text)
        frames.extend(moved_column((2, 1, -1), 1, x=x, y=y)
                      for x, y in ((950, 700), (1200, 970)))
        for frame in frames:
            for mode, _ in ORDERS:
                self.assertIsNone(automatic(frame, mode)["selectedFloor"])

    def test_column_without_map_evidence_cannot_grant_click_geometry(self):
        for mode, order in ORDERS:
            frame = moved_column(order, 1)
            self.assertEqual(automatic(frame, mode)["selectedFloor"], 1)
            observation, read = observe_s0_map(frame, mode)
            self.assertFalse(observation["detected"])
            self.assertIsNone(read["selectedFloor"])
            self.assertFalse(read["controlGeometryVerified"])

    def test_expanded_controls_reach_map_and_click_consumers_with_same_frame_proof(self):
        from floor_observation_policy import game_floor_control_hit
        from map_interface_observation import MAP_REGIONS, region_pixels
        for mode, order in ORDERS:
            for floor in order:
                frame = moved_column(order, floor)
                for anchor in ("closeButton", "zoomControl", "bottomControls"):
                    region_pixels(frame, MAP_REGIONS[anchor])[:] = (140,) * 3
                original = frame.copy()
                observation, read = observe_s0_map(frame, mode)
                self.assertTrue(observation["detected"], observation)
                self.assertEqual(read["selectedFloor"], floor)
                self.assertEqual(read["floorSource"], "auto-located")
                self.assertTrue(read["controlGeometryVerified"])
                for value, (cx, cy) in zip(order, read["centerPoints"]):
                    self.assertEqual(game_floor_control_hit((cx * 1920, cy * 1080),
                        (0, 0, 1920, 1080), read), value)
                np.testing.assert_array_equal(frame, original)

    def test_expanded_search_does_not_widen_saved_diagnostic_pixels(self):
        frame = moved_column((2, 1, -1), 1)
        read = automatic(frame, "nightmare")
        self.assertEqual(read["selectedFloor"], 1)
        x, y, w, h = control_band(frame, "nightmare", {}, read)
        self.assertLess(w, frame.shape[1] * .10)
        self.assertLess(h, frame.shape[0] * .30)
        for cx, cy in read["centerPoints"]:
            self.assertLessEqual(x, cx * frame.shape[1])
            self.assertLessEqual(cx * frame.shape[1], x + w)
            self.assertLessEqual(y, cy * frame.shape[0])
            self.assertLessEqual(cy * frame.shape[0], y + h)

    def test_expansion_does_not_cross_season_boundary(self):
        frame = moved_column((2, 1, -1), 1)
        for season in ("s1", "s2"):
            self.assertIsNone(automatic(frame, "nightmare", gameSeason=season)["selectedFloor"])


if __name__ == "__main__":
    unittest.main()
