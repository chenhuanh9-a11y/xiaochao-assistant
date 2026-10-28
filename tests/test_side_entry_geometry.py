"""Door coordinates are optional evidence, never a second map identity state."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from difficult_side_entry_identity import MapLocalEntryIdentity
from side_entry_geometry import boundary_matches, load_side_doors, observed_side_boundary


class DoorResourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'references/nightmare-entries'
        self.root.mkdir(parents=True)
        self.door = dict(mapId='map-a', floor='2', layoutId='104001', structureVersionKey='layout/current/2',
                         sourceVersion='current', boundaryCenter=[120, 60], outwardDirection='E')
        self.data = dict(schemaVersion=1, mode='nightmare', profileId='native', entries=[self.door])
        binding = dict(self.door, profile='native', resourcePath='references/nightmare-entries/a.npz')
        (self.root.parent/'nightmare-shared-structure-index.json').write_text(json.dumps(dict(entries=[binding])))
        self.entries = [(dict(mapId='map-a', floor='2', sourceProduct='a.npz',
                              canvas=dict(layout='104001', width=300, height=200)), None, None, None)]

    def load(self):
        (self.root/'side-door-anchors.json').write_text(json.dumps(self.data))
        return load_side_doors(self.root, self.entries, 'nightmare', 'native')

    def test_current_complete_inventory_loads_and_missing_resource_keeps_fallback(self):
        self.assertEqual(len(self.load()[0]), 1)
        (self.root/'side-door-anchors.json').unlink()
        rows, error = load_side_doors(self.root, self.entries, 'nightmare', 'native')
        self.assertEqual(rows, {})
        self.assertTrue(error)

    def test_one_missing_or_duplicate_map_disables_the_whole_filter(self):
        for rows in ([], [self.door, self.door]):
            with self.subTest(rows=rows):
                self.data['entries'] = rows
                self.assertEqual(self.load()[0], {})

    def test_wrong_floor_version_direction_or_canvas_cannot_prune_candidates(self):
        original = copy.deepcopy(self.door)
        for key, value in [('floor', '1'), ('structureVersionKey', 'layout/old/2'),
                           ('sourceVersion', 'old'), ('outwardDirection', 'unknown'),
                           ('boundaryCenter', [-1, 60]), ('boundaryCenter', [300, 60])]:
            with self.subTest(field=key, value=value):
                self.data['entries'] = [dict(original, **{key: value})]
                self.assertEqual(self.load()[0], {})


class CurrentDoorTests(unittest.TestCase):
    def observation(self):
        return dict(reliable=True, icon=dict(score=.8), direction=dict(reliable=True,
            direction='右', localRectangleAccepted=True, rectangleBounds=[10, 20, 110, 60]))

    def test_boundary_is_measured_wall_endpoint_not_icon_center(self):
        self.assertEqual(observed_side_boundary([self.observation()])['center'], [110, 40])

    def test_envelope_and_multiple_exits_do_not_authorize_a_boundary(self):
        observation = self.observation()
        self.assertIsNone(observed_side_boundary([observation, observation]))
        observation['direction']['localRectangleAccepted'] = False
        self.assertIsNone(observed_side_boundary([observation]))

    def test_batch_projection_checks_each_seed_independently(self):
        door = dict(outwardDirection='E', boundaryCenter=[120, 60])
        observed = dict(direction='E', center=[110, 40])
        values = boundary_matches(door, observed, np.array([.5, 1., .5]),
                                  np.array([[50, 10], [-10, -20], [70, 10]]), 60)
        np.testing.assert_array_equal(values, [True, True, False])
        self.assertFalse(boundary_matches(door, dict(observed, direction='W'), .5, [50, 10], 60))

    def test_refined_pose_must_still_obey_current_boundary(self):
        identity = MapLocalEntryIdentity.__new__(MapLocalEntryIdentity)
        row = dict(matrixSourceToClient=[[.5, 0, 70], [0, .5, 10], [0, 0, 1]],
                   sideDoorConstraint=dict(door=dict(outwardDirection='E', boundaryCenter=[120, 60]),
                       observed=dict(direction='E', center=[110, 40]), unit=60))
        result = identity.verify(np.zeros((100, 150, 3), np.uint8), row)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'side-door-boundary-contradiction')
        cross_check = identity.check_entry_evidence(np.zeros((100,150,3),np.uint8),row,
                                                    observation=dict(observations=[]))
        self.assertTrue(cross_check['contradicted'])

    def test_small_icon_search_roi_is_safe_and_returns_full_client_coordinates(self):
        from side_door_recognition import SideDoorRecognizer
        recognizer = SideDoorRecognizer.__new__(SideDoorRecognizer)
        rng = np.random.default_rng(123)
        recognizer.exit_icon = rng.integers(0, 255, (14, 16), dtype=np.uint8)
        frame = np.zeros((200, 400, 3), dtype=np.uint8)
        frame[70:84, 180:196] = recognizer.exit_icon[:, :, None]
        self.assertEqual(recognizer.find_anchors(frame, search_bounds=(180, 70, 182, 72)), [])
        with patch('side_door_recognition.anchor_appearance', return_value={}), \
             patch('side_door_recognition.appearance_ok', return_value=True):
            rows = recognizer.find_anchors(frame, False, search_bounds=(160, 60, 220, 100))
        self.assertEqual((rows[0]['x'], rows[0]['y']), (180, 70))


if __name__ == '__main__':
    unittest.main()
