"""Real imported references through the shared display consumption boundary."""
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import resource_snapshot_io

from client_route_preferences import prepare_display_layer, shared_structure_display_current
from imported_resource_catalog import ResourceResolver
import shared_structure_display as display
import test_structure_pose_inputs as fixture


class DisplayGuardConsumptionTests(unittest.TestCase):
    setUpClass = fixture.StructurePoseInputTests.__dict__['setUpClass']
    archive = fixture.StructurePoseInputTests.__dict__['archive']
    engine = fixture.StructurePoseInputTests.engine

    def native(self, *, mode='nightmare'):
        engine, _, current = self.engine('s0', mode)
        mid = next(key[1] for key in engine.qt_shared_provider.versions)
        return engine, mid, current

    def test_actual_runtime_display_entry_reuses_native_bgra_and_rgba(self):
        from client_live_runtime import LiveRuntime
        engine, mid, _ = self.native(mode='difficult')
        conversions, contiguous = [], np.ascontiguousarray
        def count_color(array, *args, **kwargs):
            if array.ndim == 3 and array.shape[-1] == 4:
                conversions.append(True)
            return contiguous(array, *args, **kwargs)
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster, \
             patch.object(np, 'ascontiguousarray', side_effect=count_color):
            layers = [LiveRuntime._display_layer(engine, mid, '1', 'shared-structure') for _ in range(3)]
        self.assertEqual(raster.call_count, 1, 'reopen/progressive consumers must share the actual native raster')
        self.assertEqual(len(conversions), 1, 'both color spaces are built once, not once per consumer')
        for layer in layers:
            self.assertIs(layer['pixels'], layers[0]['pixels'])
            self.assertIs(layer['rgbaPixels'], layers[0]['rgbaPixels'])
            np.testing.assert_array_equal(layer['rgbaPixels'], layer['pixels'][..., [2, 1, 0, 3]])
            for pixels in (layer['pixels'], layer['rgbaPixels']):
                with self.assertRaises(ValueError):
                    pixels.setflags(write=True)
        self.assertTrue(shared_structure_display_current(engine, mid, '1', layers[0]))

    def test_native_source_arrays_and_exported_materials_cannot_be_reopened_for_writes(self):
        engine, mid, _ = self.native()
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        reference = engine.qt_shared_provider('shared-structure', mid, '1')
        for array in (reference.clean_mask, reference.validation_mask, reference.known_boundary,
                      reference.unknown_mask, layer['pixels'], layer['rgbaPixels'], layer['matrixDisplayToReference']):
            if array is not None:
                with self.assertRaises(ValueError):
                    array.setflags(write=True)
        layer['resourceIdentity']['binding']['floor'] = 'consumer-metadata-only'
        again = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertEqual(again['resourceIdentity']['binding']['floor'], '1')
        self.assertIs(again['pixels'], layer['pixels'])

    def test_actual_metadata_change_rebuilds_material_and_publication_reads_actual_source(self):
        engine, mid, _ = self.native()
        provider = engine.qt_shared_provider
        before = prepare_display_layer(engine, mid, '1', 'shared-structure')
        reference = provider('shared-structure', mid, '1')
        reference.metadata['sourceClass'] = 'legacy-clean-working-draft'
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            changed = prepare_display_layer(engine, mid, '1', 'shared-structure')
            again = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertEqual(raster.call_count, 1)
        self.assertEqual(changed['displayLabel'], '历史结构草稿')
        self.assertIsNot(changed['pixels'], before['pixels'])
        self.assertIs(changed['pixels'], again['pixels'])
        reference.metadata['structureVersionKey'] = 'actual-version-changed'
        self.assertFalse(shared_structure_display_current(engine, mid, '1', changed))

    def test_same_path_mutable_cache_replacement_cannot_borrow_old_material(self):
        engine, mid, _ = self.native()
        provider = engine.qt_shared_provider
        before = prepare_display_layer(engine, mid, '1', 'shared-structure')
        key, reference = next(iter(provider._cache.items()))
        writable = replace(reference, clean_mask=reference.clean_mask.copy())
        writable.clean_mask.fill(0)
        provider._cache[key] = writable
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            changed = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertEqual(raster.call_count, 1)
        self.assertIsNot(changed['pixels'], before['pixels'])
        self.assertFalse(np.any(changed['pixels']))
        self.assertNotIn('rgbaPixels', changed, 'unowned mutable references keep their original path')

    def test_redecode_and_material_eviction_do_not_invent_resource_invalidation(self):
        engine, mid, _ = self.native()
        provider = engine.qt_shared_provider
        before = prepare_display_layer(engine, mid, '1', 'shared-structure')
        provider._cache.clear()  # Same actual file; this is ordinary eviction.
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            again = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertEqual(raster.call_count, 1)
        self.assertIsNot(again['pixels'], before['pixels'])
        self.assertTrue(shared_structure_display_current(engine, mid, '1', before))
        np.testing.assert_array_equal(again['pixels'], before['pixels'])

    def test_normal_concurrent_lru_reads_cannot_revoke_consumed_source(self):
        import inspect
        import sys
        import threading
        from client_providers import SharedReferenceProvider
        engine, mid, _ = self.native(mode='difficult')
        provider = engine.qt_shared_provider
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        target = next(key for key in provider.versions if key[1] == mid and key[3] == '1')
        original = provider._cache[target]
        others = [key for key in provider.versions if key != target][:6]
        self.assertEqual(len(others), 6)
        go, started, done = threading.Event(), threading.Event(), threading.Event()
        reads, failures, paused = [], [], []

        def ordinary_reads():
            try:
                self.assertTrue(go.wait(10))
                started.set()
                for key in others:
                    self.assertIsNotNone(provider('shared-structure', key[1], key[3]))
                    reads.append(key)
            except BaseException as error:
                failures.append(error)
            finally:
                done.set()

        method = SharedReferenceProvider._consume_display_reference
        lines, first = inspect.getsourcelines(method)
        pause_line = first + next(i for i, line in enumerate(lines) if line.lstrip().startswith('if reference is None:'))
        def trace(frame, event, arg):
            if event == 'line' and frame.f_code is method.__code__ and frame.f_lineno == pause_line and not paused:
                paused.append(True)
                go.set()
                self.assertTrue(started.wait(10))
                # Without a bounded consumption lock the ordinary reads finish
                # here and reproduce the old false invalidation. Under that
                # lock they finish after consumption; never wait out/deny reads.
                if not provider._lock._is_owned():
                    self.assertTrue(done.wait(10))
            return trace

        worker = threading.Thread(target=ordinary_reads)
        worker.start()
        previous_trace, failure, current = sys.gettrace(), None, None
        try:
            sys.settrace(trace)
            current = shared_structure_display_current(engine, mid, '1', layer)
        except Exception as error:
            failure = error
        finally:
            sys.settrace(previous_trace)
            go.set()
            worker.join(10)
        self.assertFalse(worker.is_alive(), 'ordinary consumers must finish after the bounded display consumption')
        self.assertEqual(failures, [])
        self.assertEqual(reads, others)
        self.assertEqual(paused, [True])
        self.assertNotIn(target, provider._cache, 'the six reads must actually exercise normal LRU eviction')
        decoded = provider('shared-structure', mid, '1')
        self.assertIsNot(decoded, original)
        self.assertEqual(display.structure_display_identity(original), display.structure_display_identity(decoded))
        self.assertTrue(shared_structure_display_current(engine, mid, '1', layer))
        self.assertIsNone(failure, f'ordinary LRU reads are not a resource change: {failure!r}')
        self.assertTrue(current)

    def test_native_material_cache_bounds_both_color_spaces_and_lru_entries(self):
        from native_structure_display_material import NativeStructureDisplayMaterials
        engine, mid, _ = self.native(mode='difficult')
        provider = engine.qt_shared_provider
        provider._display_materials = cache = NativeStructureDisplayMaterials(max_entries=1)
        first = prepare_display_layer(engine, mid, '1', 'shared-structure')
        retained = first['pixels'].nbytes + first['rgbaPixels'].nbytes + first['matrixDisplayToReference'].nbytes
        self.assertEqual(cache.retained_bytes, retained)
        other = next(key[1] for key in provider.versions if key[1] != mid)
        second = prepare_display_layer(engine, other, '1', 'shared-structure')
        self.assertEqual(len(cache.entries), 1)
        self.assertEqual(cache.retained_bytes,
            second['pixels'].nbytes + second['rgbaPixels'].nbytes + second['matrixDisplayToReference'].nbytes)
        second_bytes = cache.retained_bytes
        self.assertTrue(shared_structure_display_current(engine, mid, '1', first))
        rebuilt = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertIsNot(first['pixels'], rebuilt['pixels'])
        provider._display_materials = tiny = NativeStructureDisplayMaterials(max_entries=3, max_bytes=retained-1)
        oversized = prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertIn('rgbaPixels', oversized)
        self.assertEqual((len(tiny.entries), tiny.retained_bytes), (0, 0))
        provider._display_materials = byte_limited = NativeStructureDisplayMaterials(
            max_entries=3, max_bytes=max(retained, second_bytes))
        prepare_display_layer(engine, mid, '1', 'shared-structure')
        prepare_display_layer(engine, other, '1', 'shared-structure')
        self.assertEqual(len(byte_limited.entries), 1, 'the byte limit must evict even below the entry limit')
        self.assertLessEqual(byte_limited.retained_bytes, byte_limited.max_bytes)

    def test_native_shared_material_does_not_cache_author_structure_fallback(self):
        engine, mid, _ = self.native(mode='difficult')
        shared = prepare_display_layer(engine, mid, '1', 'shared-structure')
        with patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster:
            layers = [prepare_display_layer(engine, mid, '1', 'missing-author') for _ in range(2)]
        self.assertEqual(raster.call_count, 2)
        self.assertEqual(layers[0]['effectiveProfileId'], 'shared-structure')
        self.assertTrue(layers[0]['fallback'])
        self.assertNotIn('rgbaPixels', layers[0])
        self.assertIsNot(layers[0]['pixels'], shared['pixels'])

    def test_real_native_display_and_identity_each_use_two_context_checks(self):
        engine, mid, _ = self.native(mode='difficult')
        provider = engine.qt_shared_provider
        controls = provider._operation_owners.controls_view
        typed_reads = []
        actual_read = resource_snapshot_io.read_stable_file
        def count_typed(path, *args, **kwargs):
            if Path(path) == provider._cache[next(iter(provider._cache))].path:
                typed_reads.append(path)
            return actual_read(path, *args, **kwargs)
        with patch.object(provider.resources, 'load', wraps=provider.resources.load) as load:
            with patch.object(controls, '_load', wraps=controls._load) as initial:
                layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
            with patch.object(controls, '_load', wraps=controls._load) as publication, \
                 patch.object(resource_snapshot_io, 'read_stable_file', side_effect=count_typed):
                self.assertTrue(shared_structure_display_current(engine, mid, '1', layer))
        print('shared-display-context-proofs', initial.call_count, publication.call_count)
        self.assertEqual((initial.call_count, publication.call_count), (2, 2))
        self.assertEqual(load.call_count, 1, 'the warm identity read must not decode or add typed IO')
        self.assertEqual(typed_reads, [], 'warm source identity retains the original cache read, not a new batch')
        self.assertFalse(layer['pixels'].flags.writeable)

    def test_same_path_actual_cache_and_engine_owners_cannot_change_during_consumption(self):
        for mutation in ('cache-reference', 'cache-container', 'engine-provider', 'engine-guard', 'engine-source'):
            with self.subTest(mutation=mutation):
                engine, mid, _ = self.native()
                provider = engine.qt_shared_provider
                layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
                key = next(key for key in provider.versions if key[1] == mid and key[3] == '1')
                reference = provider._cache[key]
                identity, changed = display.structure_display_identity, []

                def change(actual):
                    result = identity(actual)
                    if not changed:
                        changed.append(True)
                        if mutation == 'cache-reference':
                            replacement = replace(reference, metadata=deepcopy(reference.metadata),
                                clean_mask=np.zeros_like(reference.clean_mask))
                            self.assertEqual(identity(replacement), layer['resourceIdentity'])
                            provider._cache[key] = replacement
                        elif mutation == 'cache-container':
                            provider._cache = copy(provider._cache)
                        elif mutation == 'engine-provider':
                            engine.qt_shared_provider = copy(provider)
                        elif mutation == 'engine-guard':
                            engine.qt_assert_resource_current = lambda: None
                        else:
                            engine.qt_fast_structure_source = copy(engine.qt_fast_structure_source)
                    return result

                with patch.object(display, 'structure_display_identity', side_effect=change):
                    with self.assertRaisesRegex(ValueError, 'display-resource-owner-changed|display-resource-changed') as error:
                        shared_structure_display_current(engine, mid, '1', layer)
                self.assertEqual(type(error.exception).__name__, 'DisplayResourceChanged')
                self.assertTrue(changed)

    def test_actual_catalog_and_manifest_replacement_are_explicit_resource_loss(self):
        for mutation, expected in (('catalog', 'resource-catalog-superseded'),
                                   ('manifest', 'resource-package-manifest-replaced')):
            with self.subTest(mutation=mutation):
                engine, mid, _ = self.native()
                owners = engine.qt_shared_provider._operation_owners
                catalog = owners.base_view.resource_root / 'catalog.json'
                document = dict(schema='xingtu-resource-catalog-v1', generation=str(uuid4()), contexts={}, displays=[])
                catalog.write_text(json.dumps(document), encoding='utf-8')
                snapshot = ResourceResolver(owners.base_view.resource_root).snapshot()
                owners.base_view._assert_current = owners.controls_view._assert_current = snapshot.assert_current
                layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
                identity, changed = display.structure_display_identity, []

                def replace_source(reference):
                    value = identity(reference)
                    if not changed:
                        changed.append(True)
                        if mutation == 'catalog':
                            document['generation'] = str(uuid4())
                            catalog.write_text(json.dumps(document), encoding='utf-8')
                        else:
                            manifest = owners.base_view.root.parent.parent / 'manifest.json'
                            replacement = manifest.with_suffix('.replacement')
                            replacement.write_bytes(manifest.read_bytes())
                            replacement.replace(manifest)
                    return value

                with patch.object(display, 'structure_display_identity', side_effect=replace_source):
                    with self.assertRaisesRegex(ValueError, expected) as error:
                        shared_structure_display_current(engine, mid, '1', layer)
                self.assertTrue(changed)
                self.assertEqual(type(error.exception).__name__, 'DisplayResourceChanged')

    def test_ordinary_post_guard_io_is_not_misclassified_as_resource_replacement(self):
        engine, mid, _ = self.native()
        owners = engine.qt_shared_provider._operation_owners
        manifest = owners.base_view.root.parent.parent / 'manifest.json'
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        identity, read, consumed = display.structure_display_identity, resource_snapshot_io.read_stable_file, []

        def consume(reference):
            result = identity(reference)
            consumed.append(True)
            return result

        def fail_after_consumption(path, *args, **kwargs):
            if consumed and Path(path) == manifest:
                raise OSError('ordinary-manifest-read-error')
            return read(path, *args, **kwargs)

        with patch.object(display, 'structure_display_identity', side_effect=consume), \
             patch.object(resource_snapshot_io, 'read_stable_file', side_effect=fail_after_consumption):
            with self.assertRaisesRegex(ValueError, 'manifest-unavailable') as error:
                shared_structure_display_current(engine, mid, '1', layer)
        self.assertTrue(consumed)
        self.assertNotEqual(type(error.exception).__name__, 'DisplayResourceChanged')
        self.assertEqual(error.exception.code, 'resource-package-manifest-unavailable')

    def test_actual_metadata_consumption_still_precedes_final_resource_guard(self):
        engine, mid, current = self.native()
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        identity, consumed = display.structure_display_identity, []

        def revoke(reference):
            result = identity(reference)
            consumed.append(True)
            current['controls'] = False
            return result

        with patch.object(display, 'structure_display_identity', side_effect=revoke):
            with self.assertRaisesRegex(ValueError, 'controls-selection-superseded'):
                shared_structure_display_current(engine, mid, '1', layer)
        self.assertTrue(consumed)

    def test_independent_guard_and_unknown_reader_keep_original_guard_chain(self):
        for mutation in ('independent-guard', 'unknown-reader'):
            with self.subTest(mutation=mutation):
                engine, mid, _ = self.native()
                provider = engine.qt_shared_provider
                layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
                if mutation == 'independent-guard':
                    actual = engine.qt_assert_resource_current
                    engine.qt_assert_resource_current = lambda: actual()
                else:
                    actual = provider.resources.read_resource
                    provider.resources.read_resource = lambda path: actual(path)
                controls = provider._operation_owners.controls_view
                with patch.object(controls, '_load', wraps=controls._load) as check:
                    self.assertTrue(shared_structure_display_current(engine, mid, '1', layer))
                self.assertEqual(check.call_count, 4)

    def test_cached_metadata_is_actual_even_when_versions_expect_the_old_value(self):
        engine, mid, _ = self.native()
        provider = engine.qt_shared_provider
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        key = next(key for key in provider.versions if key[1] == mid and key[3] == '1')
        provider._cache[key].metadata['structureVersionKey'] = 'actual-cache-replacement'
        self.assertNotEqual(provider._cache[key].metadata['structureVersionKey'],
                            provider.versions[key]['expected']['structureVersionKey'])
        self.assertFalse(shared_structure_display_current(engine, mid, '1', layer))

    def test_first_cold_file_failure_is_not_retried_into_a_display(self):
        engine, mid, _ = self.native()
        provider = engine.qt_shared_provider
        target = next(row['path'] for key, row in provider.versions.items() if key[1] == mid and key[3] == '1')
        read, attempts = resource_snapshot_io.read_stable_file, []

        def fail_once(path, *args, **kwargs):
            if Path(path) == target:
                attempts.append(True)
                if len(attempts) == 1:
                    raise OSError('first-real-typed-read-failure')
            return read(path, *args, **kwargs)

        with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=fail_once):
            with self.assertRaisesRegex(ValueError, 'display-reference-unavailable'):
                prepare_display_layer(engine, mid, '1', 'shared-structure')
        self.assertEqual(len(attempts), 1)
        self.assertFalse(provider._cache)
        self.assertIsNotNone(prepare_display_layer(engine, mid, '1', 'shared-structure'))

    def test_real_legacy_map_keeps_its_original_chain_and_draft_semantics(self):
        engine, mid, _ = self.engine('s0', 'difficult')
        provider = engine.qt_shared_provider
        controls = provider._operation_owners.controls_view
        with patch.object(controls, '_load', wraps=controls._load) as check:
            layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
            self.assertTrue(shared_structure_display_current(engine, mid, '1', layer))
        self.assertEqual(check.call_count, 8)
        self.assertEqual(layer['displayLabel'], '历史结构草稿')
        self.assertIs(provider('shared-structure', mid, '1').metadata['authorizesStructureValidation'], False)

    def test_unknown_provider_cannot_claim_a_guard_by_copying_its_field(self):
        engine, mid, current = self.native()
        reference = engine.qt_shared_provider('shared-structure', mid, '1')
        class UnknownProvider:
            _check_current = engine.qt_assert_resource_current
            def __call__(self, *args):
                return reference
        engine.qt_shared_provider = UnknownProvider()
        layer = prepare_display_layer(engine, mid, '1', 'shared-structure')
        identity = display.structure_display_identity
        def revoke(actual):
            result = identity(actual)
            current['controls'] = False
            return result
        with patch.object(display, 'structure_display_identity', side_effect=revoke):
            with self.assertRaisesRegex(ValueError, 'controls-selection-superseded'):
                shared_structure_display_current(engine, mid, '1', layer)

    def test_four_mode_helper_keeps_actual_sources_without_runtime_authority(self):
        for season, mode in (('s0', 'nightmare'), ('s0', 'difficult'), ('s1', 'nightmare'), ('s1', 'difficult')):
            with self.subTest(season=season, mode=mode):
                engine, mid, _ = self.engine(season, mode)
                floor = '-3' if season == 's1' else '1'
                layer = prepare_display_layer(engine, mid, floor, 'shared-structure')
                self.assertTrue(shared_structure_display_current(engine, mid, floor, layer))
                self.assertEqual(layer['displayKind'], 'shared-structure')
                self.assertNotIn('authorizesIdentity', layer)


import test_client_local_identity_commit as local_identity


class NativeMaterialRuntimeConsumptionTests(unittest.TestCase):
    """Real mixed native pool/local lock/display/solve; only numerical sensors and OS input supplied."""
    setUpClass = local_identity.LocalIdentityCommitTests.__dict__['setUpClass']
    setUp = local_identity.LocalIdentityCommitTests.setUp
    opening = local_identity.LocalIdentityCommitTests.opening

    def test_actual_solve_progressive_final_and_locked_reopen_share_cached_rgba(self):
        from copy import deepcopy
        import threading
        from structure_pose_solver import PreparedStructurePoseSolver
        from test_client_structure_unification import supported_pose
        provider = self.engine.qt_shared_provider
        provider._cache.clear()
        pending, conversions, contiguous = [], [], np.ascontiguousarray
        offer_progressive, published = [False], threading.Event()

        def event(row):
            if row['event'] == 'render-ready' and row.get('reason') == 'current-frame-review-pending':
                pending.append(self.runtime.render_payload(row['token']))
                published.set()
        self.runtime.set_event_callback(event)

        def fit(host, *args, **kwargs):
            first = deepcopy(supported_pose())
            if offer_progressive[0]:
                host._offer_usable_fit(first)
                self.assertTrue(published.wait(5), 'real runtime must consume the offered pending pose')
            return first

        def count_color(array, *args, **kwargs):
            if array.ndim == 3 and array.shape[-1] == 4:
                conversions.append(True)
            return contiguous(array, *args, **kwargs)

        with patch.object(PreparedStructurePoseSolver, 'fit', fit), \
             patch.object(display, 'structure_display_layer', wraps=display.structure_display_layer) as raster, \
             patch.object(np, 'ascontiguousarray', side_effect=count_color):
            result = self.opening()
            self.assertTrue(result['success'], result)
            self.assertTrue(self.runtime.snapshot()['identityLocked'])
            final = self.runtime.render_payload(result['renderToken'])
            cached = self.runtime._display_layer(self.engine, self.target, '1', 'shared-structure')
            self.assertEqual(final['reviewStatus'], 'complete')
            self.assertIs(final['pixels'], cached['rgbaPixels'])
            self.runtime.dispatch('close_map')
            offer_progressive[0] = True
            with patch.object(self.engine, 'qt_structure_scan',
                    side_effect=AssertionError('locked reopen must not restart catalogue scan')):
                reopened = self.opening()
            self.assertTrue(reopened['success'], reopened)
            row = self.runtime.render_payload(reopened['renderToken'])
            self.assertEqual(pending[0]['reviewStatus'], 'pending')
            self.assertIs(pending[0]['pixels'], cached['rgbaPixels'])
            self.assertEqual(row['reviewStatus'], 'complete')
            self.assertIs(row['pixels'], cached['rgbaPixels'])
        self.assertEqual(raster.call_count, 1)
        self.assertEqual(len(conversions), 1)
