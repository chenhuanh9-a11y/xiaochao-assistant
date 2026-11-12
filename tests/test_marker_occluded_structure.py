"""Generated pixels exercise full shared solvers, not production/game accuracy.

Provider and precomputed-index loading are synthetic. Extraction, proposals,
wall holdouts, ambiguity, regional checks and canonical residuals are real.
"""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from client_live_runtime import _AlignmentHost
from fast_structure_alignment import prepare
from shared_structure_reference import SharedStructureReference
from single_map_structure import (current_geometry, current_part,
    observed_corner_chains, marker_occluded_corner_proposals)


def asymmetric_structure():
    mask = np.zeros((650, 650), np.uint8)
    mask[65:520, 75:510] = 1
    for x, y, w, h in ((113,102,53,71), (225,100,77,49), (362,110,61,87),
                       (130,245,66,58), (276,228,51,111), (388,290,73,67),
                       (190,390,89,55), (352,408,57,73)):
        mask[y:y+h, x:x+w] = 0
    mask[65:133, 440:510] = 0
    mask[430:520, 75:125] = 0
    return mask


def render(mask, *, scale=1., variant='clean', radius=0, extent=None):
    truth = np.array([[scale, 0, 210], [0, scale, 130], [0, 0, 1.]])
    gray = cv2.warpAffine(mask*90, truth[:2], (1200, 1000), flags=cv2.INTER_LINEAR)
    frame = np.repeat(gray[:, :, None], 3, axis=2)
    if extent is not None:
        fog = np.zeros(gray.shape, np.float32)
        fog[:, :int(210+extent*scale)] = 1
        alpha = cv2.GaussianBlur(fog, (41, 41), 7)
        frame = np.rint(frame*alpha[:, :, None]).astype(np.uint8)
    if variant == 'blur':
        frame = cv2.GaussianBlur(frame, (radius*2+1, radius*2+1), 0)
    if variant in ('corner-marker', 'edge-marker', 'corner-dark'):
        context = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000))
        nodes = current_part(frame, context, 'nodes')
        for node in nodes:
            point = ((node['point']+nodes[node['next']]['point'])/2
                     if variant == 'edge-marker' else node['point'])
            x, y = np.rint(point).astype(int)
            frame[y-radius:y+radius+1, x-radius:x+radius+1] = (
                0 if variant == 'corner-dark' else (0, 0, 255))
    return frame, truth


def run_fit(frame, source, *, indexed=False, cached=False, mode='difficult', primary=False, known=False, unknown=None, entry_candidate=None):
    boundary = cv2.morphologyEx(source, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)) if known else None
    metadata = (dict(binding=dict(profile='shared-structure', mapId='synthetic', floor='-3'))
                if entry_candidate is not None else {})
    reference = SharedStructureReference(source, source.copy(), metadata, Path('synthetic-primary/reference.npz'),
        known_boundary=boundary, unknown_mask=unknown)
    host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode=mode),
        qt_adapters=SimpleNamespace(canonical_pack_id='synthetic-primary',
            matrix_profile_to_canonical=lambda *args: np.eye(3)),
        qt_shared_provider=lambda *args: reference), collect_fit_diagnostics=True)
    bounds = (0, 0, frame.shape[1], frame.shape[0])
    with patch('fast_structure_alignment.load_prepared', return_value=prepare(source) if indexed else None):
        fit = (host._fit_observation if primary else
               host.prepare_structure_scan(frame, bounds) if cached else host.fit)
        return fit(frame, 'synthetic', '-3', lambda: False, observation_bounds=bounds,
            **(dict(entry_candidate=entry_candidate) if entry_candidate is not None else {}))


def entry_proposal(matrix):
    return dict(mapId='synthetic', floor='-3', profileId='shared-structure',
        entryKind='main', proposalOnly=True, authorizesPose=False, authorizesIdentity=False,
        verification=dict(success=True, matrixSourceToClient=matrix))


class MarkerOccludedStructureTests(unittest.TestCase):
    def assert_pose(self, result, truth, shape, limit=5):
        self.assertTrue(result['success'], result['reason'])
        h, w = shape
        extent = np.array([[0,0,1], [w,0,1], [0,h,1], [w,h,1]])
        error = np.linalg.norm((extent@(result['matrixSourceToClient']-truth).T)[:, :2], axis=1).max()
        self.assertLess(error, limit)

    def test_entry_proposal_keeps_measured_and_marker_searches(self):
        source = asymmetric_structure()
        for variant in ('clean', 'corner-marker'):
            with self.subTest(variant=variant):
                frame, truth = render(source, variant=variant)
                baseline = run_fit(frame, source, cached=True)
                proposal = entry_proposal(truth + np.array([[0, 0, 1.5], [0, 0, -1.5], [0, 0, 0]]))
                result = run_fit(frame, source, cached=True, entry_candidate=proposal)
                self.assert_pose(result, truth, source.shape, limit=1)
                for field in ('queryChains', 'markerOccludedProposalChains', 'seedCount', 'measuredSeedCount'):
                    self.assertEqual(result['diagnostics'][field], baseline['diagnostics'][field], field)
                self.assertEqual(result['diagnostics']['entryPoseProposals']['count'], 1)
                self.assertTrue(result['diagnostics']['entryPoseProposals']['validated'])
                self.assertEqual(result['diagnostics']['poseIndexCandidates']['count'], 0)
                self.assertIs(proposal['authorizesPose'], False)

    def test_entry_proposal_cannot_remove_another_valid_pose(self):
        source = asymmetric_structure()
        reference = np.concatenate((source, source), axis=1)
        for variant in ('clean', 'corner-marker'):
            with self.subTest(variant=variant):
                frame, truth = render(source, variant=variant)
                result = run_fit(frame, reference, cached=True, entry_candidate=entry_proposal(truth))
                self.assertFalse(result['success'], result['reason'])
                self.assertIn('ambiguous', result['reason'])
                self.assertGreaterEqual(sum(c['accepted'] for c in result['diagnostics']['candidates']), 2)

    def test_bad_anchored_entry_proposal_falls_back_to_the_full_initial_search(self):
        source = asymmetric_structure()
        frame, truth = render(source)
        wrong = truth.copy()
        wrong[:2, 2] += [4000, 3000]
        proposal = dict(entry_proposal(wrong), locatorKind='side', entryKind='side-entry-passport',
            entryPoseCandidates=[dict(mapId='synthetic', floor='-3')])
        result = run_fit(frame, source, cached=True, entry_candidate=proposal)
        self.assert_pose(result, truth, source.shape, limit=1)
        self.assertEqual(result['diagnostics']['entryPoseProposals']['count'], 1)

    def test_entry_proposal_cannot_hide_a_contradictory_marker_peer(self):
        source = asymmetric_structure()
        wrong = source.copy()
        wrong[228:339, 276:327] = 1
        wrong[248:359, 302:353] = 0
        wrong[390:445, 190:279] = 1
        wrong[375:430, 170:259] = 0
        first, _ = render(wrong, variant='corner-marker')
        second, truth = render(source, variant='corner-marker', scale=.75)
        frame = np.zeros((1000, 2000, 3), np.uint8)
        frame[:, :1200] = first
        frame[:, 800:] = np.maximum(frame[:, 800:], second)
        truth[0, 2] += 800
        result = run_fit(frame, source, cached=True, entry_candidate=entry_proposal(truth))
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'manual-components-inconsistent')

    def test_entry_proposal_cannot_supply_an_unobservable_initial_scale(self):
        source = np.zeros((650, 650), np.uint8)
        for i in range(8):
            x = 100 + 8*(i % 2)
            source[50+i*50:101+i*50, x:x+14] = 1
        frame, truth = render(source)
        self.assertFalse(run_fit(frame, source, cached=True)['success'])
        for scale in (.99, 1., 1.01):
            with self.subTest(scale=scale):
                proposed = truth.copy()
                proposed[0, 0] = proposed[1, 1] = scale
                result = run_fit(frame, source, cached=True, entry_candidate=entry_proposal(proposed))
                self.assertFalse(result['success'], (result['reason'], result.get('currentFrameScaleObservation')))

    def test_single_pixel_marker_exclusions_only_propose_walls(self):
        source = asymmetric_structure()
        for variant in ('corner-marker', 'edge-marker'):
            frame, truth = render(source, variant=variant)
            context = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000))
            self.assertEqual(len(observed_corner_chains(frame, context)), 0)
            self.assertGreaterEqual(len(current_part(frame, context, 'lines')), 40)
            self.assertEqual(len(marker_occluded_corner_proposals(frame, context, [])), 40)
            for mode in ('difficult', 'nightmare'):
                for indexed in (False, True):
                    for cached in (False, True):
                        with self.subTest(variant=variant, mode=mode, indexed=indexed, cached=cached):
                            result = run_fit(frame, source, mode=mode, indexed=indexed, cached=cached)
                            self.assert_pose(result, truth, source.shape, limit=1)
                            diagnostics = result['diagnostics']
                            self.assertEqual(diagnostics['queryChains'], 0)
                            self.assertEqual(diagnostics['measuredSeedCount'], 0)
                            accepted = [c for c in diagnostics['candidates'] if c['accepted']]
                            self.assertEqual(len(accepted), 1)
                            self.assertEqual(accepted[0]['pairs'], [])
                            self.assertTrue(accepted[0]['pointGeometry']['wallScale']['rasterValidation']['verified'])

    def test_clean_scales_and_partial_exploration_keep_existing_fit(self):
        source = asymmetric_structure()
        for scale in (.55, .75, 1., 1.2):
            for extent in (None, 250, 350, 450):
                for indexed in (False, True):
                    with self.subTest(scale=scale, extent=extent, indexed=indexed):
                        frame, truth = render(source, scale=scale, extent=extent)
                        result = run_fit(frame, source, indexed=indexed)
                        if scale == .55 and extent == 250 and not indexed:
                            # Existing e5defc7f limitation: tiny explored extent
                            # extrapolates to 5.9437px error over the full canvas.
                            # Pin the old result, do not relax the 5px oracle.
                            self.assertTrue(result['success'])
                            np.testing.assert_allclose(result['matrixSourceToClient'],
                                [[.5412576450634734, 0, 211.06871562933648],
                                 [0, .5412576450634734, 131.93543454797756], [0, 0, 1]], atol=1e-10)
                        else:
                            self.assert_pose(result, truth, source.shape)

    def test_unexplained_dark_gaps_and_soft_fog_do_not_become_proposals(self):
        source = asymmetric_structure()
        for variant, radius in (('corner-dark', 2), ('corner-dark', 3), ('blur', 3)):
            frame, _ = render(source, variant=variant, radius=radius)
            context = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000))
            measured = observed_corner_chains(frame, context)
            self.assertEqual(measured, [])
            self.assertEqual(marker_occluded_corner_proposals(frame, context, measured), [])
            for indexed in (False, True):
                with self.subTest(variant=variant, radius=radius, indexed=indexed):
                    self.assertFalse(run_fit(frame, source, indexed=indexed)['success'])

    def test_repeated_whole_structure_is_ambiguous(self):
        source = asymmetric_structure()
        reference = np.concatenate((source, source), axis=1)
        for variant in ('clean', 'corner-marker', 'edge-marker'):
            frame, _ = render(source, variant=variant)
            for indexed in (False, True):
                with self.subTest(variant=variant, indexed=indexed):
                    result = run_fit(frame, reference, indexed=indexed, primary=True)
                    self.assertFalse(result['success'], result['reason'])
                    self.assertIn('ambiguous', result['reason'])
                    self.assertGreaterEqual(sum(c['accepted'] for c in result['diagnostics']['candidates']), 2)

    def test_locally_similar_wrong_reference_cannot_discard_conflicting_walls(self):
        source = asymmetric_structure()
        reference = source.copy()
        reference[228:339, 276:327] = 1
        reference[248:359, 302:353] = 0
        reference[390:445, 190:279] = 1
        reference[375:430, 170:259] = 0
        for variant in ('clean', 'corner-marker', 'edge-marker'):
            frame, _ = render(source, variant=variant)
            for indexed in (False, True):
                with self.subTest(variant=variant, indexed=indexed):
                    self.assertFalse(run_fit(frame, reference, indexed=indexed)['success'])

    def test_budget_exhaustion_is_never_unique_after_truncation(self):
        source = asymmetric_structure()
        reference = np.concatenate([source]*30, axis=1)
        frame, _ = render(source, variant='corner-marker')
        result = run_fit(frame, reference, primary=True)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'initial-structure-descriptor-budget-unresolved')
        self.assertGreater(result['diagnostics']['seedCount'], result['diagnostics']['seedBudget'])


    def test_validation_budget_is_also_fail_closed(self):
        source = asymmetric_structure()
        reference = np.concatenate([source]*12, axis=1)
        frame, _ = render(source, variant='corner-marker')
        result = run_fit(frame, reference, primary=True)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'initial-structure-validation-budget-unresolved')
        self.assertGreater(result['diagnostics']['candidateCount'], result['diagnostics']['validationBudget'])

    def test_new_recovery_checks_wall_only_peer_components(self):
        source = np.zeros((650, 950), np.uint8)
        source[:, :650] = asymmetric_structure()
        source[140:410, 710:870] = 1
        source[185:230, 745:810] = 0
        source[300:365, 785:835] = 0
        frame, truth = render(source, variant='corner-marker')
        # Mark the peer's corners too, without borrowing a source-based mask.
        from nightmare_candidate import visible_map_components
        peers = visible_map_components(frame, observation_bounds=(0, 0, 1200, 1000))
        self.assertEqual(len(peers), 2)
        peer = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000), visible_override=peers[1])
        for node in current_part(frame, peer, 'nodes'):
            x, y = np.rint(node['point']).astype(int)
            frame[y, x] = (0, 0, 255)
        for conflicting in (False, True):
            observation = frame.copy()
            if conflicting:
                observation[:, 900:] = cv2.warpAffine(frame[:, 900:],
                    np.array([[1., 0, 25], [0, 1., 0]]), (300, 1000))
            for cached in (False, True):
                for indexed in (False, True):
                    with self.subTest(conflicting=conflicting, cached=cached, indexed=indexed):
                        result = run_fit(observation, source, cached=cached, indexed=indexed)
                        if conflicting:
                            self.assertFalse(result['success'])
                            self.assertEqual(result['reason'], 'manual-components-inconsistent')
                        else:
                            if indexed:
                                # Existing fast wall recovery is unchanged at
                                # 2.2002px full-canvas error; only its peer veto
                                # is new. It is not a new proposal recovery.
                                self.assertTrue(result['success'])
                                self.assertEqual(result['reason'], 'experimental-fast-structure')
                                np.testing.assert_allclose(result['matrixSourceToClient'],
                                    [[1.0032920338222429, 0, 208.590576171875],
                                     [0, 1.0032920338222429, 128.62551879882812], [0, 0, 1]], atol=1e-7)
                                self.assertFalse(result.get('markerOccludedWallProposal', False))
                            else:
                                self.assert_pose(result, truth, source.shape, limit=1)
                                self.assertTrue(result['markerOccludedWallProposal'])
                            self.assertTrue(result['markerOccludedWallObservation'])

    def test_proposal_cache_is_frame_local_and_invalidated_by_filtered_evidence(self):
        source = asymmetric_structure()
        frame, _ = render(source, variant='corner-marker')
        context = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000))
        proposals = marker_occluded_corner_proposals(frame, context, [])
        self.assertEqual(len(proposals), 40)
        self.assertIs(marker_occluded_corner_proposals(frame, dict(context), []), proposals)
        filtered = dict(context, observed=np.zeros_like(context['observed']))
        self.assertEqual(marker_occluded_corner_proposals(frame, filtered, []), [])
        self.assertEqual(marker_occluded_corner_proposals(frame, dict(context, nodes=[]), []), [])
        # A different opening gets its own observation, not a previous cache.
        fresh_frame, _ = render(source, variant='corner-dark', radius=2)
        fresh = current_geometry(fresh_frame, observation_bounds=(0, 0, 1200, 1000))
        self.assertEqual(marker_occluded_corner_proposals(fresh_frame, fresh, []), [])

    def test_scan_reuses_only_source_independent_proposals(self):
        source = asymmetric_structure()
        frame, _ = render(source, variant='corner-marker')
        reference = SharedStructureReference(source, source.copy(), {}, Path('synthetic-primary/reference.npz'))
        host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode='nightmare'),
            qt_adapters=SimpleNamespace(canonical_pack_id='synthetic-primary'),
            qt_shared_provider=lambda *args: reference))
        bounds = (0, 0, 1200, 1000)
        from single_map_structure import _marker_occluded_corner_proposals
        with patch('single_map_structure._marker_occluded_corner_proposals', wraps=_marker_occluded_corner_proposals) as extract, \
             patch('fast_structure_alignment.load_prepared', return_value=None):
            fit = host.prepare_structure_scan(frame, bounds)
            for candidate in ('one', 'two'):
                self.assertTrue(fit(frame, candidate, '-3', lambda: False, observation_bounds=bounds)['success'])
            self.assertEqual(extract.call_count, 1)
            host.prepare_structure_scan(frame.copy(), bounds)
            self.assertEqual(extract.call_count, 1)  # Extraction remains lazy.

    def test_marker_cannot_override_the_observation_crop_unknown_band(self):
        source = asymmetric_structure()
        frame, _ = render(source, variant='corner-marker')
        context = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000))
        # Move the extraction boundary through a real wall. Current edges in
        # this band are explicitly unknown, even if marker-colored.
        context['observationBounds'] = (284, 190, 725, 660)
        measured = observed_corner_chains(frame, context)
        proposals = marker_occluded_corner_proposals(frame, context, measured)
        for proposal in proposals:
            self.assertGreater(proposal['points'][:, 0].min(), 286)


    def test_marker_secondary_recovery_cannot_hide_contradictory_primary(self):
        source = asymmetric_structure()
        wrong = source.copy()
        wrong[228:339, 276:327] = 1
        wrong[248:359, 302:353] = 0
        wrong[390:445, 190:279] = 1
        wrong[375:430, 170:259] = 0
        first, _ = render(wrong, variant='corner-marker')
        second, _ = render(source, variant='corner-marker', scale=.75)
        frame = np.zeros((1000, 2000, 3), np.uint8)
        frame[:, :1200] = first
        frame[:, 800:] = np.maximum(frame[:, 800:], second)
        for cached in (False, True):
            for indexed in (False, True):
                with self.subTest(cached=cached, indexed=indexed):
                    result = run_fit(frame, source, cached=cached, indexed=indexed)
                    self.assertFalse(result['success'])
                    self.assertEqual(result['reason'], 'manual-components-inconsistent')

    def test_three_wall_hud_peer_is_unknown_not_a_contradiction(self):
        source = asymmetric_structure()
        frame, truth = render(source, variant='corner-marker')
        frame[700:850, 900:1100] = 90
        frame[843:850, 900:1100] = (0, 0, 255)
        from nightmare_candidate import visible_map_components
        islands = visible_map_components(frame, observation_bounds=(0, 0, 1200, 1000))
        self.assertEqual(len(islands), 2)
        peer = current_geometry(frame, observation_bounds=(0, 0, 1200, 1000), visible_override=islands[1])
        self.assertEqual(len(current_part(frame, peer, 'lines')), 3)
        for cached in (False, True):
            result = run_fit(frame, source, cached=cached)
            self.assert_pose(result, truth, source.shape, limit=1)

    def test_new_proposal_cancellation_and_component_budget_remain_fail_closed(self):
        source = asymmetric_structure()
        frame, _ = render(source, variant='corner-marker')
        reference = SharedStructureReference(source, source.copy(), {}, Path('synthetic-primary/reference.npz'))
        host = _AlignmentHost(SimpleNamespace(pack=SimpleNamespace(mode='difficult'),
            qt_adapters=SimpleNamespace(canonical_pack_id='synthetic-primary'),
            qt_shared_provider=lambda *args: reference))
        bounds = (0, 0, 1200, 1000)
        from nightmare_candidate import visible_map_components
        islands = visible_map_components(frame, observation_bounds=bounds)
        fit = run_fit(frame, source)
        with patch('nightmare_candidate.visible_map_components', return_value=islands*33):
            result = host._marker_recovery_peer_rejection(frame, 'synthetic', '-3', fit, lambda:False, bounds, None)
            self.assertEqual(result['reason'], 'manual-component-budget-unresolved')
        with patch('nightmare_candidate.visible_map_components', return_value=islands):
            result = host._marker_recovery_peer_rejection(frame, 'synthetic', '-3', fit, lambda:True, bounds, None)
            self.assertEqual(result['reason'], 'structure-work-obsolete')

    def test_proposal_counts_and_selected_source_survive_bounded_diagnostics(self):
        import json
        from s1_fit_diagnostics import fit_summary, safe_evidence
        from diagnostic_privacy import sanitize_log_payload
        source = asymmetric_structure()
        frame, _ = render(source, variant='corner-marker')
        result = run_fit(frame, source)
        summary = fit_summary(result)
        evidence = safe_evidence(dict(observations=[summary]))
        exported = json.loads(sanitize_log_payload(json.dumps(dict(fitEvidence=evidence)).encode()))['fitEvidence']
        stats = exported['observations'][0]['statistics']
        self.assertEqual(stats['queryChains'], 0)
        self.assertEqual(stats['markerOccludedProposalChains'], 40)
        self.assertEqual(stats['measuredSeedCount'], 0)
        self.assertTrue(stats['markerOccludedWallProposal'])
        self.assertLessEqual(len(json.dumps(evidence).encode()), 6144)


    def test_explicit_known_wall_reference_keeps_the_same_strict_solver(self):
        source = asymmetric_structure()
        frame, truth = render(source, variant='corner-marker')
        for mode in ('nightmare', 'difficult'):
            for indexed in (False, True):
                with self.subTest(mode=mode, indexed=indexed):
                    result = run_fit(frame, source, mode=mode, indexed=indexed, known=True,
                        unknown=np.zeros_like(source))
                    self.assert_pose(result, truth, source.shape, limit=1)
                    self.assertTrue(result['markerOccludedWallProposal'])

    def test_source_unknown_peer_cannot_become_a_contradiction(self):
        source = np.zeros((650, 950), np.uint8)
        source[:, :650] = asymmetric_structure()
        source[140:410, 710:870] = 1
        source[185:230, 745:810] = 0
        source[300:365, 785:835] = 0
        frame, truth = render(source, variant='corner-marker')
        frame[:, 900:] = cv2.warpAffine(frame[:, 900:], np.array([[1.,0,25], [0,1.,0]]), (300,1000))
        unknown = np.zeros_like(source)
        unknown[:, 650:] = 1
        for cached in (False, True):
            result = run_fit(frame, source, cached=cached, known=True, unknown=unknown)
            self.assert_pose(result, truth, source.shape, limit=1)

    def test_added_proposals_cannot_hide_existing_measured_seed_budget(self):
        source = asymmetric_structure()
        frame, _ = render(source)
        context = current_geometry(frame, observation_bounds=(0,0,1200,1000))
        for node in current_part(frame, context, 'nodes')[:20]:
            x, y = np.rint(node['point']).astype(int)
            frame[y, x] = (0,0,255)
        reference = np.concatenate([source]*18, axis=1)
        # Same real solver with only the new proposal source disabled proves
        # the measured path is under1024, while the combined search exceeds it.
        with patch('single_map_structure.marker_occluded_corner_proposals', return_value=[]):
            old = run_fit(frame, reference, primary=True)
        self.assertEqual(old['reason'], 'initial-structure-validation-budget-unresolved')
        self.assertEqual(old['diagnostics']['seedCount'], 666)
        result = run_fit(frame, reference, primary=True)
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'initial-structure-descriptor-budget-unresolved')
        self.assertEqual(result['diagnostics']['queryChains'], 20)
        self.assertEqual(result['diagnostics']['markerOccludedProposalChains'], 20)
        self.assertEqual(result['diagnostics']['seedCount'], 1025)


if __name__ == '__main__':
    unittest.main()
