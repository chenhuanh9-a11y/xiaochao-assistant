"""Synthetic S0 bright-map proof and adversaries; no saved game assets."""
import copy
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from floor_control_region import locate_vertical_floor_controls
from map_interface_observation import detect_map_interface, MAP_REGIONS, neutral_ratio
from s0_vertical_controls import observe_s0_map, _observe_s0_map_geometry
from test_client_floor_region import column, ORDERS
from test_s1_preparation_robustness import bright_frame, remove_region


def s0_bright(mode, selected=1):
    order = dict(ORDERS)[mode]
    frame = bright_frame()
    frame[:700, 1600:1750] = 33
    source = column(order, selected, size=(1920, 1200))
    frame[:700, 1580:1660] = source[:700, 1580:1660]
    frame[1000:1050, 1700:1750] = 150
    return frame


def s0_tinted(mode, *, center_x=1620):
    """Complete invented HUD under one uniform blue color cast, not live tint."""
    frame = s0_bright(mode)
    frame[260:700, 500:1400] = 40
    if center_x != 1620:
        pixels = frame[:700, 1580:1660].copy()
        frame[:700, 1580:1660] = 33
        frame[:700, center_x-40:center_x+40] = pixels
    frame[:, :, 0] = np.minimum(frame[:, :, 0].astype(np.int16) + 60, 255)
    return frame


class S0MapGeometryProof(unittest.TestCase):
    def closed(self, frame, mode):
        observation, read = observe_s0_map(frame, mode)
        self.assertFalse(observation['detected'], observation)
        self.assertIsNone(read['selectedFloor'])
        self.assertFalse(read['controlGeometryVerified'])

    def test_old_red_new_green_both_modes_all_floors(self):
        for mode, order in ORDERS:
            for selected in order:
                with self.subTest(mode=mode, floor=selected):
                    frame = s0_bright(mode, selected)
                    before = frame.copy()
                    old = detect_map_interface(frame, floor_read={})
                    self.assertFalse(old['detected'])
                    self.assertEqual(old['reason'], 'map-canvas-texture-mismatch')
                    observation, read = observe_s0_map(frame, mode)
                    self.assertTrue(observation['detected'], observation)
                    self.assertEqual(read['selectedFloor'], selected, read)
                    self.assertTrue(read['controlGeometryVerified'])
                    self.assertEqual(observation['detectionMode'], 's0-signed-controls-ui-geometry')
                    self.assertEqual(observation['backdropTexture'], old['backdropTexture'])
                    self.assertEqual(detect_map_interface(frame, floor_read={}), old)
                    np.testing.assert_array_equal(frame, before)

    def test_each_independent_shape_rail_anchor_and_backdrop_required(self):
        for mode, _ in ORDERS:
            for missing in ('close', 'plus', 'minus', 'rail', 'closeButton',
                            'zoomControl', 'bottomControls', 'backdrop'):
                frame = s0_bright(mode)
                if missing in MAP_REGIONS:
                    remove_region(frame, missing)
                elif missing == 'backdrop':
                    frame[168:960, 480:1536] = 180
                else:
                    rect = dict(close=(115,185), plus=(415,490), minus=(895,965), rail=(590,750))[missing]
                    frame[rect[0]:rect[1], 1780:1850] = 40
                with self.subTest(mode=mode, missing=missing):
                    self.closed(frame, mode)

    def test_partial_shapes_and_text_cannot_replace_group(self):
        for mode, _ in ORDERS:
            for kind in ('slash', 'plus', 'text', 'disk'):
                frame = s0_bright(mode)
                frame[115:185, 1780:1850] = 40
                if kind == 'slash':
                    cv2.line(frame, (1794,130), (1834,170), (180,)*3, 10)
                elif kind == 'plus':
                    cv2.line(frame, (1794,150), (1834,150), (180,)*3, 10)
                    cv2.line(frame, (1814,130), (1814,170), (180,)*3, 10)
                elif kind == 'text':
                    cv2.putText(frame, 'EXIT', (1780,150), cv2.FONT_HERSHEY_SIMPLEX, .5, (180,)*3, 2)
                else:
                    cv2.circle(frame, (1814,150), 27, (180,)*3, -1)
                self.closed(frame, mode)

    def test_column_blue_canvas_noise_and_uniform_frames_do_not_open(self):
        for mode, order in ORDERS:
            frames = [column(order, 1, size=(1920,1200))]
            blue = frames[0].copy(); blue[:,:1500] = (75,55,40); frames.append(blue)
            frames.extend(np.full((1200,1920,3), value, np.uint8) for value in (0,40,150,230))
            frames.append(np.random.default_rng(27).integers(0,256,(1200,1920,3),dtype=np.uint8))
            for frame in frames:
                self.closed(frame, mode)

    def test_new_exposure_cannot_activate_legacy_tinted_map_without_shapes(self):
        from test_s0_bright_controls import bright_column
        for mode, order in ORDERS:
            for background in (63, 64, 68):
                for highlight in (95, 160):
                    frame = bright_column(order, 1, highlight=highlight)
                    frame[np.all(frame == 68, axis=2)] = background
                    raw = locate_vertical_floor_controls(frame, mode)
                    self.assertEqual(raw['selectedFloor'], 1)
                    self.assertIn('controlExposure', raw)
                    # This old color-only tolerant branch must not be newly
                    # unlocked just because normalization can now read a row.
                    self.assertTrue(detect_map_interface(frame, floor_read=raw)['detected'])
                    self.closed(frame, mode)

    def test_two_exposure_columns_cannot_borrow_complete_map_shapes(self):
        from test_s0_bright_controls import bright_column
        for mode, order in ORDERS:
            columns = bright_column(order, 1)
            other = column(order, 2)
            columns[:, 1450:1550] = other[:, 1570:1670]
            frame = s0_bright(mode)
            frame[:800, 1440:1785] = columns[:800, 1440:1785]
            self.closed(frame, mode)

    def test_legacy_tinted_blank_canvas_with_column_is_not_a_map(self):
        for mode, order in ORDERS:
            for background in (60, 61, 62):
                frame = column(order, 1)
                frame[np.all(frame == 33, axis=2)] = background
                raw = locate_vertical_floor_controls(frame, mode)
                old = detect_map_interface(frame, floor_read=raw)
                self.assertTrue(old['detected'])  # Old red: colors alone open.
                self.assertEqual(old['detectionMode'], 'damage-tint')
                self.closed(frame, mode)

    def test_complete_tinted_geometry_preserves_color_semantics(self):
        for mode, _ in ORDERS:
            frame = s0_tinted(mode)
            raw = locate_vertical_floor_controls(frame, mode)
            old = detect_map_interface(frame, floor_read=raw)
            self.assertEqual(old['detectionMode'], 'damage-tint', old)
            self.assertLess(old['neutralMatchedAnchors'], 3)
            observation, read = observe_s0_map(frame, mode)
            self.assertTrue(observation['detected'], observation)
            self.assertEqual(read['selectedFloor'], 1)
            self.assertEqual(observation['detectionMode'], 's0-signed-controls-ui-geometry')
            self.assertTrue(read['controlGeometryVerified'])

    def test_tinted_map_requires_each_shape_rail_and_bottom_anchor(self):
        for mode, _ in ORDERS:
            for missing in ('close', 'plus', 'minus', 'rail', 'bottomControls'):
                frame = s0_tinted(mode)
                if missing == 'bottomControls':
                    # A dark non-tinted blank removes even the tolerant anchor.
                    remove_region(frame, missing)
                else:
                    low, high = dict(close=(115,185), plus=(415,490),
                                     minus=(895,965), rail=(590,750))[missing]
                    frame[low:high,1780:1850] = (100,40,40)
                with self.subTest(mode=mode, missing=missing):
                    self.closed(frame, mode)

    def test_malformed_or_conflicting_current_floor_evidence_is_not_promoted(self):
        for mode, _ in ORDERS:
            frame = s0_bright(mode)
            raw = locate_vertical_floor_controls(frame, mode)
            self.assertEqual(raw['selectedFloor'], 1)
            mutations = [lambda r:r.update(selectedFloor=None), lambda r:r.update(selectedFloor=True),
                lambda r:r.update(season='s1'), lambda r:r.update(mode='other'),
                lambda r:r.update(selectedFloor=2), lambda r:r.update(observedOrder=[2,True]),
                lambda r:r['labelEvidence'].pop(), lambda r:r['centerPoints'].reverse(),
                lambda r:r['labelEvidence'][0].update(negative=True),
                lambda r:r.update(tileBounds=[float('nan'),.2,.03,.07])]
            old = detect_map_interface(frame, floor_read=raw)
            for mutate in mutations:
                bad = copy.deepcopy(raw); mutate(bad)
                self.assertFalse(_observe_s0_map_geometry(frame, mode, bad, old)['detected'])

    def test_previous_good_floor_is_not_reused_after_current_pixels_disappear(self):
        for mode, _ in ORDERS:
            frame = s0_bright(mode)
            self.assertTrue(observe_s0_map(frame, mode)[0]['detected'])
            frame[:700,1580:1660] = 33
            self.closed(frame, mode)

    def test_floor_column_cannot_supply_bottom_anchor(self):
        for mode, _ in ORDERS:
            frame = s0_bright(mode)
            remove_region(frame, 'bottomControls')
            # Supply enough neutral pixels inside the measured floor column
            # for the old broad anchor, but none outside that column.
            frame[1000:1120, 1590:1650] = 150
            self.assertGreaterEqual(neutral_ratio(frame, MAP_REGIONS['bottomControls']), .025)
            self.closed(frame, mode)

    def test_shape_preprocessing_cannot_read_excluded_floor_bytes(self):
        for mode, _ in ORDERS:
            frame = s0_bright(mode)
            raw = locate_vertical_floor_controls(frame, mode)
            old = detect_map_interface(frame, floor_read=raw)
            first = _observe_s0_map_geometry(frame, mode, raw, old)
            self.assertTrue(first['detected'])
            x0, _, x1, _ = first['independentFloorExclusion']
            changed = frame.copy()
            changed[:, x0:x1] = np.random.default_rng(51).integers(
                0, 256, changed[:, x0:x1].shape, dtype=np.uint8)
            # Isolation test only: deliberately hold metadata fixed. Production
            # observe_s0_map re-detects current pixels (tested separately above).
            second = _observe_s0_map_geometry(changed, mode, raw, old)
            for key in ('mapUiGeometry', 'independentAnchorRatios', 'independentBackdropRatio'):
                self.assertEqual(first[key], second[key])

    def test_multiple_supported_scales(self):
        for mode, _ in ORDERS:
            for size in ((1280,800), (2560,1600)):
                frame = cv2.resize(s0_bright(mode), size, interpolation=cv2.INTER_NEAREST)
                observation, read = observe_s0_map(frame, mode)
                self.assertTrue(observation['detected'], observation)
                self.assertEqual(read['selectedFloor'], 1)

    def test_existing_success_skips_alternate_geometry_cost(self):
        for mode, order in ORDERS:
            frame = column(order, 1, size=(1920,1200))
            frame[90:190,1790:1840] = 150
            frame[700:800,1810:1850] = 150
            frame[1000:1050,1600:1750] = 150
            with patch('s1_map_ui_geometry.observe_s1_map_ui_geometry', side_effect=AssertionError('unneeded shapes')):
                self.assertTrue(observe_s0_map(frame, mode)[0]['detected'])

    def test_independent_evidence_survives_privacy_without_user_text(self):
        from diagnostic_privacy import safe_record
        observation, _ = observe_s0_map(s0_bright('difficult'), 'difficult')
        saved = safe_record(dict(observation=dict(observation, username='PrivateName', title='PrivateTitle')))
        for key in ('detectionMode', 'independentAnchorRatios', 'independentBackdropRatio',
                    'independentFloorExclusion', 'mapUiGeometry'):
            self.assertEqual(saved['observation'][key], observation[key])
        self.assertNotIn('username', saved['observation'])
        self.assertNotIn('PrivateTitle', str(saved))


if __name__ == '__main__':
    unittest.main()
