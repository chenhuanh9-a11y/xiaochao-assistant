"""Real feature cache, matching, held-out math and source PNGs; query is synthetic."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from entry_evidence_work import ObservationWorkScheduler
from map_identity_engine import MapIdentityEngine
from nightmare_candidate import NightmareSourceCandidateStore, _Features
import test_observation_work_scheduler as entry_fixture
from entry_evidence_work import EntryEvidenceWork


class SourceStructureJobTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'references').mkdir()

    def engine(self, *, variants=False):
        points = np.array([(x*30+15, y*30+15) for y in range(8) for x in range(8)], np.float32)
        descriptors = np.random.default_rng(907).normal(size=(64, 128)).astype(np.float32)
        config = dict(schemaVersion=1, maxEdge=1000, opencvVersion=cv2.__version__, entryCount=2)
        arrays = dict(metadata=np.array(json.dumps(config)))
        rows = []
        for i in range(2):
            count = 28 if variants and i else 64
            values = descriptors[:count] if variants or i == 0 else (
                np.random.default_rng(1102).normal(size=(count, 128)).astype(np.float32))
            key = 'sheet'+str(i)
            arrays[key+'_keypoints'] = np.column_stack((points[:count], np.full(count, 5.),
                np.full(count, -1.), np.ones(count), np.zeros(count), np.full(count, -1.))).astype(np.float32)
            arrays[key+'_descriptors'] = values
            size = 250 if variants and i else 500
            png = self.root / ('sheet'+str(i)+'.png')
            image = np.zeros((size, size, 4), np.uint8)
            image[:, :, 3] = 255
            ok, encoded = cv2.imencode('.png', image)
            self.assertTrue(ok)
            png.write_bytes(encoded.tobytes())
            rows.append(dict(sourceGroup='alpha' if variants or i == 0 else 'beta',
                variant='sheet'+str(i), sourceFloor='producer-B3', sourceCandidateId=key,
                featureKey=key, featureScale=1., canvasPx=[size, size], resourcePath=png.name))
        np.savez(self.root / 'features.npz', **arrays)
        payload = dict(schemaVersion=1, entries=rows, featureStore=dict(config, path='features.npz'))
        (self.root / 'references/nightmare-source-candidate-index.json').write_text(json.dumps(payload), encoding='utf8')
        engine = MapIdentityEngine.__new__(MapIdentityEngine)
        store = NightmareSourceCandidateStore(self.root)
        self.assertIsNone(store.error)
        engine.pack = SimpleNamespace(source_candidates=store)
        engine.max_edge, engine.matcher = 1000, cv2.BFMatcher(cv2.NORM_L2)
        query_points = points*.7 + [120, 80]
        query = _Features([cv2.KeyPoint(float(x), float(y), 5.) for x, y in query_points], descriptors, 1.)
        engine._features = lambda image: query
        return engine, np.zeros((720, 1280, 3), np.uint8)

    def prepare(self, engine, frame):
        return engine.prepare_source_structure_candidates(frame, floor='2', deadline=time.perf_counter()+10)

    def drain(self, work):
        phases = []
        status = ObservationWorkScheduler(clock=time.perf_counter).drive(work, deadline=work.deadline,
            on_job_finished=lambda job: phases.append((job.phase, job.map_id)))
        return status, phases, work.finish()

    def test_original_source_entry_runs_real_matching_and_heldout_validation(self):
        engine, frame = self.engine()
        result = engine._rank_source_structure_candidates(frame, floor='2', budget_seconds=10.)
        self.assertTrue(result['complete'])
        self.assertEqual(result['automaticCandidate']['runtimeMapId'], 'source-structure-alpha')
        self.assertEqual(result['automaticCandidate']['gameFloor'], '2')
        self.assertEqual(result['automaticCandidate']['producerSourceFloorLabel'], 'producer-B3')
        self.assertFalse(result['identityHardGatePassed'])
        self.assertFalse(result['poseSubmissionAllowed'])
        self.assertFalse(result['authorRouteDisplayAllowed'])

    def test_stepwise_entry_preserves_namespaces_and_all_required_sources(self):
        engine, frame = self.engine()
        work = self.prepare(engine, frame)
        frame[:] = 99
        status, phases, report = self.drain(work)
        self.assertEqual(status, 'complete')
        self.assertEqual(phases[:2], [('source-feature-store', None), ('source-query-features', None)])
        self.assertEqual([mid for phase, mid in phases if phase == 'source-match-heldout'],
                         ['source-structure-alpha', 'source-structure-beta'])
        self.assertEqual(report['evaluatedEntryCount'], 2)
        self.assertTrue(report['complete'])
        self.assertEqual(report['automaticCandidate']['runtimeMapId'], 'source-structure-alpha')
        self.assertFalse(report['automaticCandidate']['poseVerified'])
        self.assertEqual(int(work.frame[0, 0, 0]), 0)
        with self.assertRaises(ValueError):
            work.frame.setflags(write=True)

    def test_unscanned_rival_cannot_become_unique_and_closed_job_cannot_replay(self):
        engine, frame = self.engine()
        work = self.prepare(engine, frame)
        for _ in range(3):
            work.next_job().run()
        pending = work.next_job()
        self.assertEqual(pending.map_id, 'source-structure-beta')
        report = work.finish()
        self.assertFalse(report['complete'])
        self.assertEqual(report['evaluatedEntryCount'], 1)
        self.assertIsNone(report['automaticCandidate'])
        self.assertEqual(report['candidates'][0]['sourceGroup'], 'alpha')
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_deadline_between_matches_preserves_unresolved_competitor(self):
        engine, frame = self.engine()
        clock = [100.]
        with patch('source_structure_work.time.perf_counter', side_effect=lambda: clock[0]):
            work = engine.prepare_source_structure_candidates(frame, floor='2', deadline=101.)
            def done(job):
                if job.phase == 'source-match-heldout':
                    clock[0] = 102.
            status = ObservationWorkScheduler(clock=lambda: clock[0]).drive(work,
                deadline=101., on_job_finished=done)
            report = work.finish()
        self.assertEqual(status, 'budget-unresolved')
        self.assertFalse(report['complete'])
        self.assertEqual(report['evaluatedEntryCount'], 1)
        self.assertIsNone(report['automaticCandidate'])

    def test_source_error_does_not_claim_budget_exhaustion_or_identity(self):
        engine, frame = self.engine()
        match = engine._match
        calls = []
        def fail_rival(reference, query, **kwargs):
            calls.append(reference)
            if len(calls) == 2:
                raise OSError('required source failed')
            return match(reference, query, **kwargs)
        work = self.prepare(engine, frame)
        with patch.object(engine, '_match', side_effect=fail_rival):
            with self.assertRaisesRegex(OSError, 'required source failed'):
                self.drain(work)
        report = work.finish()
        self.assertEqual(report['reason'], 'source-structure-work-failed')
        self.assertFalse(report['complete'])
        self.assertIsNone(report['automaticCandidate'])

    def test_real_variant_pixels_are_checked_before_provisional_sheet_selection(self):
        engine, frame = self.engine(variants=True)
        status, phases, report = self.drain(self.prepare(engine, frame))
        self.assertEqual(status, 'complete')
        self.assertIn(('source-variant-visibility', 'source-structure-alpha'), phases)
        self.assertTrue(report['variantExclusiveEvidence']['exclusivePixels'] >= 500)
        selected = report['automaticCandidate']
        self.assertIsNotNone(selected, report)
        self.assertTrue(selected['producerVariantUncertain'])
        self.assertEqual(selected['producerVariantLabel'], 'sheet1')
        self.assertFalse(selected['poseSubmissionAllowed'])

    def test_unfinished_variant_pixels_do_not_authorize_even_after_all_matches(self):
        engine, frame = self.engine(variants=True)
        work = self.prepare(engine, frame)
        for _ in range(4):
            work.next_job().run()
        pending = work.next_job()
        self.assertEqual(pending.phase, 'source-variant-visibility')
        report = work.finish()
        self.assertEqual(report['evaluatedEntryCount'], report['availableEntryCount'])
        self.assertFalse(report['complete'])
        self.assertIsNone(report['automaticCandidate'])
        with self.assertRaisesRegex(ValueError, 'entry-job-not-current'):
            pending.run()

    def test_cancel_after_first_source_keeps_its_rival_unresolved(self):
        engine, frame = self.engine()
        work = self.prepare(engine, frame)
        cancelled = [False]
        def done(job):
            if job.phase == 'source-match-heldout':
                cancelled[0] = True
        status = ObservationWorkScheduler(clock=time.perf_counter).drive(work,
            deadline=work.deadline, cancelled=lambda: cancelled[0], on_job_finished=done)
        report = work.finish()
        self.assertEqual(status, 'cancelled')
        self.assertFalse(report['complete'])
        self.assertEqual(report['evaluatedEntryCount'], 1)
        self.assertIsNone(report['automaticCandidate'])

    def test_invalid_actual_feature_cache_reports_capability_gap(self):
        engine, frame = self.engine()
        (self.root / 'features.npz').write_bytes(b'not a feature archive')
        status, phases, report = self.drain(self.prepare(engine, frame))
        self.assertEqual(status, 'complete')
        self.assertEqual(phases, [('source-feature-store', None)])
        self.assertEqual(report['reason'], 'source-feature-cache-unavailable')
        self.assertFalse(report['featureStatus']['ready'])
        self.assertEqual(report['candidates'], [])

    def qualified_entry_engine(self):
        fixture = entry_fixture.EntryObservationTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture.engine(unique=True), fixture.fixture.frame

    def test_optional_source_budget_does_not_remove_completed_entry_qualification(self):
        source, _ = self.engine()
        engine, frame = self.qualified_entry_engine()
        engine.pack.source_candidates = source.pack.source_candidates
        engine.max_edge, engine.matcher, engine._features = source.max_edge, source.matcher, source._features
        clock = [100.]
        with patch('source_structure_work.time.perf_counter', side_effect=lambda: clock[0]):
            work = engine.prepare_entry_observation(frame, '2', deadline=101.)
            def done(job):
                if job.phase == 'source:source-feature-store':
                    clock[0] = 102.
            status = ObservationWorkScheduler(clock=lambda: clock[0]).drive(work,
                deadline=101., on_job_finished=done)
            rows = work.finish()
        self.assertEqual(status, 'budget-unresolved')
        self.assertTrue(work.entry_complete)
        self.assertEqual([row['mapId'] for row in rows if row['identityHardGatePassed']], ['map-a'])
        self.assertFalse(engine.last_difficult_evidence['sourceStructureCandidates']['complete'])
        self.assertIsNone(engine.last_difficult_evidence['sourceStructureCandidates']['automaticCandidate'])

    def test_failed_condition_cannot_leave_cached_entry_qualification_or_diagnostics(self):
        engine, frame = self.qualified_entry_engine()
        class FailingCondition(EntryEvidenceWork):
            def _fail(self):
                raise MemoryError('condition allocation failed')
            def _jobs(self):
                yield self._job(None, 'condition-allocation', self._fail)
            def finish(self):
                self.close()
                return dict(status='conditions', complete=False, reason='controlled-allocation-failure',
                    authorizesIdentity=False, authorizesPose=False)
        engine.native_similarity_index = SimpleNamespace(prepare_proposal=lambda *a, **kw: FailingCondition(None))
        work = engine.prepare_entry_observation(frame, '2', deadline=time.perf_counter()+10)
        with self.assertRaisesRegex(MemoryError, 'condition allocation failed'):
            ObservationWorkScheduler(clock=time.perf_counter).drive(work, deadline=work.deadline)
        self.assertTrue(work.entry_complete)
        self.assertTrue(work._failed)
        rows = work.finish()
        self.assertFalse(any(row['identityHardGatePassed'] or row['strongSuggestion'] for row in rows))
        self.assertFalse(rows[0]['difficultEntry']['identityVerified'])
        self.assertFalse(rows[0]['difficultRecognition']['main']['complete'])


if __name__ == '__main__':
    unittest.main()
