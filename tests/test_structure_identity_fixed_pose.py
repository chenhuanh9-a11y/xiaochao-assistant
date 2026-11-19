"""Synthetic pixel geometry through the actual fixed residual consumer.

The package fixture prepares the RAM scope. Its reference pixels are explicitly
replaced by a synthetic canvas; this establishes no game/source equivalence.
"""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import time
from types import MappingProxyType
import unittest
from unittest.mock import patch

import numpy as np

from client_structure_recognition import (
    DirectStructureRecognitionInputs, compute_structure_identity,
)
from entry_evidence_work import FrameSnapshot
from fast_structure_alignment import prepare
from imported_resource_packages import FileRef
from structure_identity_index import DirectoryEntry, MapFloorKey, StructureIdentityIndex
from structure_identity_resources import _binding_text, footprint_anchors
from structure_pose_solver import (PreparedStructurePoseSolver, StructurePoseSolver,
    validate_fixed_structure_identity_pose, validate_fixed_structure_identity_poses)
from test_structure_identity_observation import pixel_fixture
import test_structure_pose_inputs as pose_input_fixture


class StructureIdentityFixedPoseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pose_input_fixture.StructurePoseInputTests.setUpClass()
        cls.addClassCleanup(pose_input_fixture.StructurePoseInputTests.doClassCleanups)
        fixture = pose_input_fixture.StructurePoseInputTests('runTest')
        cls.addClassCleanup(fixture.doCleanups)
        engine, mid, _ = fixture.engine()
        cls.original_inputs = fixture.prepare(engine, mid)
        cls.mask, pixels = pixel_fixture()
        cls.snapshot = FrameSnapshot(pixels)
        reference = cls.original_inputs.reference('shared-structure', mid, '1')
        mask = np.frombuffer(cls.mask.tobytes(), np.uint8).reshape(cls.mask.shape)
        cls.reference = replace(reference, clean_mask=mask, validation_mask=mask,
            known_boundary=None, unknown_mask=None, native_wall_segments=None)
        cls.inputs = replace(cls.original_inputs,
            references=MappingProxyType({(mid, '1'): cls.reference}))
        features = footprint_anchors(prepare(cls.mask))
        key = MapFloorKey(cls.inputs.binding.season + ':' + cls.inputs.binding.mode,
                          mid, '1', 'variant-01')
        reference_resource = FileRef(cls.inputs.binding.component_ref,
            cls.reference.path.relative_to(cls.reference.resource_root).as_posix())
        prepared_resource = cls.inputs.preparation_resources[(mid, '1')]
        if prepared_resource is None:
            raise AssertionError('fixture must have an independently read prepared FileRef')
        entry = DirectoryEntry(key, _binding_text(reference_resource, prepared_resource,
            cls.reference.metadata), features,
            frozenset(row.feature_id for row in features), True, True)
        cls.computation = compute_structure_identity(cls.snapshot,
            DirectStructureRecognitionInputs(cls.inputs.binding,
                StructureIdentityIndex((key,), (entry,), directory_complete=True),
                ('synthetic-source-equivalence-unverified',)))
        if cls.computation.recognition.status != 'pose-proposals':
            raise AssertionError(cls.computation.recognition)
        cls.proposal = cls.computation.recognition.pose_proposals[0]

    def validate(self, proposal=None, snapshot=None, **options):
        arguments = dict(frame_key=self.computation.recognition.frame_key,
            shape_snapshot_id=self.computation.shape_snapshot_id,
            resource_binding=self.computation.binding)
        arguments.update(options)
        return validate_fixed_structure_identity_pose(self.inputs,
            self.snapshot if snapshot is None else snapshot,
            self.proposal if proposal is None else proposal, **arguments)

    def test_actual_fixed_pixel_residual_passes_without_fit_io_or_changing_h(self):
        before = self.proposal.matrix_source_to_query
        with ExitStack() as stack:
            for owner, name in ((StructurePoseSolver, 'fit'), (PreparedStructurePoseSolver, 'fit'),
                    (Path, 'read_bytes'), (Path, 'read_text'), (Path, 'stat'), (Path, 'resolve')):
                stack.enter_context(patch.object(owner, name,
                    side_effect=AssertionError('fixed-validation-called-fit-or-resource-io')))
            fit = self.validate()
        self.assertTrue(fit['success'], fit)
        self.assertEqual(fit['matrixSourceToClient'], before)
        self.assertEqual(self.proposal.matrix_source_to_query, before)
        self.assertEqual(fit['coordinateSpace'], 'shared-structure-to-client')
        residual = fit['selectedRouteCurrentFrameResidual']
        self.assertTrue(residual['verified'], residual)
        self.assertGreater(residual['queryPixels'], 1500)
        self.assertGreater(residual['queryEdgeCount'], 200)
        self.assertTrue(residual['poseVerifiedAgainstCanonicalGeometry'])

    def test_shifted_fixed_h_is_rejected_by_actual_residual_without_refitting(self):
        region = self.proposal.pose_region
        shifted = replace(region, query_points=tuple((x+75, y) for x, y in region.query_points))
        proposal = replace(self.proposal, pose_region=shifted)
        before = proposal.matrix_source_to_query
        fit = self.validate(proposal)
        self.assertFalse(fit['success'], fit)
        self.assertEqual(fit['reason'], 'canonical-current-frame-residual-unverified')
        self.assertFalse(fit['residual']['verified'])
        self.assertGreater(fit['residual'].get('medianPixels', 0), 4.5)
        self.assertEqual(fit['matrixSourceToClient'], before)
        self.assertEqual(proposal.matrix_source_to_query, before)

    def test_wrong_scope_frame_shape_revision_and_variant_never_enter_residual(self):
        key = self.proposal.key
        reshaped = FrameSnapshot(self.snapshot.pixels.reshape(1200, 1000, 3))
        cases = ((dict(frame_key='old-frame'), None, None, 'direct-proposal-frame-mismatch'),
            ({}, None, reshaped, 'direct-proposal-frame-mismatch'),
            (dict(resource_binding=replace(self.inputs.binding, resource_revision=99)), None, None,
                'direct-proposal-resource-binding-mismatch'),
            ({}, replace(self.proposal, key=replace(key, map_id='wrong-map')), None,
                'direct-proposal-prepared-scope-mismatch'),
            ({}, replace(self.proposal, key=replace(key, floor='2')), None,
                'direct-proposal-prepared-scope-mismatch'),
            ({}, replace(self.proposal, key=replace(key, mode='s1:nightmare')), None,
                'direct-proposal-prepared-scope-mismatch'),
            ({}, replace(self.proposal, key=replace(key, variant='wrong-variant')), None,
                'direct-proposal-reference-binding-mismatch'),
            ({}, replace(self.proposal, resource_binding='old-source'), None,
                'direct-proposal-source-binding-mismatch'))
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual',
                          side_effect=AssertionError('wrong scope reached residual')):
            for options, proposal, snapshot, reason in cases:
                with self.subTest(reason=reason, options=options):
                    fit = self.validate(proposal, snapshot, **options)
                    self.assertFalse(fit['success'])
                    self.assertEqual(fit['reason'], reason)

    def test_changed_prepared_path_or_none_cannot_rebind_the_actual_read_resource(self):
        original = json.loads(self.proposal.resource_binding)
        for prepared in (dict(original['prepared'], path='other-prepared.npz'), None):
            declared = dict(original, prepared=prepared)
            proposal = replace(self.proposal, resource_binding=json.dumps(declared))
            with self.subTest(prepared=prepared):
                fit = self.validate(proposal)
                self.assertFalse(fit['success'], 'proposal chose its own expected prepared FileRef')
                self.assertEqual(fit['reason'], 'direct-proposal-source-binding-mismatch')

    def test_cancel_and_expired_deadline_reject_before_and_after_real_kernel(self):
        for options, reason in ((dict(cancelled=lambda: True), 'structure-work-obsolete'),
                (dict(deadline=time.perf_counter()-1), 'structure-work-time-budget-exhausted')):
            fit = self.validate(**options)
            self.assertFalse(fit['success'])
            self.assertEqual(fit['reason'], reason)
        reached = []
        original = StructurePoseSolver._canonical_geometry_current_frame_residual
        def residual(host, *args, **kwargs):
            value = original(host, *args, **kwargs)
            reached.append(True)
            return value
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual', residual):
            fit = self.validate(cancelled=lambda: bool(reached))
        self.assertEqual(reached, [True])
        self.assertFalse(fit['success'])
        self.assertEqual(fit['reason'], 'structure-work-obsolete')
        self.assertTrue(fit['residual']['verified'])
        reached.clear()
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual', residual), \
             patch('structure_pose_solver.time.perf_counter', side_effect=lambda: 11. if reached else 0.):
            fit = self.validate(deadline=10.)
        self.assertEqual(reached, [True])
        self.assertFalse(fit['success'])
        self.assertEqual(fit['reason'], 'structure-work-time-budget-exhausted')
        self.assertTrue(fit['residual']['verified'])

    def test_multiple_families_remain_ordered_and_qualification_gaps_survive(self):
        recognition = self.computation.recognition
        check = recognition.pose_checks[0]
        region = check.proposal.pose_region
        shifted = replace(check.proposal, pose_region=replace(region,
            query_points=tuple((x+75, y) for x, y in region.query_points)))
        # Only the family list is synthetic here; every family still executes
        # the real current-frame residual, in the supplied order.
        recognition = replace(recognition, pose_checks=(replace(check, proposal=shifted), check))
        hosts = []
        original = StructurePoseSolver._canonical_geometry_current_frame_residual
        def residual(host, *args, **options):
            hosts.append(host)
            return original(host, *args, **options)
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual', residual):
            validation = validate_fixed_structure_identity_poses(self.inputs, self.snapshot, recognition,
                shape_snapshot_id=self.computation.shape_snapshot_id,
                resource_binding=self.computation.binding)
        self.assertIs(validation.recognition, recognition)
        fits = validation.pose_checks
        self.assertEqual([fit['success'] for fit in fits], [False, True])
        self.assertEqual(len(fits), 2)
        self.assertIsNot(hosts[0], hosts[1])
        self.assertTrue(all(host._fit_resources is None for host in hosts))
        self.assertTrue(all('synthetic-source-equivalence-unverified' in fit['qualificationGaps']
                            for fit in fits))

    def test_batch_retains_b_rejected_and_unresolved_families_without_running_their_residual(self):
        recognition = self.computation.recognition
        check = recognition.pose_checks[0]
        rejected = replace(check, held_out=replace(check.held_out,
            status='rejected', reason='fixed-a-seed-b-correspondence-rejected'))
        unresolved = replace(check, held_out=replace(check.held_out,
            status='unresolved', reason='b-work-budget-exhausted'))
        # This test replaces only B result statuses to exercise aggregation.
        # The one verified family still executes the real canonical kernel.
        recognition = replace(recognition, status='unresolved', issues=('b-work-budget-exhausted',),
            pose_checks=(rejected, check, unresolved))
        original = StructurePoseSolver._canonical_geometry_current_frame_residual
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual',
                          autospec=True, side_effect=original) as residual:
            validation = validate_fixed_structure_identity_poses(self.inputs, self.snapshot, recognition,
                shape_snapshot_id=self.computation.shape_snapshot_id,
                resource_binding=self.computation.binding)
        self.assertIs(validation.recognition, recognition)
        self.assertEqual(validation.recognition.status, 'unresolved')
        self.assertEqual(validation.recognition.issues, ('b-work-budget-exhausted',))
        self.assertEqual([row['heldOutStatus'] for row in validation.pose_checks],
                         ['rejected', 'verified', 'unresolved'])
        self.assertEqual([row['success'] for row in validation.pose_checks], [False, True, False])
        self.assertEqual(residual.call_count, 1)
        self.assertEqual(validation.pose_checks[0]['heldOutReason'], rejected.held_out.reason)
        self.assertEqual(validation.pose_checks[2]['heldOutReason'], unresolved.held_out.reason)
        self.assertTrue(all(row['qualificationGaps'] == list(recognition.qualification_gaps)
                            for row in validation.pose_checks))

    def test_batch_retains_complete_recognition_when_no_family_can_be_checked(self):
        recognition = replace(self.computation.recognition, status='unresolved',
            issues=('index-work-budget-exhausted',), pose_checks=())
        with patch.object(StructurePoseSolver, '_canonical_geometry_current_frame_residual',
                          side_effect=AssertionError('empty family list reached canonical kernel')):
            validation = validate_fixed_structure_identity_poses(self.inputs, self.snapshot, recognition,
                shape_snapshot_id=self.computation.shape_snapshot_id,
                resource_binding=self.computation.binding)
        self.assertIs(validation.recognition, recognition)
        self.assertEqual(validation.pose_checks, ())
        self.assertEqual(validation.recognition.status, 'unresolved')
        self.assertEqual(validation.recognition.issues, ('index-work-budget-exhausted',))


if __name__ == '__main__':
    unittest.main()
