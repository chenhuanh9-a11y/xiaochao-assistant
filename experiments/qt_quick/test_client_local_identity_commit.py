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
from difficult_entry_identity import MainEntryEvidence, MapEntryGeometryIdentity
from difficult_side_entry_identity import MapLocalEntryIdentity
from map_observation_work import MapObservationWork
from side_entry_evidence import SideEntryEvidence
from s1_structure_selection import StructureCandidateScanner
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
        self.observations = []
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
        def measured_query(**options):
            query = new_query(**options)
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
            self.observations.append(work)
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
        self.original_fit = PreparedStructurePoseSolver.fit
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

    def supply_main_pose_conflict(self):
        """Supply main numeric measurements from its real decoded source row.

        Main source purpose, all-source jobs, finalization and side adjudication
        remain production code. Only contour/probe and pixel-fit results change.
        """
        points = np.array([[100.+i*20, 100.+(i%3)*30] for i in range(8)])
        self.stack.enter_context(patch.object(MainEntryEvidence, '_observation',
            return_value=(dict(query=np.ones((720, 1280), np.uint8)),
                          [dict(point=p) for p in points], points, np.zeros((8, 2, 2)))))
        def probe(locator, frame, entry, qpoints, qrays):
            item = entry[0]
            if item['mapId'] != self.target:
                return None
            return dict(mapId=item['mapId'], profileId=locator.profile_for_map(item['mapId']),
                entryKind='main', score=12., poseAmbiguous=False,
                sourceProduct=item['sourceProduct'],
                hypothesis=dict(chainCount=12, mainChains=3, neighborChains=9, neighborExtentGrid=[4., 4.]),
                **{key: item[key] for key in ('proposalOnly', 'authorizesPose', 'authorizesIdentity')})
        self.stack.enter_context(patch.object(MapEntryGeometryIdentity, '_probe_entry', probe))
        matrix = np.eye(3)
        matrix[0, 2] = 4.
        self.stack.enter_context(patch.object(MapEntryGeometryIdentity, 'verify',
            return_value=dict(success=True, residual=dict(verified=True), matrixSourceToClient=matrix.tolist())))

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

    def test_structure_winner_reuses_completed_candidate_pose_before_progress_is_consumed(self):
        offered, identity_consumed = threading.Event(), threading.Event()
        scan = StructureCandidateScanner.scan
        run = self.engine.qt_recognition_race.run

        def delayed_progress(scanner, *args, **kwargs):
            progress = kwargs['on_progress']
            def offer(report):
                offered.set()  # The real scanner has completed and verified the pose.
                self.assertTrue(identity_consumed.wait(5), 'structure identity must win first')
                return progress(report)
            return scan(scanner, *args, **dict(kwargs, on_progress=offer))

        def wait_for_pose(lane, row):
            if lane == 'structure':
                self.assertTrue(offered.wait(5), 'candidate must really complete its pose')

        def observe_identity(*args, **kwargs):
            consume = kwargs['on_entry']
            def entry(lane, value, **options):
                try:
                    return consume(lane, value, **options)
                finally:
                    if lane == 'structure':
                        identity_consumed.set()
            return run(*args, **dict(kwargs, on_entry=entry))

        self.before_verify = wait_for_pose
        try:
            with patch.object(StructureCandidateScanner, 'scan', delayed_progress), \
                 patch.object(self.engine.qt_recognition_race, 'run', observe_identity), \
                 patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
                result = self.opening()
            self.assertTrue(result['success'], result)
            self.assert_race_winner('structure')
            self.assertEqual(self.fit.call_count, 1, 'the completed same-frame fit must reach the ticket')
            self.assertEqual(self.scan_reports[-1]['supportedMapIds'], [self.target])
            self.assertEqual(self.runtime.render_payload(result['renderToken'])['mapId'], self.target)
            self.assertTrue(self.runtime.snapshot()['identityLocked'])
        finally:
            identity_consumed.set()
            self.drain_race()

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

    def test_identity_only_conflict_runs_ordinary_canonical_fit_and_known_map_reopen(self):
        from entry_pose_proposals import completed_entry_pose_candidate
        from structure_pose_solver import StructurePoseSolver
        from test_structure_identity_observation import pixel_fixture
        self.supply_main_pose_conflict()
        # Actual pixel extraction remains real. The fixture canvas is not the
        # decoded map: only the fast H and final image-residual measurements
        # below are supplied; prepared scope/canonical provenance stay real.
        self.capture.frame[:720, :1200] = pixel_fixture()[1][:720]
        fits, fixed, residuals = [], [], []
        original_fixed = StructurePoseSolver._fit_fixed_entry_proposal
        def fit(host, *args, **kwargs):
            value = self.original_fit(host, *args, **kwargs)
            fits.append((host, value))
            return value
        def fixed_proposal(host, *args, **kwargs):
            value = original_fixed(host, *args, **kwargs)
            fixed.append(value)
            return value
        def residual(frame, route, matrix, **kwargs):
            residuals.append((frame, np.asarray(matrix).copy(), kwargs.get('shared_reference')))
            return dict(verified=True, reason='supplied-image-residual', withinThreePixels=.95)
        with patch.object(PreparedStructurePoseSolver, 'fit', fit), \
             patch.object(StructurePoseSolver, '_fit_fixed_entry_proposal', fixed_proposal), \
             patch('fast_structure_alignment.try_alignment', side_effect=lambda *a, **kw:
                 dict(success=True, reason='supplied-fast-H', matrixSourceToClient=np.eye(3),
                      diagnostics=dict(queryChains=8))) as fast, \
             patch('nightmare_candidate.selected_route_current_frame_residual', side_effect=residual), \
             patch('client_live_runtime.INITIAL_IDENTITY_BUDGET_SECONDS', 8.):
            result = self.opening()
            self.drain_race()
            self.assertTrue(result['success'], result)
            winners = [row for row in self.recovered if row is not None]
            self.assertTrue(winners)
            self.assertTrue(all(row['identityOnly'] and not row['poseVerified'] for row in winners))
            completed = []
            for observation in self.observations:
                if observation._complete and observation.side_winner is not None:
                    completed.append(observation)
                    self.assertIsNone(completed_entry_pose_candidate(
                        observation, observation.frame, self.target, '1'))
                    main = next(row for row in observation.entry_rows if row['mapId'] == self.target)
                    self.assertTrue(main['proposalOnly'])
                    self.assertFalse(main['authorizesIdentity'])
                    self.assertFalse(main['authorizesPose'])
                    self.assertFalse(main['identityVerified'])
            self.assertTrue(completed, 'the identity must come from completed current source work')
            self.assertTrue(fixed)
            self.assertFalse(any(value and value.get('success') for value in fixed),
                ('identityOnly cannot take the fixed-H shortcut', fixed))
            self.assertGreater(fast.call_count, 0, 'the ordinary canonical fit must execute')
            successful = [value for _, value in fits if value.get('success')]
            self.assertTrue(successful, fits)
            self.assertTrue(all(value['strategy'] == 'shared-fast-alignment' for value in successful))
            self.assertTrue(residuals)
            self.assertTrue(all(reference is not None for _, _, reference in residuals))
            self.assertTrue(all(value['selectedRouteCurrentFrameResidual']['poseVerifiedAgainstCanonicalGeometry']
                                for value in successful))
            state = self.runtime.snapshot()
            self.assertTrue(state['identityLocked'])
            self.assertEqual(state['mapId'], self.target)
            row = self.runtime.render_payload(result['renderToken'])
            self.assertEqual(row['mapId'], self.target)
            self.assertFalse(row['candidateDisplayOnly'])
            self.assertTrue(self.runtime.acknowledge_render_material(row['token'])['success'])
            self.assertIn(self.target, self.runtime._core.pose_by_map)
            self.runtime.dispatch('close_map')
            self.assertTrue(self.runtime.snapshot()['identityLocked'])
            self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
            self.assertFalse(self.runtime.acknowledge_render_material(row['token'])['success'])
            previous_fits = len(fits)
            with patch.object(self.engine, 'qt_structure_scan',
                    side_effect=AssertionError('identityOnly known-map reopen repeated catalogue scan')):
                reopened = self.opening()
            self.assertTrue(reopened['success'], reopened)
            self.assertEqual(self.runtime.render_payload(reopened['renderToken'])['mapId'], self.target)
            self.assertTrue(self.runtime.snapshot()['identityLocked'])
            self.assertGreater(len(fits), previous_fits, 'a new opening still requires a current-frame fit')
            self.runtime.dispatch('close_map')
            with patch('nightmare_candidate.selected_route_current_frame_residual',
                    return_value=dict(verified=False, reason='supplied-current-frame-residual-rejected')):
                rejected = self.opening()
            self.assertFalse(rejected['success'], rejected)
            self.assertTrue(self.runtime.snapshot()['identityLocked'])
            self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
            self.assertIsNone(self.runtime.render_payload(reopened['renderToken']))

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
        self.supply_main_pose_conflict()
        for field, value in (('proposalOnly', True), ('authorizesPose', False),
                ('authorizesIdentity', False), ('sourceKind', None)):
            self.source_changes = {field: value}
            with self.subTest(field=field):
                self.opening()
                self.assertEqual(self.recovered[0]['mapId'], self.target)
                self.assertTrue(self.recovered[0]['identityOnly'])
                self.assertFalse(self.recovered[0]['poseVerified'])
                self.assert_unlocked()
            self.restart()
        self.source_changes = {}
        self.foreign_source = True
        self.opening()
        self.assertEqual(self.recovered[0]['mapId'], self.target)
        self.assertTrue(self.recovered[0]['identityOnly'])
        self.assertFalse(self.recovered[0]['poseVerified'])
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
        commit = self.runtime._commit_identity_rows
        def supersede(*args, **kwargs):
            # Preserve the admitted current callback until this exact boundary.
            # Replacing that callback invalidates native display ownership
            # before the early preview and never reaches the identity writer.
            self.factories.invalidate()
            return commit(*args, **kwargs)
        with patch.object(self.runtime, '_commit_identity_rows', side_effect=supersede) as reducer:
            result = self.opening()
        self.assertEqual(reducer.call_count, 1, dict(result=result, reports=self.scan_reports))
        self.assertFalse(self.engine.qt_resource_context_current())
        self.assertFalse(result['success'], result)
        self.assert_unlocked()
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])


if __name__ == '__main__':
    unittest.main()
