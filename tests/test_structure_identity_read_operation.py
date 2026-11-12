"""Real exported bytes through the imported base/controls operation boundary."""
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4
import weakref
import zipfile

import resource_snapshot_io
from client_imported_providers import load_native_resources
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import PackageView
from structure_identity_resources import StructureIdentityResources
import test_structure_identity_resources as native_fixture
import test_export_s0_difficult_native_base_package as difficult_fixture
import test_imported_s1_native_base as s1_fixture
from test_export_s1_native_base_package import S1NativeBaseSourceFixture


def guarded_view(view, component_id):
    # Independent installed roots isolate controls damage from the base manifest.
    catalog = view.resource_root / 'catalog.json'
    catalog.write_text(json.dumps(dict(schema='xingtu-resource-catalog-v1',
        generation=str(uuid4()), contexts={}, displays=[])), encoding='utf-8')
    snapshot = ResourceResolver(view.resource_root).snapshot()
    return PackageView(view.resource_root, replace(view.component_ref, component_id=component_id),
                       assert_current=snapshot.assert_current)


class StructureIdentityReadOperationTests(unittest.TestCase):
    setUpClass = native_fixture.StructureIdentityResourcesTests.__dict__['setUpClass']
    setUp = native_fixture.StructureIdentityResourcesTests.setUp
    view = native_fixture.StructureIdentityResourcesTests.view
    build = native_fixture.StructureIdentityResourcesTests.build

    def components(self):
        base = guarded_view(self.view(), 'base')
        controls = guarded_view(self.view(), 'controls')
        bundle, profile, native, provider, _ = load_native_resources(base, controls)
        return base, controls, bundle, profile, native, provider

    @staticmethod
    def change(view, kind):
        if kind == 'catalog':
            path = view.resource_root / 'catalog.json'
            document = json.loads(path.read_bytes())
            document['generation'] = str(uuid4())
            path.write_text(json.dumps(document), encoding='utf-8')
        else:
            path = view.root.parent.parent / 'manifest.json'
            replacement = path.with_suffix('.replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)

    def test_physical_base_controls_proofs_do_not_grow_with_layer_count(self):
        counts = []
        for floor in ('1', None):
            base, controls, bundle, _, _, provider = self.components()
            with patch.object(base, '_load', wraps=base._load) as base_load, \
                 patch.object(controls, '_load', wraps=controls._load) as controls_load:
                result = self.build(bundle, provider, floor=floor)
            self.assertEqual(len(result.index.domain), 1 if floor else 3)
            counts.append((base_load.call_count, controls_load.call_count))
        self.assertEqual(counts[0], counts[1], counts)
        self.assertGreater(counts[0][1], 0)

    def test_last_decode_controls_manifest_and_catalog_changes_reject_publication(self):
        for kind in ('manifest', 'catalog'):
            with self.subTest(kind=kind):
                _, controls, bundle, _, _, provider = self.components()
                source, changed = bundle.fast_structure_source, []
                decode = source.decode
                def change_after_last(raw, reference):
                    result = decode(raw, reference)
                    if reference.metadata['binding']['floor'] == 'B1':
                        self.change(controls, kind)
                        changed.append(True)
                    return result
                with patch.object(source, 'decode', side_effect=change_after_last):
                    with self.assertRaisesRegex(ValueError, 'manifest-replaced|catalog-superseded'):
                        self.build(bundle, provider)
                self.assertTrue(changed)

    def test_final_payload_proof_still_checks_controls_after_actual_reread(self):
        for kind in ('manifest', 'catalog'):
            with self.subTest(kind=kind):
                base, controls, bundle, _, _, provider = self.components()
                final, changed = [], []
                check, read = StructureIdentityResources.check_current, base._read_payload
                def final_check(resources, **kwargs):
                    final.append(True)
                    return check(resources, **kwargs)
                def change_during_reread(*args):
                    raw = read(*args)
                    if final and not changed:
                        self.change(controls, kind)
                        changed.append(True)
                    return raw
                with patch.object(StructureIdentityResources, 'check_current', final_check), \
                     patch.object(base, '_read_payload', side_effect=change_during_reread):
                    with self.assertRaisesRegex(ValueError, 'manifest-replaced|catalog-superseded'):
                        self.build(bundle, provider)
                self.assertTrue(changed)

    def test_last_decode_controls_owner_change_rejects_publication(self):
        _, _, bundle, controls, _, provider = self.components()
        foreign = guarded_view(self.view(), 'controls')
        source, decode = bundle.fast_structure_source, bundle.fast_structure_source.decode
        def change_owner(raw, reference):
            result = decode(raw, reference)
            if reference.metadata['binding']['floor'] == 'B1':
                controls._view = foreign
                controls._root = foreign.root
            return result
        with patch.object(source, 'decode', side_effect=change_owner):
            with self.assertRaisesRegex(ValueError, 'resource-owner-changed'):
                self.build(bundle, provider)

    def test_success_operation_releases_batch_before_final_actual_read(self):
        _, _, bundle, _, _, provider = self.components()
        source, batches = bundle.fast_structure_source, []
        prepare, check = source.prepare_batch, StructureIdentityResources.check_current
        def capture_batch(*args, **kwargs):
            batch = prepare(*args, **kwargs)
            batches.append(weakref.ref(batch))
            return batch
        def final_check(resources, **kwargs):
            self.assertEqual(len(batches), 1)
            self.assertIsNone(batches[0]())
            return check(resources, **kwargs)
        with patch.object(source, 'prepare_batch', side_effect=capture_batch), \
             patch.object(StructureIdentityResources, 'check_current', final_check):
            self.assertTrue(self.build(bundle, provider).geometry_complete)

    def test_unknown_legacy_loader_keeps_original_guarded_path(self):
        _, _, bundle, _, _, provider = self.components()
        provider._legacy_reference_loader = Mock(return_value=None)
        begin, operations = provider.begin_read_operation, []
        def capture_operation(*args, **kwargs):
            operation = begin(*args, **kwargs)
            operations.append(operation)
            return operation
        with patch.object(provider, 'begin_read_operation', side_effect=capture_operation):
            self.assertTrue(self.build(bundle, provider).geometry_complete)
        self.assertEqual(operations, [None])
        self.assertEqual(provider._legacy_reference_loader.call_count, 3)

    def test_failed_batch_keeps_optional_prefix_gap_and_warm_typed_error(self):
        for kind in ('optional', 'warm-typed'):
            with self.subTest(kind=kind):
                base, _, bundle, _, _, provider = self.components()
                source = bundle.fast_structure_source
                key = next(key for key in source.entries if key[3] == '1')
                if kind == 'warm-typed':
                    self.assertIsNotNone(provider(key[0], key[1], key[3]))
                target = base.root / source.entries[key][0 if kind == 'warm-typed' else 1].path
                read, attempts = resource_snapshot_io.read_stable_file, []
                begin, operations = provider.begin_read_operation, []
                def fail_first(path, *args, **kwargs):
                    if Path(path) == target:
                        attempts.append(True)
                        if len(attempts) == 1:
                            raise OSError('first real payload failure')
                    return read(path, *args, **kwargs)
                def capture_operation(*args, **kwargs):
                    operation = begin(*args, **kwargs)
                    operations.append(operation)
                    return operation
                with patch.object(resource_snapshot_io, 'read_stable_file', side_effect=fail_first), \
                     patch.object(provider, 'begin_read_operation', side_effect=capture_operation):
                    if kind == 'warm-typed':
                        with self.assertRaisesRegex(ValueError, 'file-unavailable'):
                            self.build(bundle, provider)
                    else:
                        result = self.build(bundle, provider)
                        self.assertIn('saved-footprint-preparation-unavailable',
                            [entry.unavailable_reason for entry in result.index.entries])
                self.assertEqual(operations, [None])


class StructureIdentityLegacyReadOperationTests(unittest.TestCase):
    setUpClass = difficult_fixture.S0DifficultNativeBaseTests.__dict__['setUpClass']
    setUp = difficult_fixture.S0DifficultNativeBaseTests.setUp
    view = difficult_fixture.S0DifficultNativeBaseTests.view
    build = native_fixture.StructureIdentityResourcesTests.build

    def test_native_pool_avoids_legacy_probe_but_real_legacy_reader_remains(self):
        base = guarded_view(self.view(), 'base')
        controls = guarded_view(self.view(), 'controls')
        bundle, _, native, provider, _ = load_native_resources(base, controls, mode='difficult')
        legacy = bundle.legacy_references
        legacy_count = len(legacy.bindings)
        with patch.object(legacy, 'assert_current', wraps=legacy.assert_current) as current:
            result = self.build(bundle, provider)
        legacy_rows = [row for row in result.sources if row.source_kind == 'legacy-shared-binary']
        self.assertEqual(len(legacy_rows), legacy_count)
        self.assertTrue(all(not row.geometry_searchable for row in legacy_rows))
        self.assertLessEqual(current.call_count, 3 * legacy_count + 4)
        source, decode = bundle.fast_structure_source, bundle.fast_structure_source.decode
        def change_native_owner(raw, reference):
            result = decode(raw, reference)
            native._controls = object()
            return result
        with patch.object(source, 'decode', side_effect=change_native_owner):
            with self.assertRaisesRegex(ValueError, 'resource-owner-changed'):
                self.build(bundle, provider)


class StructureIdentityS1ReadOperationTests(unittest.TestCase):
    setUp = s1_fixture.ImportedS1NativeBaseTests.setUp
    view = s1_fixture.ImportedS1NativeBaseTests.view
    build = native_fixture.StructureIdentityResourcesTests.build

    def test_both_s1_pools_use_the_same_operation_without_gaining_authority(self):
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                source = S1NativeBaseSourceFixture(self.root / mode, mode=mode)
                source.export()
                self.archive = source.output
                with zipfile.ZipFile(self.archive) as archive:
                    self.members = {name: archive.read(name) for name in archive.namelist()}
                base, controls = guarded_view(self.view(), 'base'), guarded_view(self.view(), 'controls')
                bundle, _, _, provider, _ = load_native_resources(base, controls, season='s1', mode=mode)
                original_rank_ready = bundle.rank_ready
                self.assertTrue(original_rank_ready)  # This fixture admits proposal ranking only.
                begin, operations = provider.begin_read_operation, []
                def capture_operation(*args, **kwargs):
                    operation = begin(*args, **kwargs)
                    operations.append(operation)
                    return operation
                with patch.object(provider, 'begin_read_operation', side_effect=capture_operation):
                    result = self.build(bundle, provider)
                self.assertEqual(len(result.index.domain), len(bundle.pack.maps) * 4)
                self.assertEqual(len(operations), 1)
                self.assertIsNotNone(operations[0])
                self.assertTrue(operations[0].closed)
                self.assertFalse(result.authorizesIdentity)
                self.assertFalse(result.lock)
                self.assertEqual(result.original_rank_ready, original_rank_ready)

