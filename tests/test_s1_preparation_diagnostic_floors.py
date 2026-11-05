"""Preparation metadata keeps S0 and all supported S1 floors, never authority."""
import json
import unittest
from diagnostic_privacy import safe_record, sanitize_log_payload
from client_preparation_diagnostics import PreparationDiagnostics


class PreparationFloorMetadataTests(unittest.TestCase):
    def observation(self, floor, selected):
        return PreparationDiagnostics.observation(
            {'mapOpen': True, 'reason': 'map-interface-detected'}, floor,
            {'selectedFloor': selected, 'reason': 's1-floor-observed'}, True)

    def test_all_supported_signed_floors_survive_preparation_metadata(self):
        for floor, selected in [('B1', -1), ('B2', -2), ('B3', -3), ('B4', -4), ('1', 1), ('2', 2)]:
            with self.subTest(floor=floor):
                row = self.observation(floor, selected)
                self.assertEqual(row['floor'], floor)
                self.assertEqual(row['selectedFloor'], selected)

    def test_unknown_values_and_bool_do_not_enter_finite_diagnostic_schema(self):
        for floor, selected in [('B5', -5), ('private value', 'private value'), (None, None), ('1', True)]:
            with self.subTest(floor=floor, selected=selected):
                row = self.observation(floor, selected)
                if floor != '1':
                    self.assertIsNone(row['floor'])
                self.assertIsNone(row['selectedFloor'])

    def test_s1_floors_survive_existing_disk_and_export_schema(self):
        for floor, selected in [('B2', -2), ('B3', -3), ('B4', -4)]:
            row = {'event': 'map-preparation', 'preparation': {
                'phase': 'observation-result', 'observation': self.observation(floor, selected)}}
            self.assertEqual(safe_record(row), row)
            self.assertEqual(json.loads(sanitize_log_payload((json.dumps(row)+'\n').encode())), row)

    def test_observation_preserves_readiness_and_failure_reason_without_promotion(self):
        row = PreparationDiagnostics.observation(
            {'mapOpen': False, 'reason': 'map-anchors-insufficient'}, None,
            {'selectedFloor': None, 'reason': 's1-floor-map-not-open'}, False)
        self.assertFalse(row['mapDetected'])
        self.assertFalse(row['mapReady'])
        self.assertEqual(row['floorReason'], 's1-floor-map-not-open')
        self.assertIsNone(row['floor'])
        self.assertIsNone(row['selectedFloor'])


if __name__ == '__main__':
    unittest.main()
