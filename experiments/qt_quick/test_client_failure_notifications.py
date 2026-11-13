"""Failure feedback through real Session, Coordinator and shipped QML.

Capture/interface/floor and admitted identity use the existing resource fixture.
The real-solver case removes its fit patch; other replacements are named locally.
No native input/game discovery or user configuration store is used.
"""
import os
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
import cv2
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])
from client_problems import ProblemsService
from client_session import SessionService
from test_client_coordinator import CoordinatorTests
from test_client_live_runtime import LiveRuntimeContracts


class FailureNotificationFlowTests(unittest.TestCase):
    def setUp(self):
        self.r = LiveRuntimeContracts()
        self.r.setUp()
        self.r.patches[-1].stop()  # Execute the actual production fit/solver.
        cv2.circle(self.r.capture.frame, (500, 350), 75, (120, 120, 120), -1)
        self.f = CoordinatorTests()
        self.f.setUpClass()
        self.f.setUp()
        self.c = self.f.coordinator
        self.session = SessionService(self.r.runtime)
        self.c.runtime, self.c.session = self.r.runtime, self.session
        self.c._applied_context = ('nightmare', '', 's0')
        self.c._last_round_active = False
        self.session.changed.connect(self.c._session_changed)
        self.r.runtime.set_event_callback(self.c.runtimeEvent.emit)
        self.notices, self.errors, self.events = [], [], []
        self.c.notificationRequested.connect(lambda *args: self.notices.append(args))
        self.c.errorReported.connect(self.errors.append)
        self.temp = tempfile.TemporaryDirectory()
        self.problems = ProblemsService(SimpleNamespace(work_root=self.temp.name))
        self.c.errorReported.connect(self.problems.record)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(r.toString() for r in rows))
        self.engine.rootContext().setContextProperty('clientCoordinator', self.c)
        self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientToast.qml'))))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.toast = self.engine.rootObjects()[0]

    def tearDown(self):
        self.c._closed = True
        self.session.shutdown()
        self.session._pool.waitForDone(4000)
        APP.processEvents()
        self.problems.shutdown()
        deadline = time.monotonic() + 4
        while self.problems.pendingWrites and time.monotonic() < deadline:
            self.problems.pool.waitForDone(4000)
            APP.processEvents()
        self.assertFalse(self.problems.pendingWrites)
        self.f.tearDown()
        self.r.doCleanups()
        self.toast.close()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()
        self.temp.cleanup()

    def settle(self):
        deadline = time.monotonic() + 4
        while self.session.busy and time.monotonic() < deadline:
            APP.processEvents()
            QTest.qWait(2)
        self.assertFalse(self.session.busy)
        APP.processEvents()
        return self.session.snapshot['lastReceipt']

    def dispatch(self, action, payload=None):
        self.session.dispatchWithPayload(action, payload or {})
        return self.settle()

    def failures(self):
        return [row for row in self.notices if row[2] in {'warning', 'error'}]

    def enable_published_pose_fixture(self, *, grouped=False):
        from client_coordinator import LiveImages
        if grouped:
            import test_client_similarity_protocol as groups
            for mid in groups.IDS[1:]:
                item = SimpleNamespace(id=mid)
                self.r.engine.pack.maps.append(item)
                self.r.engine.pack.by_id[mid] = item
            self.r.engine.qt_logical_group_registry = groups.explicit_registry('nightmare')
            self.r.engine.rank = Mock(return_value=groups.rows(mode='nightmare'))
        # Capture ownership and screen geometry are fixture boundaries. The
        # actual core grant, Session, Coordinator publication/ACK and toast run.
        self.r.runtime._evidence_kind = 'live-client-capture'
        self.r.capture.generation = 1
        self.r.capture.is_current = lambda context, *, generation: generation == 1
        self.c.images = LiveImages()
        geometry = patch.object(self.c, '_convert_client_geometry', side_effect=lambda data: data.update(deviceScale=1))
        geometry.start()
        self.addCleanup(geometry.stop)

    def publish_verified_fixture_pose(self):
        from client_live_runtime import _AlignmentHost
        # Only the numerical result is replaced with the existing independently
        # specified valid matrix/residual fixture; lifecycle is not replaced.
        with patch.object(_AlignmentHost, 'fit', side_effect=lambda *a, **kw: copy.deepcopy(self.r.fit)):
            result = self.dispatch('capture_now')
        self.assertTrue(result['success'], result)
        data = self.c._aligned
        self.assertEqual(data['token'], result['renderToken'])
        self.c._accept_current_material(data, dict(token=data['token'], visible=True,
            materialReady=True, sourceMatchesPayload=True, validPayload=True,
            geometry=[data['originX'], data['originY'], data['clientWidth'], data['clientHeight']]))
        self.assertTrue(self.r.runtime._remember_proposals[data['token']]['accepted'])
        return result

    def test_published_provisional_pose_close_is_not_an_unfinished_opening(self):
        self.enable_published_pose_fixture(grouped=True)
        self.dispatch('start_round')
        self.publish_verified_fixture_pose()
        state = self.r.runtime.snapshot()
        self.assertTrue(state['provisional'])
        self.assertFalse(state['identityLocked'])
        self.assertTrue(state['displayAuthorized'])
        self.notices.clear()
        self.errors.clear()
        self.dispatch('close_map')
        self.assertEqual(self.failures(), [])
        self.assertFalse(any(row.get('reason') == 'map-fit-incomplete' for row in self.errors))
        self.publish_verified_fixture_pose()
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.dispatch('refresh_map')
            self.dispatch('refresh_map')
        self.assertEqual(self.failures(), [])
        self.assertFalse(any(row.get('reason') == 'map-fit-incomplete' for row in self.errors))

    def test_published_pose_does_not_hide_real_failure_on_a_new_known_floor(self):
        from test_client_live_runtime import synthetic_read
        self.enable_published_pose_fixture()
        self.dispatch('start_round')
        self.publish_verified_fixture_pose()
        self.notices.clear()
        self.errors.clear()
        with patch('floor_control_region.locate_vertical_floor_controls', return_value=synthetic_read(selected=2)):
            result = self.dispatch('refresh_map')
        self.assertFalse(result['success'])
        self.assertEqual(result['reason'], 'initial-structure-supported-chains-insufficient')
        self.assertEqual(self.failures(), [])
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.errors[0]['floor'], '2')
        self.assertIn('可用结构线段不足', self.toast.property('notificationMessage'))

    def test_retry_then_published_pose_is_quiet_and_next_failed_opening_explains_once(self):
        self.enable_published_pose_fixture(grouped=True)
        self.dispatch('start_round')
        self.notices.clear()
        self.dispatch('capture_now')  # Actual solver rejection, not a verdict fixture.
        self.assertEqual(self.failures(), [])
        self.publish_verified_fixture_pose()
        self.dispatch('close_map')
        self.assertEqual(self.failures(), [])
        self.notices.clear()
        self.errors.clear()
        self.dispatch('capture_now')  # New opening, same actual negative image.
        self.dispatch('capture_now')
        self.assertEqual(self.failures(), [])
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.errors[0]['fitReport']['attemptCount'], 2)
        self.assertIn('可用结构线段不足', self.toast.property('notificationMessage'))

    def test_actual_solver_retries_explain_once_then_close_summary_reaches_qml(self):
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                if mode == 'difficult':
                    self.dispatch('end_round')
                    self.dispatch('set_context', dict(mode=mode))
                    self.c._applied_context = (mode, '', 's0')
                self.dispatch('start_round')
                self.notices.clear()
                self.errors.clear()
                failed = self.dispatch('capture_now')
                self.assertFalse(failed['success'])
                self.assertTrue(failed['fitReportingDeferred'])
                self.assertEqual(failed['reason'], 'initial-structure-supported-chains-insufficient')
                self.assertEqual(self.failures(), [])
                self.assertEqual(self.errors, [])
                second = self.dispatch('capture_now')
                self.assertEqual(second['reason'], failed['reason'])
                self.assertEqual(self.failures(), [])
                self.assertEqual(self.session.stale_results, 0)
                self.dispatch('close_map')
                self.assertEqual(len(self.failures()), 1)
                self.assertEqual(len(self.errors), 1)
                self.assertEqual(self.errors[0]['fitReport']['attemptCount'], 2)
                self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')
                self.assertIn('可用结构线段不足', self.toast.property('notificationMessage'))
                self.assertTrue(self.toast.isVisible())
                self.assertFalse(self.warnings)

    def test_actual_identity_without_candidates_explains_passive_retry_once(self):
        # Only the identity rank input is replaced; refusal, receipt acceptance
        # and feedback remain actual production code.
        self.r.engine.rank = lambda *a, **kw: []
        self.dispatch('start_round')
        self.notices.clear()
        first = self.dispatch('capture_now')
        self.assertEqual(first['reason'], 'identity-not-uniquely-verified')
        self.assertEqual(self.failures(), [])
        self.dispatch('refresh_map')
        self.assertEqual(self.failures(), [])
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)
        self.assertIn('唯一确认地图', self.toast.property('notificationMessage'))

    def test_late_terminal_event_cannot_replace_the_next_opening_notice(self):
        def hold_terminal(event):
            if event.get('event') == 'map-fit-incomplete':
                self.events.append(event)
            else:
                self.c.runtimeEvent.emit(event)
        self.r.runtime.set_event_callback(hold_terminal)
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.dispatch('close_map')
        self.assertEqual(len(self.events), 1)
        old = self.events[0]
        self.dispatch('capture_now')
        self.assertNotEqual(self.session.snapshot['lastReceipt']['openingId'], old['openingId'])
        self.notices.clear()
        revision = self.toast.property('eventRevision')
        self.c._on_runtime_event(old)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.toast.property('eventRevision'), revision)

    def interrupted_real_fit(self, reason):
        from client_live_runtime import _AlignmentHost
        entered, release = threading.Event(), threading.Event()
        original = self.r.original_fit
        def delayed(host, *args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise AssertionError('test did not release actual solver')
            return original(host, *args, **kwargs)
        self.dispatch('start_round')
        self.notices.clear()
        self.toast.dismiss()
        try:
            # Only delay the actual fit call. After release, the original fit
            # and runtime lifetime checks must prevent any late publication.
            with patch.object(_AlignmentHost, 'fit', delayed):
                self.session.dispatch('capture_now')
                self.assertTrue(entered.wait(2))
                self.r.runtime.invalidate(reason)
                APP.processEvents()
                self.assertEqual(self.failures(), [])
                self.assertNotIn(self.toast.property('notificationKind'), ('warning', 'error'))
                release.set()
                receipt = self.settle()
            self.assertTrue(receipt.get('stale'))
            self.assertEqual(self.failures(), [])
            self.assertEqual(self.errors, [])
        finally:
            release.set()

    def test_short_close_without_verdict_is_quiet_and_next_failure_still_reports(self):
        self.interrupted_real_fit('map-closed')
        self.dispatch('capture_now')
        self.assertEqual(self.failures(), [])
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')

    def test_short_cancel_without_verdict_is_quiet(self):
        self.interrupted_real_fit('cancel')

    def test_end_round_keeps_failure_summary_visible(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.notices.clear()
        self.dispatch('end_round')
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')
        self.assertEqual(self.notices[-1][0], '本次开图未能贴合')

    def test_native_end_round_preserves_real_solver_attempts_and_summary(self):
        self.dispatch('start_round')
        for _ in range(2):
            receipt = self.dispatch('capture_now')
            self.assertEqual(receipt['reason'], 'initial-structure-supported-chains-insufficient')
        self.notices.clear()
        self.errors.clear()
        self.c._native_action('end-round')
        self.settle()
        self.assertEqual(len(self.errors), 1)
        report = self.errors[0]['fitReport']
        self.assertEqual(report['attemptCount'], 2)
        self.assertEqual(report['retryCount'], 1)
        self.assertEqual(report['firstReason'], receipt['reason'])
        self.assertEqual(report['lastReason'], receipt['reason'])
        self.assertEqual(report['firstDiagnostics'], {'availability': 'not-provided-by-solver'})
        self.assertEqual(report['lastDiagnostics'], report['firstDiagnostics'])
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')
        self.assertIn('2 次', self.toast.property('notificationMessage'))

    def missing_grid_failure(self, action):
        from client_live_runtime import _AlignmentHost
        from csharp_grid_runtime import CSharpGridRuntime
        grid = CSharpGridRuntime(Path(self.temp.name) / 'missing-StarMap.Grid.exe', self.temp.name)
        self.r.runtime.grid_runtime = grid
        try:
            # Only route fit to the native search boundary. The real backend
            # detects its absent executable; _search, request_scope, worker
            # invalidation and receipt acceptance are all production code.
            with patch.object(_AlignmentHost, 'fit', side_effect=lambda *a, **kw:
                    grid._search(None, None, 1, None, lambda: False)):
                receipt = self.dispatch(action)
            self.assertTrue(grid.request_failed)
            self.assertEqual(receipt['exceptionType'], 'GridFailure')
            self.assertEqual(receipt['reason'], 'GridFailure: grid-executable-missing')
            self.assertEqual(self.r.runtime.snapshot()['message'], 'grid-computation-failed')
            self.assertGreater(self.r.runtime.snapshot()['epoch'], self.session.snapshot['epoch'])
            self.assertEqual(self.session.stale_results, 0)
            return receipt
        finally:
            self.r.runtime.grid_runtime = None
            grid.close()

    def test_actual_grid_scope_failure_on_refresh_reaches_qml(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.notices.clear()
        self.errors.clear()
        self.missing_grid_failure('refresh_map')
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationKind'), 'error')
        self.assertEqual(self.toast.property('notificationTitle'), '识别任务发生异常')
        self.assertIn('GridFailure', self.toast.property('notificationMessage'))
        self.assertTrue(self.toast.isVisible())
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)

    def test_actual_grid_scope_failure_during_first_capture_reaches_qml(self):
        self.dispatch('start_round')
        self.notices.clear()
        self.errors.clear()
        self.missing_grid_failure('capture_now')
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationKind'), 'error')

    def test_previous_grid_error_cannot_borrow_the_current_worker_generation(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.missing_grid_failure('refresh_map')
        old = self.session.snapshot
        self.missing_grid_failure('refresh_map')
        self.assertNotEqual(old['generation'], self.session.snapshot['generation'])
        self.notices.clear()
        self.errors.clear()
        self.c._notified.clear()  # Do not let dedup conceal a currentness bug.
        self.c._notify_session_transitions(old)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.errors, [])

    def test_cancelled_grid_worker_cannot_publish_its_late_scope_failure(self):
        from client_live_runtime import _AlignmentHost
        from csharp_grid_runtime import CSharpGridRuntime
        self.dispatch('start_round')
        self.dispatch('capture_now')
        grid = CSharpGridRuntime(Path(self.temp.name) / 'missing-StarMap.Grid.exe', self.temp.name)
        self.r.runtime.grid_runtime = grid
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise AssertionError('grid fit not released')
            return grid._search(None, None, 1, None, lambda: False)
        try:
            with patch.object(_AlignmentHost, 'fit', delayed):
                self.session.dispatch('refresh_map')
                self.assertTrue(entered.wait(2))
                self.session.cancelPending('superseded-grid-request')
                self.r.runtime.invalidate('map-closed')
                APP.processEvents()
                self.notices.clear()
                self.errors.clear()
                release.set()
                self.assertTrue(self.session._pool.waitForDone(4000))
                APP.processEvents()
            self.assertTrue(grid.request_failed)
            self.assertEqual(self.session.stale_results, 1)
            self.assertEqual(self.session.snapshot['lastReceipt'], {})
            self.assertEqual(self.notices, [])
            self.assertEqual(self.errors, [])
        finally:
            release.set()
            self.r.runtime.grid_runtime = None
            grid.close()

    def use_manual_s1_runtime(self, *, observed=True):
        from test_s1_manual_floor_runtime import ManualS1FloorRuntime
        self.session.changed.disconnect(self.c._session_changed)
        self.session.shutdown()
        self.session._pool.waitForDone(4000)
        self.r.runtime.set_event_callback(None)
        self.r.runtime.shutdown()
        self.r.doCleanups()
        self.r = ManualS1FloorRuntime()
        self.r.setUp()
        if not observed:
            self.r.registry.engine.qt_native_context = SimpleNamespace()
        self.session = SessionService(self.r.runtime)
        self.c.runtime, self.c.session = self.r.runtime, self.session
        self.c._applied_context = ('nightmare', 'shared-structure', 's1')
        self.c._last_round_active = False
        self.session.changed.connect(self.c._session_changed)
        self.r.runtime.set_event_callback(self.c.runtimeEvent.emit)
        self.r.fit_result = dict(success=False, reason='initial-structure-supported-chains-insufficient')

    def test_manual_s1_confirm_failure_and_close_reach_qml_in_both_capture_paths(self):
        for observed in (True, False):
            with self.subTest(observed=observed):
                # Existing manual fixture replaces floor pixels, display and
                # numerical fit only. Dispatch/admission, Session, reporting,
                # close lifetime and the shipped QML are actual consumers.
                self.use_manual_s1_runtime(observed=observed)
                self.assertTrue(self.dispatch('prepare_manual_alignment')['success'])
                self.notices.clear()
                self.errors.clear()
                failed = self.dispatch('confirm_route', dict(mapId='fixture-map'))
                self.assertFalse(failed['success'])
                self.assertTrue(failed['fitReportingDeferred'])
                self.assertTrue(failed['openingId'])
                self.assertEqual(self.failures(), [])
                self.dispatch('close_map')
                self.assertEqual(len(self.failures()), 1)
                self.assertEqual(len(self.errors), 1)
                self.assertEqual(self.errors[0]['fitReport']['attemptCount'], 1)
                self.assertEqual(self.errors[0]['fitReport']['lastReason'], failed['reason'])
                self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')
                self.assertTrue(self.toast.isVisible())
                self.assertEqual(self.session.stale_results, 0)

    def test_manual_s1_short_close_before_numerical_verdict_is_quiet(self):
        self.use_manual_s1_runtime()
        self.dispatch('prepare_manual_alignment')
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(4):
                raise AssertionError('manual fit not released')
            return dict(self.r.fit_result)
        self.r.fitter.side_effect = delayed
        self.notices.clear()
        self.errors.clear()
        self.toast.dismiss()
        try:
            self.session.dispatchWithPayload('confirm_route', dict(mapId='fixture-map'))
            self.assertTrue(entered.wait(2))
            self.r.runtime.invalidate('map-closed')
            APP.processEvents()
            self.assertFalse(self.toast.isVisible())
            self.assertEqual(self.failures(), [])
            release.set()
            self.assertTrue(self.settle().get('stale'))
            self.assertEqual(self.failures(), [])
            self.assertEqual(self.errors, [])
        finally:
            release.set()

    def test_worker_exception_reaches_qml_from_passive_refresh(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.notices.clear()
        self.errors.clear()
        # An actual _RuntimeWork catches this dispatch exception. It does not
        # inject a receipt or bypass Session's current-result acceptance.
        with patch.object(self.r.runtime, 'dispatch', side_effect=OSError('worker-test-error')):
            receipt = self.dispatch('refresh_map')
        self.assertTrue(receipt['error'])
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('worker-test-error', self.errors[0]['raw'])
        self.assertEqual(self.toast.property('notificationKind'), 'error')
        self.assertEqual(self.toast.property('notificationTitle'), '识别任务发生异常')
        self.assertTrue(self.toast.isVisible())
        with patch.object(self.r.runtime, 'dispatch', side_effect=OSError('different-worker-detail')):
            self.dispatch('refresh_map')
        self.assertEqual(len(self.failures()), 1)

    def test_old_accepted_state_does_not_reappear_on_new_frame_or_resource(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        old = self.session.snapshot
        self.dispatch('close_map')
        self.dispatch('capture_now')
        self.notices.clear()
        self.errors.clear()
        self.c._notify_session_transitions(old)
        self.assertEqual(self.failures(), [])
        self.assertEqual(self.errors, [])
        # Explicit resource mismatch exercises the consumer's queued-state
        # recheck independently of the owning runtime's resource invalidation.
        state = dict(self.session.snapshot, resourceIdentity='old-resource')
        current = dict(self.r.runtime.snapshot(), resourceIdentity='new-resource')
        with patch.object(self.r.runtime, 'snapshot', return_value=current):
            self.c._notify_session_transitions(state)
        self.assertEqual(self.failures(), [])
        state = dict(self.session.snapshot)
        state['lastReceipt'] = dict(state['lastReceipt'], resourceIdentity='old-resource')
        with patch.object(self.r.runtime, 'snapshot', return_value=current):
            state['resourceIdentity'] = 'new-resource'
            self.c._notify_session_transitions(state)
        self.assertEqual(self.failures(), [])

    def test_pending_candidate_does_not_interrupt_with_progress_toasts(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        state = self.r.runtime.snapshot()
        state['lastReceipt'] = dict(action='refresh_map', success=True, posePending=True,
            identityUnresolved=True, reason='s1-structure-candidates-awaiting-confirmation')
        self.notices.clear()
        self.errors.clear()
        # Boundary: synthetic already-accepted candidate receipt, not a claim
        # that the S1 scanner recognized this synthetic S0 fixture.
        self.c._notify_session_transitions(state)
        self.c._notify_session_transitions(state)
        self.assertEqual(self.notices, [])
        self.assertEqual(self.errors, [])

    def test_resource_refusal_keeps_specific_reason_on_passive_refresh(self):
        self.dispatch('start_round')
        self.dispatch('capture_now')
        self.notices.clear()
        self.errors.clear()
        state = self.r.runtime.snapshot()
        # Boundary: an upstream resource-readiness refusal. This test covers
        # the passive feedback consumer, not the package readiness algorithm.
        state['lastReceipt'] = self.r.runtime._receipt('refresh_map',
            dict(_diagnosticOpeningId=state['openingId']), success=False, reason='mode-resources-unavailable')
        self.c._notify_session_transitions(state)
        self.c._notify_session_transitions(state)
        self.assertEqual(self.failures(), [])
        self.dispatch('close_map')
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.toast.property('notificationTitle'), '本次开图未能贴合')
        self.assertIn('地图资源', self.toast.property('notificationMessage'))
        self.assertEqual(json.loads(self.errors[0]['raw'])['lastReason'], 'mode-resources-unavailable')
        self.assertEqual(self.toast.property('notificationKind'), 'warning')
        self.dispatch('end_round')
        self.notices.clear()
        state = self.session.snapshot
        state['lastReceipt'] = dict(action='inspect_engine_resources', success=False, reason='mode-resources-unavailable')
        self.c._notify_session_transitions(state)
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.toast.property('notificationTitle'), '当前模式资源不可用')


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromTestCase(FailureNotificationFlowTests)

