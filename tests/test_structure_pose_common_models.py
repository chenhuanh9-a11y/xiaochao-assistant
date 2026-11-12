"""Production factory common RAM models, separate from guarded fixture paths."""
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import resource_snapshot_io
import shared_structure_reference

from client_imported_providers import build_native_engine
from client_paths import RuntimePaths
from structure_pose_inputs import (DEFAULT_CACHE_BYTES, PoseInputsUnavailable,
    prepare_structure_pose_inputs, prepare_structure_pose_common_models)
import test_structure_pose_inputs as fixture


class StructurePoseCommonModelsTests(unittest.TestCase):
    setUpClass = fixture.StructurePoseInputTests.__dict__['setUpClass']
    archive = fixture.StructurePoseInputTests.__dict__['archive']
    engine = fixture.StructurePoseInputTests.engine

    def factory(self, season='s1', mode='difficult'):
        decoded, mid, current = self.engine(season, mode)
        owners = decoded.qt_shared_provider._operation_owners
        # Production CatalogSnapshot supplies one shared selection callback to
        # both views. Keep independent-view fixtures in the legacy test module.
        def selected_context():
            if not all(current.values()):
                raise ValueError('selection-superseded')
        owners.base_view._assert_current = owners.controls_view._assert_current = selected_context
        temporary = tempfile.TemporaryDirectory(prefix='pose-common-runtime-')
        self.addCleanup(temporary.cleanup)
        engine = build_native_engine(owners.base_view, owners.controls_view,
            RuntimePaths(Path(temporary.name), False), season=season, mode=mode, require_rank=False)
        engine.qt_resource_revision = 4
        engine.qt_resource_context_current = lambda: all(current.values())
        return engine, mid, current

    def first_read_failure(self, target, before_error=None):
        read, attempts = resource_snapshot_io.read_stable_file, []
        def fail_once(path, *args, **kwargs):
            if Path(path) == target:
                attempts.append(True)
                if len(attempts) == 1:
                    if before_error is not None:
                        before_error()
                    raise OSError('first-target-payload-failure')
            return read(path, *args, **kwargs)
        return attempts, patch.object(resource_snapshot_io, 'read_stable_file', side_effect=fail_once)

    def test_first_typed_failure_keeps_cold_gap_and_continues_recovery_floor(self):
        for enabled in (False, True):
            with self.subTest(common=enabled):
                engine, _, _ = self.factory()
                common = engine.qt_pose_common_models
                (mid, floor), (row, _) = next(iter(common.layers.items()))
                recovery = next(fl for fl in common.pack.floors if fl != floor)
                self.assertFalse(any(key[1] == mid and key[3] == floor
                                     for key in engine.qt_shared_provider._cache))
                attempts, fault = self.first_read_failure(row['path'])
                with patch.object(engine, 'qt_pose_common_models', common if enabled else None), fault:
                    inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4,
                                                          recovery_floors=(recovery,))
                self.assertIsNone(inputs.reference('shared-structure', mid, floor))
                self.assertIn('shared-reference-unavailable:' + floor, inputs.gaps)
                self.assertEqual(inputs.gaps, ('shared-reference-unavailable:' + floor,))
                self.assertIsNotNone(inputs.reference('shared-structure', mid, recovery))
                self.assertIsNotNone(inputs.prepared(inputs.reference('shared-structure', mid, recovery)))
                self.assertIsNone(inputs.preparation_resources[mid, floor])
                self.assertEqual(len(attempts), 1, 'consume the actual first failure without retry')

    def test_first_typed_failure_keeps_warm_required_error(self):
        for enabled in (False, True):
            with self.subTest(common=enabled):
                engine, _, _ = self.factory()
                common = engine.qt_pose_common_models
                (mid, floor), (row, _) = next(iter(common.layers.items()))
                self.assertIsNotNone(engine.qt_shared_provider('shared-structure', mid, floor))
                attempts, fault = self.first_read_failure(row['path'])
                with patch.object(engine, 'qt_pose_common_models', common if enabled else None), fault:
                    with self.assertRaisesRegex(PoseInputsUnavailable, 'reference-decode-unavailable'):
                        prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
                self.assertEqual(len(attempts), 1)

    def test_typed_first_failure_never_hides_cancel_selection_or_owner_change(self):
        for kind in ('cancel', 'selection', 'owner'):
            with self.subTest(kind=kind):
                engine, _, current = self.factory()
                common, cancelled = engine.qt_pose_common_models, []
                (mid, floor), (row, _) = next(iter(common.layers.items()))
                def change():
                    if kind == 'cancel':
                        cancelled.append(True)
                    elif kind == 'selection':
                        current['controls'] = False
                    else:
                        engine.side_entry_identity.entries = list(engine.side_entry_identity.entries)
                if kind == 'selection':
                    # Isolate the real view selection guard from the factory
                    # callback; both must retain their original refusal.
                    engine.qt_resource_context_current = lambda: True
                attempts, fault = self.first_read_failure(row['path'], change)
                expected = {'cancel': 'import-cancelled', 'selection': 'selection-superseded',
                            'owner': 'resource-binding-changed'}[kind]
                with fault, self.assertRaisesRegex(ValueError, expected):
                    prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4,
                                                  cancelled=lambda: bool(cancelled))
                self.assertEqual(len(attempts), 1)

    def test_first_optional_failure_is_a_gap_with_warm_required_reference(self):
        engine, _, _ = self.factory()
        common = engine.qt_pose_common_models
        (mid, floor), (_, declaration) = next(iter(common.layers.items()))
        self.assertIsNotNone(engine.qt_shared_provider('shared-structure', mid, floor))
        attempts, fault = self.first_read_failure(common._root / declaration[1].path)
        with fault:
            inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
        reference = inputs.reference('shared-structure', mid, floor)
        self.assertIsNotNone(reference)
        self.assertIsNone(inputs.prepared(reference))
        self.assertIn('fast-prepared-unavailable:' + floor, inputs.gaps)
        self.assertIsNone(inputs.preparation_resources[mid, floor])
        self.assertEqual(len(attempts), 1)

    def test_typed_failure_cannot_hide_selection_change_after_original_decoder(self):
        engine, _, current = self.factory()
        common = engine.qt_pose_common_models
        (mid, floor), (row, _) = next(iter(common.layers.items()))
        engine.qt_resource_context_current = lambda: True
        attempts, fault = self.first_read_failure(row['path'])
        decode, consumed = shared_structure_reference.load_shared_structure, []
        def change_after_decoder(path, **kwargs):
            reference = decode(path, **kwargs)
            if Path(path) == row['path'] and reference is None:
                consumed.append(True)
                current['controls'] = False
            return reference
        with fault, patch.object(shared_structure_reference, 'load_shared_structure', side_effect=change_after_decoder):
            with self.assertRaisesRegex(ValueError, 'selection-superseded'):
                prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(consumed, 'the actual decoder must consume the first error')

    def test_unseen_targets_read_only_their_actual_layer_payloads(self):
        engine, _, _ = self.factory()
        bundle = engine.qt_native_base
        view, reader = bundle._view, bundle._values['reader']
        maps = tuple(engine.pack.by_id)[:2]
        counts, load_counts = Counter(), []
        read, load = view._read_payload, view._load
        def counted(*args):
            counts[args[-1]] += 1
            return read(*args)
        def loaded(*args, **kwargs):
            load_counts.append(True)
            return load(*args, **kwargs)
        with patch.object(view, '_read_payload', side_effect=counted), patch.object(view, '_load', side_effect=loaded):
            inputs = [prepare_structure_pose_inputs(engine, mid, '-1', resource_revision=4) for mid in maps]
        common_reads = {path: count for path, count in counts.items() if not path.endswith('.npz')}
        print('production-unseen-common-work', dict(commonReads=common_reads, viewLoads=len(load_counts)))
        self.assertEqual(common_reads, {})
        self.assertEqual(len(counts), 4)
        self.assertTrue(all(count == 1 for count in counts.values()))
        self.assertTrue(all(value.prepared(value.reference('shared-structure', value.map_id, '-1'))
                            is not None for value in inputs))

    def test_four_contexts_match_original_inputs_and_share_common_rows(self):
        for season, mode in (('s0', 'nightmare'), ('s0', 'difficult'), ('s1', 'difficult'), ('s1', 'nightmare')):
            with self.subTest(season=season, mode=mode):
                engine, _, _ = self.factory(season, mode)
                common = engine.qt_pose_common_models
                self.assertIsNotNone(common)
                self.assertEqual(len(common.locators.side.passports), len(engine.side_entry_identity.passports))
                self.assertEqual(len(common.locators.side.entry_coverage), len(engine.side_entry_identity.entry_coverage))
                mid, floor = next(iter(common.layers))
                new = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
                with patch.object(engine, 'qt_pose_common_models', None):
                    old = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
                self.assertEqual(old.gaps, new.gaps)
                self.assertEqual(old.capabilities, new.capabilities)
                self.assertEqual(old.preparation_resources, new.preparation_resources)
                self.assertEqual(old.binding, new.binding)
                first, second = old.reference('shared-structure', mid, floor), new.reference('shared-structure', mid, floor)
                self.assertEqual(first.metadata, second.metadata)
                self.assertEqual(first.path, second.path)
                for name in ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask'):
                    np.testing.assert_array_equal(getattr(first, name), getattr(second, name))
                for name in ('distance', 'chains', 'rays', 'pitch', 'shape'):
                    np.testing.assert_array_equal(getattr(old.prepared(first), name), getattr(new.prepared(second), name))
                for locator_name in ('main', 'side'):
                    prior = getattr(old._locators, locator_name)
                    current = getattr(new._locators, locator_name)
                    self.assertEqual(len(prior.entries), len(current.entries))
                    for left, right in zip(prior.entries, current.entries):
                        self.assertEqual(left[0], right[0])
                        for a, b in zip(left[1:], right[1:]):
                            np.testing.assert_array_equal(a, b)
                    source = getattr(common.locators, locator_name)
                    self.assertTrue(all(any(row is candidate for candidate in source.entries) for row in current.entries))
                self.assertIs(new.pack, common.pack)
                self.assertEqual(engine.qt_pose_input_cache.max_entries, 2)
                self.assertLessEqual(common.retained_bytes + engine.qt_pose_input_cache.max_bytes, DEFAULT_CACHE_BYTES)
                geometry = new.new_geometry_engine()
                self.assertIsNot(geometry.entry_identity, new.new_geometry_engine().entry_identity)
                with self.assertRaises(ValueError):
                    second.clean_mask.setflags(write=True)

    def test_static_disk_edit_keeps_common_ram_until_explicit_preparation(self):
        engine, _, _ = self.factory()
        common = engine.qt_pose_common_models
        coordinate_path = common._root / engine.qt_native_base._values['interfaces']['coordinatePack']
        coordinate_path.write_bytes(b' ' * coordinate_path.stat().st_size)
        mid, floor = next(iter(common.layers))
        inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
        self.assertIsNotNone(inputs.adapters.matrix_profile_to_canonical('shared-structure', mid, floor))
        with self.assertRaises(ValueError):
            prepare_structure_pose_common_models(engine)

    def test_managed_context_change_and_cancel_refuse_partial_inputs(self):
        for kind in ('context', 'cancel'):
            with self.subTest(kind=kind):
                engine, _, current = self.factory()
                common, cancelled = engine.qt_pose_common_models, []
                mid, floor = next(iter(common.layers))
                view, read = common._reader.view, common._reader.view._read_payload
                def change_after_read(*args):
                    value = read(*args)
                    if kind == 'context':
                        current['controls'] = False
                    else:
                        cancelled.append(True)
                    return value
                with patch.object(view, '_read_payload', side_effect=change_after_read):
                    with self.assertRaises(ValueError):
                        prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4,
                                                      cancelled=lambda: bool(cancelled))
                self.assertEqual(engine.qt_pose_input_cache.retained_bytes, 0)

    def test_model_owner_change_is_refused_before_target_io(self):
        engine, _, _ = self.factory()
        common = engine.qt_pose_common_models
        mid, floor = next(iter(common.layers))
        engine.side_entry_identity.entries = list(engine.side_entry_identity.entries)
        with patch.object(common._reader, 'read_many', side_effect=AssertionError('stale target IO')):
            with self.assertRaises(PoseInputsUnavailable):
                prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)

    def test_existing_two_target_cache_shares_models_within_the_original_byte_budget(self):
        engine, _, _ = self.factory()
        common, cache = engine.qt_pose_common_models, engine.qt_pose_input_cache
        inputs = []
        for mid in tuple(common.pack.by_id)[:3]:
            value = prepare_structure_pose_inputs(engine, mid, '-1', resource_revision=4)
            self.assertTrue(cache.put(engine, value))
            inputs.append(value)
        self.assertEqual(len(cache._entries), 2)
        self.assertEqual(cache.max_entries, 2)
        self.assertLessEqual(cache.retained_bytes + common.retained_bytes, DEFAULT_CACHE_BYTES)
        self.assertTrue(all(value.pack is common.pack for value in inputs))

    def test_optional_damage_keeps_required_reference_and_actual_gap(self):
        engine, _, _ = self.factory()
        common = engine.qt_pose_common_models
        (mid, floor), (_, declaration) = next(iter(common.layers.items()))
        prepared_ref = declaration[1]
        path = common._root / prepared_ref.path
        path.write_bytes(b'x' * path.stat().st_size)
        inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
        self.assertIsNotNone(inputs.reference('shared-structure', mid, floor))
        self.assertIsNone(inputs.prepared(inputs.reference('shared-structure', mid, floor)))
        self.assertIn('fast-prepared-unavailable:' + floor, inputs.gaps)
        self.assertIsNone(inputs.preparation_resources[mid, floor])

    def test_legacy_and_independent_guard_keep_original_static_reads(self):
        for kind in ('legacy', 'independent', 'unmanaged'):
            with self.subTest(kind=kind):
                engine, mid, _ = self.factory('s0', 'difficult') if kind == 'legacy' else self.factory()
                common = engine.qt_pose_common_models
                floor = '1' if kind == 'legacy' else '-1'
                if kind == 'independent':
                    mid = next(iter(common.layers))[0]
                    original = engine.qt_assert_resource_current
                    engine.qt_assert_resource_current = lambda: original()
                elif kind == 'unmanaged':
                    del engine.qt_resource_context_current
                seen, reader = [], common._reader
                read = reader.view._read_payload
                def record(*args):
                    seen.append(args[-1])
                    return read(*args)
                with patch.object(reader.view, '_read_payload', side_effect=record):
                    inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
                self.assertTrue(any(path.endswith('coordinate-pack.json') or 'canonical-coordinate' in path for path in seen))
                self.assertIsNotNone(inputs.reference('shared-structure', mid, floor))

    def test_independent_control_selection_keeps_its_original_guarded_path(self):
        engine, _, _ = self.factory()
        common = engine.qt_pose_common_models
        controls_view = common._owners.controls_view
        calls, selected = [], controls_view._assert_current
        def independent():
            calls.append(True)
            selected()
        controls_view._assert_current = independent
        mid, floor = next(iter(common.layers))
        inputs = prepare_structure_pose_inputs(engine, mid, floor, resource_revision=4)
        self.assertTrue(calls)
        self.assertIsNotNone(inputs.reference('shared-structure', mid, floor))


if __name__ == '__main__':
    unittest.main()
