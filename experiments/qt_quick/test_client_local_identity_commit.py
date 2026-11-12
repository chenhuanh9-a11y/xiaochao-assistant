"""Decoded mixed native pool through map input, source work and identity commit.

Only OS pixels/floor reads and numerical local-probe/image-fit measurements are
supplied. Source inventory/purpose, complete competition, SidePoseRecovery,
verified_side_candidate, scanner/session, reducer, core and rendering are real.
These synthetic measurements establish control-flow contracts, not map accuracy.
"""
import copy
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import LiveRuntime
from map_observation_work import MapObservationWork
from side_entry_evidence import SideEntryEvidence
from structure_pose_solver import PreparedStructurePoseSolver
from test_client_live_runtime import FixtureCapture
import test_client_structure_unification as resources
from test_export_s0_difficult_native_base_package import IDS, LEGACY


class LocalIdentityCommitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_class = resources.ImportedDifficultPreviewTests
        fixture_class.setUpClass()
        cls.addClassCleanup(fixture_class.doClassCleanups)
        fixture = fixture_class('test_force_catalogue_after_candidate_ack_clears_shared_preference')
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        cls.engine, cls.factories = fixture.engine, fixture.runtime._factories

    def setUp(self):
        self.engine = self.factories.get_engine('difficult')
        self.capture = FixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.factories,
            capture_provider=self.capture, evidence_kind='synthetic')
        self.addCleanup(self.runtime.shutdown)
        self.runtime.dispatch('set_context', dict(season='s0', mode='difficult'))
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        self.target = 'difficult-map-23'
        self.hypotheses = {self.target: 8.}
        self.sequence = 0
        self.after_probe = None
        self.foreign_source = False
        self.probed = []
        self.recovered = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.runtime, '_observe', return_value=(
            dict(detected=True, mapOpen=True), '1', dict(selectedFloor=1, mapOpen=True))))
        nodes = [dict(point=[100.+i*20, 100.+(i%3)*30],
            rays=[[1., 0.], [0., 1.]]) for i in range(8)]
        self.stack.enter_context(patch.object(self.engine.side_entry_identity, '_observe',
            return_value=(dict(query=np.ones((720,1280), np.uint8)), nodes,
                np.array([[i, (i+1)%8] for i in range(8)]))))
        def probe(work, source):
            item = source[0]
            self.probed.append(item['mapId'])
            if self.after_probe is not None:
                self.after_probe(item)
            if item['mapId'] not in self.hypotheses:
                return
            work.rows.append(dict(mapId=item['mapId'], floor=item['floor'],
                region=item['region'], sourceProduct=item['sourceProduct'] + ('.foreign' if self.foreign_source else ''),
                profileId=work.engine.profile_for_map(item['mapId']), entryKind='local-structure',
                score=self.hypotheses[item['mapId']], identityVerified=False,
                hypothesis=dict(chainCount=8, extentGrid=[4.,4.]),
                matrixSourceToClient=np.eye(3).tolist(), sideGates=[]))
        self.stack.enter_context(patch.object(SideEntryEvidence, '_probe', probe))
        recovery = MapObservationWork._recovery
        def record_recovery(work, result):
            self.recovered.append(copy.deepcopy(result[2]))
            return recovery(work, result)
        self.stack.enter_context(patch.object(MapObservationWork, '_recovery', record_recovery))
        self.verify = self.stack.enter_context(patch.object(self.engine.side_entry_identity, 'verify',
            side_effect=lambda frame, row, **kwargs: dict(success=True,
                residual=dict(verified=True), matrixSourceToClient=row['matrixSourceToClient'])))
        self.fit = self.stack.enter_context(patch.object(PreparedStructurePoseSolver, 'fit',
            return_value=resources.supported_pose()))

    def opening(self):
        self.sequence += 1
        return self.runtime.dispatch('map', dict(openIntent=True, inputSequence=self.sequence))

    def restart(self):
        self.runtime.dispatch('end_round')
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        self.probed.clear()
        self.recovered.clear()

    def assert_unlocked(self):
        state = self.runtime.snapshot()
        self.assertFalse(state['identityLocked'], dict(mapId=state['mapId'], recovery=self.recovered))
        self.assertEqual(state['mapId'], '')

    def test_fully_verified_local_identity_commits_and_reopen_uses_known_map(self):
        self.assertFalse(self.engine.qt_rank_ready)
        self.assertFalse(self.engine.side_entry_identity.semantic_side_entries)
        result = self.opening()
        self.assertTrue(result['success'], result)
        evidence = self.engine.last_difficult_evidence
        self.assertEqual(evidence['recognitionPath']['sideProducer'], 'local-structure')
        self.assertEqual(evidence['tilemapSide']['competitiveHypotheses'], 1)
        self.assertTrue(evidence['tilemapSide']['verificationComplete'])
        self.assertEqual(set(self.probed), set(IDS), 'legacy stays in actual competition')
        self.assertEqual(self.recovered[0]['mapId'], self.target)
        self.assertTrue(self.recovered[0]['identityVerified'])
        state = self.runtime.snapshot()
        self.assertTrue(state['identityLocked'], dict(mapId=state['mapId'],
            candidateOnly=state['candidateDisplayOnly'], recovery=self.recovered))
        self.assertEqual(state['mapId'], self.target)
        row = self.runtime.render_payload(result['renderToken'])
        self.assertFalse(row['candidateDisplayOnly'])
        self.assertEqual(row['mapId'], self.target)
        self.assertNotIn('observe_candidate_floor', state['availableCommands'])
        self.assertFalse(state['candidateProvisionalDisplayEnabled'])
        self.runtime.dispatch('close_map')
        with patch.object(self.engine, 'qt_structure_scan',
                side_effect=AssertionError('known-map reopening repeated catalogue scan')):
            reopened = self.runtime.dispatch('map', dict(openIntent=True, inputSequence=2))
        self.assertTrue(reopened['success'], reopened)
        self.assertEqual(self.runtime.render_payload(reopened['renderToken'])['mapId'], self.target)

    def test_legacy_winner_is_not_authorized_or_replaced_by_lower_native_proposal(self):
        self.hypotheses = {LEGACY: 12., self.target: 8.}
        self.opening()
        self.assertEqual(set(self.probed), set(IDS))
        self.assertEqual(self.recovered[0]['mapId'], LEGACY)
        self.assert_unlocked()
        self.assertIsNone(self.engine.last_difficult_evidence['recognitionPath']['sideWinnerMapId'])

    def test_legacy_rival_stays_in_complete_competition_and_blocks_native_identity(self):
        self.hypotheses[LEGACY] = 8.
        self.opening()
        self.assertEqual(set(self.probed), set(IDS))
        self.assertEqual(self.engine.last_difficult_evidence['tilemapSide']['competitiveHypotheses'], 2)
        self.assertIsNone(self.recovered[0])
        self.assert_unlocked()

    def test_explicit_source_denial_and_missing_native_purpose_cannot_lock(self):
        source = next(row[0] for row in self.engine.side_entry_identity.entries
            if (row[0]['mapId'], row[0]['floor']) == (self.target, '1'))
        for field, value in (('proposalOnly', True), ('authorizesPose', False),
                ('authorizesIdentity', False), ('sourceKind', None)):
            with self.subTest(field=field), patch.dict(source, {field: value}):
                self.opening()
                self.assertEqual(self.recovered[0]['mapId'], self.target)
                self.assert_unlocked()
            self.restart()
        self.foreign_source = True
        self.opening()
        self.assertEqual(self.recovered[0]['mapId'], self.target)
        self.assert_unlocked()

    def test_unfinished_failed_and_budget_exhausted_competition_cannot_lock(self):
        def fail_last(item):
            if item['mapId'] == LEGACY:
                raise OSError('controlled-final-local-probe-failed')
        self.after_probe = fail_last
        self.opening()
        self.assert_unlocked()
        self.assertFalse(self.engine.last_difficult_evidence['tilemapSide']['complete'])
        self.restart()
        self.after_probe = None
        with patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 0.):
            self.opening()
        self.assert_unlocked()
        self.assertEqual(self.runtime.snapshot()['structureEvidence']['currentStatus'], 'budget-unresolved')

    def test_close_during_current_local_validation_rejects_late_identity_and_pixels(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def validate(frame, row, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return dict(success=True, residual=dict(verified=True),
                matrixSourceToClient=row['matrixSourceToClient'])
        self.verify.side_effect = validate
        worker = threading.Thread(target=lambda: results.append(self.opening()))
        worker.start()
        try:
            self.assertTrue(entered.wait(5))
            self.runtime.dispatch('close_map')
        finally:
            release.set(); worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]['success'], results)
        self.assertTrue(results[0]['stale'], results)
        self.assert_unlocked()
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_cancellation_after_numeric_probe_cannot_commit_current_local_winner(self):
        cancelled = threading.Event()
        self.after_probe = lambda item: cancelled.set() if item['mapId'] == self.target else None
        result = self.runtime.dispatch('map', dict(openIntent=True, inputSequence=1), cancelled)
        self.assertFalse(result['success'], result)
        self.assertTrue(result['stale'], result)
        self.assert_unlocked()
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_resource_owner_expiring_at_identity_reducer_rejects_entry_commit(self):
        current = [True]
        commit = self.runtime._commit_identity_rows
        def supersede(*args, **kwargs):
            current[0] = False
            return commit(*args, **kwargs)
        with patch.object(self.engine, 'qt_resource_context_current', side_effect=lambda: current[0]), \
             patch.object(self.runtime, '_commit_identity_rows', side_effect=supersede) as reducer:
            result = self.opening()
        self.assertEqual(reducer.call_count, 1)
        self.assertFalse(result['success'], result)
        self.assert_unlocked()
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])


if __name__ == '__main__':
    unittest.main()
