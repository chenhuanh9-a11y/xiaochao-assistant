"""Synthetic geometry adversaries. Real replay is separate; no game assets here."""
import unittest
import cv2
import numpy as np

from s1_map_ui_geometry import observe_s1_map_ui_geometry
from s1_floor_observation import detect_s1_floor
from test_s1_preparation_robustness import bright_frame


class S1MapUiGeometry(unittest.TestCase):
    def test_exclusion_preprocessing_matches_zeroed_pixels_and_preserves_input(self):
        frame = bright_frame()
        before = frame.copy()
        for bounds in ((1760, 0, 1790, 1200), (1780, 115, 1850, 185),
                       (1790, 500, 1840, 883)):
            with self.subTest(bounds=bounds):
                expected_frame = frame.copy()
                x0, y0, x1, y1 = bounds
                expected_frame[y0:y1, x0:x1] = 0
                expected = self.verify(expected_frame)
                actual = observe_s1_map_ui_geometry(frame, excluded_bounds=(bounds,))
                self.assertEqual(actual, expected)
                changed = frame.copy()
                changed[y0:y1, x0:x1] = 255
                self.assertEqual(observe_s1_map_ui_geometry(changed, excluded_bounds=(bounds,)), actual)
        np.testing.assert_array_equal(frame, before)

    def test_sampled_exclusions_and_gain_equal_whole_frame_operations(self):
        from map_interface_observation import MAP_REGIONS, neutral_ratio, backdrop_ratio
        rng = np.random.default_rng(472)
        for height, width in ((800, 1000), (1080, 1920), (1417, 2560)):
            frame = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
            before = frame.copy()
            masks = ((width//2+1, height//3+1, width-17, height-13),
                     (width*4//5, 0, width*9//10+1, height))
            masked = frame.copy()
            for x0, y0, x1, y1 in masks:
                masked[y0:y1, x0:x1] = 0
            for gain in (1., 2./3., 1.5):
                reference = masked if gain == 1. else cv2.convertScaleAbs(masked, alpha=gain)
                with self.subTest(size=(width, height), gain=gain):
                    for region in MAP_REGIONS.values():
                        for tinted in (False, True):
                            self.assertEqual(neutral_ratio(frame, region, tinted,
                                excluded_bounds=masks, exposure=gain), neutral_ratio(reference, region, tinted))
                    self.assertEqual(backdrop_ratio(frame, excluded_bounds=masks, exposure=gain),
                                     backdrop_ratio(reference))
            np.testing.assert_array_equal(frame, before)

    def test_both_seasons_use_the_shared_independent_ui_producer(self):
        from unittest.mock import patch
        import independent_map_ui as shared
        from s0_vertical_controls import observe_s0_map
        from test_s0_map_geometry_proof import s0_bright
        with patch.object(shared, 'observe_independent_map_ui', wraps=shared.observe_independent_map_ui) as observed:
            observation, read = observe_s0_map(s0_bright('nightmare'), 'nightmare')
            self.assertTrue(observation['detected'], (observation, read))
            s0_calls = observed.call_count
            self.assertGreater(s0_calls, 0)
            result = detect_s1_floor(bright_frame(), season='s1', mode='nightmare')
            self.assertEqual(result['selectedFloor'], -1, result)
            self.assertGreater(observed.call_count, s0_calls)

    def verify(self,frame):
        return observe_s1_map_ui_geometry(frame)

    def assert_closed(self,frame):
        shape=self.verify(frame)
        self.assertFalse(shape['verified'],shape)
        result=detect_s1_floor(frame,season='s1',mode='nightmare')
        self.assertFalse(result['mapOpen'],result)
        self.assertIsNone(result['selectedFloor'],result)

    def test_complete_group_at_multiple_scales(self):
        for size in ((1920,1200),(2560,1600),(1280,800)):
            frame=cv2.resize(bright_frame(),size)
            self.assertTrue(self.verify(frame)['verified'],self.verify(frame))
            self.assertEqual(detect_s1_floor(frame,season='s1',mode='nightmare')['selectedFloor'],-1)

    def test_each_shape_missing_and_uniform_shape_colored_tile_rejected(self):
        for x0,y0,x1,y1 in ((1780,115,1850,185),(1780,415,1850,490),(1780,895,1850,965)):
            for value in (40,150):
                frame=bright_frame();frame[y0:y1,x0:x1]=value;self.assert_closed(frame)

    def test_one_close_diagonal_or_orthogonal_cross_is_not_x(self):
        for kind in ('slash','backslash','plus','line'):
            frame=bright_frame();frame[115:185,1780:1850]=40
            if kind in ('slash','backslash'):
                start,end=((1794,130),(1834,170)) if kind=='backslash' else ((1834,130),(1794,170))
                cv2.line(frame,start,end,(180,)*3,10)
            else:
                cv2.line(frame,(1794,150),(1834,150),(180,)*3,10)
                if kind=='plus':cv2.line(frame,(1814,130),(1814,170),(180,)*3,10)
            self.assert_closed(frame)

    def test_plus_and_minus_have_different_strokes(self):
        for missing in ('plus-vertical','plus-horizontal','minus-becomes-plus'):
            frame=bright_frame()
            if missing.startswith('plus'):
                cv2.circle(frame,(1818,452),27,(180,)*3,-1)
                if missing=='plus-vertical':cv2.line(frame,(1800,452),(1836,452),(30,)*3,7)
                else:cv2.line(frame,(1818,434),(1818,470),(30,)*3,7)
            else:cv2.line(frame,(1818,912),(1818,948),(30,)*3,7)
            self.assert_closed(frame)

    def test_missing_or_interrupted_rail_is_not_control_group(self):
        for start,end in ((500,883),(590,750)):
            frame=bright_frame();frame[start:end,1790:1840]=40;self.assert_closed(frame)

    def test_only_x_or_only_plus_or_only_rail_never_proves_map(self):
        for keep in ('close','plus','rail'):
            frame=bright_frame()
            if keep!='close':frame[115:185,1780:1850]=40
            if keep!='plus':frame[415:490,1780:1850]=40
            if keep!='rail':frame[500:883,1790:1840]=40
            frame[895:965,1780:1850]=40
            self.assert_closed(frame)

    def test_displaced_plus_cannot_match_unrelated_x_and_minus(self):
        frame=bright_frame();tile=frame[415:490,1780:1850].copy();frame[415:490,1780:1850]=40
        frame[415:490,1835:1905]=tile
        self.assert_closed(frame)

    def test_wrong_vertical_spacing_rejected(self):
        frame=bright_frame();tile=frame[415:490,1780:1850].copy();frame[415:490,1780:1850]=40
        frame[340:415,1780:1850]=tile
        self.assert_closed(frame)

    def test_duplicate_close_glyphs_are_ambiguous(self):
        frame=bright_frame()
        # Second complete X in the same independent search region.
        cv2.line(frame,(1849,130),(1889,170),(180,)*3,10)
        cv2.line(frame,(1889,130),(1849,170),(180,)*3,10)
        self.assert_closed(frame)

    def test_shapes_inside_floor_region_do_not_supply_evidence(self):
        frame=bright_frame();strip=frame[:,1780:1850].copy();frame[:,1780:1850]=40
        frame[:,1660:1730]=strip
        self.assertFalse(self.verify(frame)['verified'])

    def test_shape_observer_does_not_mutate_input(self):
        frame=bright_frame();before=frame.copy();self.verify(frame)
        np.testing.assert_array_equal(frame,before)

    def test_exclusions_accept_owned_readonly_pixels(self):
        source = bright_frame()
        frame = np.frombuffer(source.tobytes(), dtype=np.uint8).reshape(source.shape)
        self.assertFalse(frame.flags.writeable)
        result = observe_s1_map_ui_geometry(frame, excluded_bounds=((1630, 200, 1730, 600),))
        self.assertTrue(result['verified'], result)
        np.testing.assert_array_equal(frame, source)

    def test_bad_type_size_and_uniform_frames_fail_closed(self):
        for f in (None,[],np.zeros((300,300,3),np.uint8),np.zeros((1200,1920),np.uint8),
                  np.zeros((1200,1920,3),np.float32),np.full((1200,1920,3),150,np.uint8)):
            self.assertFalse(self.verify(f)['verified'])


if __name__=='__main__':unittest.main()
