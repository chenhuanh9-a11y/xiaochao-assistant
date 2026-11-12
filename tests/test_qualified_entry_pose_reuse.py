"""Qualified entry proposals through the actual shared prepared fit.

Native package fixtures prepare the immutable resource/locator scope. The
numeric canvas and rendered pixels are synthetic replacements, so these tests
establish neither game accuracy nor source equivalence. No geometry kernel or
fallback is replaced: the original global fit supplies each test's proposal.
"""
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

import fast_structure_alignment as fast
from structure_pose_solver import PreparedStructurePoseSolver
from entry_evidence_work import FrameSnapshot, ObservationWorkScheduler
from map_identity_engine import MapIdentityEngine
from map_observation_work import LocalStructureObservationWork
from single_map_structure import contour_graph
from test_structure_identity_observation import pixel_fixture
import test_structure_pose_inputs as fixtures


class QualifiedEntryPoseReuseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.StructurePoseInputTests.setUpClass()
        cls.addClassCleanup(fixtures.StructurePoseInputTests.doClassCleanups)
        fixture = fixtures.StructurePoseInputTests('runTest')
        cls.addClassCleanup(fixture.doCleanups)
        mask, cls.frame = pixel_fixture()
        mask = np.frombuffer(mask.tobytes(), np.uint8).reshape(mask.shape)
        cls.cases = {}
        for season, mode in (('s0', 'difficult'), ('s0', 'nightmare'),
                             ('s1', 'difficult'), ('s1', 'nightmare')):
            engine, mid, _ = fixture.engine(season, mode,
                map_id='difficult-map-01' if (season, mode) == ('s0', 'difficult') else None)
            floor = '-1' if season == 's1' else '1'
            inputs = fixture.prepare(engine, mid, floor)
            reference = replace(inputs.reference('shared-structure', mid, floor),
                clean_mask=mask, validation_mask=mask, known_boundary=None,
                unknown_mask=None, native_wall_segments=None)
            inputs = replace(inputs, references=MappingProxyType({(mid, floor): reference}),
                preparations=MappingProxyType({(mid, floor): fast.prepare(mask)}))
            host = PreparedStructurePoseSolver(inputs)
            fit = host.fit(cls.frame, mid, floor, lambda: False)
            if not fit.get('success'):
                raise AssertionError((season, mode, fit))
            source = next(row[0] for row in host.engine.side_entry_identity.entries
                          if row[0]['mapId'] == mid and row[0]['floor'] == floor)
            candidate = dict(mapId=mid, floor=floor, profileId=reference.metadata['binding']['profile'],
                sourceProduct=source['sourceProduct'], region=source['region'],
                locatorKind='side', entryKind='local-structure', competitionComplete=True,
                poseVerified=True, identityVerified=True, identityOnly=False, poseAmbiguous=False,
                verification=dict(success=True, matrixSourceToClient=fit['matrixSourceToClient'].copy(),
                                  residual=dict(verified=True)))
            cls.cases[season, mode] = inputs, mid, candidate, fit
        cls.work = cls.entry_work(cls.cases['s0', 'difficult'][0], cls.frame)
        qualified = [row for row in cls.work.finish()
                     if (row.get('difficultEntry') or {}).get('poseVerified') is True]
        if len(qualified) != 1:
            raise AssertionError(('synthetic-full-work-did-not-resolve-pose', cls.work.side_diagnostics))

    @classmethod
    def entry_work(cls, inputs, pixels, *, drain=True, controlled_shift=None):
        """Run real source jobs/recovery/reduction on the synthetic canvas.

        The native package supplies immutable resources, source purpose and
        bridges. Like its raster replacement above, the local numeric contour
        and 60px unit are replaced to describe this synthetic canvas. Default
        observation, local search, entry pixel validator and reduction are real.
        Only the explicit controlled_shift counterexample replaces observation
        points and the entry final validator; shared fit validation stays real.
        """
        host = PreparedStructurePoseSolver(inputs)
        mid, floor = inputs.map_id, inputs.floor
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        engine.pack = replace(inputs.pack, maps=(inputs.pack.by_id[mid],),
            by_id=MappingProxyType({mid: inputs.pack.by_id[mid]}))
        engine.identity_floors, engine.identity_profile_id = (floor,), 'shared-structure'
        engine.qt_rank_ready, engine.qt_season = True, 's0'
        engine.native_similarity_index = engine.native_similarity_priority_provider = None
        engine.side_entry_error = engine.entry_advisory = None
        engine.entry_shared_reference_provider = host.engine.qt_shared_provider
        engine.entry_identity, engine.side_entry_identity = host.engine.entry_identity, host.engine.side_entry_identity
        for locator in (engine.entry_identity, engine.side_entry_identity):
            locator.shared_reference_provider = host.engine.qt_shared_provider
        side = engine.side_entry_identity
        source = next(row[0] for row in side.entries if row[0]['mapId'] == mid and row[0]['floor'] == floor)
        nodes = contour_graph(inputs.references[mid, floor].clean_mask)
        points = np.asarray([node['point'] for node in nodes])
        rays = np.asarray([node['rays'] for node in nodes])
        pairs = np.asarray([(i, node['next']) for i, node in enumerate(nodes)])
        side.entries = [(dict(source, unit=60.), points, rays, pairs)]
        side.side_search, side.side_doors = {}, {}
        if controlled_shift is not None:
            observe = side._observe
            def shifted(frame):
                context, measured, links = observe(frame)
                return context, [dict(node, point=node['point'] + controlled_shift) for node in measured], links
            side._observe = shifted
            side.verify = lambda frame, row, **options: dict(success=True,
                reason='controlled-entry-validator-only', residual=dict(verified=True),
                matrixSourceToClient=row['matrixSourceToClient'])
        work = LocalStructureObservationWork(engine, pixels, floor, deadline=float('inf'))
        if drain:
            ObservationWorkScheduler(clock=time.perf_counter).drive(work)
            work.finish()
        return work

    def fit(self, *, key=('s0', 'difficult'), candidate=None, frame=None, observation=None):
        inputs, mid, original, _ = self.cases[key]
        host = PreparedStructurePoseSolver(inputs)
        entry = deepcopy(original if candidate is None else candidate)
        with patch.object(fast, 'propose', wraps=fast.propose) as global_search:
            fit = host.fit(self.frame if frame is None else frame, mid, original['floor'], lambda: False,
                           entry_candidate=entry, entry_observation=observation)
        return fit, global_search.call_count, host

    def test_qualified_fixed_pose_skips_global_and_preserves_independent_quality(self):
        for key, (inputs, mid, candidate, original) in self.cases.items():
            if key != ('s0', 'difficult'):
                continue  # Other actual fixtures lack a qualified entry result/bridge.
            with self.subTest(mode=key):
                original_candidate_matrix = candidate['verification']['matrixSourceToClient'].copy()
                current = self.work.finish()[0]['difficultEntry']
                before = np.asarray(current['verification']['matrixSourceToClient']).copy()
                fit, searches, host = self.fit(key=key, frame=self.work.frame, observation=self.work)
                self.assertTrue(fit['success'], fit)
                self.assertEqual(searches, 0, 'already qualified pose ran global search again')
                self.assertEqual(fit['strategy'], 'qualified-entry-fixed-current-frame')
                np.testing.assert_array_equal(fit['matrixSourceToClient'], before)
                np.testing.assert_array_equal(candidate['verification']['matrixSourceToClient'], original_candidate_matrix)
                self.assertEqual(fit['coordinateSpace'], original['coordinateSpace'])
                self.assertEqual(fit['routeProfileId'], original['routeProfileId'])
                # Recompute at the result H, independently of candidate claims.
                expected = host._canonical_geometry_current_frame_residual(
                    self.work.frame, mid, candidate['floor'], 'shared-structure', before,
                    inputs.references[mid, candidate['floor']].clean_mask)[0]
                self.assertEqual(fit['selectedRouteCurrentFrameResidual'], expected)
                self.assertTrue(fit['difficultEntryValidation']['entryWalls']['verified'])

    def test_four_modes_without_qualified_entry_keep_original_global_path(self):
        for key, (_, _, original, _) in self.cases.items():
            with self.subTest(mode=key):
                fit, searches, _ = self.fit(key=key, candidate=dict(deepcopy(original),
                    poseVerified=False, proposalOnly=True, authorizesPose=False))
                self.assertTrue(fit['success'], fit)
                self.assertEqual(searches, 1)
                self.assertEqual(fit['strategy'], 'shared-fast-alignment')

    def test_s1_actual_proposal_source_cannot_borrow_row_pose_authority(self):
        for key in (('s1', 'difficult'), ('s1', 'nightmare')):
            with self.subTest(mode=key):
                # Their local declaration's feature role is proposal-only,
                # even if a caller supplies an incorrectly qualified row.
                fit, searches, _ = self.fit(key=key)
                self.assertTrue(fit['success'], fit)
                self.assertEqual(searches, 1)
                self.assertEqual(fit['strategy'], 'shared-fast-alignment')

    def test_actual_source_purpose_and_reference_bridge_cannot_be_overridden_by_row(self):
        inputs, mid, candidate, _ = self.cases['s0', 'difficult']
        for changes in (dict(proposalOnly=True), dict(authorizesPose=False),
                        dict(sourceVersion='old-source'), dict(structureVersionKey='old-structure')):
            with self.subTest(source=changes):
                host = PreparedStructurePoseSolver(inputs)
                # Only replace the query's declaration tuple. Kernels, prepared
                # arrays, pixels and the fallback are the actual shared path.
                locator = host.engine.side_entry_identity
                locator.entries = [(dict(row, **changes), *parts) for row, *parts in locator.entries]
                with patch.object(fast, 'propose', wraps=fast.propose) as search:
                    fit = host.fit(self.work.frame, mid, candidate['floor'], lambda: False,
                                   entry_candidate=deepcopy(candidate), entry_observation=self.work)
                self.assertTrue(fit['success'], fit)
                self.assertEqual(search.call_count, 1)
                self.assertEqual(fit['strategy'], 'shared-fast-alignment')
        # The S0 nightmare native fixture has no bridge for its actual typed
        # profile. Its row claims cannot manufacture that missing adapter.
        fit, searches, _ = self.fit(key=('s0', 'nightmare'))
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        self.assertEqual(fit['strategy'], 'shared-fast-alignment')

    def test_cancelled_qualified_proposal_does_not_start_global_search(self):
        inputs, mid, candidate, _ = self.cases['s0', 'difficult']
        host = PreparedStructurePoseSolver(inputs)
        with patch.object(fast, 'propose', wraps=fast.propose) as search:
            fit = host.fit(self.frame, mid, candidate['floor'], lambda: True,
                           entry_candidate=deepcopy(candidate))
        self.assertEqual(fit['reason'], 'structure-work-obsolete')
        self.assertEqual(search.call_count, 0)

    def test_unqualified_ambiguous_or_wrong_scope_keeps_original_global_fit(self):
        _, _, original, expected = self.cases['s0', 'difficult']
        cases = [dict(poseVerified=False), dict(identityOnly=True), dict(poseAmbiguous=True),
                 dict(competitionComplete=False), dict(proposalOnly=True), dict(authorizesPose=False),
                 dict(entryCrossCheck=dict(contradicted=True)), dict(floor='2'), dict(mapId='other'),
                 dict(sourceProduct='other.npz'), dict(profileId='unbound-profile'),
                 dict(entryPoseCandidates=[deepcopy(original), deepcopy(original)])]
        for changes in cases:
            with self.subTest(changes=changes):
                fit, searches, _ = self.fit(candidate=dict(deepcopy(original), **changes))
                self.assertTrue(fit['success'], fit)
                self.assertEqual(searches, 1)
                self.assertEqual(fit['strategy'], 'shared-fast-alignment')
                np.testing.assert_array_equal(fit['matrixSourceToClient'], expected['matrixSourceToClient'])
                self.assertEqual(fit['selectedRouteCurrentFrameResidual'], expected['selectedRouteCurrentFrameResidual'])

    def test_wrong_matrix_cannot_borrow_candidate_verified_residual(self):
        _, _, candidate, expected = self.cases['s0', 'difficult']
        candidate = deepcopy(candidate)
        candidate['verification']['matrixSourceToClient'][0, 2] += 75
        candidate['verification']['residual'] = deepcopy(expected['selectedRouteCurrentFrameResidual'])
        fit, searches, _ = self.fit(candidate=candidate)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        self.assertEqual(fit['strategy'], 'shared-fast-alignment')
        np.testing.assert_array_equal(fit['matrixSourceToClient'], expected['matrixSourceToClient'])

    def test_same_pixels_without_actual_work_cannot_borrow_row_uniqueness(self):
        fit, searches, _ = self.fit()
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        self.assertEqual(fit['strategy'], 'shared-fast-alignment')

    def test_old_work_different_snapshot_and_snapshot_alone_keep_global_search(self):
        for frame, observation in ((self.frame, self.work),
                (self.work.frame.copy(), self.work),
                (FrameSnapshot(self.work.frame).pixels, self.work)):
            with self.subTest(frame_owner=id(frame)):
                fit, searches, _ = self.fit(frame=frame, observation=observation)
                self.assertTrue(fit['success'], fit)
                self.assertEqual(searches, 1)
                self.assertEqual(fit['strategy'], 'shared-fast-alignment')
        snapshot = FrameSnapshot(self.work.frame)
        fit, searches, _ = self.fit(frame=snapshot.pixels, observation=snapshot)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)

    def test_unfinished_or_failed_actual_work_keeps_global_search(self):
        inputs, _, _, _ = self.cases['s0', 'difficult']
        incomplete = self.entry_work(inputs, self.frame, drain=False)
        self.addCleanup(incomplete.close)
        fit, searches, _ = self.fit(frame=incomplete.frame, observation=incomplete)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        failed = self.entry_work(inputs, self.frame, drain=False)
        self.addCleanup(failed.close)
        while (job := failed.next_job()) is not None:
            if job.phase == 'side:observe':
                with patch.object(failed.engine.side_entry_identity, '_observe',
                                  side_effect=RuntimeError('controlled-source-job-failure')):
                    with self.assertRaisesRegex(RuntimeError, 'controlled-source-job-failure'):
                        job.run()
                break
            job.run()
        self.assertTrue(failed._failed)
        fit, searches, _ = self.fit(frame=failed.frame, observation=failed)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)

    def test_current_work_entry_validator_cannot_replace_independent_pixel_verification(self):
        inputs, _, _, _ = self.cases['s0', 'difficult']
        controlled = self.entry_work(inputs, self.frame, controlled_shift=np.asarray((75., 0.)))
        self.assertTrue(controlled._complete)
        self.assertTrue(controlled.side_winner['poseVerified'])
        fit, searches, _ = self.fit(frame=controlled.frame, observation=controlled)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        self.assertEqual(fit['strategy'], 'shared-fast-alignment')

    def test_legacy_qualified_rows_cannot_authorize_repeated_room_poses(self):
        inputs, mid, candidate, _ = self.cases['s0', 'difficult']
        floor = candidate['floor']
        for shape in ('rect', 'notch'):
            mask = np.zeros((650, 1000), np.uint8)
            tile = np.ones((180, 180), np.uint8)
            if shape == 'notch':
                tile[:65, 100:] = 0
                tile[120:, :60] = 0
            mask[100:280, 80:260] = tile
            mask[100:280, 560:740] = tile
            reference = replace(inputs.reference('shared-structure', mid, floor),
                clean_mask=mask, validation_mask=mask, known_boundary=None,
                unknown_mask=None, native_wall_segments=None)
            prepared = replace(inputs,
                references=MappingProxyType({(mid, floor): reference}),
                preparations=MappingProxyType({(mid, floor): fast.prepare(mask)}))
            frame = np.zeros((1000, 1200, 3), np.uint8)
            frame[280:460, 380:560] = tile[:, :, None] * 90
            for dx in (300., -180.):
                with self.subTest(shape=shape, dx=dx):
                    row = deepcopy(candidate)
                    row['verification']['matrixSourceToClient'] = np.asarray(
                        ((1., 0, dx), (0, 1., 180.), (0, 0, 1.)))
                    with patch.object(fast, 'propose', wraps=fast.propose) as search:
                        fit = PreparedStructurePoseSolver(prepared).fit(frame, mid, floor,
                            lambda: False, entry_candidate=row)
                    self.assertFalse(fit['success'], fit)
                    self.assertEqual(fit['reason'], 'initial-structure-ambiguous')
                    self.assertEqual(search.call_count, 1)
            # Running the actual source jobs on this same snapshot cannot turn
            # the two repeated rooms into a unique current pose either.
            ambiguous = self.entry_work(prepared, frame)
            self.assertTrue(ambiguous._complete)
            self.assertFalse(any((row.get('difficultEntry') or {}).get('poseVerified')
                                 for row in ambiguous.finish()))
            with patch.object(fast, 'propose', wraps=fast.propose) as search:
                fit = PreparedStructurePoseSolver(prepared).fit(ambiguous.frame, mid, floor,
                    lambda: False, entry_candidate=deepcopy(candidate), entry_observation=ambiguous)
            self.assertFalse(fit['success'], fit)
            self.assertEqual(fit['reason'], 'initial-structure-ambiguous')
            self.assertEqual(search.call_count, 1)

    def test_new_pixels_revalidate_proposal_and_recover_through_original_global_fit(self):
        moved = cv2.warpAffine(self.frame, np.asarray(((1., 0, 60), (0, 1., 25))),
                              self.frame.shape[1::-1])
        fit, searches, _ = self.fit(frame=moved)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 1)
        self.assertEqual(fit['strategy'], 'shared-fast-alignment')
        self.assertGreater(abs(fit['matrixSourceToClient'][0, 2] -
            self.cases['s0', 'difficult'][2]['verification']['matrixSourceToClient'][0, 2]), 40)

    def test_empty_current_pixels_do_not_accept_stale_pose_proof(self):
        fit, searches, _ = self.fit(frame=np.zeros_like(self.frame))
        self.assertFalse(fit['success'], fit)
        self.assertGreaterEqual(searches, 1)

    def test_candidate_residual_is_unused_even_when_it_claims_failure(self):
        _, _, candidate, _ = self.cases['s0', 'difficult']
        candidate = deepcopy(candidate)
        candidate['verification']['residual'] = dict(verified=False, medianPixels=999.)
        # External H/flags/residual cannot replace the actual work result.
        candidate['verification']['matrixSourceToClient'][0, 2] += 75
        fit, searches, _ = self.fit(candidate=candidate, frame=self.work.frame, observation=self.work)
        self.assertTrue(fit['success'], fit)
        self.assertEqual(searches, 0)
        self.assertTrue(fit['selectedRouteCurrentFrameResidual']['verified'])
        np.testing.assert_array_equal(fit['matrixSourceToClient'],
            self.work.finish()[0]['difficultEntry']['verification']['matrixSourceToClient'])


if __name__ == '__main__':
    unittest.main()
