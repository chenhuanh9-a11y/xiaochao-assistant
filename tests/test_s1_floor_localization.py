"""Synthetic position/scale/adversarial probes; no device or game proof."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import s1_floor_observation as detector
from client_space_geometry import centered_safe_viewport
from test_s1_floor_observation import mathematical_frame, draw_column
from test_s1_preparation_robustness import bright_frame


def relocated(floor=-2, scale=1., x=1440, y=180):
    frame = mathematical_frame((floor,), anchors=False)
    return cv2.warpAffine(frame, np.float32([[scale, 0, x-scale*1682],
                                           [0, scale, y-scale*264]]),
                          (1920, 1200), flags=cv2.INTER_NEAREST, borderValue=(40,)*3)


def centered_hud(size, floor=-2):
    # Explicit mathematical HUD viewport, not a reconstructed game frame.
    hud = bright_frame((floor,))[60:1140]
    vp = centered_safe_viewport(size)
    frame = np.full((size[1], size[0], 3), 40, np.uint8)
    frame[vp.top:vp.bottom, vp.left:vp.right] = cv2.resize(hud, (vp.width, vp.height))
    return frame


class S1JointFloorLocalization(unittest.TestCase):
    def raw(self, frame):
        return detector._observe_signed_column(frame, frame.shape[1], frame.shape[0])

    def test_joint_position_and_local_scale_in_all_four_rows(self):
        for floor in detector.ORDER:
            for scale in (.5, .75, 1., 1.25, 1.5, 2.):
                with self.subTest(floor=floor, scale=scale):
                    frame = relocated(floor, scale)
                    result = self.raw(frame)
                    self.assertEqual(result.get("selectedFloor"), floor, result)
                    self.assertEqual(result["localization"], "signed-column-position-scale")
                    self.assertEqual(len(result["labelEvidence"]), 4)
                    self.assertNotIn("controlGeometryVerified", result)
                    self.assertNotIn("mapOpen", result)
                    before = frame.copy()
                    self.raw(frame)
                    np.testing.assert_array_equal(frame, before)

    def test_position_scale_column_alone_never_opens_map(self):
        for scale in (.5, 1., 2.):
            result = detector.detect_s1_floor(relocated(scale=scale), season="s1", mode="nightmare")
            self.assertIsNone(result["selectedFloor"])
            self.assertFalse(result["mapOpen"])

    def test_complete_map_ui_and_floor_in_centered_aspect_models(self):
        for size in ((1280, 720), (1280, 800), (1280, 960), (1920, 1200),
                     (2560, 1080), (2560, 1440), (3440, 1440)):
            for floor in detector.ORDER:
                with self.subTest(size=size, floor=floor):
                    result = detector.detect_s1_floor(centered_hud(size, floor), season="s1", mode="nightmare")
                    self.assertEqual(result["selectedFloor"], floor, result)
                    self.assertTrue(result["mapOpen"])

    def test_two_new_domain_columns_are_ambiguous(self):
        first = relocated(x=1400)
        other = relocated(-3, x=1250)
        first[:, 1200:1300] = other[:, 1200:1300]
        result = self.raw(first)
        self.assertIsNone(result.get("selectedFloor"), result)
        self.assertEqual(result["reason"], "s1-floor-column-ambiguous")

    def test_independently_scaled_floor_retains_external_ui_proof(self):
        for scale in (.5, 1.5, 2.):
            for floor in detector.ORDER:
                with self.subTest(scale=scale, floor=floor):
                    frame = bright_frame((floor,))
                    controls = relocated(floor, scale=scale, x=1682, y=264)
                    frame[200:600, 1630:1735] = 40
                    x0, y0, x1, y1 = self.raw(controls)["controlBounds"]
                    frame[y0:y1, x0:x1] = controls[y0:y1, x0:x1]
                    result = detector.detect_s1_floor(frame, season="s1", mode="nightmare")
                    self.assertEqual(result["selectedFloor"], floor, result)
                    self.assertTrue(result["mapOpen"])

    def test_partial_columns_missing_sign_double_highlight_and_fifth_row_rejected(self):
        for mutation in ("missing-row", "missing-sign", "two-highlights", "fifth-row"):
            frame = relocated()
            if mutation == "missing-row":
                frame[240:290, 1390:1490] = 40
            elif mutation == "missing-sign":
                frame[177:185, 1420:1440] = 40
            elif mutation == "two-highlights":
                other = relocated(-3)
                frame[320:375, 1390:1490] = other[320:375, 1390:1490]
            else:
                frame[500:550, 1390:1490] = frame[328:378, 1390:1490]
            with self.subTest(mutation=mutation):
                self.assertIsNone(self.raw(frame).get("selectedFloor"))

    def test_nearby_text_uniform_and_high_frequency_noise_cannot_create_column(self):
        for value in (0, 40, 80, 150, 230):
            self.assertIsNone(self.raw(np.full((1200, 1920, 3), value, np.uint8)).get("selectedFloor"))
        frame = np.full((1200, 1920, 3), 40, np.uint8)
        for index, text in enumerate(("SCORE -2", "TIME +1", "-3  -4", "1234")):
            cv2.putText(frame, text, (1200, 180+index*86), cv2.FONT_HERSHEY_SIMPLEX, .7, (180,)*3, 2)
        self.assertIsNone(self.raw(frame).get("selectedFloor"))
        noise = np.random.default_rng(7).integers(0, 256, (600, 1000, 3), dtype=np.uint8)
        self.assertIsNone(self.raw(noise).get("selectedFloor"))

    def test_missing_independent_ultrawide_shape_not_rescued_by_valid_column(self):
        frame = centered_hud((2560, 1080))
        vp = centered_safe_viewport((2560, 1080))
        frame[:, vp.left+round(.92*vp.width):] = 40
        self.assertEqual(self.raw(frame).get("selectedFloor"), -2)
        result = detector.detect_s1_floor(frame, season="s1", mode="nightmare")
        self.assertFalse(result["mapOpen"])
        self.assertIsNone(result["selectedFloor"])

    def test_localized_floor_pixels_are_removed_from_independent_geometry(self):
        frame = centered_hud((2560, 1080))
        raw = self.raw(frame)
        observation = detector.detect_map_interface(frame, floor_read={})
        import independent_map_ui as shared_ui
        original = shared_ui.observe_s1_map_ui_geometry
        def external(image, **kw):
            x0, y0, x1, y1 = raw["controlBounds"]
            self.assertIn((x0, y0, x1, y1), kw['excluded_bounds'])
            result = original(image, **kw)
            changed = image.copy()
            changed[y0:y1, x0:x1] = 255
            self.assertEqual(original(changed, **kw), result)
            return result
        with patch.object(shared_ui, "observe_s1_map_ui_geometry", side_effect=external):
            self.assertIsNotNone(detector._s1_independent_map_evidence(frame, observation,
                                floor_bounds=raw["controlBounds"]))

    def test_new_domain_signed_list_far_from_ui_cannot_borrow_map_anchors(self):
        frame = bright_frame()
        frame[200:600, 1635:1730] = 40
        text = relocated(x=1200)
        frame[:, 1150:1250] = text[:, 1150:1250]
        self.assertEqual(self.raw(frame).get("selectedFloor"), -2)
        result = detector.detect_s1_floor(frame, season="s1", mode="nightmare")
        self.assertIsNone(result["selectedFloor"], result)

    def test_numeric_localization_survives_privacy_without_free_text(self):
        from diagnostic_privacy import safe_record
        raw = self.raw(relocated())
        saved = safe_record(dict(floorRead=dict(raw, username="PrivateName", title="PrivateTitle")))
        self.assertEqual(saved["floorRead"]["localization"], "signed-column-position-scale")
        self.assertEqual(saved["floorRead"]["controlBounds"], raw["controlBounds"])
        self.assertEqual(saved["floorRead"]["controlLocalScale"], raw["controlLocalScale"])
        self.assertNotIn("username", saved["floorRead"])
        self.assertNotIn("PrivateTitle", str(saved))


if __name__ == "__main__":
    unittest.main()
