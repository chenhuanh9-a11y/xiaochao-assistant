"""Current signed pixels at the default search boundary, not saved game assets."""
import unittest

import cv2
import numpy as np

from floor_control_region import locate_vertical_floor_controls
from s0_vertical_controls import bind_control_geometry, observe_s0_map
from test_client_floor_region import column, ORDERS
from test_s0_map_geometry_proof import s0_bright


class FloorSearchBoundaryTests(unittest.TestCase):
    def test_top_floor_highlight_is_not_cut_by_default_search(self):
        for mode, order in ORDERS:
            for size in ((1920, 1080), (2560, 1417)):
                with self.subTest(mode=mode, size=size):
                    frame = column(order, 2, top=180, size=size)
                    before = frame.copy()
                    read = locate_vertical_floor_controls(frame, mode)
                    bound = bind_control_geometry(read, mode, size, map_open=True)
                    self.assertTrue(bound['controlGeometryVerified'], bound)
                    self.assertEqual(bound['selectedFloor'], 2)
                    self.assertEqual(bound['observedOrder'], list(order))
                    self.assertLess(read['controlSearchRegion'][1], .15)
                    np.testing.assert_array_equal(frame, before)

    def test_bottom_highlight_recovers_the_same_complete_signed_column(self):
        for mode, order in ORDERS:
            with self.subTest(mode=mode):
                frame = column(order, order[-1], top=636 - (len(order)-1)*60)
                read = locate_vertical_floor_controls(frame, mode)
                bound = bind_control_geometry(read, mode, (1920, 1080), map_open=True)
                self.assertEqual(bound['selectedFloor'], order[-1], bound)
                self.assertTrue(bound['controlGeometryVerified'])
                self.assertGreater(sum(read['controlSearchRegion'][1::2]), .6)

    def test_complete_map_proof_still_required_after_recovering_highlight(self):
        for mode, order in ORDERS:
            with self.subTest(mode=mode):
                frame = s0_bright(mode, selected=2)
                source = column(order, 2, top=180, size=(1920, 1200))
                frame[:700, 1580:1660] = source[:700, 1580:1660]
                observation, read = observe_s0_map(frame, mode)
                self.assertTrue(observation['mapOpen'], observation)
                self.assertEqual(read['selectedFloor'], 2, read)
                self.assertTrue(read['controlGeometryVerified'])
                # The same floor column alone cannot open a map.
                observation, read = observe_s0_map(source, mode)
                self.assertFalse(observation['mapOpen'], observation)
                self.assertIsNone(read['selectedFloor'])

    def test_recovery_does_not_supply_missing_or_wrong_signed_labels(self):
        for mode, order in ORDERS:
            with self.subTest(mode=mode):
                frame = column(order, 2, top=180)
                frame[230:250, 1600:1640] = 33
                read = locate_vertical_floor_controls(frame, mode)
                self.assertIsNone(read['selectedFloor'], read)
        frame = column((-1, -2, -3), -1, top=180)
        self.assertIsNone(locate_vertical_floor_controls(frame, 'nightmare')['selectedFloor'])

    def test_padding_cannot_erase_two_contradictory_complete_columns(self):
        frame = s0_bright('nightmare')
        frame[:700, 1440:1780] = 33
        left = column((2, 1, -1), 2, top=208, center_x=1500)
        right = column((2, 1, -1), 1, top=360, center_x=1700)
        frame[:700, 1450:1550] = left[:700, 1450:1550]
        frame[:700, 1650:1750] = right[:700, 1650:1750]
        # Extra ink outside the default search must not erase the fact that
        # its original pixels contained two complete, contradictory columns.
        cv2.rectangle(frame, (1498, 138), (1501, 157), (225, 225, 225), -1)
        read = locate_vertical_floor_controls(frame, 'nightmare')
        self.assertIsNone(read['selectedFloor'], read)
        self.assertEqual(read['reason'], 'columns-ambiguous')
        observation, read = observe_s0_map(frame, 'nightmare')
        self.assertFalse(observation['mapOpen'], observation)
        self.assertFalse(read['controlGeometryVerified'])


if __name__ == '__main__':
    unittest.main()
