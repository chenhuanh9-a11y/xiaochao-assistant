"""Real imported resources through region arbitration; numeric fits controlled.

These tests exercise resource reuse, invalidation and offers, not recognition
accuracy. The actual archive/decoder/provider/prepared loader remain in use.
"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

import fast_structure_alignment as fast
import resource_snapshot_io
from alignment_observation import canonical_residual_evidence
from client_providers import SharedReferenceProvider
from imported_native_base import decode_native_base
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
from imported_resource_reader import ComponentResourceReader
from structure_pose_solver import StructurePoseSolver
from structure_pose_resources import StructureFitResources
import test_export_native_base_package as exported_fixture
import test_imported_resource_reader as reader_fixture


class StructurePoseResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = exported_fixture.NativeBaseExporterTests('runTest')
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        fixture.export()
        cls.package_bytes = fixture.output.read_bytes()
        cls.map_id = fixture.mid

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='structure-fit-resources-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        package = self.root / 'fixture.xingtu-base'
        package.write_bytes(self.package_bytes)
        saved = save_archive(package, self.root / 'resources')
        base = next(ref for ref in saved.component_refs if ref.component_id == 'base')
        self.bind_view(PackageView(self.root / 'resources', base))
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.observed = []

    def bind_view(self, view):
        self.view = view
        self.bundle = decode_native_base(self.view)
        self.provider = SharedReferenceProvider(self.view.root, self.bundle.resources,
            self.bundle.versions, check_current=self.bundle.assert_current)
        self.engine = SimpleNamespace(pack=self.bundle.pack, qt_adapters=self.bundle.adapters,
            qt_shared_provider=self.provider, qt_fast_structure_source=self.bundle.fast_structure_source,
            qt_assert_resource_current=self.bundle.assert_current)
        self.host = StructurePoseSolver(self.engine)

    def test_required_and_optional_faults_hit_real_short_and_long_file_paths(self):
        alias = reader_fixture.ComponentResourceReaderTests.short_path_alias(self, self.view.resource_root)
        self.bind_view(PackageView(alias, self.view.component_ref))
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        entry = source.entries[('zhan10-speed', self.map_id, 'variant-01', '1')]
        actual_path = self.view.path(entry[0].path)
        self.assertNotEqual(reference.path, actual_path)
        self.assertTrue(reference.path.samefile(actual_path))
        self.test_first_required_read_failure_is_not_an_optional_miss()
        self.test_optional_failure_rechecks_required_inputs_before_returning_none()

    def preparation_path(self):
        entry = self.engine.qt_fast_structure_source.entries[('zhan10-speed', self.map_id, 'variant-01', '1')]
        return self.view.path(entry[1].path)

    def test_source_reads_required_and_optional_bytes_in_one_component_scope(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        manifest = self.view.root.parent.parent / 'manifest.json'
        with patch.object(resource_snapshot_io, 'read_stable_file',
                          wraps=resource_snapshot_io.read_stable_file) as read:
            raw, _ = source.read(reference)
        self.assertIsNotNone(source.decode(raw, reference))
        # Initial source ownership plus the batch's before/after manifest
        # proof, including content preflight. No separate post-batch reload.
        self.assertEqual(sum(Path(call.args[0]) == manifest for call in read.call_args_list), 3)

    def test_manifest_replaced_during_content_preflight_rejects_source_read(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        manifest = self.view.root.parent.parent / 'manifest.json'
        replacement = manifest.with_name('replacement-manifest.json')
        replacement.write_bytes(manifest.read_bytes())
        original = source.reader._check_payload
        changed = []
        def preflight(raw, extension):
            result = original(raw, extension)
            if extension == '.npz' and not changed:
                replacement.replace(manifest)
                changed.append(True)
            return result
        with patch.object(source.reader, '_check_payload', side_effect=preflight):
            with self.assertRaisesRegex(ValueError, 'manifest-replaced'):
                source.read(reference)
        self.assertTrue(changed)

    def test_source_root_changed_during_content_preflight_is_rejected(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        original = source.reader._check_payload
        def preflight(raw, extension):
            result = original(raw, extension)
            if extension == '.npz':
                source.root = self.root / 'different-component'
            return result
        with patch.object(source.reader, '_check_payload', side_effect=preflight):
            with self.assertRaisesRegex(ValueError, 'component-root-changed'):
                source.read(reference)

    def test_cancel_during_content_preflight_discards_payloads(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        original = source.reader._check_payload
        stopped = []
        def preflight(raw, extension):
            result = original(raw, extension)
            if extension == '.npz':
                stopped.append(True)
            return result
        with patch.object(source.reader, '_check_payload', side_effect=preflight):
            with self.assertRaisesRegex(ValueError, 'import-cancelled'):
                source.read(reference, cancelled=lambda: bool(stopped))

    def test_component_moved_during_preflight_is_not_returned(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        component = self.view.root
        moved = self.root / 'moved-component'
        for target in (component, moved):
            self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
        original = source.reader._check_payload
        changed = []
        def preflight(raw, extension):
            result = original(raw, extension)
            if extension == '.npz' and not changed:
                component.rename(moved)
                changed.append(True)
            return result
        try:
            with patch.object(source.reader, '_check_payload', side_effect=preflight):
                with self.assertRaisesRegex(ValueError, 'component-root-unavailable'):
                    source.read(reference)
        finally:
            if moved.exists():
                moved.rename(component)
        self.assertTrue(changed)

    def test_distinct_reader_view_keeps_its_source_manifest_proof(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        source.reader = ComponentResourceReader(PackageView(self.view.resource_root, self.view.component_ref))
        manifest = self.view.root.parent.parent / 'manifest.json'
        with patch.object(resource_snapshot_io, 'read_stable_file',
                          wraps=resource_snapshot_io.read_stable_file) as read:
            raw, _ = source.read(reference)
        self.assertIsNotNone(source.decode(raw, reference))
        # The separately pinned source view still needs its own final proof.
        self.assertEqual(sum(Path(call.args[0]).samefile(manifest) for call in read.call_args_list), 4)

    def test_optional_failure_rechecks_required_inputs_before_returning_none(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        self.preparation_path().write_bytes(b'broken-optional-preparation')
        real_read = resource_snapshot_io.read_stable_file
        def change_after_reference(path, *args, **kwargs):
            result = real_read(path, *args, **kwargs)
            if Path(path).samefile(reference.path):
                reference.path.write_bytes(b'changed-required-reference')
            return result
        with patch.object(resource_snapshot_io, 'read_stable_file', change_after_reference):
            with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
                source.read(reference)

    def test_first_required_read_failure_is_not_an_optional_miss(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        for failed_path in (reference.path, source.root / source.index_path):
            attempts = []
            real_read = resource_snapshot_io.read_stable_file
            def fail_once(path, *args, **kwargs):
                if Path(path).samefile(failed_path):
                    attempts.append(path)
                    if len(attempts) == 1:
                        raise OSError('one-shot required read failure')
                return real_read(path, *args, **kwargs)
            with self.subTest(path=failed_path), \
                 patch.object(resource_snapshot_io, 'read_stable_file', fail_once):
                with self.assertRaisesRegex(ValueError, 'file-unavailable'):
                    source.read(reference)
                self.assertEqual(len(attempts), 1)

    def test_first_optional_read_failure_keeps_the_existing_fallback(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        failed_path = self.preparation_path()
        for strict in (False, True):
            attempts = []
            real_read = resource_snapshot_io.read_stable_file
            def fail_once(path, *args, **kwargs):
                if Path(path).samefile(failed_path):
                    attempts.append(path)
                    if len(attempts) == 1:
                        raise OSError('one-shot optional read failure')
                return real_read(path, *args, **kwargs)
            with self.subTest(strict=strict), \
                 patch.object(resource_snapshot_io, 'read_stable_file', fail_once):
                if strict:
                    with self.assertRaisesRegex(ValueError, 'file-unavailable'):
                        source.read(reference, strict=True)
                else:
                    self.assertIsNone(source.read(reference))
                self.assertEqual(len(attempts), 1)

    def regions(self, *, after=None, cancelled=lambda: False):
        islands = [(self.frame[10:40, x:x+30], (x, 10, x+30, 40), dict(area=900))
                   for x in (10, 60, 110)]
        def geometry(frame, **kwargs):
            visible = kwargs.get('visible_override')
            return dict(query='controlled-region', observationBounds=visible[1]) if visible is not None else None
        def fit(*args, **kwargs):
            reference = self.host._shared_structure_reference('shared-structure', self.map_id, '1')
            prepared = self.host._load_fast_structure_prepared(reference, cancelled=cancelled)
            self.observed.append((reference, prepared))
            if after is not None:
                after(len(self.observed), reference)
            return dict(success=False, reason='controlled-no-pose')
        with patch('single_map_structure.current_geometry', side_effect=geometry), \
             patch('single_map_structure.observed_corner_chains', return_value=[]), \
             patch('single_map_structure.marker_occluded_corner_proposals', return_value=[]), \
             patch('nightmare_candidate.visible_map_components', return_value=islands), \
             patch.object(self.host, '_fit_observation', side_effect=fit):
            return self.host.fit(self.frame, self.map_id, '1', cancelled,
                                 observation_bounds=(0, 0, 1280, 720))

    def test_regions_share_real_inputs_and_next_frame_rechecks_them(self):
        preparation = self.preparation_path()
        with patch.object(resource_snapshot_io, 'read_stable_file',
                          wraps=resource_snapshot_io.read_stable_file) as read, \
             patch.object(self.engine, 'qt_shared_provider', wraps=self.provider) as provider:
            self.assertFalse(self.regions()['success'])
            self.assertEqual(len(self.observed), 3)
            first_reference, first_prepared = self.observed[0]
            self.assertIsNotNone(first_prepared)
            self.assertTrue(all(reference is first_reference and prepared is first_prepared
                                for reference, prepared in self.observed))
            # Initial acquisition reads before/after decoding, then the result
            # boundary reads the actual payload once. Region reuse cannot
            # remove that final read or acquire the arrays a second time.
            def preparation_reads():
                return sum(Path(call.args[0]).samefile(preparation)
                           for call in read.call_args_list)
            self.assertEqual(preparation_reads(), 3)
            self.assertEqual(provider.call_count, 2)
            self.assertIsNone(self.host._fit_resources)
            self.frame = self.frame.copy()
            self.assertFalse(self.regions()['success'])
            self.assertEqual(preparation_reads(), 6)
            self.assertEqual(provider.call_count, 4)
            self.assertIsNone(self.host._fit_resources)

    def test_cache_eviction_does_not_redecode_used_preparation_at_result_boundary(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        # Keep initial acquisition warm; the fit's own record must remain
        # valid even if the global array cache evicts it during geometry work.
        self.assertIsNotNone(fast.load_prepared(reference, source=source))
        def evict(count, reference):
            with fast._prepared_lock:
                fast._prepared.clear()
        with patch.object(source, 'decode', wraps=source.decode) as decoded, \
             patch.object(resource_snapshot_io, 'read_stable_file',
                          wraps=resource_snapshot_io.read_stable_file) as read:
            self.assertFalse(self.regions(after=evict)['success'])
        self.assertEqual(decoded.call_count, 0)
        self.assertEqual(len(self.observed), 3)
        self.assertTrue(all(prepared is self.observed[0][1] for _, prepared in self.observed))
        preparation = self.preparation_path()
        self.assertEqual(sum(Path(call.args[0]).samefile(preparation)
                             for call in read.call_args_list), 3)
        self.assertIsNone(self.host._fit_resources)

    def test_damage_after_first_offer_is_rejected_at_final_result_boundary(self):
        offered = []
        path = self.preparation_path()
        def callback(result):
            offered.append(result)
            path.write_bytes(b'changed-after-first-offer')
        self.host.usable_fit_callback = callback
        def fit(*args, **kwargs):
            reference = self.host._shared_structure_reference('shared-structure', self.map_id, '1')
            self.assertIsNotNone(self.host._load_fast_structure_prepared(reference))
            residual = canonical_residual_evidence(dict(verified=True),
                basis='game-rendered-shared-structure', coordinate_pack_id='fixture')
            return dict(success=True, matrixSourceToClient=np.eye(3),
                        selectedRouteCurrentFrameResidual=residual)
        with patch.object(self.host, '_fit_observation', side_effect=fit):
            with self.assertRaisesRegex(ValueError, 'structure-fit-preparation-changed'):
                self.host.fit(self.frame, self.map_id, '1', lambda: False)
        self.assertEqual(len(offered), 1)
        self.assertIsNone(self.host._fit_resources)

    def test_cold_decode_rechecks_actual_required_bytes_and_preserves_optional_miss(self):
        source = self.engine.qt_fast_structure_source
        reference = self.provider('shared-structure', self.map_id, '1')
        for path in (reference.path, source.root / source.index_path, self.preparation_path()):
            raw = path.read_bytes()
            with self.subTest(path=path):
                with fast._prepared_lock:
                    fast._prepared.clear()
                original_decode = source.decode
                def changed(payload, ref):
                    result = original_decode(payload, ref)
                    path.write_bytes(b'changed-during-cold-decode')
                    return result
                try:
                    # The actual provider decoder runs before the controlled
                    # file change; no prepared arrays or verdict are mocked.
                    with patch.object(source, 'decode', side_effect=changed):
                        if path.samefile(self.preparation_path()):
                            use = source.acquire_prepared_use(reference)
                            self.assertIsNone(use.prepared)
                        else:
                            with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
                                source.acquire_prepared_use(reference)
                    with fast._prepared_lock:
                        self.assertFalse(any(key[0] == 'imported' and key[1][-1] == id(reference)
                                             for key in fast._prepared))
                finally:
                    path.write_bytes(raw)

    def test_prepared_use_for_another_reference_cannot_enter_the_fit(self):
        source = self.engine.qt_fast_structure_source
        first = self.provider('shared-structure', self.map_id, '1')
        second = self.provider('shared-structure', self.map_id, '2')
        use = source.acquire_prepared_use(first)
        self.assertIsNotNone(use.prepared)
        self.assertIsNot(first, second)
        scope = StructureFitResources(self.engine)
        with patch.object(source, 'acquire_prepared_use', return_value=use):
            with self.assertRaisesRegex(ValueError, 'structure-fit-preparation-source-changed'):
                scope.prepared(second)
        self.assertEqual(scope.preparations, {})

    def test_changed_reference_bytes_reject_the_operation(self):
        def damage(count, reference):
            if count == 1:
                reference.path.write_bytes(b'changed-reference')
        with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
            self.regions(after=damage)
        self.assertIsNone(self.host._fit_resources)

    def test_changed_preparation_cannot_escape_from_region_cache(self):
        path = self.preparation_path()
        def damage(count, reference):
            if count == 1:
                path.write_bytes(b'changed-preparation')
        with self.assertRaisesRegex(ValueError, 'structure-fit-preparation-changed'):
            self.regions(after=damage)
        self.assertIsNone(self.host._fit_resources)

    def test_preparation_damage_is_checked_before_first_offer(self):
        offers = []
        self.host.usable_fit_callback = offers.append
        path = self.preparation_path()
        def fit(*args, **kwargs):
            reference = self.host._shared_structure_reference('shared-structure', self.map_id, '1')
            self.assertIsNotNone(self.host._load_fast_structure_prepared(reference))
            path.write_bytes(b'changed-before-offer')
            residual = canonical_residual_evidence(dict(verified=True),
                basis='game-rendered-shared-structure', coordinate_pack_id='fixture')
            return dict(success=True, matrixSourceToClient=np.eye(3),
                        selectedRouteCurrentFrameResidual=residual)
        with patch.object(self.host, '_fit_observation', side_effect=fit):
            with self.assertRaisesRegex(ValueError, 'structure-fit-preparation-changed'):
                self.host.fit(self.frame, self.map_id, '1', lambda: False)
        self.assertEqual(offers, [])
        self.assertIsNone(self.host._fit_resources)

    def test_reference_damage_is_checked_before_offer_when_fast_lane_is_unused(self):
        offers = []
        self.host.usable_fit_callback = offers.append
        def fit(*args, **kwargs):
            reference = self.host._shared_structure_reference('shared-structure', self.map_id, '1')
            reference.path.write_bytes(b'changed-without-fast-preparation')
            residual = canonical_residual_evidence(dict(verified=True),
                basis='game-rendered-shared-structure', coordinate_pack_id='fixture')
            return dict(success=True, matrixSourceToClient=np.eye(3),
                        selectedRouteCurrentFrameResidual=residual)
        with patch.object(self.host, '_fit_observation', side_effect=fit):
            with self.assertRaisesRegex(ValueError, 'file-integrity-mismatch'):
                self.host.fit(self.frame, self.map_id, '1', lambda: False)
        self.assertEqual(offers, [])
        self.assertIsNone(self.host._fit_resources)

    def test_cancelled_operation_does_not_leak_its_inputs_into_the_next_frame(self):
        stopped = [False]
        path = self.preparation_path()
        def stop(count, reference):
            path.write_bytes(b'changed-after-cancel')
            stopped[0] = True
        result = self.regions(after=stop, cancelled=lambda: stopped[0])
        self.assertEqual(result['reason'], 'structure-work-obsolete')
        self.assertIsNotNone(self.observed[0][1])
        self.assertIsNone(self.host._fit_resources)
        self.observed.clear()
        self.assertFalse(self.regions()['success'])
        self.assertTrue(all(prepared is None for _, prepared in self.observed))


if __name__ == '__main__':
    unittest.main()
