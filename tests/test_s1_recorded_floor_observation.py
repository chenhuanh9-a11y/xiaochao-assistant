"""Synthetic contracts plus opt-in private saved-frame replay (no raw assets)."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).parents[1] / 'app'))
from s1_floor_observation import detect_s1_floor
from s1_native_resources import S1_RECORDED_CANDIDATE_CAPABILITY as S1_FLOOR_DETECTOR_CAPABILITY


def synthetic(selected=-1):
    # A mathematical glyph/column fixture, NOT real B3/B4 evidence.
    frame = np.full((1200,1920,3),40,np.uint8)
    for i in range(4):
        y=264+i*86
        color=180
        if -1-i == selected:
            frame[y-40:y+40,1650:1714]=150
            color=30
        frame[y:y+2,1669:1681]=color
        if i == 0:
            frame[y-10:y+12,1687:1692]=color
        else:
            # Outline rectangles give tall connected glyph-like components.
            cv2.rectangle(frame,(1684,y-10),(1694,y+11),(color,)*3,2)
    return frame


class S1FloorContracts(unittest.TestCase):
    def read(self, frame, **kwargs):
        return detect_s1_floor(frame,season=kwargs.pop('season','s1'),
                               mode=kwargs.pop('mode','nightmare'),**kwargs)

    @patch('s1_floor_observation.detect_map_interface',return_value={'detected':True})
    def test_synthetic_all_four_positions_only(self,_):
        for floor in (-1,-2,-3,-4):
            result=self.read(synthetic(floor))
            self.assertEqual(result['selectedFloor'],floor,result)
            # The flag registers this floor's detector evidence, not the
            # provenance or freshness of this synthetic input frame.
            self.assertTrue(result['observedFloorValidated'])

    @patch('s1_floor_observation.detect_map_interface',return_value={'detected':True})
    def test_incomplete_or_malformed_column_rejected(self,_):
        for mutation in ('missing-sign','missing-row','uneven','two-highlights','no-highlight'):
            frame=synthetic()
            if mutation=='missing-sign': frame[436:440,1669:1681]=40
            elif mutation=='missing-row': frame[480:570,1650:1715]=40
            elif mutation=='uneven':
                frame[500:580,1650:1715]=frame[484:564,1650:1715].copy()
            elif mutation=='two-highlights':
                other=synthetic(-2);frame[310:390,1650:1715]=other[310:390,1650:1715]
            else: frame=synthetic(0)
            self.assertIsNone(self.read(frame)['selectedFloor'],mutation)

    @patch('s1_floor_observation.detect_map_interface',return_value={'detected':False})
    def test_column_alone_cannot_authorize_map(self,_):
        self.assertIsNone(self.read(synthetic())['selectedFloor'])
        self.assertFalse(self.read(synthetic())['mapOpen'])

    @patch('s1_floor_observation.detect_map_interface')
    def test_context_and_stale_reject_before_pixels(self,gate):
        for kw in ({'season':'s0'},{'season':None},{'mode':'unknown'},
                   {'frame_id':1,'expected_frame_id':2},{'expected_frame_id':2}):
            self.assertIsNone(self.read(synthetic(),**kw)['selectedFloor'])
        gate.assert_not_called()

    def test_invalid_and_uniform_inputs(self):
        for frame in (None,np.zeros((4,4),np.uint8),np.zeros((600,1000,4),np.uint8),
                      np.zeros((1200,1920,3),np.float32),np.full((1200,1920,3),128,np.uint8)):
            self.assertIsNone(self.read(frame)['selectedFloor'])

    def test_capability_is_not_a_live_ready_flag(self):
        self.assertTrue(S1_FLOOR_DETECTOR_CAPABILITY['candidateReady'])
        self.assertFalse(S1_FLOOR_DETECTOR_CAPABILITY['liveValidated'])
        self.assertEqual(S1_FLOOR_DETECTOR_CAPABILITY['observedFloors'],(-1,-2,-3,-4))


@unittest.skipUnless(os.getenv('S1_FLOOR_FIXTURE_ROOT'),'private screenshot replay not configured')
class S1SavedFrames(unittest.TestCase):
    def test_real_originals_and_client_crops(self):
        root=Path(os.environ['S1_FLOOR_FIXTURE_ROOT'])
        cases=[('screenshot_inspection',n,f) for n,f in (
            ('024307',-1),('024502',-1),('024533',-2),('024308',None),
            ('030018',None),('030406',None))]
        cases += [('s1-real-alignment-20261105/inputs',n,-1) for n in ('033924','034225')]
        for directory,name,expected in cases:
            frame=cv2.imread(str(root/directory/f'image(20261105-{name}).png'))
            self.assertIsNotNone(frame,name)
            for image in (frame,frame[23:],cv2.resize(frame,(1280,800))):
                with self.subTest(name=name,shape=image.shape):
                    result=detect_s1_floor(image,season='s1',mode='nightmare',frame_id=name,expected_frame_id=name)
                    self.assertEqual(result['selectedFloor'],expected,result)
                    if name=='024308': self.assertFalse(result['mapOpen'])

if __name__=='__main__': unittest.main()
