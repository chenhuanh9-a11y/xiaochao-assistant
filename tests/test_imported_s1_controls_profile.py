"""Actual package decoding plus independently drawn pixels; no game proof."""
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import imported_resource_packages as packages
from imported_s1_controls_profile import DETECTOR_ID, SCHEMA, S1ControlsProfile
import s1_floor_observation as detector
from test_s1_floor_observation import draw_column, mathematical_frame
from test_s1_preparation_robustness import bright_frame


def document(modes=('difficult', 'nightmare')):
    # Literal reviewed data, independent of the production parameter constants.
    return dict(schema=SCHEMA, detectorId=DETECTOR_ID,
        contexts={f's1:{mode}': dict(
            floors=[dict(observedValue=value, floorId=str(value)) for value in (-1, -2, -3, -4)],
            visualOrder=[-1, -2, -3, -4]) for mode in modes},
        fixedSearchRegion=[.80, .12, .12, .44],
        expandedSearchRegion=[.55, .04, .435, .89],
        mapUi=dict(searchRegions=dict(close=[.92, .02, .07, .16],
            plus=[.92, .25, .07, .21], minus=[.92, .66, .07, .20]),
            sizes=[.024, .026, .028, .030, .032, .034, .036, .038],
            minimumCorrelation=dict(close=.62, plus=.65, minus=.65)))


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


class ProfilePackage:
    def __init__(self, root, value, *, contexts=None, guard=None):
        self.root = Path(root)
        self.package_root = self.root / 'packages' / 's1-controls-test' / 'v1'
        data_root = self.package_root / 'data' / 'controls'
        data_root.mkdir(parents=True)
        self.payload_path = data_root / 'profile.json'
        raw = encoded(value)
        self.payload_path.write_bytes(raw)
        contexts = ([dict(season='s1', mode=key.split(':')[1]) for key in value['contexts']]
                    if contexts is None else contexts)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='s1-controls-test', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='controls', type='controls-profile', schemaVersion=1,
                contexts=contexts, entrypoint='profile.json', dependencies={})],
            files=[dict(componentId='controls', path='profile.json', bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())])
        self.manifest_path = self.package_root / 'manifest.json'
        manifest_raw = encoded(manifest)
        self.manifest_path.write_bytes(manifest_raw)
        ref = packages.ComponentRef('s1-controls-test', 'v1', 'controls',
                                    hashlib.sha256(manifest_raw).hexdigest())
        self.view = packages.PackageView(self.root, ref, assert_current=guard)


class ImportedS1Controls(unittest.TestCase):
    def package(self, value=None, **kwargs):
        temporary = tempfile.TemporaryDirectory(prefix='s1-controls-test-')
        self.addCleanup(temporary.cleanup)
        return ProfilePackage(temporary.name, document() if value is None else value, **kwargs)

    def profile(self, value=None, **kwargs):
        return S1ControlsProfile(self.package(value, **kwargs).view)

    def observe(self, frame, controls, mode='nightmare'):
        return detector.detect_s1_floor(frame, season='s1', mode=mode, controls=controls)

    def test_real_package_decodes_immutable_exact_contexts(self):
        controls = self.profile()
        for mode in ('difficult', 'nightmare'):
            context = controls.for_context('s1', mode)
            self.assertEqual(context.contract.floor_ids, ('-1', '-2', '-3', '-4'))
            self.assertEqual(context.contract.observed_values, (-1, -2, -3, -4))
            self.assertEqual(context.visual_order, (-1, -2, -3, -4))
            with self.assertRaises(FrozenInstanceError):
                context.visual_order = ()
        self.assertIsNone(controls.for_context('s0', 'nightmare'))
        self.assertIsNone(controls.for_context('s1', 'difficulty2'))
        with self.assertRaises(TypeError):
            controls.map_ui.search['close'] = (0, 0, 1, 1)
        with self.assertRaises(TypeError):
            controls.map_ui.minimum_correlation['close'] = 0
        with self.assertRaises(FrozenInstanceError):
            controls.map_ui.sizes = ()

    def test_external_parameters_observe_independent_pixels_in_both_modes(self):
        controls = self.profile()
        before_floors = detector.SOURCE_REPLAY_FLOORS
        for mode in ('difficult', 'nightmare'):
            for floor in (-1, -2, -3, -4):
                with self.subTest(mode=mode, floor=floor):
                    frame = bright_frame((floor,))
                    original = frame.copy()
                    result = self.observe(frame, controls, mode)
                    self.assertEqual(result['selectedFloor'], floor, result)
                    self.assertEqual(result['observedOrder'], [-1, -2, -3, -4])
                    self.assertEqual(result['validationScope'], 'external-controls-unreviewed')
                    self.assertFalse(result['observedFloorValidated'])
                    self.assertFalse(result['automaticFloorEligible'])
                    self.assertTrue(result['mapOpen'])
                    np.testing.assert_array_equal(frame, original)
        self.assertEqual(detector.SOURCE_REPLAY_FLOORS, before_floors)

    def test_fixed_and_expanded_regions_are_both_consumed_without_constant_fallback(self):
        frame = bright_frame((-3,))
        self.assertEqual(self.observe(frame, self.profile())['selectedFloor'], -3)
        fixed_elsewhere = document()
        fixed_elsewhere['fixedSearchRegion'] = [.70, .12, .05, .44]
        # The fixed pass cannot find the drawn column; only the explicitly
        # declared expanded region can recover it through real localization.
        result = self.observe(frame, self.profile(fixed_elsewhere))
        self.assertEqual(result['selectedFloor'], -3, result)
        self.assertEqual(result['localization'], 'signed-column-position-scale')
        fixed_elsewhere['expandedSearchRegion'] = [.70, .12, .05, .44]
        result = self.observe(frame, self.profile(fixed_elsewhere))
        self.assertIsNone(result['selectedFloor'], result)
        self.assertFalse(result['automaticFloorEligible'])

    def test_legal_floor_region_ending_at_frame_edge_fails_closed_without_empty_band_crash(self):
        value = document()
        value['fixedSearchRegion'] = [.8, .12, .2, .44]
        value['expandedSearchRegion'] = [.55, .04, .45, .89]
        result = self.observe(bright_frame(), self.profile(value))
        self.assertIsNone(result['selectedFloor'], result)
        self.assertFalse(result['automaticFloorEligible'])

    def test_shape_search_boundaries_fail_closed_before_pixel_conversion(self):
        from s1_map_ui_geometry import observe_s1_map_ui_geometry
        for edge in (1., float('nan'), float('inf'), -.1, 1.1, 10 ** 1000, True):
            with self.subTest(edge=repr(edge)):
                result = observe_s1_map_ui_geometry(bright_frame(), floor_search_right=edge)
                self.assertFalse(result['verified'], result)

    def test_external_fixed_column_far_from_hud_cannot_borrow_independent_ui(self):
        value = document()
        value['fixedSearchRegion'] = [.1, .12, .12, .44]
        value['expandedSearchRegion'] = [.1, .04, .885, .89]
        controls = self.profile(value)
        for make_frame in (mathematical_frame, bright_frame):
            with self.subTest(frame=make_frame.__name__):
                frame = make_frame()
                frame[200:600, 1630:1730] = 40
                draw_column(frame, selected=(-3,), offset=-1300)
                trusted = detector.detect_s1_floor(frame, season='s1', mode='nightmare')
                self.assertIsNone(trusted['selectedFloor'], trusted)
                result = self.observe(frame, controls)
                self.assertIsNone(result['selectedFloor'], result)
                self.assertFalse(result['observedFloorValidated'])
                self.assertFalse(result['automaticFloorEligible'])

    def test_external_default_and_changed_parameters_do_not_inherit_source_replay_authority(self):
        for fixed in ([.80, .12, .12, .44], [.82, .10, .10, .50]):
            value = document()
            value['fixedSearchRegion'] = fixed
            result = self.observe(bright_frame((-3,)), self.profile(value))
            self.assertEqual(result['selectedFloor'], -3, result)
            self.assertFalse(result['observedFloorValidated'])
            self.assertFalse(result['automaticFloorEligible'])
            self.assertEqual(result['validationScope'], 'external-controls-unreviewed')
        trusted = detector.detect_s1_floor(bright_frame((-3,)), season='s1', mode='nightmare')
        self.assertTrue(trusted['observedFloorValidated'])
        self.assertTrue(trusted['automaticFloorEligible'])
        self.assertEqual(trusted['validationScope'], 'source-image-replay')

    def test_external_shape_search_sizes_and_thresholds_reach_actual_geometry(self):
        frame = bright_frame((-2,))
        self.assertEqual(self.observe(frame, self.profile())['selectedFloor'], -2)
        cases = []
        changed = document()
        changed['mapUi']['searchRegions']['close'] = [.92, .20, .07, .03]
        cases.append(('search', changed))
        changed = document()
        changed['mapUi']['sizes'] = [.005]
        cases.append(('sizes', changed))
        changed = document()
        changed['mapUi']['minimumCorrelation']['close'] = 1.
        cases.append(('correlation', changed))
        for name, value in cases:
            with self.subTest(name=name):
                result = self.observe(frame, self.profile(value))
                self.assertIsNone(result['selectedFloor'], result)
                self.assertFalse(result['mapOpen'])

    def test_floor_mask_and_shape_parameters_share_the_external_context(self):
        value = document()
        value['fixedSearchRegion'] = [.82, .10, .10, .50]
        controls = self.profile(value)
        frame = bright_frame((-4,))
        original = detector.observe_s1_map_ui_geometry
        masked = []
        def geometry(image, **kwargs):
            self.assertIs(kwargs['parameters'], controls.map_ui)
            self.assertAlmostEqual(kwargs['floor_search_right'], .92)
            # The second corroboration receives an actual localized floor
            # mask. Delegate every solver decision to the real shape observer.
            masked.append(int(image[250:540, 1660:1710].max()) == 0)
            return original(image, **kwargs)
        with patch.object(detector, 'observe_s1_map_ui_geometry', side_effect=geometry):
            result = self.observe(frame, controls)
        self.assertEqual(result['selectedFloor'], -4, result)
        self.assertTrue(any(masked))

    def test_missing_fields_wrong_schema_and_exact_floor_contracts_rejected(self):
        for field in document():
            value = document()
            del value[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                self.profile(value, contexts=[dict(season='s1', mode='nightmare')])
        changes = [
            ('schema', lambda value: value.update(schema='other')),
            ('detector', lambda value: value.update(detectorId='package-owned-detector')),
            ('floor', lambda value: value['contexts']['s1:nightmare']['floors'][0].update(floorId='B1')),
            ('observed', lambda value: value['contexts']['s1:nightmare']['floors'][0].update(observedValue=1)),
            ('bool', lambda value: value['contexts']['s1:nightmare']['floors'][0].update(observedValue=True)),
            ('missing-floor', lambda value: value['contexts']['s1:nightmare']['floors'].pop()),
            ('order', lambda value: value['contexts']['s1:nightmare'].update(visualOrder=[-4, -3, -2, -1])),
            ('context-fields', lambda value: value['contexts']['s1:nightmare'].update(liveValidated=True)),
            ('evidence-authority', lambda value: value.update(sourceReplayFloors=[-1, -2, -3, -4])),
            ('map-ui-field', lambda value: value['mapUi'].pop('sizes')),
            ('missing-shape', lambda value: value['mapUi']['searchRegions'].pop('minus')),
        ]
        for name, change in changes:
            value = document()
            change(value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.profile(value)

    def test_nonfinite_and_bounded_shape_parameters_rejected(self):
        changes = [
            ('nonfinite-region', lambda value: value['fixedSearchRegion'].__setitem__(0, float('nan'))),
            ('nonfinite-size', lambda value: value['mapUi'].update(sizes=[float('inf')])),
            ('nonfinite-correlation', lambda value: value['mapUi']['minimumCorrelation'].update(close=float('inf'))),
            ('huge-sizes', lambda value: value['mapUi'].update(sizes=[.025] * 10000)),
            ('large-template', lambda value: value['mapUi'].update(sizes=[1000000])),
            ('duplicate-sizes', lambda value: value['mapUi'].update(sizes=[.025, .025])),
            ('bool-size', lambda value: value['mapUi'].update(sizes=[True])),
            ('empty-sizes', lambda value: value['mapUi'].update(sizes=[])),
            ('correlation', lambda value: value['mapUi']['minimumCorrelation'].update(close=1.1)),
            ('bounds', lambda value: value.update(fixedSearchRegion=[.9, .1, .2, .4])),
            ('expanded', lambda value: value.update(expandedSearchRegion=[.8, .2, .1, .2])),
        ]
        for name, change in changes:
            value = document()
            change(value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.profile(value)

    def test_context_mismatch_never_uses_default_observer(self):
        value = document(('difficult',))
        controls = self.profile(value)
        with self.assertRaisesRegex(ValueError, 'context-unsupported'):
            self.observe(bright_frame(), controls)
        with self.assertRaisesRegex(ValueError, 'profile-required'):
            self.observe(bright_frame(), object())
        value = document()
        with self.assertRaisesRegex(ValueError, 'contexts-component-mismatch'):
            self.profile(value, contexts=[dict(season='s1', mode='difficult')])
        with self.assertRaisesRegex(ValueError, 'component-context-unsupported'):
            self.profile(value, contexts=[dict(season='s0', mode='difficult')])

    def test_current_view_guard_blocks_cached_parameters_before_and_after_pixels(self):
        alive = [True]
        def guard():
            if not alive[0]:
                raise packages.files.ImportRejected('resource-catalog-superseded')
        controls = self.profile(guard=guard)
        alive[0] = False
        for operation in (lambda: controls.for_context('s1', 'nightmare'),
                          lambda: controls.map_ui, lambda: self.observe(bright_frame(), controls)):
            with self.assertRaisesRegex(ValueError, 'resource-catalog-superseded'):
                operation()
        alive[0] = True
        original = detector._observe_signed_column
        def expire(*args, **kwargs):
            result = original(*args, **kwargs)
            alive[0] = False
            return result
        with patch.object(detector, '_observe_signed_column', side_effect=expire):
            with self.assertRaisesRegex(ValueError, 'resource-catalog-superseded'):
                self.observe(bright_frame(), controls)

    def test_one_observation_reads_parameters_between_two_current_view_checks(self):
        controls = self.profile()
        frame = bright_frame((-2,))
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode), \
                 patch.object(controls, 'assert_current', wraps=controls.assert_current) as current:
                for count in (2, 4):
                    result = self.observe(frame, controls, mode)
                    self.assertEqual(result['selectedFloor'], -2, result)
                    self.assertFalse(result['observedFloorValidated'])
                    self.assertFalse(result['automaticFloorEligible'])
                    self.assertEqual(current.call_count, count)

    def test_package_manifest_and_payload_integrity_are_not_bypassed(self):
        package = self.package()
        package.payload_path.write_bytes(package.payload_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'integrity-mismatch'):
            S1ControlsProfile(package.view)
        package = self.package()
        controls = S1ControlsProfile(package.view)
        package.manifest_path.write_bytes(package.manifest_path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest-changed'):
            self.observe(bright_frame(), controls)


if __name__ == '__main__':
    unittest.main()
