"""Complete-column contradictions survive exposure and search-domain changes."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import floor_control_region as detector
from s0_vertical_controls import observe_s0_map, validated_control_rects
from test_client_floor_region import column, ORDERS
from test_s0_bright_controls import bright_column
from test_s0_map_geometry_proof import s0_bright


def panel_frame(mode, panels=('bright', 'dark'), *, tall=False, bottom=700):
    """Invented complete map UI with independent local bright/dark columns."""
    order = dict(ORDERS)[mode]
    frame = s0_bright(mode)
    frame[260:700, 1056:1400] = 33
    frame[:700, 1440:1750] = 33
    bright = bright_column(order, 1)
    dark = column(order, 2)
    if tall:
        bright[:, 1450:1550] = dark[:, 1570:1670]
        frame[180:bottom, 1440:1785] = bright[180:bottom, 1440:1785]
    else:
        if 'bright' in panels:
            frame[280:560, 1570:1670] = bright[280:560, 1570:1670]
        if 'dark' in panels:
            frame[280:560, 1450:1550] = dark[280:560, 1570:1670]
    return frame


def settings_for(choice, size=(1920, 1200), rect=None):
    return dict(floorControlMode=choice, floorControlRegion=dict(
        clientSize=list(size), rect=list(rect or detector.VERTICAL_SEARCH_AREA)))


class S0FloorAmbiguity(unittest.TestCase):
    def check(self, frame, mode, expected, settings=None):
        before = frame.copy()
        observation, read = observe_s0_map(frame, mode, settings=settings)
        self.assertEqual((observation['mapOpen'], observation.get('floor'),
            read['selectedFloor'], read['controlGeometryVerified'],
            validated_control_rects(read) is not None),
            (expected is not None, str(expected) if expected is not None else None,
             expected, expected is not None, expected is not None), (observation, read))
        np.testing.assert_array_equal(frame, before)
        return read

    def test_explicit_default_ambiguity_cannot_start_expanded_search(self):
        for mode, _ in ORDERS:
            frame = panel_frame(mode, tall=True)
            current = detector.locate_vertical_floor_controls(frame, mode)
            self.assertEqual(current['reason'], 'columns-ambiguous')
            with patch.object(detector, 'locate_vertical_floor_controls',
                              side_effect=AssertionError('explicit contradiction is terminal')):
                read = detector.apply_floor_region({}, mode, frame, current)
            self.assertEqual(read['reason'], 'columns-ambiguous')
            self.assertIsNone(read['selectedFloor'])

    def test_ambiguous_saved_region_cannot_fall_back_to_positive_default(self):
        for mode, _ in ORDERS:
            frame = panel_frame(mode, tall=True)
            dark = detector.read_floor_in_region(frame, [1450/1920,280/1200,100/1920,280/1200], mode)
            self.assertEqual(dark['selectedFloor'], 2)
            read = detector.apply_floor_region(settings_for('region'), mode, frame, dark)
            self.assertEqual(read['reason'], 'columns-ambiguous')
            self.assertEqual(read['floorSource'], 'region')
            self.assertIsNone(read['selectedFloor'])

    def test_broad_panel_conflict_survives_domain_height_and_scale(self):
        for mode, _ in ORDERS:
            for bottom in (560, 600, 650, 700, 760, 820):
                for size in ((1920,1200), (1280,800)):
                    frame = cv2.resize(panel_frame(mode, tall=True, bottom=bottom),
                                       size, interpolation=cv2.INTER_NEAREST)
                    for expanded in (False, True):
                        self.assertEqual(detector.locate_vertical_floor_controls(
                            frame, mode, expanded=expanded)['reason'], 'columns-ambiguous')
                    for choice in ('fixed', 'auto', 'region'):
                        with self.subTest(mode=mode, bottom=bottom, size=size, choice=choice):
                            self.check(frame, mode, None, settings_for(choice, size))

    def test_compact_bright_dark_and_conflicting_columns_are_distinguished(self):
        for mode, _ in ORDERS:
            for panels, expected in ((('bright',),1), (('dark',),2), (('bright','dark'),None)):
                frame = panel_frame(mode, panels)
                gray, _ = detector._crop(frame, detector.VERTICAL_SEARCH_AREA)
                # The complete local bright column need not dominate the ROI.
                self.assertLessEqual(float(np.percentile(gray[::2,::2],75)), 62)
                for choice in ('fixed','auto','region'):
                    with self.subTest(mode=mode, panels=panels, choice=choice):
                        self.check(frame, mode, expected, settings_for(choice))

    def test_valid_saved_single_column_retains_priority_over_default_conflict(self):
        for mode, _ in ORDERS:
            for tall in (False, True):
                frame = panel_frame(mode, tall=tall)
                self.assertEqual(detector.locate_vertical_floor_controls(frame,mode)['reason'],
                                 'columns-ambiguous')
                for x, expected in ((1570,1), (1450,2)):
                    rect = [x/1920,280/1200,100/1920,280/1200]
                    read = self.check(frame,mode,expected,settings_for('region',rect=rect))
                    self.assertEqual(read['floorSource'],'region')

    def test_true_absence_and_empty_saved_region_still_expand(self):
        for mode, _ in ORDERS:
            frame = panel_frame(mode, ('bright',))
            frame[280:560,1170:1270] = frame[280:560,1570:1670]
            frame[280:560,1570:1670] = 33
            fixed = detector.locate_vertical_floor_controls(frame,mode)
            self.assertIsNone(fixed['selectedFloor'])
            self.assertNotEqual(fixed['reason'],'columns-ambiguous')
            self.check(frame,mode,None,settings_for('fixed'))
            self.check(frame,mode,1,settings_for('auto'))
            read = self.check(frame,mode,1,settings_for('region',rect=[.56,.04,.04,.06]))
            self.assertEqual(read['floorSource'],'auto-located')


if __name__ == '__main__':
    unittest.main()
