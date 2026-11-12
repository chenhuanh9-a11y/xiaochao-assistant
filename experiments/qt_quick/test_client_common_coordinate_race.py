"""Production race/work/pool/material composition with controlled measurements.

Capture/floor, display pixels, common-coordinate measurement and fixed/candidate
numerical fits are fixtures. The scanner, two queues, Work, owner reduction,
round/core authorization and final material consumers remain production. Real
package/observer execution is validated separately, not inferred from these tests.
"""
from copy import deepcopy
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from client_providers import attach_structure_scanner
from s1_candidate_purpose import _NATIVE_S1_PRODUCTION_CANDIDATE
from structure_pose_solver import StructurePoseSolver
import test_s1_native_candidate_preview as fixture


class CommonCoordinateRaceRuntimeTests(unittest.TestCase):
    mode = 'nightmare'

    def setUp(self):
        self.fx = fixture.NativeCandidateContracts('runTest')
        self.fx.candidate_purpose = _NATIVE_S1_PRODUCTION_CANDIDATE
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.runtime, self.engine, self.catalog = self.fx.runtime, self.fx.registry.engine, self.fx.registry.catalog
        if self.mode != 'nightmare':
            from dataclasses import replace
            self.engine.pack.mode = self.mode
            self.catalog = self.fx.registry.catalog = replace(self.catalog, mode=self.mode)
            self.fx.start_patch(patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.catalog))
            self.runtime.dispatch('set_context', dict(season='s1', mode=self.mode))
        self.source_current = True
        self.prepare_calls, self.fixed_calls, self.scan_reports, self.events = [], [], [], []
        self.candidate_ids = []
        self.candidate_entered, self.coordinate_entered = threading.Event(), threading.Event()
        self.release_candidate, self.release_coordinate, self.material_ready = (
            threading.Event(), threading.Event(), threading.Event())
        self.block_candidate = self.block_coordinate = self.wait_material = self.wait_candidate = False
        self.wait_coordinate = False
        self.fixed_entered = threading.Event()
        self.verified, self.bad, self.fixed_success = True, ('a',), True
        self.addCleanup(self.release_candidate.set)
        self.addCleanup(self.release_coordinate.set)
        self.prepared = SimpleNamespace(catalog=self.catalog, current=lambda:self.source_current,
            observer=SimpleNamespace(engine=self.engine), prepare=self.prepare, fixed_fit=self.fixed)
        self.engine.qt_s1_coordinate_recognition = self.prepared
        self.engine.qt_s1_private_coordinate_prepare = self.prepare
        self.engine.qt_s1_pose_prior = None
        attach_structure_scanner(self.engine, self.catalog, private_fit=self.fixed)
        original = self.engine.qt_structure_scan
        def scan(*args, **kwargs):
            value = original(*args, **kwargs)
            self.scan_reports.append(value)
            return value
        self.engine.qt_structure_scan = self.engine.qt_s1_structure_scan = scan
        self.fx.start_patch(patch.object(StructurePoseSolver, 'fit', self.numeric))
        def event(row):
            self.events.append(row)
            if row.get('event') == 'render-ready':
                self.material_ready.set()
        self.runtime.set_event_callback(event)
        self.assertTrue(self.runtime.dispatch('start_round')['success'])

    def prepare(self, frame, ids, floor, *, cancelled, stage=None):
        self.prepare_calls.append((frame, tuple(ids), floor))
        self.coordinate_entered.set()
        if self.wait_candidate:
            self.assertTrue(self.candidate_entered.wait(3))
        if self.wait_material:
            self.assertTrue(self.material_ready.wait(3))
        if self.block_coordinate:
            self.assertTrue(self.release_coordinate.wait(4))
        return dict(mode=self.mode, resourceKey=self.catalog.resource_key, floor=str(floor),
            coordinateVerified=self.verified, evaluatedMapIds=list(ids),
            contradictedMapIds=[mid for mid in self.bad if mid in ids] if self.verified else [],
            reason='controlled-coordinate-measurement' if self.verified else 'current-common-coordinate-unresolved')

    def fixed(self, frame, mid, floor, cancelled, *, report, observation_bounds=None):
        self.fixed_calls.append((frame, mid, floor))
        self.fixed_entered.set()
        return deepcopy(self.fx.fit) if self.fixed_success else dict(success=False, reason='canonical-current-frame-residual-unverified')

    def numeric(self, frame, mid, floor, cancelled, *args, **kwargs):
        self.candidate_ids.append(mid)
        self.candidate_entered.set()
        if self.wait_coordinate:
            self.assertTrue(self.coordinate_entered.wait(3))
        if self.block_candidate:
            self.assertTrue(self.release_candidate.wait(4))
        return deepcopy(self.fx.fit)

    def drain(self):
        limit = time.monotonic()+4
        while self.runtime.private_evidence_busy and time.monotonic() < limit:
            time.sleep(.001)
        self.assertFalse(self.runtime.private_evidence_busy)

    def capture(self):
        return self.runtime.dispatch('capture_now')

    def assert_locked_b(self, result):
        self.assertTrue(result['success'], result)
        row = self.runtime.render_payload(result['renderToken'])
        self.assertEqual(row['mapId'], 'b')
        self.assertTrue(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.runtime.snapshot()['durableMapId'], 'b')
        self.assertEqual(len([row for row in self.events if row['event']=='private-experimental-locked']), 1)
        json.dumps(self.scan_reports[-1], default=str)  # No raw lane result cycle or private Work.

    def test_structure_wins_and_reuses_completed_fixed_fit_without_candidate_preparation(self):
        self.block_candidate = self.wait_candidate = True
        try:
            with patch('client_live_runtime._prepare_alignment_host', side_effect=AssertionError('duplicate-input-preparation')), \
                 patch('client_live_runtime._fit_alignment_host', side_effect=AssertionError('duplicate-numerical-fit')):
                result = self.capture()
            self.assertFalse(self.release_candidate.is_set())
            self.assert_locked_b(result)
            self.assertEqual(self.scan_reports[-1]['recognitionRace']['winner'], 'structure')
        finally:
            self.release_candidate.set()
            self.drain()
        self.assertEqual(len(self.prepare_calls), 1)
        self.assertEqual([row[1] for row in self.fixed_calls], ['b'])

    def test_candidate_preview_is_replaced_only_by_completed_structure_material(self):
        self.wait_material = True
        result = self.capture()
        self.drain()
        self.assert_locked_b(result)
        ready = [row for row in self.events if row['event']=='render-ready']
        self.assertEqual(len(ready), 2)
        self.assertIsNone(self.runtime.render_payload(ready[0]['token']))
        self.assertEqual(self.scan_reports[-1]['recognitionRace']['winner'], 'structure')

    def test_unresolved_structure_keeps_candidate_preview_and_public_report_serializable(self):
        self.verified, self.bad = False, ()
        result = self.capture()
        self.drain()
        self.assertTrue(result['success'], result)
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        self.assertEqual(self.runtime.render_payload(result['renderToken'])['mapId'], 'a')
        self.assertEqual(self.fixed_calls, [])
        self.assertEqual(len(self.prepare_calls), 1)
        json.dumps(self.scan_reports[-1], default=str)

    def close_coordinate_to_history(self):
        self.block_coordinate = True
        results = []
        worker = threading.Thread(target=lambda:results.append(self.capture()))
        worker.start()
        try:
            self.assertTrue(self.coordinate_entered.wait(3))
            self.runtime.dispatch('close_map')
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(results[0]['success'])
        finally:
            self.release_coordinate.set()
            self.drain()
            worker.join(3)
        pool = self.runtime._s1_private_pool
        self.assertEqual(pool.remaining, ('b',))
        self.assertEqual(pool.observation_count, 1)
        self.assertFalse(pool.locked_id)
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.assertEqual(len(self.prepare_calls), 1)
        self.assertEqual(self.fixed_calls, [])

    def test_close_transfers_completed_report_once_to_history_without_refit(self):
        self.close_coordinate_to_history()

    def test_candidate_unique_current_material_does_not_wait_for_common_kernel(self):
        self.close_coordinate_to_history()
        self.release_coordinate.clear()
        self.coordinate_entered.clear()
        self.wait_coordinate = True
        try:
            result = self.capture()
            self.assertFalse(self.release_coordinate.is_set())
            self.assert_locked_b(result)
            self.assertEqual(self.scan_reports[-1]['recognitionRace']['winner'], 'candidate')
        finally:
            self.release_coordinate.set()
            self.drain()
        self.assertEqual(self.fixed_calls, [])

    def test_unique_report_with_failed_fixed_residual_cannot_lock_or_publish(self):
        self.fixed_success = False
        self.block_candidate = self.wait_candidate = True
        results = []
        worker = threading.Thread(target=lambda:results.append(self.capture()))
        worker.start()
        try:
            self.assertTrue(self.fixed_entered.wait(3))
            self.assertFalse(self.runtime.snapshot()['identityLocked'])
            self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
            self.runtime.dispatch('close_map')
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertFalse(results[0]['success'])
        finally:
            self.release_candidate.set()
            self.drain()
            worker.join(3)
        self.assertFalse(self.runtime._s1_private_pool.locked_id)
        self.assertEqual([row[1] for row in self.fixed_calls], ['b'])

    def test_common_material_io_error_leaves_candidate_lane_running_to_valid_lock(self):
        self.block_candidate = self.wait_candidate = True
        original_solve, original_display = self.runtime._solve_observation, self.runtime._display_layer
        common, injected = [False], []
        def solve(*args, **kwargs):
            common[0] = kwargs.get('coordinate_observation') is not None
            try:
                return original_solve(*args, **kwargs)
            finally:
                common[0] = False
        def display(*args, **kwargs):
            if common[0] and not injected:
                injected.append(args[1])
                self.release_candidate.set()
                raise OSError('one-common-material-io-failure')
            return original_display(*args, **kwargs)
        try:
            with patch.object(self.runtime, '_solve_observation', solve), \
                 patch.object(self.runtime, '_display_layer', display):
                result = self.capture()
                self.drain()
        finally:
            self.release_candidate.set()
            self.drain()
        self.assertEqual(injected, ['b'])
        self.assert_locked_b(result)
        self.assertIn('b', self.candidate_ids)
        self.assertEqual(self.scan_reports[-1]['recognitionRace']['winner'], 'candidate')

    def test_locked_reopen_skips_identity_work_and_still_fits_current_frame(self):
        result = self.capture()
        self.drain()
        self.assert_locked_b(result)
        self.runtime.dispatch('close_map')
        self.fx.floor = '-2'
        before = len(self.prepare_calls)
        with patch.object(self.prepared, 'prepare', side_effect=AssertionError('identity-work-after-lock')):
            reopened = self.capture()
        self.drain()
        self.assertTrue(reopened['success'], reopened)
        row = self.runtime.render_payload(reopened['renderToken'])
        self.assertEqual((row['mapId'],row['floor']), ('b','-2'))
        self.assertNotEqual(row['token'], result['renderToken'])
        self.assertEqual(len(self.prepare_calls), before)


class DifficultCommonCoordinateRaceRuntimeTests(CommonCoordinateRaceRuntimeTests):
    mode = 'difficult'


if __name__ == '__main__':
    unittest.main()
