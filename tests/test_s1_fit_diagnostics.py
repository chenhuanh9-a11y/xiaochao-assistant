"""Numeric evidence and real storage contracts; synthetic frames, no game IO."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'app'), str(ROOT/'experiments/qt_quick'), str(ROOT/'tools')]
import s1_fit_diagnostics as diag
import fast_structure_alignment as fast
from client_live_runtime import _AlignmentHost
from client_failure_samples import FailureSampleRecorder
from diagnostic_privacy import safe_record, sanitize_log_payload


def candidate():
    return dict(matrix=np.eye(3), accepted=False,
        regions=dict(regionalEdgeVerified=False, regions=[dict(count=40, median=5., within3=.3, within6=.8)]),
        residual=dict(verified=False, withinThreePixels=.2))


def evidence():
    fit = dict(success=False, reason='initial-structure-current-geometry-failed',
        diagnostics=dict(queryChains=3, sourceChains=50, seedCount=11,
                         candidateCount=1, candidates=[candidate()]))
    observation = diag.observation_summary(np.zeros((40, 60, 3), np.uint8),
        dict(query=np.ones((40, 60), np.uint8), observationBounds=(4, 5, 55, 36)), fit, 'shared-initial-structure')
    event = dict(event='s1-fit-observation', mapId='107009', floor='-3', **observation)
    return diag.scan_summary([event, dict(event='experimental-pose-prior', mapId='107009', floor='-3',
        priorMatrix=np.eye(3), accepted=False, fallback=True, elapsedMs=1.,
        rejection=dict(reason='fast-existing-validation-rejected'))], mode='nightmare',
        resource_key='a'*64, floor='-3', frame_shape=(40, 60, 3))


class TypedEvidenceTests(unittest.TestCase):
    def test_nested_prior_attempts_cannot_bypass_hard_json_limit(self):
        payload = {key: 123456789.1234567 for key in ('queryChains', 'sourceChains', 'seedCount',
            'seedBudget', 'candidateCount', 'validationBudget', 'acceptedPoseCount', 'points', 'coarsePoints',
            'proposalMs', 'totalMs', 'elapsedMs', 'maskEdges', 'contrastSupportedEdges', 'withinThreePixels',
            'withinSixPixels', 'medianDistance', 'medianErrorPixels', 'count', 'median', 'within3', 'within6')}
        nested = dict(payload, matrix=np.eye(3).tolist(), reason='fast-existing-validation-rejected', accepted=False)
        for _ in range(7):
            nested = dict(payload, priorAttempts=[nested]*3, statistics=nested, regions=nested, priorBinding=nested)
        value = dict(schemaVersion=1, priorAttempts=[dict(matrix=np.eye(3).tolist(), accepted=False,
            priorAttempts=[nested]*3, regions=nested) for _ in range(3)])
        result = diag.safe_evidence(value)
        self.assertLessEqual(len(json.dumps(result).encode()), diag.MAX_BYTES)
        self.assertEqual(result['priorAttempts'][0]['matrix'], np.eye(3).tolist())
        self.assertNotIn('priorAttempts', result['priorAttempts'][0])
        self.assertTrue(result['truncated'])

    def test_matrix_and_rejection_survive_privacy_export(self):
        row = dict(fitEvidence=evidence())
        exported = json.loads(sanitize_log_payload(json.dumps(row).encode()))['fitEvidence']
        self.assertEqual(exported['priorAttempts'][0]['matrix'], np.eye(3).tolist())
        self.assertEqual(exported['priorAttempts'][0]['rejection']['reason'], 'fast-existing-validation-rejected')
        self.assertEqual(exported['observations'][0]['statistics']['seedCount'], 11)
        self.assertEqual(exported['observations'][0]['candidates'][0]['regionMetrics'][0]['median'], 5.)

    def test_arbitrary_text_pixels_paths_and_nonfinite_values_are_rejected(self):
        value = evidence()
        value.update(username='PRIVATE_NAME', frame=np.ones((400, 500)),
                     priorBinding=dict(resourceKey='PRIVATE_NAME', ownerScopeDigest='b'*64,
                         sourceFloor='-2', targetFloor='-3', matrix=np.ones((4, 4))))
        value['observations'][0].update(reason='OSError: PRIVATE_NAME C:/private/file',
            queryPixelCount=float('inf'), candidates=[dict(matrix=[[1, 0, float('nan')], [0, 1, 0], [0, 0, 1]])])
        result = diag.safe_evidence(value)
        text = json.dumps(result, allow_nan=False)
        for private in ('PRIVATE_NAME', 'C:/private', 'username', 'NaN', 'Infinity'):
            self.assertNotIn(private, text)
        self.assertIsNone(result['observations'][0]['candidates'][0]['matrix'])
        self.assertIsNone(result['observations'][0]['queryPixelCount'])
        self.assertGreater(result['omittedEvidenceCount'], 0)

    def test_bounds_and_does_not_modify_source(self):
        value = evidence()
        value['observations'] *= 100
        value['priorAttempts'] *= 100
        before = copy.deepcopy(value)
        result = diag.safe_evidence(value)
        self.assertEqual(value, before)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':')).encode()), diag.MAX_BYTES)
        self.assertLessEqual(len(json.dumps(result).encode()), diag.MAX_BYTES)
        self.assertEqual(len(result['observations']), 2)
        self.assertEqual(len(result['priorAttempts']), 3)
        self.assertTrue(result['truncated'])

    def test_truncated_event_ring_reports_actual_omissions(self):
        result = diag.scan_summary([], mode='nightmare', resource_key='a'*64, floor='-3', frame_shape=(40, 60, 3),
            event_counts={'s1-fit-observation': 32, 'experimental-pose-prior': 31})
        self.assertEqual(result['omittedObservationCount'], 30)
        self.assertEqual(result['omittedPriorCount'], 28)

    def test_invalid_matrices_and_numeric_bounds(self):
        for value in (np.ones((30, 30)), [[True, 0, 0], [0, 1, 0], [0, 0, 1]],
                      [[1, 0, 1e9], [0, 1, 0], [0, 0, 1]], [[1, 0, 0], [0, 1, 0], [0, .1, 1]]):
            self.assertIsNone(diag.matrix(value))

    def test_huge_integers_and_recursive_lists_do_not_raise_or_escape_budget(self):
        cyclic = []
        cyclic.append(dict(priorAttempts=cyclic, matrix=np.eye(3).tolist(), accepted=False))
        for bad in (10**1000, -(10**1000), float('nan'), float('inf')):
            result = diag.safe_evidence(dict(schemaVersion=bad, priorAttempts=cyclic,
                fastAttempts=[dict(elapsedMs=bad, residualMatrix=[[1, 0, bad], [0, 1, 0], [0, 0, 1]])]))
            self.assertLessEqual(len(json.dumps(result, allow_nan=False).encode()), diag.MAX_BYTES)
            self.assertIsNone(result['schemaVersion'])
            self.assertIsNone(result['fastAttempts'][0]['residualMatrix'])

    def test_large_valid_stage_detail_keeps_useful_local_global_summary(self):
        value = evidence()
        phase = dict(accepted=False, reason='fast-existing-validation-rejected',
            proposalMatrix=np.eye(3).tolist(), residualMatrix=np.eye(3).tolist(),
            regions=dict(regionalEdgeVerified=False), residual=dict(verified=False),
            regionMetrics=[dict(median=12345678.12345, within3=.123456789, within6=.123456789, count=12345678) for _ in range(9)])
        value['priorAttempts'] = [dict(matrix=np.eye(3).tolist(), accepted=False, fallback=True, rejection=phase) for _ in range(3)]
        value['fastAttempts'] = [dict(phase, searchKind=kind) for kind in ('local-prior', 'global-search', 'global-search')]
        result = diag.safe_evidence(value)
        self.assertLessEqual(len(json.dumps(result).encode()), diag.MAX_BYTES)
        self.assertEqual(result['priorAttempts'][0]['matrix'], np.eye(3).tolist())
        self.assertEqual(result['fastAttempts'][-1]['searchKind'], 'global-search')
        self.assertEqual(result['fastAttempts'][-1]['reason'], 'fast-existing-validation-rejected')
        self.assertEqual(result['fastAttempts'][-1]['residualMatrix'], np.eye(3).tolist())

    def test_known_key_nesting_has_node_and_byte_budgets(self):
        nested = dict(count=1)
        for _ in range(8):
            nested = {key: nested for key in ('residual', 'regions', 'statistics', 'rejection', 'priorBinding')}
        result = diag.safe_evidence(nested)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':')).encode()), diag.MAX_BYTES)
        self.assertTrue(result['truncated'])

    def test_oversized_candidates_preserve_actual_prior_matrix(self):
        value = evidence()
        row = value['observations'][0]['candidates'][0]
        row['regionMetrics'] = [dict(count=10**8, median=12345.12345, within3=.123456789, within6=.987654321) for _ in range(9)]
        value['observations'] *= 2
        for observation in value['observations']:
            observation['candidates'] = [copy.deepcopy(row), copy.deepcopy(row)]
        result = diag.safe_evidence(value)
        self.assertLessEqual(len(json.dumps(result, separators=(',', ':')).encode()), diag.MAX_BYTES)
        self.assertEqual(result['priorAttempts'][0]['matrix'], np.eye(3).tolist())


class FitStageTests(unittest.TestCase):
    def test_refined_point_and_wall_matrices_match_their_validation_stage(self):
        for wall, residual_ok in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(wall=wall, residual_ok=residual_ok):
                host = self.host()
                reference = host.engine.qt_shared_provider(None, None, None)
                reference.known_boundary = None
                point = np.array([[1., 0., 17.], [0., 1., 23.], [0., 0., 1.]])
                final = np.array([[1., 0., 31.], [0., 1., 41.], [0., 0., 1.]]) if wall else point
                host.map_id, host.floor = '107009', '-3'
                context = dict(query=np.ones((20, 20), np.uint8), nodes=[])
                used = []
                def residual(frame, ref, matrix, **kw):
                    used.append(matrix.copy())
                    return dict(verified=True)
                with patch.object(fast, 'load_prepared', return_value=object()), \
                        patch.object(fast, 'propose', return_value=([(1., 1., [0., 0.])], context, {})), \
                        patch.object(fast, 'native_point_geometry', return_value=dict(structureModel='point', point=dict(success=True, matrixSourceToClient=point))), \
                        patch('single_map_structure.projected_geometry', return_value=dict(mask=np.ones((20, 20)))), \
                        patch('single_map_structure.regional_validation', return_value=dict(regionalEdgeVerified=not wall)), \
                        patch('single_map_structure.partial_wall_geometry', return_value=dict(success=True, matrix=final)), \
                        patch.object(host, '_rendered_profile_current_frame_residual', side_effect=residual), \
                        patch('alignment_observation.rendered_profile_residual_is_verified', return_value=residual_ok):
                    fit = fast._try_alignment(host, np.zeros((20, 20, 3), np.uint8), '107009', '-3')
                self.assertEqual(bool(fit), residual_ok)
                event = host.events[-1]
                self.assertEqual(event['accepted'], residual_ok)
                np.testing.assert_array_equal(event['proposalMatrix'], np.eye(3))
                np.testing.assert_array_equal(event['pointMatrix'], point)
                np.testing.assert_array_equal(event['regionalMatrix'], point)
                np.testing.assert_array_equal(event['residualMatrix'], used[-1])
                np.testing.assert_array_equal(event['residualMatrix'], final)
                if wall:
                    np.testing.assert_array_equal(event['wallMatrix'], final)
                summary = diag.scan_summary(host.events, mode='nightmare', resource_key='a'*64,
                    floor='-3', frame_shape=(20, 20))
                exported = safe_record(dict(fitEvidence=summary))['fitEvidence']['fastAttempts'][-1]
                self.assertEqual(exported['residualMatrix'], final.tolist())

    def host(self, collect=True):
        reference = SimpleNamespace(clean_mask=np.ones((20, 20), np.uint8),
            metadata=dict(binding=dict(mapId='107009', floor='-3'), sourceGeometry={}))
        engine = SimpleNamespace(pack=None, qt_adapters=None,
            qt_shared_provider=lambda *a: reference, identity_profile_id='shared-structure')
        return _AlignmentHost(engine, collect_fit_diagnostics=collect)

    def test_failed_fit_retains_candidates_and_observation_without_changing_verdict(self):
        host = self.host()
        fit = dict(success=False, reason='initial-structure-current-geometry-failed',
                   diagnostics=dict(seedCount=11, candidates=[candidate()]))
        frame = np.zeros((40, 60, 3), np.uint8)
        context = dict(query=np.ones((40, 60), np.uint8), observationBounds=(4, 5, 55, 36))
        with patch('fast_structure_alignment.try_alignment', return_value=fit):
            actual = host._fit_observation(frame, '107009', '-3', lambda: False,
                observation_bounds=(0, 0, 60, 40), _context=context)
        self.assertIs(actual, fit)
        row = host.events[-1]
        self.assertEqual(row['statistics']['seedCount'], 11)
        self.assertEqual(row['observationBounds'], [4, 5, 55, 36])
        self.assertEqual(row['resourceBinding']['floor'], '-3')

    def test_bad_diagnostic_hook_cannot_change_failure(self):
        host = self.host()
        fit = dict(success=False, reason='initial-structure-current-geometry-failed')
        with patch('fast_structure_alignment.try_alignment', return_value=fit), \
                patch('s1_fit_diagnostics.observation_summary', side_effect=RuntimeError('fixture')):
            self.assertIs(host._fit_observation(np.zeros((20, 20, 3), np.uint8), '107009', '-3', None), fit)

    def test_final_canonical_residual_rejection_is_the_recorded_verdict(self):
        host = self.host()
        fit = dict(success=True, matrixSourceToClient=np.eye(3), reason='initial-structure-candidate')
        with patch('fast_structure_alignment.try_alignment', return_value=fit), \
                patch.object(host, '_rendered_profile_current_frame_residual', return_value=dict(verified=False, withinThreePixels=.2)):
            result = host._fit_observation(np.zeros((20, 20, 3), np.uint8), '107009', '-3', None)
        self.assertFalse(result['success'])
        self.assertEqual(host.events[-1]['reason'], 'canonical-current-frame-residual-unverified')
        self.assertEqual(host.events[-1]['residual']['withinThreePixels'], .2)

    def test_opt_out_keeps_no_detailed_observation(self):
        host = self.host(False)
        fit = dict(success=False, reason='initial-structure-current-geometry-failed')
        with patch('fast_structure_alignment.try_alignment', return_value=fit):
            host._fit_observation(np.zeros((20, 20, 3), np.uint8), '107009', '-3', None)
        self.assertEqual(host.events, [])

    def test_local_reject_then_global_success_or_failure_are_separate(self):
        for success in (True, False):
            host = self.host()
            def stage(*a, **kw):
                local = kw.get('_external_prior') is not None
                host._log('experimental-fast-alignment', searchKind='local-prior' if local else 'global-search',
                          accepted=success and not local, reason='fast-existing-validation-rejected')
                return dict(success=True) if not local and success else None
            with patch.object(fast, '_try_alignment', side_effect=stage):
                result = fast.try_alignment(host, np.zeros((20, 20, 3), np.uint8), '107009', '-3', pose_prior=np.eye(3))
            self.assertEqual(bool(result), success)
            report = diag.scan_summary(host.events, mode='nightmare', resource_key='a'*64, floor='-3', frame_shape=(20, 20))
            self.assertEqual([r['searchKind'] for r in report['fastAttempts']], ['local-prior', 'global-search'])
            self.assertFalse(report['priorAttempts'][0]['accepted'])
            self.assertTrue(report['priorAttempts'][0]['fallback'])
            self.assertEqual(report['fastAttempts'][-1]['accepted'], success)

    def test_missing_prepared_resource_reason_is_preserved(self):
        host = self.host()
        with patch.object(fast, 'load_prepared', return_value=None):
            result = fast.try_alignment(host, np.zeros((20, 20, 3), np.uint8), '107009', '-3', pose_prior=np.eye(3))
        self.assertIsNone(result)
        prior = next(e for e in host.events if e['event'] == 'experimental-pose-prior')
        self.assertEqual(prior['rejection']['reason'], 'fast-prepared-unavailable')


class AutomaticEvidenceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows handle storage')
    def test_invalidation_during_image_write_keeps_intent_without_final_commit(self):
        import failure_sampling
        alive = [True]
        with tempfile.TemporaryDirectory() as folder:
            recorder = FailureSampleRecorder(); recorder.automatic_enabled = True
            original = failure_sampling.create_automatic_image
            def invalidate(*args, **kwargs):
                result = original(*args, **kwargs)
                alive[0] = False
                return result
            with patch.object(failure_sampling, 'create_automatic_image', side_effect=invalidate):
                result = recorder.record_automatic(root=folder, reason='no-current-supported-candidates',
                    frame=np.zeros((40, 60, 3), np.uint8), diagnostic=dict(evidenceKind='live-client-capture', fitEvidence=evidence()),
                    current=lambda: alive[0], crop_bounds=(10, 12, 20, 16), crop_provenance='color-component-unvalidated')
            self.assertFalse(result['saved'])
            self.assertTrue(result['recoveryRequired'])
            rows = [json.loads(line) for line in (Path(folder)/'failures/automatic-v2/automatic-failures.jsonl').read_bytes().splitlines()]
            self.assertTrue(list(Path(folder).rglob('*.png')))
            self.assertEqual(rows[-1]['event'], 'automatic-failure-image-intent')
            self.assertFalse(any(row['event'] == 'automatic-failure-sample' for row in rows))
            self.assertEqual(recorder.automatic_saved_count, 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows handle storage')
    def test_saved_crop_mapping_is_distinct_from_solver_component(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FailureSampleRecorder()
            recorder.automatic_enabled = True
            value = evidence()
            value['privatePath'] = 'PRIVATE_NAME'
            result = recorder.record_automatic(root=folder, reason='no-current-supported-candidates',
                frame=np.zeros((40, 60, 3), np.uint8), diagnostic=dict(evidenceKind='live-client-capture', fitEvidence=value),
                current=lambda: True, crop_bounds=(10, 12, 20, 16), crop_provenance='color-component-unvalidated')
            self.assertTrue(result['saved'], result)
            rows = [json.loads(line) for line in (Path(folder)/'failures/automatic-v2/automatic-failures.jsonl').read_bytes().splitlines()]
            saved = rows[-1]['diagnostic']['fitEvidence']
            self.assertEqual(saved['frameShapeHW'], [40, 60])
            self.assertEqual(saved['cropOriginClientXY'], [10, 12])
            self.assertEqual(saved['cropShapeHW'], [16, 20])
            self.assertEqual(saved['sampleScaleXY'], [1, 1])
            self.assertEqual(saved['observations'][0]['observationBounds'], [4, 5, 55, 36])
            self.assertNotIn('PRIVATE_NAME', json.dumps(rows))

    def test_disabled_and_stale_are_zero_write(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FailureSampleRecorder()
            for enabled, current in ((False, True), (True, False)):
                recorder.automatic_enabled = enabled
                result = recorder.record_automatic(root=folder, reason='no-current-supported-candidates', frame=None,
                    diagnostic=dict(fitEvidence=evidence()), current=lambda: current)
                self.assertFalse(result['saved'])
                self.assertEqual(list(Path(folder).iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'Windows handle storage')
    def test_no_frame_drops_fit_details_in_metadata_only_result(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FailureSampleRecorder(); recorder.automatic_enabled = True
            result = recorder.record_automatic(root=folder, reason='no-current-supported-candidates', frame=None,
                diagnostic=dict(fitEvidence=evidence()), current=lambda: True)
            self.assertFalse(result['saved'])
            rows = [json.loads(line) for line in (Path(folder)/'failures/automatic-v2/automatic-failures.jsonl').read_bytes().splitlines()]
            self.assertNotIn('fitEvidence', rows[-1]['diagnostic'])

    @unittest.skipUnless(os.name == 'nt', 'Windows handle storage')
    def test_exhausted_image_budget_returns_metadata_without_image(self):
        with tempfile.TemporaryDirectory() as folder:
            recorder = FailureSampleRecorder(); recorder.automatic_enabled = True
            with patch('failure_sampling.ensure_automatic_capacity', return_value=dict(complete=False, status='image-byte-limit')):
                result = recorder.record_automatic(root=folder, reason='no-current-supported-candidates',
                    frame=np.zeros((40, 60, 3), np.uint8), diagnostic=dict(evidenceKind='live-client-capture', fitEvidence=evidence()),
                    current=lambda: True, crop_bounds=(10, 12, 20, 16), crop_provenance='color-component-unvalidated')
            self.assertFalse(result['saved'])
            self.assertEqual(result['missingReason'], 'image-byte-limit')
            self.assertEqual(list(Path(folder).rglob('*.png')), [])


class RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self):
        import test_s1_formal_round_lock as formal
        self.fx = formal.FormalRoundLockContracts()
        self.fx.setUp(); self.addCleanup(self.fx.doCleanups)

    def test_only_locked_enabled_failure_hands_details_to_existing_recorder(self):
        self.fx.locked()
        runtime = self.fx.runtime
        runtime.set_automatic_failure_sampling_enabled(True)
        self.fx.registry.engine.memory_store = SimpleNamespace(root=Path('/synthetic-no-io'))
        original = self.fx.registry.engine.qt_s1_structure_scan
        options, saved = [], []
        def scan(*a, **kw):
            options.append(kw)
            # The fixture forwards directly to Scanner, unlike the production
            # provider which consumes this optional diagnostic flag itself.
            forwarded = dict(kw); forwarded.pop('collect_fit_diagnostics', None)
            report = original(*a, **forwarded)
            report['fitEvidence'] = evidence()
            return report
        self.fx.registry.engine.qt_s1_structure_scan = scan
        runtime._failure_samples.record_automatic = lambda **kw: saved.append(dict(kw, currentAtWrite=kw['current']())) or dict(saved=False, missingReason='mock-no-file-io')
        self.fx.unknown.add('a')
        self.assertFalse(runtime.dispatch('capture_now')['success']); self.fx.idle()
        self.assertTrue(options[-1]['collect_fit_diagnostics'])
        self.assertTrue(saved[-1]['currentAtWrite'])
        self.assertIn('fitEvidence', saved[-1]['diagnostic'])
        self.assertTrue(runtime.snapshot()['identityLocked'])

    def test_prelock_or_disabled_does_not_request_detailed_collection(self):
        options = []
        original = self.fx.registry.engine.qt_s1_structure_scan
        def scan(*a, **kw):
            options.append(kw)
            return original(*a, **kw)
        self.fx.registry.engine.qt_s1_structure_scan = scan
        self.fx.runtime.set_automatic_failure_sampling_enabled(True)
        self.fx.show(); self.fx.idle()
        self.assertTrue(all('collect_fit_diagnostics' not in kw for kw in options))
        self.fx.runtime.set_automatic_failure_sampling_enabled(False)
        self.fx.runtime.dispatch('capture_now'); self.fx.idle()
        self.assertNotIn('collect_fit_diagnostics', options[-1])


class CropReplayTests(unittest.TestCase):
    def test_scaled_crop_points_map_back_to_client_without_full_frame_claim(self):
        from replay_s1_crop_observation import replay
        import cv2
        image = np.zeros((120, 160, 3), np.uint8)
        image[20:90, 30:120] = (130, 160, 180)
        result = replay(image, np.ones((100, 100), np.uint8), [1405, 371])
        self.assertFalse(result['fullFrameReproduction'])
        for row in result['rows']:
            local = np.array(row['exampleCropChainCenters']).reshape(-1, 2)
            expected = (local + .5) / row['actualScaleXY'] - .5 + [1405, 371]
            np.testing.assert_allclose(np.array(row['exampleClientChainCenters']).reshape(-1, 2), expected)

    def test_nonempty_synthetic_centers_use_actual_resize_scale_and_crop_origin(self):
        import replay_s1_crop_observation as replay
        chain = dict(points=np.array([[10., 10.], [20., 30.], [40., 30.]]), rays=np.eye(2))
        context = dict(query=np.ones((120, 160), np.uint8), observationBounds=[0, 0, 160, 120],
                       observed=np.ones((120, 160), bool))
        with patch.object(replay, 'current_geometry', return_value=context), \
                patch.object(replay, 'observed_corner_chains', return_value=[chain]):
            result = replay.replay(np.zeros((120, 160, 3), np.uint8), np.ones((100, 100), np.uint8), [1405, 371], scales=(1., .75, 1.25))
        self.assertEqual(result['rows'][0]['queryChains'], 1)
        self.assertEqual(result['rows'][0]['exampleClientChainCenters'], [[1425., 401.]])
        np.testing.assert_allclose(result['rows'][1]['exampleClientChainCenters'], [[1431+5/6, 411+1/6]])
        np.testing.assert_allclose(result['rows'][2]['exampleClientChainCenters'], [[1420.9, 394.9]])


if __name__ == '__main__':
    unittest.main()
