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
from alignment_observation import canonical_residual_evidence
from client_providers import SharedReferenceProvider
from imported_native_base import decode_native_base
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
from structure_pose_solver import StructurePoseSolver
import test_export_native_base_package as exported_fixture


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
        self.view = PackageView(self.root / 'resources', base)
        self.bundle = decode_native_base(self.view)
        self.provider = SharedReferenceProvider(self.view.root, self.bundle.resources,
            self.bundle.versions, check_current=self.bundle.assert_current)
        self.engine = SimpleNamespace(pack=self.bundle.pack, qt_adapters=self.bundle.adapters,
            qt_shared_provider=self.provider, qt_fast_structure_source=self.bundle.fast_structure_source,
            qt_assert_resource_current=self.bundle.assert_current)
        self.host = StructurePoseSolver(self.engine)
        self.frame = np.zeros((720, 1280, 3), np.uint8)
        self.observed = []

    def preparation_path(self):
        entry = self.engine.qt_fast_structure_source.entries[('zhan10-speed', self.map_id, 'variant-01', '1')]
        return self.view.path(entry[1].path)

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
        with patch.object(fast, 'load_prepared', wraps=fast.load_prepared) as loaded, \
             patch.object(self.engine, 'qt_shared_provider', wraps=self.provider) as provider:
            self.assertFalse(self.regions()['success'])
            self.assertEqual(len(self.observed), 3)
            first_reference, first_prepared = self.observed[0]
            self.assertIsNotNone(first_prepared)
            self.assertTrue(all(reference is first_reference and prepared is first_prepared
                                for reference, prepared in self.observed))
            # One preparation plus a real return-boundary check, regardless
            # of the number of source-independent regions attempted.
            self.assertEqual(loaded.call_count, 2)
            self.assertEqual(provider.call_count, 2)
            self.assertIsNone(self.host._fit_resources)
            self.frame = self.frame.copy()
            self.assertFalse(self.regions()['success'])
            self.assertEqual(loaded.call_count, 4)
            self.assertEqual(provider.call_count, 4)
            self.assertIsNone(self.host._fit_resources)

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
