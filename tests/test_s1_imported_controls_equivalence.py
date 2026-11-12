"""Imported controls use the same observer semantics; synthetic pixels are not game proof."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_region_capture import ClientCaptureContext, RegionCapture
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_s1_controls_profile import S1ControlsProfile
import s1_floor_observation as observer
from test_export_s1_native_base_package import S1NativeBaseSourceFixture
from test_imported_s1_controls_profile import ProfilePackage, document
from test_s1_floor_observation import mathematical_frame
from test_s1_preparation_robustness import bright_frame


class SavedPixels:
    """Only OS capture is replaced; the runtime owns map/floor/scan decisions."""
    def __init__(self, frame):
        self.frame, self.enabled = frame, False
        self.generation = 1

    def bindFullClient(self, hwnd):
        pass

    def enable(self):
        self.enabled = True
        return True

    def disable(self):
        self.enabled = False

    shutdown = disable

    def is_current(self, context, *, generation):
        h, w = self.frame.shape[:2]
        return self.enabled and generation == self.generation and context.full_client_size == (w, h)

    def capture(self, cancelled=None):
        assert self.enabled
        h, w = self.frame.shape[:2]
        return RegionCapture(self.frame.copy(), (0, 0, w, h), (0, 0, w, h),
            ClientCaptureContext((w, h), (0, 23, w, h + 23), 96), 'synthetic-s1-controls')


class ImportedObserverEquivalence(unittest.TestCase):
    def profile(self, value=None, guard=None):
        temporary = tempfile.TemporaryDirectory(prefix='s1-controls-equivalence-')
        self.addCleanup(temporary.cleanup)
        return S1ControlsProfile(ProfilePackage(temporary.name,
            document() if value is None else value, guard=guard).view)

    def test_default_fixed_column_does_not_acquire_extra_ui_requirement(self):
        frame = mathematical_frame((-2,))
        controls = self.profile()
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                native = observer.detect_s1_floor(frame, season='s1', mode=mode)
                self.assertEqual(native['selectedFloor'], -2, native)
                imported = observer.detect_s1_floor(frame, season='s1', mode=mode, controls=controls)
                self.assertEqual(imported, native)

    def test_exported_roundtrip_preserves_native_semantics_at_client_aspects(self):
        from export_native_base_package import _s1_controls_payloads
        for mode in ('difficult', 'nightmare'):
            controls = self.profile(json.loads(_s1_controls_payloads(mode)['profile.json']))
            for size in ((1920, 1080), (1440, 1080), (2560, 1080)):
                with self.subTest(mode=mode, size=size):
                    frame = cv2.resize(mathematical_frame((-3,)), size)
                    before = frame.copy()
                    native = observer.detect_s1_floor(frame, season='s1', mode=mode)
                    imported = observer.detect_s1_floor(frame, season='s1', mode=mode, controls=controls)
                    self.assertEqual(imported, native)
                    np.testing.assert_array_equal(frame, before)

    def test_each_changed_parameter_class_and_nearby_endpoints_keep_specific_gaps(self):
        changes = (
            ('fixed-search-region', lambda v: v['fixedSearchRegion'].__setitem__(0, .81)),
            ('expanded-search-region', lambda v: v['expandedSearchRegion'].__setitem__(1, .03)),
            ('map-ui-search', lambda v: v['mapUi']['searchRegions']['close'].__setitem__(1, .03)),
            ('map-ui-sizes', lambda v: v['mapUi']['sizes'].__setitem__(0, .023)),
            ('map-ui-correlation', lambda v: v['mapUi']['minimumCorrelation'].__setitem__('close', .63)),
            # The former literal fixture decimals decode one ULP away from the
            # reviewed endpoints. No rounding/tolerance may qualify them.
            ('expanded-search-region', lambda v: v['expandedSearchRegion'].__setitem__(2, .435)),
            ('map-ui-search', lambda v: v['mapUi']['searchRegions']['plus'].__setitem__(3, .21)),
            ('map-ui-search', lambda v: v['mapUi']['searchRegions']['minus'].__setitem__(3, .20)),
        )
        for field, change in changes:
            value = document()
            change(value)
            controls = self.profile(value)
            with self.subTest(field=field, value=value):
                gap = f's1-controls-{field}-not-source-replay-equivalent'
                self.assertEqual(observer.controls_source_replay_gaps(controls, season='s1', mode='nightmare'), (gap,))
                result = observer.detect_s1_floor(bright_frame((-2,)), season='s1',
                    mode='nightmare', controls=controls)
                self.assertFalse(result['observedFloorValidated'])
                self.assertFalse(result['automaticFloorEligible'])
                self.assertEqual(result['qualificationGaps'], [gap])

    def test_equivalent_invalid_pixels_and_stale_ids_keep_native_failure(self):
        controls = self.profile()
        cases = (None, np.zeros((30, 30, 3), np.uint8), np.zeros((1200, 1920, 3), np.uint8),
            mathematical_frame((-1, -2)), mathematical_frame((), missing_sign=-3))
        for frame in cases:
            native = observer.detect_s1_floor(frame, season='s1', mode='nightmare')
            actual = observer.detect_s1_floor(frame, season='s1', mode='nightmare', controls=controls)
            self.assertEqual(actual, native)
            self.assertIsNone(actual['selectedFloor'])
            self.assertFalse(actual['observedFloorValidated'])
            self.assertFalse(actual['automaticFloorEligible'])
        result = observer.detect_s1_floor(mathematical_frame(), season='s1', mode='nightmare',
            controls=controls, frame_id='old', expected_frame_id='new')
        self.assertEqual(result['reason'], 's1-floor-stale-frame')

    def test_owner_expiring_after_kernel_prevents_returning_qualified_pixels(self):
        alive = [True]
        def guard():
            if not alive[0]:
                raise ValueError('controls-expired-after-kernel')
        controls = self.profile(guard=guard)
        original = observer._detect_s1_floor
        def expire(*args, **kwargs):
            value = original(*args, **kwargs)
            self.assertEqual(value['selectedFloor'], -2)
            alive[0] = False
            return value
        with patch.object(observer, '_detect_s1_floor', side_effect=expire):
            with self.assertRaisesRegex(ValueError, 'controls-expired-after-kernel'):
                observer.detect_s1_floor(mathematical_frame((-2,)), season='s1',
                    mode='nightmare', controls=controls)


class ImportedMapEntry(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s1-equivalent-map-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.sources = {}
        for mode in ('difficult', 'nightmare'):
            source = S1NativeBaseSourceFixture(Path(temporary.name) / mode, mode=mode)
            source.export()
            cls.sources[mode] = source

    def assert_map_entry(self, mode):
        from client_imported_activation import activate_context
        from resource_transaction import resource_runtime_lease
        temporary = tempfile.TemporaryDirectory(prefix='s1-equivalent-map-runtime-')
        self.addCleanup(temporary.cleanup)
        paths = RuntimePaths(Path(temporary.name), False)
        resolver = ResourceResolver(paths.imported_resource_root,
            supported_capabilities=SUPPORTED_CAPABILITIES)
        saved = save_archive(self.sources[mode].output, resolver.root)
        refs = {ref.component_id: ref for ref in saved.component_refs}
        with resource_runtime_lease(resolver.root):
            activate_context(resolver, paths, refs['base'], refs['controls'], season='s1', mode=mode)
        factories = build_imported_engine_factories(resolver, paths)
        engine = factories.get_engine(mode, 's1')
        from s1_candidate_purpose import _NATIVE_S1_PREVIEW
        for evidence, purpose in (('recorded', None), ('live-client-capture', _NATIVE_S1_PREVIEW)):
            with self.subTest(mode=mode, evidence=evidence):
                runtime = LiveRuntime(engine_factories=factories,
                    capture_provider=SavedPixels(mathematical_frame((-2,))),
                    evidence_kind=evidence, s1_candidate_purpose=purpose)
                self.addCleanup(runtime.shutdown)
                self.assertTrue(runtime.dispatch('set_context', dict(season='s1', mode=mode))['success'])
                started = runtime.dispatch('start_round')
                self.assertTrue(started['success'], started)
                events = []
                runtime.set_event_callback(events.append)
                result = runtime.dispatch('map', dict(openIntent=True, inputSequence=1))
                state = runtime.snapshot()
                self.assertEqual(state['floor'], '-2', result)
                self.assertTrue(any(row['event'] == 'structure-scan-started' for row in events), result)
                read = runtime._last_observation['floor_read']
                self.assertEqual(read['selectedFloor'], -2)
                self.assertTrue(read['observedFloorValidated'])
                self.assertTrue(read['automaticFloorEligible'])
                self.assertFalse(read['controlGeometryVerified'])
                # Tiny complete source fixtures deliberately cannot fit this large map.
                self.assertFalse(state['identityLocked'])
                self.assertFalse(state['automaticIdentityAvailable'])
                self.assertFalse(state['liveEngineAvailable'])
                self.assertFalse(engine.qt_live_ready)
                self.assertFalse(state['displayAuthorized'])
                runtime.shutdown()
        ordinary = LiveRuntime(engine_factories=factories,
            capture_provider=SavedPixels(mathematical_frame((-2,))), evidence_kind='live-client-capture')
        self.addCleanup(ordinary.shutdown)
        self.assertTrue(ordinary.dispatch('set_context', dict(season='s1', mode=mode))['success'])
        rejected = ordinary.dispatch('start_round')
        self.assertFalse(rejected['success'], rejected)
        self.assertTrue(rejected['unavailable'])

    def test_difficult_recorded_start_and_map_use_current_pixels(self):
        self.assert_map_entry('difficult')

    def test_nightmare_recorded_start_and_map_use_current_pixels(self):
        self.assert_map_entry('nightmare')


@unittest.skipUnless(os.getenv('S1_IMPORTED_RUNTIME_ROOT'), 'installed controls replay root not configured')
class InstalledDefaultControls(unittest.TestCase):
    def test_selected_installed_profiles_preserve_default_pixel_observation(self):
        root = Path(os.environ['S1_IMPORTED_RUNTIME_ROOT'])
        for mode in ('difficult', 'nightmare'):
            paths = RuntimePaths(root / ('integrated-runtime-s1-' + mode), False)
            resolver = ResourceResolver(paths.imported_resource_root,
                supported_capabilities=SUPPORTED_CAPABILITIES)
            resources = resolver.snapshot().resolve_context('s1', mode)
            controls = S1ControlsProfile(resources.controls)
            frame = mathematical_frame((-2,))
            with self.subTest(mode=mode):
                native = observer.detect_s1_floor(frame, season='s1', mode=mode)
                actual = observer.detect_s1_floor(frame, season='s1', mode=mode, controls=controls)
                self.assertEqual(actual, native)


if __name__ == '__main__':
    unittest.main()
