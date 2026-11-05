"""Mathematical glyph fixtures; these are not source screenshots or live proof."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import s1_floor_observation as detector


def draw_column(frame, selected=(-1,), offset=0, missing_sign=None):
    for index, floor in enumerate(detector.ORDER):
        cy = 264 + index * 86
        chosen = floor in selected
        if chosen:
            frame[cy - 40:cy + 40, 1650 + offset:1714 + offset] = 150
        color = 30 if chosen else 180
        if floor != missing_sign:
            frame[cy:cy + 2, 1669 + offset:1681 + offset] = color
        if index == 0:
            frame[cy - 10:cy + 12, 1687 + offset:1692 + offset] = color
        else:
            cv2.rectangle(frame, (1684 + offset, cy - 10), (1694 + offset, cy + 11),
                          (color, color, color), 2)


def mathematical_frame(selected=(-1,), *, anchors=True, missing_sign=None):
    frame = np.full((1200, 1920, 3), 40, np.uint8)
    if anchors:
        # Independent neutral controls, outside the signed-column search band.
        frame[90:190, 1790:1840] = 150
        frame[700:800, 1810:1850] = 150
        frame[1000:1050, 1600:1700] = 150
    draw_column(frame, selected, missing_sign=missing_sign)
    return frame


def read(frame, **kwargs):
    return detector.detect_s1_floor(frame, season="s1", mode="nightmare", **kwargs)


class S1FloorPixelObservation(unittest.TestCase):
    def test_safe_diagnostics_keep_private_eligibility_separate_from_real_validation(self):
        import json
        from diagnostic_privacy import safe_record
        current = detector.detect_s1_floor(mathematical_frame((-3,)), season="s1", mode="nightmare")
        row = safe_record(dict(floorRead=dict(current, rawSelectedFloor=-3),
            sourceReplayFloors=[-1, -2], syntheticOnlyFloors=[-3, -4],
            username="PrivateFloorSecret", reason="s1-floor-observed"))
        read = row["floorRead"]
        self.assertTrue(read["automaticFloorEligible"])
        self.assertFalse(read["observedFloorValidated"])
        self.assertEqual(read["validationScope"], "synthetic-only")
        self.assertEqual(read["rawSelectedFloor"], -3)
        self.assertEqual(row["sourceReplayFloors"], [-1, -2])
        self.assertEqual(row["reason"], "s1-floor-observed")
        self.assertNotIn("PrivateFloorSecret", json.dumps(row))

    def test_complete_signed_columns_in_all_declared_modes(self):
        for mode in ("difficulty2", "difficult", "nightmare"):
            for floor in detector.ORDER:
                with self.subTest(mode=mode, floor=floor):
                    result = detector.detect_s1_floor(mathematical_frame((floor,)), season="s1", mode=mode)
                    self.assertEqual(result["selectedFloor"], floor, result)
                    self.assertTrue(result["mapOpen"])
                    self.assertEqual(result["observedFloorValidated"], floor in (-1, -2))
                    self.assertTrue(result["automaticFloorEligible"])
                    self.assertEqual(result["validationScope"], "source-image-replay" if floor in (-1, -2) else "synthetic-only")
                    self.assertEqual(result["capability"], "s1-floor-only-manual-map-use")
                    self.assertEqual(result["floorUse"], "manual-map-only")
                    self.assertEqual(result["observedOrder"], [-1, -2, -3, -4])
                    self.assertEqual(len(result["labelEvidence"]), 4)
                    self.assertTrue(all(row["negative"] for row in result["labelEvidence"]))
                    self.assertTrue(all(0 < x < 1 and 0 < y < 1 for x, y in result["centerPoints"]))
                    self.assertNotIn("candidateReady", result)
                    self.assertNotIn("liveValidated", result)
                    self.assertNotIn("controlGeometryVerified", result)

    def test_independent_map_gate_receives_empty_floor_proof(self):
        frame = mathematical_frame()
        with patch.object(detector, "detect_map_interface", return_value={"detected": False}) as gate:
            result = read(frame)
        gate.assert_called_once()
        self.assertIs(gate.call_args.args[0], frame)
        self.assertEqual(gate.call_args.kwargs, {"floor_read": {}})
        self.assertIsNone(result["selectedFloor"])
        self.assertFalse(result["mapOpen"])
        self.assertFalse(result["observedFloorValidated"])
        self.assertFalse(result["automaticFloorEligible"])
        self.assertIsNone(read(mathematical_frame(anchors=False))["selectedFloor"])

    def test_invalid_context_types_and_known_stale_frames_fail_before_map_gate(self):
        frame = mathematical_frame()
        invalid = [dict(season="s0", mode="nightmare"), dict(season="s1", mode="normal"),
                   dict(season="s1", mode=[]), dict(season=[], mode="nightmare"),
                   dict(season=np.array(["s1", "s1"]), mode="nightmare")]
        with patch.object(detector, "detect_map_interface") as gate:
            for context in invalid:
                self.assertIsNone(detector.detect_s1_floor(frame, **context)["selectedFloor"])
            for kwargs in (dict(frame_current=False), dict(frame_current=1),
                           dict(frame_id=1, expected_frame_id=2),
                           dict(frame_id=True, expected_frame_id=1),
                           dict(expected_frame_id="missing")):
                self.assertIsNone(read(frame, **kwargs)["selectedFloor"])
            gate.assert_not_called()
        self.assertEqual(read(frame, frame_id=123, expected_frame_id=123)["selectedFloor"], -1)

    def test_frame_shape_and_type_rejected(self):
        for frame in (None, [], np.zeros((1200, 1920), np.uint8),
                      np.zeros((1200, 1920, 4), np.uint8),
                      np.zeros((1200, 1920, 3), np.float32),
                      np.zeros((599, 1920, 3), np.uint8),
                      np.zeros((1200, 999, 3), np.uint8),
                      np.zeros((0, 0, 3), np.uint8)):
            with self.subTest(shape=getattr(frame, "shape", None)):
                self.assertIsNone(read(frame)["selectedFloor"])

    def test_missing_sign_or_row_and_no_or_two_highlights_rejected(self):
        cases = [mathematical_frame((), missing_sign=None), mathematical_frame((-1, -2))]
        cases.extend(mathematical_frame(missing_sign=floor) for floor in detector.ORDER)
        incomplete = mathematical_frame()
        incomplete[500:560, 1640:1730] = 40
        cases.append(incomplete)
        for frame in cases:
            self.assertIsNone(read(frame)["selectedFloor"])

    def test_ambiguous_fill_rejected(self):
        frame = mathematical_frame(())
        frame[224:304, 1670:1694] = 150
        # Redraw glyphs into a partially filled tile; its fill falls between
        # unselected <= .20 and selected >= .65.
        frame[254:276, 1687:1692] = 30
        frame[264:266, 1669:1681] = 30
        self.assertIsNone(read(frame)["selectedFloor"])

    def test_two_columns_and_duplicate_same_column_evidence_rejected(self):
        frame = mathematical_frame()
        draw_column(frame, offset=-90)
        result = read(frame)
        self.assertIsNone(result["selectedFloor"])
        self.assertEqual(result["reason"], "s1-floor-column-ambiguous")
        original = detector._labels
        with patch.object(detector, "_labels", side_effect=lambda *args: original(*args) * 2):
            self.assertEqual(read(mathematical_frame())["reason"], "s1-floor-column-ambiguous")

    def test_narrow_lower_glyph_and_nonuniform_spacing_rejected(self):
        frame = mathematical_frame()
        frame[338:365, 1682:1700] = 40
        frame[340:362, 1687:1692] = 180
        self.assertIsNone(read(frame)["selectedFloor"])
        frame = mathematical_frame()
        row = frame[420:460, 1660:1710].copy()
        frame[420:460, 1660:1710] = 40
        frame[445:485, 1660:1710] = row
        self.assertIsNone(read(frame)["selectedFloor"])

    def test_sign_and_digit_must_share_polarity(self):
        frame = mathematical_frame()
        frame[264:266, 1669:1681] = 180  # selected dark digit, light sign
        self.assertIsNone(read(frame)["selectedFloor"])

    def test_two_signs_for_one_digit_rejected(self):
        frame = mathematical_frame()
        frame[260:262, 1669:1681] = 30
        self.assertIsNone(read(frame)["selectedFloor"])

    def test_first_glyph_must_be_narrow(self):
        frame = mathematical_frame()
        frame[254:276, 1687:1692] = 150
        cv2.rectangle(frame, (1684, 254), (1694, 275), (30, 30, 30), 2)
        self.assertIsNone(read(frame)["selectedFloor"])

    def test_empty_roi_and_excessive_labels_rejected(self):
        with patch.object(detector, "SEARCH_BOUNDS", (0, 0, 0, 0)):
            self.assertEqual(read(mathematical_frame())["reason"], "s1-floor-empty-roi")
        with patch.object(detector, "_labels", return_value=[{}] * 17):
            self.assertIsNone(read(mathematical_frame())["selectedFloor"])

    def test_empty_fill_window_rejected(self):
        self.assertIsNone(detector._fill(np.zeros((0, 0), np.uint8),
                                        {"centerX": 0, "y": 0}, 1920))


if __name__ == "__main__":
    unittest.main()
