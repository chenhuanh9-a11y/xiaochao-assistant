"""8F8B pointer regression; synthetic pixels, no game/hook/physical input."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from client_map_input import OrderedMapInput
from map_close_control import locate_map_close_control, map_close_control_hit
from test_client_game_floor_click import FloorClickContracts


def controls():
    frame = np.full((720, 1280, 3), 25, np.uint8)
    cv2.line(frame, (1190, 34), (1225, 69), (160, 160, 160), 12)
    cv2.line(frame, (1225, 34), (1190, 69), (160, 160, 160), 12)
    cv2.circle(frame, (1210, 152), 26, (160, 160, 160), 4)
    cv2.rectangle(frame, (1199, 140), (1213, 164), (160, 160, 160), 3)
    cv2.arrowedLine(frame, (1225, 152), (1207, 152), (160, 160, 160), 3)
    return frame


class CloseEvidenceTests(unittest.TestCase):
    def test_navigation_is_not_close_and_visible_x_is_close_with_client_origin(self):
        frame = controls()
        self.assertIsNotNone(locate_map_close_control(frame))
        bounds = (100, 200, 1380, 920)
        self.assertTrue(map_close_control_hit(frame, (1307, 252), bounds))
        self.assertFalse(map_close_control_hit(frame, (1310, 352), bounds))
        self.assertFalse(map_close_control_hit(frame, (1310, 300), bounds))
        self.assertFalse(map_close_control_hit(frame, (1307, 252), (0, 0, 1920, 1080)))
        frame[:100] = 25
        self.assertIsNone(locate_map_close_control(frame))
        self.assertFalse(map_close_control_hit(frame, (1307, 252), bounds))

    def test_visual_reconciliation_does_not_override_a_later_escape_or_key(self):
        inputs = OrderedMapInput()
        opening = inputs.press()
        self.assertTrue(inputs.reconcile(False, opening['sequence']))
        self.assertTrue(inputs.press()['openIntent'])
        closed = inputs.close()
        self.assertFalse(inputs.reconcile(True, opening['sequence']))
        self.assertFalse(inputs.open_intent)
        self.assertTrue(inputs.press()['openIntent'])
        self.assertFalse(inputs.reconcile(False, closed['sequence']))


class RuntimeCloseEvidenceTests(unittest.TestCase):
    setUp = FloorClickContracts.setUp
    dispatch = FloorClickContracts.dispatch
    prepare = FloorClickContracts.prepare
    xy = FloorClickContracts.xy
    click = FloorClickContracts.click

    def test_navigation_then_floor_then_escape_then_configured_map_reopen(self):
        self.capture.frame = controls()
        context = self.prepare()
        navigation = self.runtime.observe_game_floor_click(1310, 352, context)
        self.assertEqual(navigation['reason'], 'floor-click-not-a-control')
        self.assertFalse(navigation['closeControlHit'])
        self.assertTrue(self.runtime.snapshot()['mapOpen'])
        self.assertTrue(self.runtime.observe_game_floor_click(1307, 252, context)['closeControlHit'])
        self.assertTrue(self.click(2, context)['success'])
        self.read['selectedFloor'] = 2
        current = self.dispatch('refresh_map')
        self.assertTrue(current['success'], current)
        self.assertEqual(self.runtime.snapshot()['floor'], '2')
        visual = current['mapInputObservation']
        self.assertTrue(self.runtime.map_input_observation_is_current(visual))
        self.dispatch('close_map', inputSequence=1, inputSource='escape')
        self.assertFalse(self.runtime.map_input_observation_is_current(visual))
        self.assertNotIn('closeControlHit', self.runtime.observe_game_floor_click(1307, 252, context))
        self.assertFalse(self.runtime.snapshot()['mapOpen'])
        reopened = self.dispatch('map', inputSequence=2, openIntent=True, inputSource='configured-map-key')
        self.assertTrue(reopened['success'], reopened)
        self.assertTrue(self.runtime.snapshot()['mapOpen'])
        self.assertEqual(reopened['mapInputObservation']['inputSequence'], 2)

    def test_confirmed_visual_close_resynchronizes_and_old_frame_cannot_reopen(self):
        self.prepare()
        current = self.dispatch('refresh_map')
        prior = current['mapInputObservation']
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)), \
                patch('map_interface_observation.detect_selected_floor', return_value=dict(selectedFloor=None)):
            first = self.dispatch('refresh_map')
            self.assertNotIn('mapInputObservation', first)
            closed = self.dispatch('refresh_map')
        self.assertFalse(closed['mapInputObservation']['mapOpen'])
        self.assertTrue(self.runtime.map_input_observation_is_current(closed['mapInputObservation']))
        self.assertFalse(self.runtime.map_input_observation_is_current(prior))
        self.assertFalse(self.runtime.snapshot()['mapOpen'])


if __name__ == '__main__':
    unittest.main()
