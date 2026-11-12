"""Actual imported factory/pose preparation; numerical algorithms stay real."""
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import imported_fast_structure
import resource_snapshot_io
import shared_structure_reference
import structure_pose_inputs
import client_s1_coordinate_recognition as coordinate
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_paths import RuntimePaths
from client_providers import SharedReferenceProvider, prepare_pose_solver
from imported_native_base import decode_native_base
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver, SCHEMA
from imported_resource_packages import PackageView
import test_structure_pose_inputs as fixture
import test_client_s1_coordinate_recognition as coordinate_fixture
from test_imported_author_display import _AuthorDisplayFixture


class StructurePoseStaticPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.StructurePoseInputTests.setUpClass.__func__(cls)
        coordinate_fixture.S1CoordinateRecognitionTests.setUpClass.__func__(cls)
    archive = fixture.StructurePoseInputTests.__dict__['archive']

    def factory(self, season='s1', mode='difficult', *, raw=None, author_display=False):
        temporary = tempfile.TemporaryDirectory(prefix='pose-payload-factory-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        raw = self.archive(season, mode)[0] if raw is None else raw
        archive = root / 'base.zip'
        archive.write_bytes(raw)
        resolver = ResourceResolver(root / 'resources', supported_capabilities=SUPPORTED_CAPABILITIES)
        saved = save_archive(archive, resolver.root)
        refs = {ref.component_id: dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
            componentId=ref.component_id, manifestSha256=ref.manifest_sha256) for ref in saved.component_refs}
        displays = []
        if author_display:
            # Reuse the actual independently imported author ZIP fixture in
            # this factory's selected resource root, including its base binding.
            author = _AuthorDisplayFixture('runTest')
            author.root, author.resources, author.counter = root, resolver.root, 0
            author.base_view = PackageView(resolver.root, next(
                ref for ref in saved.component_refs if ref.component_id == 'base'))
            author.base = decode_native_base(author.base_view, season=season, mode=mode)
            author.provider = SharedReferenceProvider(author.base_view.root, author.base.resources,
                author.base.versions, check_current=author.base.assert_current)
            author.source = SimpleNamespace(ids=tuple(author.base.pack.by_id))
            display, _, _ = author.package()
            displays.append(author.wire(display.component_ref))
        (resolver.root / 'catalog.json').write_text(json.dumps(dict(schema=SCHEMA,
            generation=uuid4().hex, displays=displays, contexts={f'{season}:{mode}': dict(
                recognitionBase=refs['base'], controls=refs['controls'], reviewedGroups=None)})))
        factories = build_imported_engine_factories(resolver, RuntimePaths(root / 'runtime', False))
        return factories, lambda: factories._get(mode, season), root

    def counts(self, stack):
        counts = Counter()
        load, read = PackageView._load, resource_snapshot_io.read_stable_file
        typed, fast = structure_pose_inputs.load_shared_structure, imported_fast_structure.decode_prepared
        def loaded(view, *args, **kwargs):
            counts['viewLoads'] += 1
            return load(view, *args, **kwargs)
        def read_file(path, *args, **kwargs):
            counts['stableReads'] += 1
            counts['npzReads' if Path(path).suffix == '.npz' else 'metadataReads'] += 1
            return read(path, *args, **kwargs)
        def typed_decode(*args, **kwargs):
            counts['typedDecodes'] += 1
            return typed(*args, **kwargs)
        def fast_decode(*args, **kwargs):
            counts['fastDecodes'] += 1
            return fast(*args, **kwargs)
        stack.enter_context(patch.object(PackageView, '_load', loaded))
        stack.enter_context(patch.object(resource_snapshot_io, 'read_stable_file', read_file))
        stack.enter_context(patch.object(structure_pose_inputs, 'load_shared_structure', typed_decode))
        stack.enter_context(patch.object(shared_structure_reference, 'load_shared_structure', typed_decode))
        stack.enter_context(patch.object(coordinate, 'load_shared_structure', typed_decode))
        stack.enter_context(patch.object(imported_fast_structure, 'decode_prepared', fast_decode))
        return counts

    def test_new_targets_consume_only_activation_payloads(self):
        _, get_engine, _ = self.factory()
        engine = get_engine()
        maps = tuple(engine.qt_pose_common_models.pack.by_id)[:2]
        with ExitStack() as stack:
            counts = self.counts(stack)
            solvers = [prepare_pose_solver(engine, mid, '-1')[0] for mid in maps]
        print('production-static-target-work', dict(counts))
        self.assertEqual(counts['viewLoads'], 0)
        self.assertEqual(counts['stableReads'], 0)
        self.assertEqual(counts['typedDecodes'], 2)
        self.assertEqual(counts['fastDecodes'], 2)
        self.assertEqual(len(solvers), 2)

    def test_four_modes_keep_original_decoded_arrays_sources_and_geometry(self):
        for season, mode in (('s0', 'difficult'), ('s0', 'nightmare'), ('s1', 'difficult'), ('s1', 'nightmare')):
            with self.subTest(season=season, mode=mode):
                _, get_engine, _ = self.factory(season, mode)
                engine = get_engine()
                static = engine.qt_pose_static_payloads
                self.assertIsNotNone(static)
                key = next(iter(static.common.layers))
                with ExitStack() as stack:
                    counts = self.counts(stack)
                    stack.enter_context(patch.object(Path, 'resolve', side_effect=AssertionError('target filesystem resolve')))
                    new = prepare_pose_solver(engine, *key)[0]
                self.assertEqual(counts, Counter(typedDecodes=1, fastDecodes=1))
                engine.qt_pose_input_cache.invalidate()
                with patch.object(engine, 'qt_pose_static_payloads', None):
                    old = prepare_pose_solver(engine, *key)[0]
                first, second = old.inputs, new.inputs
                self.assertEqual(first.binding, second.binding)
                self.assertEqual(first.gaps, second.gaps)
                self.assertEqual(first.capabilities, second.capabilities)
                self.assertEqual(first.preparation_resources, second.preparation_resources)
                left, right = first.references[key], second.references[key]
                self.assertEqual(left.metadata, right.metadata)
                self.assertEqual(left.path, right.path)
                self.assertEqual(left.resource_root, right.resource_root)
                for field in ('clean_mask', 'validation_mask', 'known_boundary', 'unknown_mask'):
                    np.testing.assert_array_equal(getattr(left, field), getattr(right, field))
                for field in ('distance', 'chains', 'rays', 'pitch', 'shape'):
                    np.testing.assert_array_equal(getattr(first.prepared(left), field), getattr(second.prepared(right), field))
                from test_structure_identity_observation import pixel_fixture
                _, frame = pixel_fixture()
                a, b = old.fit(frame, *key, lambda: False), new.fit(frame, *key, lambda: False)
                self.assertEqual(a.keys(), b.keys())
                def numerical(value):
                    if isinstance(value, dict):
                        return {name: numerical(item) for name, item in value.items() if name != 'elapsedMs'}
                    if isinstance(value, (tuple, list)):
                        return [numerical(item) for item in value]
                    return value.tolist() if isinstance(value, np.ndarray) else value
                self.assertEqual(numerical(a), numerical(b))

    def snapshot_fault(self, *, optional, changed=None, warm=False, cancel=False):
        prepare = structure_pose_inputs.prepare_structure_pose_static_payloads
        attempts, seen = Counter(), {}
        def at_activation(engine, **kwargs):
            stopped = []
            kwargs['cancelled'] = lambda: bool(stopped)
            common = engine.qt_pose_common_models
            key, (row, declaration) = next(iter(common.layers.items()))
            target = common._root / declaration[1].path if optional else row['path']
            seen.update(engine=engine, key=key, target=target)
            if warm:
                self.assertIsNotNone(engine.qt_shared_provider('shared-structure', *key))
            read = resource_snapshot_io.read_stable_file
            def fail_once(path, *args, **options):
                if Path(path).suffix == '.npz':
                    attempts[Path(path)] += 1
                if Path(path) == target and attempts[target] == 1:
                    if cancel:
                        stopped.append(True)
                    if changed is not None:
                        changed(engine, kwargs)
                    raise OSError('first-static-payload-failure')
                return read(path, *args, **options)
            with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=fail_once):
                return prepare(engine, **kwargs)
        return attempts, seen, patch.object(structure_pose_inputs, 'prepare_structure_pose_static_payloads', at_activation)

    def test_first_required_payload_failure_is_not_retried_or_hidden_by_warm_cache(self):
        for warm in (False, True):
            with self.subTest(warm=warm):
                _, get_engine, _ = self.factory()
                attempts, seen, fault = self.snapshot_fault(optional=False, warm=warm)
                with fault, self.assertRaisesRegex(RuntimeError, 'resource-package-file-unavailable') as raised:
                    get_engine()
                self.assertEqual(attempts[seen['target']], 1)
                self.assertEqual(raised.exception.__cause__.resource_file_ref.path,
                    seen['engine'].qt_pose_common_models._reader.relative_path(seen['target']))

    def test_first_optional_payload_failure_retains_gap_prefix_and_recovery_floor(self):
        _, get_engine, _ = self.factory()
        attempts, seen, fault = self.snapshot_fault(optional=True, warm=True)
        with fault:
            engine = get_engine()
        self.assertEqual(attempts[seen['target']], 1)
        self.assertTrue(all(count == 1 for count in attempts.values()), attempts)
        key = seen['key']
        recovery = next(floor for floor in engine.pack.floors if floor != key[1])
        from types import SimpleNamespace
        with ExitStack() as stack:
            counts = self.counts(stack)
            inputs = prepare_pose_solver(engine, *key, group=SimpleNamespace(verified_floors=(recovery,)))[0].inputs
        self.assertEqual(counts, Counter(typedDecodes=2, fastDecodes=1))
        self.assertIsNotNone(inputs.references[key])
        self.assertIsNone(inputs.preparations[key])
        self.assertIsNone(inputs.preparation_resources[key])
        self.assertIn('fast-prepared-unavailable:' + key[1], inputs.gaps)
        self.assertIsNotNone(inputs.preparations[key[0], recovery])

    def test_optional_failure_never_hides_cancel_or_owner_replacement(self):
        for kind in ('cancel', 'owner', 'selection'):
            with self.subTest(kind=kind):
                _, get_engine, _ = self.factory()
                def changed(engine, kwargs):
                    if kind == 'owner':
                        engine.side_entry_identity.entries = list(engine.side_entry_identity.entries)
                    elif kind == 'selection':
                        catalog = engine.qt_pose_common_models._reader.view.resource_root / 'catalog.json'
                        value = json.loads(catalog.read_bytes())
                        value['generation'] = uuid4().hex
                        catalog.write_text(json.dumps(value))
                attempts, seen, fault = self.snapshot_fault(optional=True, changed=changed, cancel=kind == 'cancel')
                expected = {'cancel': 'import-cancelled', 'owner': 'resource-binding-changed',
                            'selection': 'resource-catalog-superseded'}[kind]
                with fault, self.assertRaisesRegex(RuntimeError, expected):
                    get_engine()
                self.assertEqual(attempts[seen['target']], 1)

    def test_prepared_ram_survives_later_disk_damage_but_new_preparation_rejects_it(self):
        _, get_engine, _ = self.factory()
        engine = get_engine()
        key, (row, _) = next(iter(engine.qt_pose_common_models.layers.items()))
        path = row['path']
        path.write_bytes(b'x' * path.stat().st_size)
        with ExitStack() as stack:
            counts = self.counts(stack)
            inputs = prepare_pose_solver(engine, *key)[0].inputs
        self.assertEqual(counts, Counter(typedDecodes=1, fastDecodes=1))
        self.assertIsNotNone(inputs.preparations[key])
        with self.assertRaisesRegex(ValueError, 'integrity-mismatch'):
            structure_pose_inputs.prepare_structure_pose_static_payloads(engine)

    def test_managed_update_retires_old_ram_and_new_engine_prepares_normally(self):
        factories, get_engine, _ = self.factory()
        engine = get_engine()
        key = next(iter(engine.qt_pose_common_models.layers))
        prepare_pose_solver(engine, *key)
        factories.invalidate()
        with self.assertRaisesRegex(RuntimeError, 'resource-context-superseded'):
            prepare_pose_solver(engine, *key)
        current = get_engine()
        self.assertIsNot(current, engine)
        self.assertIsNot(current.qt_pose_static_payloads, engine.qt_pose_static_payloads)
        self.assertIsNotNone(prepare_pose_solver(current, *key)[0].inputs.preparations[key])

    def test_memory_budget_stays_two_targets_and_normal_lru_reads_keep_snapshot_current(self):
        source, _, digest, _ = self.cases['difficult']
        _, get_engine, _ = self.factory(raw=source.output.read_bytes())
        with patch.object(coordinate, 'EXPECTED_INDEX_SHA256', digest):
            engine = get_engine()
        static, cache = engine.qt_pose_static_payloads, engine.qt_pose_input_cache
        keys = tuple(static.references)[::len(engine.pack.floors)][:3]
        for key in keys:
            self.assertIsNotNone(engine.qt_shared_provider('shared-structure', *key))
            prepare_pose_solver(engine, *key)
        self.assertEqual(cache.max_entries, 2)
        self.assertEqual(len(cache._entries), 2)
        self.assertGreater(static.retained_bytes, engine.qt_s1_coordinate_recognition.retained_reference_bytes)
        self.assertLessEqual(static.retained_bytes + static.common.retained_bytes + cache.retained_bytes,
            structure_pose_inputs.DEFAULT_CACHE_BYTES)
        self.assertEqual(static.retained_bytes + static.common.retained_bytes + cache.max_bytes,
            structure_pose_inputs.DEFAULT_CACHE_BYTES)

    def test_missing_validation_authority_does_not_prepare_or_read_optional_fast_payload(self):
        import test_export_native_base_package as native_fixture
        source = native_fixture.NativeBaseExporterTests('runTest')
        source.setUp()
        self.addCleanup(source.doCleanups)
        row = next(row for row in source.rows if row['floor'] == '1')
        path = source.source / row['resourcePath']
        with np.load(path, allow_pickle=False) as archive:
            arrays = {name: archive[name] for name in archive.files}
        metadata = json.loads(str(arrays['metadata'].item()))
        metadata['authorizesStructureValidation'] = False
        arrays['metadata'] = np.asarray(json.dumps(metadata))
        np.savez_compressed(path, **arrays)
        source.export()
        _, get_engine, _ = self.factory('s0', 'nightmare', raw=source.output.read_bytes())
        prepare, attempted = structure_pose_inputs.prepare_structure_pose_static_payloads, []
        def at_activation(engine, **kwargs):
            common = engine.qt_pose_common_models
            _, declaration = common.layers[source.mid, '1']
            unused = common._root / declaration[1].path
            read = resource_snapshot_io.read_stable_file
            def refuse(path, *args, **options):
                if Path(path) == unused:
                    attempted.append(True)
                    raise AssertionError('unused optional fast payload was read')
                return read(path, *args, **options)
            with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=refuse):
                return prepare(engine, **kwargs)
        with patch.object(structure_pose_inputs, 'prepare_structure_pose_static_payloads', at_activation):
            engine = get_engine()
        with ExitStack() as stack:
            counts = self.counts(stack)
            inputs = prepare_pose_solver(engine, source.mid, '1')[0].inputs
        self.assertEqual(counts, Counter(typedDecodes=1))
        self.assertEqual(attempted, [])
        engine.qt_pose_input_cache.invalidate()
        with patch.object(engine, 'qt_pose_static_payloads', None):
            old = prepare_pose_solver(engine, source.mid, '1')[0].inputs
        self.assertEqual(inputs.gaps, old.gaps)
        self.assertIn('fast-structure-validation-unavailable:1', inputs.gaps)
        self.assertIs(inputs.references[source.mid, '1'].metadata['authorizesStructureValidation'], False)

    def test_small_byte_budget_keeps_original_target_reader_path(self):
        _, get_engine, _ = self.factory()
        prepare = structure_pose_inputs.prepare_structure_pose_static_payloads
        def small_budget(engine, **kwargs):
            engine.qt_pose_input_cache.max_bytes = 1
            return prepare(engine, **kwargs)
        with patch.object(structure_pose_inputs, 'prepare_structure_pose_static_payloads', small_budget):
            engine = get_engine()
        self.assertIsNone(engine.qt_pose_static_payloads)
        key = next(iter(engine.qt_pose_common_models.layers))
        with ExitStack() as stack:
            counts = self.counts(stack)
            inputs = prepare_pose_solver(engine, *key)[0].inputs
        self.assertGreater(counts['viewLoads'], 0)
        self.assertIsNotNone(inputs.preparations[key])

    def test_legacy_keeps_original_reader_and_replaced_guard_refuses_old_snapshot(self):
        for kind in ('legacy', 'guard'):
            with self.subTest(kind=kind):
                season, mode = ('s0', 'difficult') if kind == 'legacy' else ('s1', 'difficult')
                _, get_engine, _ = self.factory(season, mode)
                engine = get_engine()
                if kind == 'legacy':
                    key = self.archive(season, mode)[1], '1'
                    self.assertNotIn(key, engine.qt_pose_common_models.layers)
                else:
                    key = next(iter(engine.qt_pose_common_models.layers))
                    original = engine.qt_assert_resource_current
                    def guarded():
                        original()
                    engine.qt_assert_resource_current = guarded
                with ExitStack() as stack:
                    counts = self.counts(stack)
                    if kind == 'guard':
                        with self.assertRaisesRegex(ValueError, 'resource-binding-changed'):
                            prepare_pose_solver(engine, *key)
                        # A replaced owner of an installed snapshot is stale,
                        # rather than permission to fall through its guard.
                        self.assertEqual(counts, Counter())
                    else:
                        inputs = prepare_pose_solver(engine, *key)[0].inputs
                        self.assertGreater(counts['viewLoads'], 0)
                        self.assertEqual(inputs.references[key].metadata['sourceKind'], 'legacy-shared-binary')

    def test_published_snapshot_rejects_replaced_source_and_coordinate_reference_owner(self):
        source, _, digest, _ = self.cases['difficult']
        for kind in ('source', 'references'):
            with self.subTest(kind=kind):
                _, get_engine, _ = self.factory(raw=source.output.read_bytes())
                with patch.object(coordinate, 'EXPECTED_INDEX_SHA256', digest):
                    engine = get_engine()
                key = next(iter(engine.qt_pose_static_payloads.references))
                prepare_pose_solver(engine, *key)
                if kind == 'source':
                    engine.qt_fast_structure_source = object()
                else:
                    from types import MappingProxyType
                    engine.qt_s1_coordinate_recognition.references = MappingProxyType(dict(
                        engine.qt_s1_coordinate_recognition.references))
                with ExitStack() as stack:
                    counts = self.counts(stack)
                    with self.assertRaisesRegex(ValueError, 'resource-binding-changed'):
                        prepare_pose_solver(engine, *key)
                self.assertEqual(counts, Counter())

    def test_real_coordinate_helper_reuses_typed_references_without_files(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                source, _, digest, _ = self.cases[mode]
                _, get_engine, _ = self.factory('s1', mode, raw=source.output.read_bytes())
                with ExitStack() as stack:
                    activation = self.counts(stack)
                    stack.enter_context(patch.object(coordinate, 'EXPECTED_INDEX_SHA256', digest))
                    engine = get_engine()
                helper, static = engine.qt_s1_coordinate_recognition, engine.qt_pose_static_payloads
                self.assertIsNotNone(helper)
                self.assertIsNotNone(static)
                print('production-s1-static-activation-work', mode, dict(activation),
                    dict(compressedBytes=sum(map(len, static.payloads.values())),
                        referenceBytes=helper.retained_reference_bytes, staticBytes=static.retained_bytes,
                        commonBytes=static.common.retained_bytes, targetCacheMaxBytes=engine.qt_pose_input_cache.max_bytes))
                targets = tuple(static.references)[:2]
                with ExitStack() as stack:
                    counts = self.counts(stack)
                    stack.enter_context(patch.object(Path, 'resolve', side_effect=AssertionError('target filesystem resolve')))
                    inputs = [prepare_pose_solver(engine, *key)[0].inputs for key in targets]
                print('production-s1-static-target-work', mode, dict(counts))
                self.assertEqual(counts, Counter(fastDecodes=2))
                for key, value in zip(targets, inputs):
                    reference = value.references[key]
                    self.assertIs(reference, helper.references[key])
                    self.assertIsNotNone(value.prepared(reference), value.gaps)
                    with self.assertRaises(ValueError):
                        reference.clean_mask.setflags(write=True)

    def test_valid_author_display_preserves_s1_helper_and_cold_target_ram_path(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                source, _, digest, _ = self.cases[mode]
                _, get_engine, _ = self.factory('s1', mode, raw=source.output.read_bytes(), author_display=True)
                with patch.object(coordinate, 'EXPECTED_INDEX_SHA256', digest):
                    engine = get_engine()
                helper, static = engine.qt_s1_coordinate_recognition, engine.qt_pose_static_payloads
                self.assertIsNotNone(engine.qt_optional_displays)
                self.assertEqual(engine.qt_optional_displays.errors, {})
                self.assertIs(engine.qt_adapters, engine.qt_optional_displays.adapters)
                self.assertIsNot(engine.qt_adapters, engine.qt_native_base._values['adapters'])
                self.assertIn('author-fixture', engine.qt_adapters.profiles)
                self.assertIsNotNone(helper)
                self.assertTrue(helper.current())
                self.assertIsNotNone(static)
                targets = tuple((mid, helper.catalog.floors[0]) for mid in helper.catalog.map_ids[:2])
                self.assertTrue(all(engine.qt_pose_input_cache.get(engine, *key,
                    resource_revision=engine.qt_resource_revision) is None for key in targets))
                with ExitStack() as stack:
                    counts = self.counts(stack)
                    stack.enter_context(patch.object(Path, 'resolve', side_effect=AssertionError('author cold target filesystem resolve')))
                    inputs = [prepare_pose_solver(engine, *key)[0].inputs for key in targets]
                print('production-author-s1-static-target-work', mode, dict(counts))
                self.assertEqual(counts, Counter(fastDecodes=2))
                self.assertTrue(helper.current())
                for key, value in zip(targets, inputs):
                    self.assertIs(value.references[key], helper.references[key])
                    self.assertIsNotNone(value.preparations[key], value.gaps)


if __name__ == '__main__':
    unittest.main()
