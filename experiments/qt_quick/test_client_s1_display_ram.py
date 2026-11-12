"""Formal S1 display composition over tiny synthetic imported resources.

Real readers, preparation, catalog selection, author wrapper and native display
run here. The coordinate fixture supplies its existing synthetic index digest;
these checks do not measure production resource, GUI or game performance.
"""
from contextlib import ExitStack
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
import unittest
from unittest.mock import patch

import numpy as np

import client_route_preferences as preferences
from client_live_runtime import LiveRuntime
from structure_pose_inputs import _original_native_loaders
import shared_structure_display as display
import test_client_s1_coordinate_recognition as coordinate_fixture


class S1DisplayRamTests(unittest.TestCase):
    setUpClass = coordinate_fixture.S1CoordinateRecognitionTests.__dict__['setUpClass']
    author_engine = coordinate_fixture.S1CoordinateRecognitionTests.author_engine

    def context(self, mode='difficult'):
        engine = self.author_engine(mode)
        coordinate = engine.qt_s1_coordinate_recognition
        self.assertIsNotNone(engine.qt_pose_static_payloads)
        self.assertTrue(_original_native_loaders(engine.qt_pose_static_payloads.common))
        self.assertTrue(coordinate.current())
        return engine, *next(iter(coordinate.references))

    def assert_same_layer(self, before, after):
        self.assertEqual(set(after) - set(before), {'rgbaPixels'})
        for key, value in before.items():
            if isinstance(value, np.ndarray):
                np.testing.assert_array_equal(after[key], value, err_msg=key)
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
        self.assertEqual((actual.path, actual.resource_root, actual.shape),
            (original.path, original.resource_root, original.shape))
        self.assert_json_contract(actual.metadata, original.metadata)
        self.assert_json_contract(actual.native_wall_segments, original.native_wall_segments)
        for name in ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask'):
            before, after = getattr(original, name), getattr(actual, name)
            if before is None:
                self.assertIsNone(after)
            else:
                np.testing.assert_array_equal(after, before, err_msg=name)

    def test_default_structure_fallback_reuses_material_after_actual_native_selection(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine, mid, floor = self.context(mode)
                native = engine.qt_native_context
                with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster, \
                        patch.object(native, 'rendered_display_layer', wraps=native.rendered_display_layer) as rendered:
                    layers = [LiveRuntime._display_layer(engine, mid, floor, '') for _ in range(3)]
                self.assertEqual(raster.call_count, 1)
                self.assertEqual(rendered.call_count, 3, 'native source selection and its guard remain real')
                self.assertEqual(layers[0]['preferredProfileId'], '')
                self.assertEqual(layers[0]['effectiveProfileId'], 'shared-structure')
                self.assertEqual(layers[0]['authorStatus'], 'missing')
                self.assertEqual(layers[0]['fallbackReason'], 's1-native-display-not-installed')
                self.assertTrue(layers[0]['fallback'])
                self.assertEqual(layers[0]['displayLabel'], '结构底图（作者路线不可用）')
                for layer in layers:
                    self.assertIs(layer['pixels'], layers[0]['pixels'])
                    self.assertIs(layer['rgbaPixels'], layers[0]['rgbaPixels'])
                    np.testing.assert_array_equal(layer['rgbaPixels'], layer['pixels'][..., [2, 1, 0, 3]])
                    for pixels in (layer['pixels'], layer['rgbaPixels'], layer['matrixDisplayToReference']):
                        with self.assertRaises(ValueError):
                            pixels.setflags(write=True)
                layers[0]['resourceIdentity']['binding']['floor'] = 'detached-consumer-metadata'
                again = preferences.prepare_display_layer(engine, mid, floor, '')
                self.assertEqual(again['resourceIdentity']['binding']['floor'], floor)
                self.assertIs(again['pixels'], layers[0]['pixels'])

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
            reference.metadata['canvas']['consumerAnnotation'] = len(seen)
            return None
        engine.qt_custom_display = custom
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            first = preferences.prepare_display_layer(engine, mid, floor, '')
            second = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(raster.call_count, 1)
        self.assertEqual([row[:3] for row in seen], [('', mid, floor)] * 2)
        # Reference wrapper identity was never a consumer guarantee. Preserve
        # actual masks/source while isolating each callback's mutable metadata.
        self.assertIs(seen[0][3].clean_mask, seen[1][3].clean_mask)
        self.assertIsNot(seen[0][3].metadata, seen[1][3].metadata)
        self.assertIsNot(seen[0][3].metadata['canvas'], seen[1][3].metadata['canvas'])
        self.assertEqual(seen[0][3].metadata['binding']['mapId'], mid)
        self.assertNotIn('consumerAnnotation', original.metadata['canvas'])
        self.assertIs(first['pixels'], second['pixels'])
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
                    # Existing JSON consumers compare lists and annotate a
                    # deepcopy; neither operation may silently select fallback.
                    if reference.metadata['canvas']['shapeHW'] != list(reference.shape):
                        return None
                    annotated = deepcopy(reference.metadata)
                    annotated['canvas']['consumerAnnotation'] = True
                    return supplied
                engine.qt_custom_display = custom
                with patch.object(preferences, '_owned_s1_display_reference', return_value=NotImplemented):
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
            annotated = deepcopy(reference.metadata)
            annotated['canvas']['nativeAnnotation'] = True
            reference.metadata['canvas']['nativeAnnotation'] = True
            return NativeDisplayResult(native_layer, '')
        with patch.object(engine.qt_native_context, 'rendered_display_layer', side_effect=native) as rendered, \
                patch.object(display, 'structure_display_layer', side_effect=AssertionError('native pixels replaced')):
            actual = preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertEqual(rendered.call_count, 1)
        self.assertIs(actual['pixels'], native_layer['pixels'])
        self.assertEqual(actual['resourceIdentity'], 'actual-native-display')
        self.assertNotIn('rgbaPixels', actual)
        self.assertNotIn('nativeAnnotation', original.metadata['canvas'])

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
        preferences.prepare_display_layer(engine, mid, floor, '')
        provider, coordinate = engine.qt_shared_provider, engine.qt_s1_coordinate_recognition
        owners = provider._operation_owners
        mismatched = dict(engine.qt_pose_static_payloads.references)
        mismatched[mid, floor] = replace(mismatched[mid, floor])
        changes = ((engine, 'qt_native_base', copy(engine.qt_native_base)),
            (engine, 'qt_controls_profile', copy(engine.qt_controls_profile)),
            (engine, 'qt_fast_structure_source', copy(engine.qt_fast_structure_source)),
            (engine, 'qt_resource_revision', engine.qt_resource_revision + 1),
            (engine, 'qt_resource_context_current', lambda: True),
            (engine, 'qt_pose_static_payloads', replace(engine.qt_pose_static_payloads,
                references=MappingProxyType(mismatched))),
            (coordinate, 'references', MappingProxyType(dict(coordinate.references))),
            (owners.base_view, '_assert_current', lambda: None),
            (owners.controls_view, '_assert_current', lambda: None))
        for owner, name, value in changes:
            with self.subTest(name=name), patch.object(owner, name, value), ExitStack() as stack:
                for method in ('read_bytes', 'read_text'):
                    stack.enter_context(patch.object(Path, method, side_effect=AssertionError('obsolete source IO')))
                with self.assertRaises(ValueError):
                    preferences.prepare_display_layer(engine, mid, floor, '')

    def test_resource_change_during_custom_callback_rejects_the_fallback(self):
        engine, mid, floor = self.context()
        def change(*args):
            engine.qt_resource_revision += 1
            return None
        engine.qt_custom_display = change
        with self.assertRaises(ValueError):
            preferences.prepare_display_layer(engine, mid, floor, '')

    def test_owned_reference_and_existing_material_caches_are_bounded(self):
        engine, _, _ = self.context()
        provider = engine.qt_shared_provider
        for mid, floor in list(engine.qt_s1_coordinate_recognition.references)[:5]:
            preferences.prepare_display_layer(engine, mid, floor, '')
        self.assertLessEqual(len(provider._s1_display_references), 3)
        self.assertLessEqual(sum(row[2] for row in provider._s1_display_references.values()),
            provider._display_materials.max_bytes)
        self.assertLessEqual(len(provider._display_materials.entries), provider._display_materials.max_entries)
        self.assertLessEqual(provider._display_materials.retained_bytes, provider._display_materials.max_bytes)


if __name__ == '__main__':
    unittest.main()
