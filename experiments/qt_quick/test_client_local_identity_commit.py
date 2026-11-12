"""Decoded mixed native pool through map input, source work and identity commit.

Only OS pixels/floor reads and numerical local-probe/image-fit measurements are
supplied. Source inventory/purpose, complete competition, SidePoseRecovery,
verified_side_candidate, scanner/session, reducer, core and rendering are real.
Negative-purpose cases substitute explicit fields on the actual query's source
metadata; its consumer stays real. Query factories and race/scan spies delegate
to their original implementations. Synthetic measurements establish control-flow
contracts, not map accuracy or performance.
"""
import copy
from contextlib import ExitStack
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np

from client_live_runtime import LiveRuntime
from difficult_side_entry_identity import MapLocalEntryIdentity
from map_observation_work import MapObservationWork
from side_entry_evidence import SideEntryEvidence
from structure_pose_solver import PreparedStructurePoseSolver
from structure_pose_inputs import _freeze
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
        self.before_verify = None
        self.foreign_source = False
        self.source_changes = {}
        self.lane_hypotheses = {}
        self.probed = []
        self.probed_by_lane = {}
        self.recovered = []
        self.queries = []
        self.scan_reports = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.runtime, '_observe', return_value=(
            dict(detected=True, mapOpen=True), '1', dict(selectedFloor=1, mapOpen=True))))
        nodes = [dict(point=[100.+i*20, 100.+(i%3)*30],
            rays=[[1., 0.], [0., 1.]]) for i in range(8)]
        # The production lanes create independent queries from prepared static
        # templates. Replace only measured pixels/verification on the actual
        # locator class, rather than patching a live instance nobody consumes.
        def observe(locator, frame):
            return (dict(query=np.ones((720,1280), np.uint8)), copy.deepcopy(nodes),
                    np.array([[i, (i+1)%8] for i in range(8)]))
        self.stack.enter_context(patch.object(MapLocalEntryIdentity, '_observe', observe))
        prepared = self.engine.qt_local_structure_recognition
        new_query = prepared._new_query_engine
        def measured_query():
            query = new_query()
            thread = threading.current_thread().name
            lane = {'recognition-candidate': 'candidate', 'recognition-structure': 'structure'}.get(thread)
            self.assertIsNotNone(lane, thread)
            query._test_recognition_lane = query.side_entry_identity._test_recognition_lane = lane
            # Source-purpose substitutions belong to the actual consumed
            # prepared row. Geometry, inventory and every rival stay intact.
            if self.source_changes:
                query.side_entry_identity.entries = tuple(
                    (_freeze(dict(row[0], **self.source_changes)), *row[1:])
                    if (row[0]['mapId'], row[0]['floor']) == (self.target, '1') else row
                    for row in query.side_entry_identity.entries)
            self.queries.append(query)
            return query
        self.stack.enter_context(patch.object(prepared, '_new_query_engine', measured_query))
        scan = self.engine.qt_structure_scan
        def record_scan(*args, **kwargs):
            report = scan(*args, **kwargs)
            self.scan_reports.append(report)
            return report
        self.stack.enter_context(patch.object(self.engine, 'qt_structure_scan', record_scan))
        def probe(work, source):
            item = source[0]
            lane = work.engine._test_recognition_lane
            self.probed.append(item['mapId'])
            self.probed_by_lane.setdefault(lane, []).append(item['mapId'])
            if self.after_probe is not None:
                self.after_probe(item)
            hypotheses = self.lane_hypotheses.get(lane, self.hypotheses)
            if item['mapId'] not in hypotheses:
                return
            work.rows.append(dict(mapId=item['mapId'], floor=item['floor'],
                region=item['region'], sourceProduct=item['sourceProduct'] + ('.foreign' if self.foreign_source else ''),
                profileId=work.engine.profile_for_map(item['mapId']), entryKind='local-structure',
                score=hypotheses[item['mapId']], identityVerified=False,
                hypothesis=dict(chainCount=8, extentGrid=[4.,4.]),
                matrixSourceToClient=np.eye(3).tolist(), sideGates=[]))
        self.stack.enter_context(patch.object(SideEntryEvidence, '_probe', probe))
        recovery = MapObservationWork._recovery
        def record_recovery(work, result):
            self.recovered.append(copy.deepcopy(result[2]))
            return recovery(work, result)
        self.stack.enter_context(patch.object(MapObservationWork, '_recovery', record_recovery))
        self.verify = Mock(side_effect=lambda frame, row, **kwargs: dict(success=True,
            residual=dict(verified=True), matrixSourceToClient=row['matrixSourceToClient']))
        def verify(locator, frame, row, **kwargs):
            lane = getattr(locator, '_test_recognition_lane', 'geometry')
            if self.before_verify is not None:
                self.before_verify(lane, row)
            return self.verify(frame, row, **kwargs)
        self.stack.enter_context(patch.object(MapLocalEntryIdentity, 'verify', verify))
        self.fit = self.stack.enter_context(patch.object(PreparedStructurePoseSolver, 'fit',
            return_value=resources.supported_pose()))
        # The owner deliberately does not join a losing numerical kernel.
        # Tests drain it before removing their scoped measurement substitutes.
        self.addCleanup(self.drain_race)

    def drain_race(self):
        for queue in self.engine.qt_recognition_race._queues.values():
            worker = queue._thread
            if worker is not None and worker is not threading.current_thread():
                worker.join(5)
                self.assertFalse(worker.is_alive(), 'measurement worker did not drain')

    def opening(self):
        self.sequence += 1
        return self.runtime.dispatch('map', dict(openIntent=True, inputSequence=self.sequence))

    def restart(self):
        self.runtime.dispatch('end_round')
        self.drain_race()
        self.assertTrue(self.runtime.dispatch('start_round')['success'])
        self.probed.clear()
        self.probed_by_lane.clear()
        self.recovered.clear()
        self.queries.clear()
        self.scan_reports.clear()

    def query_evidence(self):
        # Unlocked diagnostics remain query-owned. Only a consumed positive
        # entry callback publishes its actual evidence to the live engine.
        rows = [query.last_difficult_evidence for query in self.queries
                if getattr(query, 'last_difficult_evidence', None) is not None]
        self.assertTrue(rows, 'actual query work must produce diagnostics')
        self.assertEqual(len(rows), len(self.queries), 'unfinished/failed query evidence cannot disappear')
        return rows

    def assert_unlocked(self):
        state = self.runtime.snapshot()
        self.assertFalse(state['identityLocked'], dict(mapId=state['mapId'], recovery=self.recovered))
        self.assertEqual(state['mapId'], '')

    def assert_independent_lanes(self):
        queries = {query._test_recognition_lane: query for query in self.queries}
        self.assertEqual(set(queries), {'candidate', 'structure'})
        first, second = queries['candidate'], queries['structure']
        self.assertIsNot(first, second)
        self.assertIsNot(first.entry_identity, second.entry_identity)
        self.assertIsNot(first.side_entry_identity, second.side_entry_identity)
        self.assertIsNot(first.entry_identity._wall_lines, second.entry_identity._wall_lines)
        self.assertIs(first.side_entry_identity.entries, second.side_entry_identity.entries)
        self.assertIsNot(first.side_entry_identity, self.engine.side_entry_identity)
        self.assertIsNot(second.side_entry_identity, self.engine.side_entry_identity)

    def assert_race_winner(self, lane):
        outcome = self.scan_reports[-1]['recognitionRace']
        self.assertEqual(outcome['status'], 'identity-committed')
        self.assertEqual(outcome['winner'], lane)
        loser = 'candidate' if lane == 'structure' else 'structure'
        self.assertTrue(outcome['lanes'][loser]['cancelRequested'])
        self.assertFalse(outcome['lanes'][lane]['cancelRequested'])
        self.assertEqual(set(self.probed_by_lane[lane]), set(IDS))

    def run_first_identity_order(self, winner):
        loser = 'candidate' if winner == 'structure' else 'structure'
        entered, release = threading.Event(), threading.Event()
        def wait_at_measurement(lane, row):
            if lane == loser:
                entered.set()
                self.assertTrue(release.wait(5), 'release losing numerical validation')
            elif lane == winner:
                self.assertTrue(entered.wait(5), 'both real lane queries must reach verification')
        self.before_verify = wait_at_measurement
        try:
            with patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
                result = self.opening()
            self.assertTrue(result['success'], result)
            self.assertTrue(entered.is_set())
            self.assertFalse(release.is_set(), 'the owner must not wait for the losing kernel')
            self.assert_race_winner(winner)
            self.assert_independent_lanes()
            for lane in ('candidate', 'structure'):
                self.assertEqual(set(self.probed_by_lane[lane]), set(IDS))
        finally:
            release.set()
            self.drain_race()
        state = self.runtime.snapshot()
        self.assertTrue(state['identityLocked'])
        self.assertEqual(state['mapId'], self.target)
        self.assertEqual(self.runtime.render_payload(result['renderToken'])['mapId'], self.target)

    def test_candidate_unique_identity_cancels_structure_without_waiting_for_its_kernel(self):
        self.run_first_identity_order('candidate')

    def test_structure_unique_identity_cancels_candidate_without_waiting_for_its_kernel(self):
        self.run_first_identity_order('structure')

    def test_candidate_terminal_without_identity_keeps_structure_work_eligible(self):
        self.lane_hypotheses['candidate'] = {}
        terminal, observed = threading.Event(), []
        def wait_for_actual_candidate_terminal(lane, row):
            if lane == 'structure':
                self.assertTrue(terminal.wait(5), 'candidate must really terminate before direct identity')
        self.before_verify = wait_for_actual_candidate_terminal
        race, run = self.engine.qt_recognition_race, self.engine.qt_recognition_race.run
        def observe_owner_callbacks(*args, **kwargs):
            callback = kwargs['on_terminal']
            def on_terminal(lane, report):
                value = callback(lane, report)
                if lane == 'candidate':
                    self.assertFalse(self.runtime.snapshot()['identityLocked'])
                    self.assertFalse(race._active.stops['structure'].is_set())
                    observed.append(report['status'])
                    terminal.set()
                return value
            return run(*args, **dict(kwargs, on_terminal=on_terminal))
        with patch.object(race, 'run', observe_owner_callbacks), \
             patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
            result = self.opening()
        self.assertTrue(terminal.is_set())
        self.assertTrue(observed)
        self.assertTrue(result['success'], result)
        self.assert_race_winner('structure')
        self.assert_independent_lanes()
        self.assertEqual(set(self.probed_by_lane['candidate']), set(IDS))
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.runtime.snapshot()['mapId'], self.target)

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
        for evidence in self.query_evidence():
            self.assertIsNone(evidence['recognitionPath']['sideWinnerMapId'])

    def test_legacy_rival_stays_in_complete_competition_and_blocks_native_identity(self):
        self.hypotheses[LEGACY] = 8.
        self.opening()
        self.assertEqual(set(self.probed), set(IDS))
        for evidence in self.query_evidence():
            self.assertEqual(evidence['tilemapSide']['competitiveHypotheses'], 2)
        self.assertIsNone(self.recovered[0])
        self.assert_unlocked()

    def test_explicit_source_denial_and_missing_native_purpose_cannot_lock(self):
        for field, value in (('proposalOnly', True), ('authorizesPose', False),
                ('authorizesIdentity', False), ('sourceKind', None)):
            self.source_changes = {field: value}
            with self.subTest(field=field):
                self.opening()
                self.assertEqual(self.recovered[0]['mapId'], self.target)
                self.assert_unlocked()
            self.restart()
        self.source_changes = {}
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
        for evidence in self.query_evidence():
            self.assertFalse(evidence['tilemapSide']['complete'])
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
