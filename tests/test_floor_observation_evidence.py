"""Floor evidence contracts, using production detector without capture or GUI."""
from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).parents[1]/'app'))
from identityv_live_mvp import detect_selected_floor
from session_state_machine import stable_floor_from_observations
class FloorObservationEvidence(unittest.TestCase):
    def test_unknown_latest_never_inherits_prior_floor(self):
        for strong in (False,True):
            self.assertIsNone(stable_floor_from_observations([2,2,2,None],strong_latest=strong))
    def test_weak_valid_latest_is_consumed_without_consensus(self):
        # Document current behavior; this is not evidence that the new floor is correct.
        self.assertEqual(stable_floor_from_observations([2,2,2,1],strong_latest=False),1)
    def test_strong_highlight_does_not_add_latency(self):
        self.assertEqual(stable_floor_from_observations([1],strong_latest=True),1)
    def test_uniform_images_have_no_floor_authority(self):
        for value in (0,50,128,255):
            with self.subTest(value=value):
                read=detect_selected_floor(np.full((720,1280,3),value,np.uint8))
                self.assertIsNone(read['selectedFloor']); self.assertEqual(read['confidence'],0.)
if __name__=='__main__':unittest.main()
