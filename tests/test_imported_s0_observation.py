"""End-to-end S0 consumption of one packaged controls profile.

Pixel fixtures are drawn independently from the profile's NPY vectors. The
package helper builds a real PackageView and the profile decoder reads its
declared bytes through the normal resource path.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from floor_control_region import locate_vertical_floor_controls
from imported_controls_profile import S0ControlsProfile
from s0_vertical_controls import (bind_control_geometry, control_hit,
                                  observe_s0_map, validated_control_rects)
from test_imported_controls_profile import ProfilePackage
from test_s0_map_geometry_proof import s0_bright
from s0_floor_templates import LABELS, _bank


def _draw_column(frame, mode, order, selected, *, center_x=1620, top=760,
                 pitch=60, broken=None):
    """Draw labels/highlights from independent numeric truth, not a read result."""
    for index, floor in enumerate(order):
        x, y = center_x, top + index * pitch
        if floor == selected:
            frame[y - 32:y + 32, x - 30:x + 30] = 110
        color = (15,) * 3 if floor == selected else (225,) * 3
        label = 3 if broken == ("wrong-glyph", floor) else abs(floor)
        if label == 1:
            cv2.rectangle(frame, (x - 2, y - 10), (x + 1, y + 9), color, -1)
        elif label == 2:
            cv2.polylines(frame, [np.array([(x - 6, y - 10), (x + 6, y - 10),
                (x + 6, y - 2), (x - 6, y + 4), (x - 6, y + 9), (x + 6, y + 9)])],
                False, color, 2, cv2.LINE_8)
        else:
            cv2.putText(frame, str(label), (x - 7, y + 10), cv2.FONT_HERSHEY_PLAIN,
                        .9, color, 2, cv2.LINE_8)
        if floor < 0 and broken != ("missing-sign", floor):
            cv2.rectangle(frame, (x - 13, y - 1), (x - 6, y), color, -1)
    return frame


def _board(mode, order, selected, *, center_x=1620, top=760, pitch=60,
           broken=None, second=None):
    frame = s0_bright(mode, selected)
    # Remove the legacy-column pixels from the independent canvas fixture so
    # neither the old fixed search nor its broad fallback can provide success.
    frame[200:700, 1570:1740] = 33
    frame[:200, 1570:1670] = 33
    # Make room for relocated controls without drawing truth from a solver.
    frame[max(0, top - 48):min(frame.shape[0], top + pitch * len(order) + 50),
          max(0, center_x - 55):min(frame.shape[1], center_x + 56)] = 33
    _draw_column(frame, mode, order, selected, center_x=center_x, top=top,
                 pitch=pitch, broken=broken)
    if second is not None:
        _draw_column(frame, mode, order, second, center_x=center_x + 100,
                     top=top, pitch=pitch)
    return frame


class ImportedS0ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def profile(self, *, orders=None, ids=None, fixed=None, expanded=None,
                modes=("nightmare", "difficult")):
        vectors, labels = _bank()
        labels = [int(value) for value in labels]
        document = deepcopy(ProfilePackage.document(labels))
        orders = orders or {"nightmare": (2, 1, -1), "difficult": (2, 1)}
        ids = ids or {}
        for mode in modes:
            context = document["contexts"][f"s0:{mode}"]
            context["visualOrder"] = list(orders[mode])
            mapping = ids.get(mode, {})
            for row in context["floors"]:
                row["floorId"] = mapping.get(row["observedValue"], row["floorId"])
        for mode in ("nightmare", "difficult"):
            if mode not in modes:
                document["contexts"].pop(f"s0:{mode}")
        if fixed is not None:
            document["fixedSearchRegion"] = list(fixed)
        if expanded is not None:
            document["expandedSearchRegion"] = list(expanded)
        component_contexts = [{'season': 's0', 'mode': mode} for mode in modes]
        package = ProfilePackage(Path(self.temp.name) / f"pkg-{len(list(Path(self.temp.name).glob('pkg-*')))}",
                                 entrypoint=document, vectors=vectors, labels=labels,
                                 component_contexts=component_contexts)
        return package, S0ControlsProfile(package.view)

    def test_packaged_contract_order_templates_and_clicks_drive_both_modes(self):
        orders = {"nightmare": (1, 2, -1), "difficult": (1, 2)}
        ids = {
            "nightmare": {-1: "Basement-X", 1: "First-X", 2: "Second-X"},
            "difficult": {1: "Ground-A", 2: "Upper-A"},
        }
        package, controls = self.profile(orders=orders, ids=ids,
                                        fixed=(.75, .60, .08, .33))
        self.assertIsNotNone(package.view)
        for mode, order in orders.items():
            selected = 1
            frame = _board(mode, order, selected, center_x=1500)
            # This patch catches any silent use of the built-in template bank;
            # pixel detection itself remains real and unmocked.
            with patch("s0_floor_templates._bank",
                       side_effect=AssertionError("built-in bank used")):
                observation, read = observe_s0_map(frame, mode, controls=controls)
            self.assertTrue(observation["detected"], (mode, observation, read))
            self.assertEqual(read["selectedFloor"], selected, read)
            self.assertEqual(read["observedOrder"], list(order), read)
            self.assertEqual(observation["floor"], ids[mode][selected])
            self.assertEqual(read["controlSearchRegion"], list(controls.fixed_search_region))
            self.assertIsNotNone(validated_control_rects(read, controls=controls))
            self.assertIsNone(validated_control_rects(read))
            for floor, (cx, cy) in zip(order, read["centerPoints"]):
                self.assertEqual(control_hit((cx * frame.shape[1], cy * frame.shape[0]),
                                             (0, 0, frame.shape[1], frame.shape[0]),
                                             read, controls=controls), floor)
            self.assertIsNone(control_hit((-1, -1),
                                          (0, 0, frame.shape[1], frame.shape[0]),
                                          read, controls=controls))

    def test_expanded_search_uses_profile_domain_outside_legacy_broad_area(self):
        package, controls = self.profile(
            fixed=(.75, .15, .18, .45), expanded=(.47, .60, .14, .32))
        frame = _board("nightmare", (2, 1, -1), 1, center_x=1000)
        with patch("s0_floor_templates._bank",
                   side_effect=AssertionError("built-in bank used")):
            observation, read = observe_s0_map(frame, "nightmare", controls=controls)
        self.assertTrue(observation["detected"], (observation, read))
        self.assertEqual(read["selectedFloor"], 1, read)
        self.assertEqual(read["floorSource"], "auto-located")
        self.assertEqual(read["controlSearchBounds"], [902, 720, 1172, 1104])
        self.assertIsNotNone(validated_control_rects(read, controls=controls))
        self.assertEqual(package.view.component["type"], "controls-profile")

    def test_user_framed_region_still_overrides_profile_fixed_search(self):
        _package, controls = self.profile(fixed=(.75, .15, .18, .45))
        frame = _board("difficult", (2, 1), 1, center_x=1500)
        settings = {"floorControlMode": "region",
                    "floorControlRegion": {"rect": [.75, .60, .08, .28],
                                            "clientSize": [1920, 1200]}}
        with patch("s0_floor_templates._bank",
                   side_effect=AssertionError("built-in bank used")):
            observation, read = observe_s0_map(frame, "difficult", settings=settings,
                                               controls=controls)
        self.assertTrue(observation["detected"], (observation, read))
        self.assertEqual(read["floorSource"], "region")
        self.assertEqual(read["selectedFloor"], 1, read)
        self.assertEqual(read["regionRead"]["controlSearchRegion"], [.75, .6, .08, .28])

    def test_missing_profile_context_and_s1_never_fall_back_to_static_s0(self):
        _package, controls = self.profile(modes=("nightmare",))
        frame = _board("difficult", (2, 1), 1)
        observation, read = observe_s0_map(frame, "difficult", controls=controls)
        self.assertFalse(observation["detected"])
        self.assertEqual(read["reason"], "controls-profile-context-unavailable")
        observation, read = observe_s0_map(frame, "nightmare", season="s1", controls=controls)
        self.assertFalse(observation["detected"])
        self.assertEqual(read["reason"], "controls-profile-context-unavailable")

    def test_one_observation_reuses_parameters_and_next_observation_rechecks_resources(self):
        _package, controls = self.profile(fixed=(.75, .60, .08, .33))
        for mode, order in (("nightmare", (2, 1, -1)), ("difficult", (2, 1))):
            with self.subTest(mode=mode):
                frame = _board(mode, order, 1, center_x=1500)
                with patch.object(controls, 'assert_current', wraps=controls.assert_current) as current:
                    for count in (2, 4):
                        observation, read = observe_s0_map(frame, mode, controls=controls)
                        self.assertTrue(observation['detected'], (observation, read))
                        self.assertEqual(read['selectedFloor'], 1)
                        self.assertEqual(current.call_count, count)

    def test_changed_manifest_after_pixels_cannot_return_a_map_observation(self):
        import s0_vertical_controls as detector
        package, controls = self.profile(fixed=(.75, .60, .08, .33))
        frame = _board('nightmare', (2, 1, -1), 1, center_x=1500)
        original = detector._observe_s0_map_geometry
        def replace_after_geometry(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(result['detected'], result)
            manifest = package.package_root / 'manifest.json'
            replacement = manifest.with_suffix('.replacement')
            replacement.write_bytes(manifest.read_bytes())
            replacement.replace(manifest)
            return result
        with patch.object(detector, '_observe_s0_map_geometry', side_effect=replace_after_geometry):
            with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
                observe_s0_map(frame, 'nightmare', controls=controls)

    def test_expired_profile_fails_before_pixels_and_saved_clicks_recheck_it(self):
        package, controls = self.profile(fixed=(.75, .60, .08, .33))
        frame = _board('nightmare', (2, 1, -1), 1, center_x=1500)
        observation, read = observe_s0_map(frame, 'nightmare', controls=controls)
        self.assertTrue(observation['detected'], (observation, read))
        manifest = package.package_root / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b' ')
        with patch('floor_control_region.locate_vertical_floor_controls',
                   side_effect=AssertionError('expired profile reached pixels')):
            with self.assertRaisesRegex(ValueError, 'manifest-changed'):
                observe_s0_map(frame, 'nightmare', controls=controls)
        self.assertIsNone(validated_control_rects(read, controls=controls))
        self.assertIsNone(control_hit((1500, 820), (0, 0, 1920, 1200), read, controls=controls))

    def test_external_signed_labels_reject_missing_minus_wrong_glyph_and_ambiguous_columns(self):
        _package, controls = self.profile()
        cases = (
            ("missing-sign", _board("nightmare", (2, 1, -1), 1,
                                     broken=("missing-sign", -1))),
            ("wrong-glyph", _board("nightmare", (2, 1, -1), 1,
                                    broken=("wrong-glyph", 1))),
            ("ambiguous", _board("nightmare", (2, 1, -1), 1, second=-1)),
        )
        for name, frame in cases:
            with self.subTest(case=name):
                with patch("s0_floor_templates._bank",
                           side_effect=AssertionError("built-in bank used")):
                    observation, read = observe_s0_map(frame, "nightmare", controls=controls)
                self.assertFalse(observation["detected"], (observation, read))
                self.assertIsNone(read["selectedFloor"], read)
                self.assertIsNone(validated_control_rects(read, controls=controls))

    def test_profile_bound_click_geometry_rejects_out_of_bounds_mutation(self):
        _package, controls = self.profile(orders={"nightmare": (1, 2, -1),
                                                  "difficult": (1, 2)})
        frame = _board("nightmare", (1, 2, -1), 1)
        with patch("s0_floor_templates._bank",
                   side_effect=AssertionError("built-in bank used")):
            observation, read = observe_s0_map(frame, "nightmare", controls=controls)
        self.assertTrue(observation["detected"], (observation, read))
        corrupted = deepcopy(read)
        corrupted["controlRects"][0]["rect"] = [-.01, .2, .2, .4]
        self.assertIsNone(validated_control_rects(corrupted, controls=controls))
        self.assertIsNone(control_hit((1620, 760), (0, 0, 1920, 1200),
                                     corrupted, controls=controls))


class OriginalS0ControlsLifecycleTests(unittest.TestCase):
    """Current source observer plus real external owner, without P3 parameters.

    ImportedS0ObservationTests above records the retired arbitrary-parameter
    detector. The current observer accepts only the donor controls contract.
    """
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def profile(self):
        import json
        from export_native_base_package import _controls_payloads
        payloads = _controls_payloads('nightmare')
        document = json.loads(payloads['profile.json'])
        document['contexts'].update(json.loads(_controls_payloads('difficult')['profile.json'])['contexts'])
        vectors, labels = _bank()
        package = ProfilePackage(Path(self.temp.name), entrypoint=document,
            vectors=vectors, labels=labels)
        return package, S0ControlsProfile(package.view)

    def test_original_parameters_preserve_independent_pixel_observation_in_both_modes(self):
        from s0_vertical_controls import _observe_s0_map_original
        _, controls = self.profile()
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                frame = s0_bright(mode, 1)
                expected, expected_read = _observe_s0_map_original(frame, mode)
                actual, read = observe_s0_map(frame, mode, controls=controls)
                self.assertTrue(actual['detected'], (actual, read))
                self.assertEqual(actual['floor'], '1')
                self.assertEqual(actual, expected)
                self.assertEqual(read, expected_read)

    def test_changed_manifest_after_original_pixels_rejects_the_result(self):
        import s0_vertical_controls as detector
        package, controls = self.profile()
        original = detector._observe_s0_map_original
        def change_after_pixels(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertTrue(result[0]['detected'], result)
            manifest = package.package_root / 'manifest.json'
            manifest.write_bytes(manifest.read_bytes() + b' ')
            return result
        with patch.object(detector, '_observe_s0_map_original', side_effect=change_after_pixels):
            with self.assertRaisesRegex(ValueError, 'manifest-changed'):
                observe_s0_map(s0_bright('nightmare'), 'nightmare', controls=controls)

    def test_stale_owner_fails_before_original_pixels(self):
        package, controls = self.profile()
        manifest = package.package_root / 'manifest.json'
        manifest.write_bytes(manifest.read_bytes() + b' ')
        with patch('s0_vertical_controls._observe_s0_map_original',
                   side_effect=AssertionError('stale controls reached pixels')):
            with self.assertRaisesRegex(ValueError, 'manifest-changed'):
                observe_s0_map(s0_bright('nightmare'), 'nightmare', controls=controls)

    def test_parameter_or_context_change_cannot_silently_select_another_observer(self):
        _, controls = self.profile()
        with patch('s0_vertical_controls._observe_s0_map_original',
                   side_effect=AssertionError('unqualified controls reached pixels')):
            with self.assertRaisesRegex(ValueError, 'original-controls-floor-context-mismatch'):
                observe_s0_map(s0_bright('nightmare'), 'nightmare', season='s1', controls=controls)
            controls.fixed_search_region = (.1, .1, .2, .2)
            with self.assertRaisesRegex(ValueError, 'original-controls-parameters-not-source-equivalent'):
                observe_s0_map(s0_bright('nightmare'), 'nightmare', controls=controls)


if __name__ == "__main__":
    unittest.main()
