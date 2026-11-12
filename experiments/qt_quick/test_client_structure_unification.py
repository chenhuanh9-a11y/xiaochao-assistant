"""Default imported S0 -> shared scanner/session; synthetic capture/fit only."""
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from client_live_runtime import LiveRuntime
from structure_pose_solver import PreparedStructurePoseSolver as _AlignmentHost
from structure_pose_solver import StructurePoseSolver
from client_engine import INITIAL_IDENTITY_BUDGET_SECONDS
from client_providers import ProviderUnavailable, attach_structure_scanner
from s1_structure_selection import StructureCatalog, StructureCandidateScanner
from test_client_live_runtime import FixtureCapture
import test_client_native_base_provider as native_fixture


def supported_pose():
    return dict(success=True, coordinateSpace='shared-structure-to-client',
        matrixSourceToClient=np.eye(3), selectedRouteCurrentFrameResidual=dict(
            verified=True, poseVerifiedAgainstSharedStructure=True,
            poseVerifiedAgainstCanonicalGeometry=True, profileRenderDerivedFromCanonical=True,
            coordinatePackId='fixture-pack', validationBasis='shared-canonical-geometry-current-frame',
            withinThreePixels=.95))


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

    def test_capture_observes_owned_pixels_once_before_shared_scan(self):
        captured = []
        original_capture = self.capture.capture
        def acquire(*args, **kwargs):
            result = original_capture(*args, **kwargs)
            captured.append(result)
            return result
        self.runtime._observe.reset_mock()
        prepare_scan = StructurePoseSolver.prepare_structure_scan
        with patch.object(self.capture, 'capture', side_effect=acquire), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()), \
             patch.object(StructurePoseSolver, 'prepare_structure_scan', autospec=True,
                 side_effect=prepare_scan) as scan_preparation:
            result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.assertEqual(len(captured), 1)
        self.assertEqual(scan_preparation.call_count, 1)
        self.assertIs(scan_preparation.call_args.kwargs['automatic_region'], True)
        self.runtime._observe.assert_called_once()
        pixels = captured[0].frame_bgr
        self.assertIs(self.runtime._observe.call_args.args[1], pixels)
        self.assertIs(self.runtime._last_observation['frame'], pixels)
        self.assertIs(captured[0].frame_snapshot.pixels, pixels)
        with self.assertRaises(ValueError):
            pixels.setflags(write=True)

    def test_current_s0_candidate_hands_off_preview_without_identity_or_persistent_pose(self):
        # The imported typed engine, scanner/session, display provider, core and
        # final render/ACK consumer are real. Only capture pixels, floor reading
        # and the expensive fit sensor are fixture inputs.
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
            result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.assertEqual(fit.call_count, 1, 'preview must reuse the verified ticket pose')
        row = self.runtime.render_payload(result['renderToken'])
        self.assertIsNotNone(row)
        self.assertEqual(row['mapId'], 'fixture-map')
        self.assertTrue(row['candidateDisplayOnly'])
        self.assertFalse(row['identityLocked'])
        self.assertEqual(self.runtime._map_id, '')
        self.assertEqual(self.runtime._durable_map_id, '')
        self.assertIsNone(self.runtime._gate.confirmed_id)
        self.assertFalse(self.runtime._core.pose_by_map)
        accepted = self.runtime.acknowledge_render_material(row['token'])
        self.assertTrue(accepted['success'], accepted)
        self.assertFalse(accepted['needsWorker'])
        self.assertFalse(self.runtime._core.pose_by_map)
        self.assertIsNone(self.runtime._group)
        self.assertTrue(self.runtime.reference_payload()['candidateReference'])
        self.assertTrue(self.runtime.snapshot()['candidateProvisionalDisplayEnabled'])

    def test_passive_refresh_keeps_material_and_does_not_repeat_catalogue_scan(self):
        self.capture_choice_payload()
        first = self.runtime._render
        events = []
        self.runtime.set_event_callback(events.append)
        from client_live_runtime import _AlignmentHost as RuntimeHost
        with patch.object(RuntimeHost, '_canonical_geometry_current_frame_residual',
                return_value=(supported_pose()['selectedRouteCurrentFrameResidual'], None)), \
             patch.object(self.engine, 'qt_structure_scan',
                side_effect=AssertionError('same-opening poll repeated catalogue scan')):
            for _ in range(3):
                result = self.runtime.dispatch('refresh_map')
                self.assertTrue(result['success'], result)
                self.assertTrue(result['unchanged'], result)
                self.assertIsNotNone(self.runtime.render_payload(first['token']))
        self.assertFalse(any(row['event'] == 'render-revoked' for row in events), events)
        self.assertEqual(self.runtime._map_id, '')
        self.assertFalse(self.runtime._core.pose_by_map)

    def test_refresh_replaces_only_after_new_frame_fit_and_retains_on_failure_or_cancel(self):
        self.capture_choice_payload()
        first = self.runtime._render
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        self.capture.frame[0, 0] = 255
        results = []
        from client_live_runtime import _AlignmentHost as RuntimeHost
        def fit(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return supported_pose()
        with patch.object(RuntimeHost, '_canonical_geometry_current_frame_residual',
                return_value=(dict(verified=False), None)), \
             patch.object(_AlignmentHost, 'fit', side_effect=fit):
            worker = threading.Thread(target=lambda: results.append(
                self.runtime.dispatch('refresh_map', {}, cancelled)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNotNone(self.runtime.render_payload(first['token']))
                self.assertFalse(self.runtime.acknowledge_render_material(first['token'])['success'],
                    'old material must not authorize the new observation')
                cancelled.set()
            finally:
                release.set(); worker.join(5)
        self.assertTrue(results[0]['stale'], results)
        self.assertIsNotNone(self.runtime.render_payload(first['token']))
        with patch.object(RuntimeHost, '_canonical_geometry_current_frame_residual',
                return_value=(dict(verified=False), None)), \
             patch.object(_AlignmentHost, 'fit', return_value=dict(success=False, reason='fixture-no-match')):
            failed = self.runtime.dispatch('refresh_map')
        self.assertFalse(failed['success'], failed)
        self.assertIsNotNone(self.runtime.render_payload(first['token']))
        replacement = supported_pose()
        replacement['matrixSourceToClient'][0, 0] = 1.1
        replacement['matrixSourceToClient'][1, 1] = 1.1
        with patch.object(RuntimeHost, '_canonical_geometry_current_frame_residual',
                return_value=(dict(verified=False), None)), \
             patch.object(_AlignmentHost, 'fit', return_value=replacement):
            result = self.runtime.dispatch('refresh_map')
        self.assertTrue(result['success'], result)
        current = self.runtime.render_payload(result['renderToken'])
        self.assertNotEqual(current['token'], first['token'])
        self.assertNotEqual(current['frameId'], first['frameId'])
        self.assertIsNone(self.runtime.render_payload(first['token']))
        self.assertAlmostEqual(current['canonicalMatrix'][0][0], 1.1)

    def test_same_opening_probe_failure_retains_but_boundaries_retire_material(self):
        self.capture_choice_payload()
        first = self.runtime._render
        with patch.object(self.capture, 'capture', side_effect=OSError('fixture capture failure')):
            result = self.runtime.dispatch('refresh_map')
        self.assertTrue(result['error'], result)
        self.assertIsNotNone(self.runtime.render_payload(first['token']))
        self.runtime.dispatch('close_map')
        self.assertIsNone(self.runtime.render_payload(first['token']))
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_floor_change_retires_material_before_new_floor_fit(self):
        self.capture_choice_payload()
        first = self.runtime._render
        entered, release = threading.Event(), threading.Event()
        results = []
        self.runtime._observe.return_value = (dict(detected=True, mapOpen=True), '2',
            dict(selectedFloor=2, mapOpen=True))
        def fit(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return supported_pose()
        with patch.object(_AlignmentHost, 'fit', side_effect=fit):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('refresh_map')))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.runtime.render_payload(first['token']))
            finally:
                release.set(); worker.join(5)
        self.assertTrue(results[0]['success'], results)
        self.assertEqual(self.runtime.render_payload(results[0]['renderToken'])['floor'], '2')

    def test_close_during_replacement_rejects_late_fit(self):
        self.capture_choice_payload()
        first = self.runtime._render
        self.capture.frame[0, 0] = 255
        entered, release = threading.Event(), threading.Event()
        results = []
        from client_live_runtime import _AlignmentHost as RuntimeHost
        def fit(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return supported_pose()
        with patch.object(RuntimeHost, '_canonical_geometry_current_frame_residual',
                return_value=(dict(verified=False), None)), \
             patch.object(_AlignmentHost, 'fit', side_effect=fit):
            worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch('refresh_map')))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNotNone(self.runtime.render_payload(first['token']))
                self.assertTrue(self.runtime.dispatch('close_map')['success'])
                self.assertIsNone(self.runtime.render_payload(first['token']))
            finally:
                release.set(); worker.join(5)
        self.assertFalse(results[0]['success'], results)
        self.assertTrue(results[0]['stale'], results)
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])

    def test_passive_probe_retires_changed_capture_context(self):
        self.capture_choice_payload()
        first = self.runtime._render
        with patch.object(self.runtime, '_capture_is_current', return_value=False):
            result = self.runtime.dispatch('refresh_map')
        self.assertEqual(result['reason'], 'capture-context-changed', result)
        self.assertIsNone(self.runtime.render_payload(first['token']))

    def test_candidate_material_and_passive_probe_reject_superseded_resource_owner(self):
        self.capture_choice_payload()
        first = self.runtime._render
        with patch.object(self.engine, 'qt_resource_context_current', return_value=False):
            result = self.runtime.dispatch('refresh_map')
        self.assertEqual(result['reason'], 'resource-context-superseded', result)
        self.assertIsNone(self.runtime._render)
        self.assertFalse(self.runtime._core.snapshot()['displayAuthorized'])
        self.assertIsNone(self.runtime.render_payload(first['token']))

    def test_render_consumer_retires_superseded_resource_owner_without_probe(self):
        self.capture_choice_payload()
        first = self.runtime._render
        with patch.object(self.engine, 'qt_resource_context_current', return_value=False):
            self.assertIsNone(self.runtime.render_payload(first['token']))
        self.assertIsNone(self.runtime._render)

    def test_coordinator_candidate_poll_after_drag_uses_existing_probe_owner(self):
        from types import SimpleNamespace
        from client_coordinator import ClientCoordinator
        self.capture_choice_payload()
        self.drag_current_preview()
        moved = self.runtime._render
        self.capture.frame[0, 0] = 255
        calls, results = [], []
        def dispatch(action):
            calls.append(action)
            results.append(self.runtime.dispatch(action))
        session = SimpleNamespace(busy=False, snapshot=self.runtime.snapshot(), dispatch=dispatch)
        coordinator = SimpleNamespace(_closed=False, _closing=False, _binding_suspended=False,
            _bound='123', session=session, resources=SimpleNamespace(busy=False, pendingKind=''),
            _pointer_drag=None, _pending_context=None, _last_manual_floor_observation_at=0,
            _native_candidate_preview_ready=ClientCoordinator._native_candidate_preview_ready,
            _native_windows=SimpleNamespace(foreground_window=lambda: 123))
        with patch.object(self.engine, 'qt_structure_scan',
                side_effect=AssertionError('transient poll must not rescan candidates')):
            ClientCoordinator._track_map(coordinator)
        self.assertEqual(calls, ['observe_candidate_floor'])
        self.assertTrue(results[0]['success'], results)
        self.assertFalse(results[0]['fitAttempted'], results)
        current = self.runtime.render_payload(moved['token'])
        self.assertIsNotNone(current)
        self.assertEqual(current['matrix'], moved['matrix'])
        self.runtime.dispatch('close_map')
        self.assertIsNone(self.runtime.render_payload(moved['token']))

    def test_candidate_drag_capture_failure_keeps_successful_material(self):
        self.capture_choice_payload()
        self.drag_current_preview()
        first = self.runtime._render
        with patch.object(self.capture, 'capture', side_effect=OSError('fixture drag capture failure')):
            result = self.runtime.dispatch('observe_candidate_floor')
        self.assertTrue(result['error'], result)
        self.assertIsNotNone(self.runtime.render_payload(first['token']))

    def test_cancel_candidate_drag_probe_keeps_successful_material(self):
        self.capture_choice_payload()
        self.drag_current_preview()
        first = self.runtime._render
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        results = []
        capture = self.capture.capture
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return capture(*args, **kwargs)
        with patch.object(self.capture, 'capture', side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(
                self.runtime.dispatch('observe_candidate_floor', {}, cancelled)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                cancelled.set()
            finally:
                release.set(); worker.join(5)
        self.assertTrue(results[0]['stale'], results)
        self.assertIsNotNone(self.runtime.render_payload(first['token']))

    def test_current_ticket_preview_stays_visible_until_identity_material_replaces_it(self):
        payload = self.capture_choice_payload()
        old = self.runtime._render
        observation = self.runtime._last_observation
        session, ticket = self.runtime._s1_structure_session, self.runtime._s1_structure_ticket
        events = []
        self.runtime.set_event_callback(lambda event: events.append((event['event'],
            self.runtime._map_id, self.runtime._provisional_display_map_id, self.runtime._render)))
        # The source of this already-qualified identity row is replaced here;
        # the real reducer and downstream ticket/material consumers are used.
        row = dict(mapId=payload['mapId'], strongSuggestion=True, identityHardGatePassed=True)
        selected, seed, stale = self.runtime._commit_identity_rows(self.engine, [row],
            observation['frame'], observation['floor'], self.runtime._epoch,
            observation['frame_id'], None, identity_snapshot=(self.runtime._identity_native,
                self.runtime._identity_context(), self.runtime._group_intent_generation))
        self.assertFalse(stale)
        self.assertEqual(selected, payload['mapId'])
        self.assertEqual(self.runtime._durable_map_id, selected)
        retained = self.runtime.render_payload(old['token'])
        self.assertIsNotNone(retained)
        self.assertIs(retained['pixels'], old['pixels'])
        self.assertEqual(retained['authorizationId'], old['authorizationId'])
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])
        self.assertNotIn(old['token'], self.runtime._remember_proposals)
        self.assertIsNone(self.runtime._reference_sheet)
        self.assertEqual(self.runtime._s1_structure_ticket, ticket)
        revoked = [event for event in events if event[0] == 'render-revoked']
        self.assertEqual(revoked, [])
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('same-ticket fit repeated')):
            result = self.runtime._solve_observation('capture_now', {}, None, self.engine, None,
                self.runtime._epoch, observation['captured'], observation['frame'], observation['floor'],
                observation['digest'], observation['capture_generation'], observation['generation'],
                observation['frame_id'], observation['cycle_id'], selected, seed,
                structure_ticket=(session, ticket))
        self.assertTrue(result['success'], result)
        self.assertTrue(self.runtime.render_payload(result['renderToken'])['identityLocked'])
        self.assertIn(selected, self.runtime._core.pose_by_map)

    def test_entry_identity_after_new_capture_retires_candidate_memory_without_pixels_id(self):
        self.capture_choice_payload()
        preview = self.runtime._render
        old_reference = self.runtime._reference_sheet
        self.assertTrue(self.runtime.acknowledge_render_material(preview['token'])['success'])
        self.assertIsNotNone(self.runtime._s1_candidate_preference)
        self.assertIsNotNone(self.runtime._s1_candidate_pose_hint)
        scan, retired = self.engine.qt_structure_scan, []
        def entry_before_scan(frame, **kwargs):
            # Only the qualified entry-row source is substituted. The new
            # capture has already retired pixels; the real identity reducer,
            # scanner/session and final material consumer remain in use.
            self.assertEqual(self.runtime._provisional_display_map_id, '')
            self.assertIsNone(self.runtime._render)
            selected, _, stale = self.runtime._commit_identity_rows(self.engine,
                [dict(mapId='fixture-map', strongSuggestion=True, identityHardGatePassed=True)],
                frame, kwargs['floor'], self.runtime._epoch, self.runtime._frame_id, None,
                identity_snapshot=(self.runtime._identity_native, self.runtime._identity_context(),
                    self.runtime._group_intent_generation))
            self.assertFalse(stale)
            self.assertEqual(selected, 'fixture-map')
            retired.append(dict(preference=self.runtime._s1_candidate_preference,
                hint=self.runtime._s1_candidate_pose_hint,
                oldProposal=preview['token'] in self.runtime._remember_proposals,
                oldReference=self.runtime._reference_sheet is old_reference))
            return scan(frame, **kwargs)
        with patch.object(self.engine, 'qt_structure_scan', side_effect=entry_before_scan), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            result = self.runtime.dispatch('capture_now')
        self.assertEqual(retired, [dict(preference=None, hint=None, oldProposal=False, oldReference=False)])
        self.assertFalse(self.runtime.acknowledge_render_material(preview['token'])['success'])
        # Produce real confirmed material, then repeat retirement: the helper
        # must leave the formal render/reference and noncandidate proposal alone.
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            confirmed = self.runtime.dispatch('capture_now')
        self.assertTrue(confirmed['success'], confirmed)
        current = self.runtime._render
        reference = self.runtime._reference_sheet
        proposal = self.runtime._remember_proposals[current['token']]
        self.assertFalse(current['candidateDisplayOnly'])
        self.assertFalse(proposal.get('candidateOnly'))
        authorization = self.runtime._core.display_authorization
        with self.runtime._lock:
            token = self.runtime._retire_candidate_preview(reason='repeated-identity-commit')
        self.assertEqual(token, '')
        self.assertIs(self.runtime._render, current)
        self.assertIs(self.runtime._reference_sheet, reference)
        self.assertIs(self.runtime._remember_proposals[current['token']], proposal)
        self.assertIs(self.runtime._core.display_authorization, authorization)
        self.assertIsNotNone(self.runtime.render_payload(current['token']))

    def test_different_identity_still_retires_current_candidate_pixels(self):
        self.capture_choice_payload()
        old = self.runtime._render
        with self.runtime._lock:
            token = self.runtime._retire_candidate_preview(
                reason='candidate-identity-committed', map_id='different-map')
        self.assertEqual(token, old['token'])
        self.assertIsNone(self.runtime.render_payload(old['token']))
        self.assertFalse(self.runtime._core.display_authorization.active)

    def drag_current_preview(self):
        self.runtime._monotonic = lambda: 1.
        context = self.runtime.game_floor_click_context()
        self.assertIsNotNone(context)
        for phase, sequence, total, delta in (
                ('down', 1, (0., 0.), (0., 0.)),
                ('move', 2, (13., -7.), (13., -7.)),
                ('up', 3, (13., -7.), (0., 0.))):
            result = self.runtime.observe_game_drag(dict(phase=phase, gestureId='shared-preview',
                pointerSequence=sequence, context=context, createdAt=1., totalDelta=total, delta=delta))
            self.assertTrue(result['success'], result)
        self.assertTrue(self.runtime._transient_cycle_current())

    def assert_candidate_arrivals_cannot_adopt_successor(self, boundary):
        for action in ('next_candidate', 'previous_candidate', 'select_candidate', 'reselect_map', 'map_reselection'):
            with self.subTest(action=action, boundary=boundary):
                payload = self.capture_choice_payload()
                arrived, results = threading.Event(), []
                serials, main = self.runtime._dispatch_serials, threading.get_ident()
                class ObservedSerials:
                    def __next__(self):
                        value = next(serials)
                        if threading.get_ident() != main:
                            arrived.set()
                        return value
                self.runtime._dispatch_serials = ObservedSerials()
                try:
                    with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()), self.runtime._lock:
                        # This is the real dispatch arrival and original RLock;
                        # only observe serial allocation before its lock wait.
                        worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch(action, payload)))
                        worker.start()
                        self.assertTrue(arrived.wait(3), 'candidate command never arrived')
                        if boundary == 'close':
                            self.runtime.dispatch('close_map')
                        elif boundary == 'settings':
                            self.runtime.configure_settings(dict(self.runtime._settings,
                                routePointNumbers=not self.runtime._settings.get('routePointNumbers', False)))
                        elif boundary == 'end-round':
                            self.runtime.dispatch('end_round')
                            self.assertTrue(self.runtime.dispatch('start_round')['success'])
                        else:
                            self.runtime.invalidate('resources-changed')
                        successor = self.runtime.dispatch('capture_now')
                        self.assertTrue(successor['success'], successor)
                        token, ticket = successor['renderToken'], self.runtime._s1_structure_ticket
                        epoch, count = self.runtime._epoch, self.capture.count
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(results[0]['success'], results)
                    self.assertTrue(results[0].get('stale'), results)
                    self.assertIsNotNone(self.runtime.render_payload(token))
                    self.assertEqual(self.runtime._s1_structure_ticket, ticket)
                    self.assertEqual(self.runtime._epoch, epoch)
                    self.assertEqual(self.capture.count, count)
                finally:
                    self.runtime._dispatch_serials = serials

    def test_s0_candidate_arrivals_before_close_cannot_adopt_successor(self):
        self.assert_candidate_arrivals_cannot_adopt_successor('close')

    def test_s0_candidate_arrivals_before_settings_cannot_adopt_successor(self):
        self.assert_candidate_arrivals_cannot_adopt_successor('settings')

    def test_s0_candidate_arrivals_before_end_or_resources_cannot_adopt_successor(self):
        for boundary in ('end-round', 'resources'):
            self.assert_candidate_arrivals_cannot_adopt_successor(boundary)

    def test_s0_reselection_waiting_for_work_keeps_its_own_birth(self):
        for action, boundary in ((action, boundary) for action in ('reselect_map', 'map_reselection')
                for boundary in ('close', 'new-frame')):
            with self.subTest(action=action, boundary=boundary):
                self.capture_choice_payload()
                waiting, results = threading.Event(), []
                main = threading.get_ident()
                self.runtime.set_event_callback(lambda event: waiting.set()
                    if threading.get_ident() != main and event['event'] == 'render-revoked'
                    and event['reason'] == 'candidate-reselection' else None)
                try:
                    with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()), self.runtime._work:
                        worker = threading.Thread(target=lambda: results.append(self.runtime.dispatch(action)))
                        worker.start()
                        self.assertTrue(waiting.wait(3), 'reselection never revoked its original preview')
                        if boundary == 'close':
                            self.runtime.dispatch('close_map')
                        successor = self.runtime.dispatch('capture_now')
                        self.assertTrue(successor['success'], successor)
                        token, ticket = successor['renderToken'], self.runtime._s1_structure_ticket
                        epoch, count = self.runtime._epoch, self.capture.count
                    worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertFalse(results[0]['success'], results)
                    self.assertTrue(results[0].get('stale'), results)
                    self.assertIsNotNone(self.runtime.render_payload(token))
                    self.assertEqual(self.runtime._s1_structure_ticket, ticket)
                    self.assertEqual(self.runtime._epoch, epoch)
                    self.assertEqual(self.capture.count, count)
                    self.assertFalse(self.runtime._candidate_edit)
                finally:
                    self.runtime.set_event_callback(None)

    def test_old_s0_candidate_material_error_cannot_revoke_new_frame_owner(self):
        payload = self.capture_choice_payload()
        display, successor = self.runtime._display_layer, []
        def old_material_failure(*args, **kwargs):
            if not successor:
                successor.append(None)
                self.runtime.configure_settings(dict(self.runtime._settings, routePointNumbers=True))
                current = self.runtime.dispatch('capture_now')
                self.assertTrue(current['success'], current)
                successor[0] = current['renderToken']
                raise OSError('old candidate display read failed')
            return display(*args, **kwargs)
        with patch.object(self.runtime, '_display_layer', side_effect=old_material_failure), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            old = self.runtime.dispatch('select_candidate', payload)
        self.assertFalse(old['success'], old)
        self.assertTrue(old.get('stale'), old)
        self.assertIsNotNone(self.runtime.render_payload(successor[0]))

    def test_s0_preview_drag_is_replaced_by_current_ticket_on_new_capture(self):
        self.capture_choice_payload()
        first = self.runtime._render
        self.drag_current_preview()
        for attempt in range(2):
            with self.subTest(attempt=attempt), \
                 patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
                result = self.runtime.dispatch('capture_now')
            self.assertTrue(result['success'], result)
            self.assertEqual(fit.call_count, 1)
            current = self.runtime.render_payload(result['renderToken'])
            self.assertIsNotNone(current)
            self.assertNotEqual(current['frameId'], first['frameId'])
            self.assertFalse(current.get('transient'))
            self.assertFalse(self.runtime._transient_cycle_current())
            self.assertIsNone(self.runtime._transient_drag)
            self.assertFalse(self.runtime._core.pose_by_map)
            self.assertEqual(self.runtime._map_id, '')
            self.assertFalse(self.runtime._gate.confirmed_id)
            self.drag_current_preview()

    def test_s0_preview_drag_probe_preserves_pixels_and_close_revokes_transient(self):
        self.capture_choice_payload()
        first = self.runtime._render
        self.drag_current_preview()
        moved = self.runtime._render
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('drag probe ran fit')):
            result = self.runtime.dispatch('probe_map_after_drag')
            refreshed = self.runtime.dispatch('refresh_map')
        self.assertTrue(result['success'], result)
        self.assertTrue(refreshed['success'], refreshed)
        self.assertFalse(refreshed['fitAttempted'])
        current = self.runtime.render_payload(first['token'])
        self.assertIsNotNone(current)
        np.testing.assert_array_equal(current['matrix'], moved['matrix'])
        self.assertTrue(current['transient'])
        self.assertFalse(self.runtime._core.pose_by_map)
        self.assertTrue(self.runtime.dispatch('close_map')['success'])
        self.assertIsNone(self.runtime._transient_drag)
        self.assertIsNone(self.runtime._transient_cycle)
        self.assertIsNone(self.runtime.render_payload(first['token']))
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            reopened = self.runtime.dispatch('capture_now')
        self.assertTrue(reopened['success'], reopened)
        self.assertFalse(self.runtime.render_payload(reopened['renderToken']).get('transient'))

    def test_s0_candidate_switch_after_drag_uses_a_new_current_ticket(self):
        payload = self.capture_choice_payload()
        old_frame = self.runtime._frame_id
        old_ticket = self.runtime._s1_structure_ticket
        self.drag_current_preview()
        count = self.capture.count
        rejected = self.runtime.dispatch('select_candidate', dict(payload, structureFrameId='foreign'))
        self.assertFalse(rejected['success'], rejected)
        self.assertEqual(self.capture.count, count)
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
            result = self.runtime.dispatch('select_candidate', payload)
        self.assertTrue(result['success'], result)
        self.assertEqual(self.capture.count, count + 1)
        self.assertEqual(fit.call_count, 1)
        self.assertNotEqual(self.runtime._frame_id, old_frame)
        self.assertNotEqual(self.runtime._s1_structure_ticket, old_ticket)
        current = self.runtime.render_payload(result['renderToken'])
        self.assertIsNotNone(current)
        self.assertFalse(current.get('transient'))
        self.assertFalse(self.runtime._transient_cycle_current())
        self.assertFalse(self.runtime._core.pose_by_map)
        self.assertFalse(self.runtime._durable_map_id)
        self.drag_current_preview()
        with patch.object(_AlignmentHost, 'fit', return_value=supported_pose()):
            advanced = self.runtime.dispatch('next_candidate')
        self.assertTrue(advanced['success'], advanced)
        self.assertFalse(self.runtime.render_payload(advanced['renderToken']).get('transient'))
        context = self.runtime.game_floor_click_context()
        down = self.runtime.observe_game_drag(dict(phase='down', gestureId='held-preview',
            pointerSequence=4, context=context, createdAt=1., totalDelta=(0., 0.), delta=(0., 0.)))
        self.assertTrue(down['success'], down)
        row = self.runtime.snapshot()['candidateRows'][0]
        selection = {key: row[key] for key in (
            'mapId', 'structureFrameId', 'structureGeneration', 'structureRoundId')}
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('unmoved choice must reuse ticket')):
            switched = self.runtime.dispatch('select_candidate', selection)
        self.assertTrue(switched['success'], switched)
        self.assertIsNone(self.runtime._transient_drag)
        late = self.runtime.observe_game_drag(dict(phase='move', gestureId='held-preview',
            pointerSequence=5, context=context, createdAt=1., totalDelta=(13., -7.), delta=(13., -7.)))
        self.assertFalse(late['success'], late)

    def test_scan_supported_but_incomplete_render_pose_cannot_preview_or_lock(self):
        partial = supported_pose()
        partial['selectedRouteCurrentFrameResidual'] = dict(verified=True,
            poseVerifiedAgainstSharedStructure=True, withinThreePixels=.95)
        with patch.object(_AlignmentHost, 'fit', return_value=partial):
            result = self.runtime.dispatch('capture_now')
        self.assertFalse(result['success'], result)
        self.assertEqual(self.runtime.snapshot()['structureEvidence']['currentSupportedMapIds'], ['fixture-map'])
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        self.assertIsNone(self.runtime._render)
        self.assertFalse(self.runtime._durable_map_id)
        self.assertFalse(self.runtime._gate.confirmed_id)
        self.assertFalse(self.runtime._core.pose_by_map)

    def test_s0_author_display_change_reuses_candidate_pose_without_promoting_identity(self):
        self.capture_choice_payload()
        old = self.runtime._render
        pose = np.asarray(old['canonicalMatrix'])
        with patch.object(_AlignmentHost, 'fit', side_effect=AssertionError('author ran fit')):
            result = self.runtime.dispatch('set_context', dict(mode='nightmare', season='s0', profileId='wang-qingxin'))
        self.assertTrue(result['success'], result)
        current = self.runtime._render
        self.assertIsNotNone(current)
        self.assertNotEqual(current['token'], old['token'])
        np.testing.assert_array_equal(current['canonicalMatrix'], pose)
        self.assertTrue(current['candidateDisplayOnly'])
        self.assertFalse(current['identityLocked'])
        self.assertEqual(self.runtime._map_id, '')
        self.assertFalse(self.runtime._gate.confirmed_id)
        self.assertFalse(self.runtime._core.pose_by_map)
        self.assertFalse(self.runtime.acknowledge_render_material(old['token'])['success'])
        self.assertTrue(self.runtime.acknowledge_render_material(current['token'])['success'])

    def test_unresolved_default_s0_publishes_shared_structure_proposals_without_identity(self):
        self.assertFalse(hasattr(self.runtime, '_rank_identity'))
        self.assertFalse(hasattr(self.runtime, '_rank_structure_candidates'))
        with patch.object(StructureCandidateScanner, 'scan', autospec=True,
                          side_effect=StructureCandidateScanner.scan) as scan, \
             patch.object(self.engine, 'rank', side_effect=AssertionError('old rank')), \
             patch.object(self.engine, 'rank_established_group', side_effect=AssertionError('old group rank')), \
             patch.object(self.runtime, '_collect_legacy_entry_evidence', side_effect=AssertionError('legacy collector')), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
            result = self.runtime.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(fit.call_count, 1)
        state = self.runtime.snapshot()
        self.assertEqual(state['structureEvidence']['currentSupportedMapIds'], ['fixture-map'])
        self.assertEqual(state['structureEvidence']['possibleMapIds'], ['fixture-map'])
        self.assertEqual(state['structureEvidence']['excludedMapIds'], [])
        self.assertTrue(state['candidateRows'][0]['structureSupported'])
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['groupLocked'])
        self.assertTrue(state['displayAuthorized'])
        self.assertTrue(self.runtime._render['candidateDisplayOnly'])
        self.assertEqual(self.runtime._identity_source, 's0-current-structure-hypothesis')
        self.assertEqual(self.engine.qt_logical_group_registry,
                         self.engine.qt_native_base.reviewed_front_groups)
        with patch.object(self.capture, 'capture', side_effect=AssertionError('retained must not capture')), \
             patch.object(self.runtime, '_collect_legacy_entry_evidence', side_effect=AssertionError('legacy retained collector')), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as retained_fit:
            retained = self.runtime._solve_retained('capture_now', {}, None, acquire_current=False)
        self.assertTrue(retained['success'], retained)
        self.assertEqual(retained_fit.call_count, 1)
        self.assertFalse(self.runtime.snapshot()['identityLocked'])
        import cv2
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from client_engine import EngineAdapter
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.png'
            ok, encoded = cv2.imencode('.png', self.capture.frame)
            self.assertTrue(ok)
            path.write_bytes(encoded.tobytes())
            adapter = EngineAdapter(engine_factories=self.source.factories, allowed_read_root=folder)
            with patch.object(self.engine, 'rank', side_effect=AssertionError('old offline rank')), \
                 patch.object(self.engine, 'rank_established_group', side_effect=AssertionError('old offline group rank')), \
                 patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as saved_fit:
                saved = adapter.dispatch('rank_saved_frame', dict(path=str(path), season='s0',
                    mode='nightmare', floor='1', inputKind='synthetic'))
            self.assertTrue(saved['success'], saved)
            self.assertEqual(saved_fit.call_count, 1)
            self.assertFalse(saved['identityOrPoseAuthorized'])
            self.assertEqual(saved['diagnostics']['structureObservation']['supportedMapIds'], ['fixture-map'])
        # Spend the budget inside the real common plan's entry-scope kernel.
        # Neither old rank controller may run or allocate a second fit budget.
        select, perf_counter, offset = self.engine.front_gate_prefilter.select, time.perf_counter, [0.]
        def spent_entry_budget(*args, **kwargs):
            rows = select(*args, **kwargs)
            offset[0] += INITIAL_IDENTITY_BUDGET_SECONDS + 1
            return rows
        with patch.object(self.engine, 'rank', side_effect=AssertionError('old rank')), \
             patch.object(self.engine, 'rank_established_group', side_effect=AssertionError('old group rank')), \
             patch.object(self.engine.front_gate_prefilter, 'select', side_effect=spent_entry_budget), \
             patch('client_live_runtime.time.perf_counter', side_effect=lambda: perf_counter() + offset[0]), \
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
        self.assertTrue(result['success'], result)
        rows = self.runtime.snapshot()['candidateRows']
        index = next(i for i, row in enumerate(rows) if row['structureSupported'])
        row = rows[index]
        # These are the fields sent by ClientControls.chooseCandidate(). The
        # S0 UI adds a stable map ID when the row carries current structure.
        return dict(index=index, **{key: row[key] for key in (
            'mapId', 'structureFrameId', 'structureGeneration', 'structureRoundId')})

    def test_current_s0_structure_choice_changes_preview_without_confirming_identity(self):
        payload = self.capture_choice_payload()
        result = self.runtime.dispatch('select_candidate', dict(payload, index=-1))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['reason'], 'current-frame-pose-authorized')
        state = self.runtime.snapshot()
        self.assertEqual(state['selectedCandidate']['mapId'], 'fixture-map')
        self.assertFalse(self.runtime._selection_pending)
        self.assertFalse(state['identityLocked'])
        self.assertFalse(state['groupLocked'])
        self.assertTrue(state['displayAuthorized'])
        self.assertTrue(self.runtime._render['candidateDisplayOnly'])

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
        with patch.object(self.runtime, '_recognition_diagnostic',
                          wraps=self.runtime._recognition_diagnostic) as diagnostic:
            result = self.runtime.dispatch('capture_now')
        self.assertEqual(result['reason'], 'identity-not-uniquely-verified', result)
        evidence = self.runtime.snapshot()['structureEvidence']
        self.assertEqual(evidence['currentStatus'], 'complete')
        self.assertEqual(evidence['currentSupportedMapIds'], [])
        self.assertEqual(evidence['possibleMapIds'], ['fixture-map'])
        self.assertEqual(evidence['excludedMapIds'], [])
        self.assertFalse(self.runtime.snapshot()['displayAuthorized'])
        scan = [call.kwargs for call in diagnostic.call_args_list if call.args[0] == 's1-scan-result']
        self.assertEqual(len(scan), 1)
        self.assertEqual(scan[0]['candidatePreparationCount'], 1)
        self.assertGreater(scan[0]['candidatePreparationMs'], 0)
        self.assertGreater(scan[0]['candidateFitMs'], 0)
        self.assertFalse(scan[0]['directStructureRecognition']['authorizesIdentity'])
        self.assertFalse(scan[0]['directStructureRecognition']['lock'])

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


class ImportedDifficultPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from test_export_s0_difficult_native_base_package import S0DifficultSourceFixture
        temporary = TemporaryDirectory(prefix='shared-s0d-preview-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = S0DifficultSourceFixture(Path(temporary.name))
        cls.source.export()

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
        from client_paths import RuntimePaths
        from imported_resource_archive import save_archive
        from imported_resource_catalog import ResourceResolver
        from resource_transaction import resource_runtime_lease
        import client_imported_activation as activation
        temporary = TemporaryDirectory(prefix='shared-s0d-preview-active-')
        self.addCleanup(temporary.cleanup)
        paths = RuntimePaths(Path(temporary.name).resolve(), False)
        resolver = ResourceResolver(paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
        refs = {ref.component_id: ref for ref in save_archive(self.source.output, resolver.root).component_refs}
        with resource_runtime_lease(resolver.root):
            activation.activate_context(resolver, paths, refs['base'], refs['controls'], season='s0', mode='difficult')
        factories = build_imported_engine_factories(resolver, paths)
        self.runtime = LiveRuntime(engine_factories=factories, capture_provider=FixtureCapture(), evidence_kind='synthetic')
        self.addCleanup(self.runtime.shutdown)
        self.runtime.dispatch('set_context', dict(season='s0', mode='difficult'))
        self.engine = factories.get_engine('difficult')
        self.assertTrue(self.runtime.dispatch('start_round')['success'])

    def capture(self, floor, payload=None):
        with patch.object(self.runtime, '_observe', return_value=(
                dict(detected=True, mapOpen=True), floor, dict(selectedFloor=int(floor), mapOpen=True))), \
             patch.object(_AlignmentHost, 'fit', return_value=supported_pose()) as fit:
            result = self.runtime.dispatch('capture_now', payload)
        self.assertTrue(result['success'], result)
        # Real imported scan, typed material, runtime/core and payload handoff;
        # only frame observation and the fit sensor use synthetic inputs.
        row = self.runtime.render_payload(result['renderToken'])
        self.assertIsNotNone(row)
        self.assertEqual(row['floor'], floor)
        self.assertTrue(row['candidateDisplayOnly'])
        self.assertFalse(row['identityLocked'])
        self.assertFalse(self.runtime._core.pose_by_map)
        self.assertFalse(self.runtime._transient_cycle_current())
        self.assertFalse(self.runtime._gate.confirmed_id)
        self.assertIsNone(self.runtime._s1_private_pool)
        self.assertFalse(self.engine.qt_rank_ready)
        self.assertGreater(fit.call_count, 0)
        return row

    def test_force_catalogue_after_candidate_ack_clears_shared_preference(self):
        row = self.capture('1')
        self.assertTrue(self.runtime.acknowledge_render_material(row['token'])['success'])
        self.assertIsNotNone(self.runtime._s1_candidate_preference)
        scan, observed = self.engine.qt_structure_scan, []
        def scan_current(*args, **kwargs):
            observed.append(kwargs.get('preferred_map_id'))
            return scan(*args, **kwargs)
        with patch.object(self.engine, 'qt_structure_scan', side_effect=scan_current):
            self.capture('1', dict(forceCatalogue=True))
        self.assertEqual(observed, [None])
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.assertFalse(self.runtime._durable_map_id)

    def test_actual_s0_difficult_both_floors_preview_and_different_identity_retires_old_material(self):
        first = self.capture('1')
        SharedStructureRuntimeTests.drag_current_preview(self)
        current = self.capture('2')
        self.assertIsNone(self.runtime.render_payload(first['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(first['token'])['success'])
        self.assertTrue(self.runtime.acknowledge_render_material(current['token'])['success'])
        target = next(mid for mid in self.engine.pack.by_id if mid != current['mapId'])
        observation = self.runtime._last_observation
        events = []
        self.runtime.set_event_callback(lambda event: events.append((event['event'],
            self.runtime._map_id, self.runtime._provisional_display_map_id, self.runtime._render)))
        # Inject only the identity source's already-qualified row. This verifies
        # atomic reducer ownership, not a claim that S0D rank is now qualified.
        selected, _, stale = self.runtime._commit_identity_rows(self.engine,
            [dict(mapId=target, strongSuggestion=True, identityHardGatePassed=True)],
            observation['frame'], '2', self.runtime._epoch, observation['frame_id'], None,
            identity_snapshot=(self.runtime._identity_native, self.runtime._identity_context(),
                self.runtime._group_intent_generation))
        self.assertFalse(stale)
        self.assertEqual(selected, target)
        self.assertEqual(self.runtime._map_id, target)
        self.assertIsNone(self.runtime.render_payload(current['token']))
        self.assertFalse(self.runtime.acknowledge_render_material(current['token'])['success'])
        self.assertNotIn(current['token'], self.runtime._remember_proposals)
        self.assertIsNone(self.runtime._reference_sheet)
        self.assertIsNone(self.runtime._s1_candidate_preference)
        self.assertIsNone(self.runtime._s1_candidate_pose_hint)
        self.assertIn(('render-revoked', target, '', None), events)

if __name__ == '__main__':
    unittest.main()
