"""Synthetic bright-panel S0 pixels; original user frames are replayed locally only."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import floor_control_region as detector
from client_space_geometry import centered_safe_viewport
from test_client_floor_region import column, ORDERS, REGION


def bright_column(order, floor, *, size=(1920, 1080), brightness=1., highlight=160):
    hud = column(order, floor)
    hud[np.all(hud == 33, axis=2)] = 68
    hud[np.all(hud == 110, axis=2)] = highlight
    hud = cv2.convertScaleAbs(hud, alpha=brightness)
    viewport = centered_safe_viewport(size)
    frame = np.full((size[1], size[0], 3), round(68 * brightness), np.uint8)
    frame[viewport.top:viewport.bottom, viewport.left:viewport.right] = cv2.resize(
        hud, (viewport.width, viewport.height), interpolation=cv2.INTER_NEAREST)
    return frame


class BrightS0Controls(unittest.TestCase):
    def test_connected_bright_backdrop_retains_all_signed_floors(self):
        for size in ((1280, 720), (1920, 1080), (2560, 1440), (1920, 1200)):
            for brightness in (.8, 1., 1.15, 1.25):
                for mode, order in ORDERS:
                    for floor in order:
                        with self.subTest(size=size, brightness=brightness, mode=mode, floor=floor):
                            frame = bright_column(order, floor, size=size, brightness=brightness)
                            read = detector.locate_vertical_floor_controls(frame, mode)
                            self.assertEqual(read["selectedFloor"], floor, read)
                            self.assertEqual(read["observedOrder"], list(order))
                            self.assertEqual(len(read["labelEvidence"]), len(order))
                            self.assertFalse(read["controlGeometryVerified"])

    def test_player_region_uses_same_complete_column_proof_without_pixel_mutation(self):
        for mode, order in ORDERS:
            frame = bright_column(order, 1)
            original = frame.copy()
            read = detector.read_floor_in_region(frame, REGION, mode)
            self.assertEqual(read["selectedFloor"], 1, read)
            np.testing.assert_array_equal(frame, original)
            self.assertEqual(detector.FILL_LEVEL, 62)
            self.assertEqual(detector.GLYPH_LEVEL, 140)
            self.assertEqual(detector.BACKDROP_MAX, 75)

    def test_suggested_complete_region_does_not_measure_highlight_as_background(self):
        from client_floor_region import suggested_rect
        for mode, order in ORDERS:
            for floor in order:
                frame = bright_column(order, floor)
                read = detector.locate_vertical_floor_controls(frame, mode)
                rect = suggested_rect(mode, read, frame.shape[1::-1])
                self.assertEqual(detector.read_floor_in_region(frame, rect, mode)["selectedFloor"], floor)

    def test_wrong_count_sign_order_and_partial_rows_remain_unread(self):
        for mode, order in (("difficult", (2, 1, -1)), ("nightmare", (2, 1)),
                            ("difficult", (-1, -2)), ("nightmare", (-1, -2, -3, -4)),
                            ("difficult", (1, 2)), ("nightmare", (-1, 1, 2))):
            with self.subTest(mode=mode, order=order):
                self.assertIsNone(detector.locate_vertical_floor_controls(
                    bright_column(order, order[0]), mode)["selectedFloor"])
        frame = bright_column((2, 1, -1), 1)
        frame[465:495, 1600:1640] = 68
        self.assertIsNone(detector.locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])

    def test_no_highlight_double_highlight_and_low_contrast_fail_closed(self):
        for mode, order in ORDERS:
            for highlight in (68, 75):
                with self.subTest(mode=mode, highlight=highlight):
                    self.assertIsNone(detector.locate_vertical_floor_controls(
                        bright_column(order, 1, highlight=highlight), mode)["selectedFloor"])
            frame = bright_column(order, 1)
            other = bright_column(order, 2)
            frame[328:392, 1590:1650] = other[328:392, 1590:1650]
            self.assertIsNone(detector.locate_vertical_floor_controls(frame, mode)["selectedFloor"])

    def test_complete_bright_columns_are_not_resolved_by_exposure(self):
        frame = bright_column((2, 1, -1), 1)
        frame[:, 1450:1550] = frame[:, 1570:1670]
        self.assertIsNone(detector.locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])
        # Already observed ambiguity must not be hidden by another exposure.
        with patch.object(detector, "_read_vertical", return_value=dict(
                selectedFloor=None, reason="columns-ambiguous", confidence=0.)) as read:
            result = detector.locate_vertical_floor_controls(frame, "nightmare")
        self.assertEqual(result["reason"], "columns-ambiguous")
        self.assertEqual(read.call_count, 1)

    def test_original_success_cannot_hide_another_exposure_column(self):
        for mode, order in ORDERS:
            frame = bright_column(order, 1)
            other = column(order, 2)
            frame[:, 1450:1550] = other[:, 1570:1670]
            # One dark column is readable before normalizing the surrounding
            # bright panel; normalization must still veto the second column.
            gray, origin = detector._crop(frame, detector.VERTICAL_SEARCH_AREA)
            raw = detector._read_vertical(gray, detector.MODE_FLOORS[mode], origin, frame.shape[1::-1], "test")
            self.assertEqual(raw["selectedFloor"], 2)
            read = detector.locate_vertical_floor_controls(frame, mode)
            self.assertIsNone(read["selectedFloor"], read)
            self.assertEqual(read["reason"], "columns-ambiguous")

    def test_bright_ui_text_noise_and_unregistered_contexts_fail_closed(self):
        frames = [np.full((1080, 1920, 3), value, np.uint8) for value in (0, 33, 68, 90, 160, 230)]
        light = bright_column((2, 1, -1), 1)
        light = np.clip(light.astype(np.int16) + 100, 0, 255).astype(np.uint8)
        frames.append(light)
        text = np.full((1080, 1920, 3), 68, np.uint8)
        for index, label in enumerate(("SCORE 2", "TIME 1", "-1 -2")):
            cv2.putText(text, label, (1530, 300 + index * 60), cv2.FONT_HERSHEY_SIMPLEX,
                        .7, (225,) * 3, 2)
        frames.extend((text, np.random.default_rng(24).integers(0, 256, (1080, 1920, 3), dtype=np.uint8)))
        for frame in frames:
            for mode, _ in ORDERS:
                self.assertIsNone(detector.locate_vertical_floor_controls(frame, mode)["selectedFloor"])
        for season in ("s1", "s2"):
            self.assertIsNone(detector.locate_vertical_floor_controls(
                bright_column((2, 1, -1), 1), "nightmare", season=season)["selectedFloor"])

    def test_bounded_exposure_evidence_survives_privacy_without_user_text(self):
        from diagnostic_privacy import safe_record
        read = detector.locate_vertical_floor_controls(bright_column((2, 1, -1), 1), "nightmare")
        saved = safe_record(dict(floorRead=dict(read, username="PrivateName", title="PrivateTitle")))
        self.assertEqual(saved["floorRead"]["controlBackground"], read["controlBackground"])
        self.assertEqual(saved["floorRead"]["controlExposure"], read["controlExposure"])
        self.assertNotIn("username", saved["floorRead"])
        self.assertNotIn("PrivateTitle", str(saved))


if __name__ == "__main__":
    unittest.main()
