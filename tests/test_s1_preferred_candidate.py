"""Synthetic runtime/material ownership, not native or visible-pixel evidence."""
import copy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

import numpy as np

import test_s1_native_candidate_preview as native
from s1_structure_selection import StructureCandidateScanner, StructureEvidenceSession


class PreferredCandidateContracts(unittest.TestCase):
    setUp = native.NativeCandidateContracts.setUp
    start_patch = native.NativeCandidateContracts.start_patch
    show = native.NativeCandidateContracts.show
    current_payload = native.NativeCandidateContracts.current_payload

    def instrument(self, *, acknowledge=True, fit=None):
        self.calls, self.events = [], []
        def run(frame, mid, floor, cancelled, **kwargs):
            self.calls.append((mid, floor, int(frame[0, 0, 0])))
            return fit(frame, mid, floor, cancelled) if fit else copy.deepcopy(self.fit)
        self.registry.engine.qt_s1_structure_scan = lambda frame, **kwargs: StructureCandidateScanner(
            self.registry.catalog, run).scan(frame, **kwargs)
        def event(row):
            self.events.append(row)
            if acknowledge and row['event'] == 'render-ready':
                self.assertTrue(self.runtime.acknowledge_render_material(row['token'])['success'])
        self.runtime.set_event_callback(event)

    def choose_b(self):
        self.instrument()
        self.show()
        result = self.runtime.dispatch('next_candidate')
        self.assertTrue(result['success'], result)
        self.assertEqual(self.current_payload()['mapId'], 'b')
        self.calls.clear()

    def reopen(self):
        self.runtime.dispatch('close_map')
        self.capture.frame[0, 0, 0] += 1
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        return result

    def test_ack_only_saves_id_and_never_enqueues_learning(self):
        self.instrument(acknowledge=False)
        first = self.show()
        self.assertIsNone(self.runtime._s1_candidate_preference)
        ack = self.runtime.acknowledge_render_material(first['token'])
        self.assertTrue(ack['success'], ack)
        self.assertFalse(ack['needsWorker'])
        self.assertEqual(self.runtime._s1_candidate_preference[1], 'a')
        self.assertFalse(any(e['event'] == 'render-material-accepted' for e in self.events))
        self.assertTrue(any(e['event'] == 'candidate-material-accepted' for e in self.events))
        self.assertFalse(self.runtime.dispatch('remember_render_material', dict(renderToken=first['token']))['success'])
        self.assertFalse(self.runtime._steady_material_ready)

    def test_unacknowledged_first_render_does_not_seed_preference(self):
        self.instrument(acknowledge=False)
        self.show(); self.calls.clear()
        result = self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.assertFalse(result['scanSkipped'])

    def test_same_generation_stale_capture_rejects_ack_without_learning(self):
        self.instrument(acknowledge=False)
        current = self.show()
        generation = self.capture.generation
        with patch.object(self.capture, 'is_current', return_value=False):
            rejected = self.runtime.acknowledge_render_material(current['token'])
        self.assertEqual(self.capture.generation, generation)
        self.assertFalse(rejected['success'], rejected)
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.assertFalse(self.runtime._remember_proposals[current['token']]['accepted'])
        # With the actual current-context guard restored the same valid receipt
        # still succeeds; rejecting an ACK did not itself revoke this owner.
        accepted = self.runtime.acknowledge_render_material(current['token'])
        self.assertTrue(accepted['success'], accepted)
        self.assertEqual(self.runtime._s1_candidate_preference[1], 'a')

    def test_rejected_ack_does_not_unconditionally_clear_existing_owner(self):
        self.choose_b()
        self.instrument(acknowledge=False)
        self.reopen()
        current = self.current_payload()
        preference = self.runtime._s1_candidate_preference
        with patch.object(self.capture, 'is_current', return_value=False):
            rejected = self.runtime.acknowledge_render_material(current['token'])
        self.assertFalse(rejected['success'])
        self.assertIs(self.runtime._s1_candidate_preference, preference)
        self.assertEqual(self.current_payload()['token'], current['token'])

    def test_switch_preference_fresh_reopen_skips_catalogue_without_identity(self):
        self.choose_b()
        old = self.current_payload()
        result = self.reopen()
        self.assertEqual(self.calls, [('b', '-1', 1)])
        self.assertEqual(self.current_payload()['mapId'], 'b')
        self.assertNotEqual(old['frameId'], self.current_payload()['frameId'])
        self.assertEqual(result['structureScanStatus'], 'preferred-material-committed')
        self.assertFalse(result['structureScanComplete'])
        self.assertTrue(result['scanSkipped'])
        state = self.runtime.snapshot()
        self.assertEqual(state['structureEvidence']['currentUnscannedMapIds'], ['a'])
        self.assertEqual(state['structureEvidence']['possibleMapIds'], ['a', 'b'])
        self.assertFalse(state['structureEvidence']['excludedMapIds'])
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['structureEvidence']['uniqueIdentityVerified'])
        self.assertEqual(self.runtime._durable_map_id, '')
        self.assertIsNone(self.runtime._gate.confirmed_id)

    def test_new_floor_and_frame_use_new_fit_not_old_pose(self):
        self.choose_b()
        self.floor = '-2'
        def fit(frame, mid, floor, cancelled):
            self.assertEqual(floor, '-2')
            result = copy.deepcopy(self.fit)
            result['matrixSourceToClient'][0, 2] = 7
            return result
        self.instrument(fit=fit)
        self.reopen()
        self.assertEqual(self.calls, [('b', '-2', 1)])
        self.assertEqual(self.current_payload()['floor'], '-2')
        self.assertEqual(self.current_payload()['matrix'][0][2], 7)

    def test_failed_preferred_fit_keeps_unknown_and_scans_all(self):
        self.choose_b()
        self.instrument(fit=lambda frame, mid, *args: dict(success=False, reason='no-fit')
                        if mid == 'b' else copy.deepcopy(self.fit))
        result = self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['b', 'a'])
        self.assertEqual(self.current_payload()['mapId'], 'a')
        self.assertFalse(result['scanSkipped'])
        self.assertTrue(result['structureScanComplete'])
        current = self.runtime._s1_structure_session._current
        self.assertEqual(current['unknownMapIds'], ['b'])
        self.assertEqual(current['excludedMapIds'], [])

    def test_exception_in_preferred_fit_falls_back(self):
        self.choose_b()
        def fit(frame, mid, *args):
            if mid == 'b':
                raise ValueError('fixture fit exception')
            return copy.deepcopy(self.fit)
        self.instrument(fit=fit)
        self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['b', 'a'])
        self.assertEqual(self.current_payload()['mapId'], 'a')

    def test_preferred_material_failure_scans_other_candidates(self):
        self.choose_b()
        def material(engine, mid, *args, **kwargs):
            if mid == 'b':
                raise ValueError('fixture material unavailable')
            return self.display
        with patch.object(self.runtime, '_display_layer', side_effect=material):
            result = self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['b', 'a'])
        self.assertEqual(self.current_payload()['mapId'], 'a')
        self.assertFalse(result['scanSkipped'])

    def test_explicit_reselect_clears_hint_and_revokes_old_ack(self):
        self.choose_b()
        token = self.current_payload()['token']
        self.assertTrue(self.runtime.dispatch('reselect_map')['success'])
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.assertFalse(self.runtime.acknowledge_render_material(token)['success'])
        result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.assertFalse(result['scanSkipped'])

    def test_switch_after_shortcut_captures_and_checks_other_ids_again(self):
        self.choose_b(); self.reopen(); self.calls.clear()
        frame_id, count = self.current_payload()['frameId'], self.capture.count
        result = self.runtime.dispatch('next_candidate')
        self.assertTrue(result['success'], result)
        self.assertGreater(self.capture.count, count)
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.assertEqual(self.current_payload()['mapId'], 'a')
        self.assertNotEqual(self.current_payload()['frameId'], frame_id)
        self.assertEqual(self.runtime._s1_candidate_preference[1], 'a')

    def test_explicit_force_catalogue_capture_never_shortcuts(self):
        self.choose_b()
        result = self.runtime.dispatch('capture_now', dict(forceCatalogue=True))
        self.assertTrue(result['success'], result)
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.assertFalse(result['scanSkipped'])

    def test_switch_capture_handoff_keeps_original_close_end_and_context_owner(self):
        for boundary in ('close_map', 'end_round', 'end_then_context'):
            with self.subTest(boundary=boundary):
                fx = PreferredCandidateContracts(); fx.setUp()
                try:
                    fx.choose_b(); fx.reopen(); fx.calls.clear()
                    runtime = fx.runtime
                    original = runtime._capture_and_solve
                    entered, release = threading.Event(), threading.Event()
                    results = []
                    before_count = fx.capture.count
                    def delayed(*args, **kwargs):
                        entered.set(); self.assertTrue(release.wait(3))
                        return original(*args, **kwargs)
                    with patch.object(runtime, '_capture_and_solve', side_effect=delayed):
                        worker = threading.Thread(target=lambda: results.append(runtime.dispatch('next_candidate')))
                        worker.start()
                        try:
                            self.assertTrue(entered.wait(3))
                            if boundary == 'end_then_context':
                                runtime.dispatch('end_round')
                                runtime.dispatch('set_context', dict(season='s1', mode='difficult'))
                            else:
                                runtime.dispatch(boundary)
                        finally:
                            release.set(); worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(results[0]['success'], results)
                    self.assertTrue(results[0]['stale'])
                    self.assertEqual(fx.capture.count, before_count)
                    self.assertFalse(fx.calls)
                    self.assertIsNone(fx.current_payload())
                    if boundary != 'close_map':
                        self.assertFalse(runtime._active)
                finally:
                    fx.doCleanups()

    def test_switch_rescan_marks_busy_and_cleans_only_its_capture_owner(self):
        self.choose_b(); self.reopen()
        original = self.runtime._capture_and_solve
        def inspect(*args, **kwargs):
            state = self.runtime.snapshot()
            self.assertTrue(state['captureInProgress'])
            self.assertTrue(state['structureScanInProgress'])
            self.assertNotIn('next_candidate', state['availableCommands'])
            self.assertIsNotNone(self.runtime._capture_pending_owner)
            return original(*args, **kwargs)
        with patch.object(self.runtime, '_capture_and_solve', side_effect=inspect):
            result = self.runtime.dispatch('next_candidate')
        self.assertTrue(result['success'], result)
        self.assertFalse(self.runtime.snapshot()['captureInProgress'])
        self.assertIsNone(self.runtime._capture_pending_owner)

    def test_switch_waiting_for_work_cannot_consume_successor_frame(self):
        for close_first in (False, True):
            with self.subTest(close_first=close_first):
                fx = PreferredCandidateContracts(); fx.setUp()
                try:
                    fx.choose_b(); fx.reopen()
                    runtime = fx.runtime
                    work, main = runtime._work, threading.get_ident()
                    waiting, results = threading.Event(), []
                    class ObservedWork:
                        def __enter__(self):
                            if threading.get_ident() != main:
                                waiting.set()
                            work.acquire()
                            return self
                        def __exit__(self, *exc):
                            work.release()
                    runtime._work = ObservedWork()
                    with work:
                        worker = threading.Thread(target=lambda: results.append(runtime.dispatch('next_candidate')))
                        worker.start()
                        self.assertTrue(waiting.wait(3))
                        if close_first:
                            runtime.dispatch('close_map')
                        self.assertTrue(runtime.dispatch('capture_now')['success'])
                        token = fx.current_payload()['token']
                        hint, count = runtime._s1_candidate_preference, fx.capture.count
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(results[0]['success'], results)
                    self.assertTrue(results[0]['stale'])
                    self.assertEqual(fx.current_payload()['token'], token)
                    self.assertEqual(runtime._s1_candidate_preference, hint)
                    self.assertEqual(fx.capture.count, count)
                finally:
                    fx.doCleanups()

    def test_candidate_arrival_before_close_cannot_adopt_new_birth(self):
        for action in ('next_candidate', 'previous_candidate', 'select_candidate'):
            with self.subTest(action=action):
                fx = PreferredCandidateContracts(); fx.setUp()
                try:
                    fx.choose_b(); fx.reopen()
                    runtime = fx.runtime
                    serials, main = runtime._dispatch_serials, threading.get_ident()
                    arrived, results = threading.Event(), []
                    class ObservedSerials:
                        def __next__(self):
                            value = next(serials)
                            if threading.get_ident() != main:
                                arrived.set()
                            return value
                    runtime._dispatch_serials = ObservedSerials()
                    state = runtime.snapshot()
                    payload = dict(mapId='b', structureFrameId=state['frameId'],
                        structureGeneration=state['generation'], structureRoundId=state['roundId'])
                    with runtime._lock:
                        worker = threading.Thread(target=lambda: results.append(runtime.dispatch(action, payload)))
                        worker.start()
                        self.assertTrue(arrived.wait(3))
                        runtime.dispatch('close_map')
                        self.assertTrue(runtime.dispatch('capture_now')['success'])
                        token = fx.current_payload()['token']
                        hint, count = runtime._s1_candidate_preference, fx.capture.count
                    worker.join(3)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(results[0]['success'], results)
                    self.assertTrue(results[0]['stale'])
                    self.assertEqual(fx.current_payload()['token'], token)
                    self.assertEqual(runtime._s1_candidate_preference, hint)
                    self.assertEqual(fx.capture.count, count)
                finally:
                    fx.doCleanups()

    def test_old_s1_arrival_stays_fenced_after_season_change(self):
        self.choose_b(); self.reopen()
        runtime = self.runtime
        serials, main = runtime._dispatch_serials, threading.get_ident()
        arrived, results = threading.Event(), []
        class ObservedSerials:
            def __next__(self):
                value = next(serials)
                if threading.get_ident() != main:
                    arrived.set()
                return value
        runtime._dispatch_serials = ObservedSerials()
        with runtime._lock:
            worker = threading.Thread(target=lambda: results.append(runtime.dispatch('next_candidate')))
            worker.start()
            self.assertTrue(arrived.wait(3))
            self.assertTrue(runtime.dispatch('end_round')['success'])
            # This fixture normally supplies only S1's three-mode catalogue.
            # Keep its synthetic capability list valid for the S0 boundary.
            capabilities = self.registry.capabilities
            with patch.object(self.registry, 'capabilities', side_effect=lambda season='s0': {
                    mode: row for mode, row in capabilities(season).items()
                    if season != 's0' or mode != 'difficulty2'}):
                self.assertTrue(runtime.dispatch('set_context', dict(season='s0', mode='nightmare'))['success'])
            count, epoch = self.capture.count, runtime._epoch
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0]['success'], results)
        self.assertTrue(results[0]['stale'])
        self.assertEqual(runtime._season, 's0')
        self.assertEqual(runtime._epoch, epoch)
        self.assertEqual(self.capture.count, count)
        self.assertFalse(runtime._active)

    def test_settings_and_capture_generation_drop_hint(self):
        self.choose_b()
        self.runtime.configure_settings(dict(mapSelectionMode='auto', fixtureSetting=True))
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.runtime.dispatch('next_candidate'); self.calls.clear()
        self.capture.generation += 1
        self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])

    def test_resource_key_change_drops_preference(self):
        self.choose_b()
        self.registry.catalog = replace(self.registry.catalog, resource_key='new-resource')
        with patch('s1_structure_selection.StructureCatalog.from_engine', return_value=self.registry.catalog):
            self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])

    def test_engine_replacement_drops_preference_even_with_same_catalogue(self):
        self.choose_b()
        replacement = copy.copy(self.registry.engine)
        self.registry.engine = self.runtime._engine = replacement
        self.reopen()
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])

    def test_reentrant_new_open_cannot_be_withdrawn_by_old_preferred_finish(self):
        self.choose_b()
        original = self.runtime._event_callback
        nested, entered = [], []
        def event(row):
            if row['event'] == 'render-ready' and not entered:
                entered.append(True)
                self.runtime.dispatch('close_map')
                nested.append(self.runtime.dispatch('map', dict(openIntent=True)))
            else:
                original(row)
        self.runtime.set_event_callback(event)
        self.runtime.dispatch('close_map')
        old = self.runtime.dispatch('map', dict(openIntent=True))
        self.assertFalse(old['success'])
        self.assertTrue(nested[0]['success'], nested)
        self.assertEqual(self.current_payload()['token'], nested[0]['renderToken'])
        self.assertEqual(self.runtime._s1_candidate_preference[1], 'b')
        self.assertFalse(self.runtime.snapshot()['identityLocked'])

    def test_unknown_floor_revokes_current_but_hint_requires_new_fit(self):
        self.choose_b()
        self.floor = None
        self.assertFalse(self.runtime.dispatch('capture_now')['success'])
        self.assertIsNone(self.current_payload())
        self.assertFalse(self.calls)
        self.floor = '-2'
        self.reopen()
        self.assertEqual(self.calls, [('b', '-2', 1)])

    def test_diagnostic_fields_and_codes_are_explicit(self):
        from diagnostic_schema import FIELDS, CODES
        self.assertTrue({'candidateReuse', 'scanSkipped'} <= FIELDS)
        self.assertTrue({'candidate-material-accepted', 'preferred-material-committed', 'preferred-current-frame'} <= CODES)

    def test_end_and_cancel_drop_hint_and_old_results(self):
        for action in ('end_round', 'cancel'):
            with self.subTest(action=action):
                self.choose_b()
                entered, release = threading.Event(), threading.Event()
                def fit(*args):
                    entered.set(); self.assertTrue(release.wait(3)); return copy.deepcopy(self.fit)
                self.instrument(fit=fit)
                self.runtime.dispatch('close_map')
                results = []
                worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('capture_now')))
                worker.start()
                try:
                    self.assertTrue(entered.wait(3))
                    self.runtime.dispatch(action)
                finally:
                    release.set(); worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(results[0]['success'])
                self.assertIsNone(self.current_payload())
                self.assertIsNone(self.runtime._s1_candidate_preference)

    def test_old_ack_cannot_replace_new_choice(self):
        self.instrument(acknowledge=False)
        old = self.show()
        self.runtime.dispatch('next_candidate')
        newer = self.current_payload()
        self.assertTrue(self.runtime.acknowledge_render_material(newer['token'])['success'])
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])
        self.assertEqual(self.runtime._s1_candidate_preference[1], 'b')

    def test_payload_cannot_mint_hint_or_identity(self):
        self.instrument(acknowledge=False)
        self.runtime.dispatch('start_round')
        result = self.runtime.dispatch('capture_now', dict(preferred_map_id='b', preferredMapId='b',
            candidateReuse='preferred-current-frame', scanSkipped=True, identityLocked=True))
        self.assertTrue(result['success'], result)
        self.assertEqual([x[0] for x in self.calls], ['a', 'b'])
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.assertFalse(self.runtime.snapshot()['identityLocked'])


class PreferredScannerContracts(unittest.TestCase):
    def test_callback_failure_cannot_hide_other_candidates(self):
        from test_s1_structure_selection import catalog, pose
        calls = []
        def broken(mid):
            raise ValueError('fixture material exception')
        def fit(frame, mid, *args, **kwargs):
            calls.append(mid)
            return pose()
        report = StructureCandidateScanner(catalog(), fit).scan(np.zeros((8, 8, 3), np.uint8),
            floor='-1', floor_source='current-frame-verified', preferred_map_id='b',
            preferred_material_committed=broken)
        self.assertEqual(calls, ['b', 'a', 'c'])
        self.assertTrue(report['scanComplete'])
        self.assertFalse(report['scanSkipped'])

    def test_terminal_schema_keeps_alternatives_and_rejects_false_complete(self):
        from test_s1_structure_selection import catalog, pose
        cat = catalog()
        updates = []
        report = StructureCandidateScanner(cat, lambda *args, **kwargs: pose()).scan(
            np.zeros((8, 8, 3), np.uint8), floor='-1', floor_source='current-frame-verified',
            preferred_map_id='b', preferred_material_committed=lambda mid: True, on_progress=updates.append)
        self.assertEqual(report['checkedMapIds'], ['b'])
        self.assertEqual(report['unscannedMapIds'], ['a', 'c'])
        self.assertEqual(report['unknownMapIds'], ['a', 'c'])
        session = StructureEvidenceSession(cat, 'round-1')
        ticket = session.begin_observation('-1')
        self.assertTrue(session.commit(updates[0], ticket))
        forged = copy.deepcopy(report); forged['scanComplete'] = True
        self.assertFalse(session.commit(forged, ticket))
        self.assertTrue(session.commit(report, ticket))
        self.assertFalse(session.snapshot()['currentScanComplete'])
        self.assertFalse(session.commit(updates[0], ticket))
        self.assertFalse(session.snapshot()['uniqueIdentityVerified'])

    def test_cancel_after_material_callback_does_not_leave_terminal_support(self):
        from test_s1_structure_selection import catalog, pose
        cancelled = threading.Event()
        def committed(mid):
            cancelled.set(); return True
        report = StructureCandidateScanner(catalog(), lambda *a, **kw: pose()).scan(
            np.zeros((8, 8, 3), np.uint8), floor='-1', floor_source='current-frame-verified',
            preferred_map_id='b', preferred_material_committed=committed, cancelled=cancelled.is_set)
        self.assertEqual(report['status'], 'cancelled')
        self.assertFalse(report['poses'])
        self.assertFalse(report['scanSkipped'])


if __name__ == '__main__':
    unittest.main()
