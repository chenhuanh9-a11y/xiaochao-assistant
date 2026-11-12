"""Real S1 package/reader/preparation paths over explicit synthetic sources.

The tiny native fixture carries a matching synthetic coordinate index. Only its
existing expected-index pin is supplied as that fixture's digest. The real-data
probe separately uses the unchanged production index and no patched pin.
"""
from contextlib import ExitStack
from copy import copy
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np

import client_s1_coordinate_recognition as coordinate
from client_imported_providers import (SUPPORTED_CAPABILITIES, build_imported_engine_factories,
                                       build_native_engine)
from client_paths import RuntimePaths
from imported_native_base import decode_native_base, S1_COORDINATE_INTERFACE, S1_COORDINATE_FILES
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
from imported_resource_catalog import ResourceResolver
from imported_resource_reader import ComponentResourceReader
from imported_native_context import ImportedResourceOwners
from s1_common_coordinate_filter import CommonCoordinateFilter
from s1_pose_prior import AcknowledgedPose
from s1_private_experiment import PrivateCoordinateObserver
from s1_gate_anchor import GateAnchorLocator
from s1_structure_selection import StructureCatalog
from test_export_s1_native_base_package import S1NativeBaseSourceFixture
from test_structure_identity_observation import pixel_fixture


ROOT = Path(__file__).resolve().parents[2]


class S1CoordinateRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='s1-coordinate-fixture-')
        cls.addClassCleanup(temporary.cleanup)
        cls.root, cls.cases = Path(temporary.name), {}
        for mode in ('difficult', 'nightmare'):
            source = S1NativeBaseSourceFixture(cls.root / mode, mode=mode)
            for binding, relative in source.resources.items():
                for path, metadata_key in ((source.prefixRoot / relative, 'metadata'),
                        (source.prefixRoot / source.prepared_resources[binding], 'sourceMetadata')):
                    with np.load(path, allow_pickle=False) as archive:
                        data = {key: archive[key] for key in archive.files}
                    metadata = json.loads(str(data[metadata_key].item()))
                    metadata['sourceGeometry'] = dict(sourceCatalogSha256='c' * 64, rendererMetadataSha256='d' * 64)
                    data[metadata_key] = np.asarray(json.dumps(metadata))
                    np.savez_compressed(path, **data)
            source.refresh_after_source_edit()
            assets = cls.root / (mode + '-coordinate-assets')
            assets.mkdir()
            for name in S1_COORDINATE_FILES[1:]:
                (assets / name).write_bytes((ROOT / 's1-private-assets' / name).read_bytes())
            manifest = json.loads((assets / 'template_manifest.json').read_bytes())
            index = dict(schema='s1-common-entrance-coordinate-index-v1', sourceVersion='2.104.3099014',
                glyphTemplateSha256=manifest['template_sha256'], maps={mode: {mid: {floor: dict(
                    imageToEntranceXZ=[[1, 0, 0], [0, -1, 0], [0, 0, 1]], shapeHW=[8, 8],
                    sourceCatalogSha256='c' * 64, sourceRendererMetadataSha256='d' * 64)
                    for floor in source.floors} for mid in source.ids}})
            raw = json.dumps(index).encode()
            (assets / S1_COORDINATE_FILES[0]).write_bytes(raw)
            source.export(s1_coordinate_asset_dir=assets)
            saved = save_archive(source.output, cls.root / (mode + '-installed'))
            views = {ref.component_id: PackageView(cls.root / (mode + '-installed'), ref)
                     for ref in saved.component_refs}
            cls.cases[mode] = source, assets, hashlib.sha256(raw).hexdigest(), views

    def engine(self, mode='difficult'):
        _, _, digest, views = self.cases[mode]
        with patch.object(coordinate, 'EXPECTED_INDEX_SHA256', digest):
            return build_native_engine(views['base'], views['controls'], RuntimePaths(self.root / ('runtime-' + mode), False),
                season='s1', mode=mode)

    def author_engine(self, mode):
        """Actual display package + selected catalog + formal imported factory."""
        from client_providers import SharedReferenceProvider
        from test_imported_author_display import _AuthorDisplayFixture
        fixture = _AuthorDisplayFixture('runTest')
        self.addCleanup(fixture.doCleanups)
        temporary = tempfile.TemporaryDirectory(prefix='coordinate-author-')
        fixture.addCleanup(temporary.cleanup)
        fixture.root, fixture.source = Path(temporary.name), self.cases[mode][0]
        fixture.resources = fixture.root / 'resources'
        saved = save_archive(fixture.source.output, fixture.resources)
        fixture.refs = {ref.component_id: ref for ref in saved.component_refs}
        fixture.base_view = PackageView(fixture.resources, fixture.refs['base'])
        fixture.base = decode_native_base(fixture.base_view, season='s1', mode=mode)
        fixture.provider = SharedReferenceProvider(fixture.base_view.root, fixture.base.resources,
            fixture.base.versions, check_current=fixture.base.assert_current)
        fixture.counter = 0
        display, _, _ = fixture.package()
        (fixture.resources / 'catalog.json').write_text(json.dumps(dict(schema='xingtu-resource-catalog-v1',
            generation=uuid4().hex, contexts={f's1:{mode}': dict(
                recognitionBase=fixture.wire(fixture.refs['base']), controls=fixture.wire(fixture.refs['controls']),
                reviewedGroups=None)}, displays=[fixture.wire(display.component_ref)])), encoding='utf-8')
        resolver = ResourceResolver(fixture.resources, supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver, RuntimePaths(fixture.root, False))
        self.addCleanup(factories.invalidate)
        with patch.object(coordinate, 'EXPECTED_INDEX_SHA256', self.cases[mode][2]):
            return factories._get(mode, season='s1')

    def test_actual_author_display_preserves_native_coordinate_preparation_and_ram_prior(self):
        from imported_author_displays import _DisplayAdapters
        for mode in ('difficult', 'nightmare'):
            with self.subTest(mode=mode):
                engine = self.author_engine(mode)
                prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
                native = engine.qt_native_base._values['adapters']
                self.assertIs(type(engine.qt_adapters), _DisplayAdapters)
                self.assertIs(engine.qt_adapters._base, native)
                self.assertIn('author-fixture', engine.qt_optional_displays.profiles)
                self.assertNotIn('shared-structure', engine.qt_adapters.display_profiles)
                for floor in ('-1', '-4'):
                    np.testing.assert_array_equal(
                        engine.qt_adapters.matrix_profile_to_canonical('shared-structure', catalog.map_ids[0], floor),
                        native.matrix_profile_to_canonical('shared-structure', catalog.map_ids[0], floor))
                source = AcknowledgedPose(catalog.mode, catalog.resource_key, catalog.map_ids[0], '-1',
                    ('round',), 'frame', ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
                with ExitStack() as stack:
                    for owner, name in ((Path, 'read_bytes'), (Path, 'read_text'),
                            (ComponentResourceReader, '__call__'), (ComponentResourceReader, 'read_many')):
                        stack.enter_context(patch.object(owner, name,
                            side_effect=AssertionError('author-wrapper-caused-geometry-io')))
                    self.assertTrue(prepared.current())
                    self.assertIsNotNone(prepared.pose_prior(source, '-2', ('round',)))
                    report = prepared.prepare(np.zeros((160, 240, 3), np.uint8), catalog.map_ids, '-1',
                        cancelled=lambda: False)
                    self.assertNotEqual(report['reason'], 'coordinate-work-obsolete')
                    self.assertEqual(set(report['evaluatedMapIds']), set(catalog.map_ids))

    def test_author_wrapper_does_not_authorize_unknown_or_replaced_structure_adapters(self):
        engine = self.author_engine('difficult')
        prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
        native, display = engine.qt_native_base._values['adapters'], engine.qt_adapters
        source = AcknowledgedPose(catalog.mode, catalog.resource_key, catalog.map_ids[0], '-1',
            ('round',), 'frame', ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
        changes = (
            patch.object(engine, 'qt_adapters', copy(native)),
            patch.object(display, '_base', copy(native)),
            patch.object(display, 'display_profiles', set(display.display_profiles) | {'shared-structure'}),
            patch.dict(engine.qt_native_base._values, adapters=copy(native)))
        for index, change in enumerate(changes):
            with self.subTest(change=index), change, ExitStack() as stack:
                for owner, name in ((Path, 'read_bytes'), (Path, 'read_text'),
                        (ComponentResourceReader, '__call__'), (ComponentResourceReader, 'read_many')):
                    stack.enter_context(patch.object(owner, name, side_effect=AssertionError('adapter-check-read-files')))
                self.assertFalse(prepared.current())
                self.assertIsNone(prepared.pose_prior(source, '-2', ('round',)))
        self.assertTrue(prepared.current())

    def test_declared_assets_are_admitted_and_attach_original_algorithms(self):
        for mode, (source, _, _, views) in self.cases.items():
            with self.subTest(mode=mode):
                bundle = decode_native_base(views['base'], season='s1', mode=mode)
                self.assertIn(S1_COORDINATE_INTERFACE, bundle.interfaces)
                engine = self.engine(mode)
                prepared = engine.qt_s1_coordinate_recognition
                self.assertIsNotNone(prepared, engine.qt_s1_coordinate_capability)
                self.assertIsInstance(prepared.observer, PrivateCoordinateObserver)
                self.assertTrue(callable(engine.qt_s1_private_coordinate_prepare))
                self.assertTrue(callable(engine.qt_s1_pose_prior))
                self.assertEqual(len(prepared.references), len(source.ids) * 4)
                self.assertEqual(len(prepared.observer.filter._cache), 0)
                self.assertEqual(prepared.observer.filter.retained_pool_bytes, 0)
                self.assertTrue(engine.qt_pose_common_models is not None)

    def test_old_package_has_specific_gap_and_no_software_asset_fallback(self):
        source = S1NativeBaseSourceFixture(self.root / 'old-package')
        source.export()
        saved = save_archive(source.output, self.root / 'old-installed')
        views = {ref.component_id: PackageView(self.root / 'old-installed', ref) for ref in saved.component_refs}
        engine = build_native_engine(views['base'], views['controls'], RuntimePaths(self.root / 'old-runtime', False),
            season='s1', mode='difficult')
        self.assertIsNone(engine.qt_s1_private_coordinate_prepare)
        self.assertIsNone(engine.qt_s1_pose_prior)
        self.assertEqual(engine.qt_s1_coordinate_capability,
            dict(available=False, gaps=['s1-coordinate-recognition-not-declared']))

    def test_prepared_frame_work_and_prior_do_not_read_files(self):
        engine = self.engine()
        prepared = engine.qt_s1_coordinate_recognition
        catalog = StructureCatalog.from_engine(engine)
        mid, floor = catalog.map_ids[0], catalog.floors[0]
        source = AcknowledgedPose(catalog.mode, catalog.resource_key, mid, floor, ('round',), 'frame',
            ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
        with ExitStack() as stack:
            for owner, name in ((Path, 'read_bytes'), (Path, 'read_text'), (ComponentResourceReader, '__call__'),
                                (ComponentResourceReader, 'read_many')):
                stack.enter_context(patch.object(owner, name, side_effect=AssertionError('per-frame-resource-io')))
            report = prepared.prepare(np.zeros((160, 240, 3), np.uint8), catalog.map_ids, floor, cancelled=lambda: False)
            hint = prepared.pose_prior(source, '-2', ('round',))
        self.assertFalse(report['coordinateVerified'])
        self.assertEqual(set(report['evaluatedMapIds']), set(catalog.map_ids))
        self.assertIsNotNone(hint)
        np.testing.assert_array_equal(hint.matrix, source.matrix)
        self.assertFalse(hint.identity_authorized)
        self.assertFalse(hint.display_authorized)
        with self.assertRaises(ValueError):
            prepared.references[mid, floor].clean_mask.setflags(write=True)

    def test_cancel_and_existing_resource_revision_reject_without_pose(self):
        engine = self.engine()
        prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
        frame = np.zeros((160, 240, 3), np.uint8)
        report = prepared.prepare(frame, catalog.map_ids, '-1', cancelled=lambda: True)
        self.assertEqual(report['reason'], 'coordinate-work-obsolete')
        self.assertFalse(report['coordinateVerified'])
        engine.qt_resource_revision = 7
        self.assertTrue(prepared.current())  # Existing factory publication revision.
        engine.qt_resource_revision = 8
        report = prepared.prepare(frame, catalog.map_ids, '-1', cancelled=lambda: False)
        self.assertEqual(report['reason'], 'coordinate-work-obsolete')
        fit = prepared.fixed_fit(frame, catalog.map_ids[0], '-1', lambda: False, report={})
        self.assertFalse(fit['success'])
        self.assertNotIn('matrixSourceToClient', fit)

    def test_selection_guard_replacement_rejects_prepared_paths_without_file_reads(self):
        engine = self.engine()
        prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
        engine.qt_resource_revision = 7
        self.assertTrue(prepared.current())
        owners = engine.qt_shared_provider._operation_owners
        source = AcknowledgedPose(catalog.mode, catalog.resource_key, catalog.map_ids[0], '-1',
            ('round',), 'frame', ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
        frame = np.zeros((160, 240, 3), np.uint8)
        for label, view in (('base', owners.base_view), ('controls', owners.controls_view)):
            with self.subTest(view=label):
                before = owners.read_state()
                with patch.object(view, '_assert_current', side_effect=ValueError('selection-owner-replaced')):
                    self.assertNotEqual(owners.read_state(), before)
                    with self.assertRaisesRegex(ValueError, 'selection-owner-replaced'):
                        engine.qt_assert_resource_current()
                    with ExitStack() as stack:
                        for owner, name in ((Path, 'read_bytes'), (Path, 'read_text'),
                                (ComponentResourceReader, '__call__'), (ComponentResourceReader, 'read_many')):
                            stack.enter_context(patch.object(owner, name,
                                side_effect=AssertionError('owner-check-read-files')))
                        self.assertFalse(prepared.current())
                        self.assertIsNone(prepared.pose_prior(source, '-2', ('round',)))
                        report = prepared.prepare(frame, catalog.map_ids, '-1', cancelled=lambda: False)
                        self.assertEqual(report['reason'], 'coordinate-work-obsolete')
                        fit = prepared.fixed_fit(frame, catalog.map_ids[0], '-1', lambda: False, report={})
                        self.assertEqual(fit['reason'], 'structure-work-obsolete')
                        self.assertNotIn('matrixSourceToClient', fit)
                self.assertTrue(prepared.current())

    def test_same_class_view_and_source_owner_replacements_reject_without_io(self):
        engine = self.engine()
        prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
        owners = engine.qt_shared_provider._operation_owners
        reader = engine.qt_native_base._values['reader']
        base_alias = PackageView(owners.base_view.resource_root, owners.base_view.component_ref)
        controls_alias = PackageView(owners.controls_view.resource_root, owners.controls_view.component_ref)
        replacement = ImportedResourceOwners(owners.base_view, owners.bundle, owners.controls,
            native=owners.native, legacy=owners.legacy)
        source = AcknowledgedPose(catalog.mode, catalog.resource_key, catalog.map_ids[0], '-1',
            ('round',), 'frame', ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
        frame = np.zeros((160, 240, 3), np.uint8)
        for label, target, attribute, value in (
                ('base-view', owners, 'base_view', base_alias),
                ('reader-view', reader, 'view', base_alias),
                ('controls-view', owners.controls, '_view', controls_alias),
                ('source-owner', engine.qt_shared_provider, '_operation_owners', replacement)):
            with self.subTest(owner=label), patch.object(target, attribute, value), ExitStack() as stack:
                for owner, name in ((Path, 'read_bytes'), (Path, 'read_text'),
                        (ComponentResourceReader, '__call__'), (ComponentResourceReader, 'read_many')):
                    stack.enter_context(patch.object(owner, name,
                        side_effect=AssertionError('owner-check-read-files')))
                self.assertFalse(prepared.current())
                self.assertIsNone(prepared.pose_prior(source, '-2', ('round',)))
                self.assertEqual(prepared.prepare(frame, catalog.map_ids, '-1', cancelled=lambda: False)['reason'],
                                 'coordinate-work-obsolete')
                self.assertEqual(prepared.fixed_fit(frame, catalog.map_ids[0], '-1', lambda: False,
                                 report={})['reason'], 'structure-work-obsolete')
        self.assertTrue(prepared.current())

    def test_first_reader_failure_and_original_index_pin_never_fall_back(self):
        engine = self.engine()
        catalog = StructureCatalog.from_engine(engine)
        reader = engine.qt_native_base.reader
        with patch.object(reader, 'read_many', side_effect=ValueError('first-coordinate-batch-failed')):
            failed = coordinate.attach_imported_s1_coordinate_recognition(engine, catalog)
        self.assertIsNone(failed)
        self.assertIn('first-coordinate-batch-failed', engine.qt_s1_coordinate_capability['gaps'])
        self.assertIsNone(engine.qt_s1_private_coordinate_prepare)
        failed = coordinate.attach_imported_s1_coordinate_recognition(engine, catalog,
            expected_index_sha256='0' * 64)
        self.assertIsNone(failed)
        self.assertIn('pose-prior-index-digest-mismatch', engine.qt_s1_coordinate_capability['gaps'])

    def test_immutable_prior_does_not_wait_for_background_pool_lane_and_still_expires(self):
        engine = self.engine()
        prepared, catalog = engine.qt_s1_coordinate_recognition, StructureCatalog.from_engine(engine)
        engine.qt_resource_revision = 7
        self.assertTrue(prepared.current())
        source = AcknowledgedPose(catalog.mode, catalog.resource_key, catalog.map_ids[0], '-1',
            ('round',), 'frame', ((1., 0, 30), (0, 1., 40), (0, 0, 1.)), True, True)
        entered, release = threading.Event(), threading.Event()
        frame = np.zeros((160, 240, 3), np.uint8)
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test-background-release-missing')
            return dict(coordinateVerified=True)
        with patch.object(prepared.observer, 'prepare', side_effect=blocked):
            worker = threading.Thread(target=lambda: prepared.prepare(frame, catalog.map_ids, '-1', cancelled=lambda: False))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                result, finished = [], threading.Event()
                reader = threading.Thread(target=lambda: (result.append(prepared.pose_prior(source, '-2', ('round',))),
                                                          finished.set()))
                reader.start()
                try:
                    self.assertTrue(finished.wait(1), 'immutable prior waited for mutable pool calculation')
                    self.assertIsNotNone(result[0])
                    engine.qt_resource_revision = 8
                    self.assertIsNone(prepared.pose_prior(source, '-2', ('round',)))
                    self.assertEqual(prepared.fixed_fit(frame, catalog.map_ids[0], '-1', lambda: False,
                        report={})['reason'], 'structure-work-obsolete')
                finally:
                    release.set()
                    reader.join(2)
            finally:
                release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())

    def test_metadata_mismatch_is_a_gap_not_fake_coordinate_bridge(self):
        engine = self.engine()
        catalog = StructureCatalog.from_engine(engine)
        original = coordinate.load_shared_structure
        def wrong(*args, **kwargs):
            reference = original(*args, **kwargs)
            metadata = deepcopy(reference.metadata)
            metadata['sourceGeometry']['sourceCatalogSha256'] = '0' * 64
            from dataclasses import replace
            return replace(reference, metadata=metadata)
        with patch.object(coordinate, 'load_shared_structure', side_effect=wrong):
            failed = coordinate.attach_imported_s1_coordinate_recognition(engine, catalog,
                expected_index_sha256=self.cases['difficult'][2])
        self.assertIsNone(failed)
        self.assertEqual(engine.qt_s1_coordinate_capability['gaps'], ['pose-prior-resource-binding-mismatch'])

    def test_native_admission_rejects_mismatched_declared_index_and_s0_export(self):
        source = S1NativeBaseSourceFixture(self.root / 'bad-index')
        assets = self.root / 'bad-index-assets'
        assets.mkdir()
        original = self.cases['difficult'][1]
        for name in S1_COORDINATE_FILES:
            (assets / name).write_bytes((original / name).read_bytes())
        index = json.loads((assets / S1_COORDINATE_FILES[0]).read_bytes())
        # These source files have no sourceGeometry declaration at all.
        # The package must reject the supplied coordinate metadata at admission.
        with self.assertRaisesRegex(ValueError, 's1-coordinate-reference-binding-mismatch'):
            source.export(s1_coordinate_asset_dir=assets)
        self.assertFalse(source.output.exists())
        from test_export_native_base_package import NativeBaseExporterTests
        s0 = NativeBaseExporterTests('runTest')
        s0.setUp()
        self.addCleanup(s0.doCleanups)
        with self.assertRaisesRegex(ValueError, 's1-coordinate-assets-context'):
            s0.export(s1_coordinate_asset_dir=assets)

    def test_prepared_pool_rejects_different_resource_source_floor_and_scope(self):
        engine = self.engine()
        common = engine.qt_s1_coordinate_recognition.observer.filter
        mid = next(iter(common.entries))
        data = common._pool((mid,), '-2', lambda: False)
        frame = np.zeros((160, 240, 3), np.uint8)
        for changes in (dict(resourceKey='old'), dict(floor='-1'), dict(ids=('other',)),
                        dict(sourceProvider=lambda *args: None), dict(sourceIndex={})):
            with self.subTest(changes=tuple(changes)):
                report = common.evaluate(frame, [], (mid,), '-2', prepared_pool=dict(data, **changes))
                self.assertFalse(report['coordinateVerified'])
                self.assertEqual(report['detail'], 'prepared-pool-context-mismatch')

    def test_oversized_pool_is_computed_once_per_frame_without_omitting_comparison(self):
        mask, frame = pixel_fixture()
        meta = dict(sourceVersion='fixture', sourceGeometry=dict(sourceCatalogSha256='c', rendererMetadataSha256='r'))
        provider = lambda mid, floor: SimpleNamespace(clean_mask=mask, metadata=meta)
        entry = dict(shapeHW=list(mask.shape), sourceCatalogSha256='c', sourceRendererMetadataSha256='r',
                     imageToEntranceXZ=[[1, 0, 0], [0, -1, 0], [0, 0, 1]])
        index = dict(schema='s1-common-entrance-coordinate-index-v1', sourceVersion='fixture',
                     maps={'difficult': {mid: {'-2': entry} for mid in ('a', 'b')}})
        observer = PrivateCoordinateObserver.__new__(PrivateCoordinateObserver)
        observer.locator = GateAnchorLocator(asset_dir=ROOT / 's1-private-assets')
        observer.filter = CommonCoordinateFilter(provider, index, mode='difficult', resource_key='fixture', max_cache_bytes=0)
        with patch.object(observer.filter, '_build_pool', wraps=observer.filter._build_pool) as build:
            report = observer.prepare(frame, ('a', 'b'), '-2', cancelled=lambda: False)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(observer.filter.retained_pool_bytes, 0)
            self.assertTrue(report['coordinateVerified'], report)
            self.assertEqual([row['mapId'] for row in report['candidateChecks']], ['a', 'b'])
            observer.prepare(frame, ('a', 'b'), '-2', cancelled=lambda: False)
            self.assertEqual(build.call_count, 2)


if __name__ == '__main__':
    unittest.main()
