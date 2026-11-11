"""Default imported S0 -> shared scanner/session; synthetic capture/fit only."""
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import LiveRuntime, _AlignmentHost
from client_engine import INITIAL_IDENTITY_BUDGET_SECONDS
from client_providers import ProviderUnavailable, attach_structure_scanner
from s1_structure_selection import StructureCatalog, StructureCandidateScanner
from test_client_live_runtime import FixtureCapture
import test_client_native_base_provider as native_fixture


def supported_pose():
    return dict(success=True, coordinateSpace='shared-structure-to-client',
        matrixSourceToClient=np.eye(3), selectedRouteCurrentFrameResidual=dict(
            verified=True, poseVerifiedAgainstSharedStructure=True, withinThreePixels=.95))


class SharedStructureRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native_fixture.NativeBaseProviderTests.setUpClass()
        cls.addClassCleanup(native_fixture.NativeBaseProviderTests.doClassCleanups)

    def setUp(self):
        self.source = native_fixture.NativeBaseProviderTests('test_default_factory_builds_actual_typed_engine_and_structural_display')
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.source.activate()
        self.engine = self.source.factories.get_engine('nightmare')
        self.capture = FixtureCapture()
        self.runtime = LiveRuntime(engine_factories=self.source.factories,
            capture_provider=self.capture, evidence_kind='synthetic')
        self.addCleanup(self.runtime.shutdown)
        observe = patch.object(self.runtime, '_observe', return_value=(
            dict(detected=True, mapOpen=True), '1', dict(selectedFloor=1, mapOpen=True)))
        observe.start()
        self.addCleanup(observe.stop)
        self.assertTrue(self.runtime.dispatch('start_round')['success'])

    def test_unresolved_default_s0_publishes_shared_structure_proposals_without_identity(self):
        with patch.object(StructureCandidateScanner, 'scan', autospec=True,
                          side_effect=StructureCandidateScanner.scan) as scan, \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
            result = self.runtime.dispatch('capture_now')
        self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(fit.call_count, 1)
        state = self.runtime.snapshot()
        self.assertEqual(state['structureEvidence']['currentSupportedMapIds'], ['fixture-map'])
        self.assertEqual(state['structureEvidence']['possibleMapIds'], ['fixture-map'])
        self.assertEqual(state['structureEvidence']['excludedMapIds'], [])
        self.assertTrue(state['candidateRows'][0]['structureSupported'])
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['groupLocked'])
        self.assertFalse(state['displayAuthorized'])
        self.assertIsNone(self.runtime._render)
        self.assertEqual(self.runtime._identity_source, 's0-structure-candidates')
        self.assertEqual(self.engine.qt_logical_group_registry,
                         self.engine.qt_native_base.reviewed_front_groups)
        # Let the real entry producer finish, then advance only the request's
        # monotonic clock: the fallback must not allocate another full budget.
        rank, monotonic, offset = self.engine.rank, time.monotonic, [0.]
        def spent_entry_budget(*args, **kwargs):
            rows = rank(*args, **kwargs)
            offset[0] += INITIAL_IDENTITY_BUDGET_SECONDS + 1
            return rows
        with patch.object(self.engine, 'rank', side_effect=spent_entry_budget), \
             patch('client_live_runtime.time.monotonic', side_effect=lambda: monotonic() + offset[0]), \
             patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('second scan budget')):
            exhausted = self.runtime.dispatch('capture_now')
        self.assertEqual(exhausted['reason'], 'identity-not-uniquely-verified', exhausted)
        evidence = self.runtime.snapshot()['structureEvidence']
        self.assertEqual(evidence['currentStatus'], 'budget-unresolved')
        self.assertEqual(evidence['currentUnscannedMapIds'], ['fixture-map'])
        self.assertEqual(evidence['currentSupportedMapIds'], [])
        self.assertEqual(evidence['possibleMapIds'], ['fixture-map'])

    def test_late_shared_proposal_after_close_cannot_restore_candidates(self):
        entered, release = threading.Event(), threading.Event()
        def fit(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError('test scan was not released')
            return supported_pose()
        results = []
        with patch.object(_AlignmentHost, 'fit', side_effect=fit):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('capture_now')))
            worker.start()
            try:
                self.assertTrue(entered.wait(10), 'default S0 never entered shared scanner')
                closed = self.runtime.dispatch('close_map')
                self.assertTrue(closed['success'], closed)
            finally:
                release.set()
                worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]['success'], results)
        self.assertTrue(results[0].get('stale'), results)
        state = self.runtime.snapshot()
        self.assertFalse(state['displayAuthorized'])
        self.assertFalse(state['structureEvidence']['currentSupportedMapIds'])
        self.assertFalse(any(row['structureSupported'] for row in state['candidateRows']))

    def capture_choice_payload(self):
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            result = self.runtime.dispatch('capture_now')
        self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
        rows = self.runtime.snapshot()['candidateRows']
        index = next(i for i, row in enumerate(rows) if row['structureSupported'])
        row = rows[index]
        # These are the fields sent by ClientControls.chooseCandidate(). The
        # S0 UI adds a stable map ID when the row carries current structure.
        return dict(index=index, **{key: row[key] for key in (
            'mapId', 'structureFrameId', 'structureGeneration', 'structureRoundId')})

    def test_current_s0_structure_choice_waits_for_explicit_confirmation(self):
        payload = self.capture_choice_payload()
        result = self.runtime.dispatch('select_candidate', dict(payload, index=-1))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['reason'], 'structure-candidate-selected-awaiting-confirmation')
        state = self.runtime.snapshot()
        self.assertEqual(state['selectedCandidate']['mapId'], 'fixture-map')
        self.assertTrue(self.runtime._selection_pending)
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['groupLocked'])
        self.assertFalse(state['displayAuthorized'])
        self.assertIsNone(self.runtime._render)

    def test_s0_structure_choice_rejects_changed_frame_scope_and_base(self):
        old = self.capture_choice_payload()
        current = self.capture_choice_payload()
        self.assertFalse(self.runtime.dispatch('select_candidate', old)['success'])
        for field in ('structureFrameId', 'structureGeneration', 'structureRoundId', 'mapId'):
            with self.subTest(field=field):
                rejected = self.runtime.dispatch('select_candidate', dict(current, **{field: 'foreign'}))
                self.assertFalse(rejected['success'], rejected)
        # Same bytes/ref but a new actual catalog publication supersedes the
        # engine's source generation; a current-looking UI row cannot revive it.
        self.source.activate()
        rejected = self.runtime.dispatch('select_candidate', current)
        self.assertFalse(rejected['success'], rejected)
        self.assertTrue(rejected.get('stale'), rejected)
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_catalog_uses_imported_base_identity_and_real_s0_floors(self):
        cat = StructureCatalog.from_engine(self.engine)
        ref = self.engine.qt_native_base.component_ref
        self.assertEqual(cat.season, 's0')
        self.assertEqual(cat.map_ids, ('fixture-map',))
        self.assertEqual(cat.floors, tuple(self.engine.pack.floors))
        self.assertIn('B1', cat.floors)
        self.assertIn(ref.manifest_sha256, cat.resource_key)
        self.source.activate()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            StructureCatalog.from_engine(self.engine)

    def test_actual_shared_solver_keeps_black_frame_unknown(self):
        result = self.runtime.dispatch('capture_now')
        self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
        evidence = self.runtime.snapshot()['structureEvidence']
        self.assertEqual(evidence['currentStatus'], 'complete')
        self.assertEqual(evidence['currentSupportedMapIds'], [])
        self.assertEqual(evidence['possibleMapIds'], ['fixture-map'])
        self.assertEqual(evidence['excludedMapIds'], [])
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_attaching_scanner_does_not_bypass_rank_qualification(self):
        attach_structure_scanner(self.engine, StructureCatalog.from_engine(self.engine))
        self.assertFalse(hasattr(self.engine, 'qt_s1_structure_scan'))
        self.engine.qt_rank_ready = False
        self.engine.qt_rank_gaps = ('native-base-rank-unavailable',)
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('unauthorized fit')):
            with self.assertRaisesRegex(ProviderUnavailable, 'native-base-rank-unavailable'):
                self.engine.qt_structure_scan(self.capture.frame, floor='1')


class SharedScannerScopeTests(unittest.TestCase):
    def test_s0_modes_keep_actual_floors_and_unknown_unscanned_competitors(self):
        for mode, floors in (('nightmare', ('B1', '1', '2')), ('difficult', ('1', '2'))):
            cat = StructureCatalog('s0', mode, 'base-fixture', ('a', 'b'), floors)
            with self.subTest(mode=mode):
                calls = []
                def fit(*args, **kwargs):
                    calls.append(args[1])
                    return supported_pose()
                empty = StructureCandidateScanner(cat, fit).scan(np.zeros((8, 8, 3), np.uint8),
                    floor=floors[0], floor_source='current-frame-verified', budget_seconds=0)
                self.assertEqual(calls, [])
                self.assertEqual(empty['status'], 'budget-unresolved')
                self.assertEqual(empty['unscannedMapIds'], ['a', 'b'])
                self.assertEqual(empty['unknownMapIds'], ['a', 'b'])
                self.assertEqual(empty['excludedMapIds'], [])
                self.assertFalse(empty['liveAuthorization'])
                self.assertFalse(empty['automaticSelectionAuthorized'])

if __name__ == '__main__':
    unittest.main()
