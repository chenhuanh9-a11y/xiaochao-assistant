"""Reviewed S0 identity and fail-closed future-season reader boundary."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from floor_contract import floor_contract
from floor_control_region import (MODE_FLOORS, FLOOR_NAMES, apply_floor_region,
                                  locate_floor_row, read_floor_in_region)


class FloorContractTests(unittest.TestCase):
    def test_exact_existing_order_and_bidirectional_identity(self):
        for mode, observed, ids in (("nightmare", (-1, 1, 2), ("B1", "1", "2")),
                                    ("difficult", (1, 2), ("1", "2"))):
            contract = floor_contract(mode)
            self.assertEqual((contract.season, contract.mode, contract.control_axis),
                             ("s0", mode, "horizontal"))
            self.assertEqual(contract.observed_values, observed)
            self.assertEqual(contract.floor_ids, ids)
            for value, floor_id in zip(observed, ids):
                self.assertEqual(contract.floor_id_for(value), floor_id)
                self.assertEqual(contract.observed_value_for(floor_id), value)
        self.assertIsNone(floor_contract("difficult").floor_id_for(-1))

    def test_unregistered_season_never_inherits_s0(self):
        for season in ("s2", "", None, [], "S0"):
            for mode in ("nightmare", "difficult"):
                self.assertIsNone(floor_contract(mode, season=season))
        for mode in ("", "other", None, []):
            self.assertIsNone(floor_contract(mode))

    def test_floor_conversion_rejects_slot_numbers_and_coercion(self):
        contract = floor_contract("nightmare")
        for value in (0, -2, -3, -4, 3, True, 1.0, "1", None):
            self.assertIsNone(contract.floor_id_for(value))
        for value in ("-1", "F1", "B2", 1, None):
            self.assertIsNone(contract.observed_value_for(value))

    def test_reviewed_contract_and_floor_definitions_are_immutable(self):
        contract = floor_contract("nightmare")
        with self.assertRaises(FrozenInstanceError):
            contract.season = "s1"
        with self.assertRaises(FrozenInstanceError):
            contract.floors[0].floor_id = "-1"
        self.assertIsInstance(contract.floors, tuple)

    def test_legacy_exports_preserve_values_not_registration_authority(self):
        self.assertEqual(MODE_FLOORS, {"nightmare": (-1, 1, 2), "difficult": (1, 2)})
        self.assertEqual(FLOOR_NAMES, {-1: "B1", 1: "1", 2: "2"})
        with patch.dict(MODE_FLOORS, nightmare=(-4, -3, -2, -1)):
            self.assertEqual(floor_contract("nightmare").observed_values, (-1, 1, 2))

    def test_readers_reject_s1_before_inspecting_pixels(self):
        for mode in ("nightmare", "difficult"):
            for read in (lambda: read_floor_in_region(None, None, mode, season="s1"),
                         lambda: locate_floor_row(None, mode, season="s1")):
                result = read()
                self.assertIsNone(result["selectedFloor"])
                self.assertEqual(result["reason"], "vertical-floor-detector-unvalidated")

    def test_future_settings_discard_stale_positive_fixed_reading_in_all_modes(self):
        stale = dict(selectedFloor=2, centers=[10, 20], confidence=1., layout="difficult-two-buttons")
        for mode in ("nightmare", "difficult"):
            for choice in ("fixed", "auto", "region"):
                for season in ("s1", "s2", "", None):
                    with self.subTest(mode=mode, choice=choice, season=season):
                        result = apply_floor_region(dict(gameSeason=season, floorControlMode=choice), mode, None, stale)
                        self.assertIsNone(result["selectedFloor"])
                        self.assertEqual(result["floorSource"], "unavailable")
                        self.assertNotIn("centers", result)
        self.assertEqual(stale["selectedFloor"], 2)

    def test_s0_fixed_reading_unchanged_with_explicit_or_legacy_settings(self):
        reading = dict(selectedFloor=-1, confidence=.8, centers=[100, 150, 200])
        for settings in (dict(floorControlMode="fixed"), dict(gameSeason="s0", floorControlMode="fixed")):
            self.assertEqual(apply_floor_region(settings, "nightmare", None, reading),
                             dict(reading, floorSource="fixed"))


if __name__ == "__main__":
    unittest.main()
