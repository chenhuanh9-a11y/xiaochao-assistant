"""Original-layout S1 display composition over tiny synthetic imported resources.

Real archive readers, activation, author selection, native display, and resource
owners run here. Synthetic canvases establish display contracts only.
"""
from copy import copy, deepcopy
from pathlib import Path
import unittest
from unittest.mock import PropertyMock, patch

import numpy as np

import client_route_preferences as preferences
from client_live_runtime import LiveRuntime
import shared_structure_display as display
from test_client_display_guard_consumption import OriginalDisplayContextMixin, no_display_resource_io


class S1DisplayRamTests(OriginalDisplayContextMixin, unittest.TestCase):
    def test_snapshot_uses_prepared_floor_metadata_but_worker_still_checks_actual_resources(self):
        from test_client_live_runtime import FixtureCapture
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, factories, _, _, _ = self.original_context('s1', mode)
                capture = FixtureCapture()
                runtime = LiveRuntime(engine_factories=factories, capture_provider=capture,
                    evidence_kind='synthetic')
                self.addCleanup(runtime.shutdown)
                self.assertTrue(runtime.dispatch('set_context', dict(season='s1', mode=mode))['success'])
                self.assertTrue(runtime.dispatch('prepare_manual_alignment')['success'])
                before = runtime.snapshot()
                self.assertTrue(before['automaticFloorObservationAvailable'])
                native = engine.qt_native_context
                # Resource admission is real. Only forbid the guarded property
                # on UI snapshot reads, never on capture/observation/dispatch.
                with patch.object(type(native), 'floor_observation_capability', new_callable=PropertyMock,
                        side_effect=AssertionError('UI snapshot reopened admitted floor resources')):
                    for _ in range(3):
                        self.assertEqual(runtime.snapshot(), before)
                reports = factories.capabilities('s1')
                reports[mode]['floorObservation']['automaticFloors'] = [-1, -2]
                with patch.object(factories, 'capabilities', return_value=reports):
                    unsupported = runtime.snapshot()
                self.assertFalse(unsupported['automaticFloorObservationAvailable'])
                self.assertEqual(unsupported['automaticFloorSupportedFloors'], [])
                self.assertFalse(unsupported['liveAuthorization'])
                # Stale cached UI capability must never authorize actual work.
                self.supersede_catalog(engine)
                rejected = runtime.dispatch('observe_manual_floor')
                self.assertFalse(rejected['success'])
                self.assertTrue(rejected.get('error'), rejected)
                self.assertEqual(capture.count, 0)
                self.assertIsNone(runtime.reference_payload())
                self.assertIsNone(runtime._render)

    def context(self, mode='difficult', *, short_resource_root=False):
        engine, _, _, _, _ = self.original_context('s1', mode, author=True,
            short_resource_root=short_resource_root)
        key = next(iter(engine.qt_shared_provider.versions))
        return engine, key[1], key[3]

    def assert_same_layer(self, before, after):
        self.assertEqual(set(after), set(before))
        for key, value in before.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(after[key], value, err_msg=key)
            elif key == 'resourceIdentity':
                self.assertIs(type(after[key]['path']), type(value['path']))
                self.assertTrue(Path(after[key]['path']).samefile(value['path']))
                self.assert_json_contract({name: item for name, item in after[key].items() if name != 'path'},
                    {name: item for name, item in value.items() if name != 'path'})
            else:
                self.assertEqual(after[key], value, key)

    def assert_json_contract(self, actual, expected):
        self.assertIs(type(actual), type(expected))
        if isinstance(expected, dict):
            self.assertEqual(actual.keys(), expected.keys())
            for key in expected:
                self.assert_json_contract(actual[key], expected[key])
        elif isinstance(expected, list):
            self.assertEqual(len(actual), len(expected))
            for item, original in zip(actual, expected):
                self.assert_json_contract(item, original)
        else:
            self.assertEqual(actual, expected)

    def assert_reference_contract(self, actual, original):
        self.assertTrue(actual.path.samefile(original.path))
        if original.resource_root is None:
            self.assertIsNone(actual.resource_root)
        else:
            self.assertTrue(actual.resource_root.samefile(original.resource_root))
        self.assertEqual(actual.shape, original.shape)
        self.assert_json_contract(actual.metadata, original.metadata)
        self.assert_json_contract(actual.native_wall_segments, original.native_wall_segments)
        for name in ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask'):
            before, after = getattr(original, name), getattr(actual, name)
            if before is None:
                self.assertIsNone(after)
            else:
                np.testing.assert_array_equal(after, before, err_msg=name)

    def test_default_structure_fallback_keeps_actual_native_selection_without_p3_static_cache(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, mid, floor = self.context(mode)
                native = engine.qt_native_context
                with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster, \
                        patch.object(native, 'rendered_display_layer', wraps=native.rendered_display_layer) as rendered:
                    layers = [LiveRuntime._display_layer(engine, mid, floor, '') for _ in range(3)]
                self.assertEqual(raster.call_count, 3,
                    'original S1 uses the actual reference path; P3 static display payloads are absent')
                self.assertEqual(rendered.call_count, 3, 'native source selection and its guard remain real')
                self.assertEqual(layers[0]['preferredProfileId'], '')
                self.assertEqual(layers[0]['effectiveProfileId'], 'shared-structure')
                self.assertEqual(layers[0]['authorStatus'], 'missing')
                self.assertEqual(layers[0]['fallbackReason'], 's1-native-display-not-installed')
                self.assertTrue(layers[0]['fallback'])
                self.assertEqual(layers[0]['displayLabel'], '结构底图（作者路线不可用）')
                for layer in layers:
                    np.testing.assert_array_equal(layer['pixels'], layers[0]['pixels'])
                    self.assertNotIn('rgbaPixels', layer,
                        'blank-profile fallback keeps the original structure-display layer contract')
                self.assertEqual(layers[0]['resourceIdentity']['binding']['floor'], floor)

    def test_same_actual_source_and_metadata_as_original_guarded_default_chain(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, mid, floor = self.context(mode)
                with patch.object(preferences, '_owned_s1_display_reference', return_value=NotImplemented):
                    before = preferences.prepare_display_layer(engine, mid, floor, '')
                after = preferences.prepare_display_layer(engine, mid, floor, '')
                self.assert_same_layer(before, after)

    def test_real_custom_callback_can_decline_or_supply_pixels_without_being_skipped(self):
        engine, mid, floor = self.context()
        original = engine.qt_shared_provider('shared-structure', mid, floor)
        seen = []
        def custom(profile, map_id, current_floor, reference):
            self.assert_reference_contract(reference, original)
            seen.append((profile, map_id, current_floor, reference))
            return None
        engine.qt_custom_display = custom
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            first = preferences.prepare_display_layer(engine, mid, floor, '')
            second = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(raster.call_count, 2,
            'callback decline uses the original S1 reference path on each request')
        self.assertEqual([row[:3] for row in seen], [('', mid, floor)] * 2)
        # The original source and JSON metadata reach each callback intact.
        self.assertEqual(seen[0][3].metadata['binding']['mapId'], mid)
        self.assertEqual(seen[0][3].metadata, seen[1][3].metadata)
        np.testing.assert_array_equal(first['pixels'], second['pixels'])
        self.assertEqual(first['resourceIdentity'], second['resourceIdentity'])
        supplied = dict(pixels=np.full_like(first['pixels'], 41), matrixDisplayToReference=np.eye(3),
            resourceIdentity='custom-provider-current-layer', displayKind='custom-provider',
            displayLabel='custom fixture', routeProfileId='custom-provider')
        engine.qt_custom_display = lambda *args: supplied
        with patch.object(display, 'structure_display_layer', side_effect=AssertionError('custom pixels replaced')), \
                patch.object(engine.qt_native_context, 'rendered_display_layer',
                    side_effect=AssertionError('native called after custom selection')):
            actual = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertIs(actual['pixels'], supplied['pixels'])
        self.assertEqual(actual['resourceIdentity'], supplied['resourceIdentity'])

    def test_default_custom_json_metadata_contract_preserves_selected_pixels(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, mid, floor = self.context(mode)
                supplied = dict(pixels=np.full((8, 8, 4), 93, np.uint8),
                    matrixDisplayToReference=np.eye(3), resourceIdentity='canvas-checked-custom',
                    displayKind='custom-provider', displayLabel='checked custom', routeProfileId='custom-provider')
                def custom(profile, map_id, current_floor, reference):
                    # Existing JSON consumers compare lists and may deepcopy
                    # metadata before deciding whether to provide a layer.
                    if reference.metadata['canvas']['shapeHW'] != list(reference.shape):
                        return None
                    deepcopy(reference.metadata)
                    return supplied
                engine.qt_custom_display = custom
                before = LiveRuntime._display_layer(engine, mid, floor, '')
                self.assertIs(before['pixels'], supplied['pixels'])
                after = LiveRuntime._display_layer(engine, mid, floor, '')
                self.assertIs(after['pixels'], supplied['pixels'],
                    'RAM reference metadata must preserve the original JSON callback contract')
                self.assertEqual(after['resourceIdentity'], before['resourceIdentity'])
                self.assertEqual(after['displayKind'], before['displayKind'])

    def test_selected_author_and_missing_custom_keep_the_original_paths(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, mid, floor = self.context(mode)
                _, author_mid, author_floor = next(key for key in engine.qt_optional_displays.bindings
                    if key[0] == 'author-fixture')
                with patch.object(preferences, '_owned_s1_display_reference',
                        side_effect=AssertionError('selected author/custom replaced by default RAM path')):
                    author = preferences.prepare_display_layer(engine, author_mid, author_floor, 'author-fixture')
                    missing = preferences.prepare_display_layer(engine, mid, floor, 'missing-author')
                    with self.assertRaisesRegex(ValueError, 'custom-display-unavailable'):
                        preferences.prepare_display_layer(engine, mid, floor, 'custom:missing')
                self.assertEqual(author['displayKind'], 'author-route')
                self.assertEqual(author['preferredProfileId'], 'author-fixture')
                self.assertFalse(author['fallback'])
                self.assertNotIn('rgbaPixels', author)
                self.assertEqual(missing['preferredProfileId'], 'missing-author')
                self.assertEqual(missing['effectiveProfileId'], 'shared-structure')
                self.assertNotIn('rgbaPixels', missing)

    def test_selected_author_uses_original_pixels_and_bridge_with_a_real_windows_short_root(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, _, _ = self.context(mode, short_resource_root=True)
                _, mid, floor = next(key for key in engine.qt_optional_displays.bindings
                    if key[0] == 'author-fixture')
                actual_reference = engine.qt_shared_provider('shared-structure', mid, floor)
                actual = preferences.prepare_display_layer(engine, mid, floor, 'author-fixture')
                self.assertEqual(actual['displayKind'], 'author-route')
                self.assertFalse(actual['fallback'])
                expected = np.full((3, 5, 4), (33, 22, 11, 44), np.uint8)
                expected[1, 2] = (3, 2, 1, 0)
                np.testing.assert_array_equal(actual['pixels'], expected)
                np.testing.assert_array_equal(actual['matrixDisplayToReference'],
                    [[.8, .2, 1], [.1, 1.2, 2], [0, 0, 1]])
                self.assertIs(engine.qt_shared_provider('shared-structure', mid, floor), actual_reference)

    def test_native_layer_can_replace_structural_fallback_without_using_material_cache(self):
        engine, mid, floor = self.context()
        original = engine.qt_shared_provider('shared-structure', mid, floor)
        from s1_native_display import NativeDisplayResult
        native_layer = dict(pixels=np.full((8, 8, 4), 72, np.uint8), matrixDisplayToReference=np.eye(3),
            resourceIdentity='actual-native-display', displayKind='native-fixture',
            displayLabel='native fixture', routeProfileId='shared-structure')
        def native(map_id, current_floor, reference):
            self.assertEqual((map_id, current_floor), (mid, floor))
            self.assert_reference_contract(reference, original)
            deepcopy(reference.metadata)
            return NativeDisplayResult(native_layer, '')
        with patch.object(engine.qt_native_context, 'rendered_display_layer', side_effect=native) as rendered, \
                patch.object(display, 'structure_display_layer', side_effect=AssertionError('native pixels replaced')):
            actual = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(rendered.call_count, 1)
        self.assertIs(actual['pixels'], native_layer['pixels'])
        self.assertEqual(actual['resourceIdentity'], 'actual-native-display')
        self.assertNotIn('rgbaPixels', actual)

    def test_unknown_provider_and_replaced_loader_use_their_actual_guarded_source(self):
        engine, mid, floor = self.context()
        provider, calls = engine.qt_shared_provider, []
        def delegated(*args):
            calls.append(args)
            return provider(*args)
        engine.qt_shared_provider = delegated
        layer = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(calls, [('shared-structure', mid, floor)])
        self.assertNotIn('rgbaPixels', layer)
        engine.qt_shared_provider = provider
        original = provider._load
        with patch.object(provider, '_load', wraps=original) as replaced:
            layer = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(replaced.call_count, 1)
        self.assertNotIn('rgbaPixels', layer)

    def test_owner_selection_source_and_revision_replacements_reject_before_consumption(self):
        engine, mid, floor = self.context()
        prepared = preferences.prepare_display_layer(engine, mid, floor, 'shared-structure')
        provider = engine.qt_shared_provider
        owners = provider._operation_owners
        changes = ((engine, 'qt_native_base', copy(engine.qt_native_base)),
            (engine, 'qt_controls_profile', copy(engine.qt_controls_profile)),
            (engine, 'qt_fast_structure_source', copy(engine.qt_fast_structure_source)),
            (engine, 'qt_resource_revision', engine.qt_resource_revision + 1),
            (engine, 'qt_resource_context_current', lambda: True),
            (engine, 'qt_native_context', copy(engine.qt_native_context)),
            (owners.base_view, '_assert_current', lambda: None),
            (owners.controls_view, '_assert_current', lambda: None))
        for owner, name, value in changes:
            with self.subTest(name=name), patch.object(owner, name, value), no_display_resource_io():
                with self.assertRaises(ValueError):
                    preferences.shared_structure_display_current(engine, mid, floor, prepared)

    def test_resource_change_during_custom_callback_rejects_the_fallback(self):
        engine, mid, floor = self.context()
        def change(*args):
            self.supersede_catalog(engine)
            return None
        engine.qt_custom_display = change
        with self.assertRaisesRegex(ValueError, 'resource-catalog-superseded'):
            preferences.prepare_display_layer(engine, mid, floor, '')

    def test_original_s1_default_display_does_not_import_retired_p3_preparation(self):
        import builtins
        engine, mid, floor = self.context()
        original_import = builtins.__import__
        def reject_retired(name, *args, **kwargs):
            if name in ('structure_pose_inputs', 'client_s1_coordinate_recognition'):
                raise AssertionError(f'default original S1 display imported retired {name}')
            return original_import(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=reject_retired):
            layer = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertIsNotNone(layer)
        self.assertTrue(preferences.shared_structure_display_current(engine, mid, floor, layer))

    def test_original_s1_display_material_cache_is_bounded(self):
        engine, _, _ = self.context()
        provider = engine.qt_shared_provider
        for key in list(provider.versions)[:5]:
            mid, floor = key[1], key[3]
            preferences.prepare_display_layer(engine, mid, floor, 'shared-structure')
        self.assertLessEqual(len(provider._display_materials.entries), provider._display_materials.max_entries)
        self.assertLessEqual(provider._display_materials.retained_bytes, provider._display_materials.max_bytes)


if __name__ == '__main__':
    unittest.main()
