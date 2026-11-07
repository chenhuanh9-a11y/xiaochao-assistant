"""Floor ROI cannot affect external UI geometry, including preprocessing.

The noisy shifted-UI fixture reproduces the independent review's v2 boolean
flip. These are synthetic contracts, not real gameplay or source-image proof.
"""
import unittest
import cv2
import numpy as np

from s1_map_ui_geometry import observe_s1_map_ui_geometry as geometry
from s1_floor_observation import detect_s1_floor
from test_s1_preparation_robustness import bright_frame


def shifted(shift):
    src=bright_frame();frame=src.copy();frame[:,1770:1860]=40
    frame[:,1770-shift:1860-shift]=src[:,1770:1860]
    return frame


class S1MapUiPixelIndependence(unittest.TestCase):
    def test_all_floor_pixels_arbitrarily_change_without_any_geometry_change(self):
        rng=np.random.default_rng(20261105)
        for shift in (0,18,23,27,37):
            for size in ((1920,1200),(1280,800)):
                frame=cv2.resize(shifted(shift),size);height,width=frame.shape[:2]
                x0,x1,y0,y1=round(width*.80),round(width*.92),round(height*.12),round(height*.56)
                reference=geometry(frame)
                for pattern in ('black','white','gray','random','checker','edge-strip'):
                    with self.subTest(shift=shift,size=size,pattern=pattern):
                        changed=frame.copy();roi=changed[y0:y1,x0:x1]
                        if pattern=='random':roi[:]=rng.integers(0,256,roi.shape,dtype=np.uint8)
                        elif pattern=='checker':
                            yy,xx=np.indices(roi.shape[:2]);roi[:]=(((xx+yy)%2)*255)[:,:,None]
                        elif pattern=='edge-strip':roi[:,-5:]=255
                        else:roi[:]=dict(black=0,white=255,gray=40)[pattern]
                        np.testing.assert_array_equal(frame[:,x1:],changed[:,x1:])
                        # Scores, accepted flag, reason, bounds, local contrasts
                        # and rail must all be identical, not just final floor.
                        self.assertEqual(geometry(changed),reference)

    def test_reviewers_noisy_boundary_case_has_no_geometry_or_map_boolean_flip(self):
        frame=shifted(27)
        rng=np.random.default_rng(20261105);noise=rng.normal(0,1,(80,80,1))
        for sigma in (0,20,40):
            base=frame.copy()
            base[410:490,1766:1846]=np.clip(base[410:490,1766:1846].astype(float)+noise*sigma,0,255).astype(np.uint8)
            results=[]
            for value in (0,40,255):
                changed=base.copy();changed[144:672,1761:1766]=value
                ui=geometry(changed);out=detect_s1_floor(changed,season='s1',mode='nightmare')
                results.append((ui,out['mapOpen'],out['selectedFloor']))
            with self.subTest(sigma=sigma):
                self.assertEqual(results[0],results[1]);self.assertEqual(results[0],results[2])


if __name__=='__main__':unittest.main()
