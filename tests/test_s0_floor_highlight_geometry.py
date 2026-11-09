"""Soft highlight geometry and opt-in private floor-control frame replay."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

import cv2
import numpy as np

import floor_control_region as detector
from s0_vertical_controls import observe_s0_map, validated_control_rects
from test_s0_map_geometry_proof import s0_bright


def soft_column(mode, selected=2):
    """Independent drawn labels keep their pitch as a soft tile changes size."""
    order = (2, 1, -1) if mode == 'nightmare' else (2, 1)
    frame = s0_bright(mode, selected)
    frame[:800, 1540:1740] = 33
    for index, floor in enumerate(order):
        x, y = 1620, 340 + index * 113
        if floor == selected:
            frame[y-65:y+65, x-49:x+49] = 50
            frame[y-56:y+56, x-42:x+42] = 110
        color = (15,) * 3 if floor == selected else (225,) * 3
        if abs(floor) == 1:
            cv2.rectangle(frame, (x+2, y-14), (x+6, y+15), color, -1)
        else:
            cv2.putText(frame, '2', (x-9, y+14), cv2.FONT_HERSHEY_PLAIN,
                        2.4, color, 2)
        if floor < 0:
            cv2.rectangle(frame, (x-16, y), (x-6, y+2), color, -1)
    return frame


class SoftHighlightGeometry(unittest.TestCase):
    def test_exposure_candidates_locate_the_same_visible_labels(self):
        frame = soft_column('nightmare')
        gray, origin = detector._crop(frame, detector.VERTICAL_SEARCH_AREA)
        reads = [detector._read_vertical(cv2.convertScaleAbs(gray, alpha=gain),
                 detector.MODE_FLOORS['nightmare'], origin, frame.shape[1::-1], 'probe')
                 for gain in (1., 1.5)]
        self.assertEqual([read['selectedFloor'] for read in reads], [2, 2])
        # The fill genuinely grows, while the independently drawn glyphs stay put.
        self.assertNotEqual(reads[0]['tileBounds'][3], reads[1]['tileBounds'][3])
        for read in reads:
            np.testing.assert_allclose(
                [point[1] * frame.shape[0] for point in read['centerPoints']],
                [340, 453.5, 566.5], atol=2)

    def test_complete_observation_keeps_soft_columns_in_both_modes(self):
        for mode, order in detector.MODE_FLOORS.items():
            for selected in order:
                with self.subTest(mode=mode, selected=selected):
                    frame = soft_column(mode, selected)
                    original = frame.copy()
                    observation, read = observe_s0_map(frame, mode)
                    self.assertTrue(observation['mapOpen'], (observation, read))
                    self.assertEqual(read['selectedFloor'], selected, read)
                    self.assertIsNotNone(validated_control_rects(read))
                    np.testing.assert_array_equal(frame, original)


@unittest.skipUnless(os.getenv('S0_FLOOR_CONTROL_FIXTURE_ROOT'),
                     'private user floor-control frames not configured')
class RecordedFloorControls(unittest.TestCase):
    def test_originals_and_client_crops_through_production_observer(self):
        from client_live_runtime import LiveRuntime

        root = Path(os.environ['S0_FLOOR_CONTROL_FIXTURE_ROOT'])
        runtime = LiveRuntime(engine_factories={}, evidence_kind='recorded')
        engine = SimpleNamespace(pack=SimpleNamespace(mode='nightmare'))
        for name, expected, floor_id in [('f2', 2, '2'), ('f1', 1, '1'), ('b1', -1, 'B1')]:
            frame = cv2.imdecode(np.frombuffer((root / (name + '.png')).read_bytes(),
                                              dtype=np.uint8), cv2.IMREAD_COLOR)
            self.assertIsNotNone(frame, name)
            # Original window and its actual 23px title-bar-free client.
            for top in (0, 23):
                with self.subTest(name=name, title_bar_crop=top):
                    image = frame[top:]
                    # The actual runtime observation entry calls the real pixel
                    # detectors. Only engine metadata is supplied; no solver,
                    # capture service or detector is mocked into success.
                    observation, floor, read = runtime._observe(engine, image)
                    self.assertTrue(observation['mapOpen'], (observation, read))
                    self.assertEqual((read['selectedFloor'], observation['floor'], floor),
                                     (expected, floor_id, floor_id), (observation, read))
                    self.assertIsNotNone(validated_control_rects(read))
                    self.assertFalse(runtime.snapshot()['displayAuthorized'])


if __name__ == '__main__':
    unittest.main()
