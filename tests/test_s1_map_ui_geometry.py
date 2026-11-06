"""Synthetic geometry adversaries. Real replay is separate; no game assets here."""
import unittest
import cv2
import numpy as np

from s1_map_ui_geometry import observe_s1_map_ui_geometry
from s1_floor_observation import detect_s1_floor
from test_s1_preparation_robustness import bright_frame


class S1MapUiGeometry(unittest.TestCase):
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

    def test_bad_type_size_and_uniform_frames_fail_closed(self):
        for f in (None,[],np.zeros((300,300,3),np.uint8),np.zeros((1200,1920),np.uint8),
                  np.zeros((1200,1920,3),np.float32),np.full((1200,1920,3),150,np.uint8)):
            self.assertFalse(self.verify(f)['verified'])


if __name__=='__main__':unittest.main()
