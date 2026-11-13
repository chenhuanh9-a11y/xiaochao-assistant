"""Synthetic solver results; no capture/game IO. Clock is fully controlled."""
import copy
import unittest
from test_client_live_runtime import LiveRuntimeContracts
from test_client_coordinator import CoordinatorTests


class FitRuntimeTests(LiveRuntimeContracts):
    def setUp(self):
        super().setUp()
        self.events = []
        self.runtime.set_event_callback(self.events.append)
        self.now = 1.0
        self.runtime._monotonic = lambda: self.now
        self.good = copy.deepcopy(self.fit)

    def fail_fit(self, reason='initial-structure-ambiguous'):
        self.fit = dict(success=False, reason=reason)
        return self.dispatch('capture_now')

    def finals(self):
        return [e for e in self.events if e['event'] == 'map-fit-incomplete']

    def test_fixture_assertions_really_raise(self):
        self.assertIs(type(self).fail, unittest.TestCase.fail)
        with self.assertRaises(AssertionError):
            self.assertEqual('deliberate-negative-control', 'not-equal')

    def test_fail_success_close_is_quiet(self):
        self.dispatch('start_round')
        for _ in range(3):
            self.assertTrue(self.fail_fit().get('fitReportingDeferred'))
        self.fit = self.good
        self.assertTrue(self.dispatch('capture_now')['success'])
        self.dispatch('close_map')
        self.assertEqual(self.finals(), [])
        self.assertEqual(len([e for e in self.events if e['event'] == 'map-fit-retry']), 3)

    def test_success_then_passive_map_disappearance_is_not_an_unfinished_opening(self):
        from unittest.mock import patch
        self.dispatch('start_round')
        self.assertTrue(self.dispatch('capture_now')['success'])
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.dispatch('refresh_map')
            self.dispatch('refresh_map')
        self.assertEqual(self.finals(), [])

    def test_new_known_floor_failure_then_unknown_does_not_reuse_old_floor_success(self):
        from unittest.mock import patch
        from test_client_live_runtime import synthetic_read
        self.dispatch('start_round')
        self.assertTrue(self.dispatch('capture_now')['success'])
        self.fit = dict(success=False, reason='initial-structure-supported-chains-insufficient')
        with patch('floor_control_region.locate_vertical_floor_controls', return_value=synthetic_read(selected=2)):
            failed = self.dispatch('refresh_map')
        self.assertFalse(failed['success'])
        self.assertEqual(failed['reason'], self.fit['reason'])
        self.assertEqual(self.runtime.snapshot()['floor'], '2')
        with patch('floor_control_region.locate_vertical_floor_controls', return_value=dict(selectedFloor=None)):
            self.assertEqual(self.dispatch('refresh_map')['reason'], 'current-floor-unavailable')
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.dispatch('refresh_map')
            self.dispatch('refresh_map')
        self.assertEqual(len(self.finals()), 1)
        self.assertTrue(self.finals()[0]['terminal'])

    def test_close_finalizes_once_with_real_reasons_and_resets(self):
        self.dispatch('start_round')
        self.fail_fit()
        self.now = 3.9
        self.assertFalse(self.runtime.snapshot()['fitReportingWaiting'])
        self.assertFalse(any(e['event'] == 'map-fit-waiting' for e in self.events))
        self.now = 4.1
        self.fail_fit('initial-structure-current-geometry-failed')
        self.assertEqual(len([e for e in self.events if e['event'] == 'map-fit-waiting']), 1)
        self.dispatch('close_map'); self.dispatch('close_map')
        self.assertEqual(len(self.finals()), 1)
        report = self.finals()[0]['fitReport']
        self.assertEqual(report['attemptCount'], 2)
        self.assertEqual(report['retryCount'], 1)
        self.assertEqual(report['firstReason'], 'initial-structure-ambiguous')
        self.assertEqual(report['lastReason'], 'initial-structure-current-geometry-failed')
        self.fail_fit(); self.dispatch('close_map')
        self.assertEqual(self.finals()[-1]['fitReport']['attemptCount'], 1)

    def test_both_modes_and_diagnostic_attempts(self):
        from unittest.mock import Mock
        for mode in ('nightmare', 'difficult'):
            with self.subTest(mode=mode):
                self.dispatch('end_round')
                self.assertTrue(self.dispatch('set_context', mode=mode)['success'])
                self.runtime._diagnostics = Mock()
                self.dispatch('start_round')
                self.fail_fit(); self.fail_fit(); self.dispatch('close_map')
                attempts = [c for c in self.runtime._diagnostics.record.call_args_list
                    if c.args == ('runtime-result',) and c.kwargs.get('reason') == 'initial-structure-ambiguous']
                self.assertEqual(len(attempts), 2)
                self.assertEqual(self.finals()[-1]['fitReport']['attemptCount'], 2)
                self.assertEqual(self.finals()[-1]['fitReport']['firstDiagnostics'],
                                 {'availability': 'not-provided-by-solver'})

    def test_late_solver_cannot_enter_new_opening(self):
        import threading
        from unittest.mock import patch
        from client_live_runtime import _AlignmentHost
        entered, release = threading.Event(), threading.Event()
        self.dispatch('start_round')
        def delayed(*a, **kw):
            entered.set()
            self.assertTrue(release.wait(3))
            return dict(success=False, reason='initial-structure-ambiguous')
        result = []
        with patch.object(_AlignmentHost, 'fit', side_effect=delayed):
            worker = threading.Thread(target=lambda: result.append(self.dispatch('capture_now')))
            worker.start()
            self.assertTrue(entered.wait(3))
            self.runtime.invalidate('map-closed')
            release.set(); worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertTrue(result[0].get('stale'))
        self.assertEqual(len(self.finals()), 1)
        first = self.finals()[0]
        self.assertNotIn('fitReport', first)
        self.assertEqual(first['lastReason'], '')
        self.fail_fit(); self.dispatch('close_map')
        self.assertEqual(self.finals()[-1]['fitReport']['attemptCount'], 1)
        self.assertNotEqual(self.finals()[-1]['openingId'], first['openingId'])

    def test_unreported_floor_change_does_not_attribute_old_failure_to_new_floor(self):
        self.dispatch('start_round'); self.fail_fit()
        # A changed floor with no subsequent solve must not finalize old evidence
        # as a failure of the newly selected floor.
        with self.runtime._lock:
            self.runtime._floor = '2'
        self.dispatch('close_map')
        self.assertEqual(len(self.finals()), 1)
        self.assertNotIn('fitReport', self.finals()[0])
        self.assertEqual(self.finals()[0]['lastReason'], '')

    def test_real_solver_diagnostics_are_preserved(self):
        self.dispatch('start_round')
        self.fit = dict(success=False, reason='initial-structure-ambiguous',
                        strategy='fixture-real-strategy', elapsedMs=17)
        self.dispatch('capture_now'); self.dispatch('close_map')
        self.assertEqual(self.finals()[0]['fitReport']['lastDiagnostics'],
                         dict(strategy='fixture-real-strategy', elapsedMs=17))

    def test_close_after_commit_before_pose_notification_is_not_failure(self):
        from unittest.mock import patch
        self.dispatch('start_round'); self.fail_fit()
        self.fit = self.good
        original = self.runtime._set_pose_phase
        def close_before_notification(status, *args, **kwargs):
            if status == 'valid':
                # Deterministic actual commit -> close -> late pose notification.
                self.runtime.invalidate('map-closed')
            return original(status, *args, **kwargs)
        with patch.object(self.runtime, '_set_pose_phase', side_effect=close_before_notification):
            self.dispatch('capture_now')
        self.assertEqual(self.finals(), [])
        self.assertFalse(self.runtime.snapshot()['mapOpen'])
        self.fail_fit(); self.dispatch('close_map')
        self.assertEqual(self.finals()[-1]['fitReport']['attemptCount'], 1)

    def test_real_resource_failure_not_deferred(self):
        self.dispatch('start_round')
        self.assertFalse(self.fail_fit('initial-structure-resource-unavailable').get('fitReportingDeferred', False))
        self.dispatch('close_map')
        self.assertEqual(len(self.finals()), 1)
        self.assertNotIn('fitReport', self.finals()[0])
        self.assertEqual(self.finals()[0]['lastReason'], 'initial-structure-resource-unavailable')

    def test_floor_or_identity_invalidation_discards_old_failure_evidence(self):
        for reason in ('game-floor-control-intent', 'identity-reselection'):
            with self.subTest(reason=reason):
                self.dispatch('start_round'); self.fail_fit()
                self.events.clear()
                self.runtime.invalidate(reason)
                self.runtime.invalidate('map-closed')
                self.assertEqual(self.finals(), [])

    def test_end_round_summarizes_partial_opening_once(self):
        self.dispatch('start_round'); self.fail_fit()
        self.runtime.invalidate('end_round')
        self.runtime.invalidate('map-closed')
        self.assertEqual(len(self.finals()), 1)
        self.assertEqual(self.finals()[0]['terminationReason'], 'end_round')
        self.assertEqual(self.finals()[0]['fitReport']['attemptCount'], 1)

    def test_foreground_interruption_close_does_not_claim_unfinished_solver_attempts(self):
        self.dispatch('start_round'); self.fail_fit()
        self.runtime.invalidate('foreground-lost')
        self.runtime.invalidate('map-closed')
        self.assertEqual(len(self.finals()), 1)
        self.assertNotIn('fitReport', self.finals()[0])


class FitCoordinatorTests(CoordinatorTests):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_final_event_is_one_problem_and_reason_toast_without_input_reset(self):
        errors, notices = [], []
        self.coordinator.errorReported.connect(errors.append)
        self.coordinator.notificationRequested.connect(lambda *a: notices.append(a))
        event = dict(event='map-fit-incomplete', mode='nightmare', roundId=1, cycleId=1, epoch=3,
            fitReport=dict(attemptCount=2, firstReason='initial-structure-ambiguous',
                           lastReason='initial-structure-current-geometry-failed'))
        self.coordinator._on_runtime_event(event)
        self.coordinator._on_runtime_event(event)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['title'], '本次开图未能贴合')
        self.assertIn('2 次', errors[0]['summary'])
        self.assertEqual(notices, [(errors[0]['title'], errors[0]['summary'], 'warning')])
        self.input.resetMapInput.assert_not_called()

    def test_real_exception_still_reports_immediately(self):
        errors, notices = [], []
        self.coordinator.errorReported.connect(errors.append)
        self.coordinator.notificationRequested.connect(lambda *a: notices.append(a))
        self.coordinator._notify_session_transitions(dict(lastReceipt=dict(success=False,
            action='capture_now', reason='capture-error', error=True)))
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(notices), 1)

    def test_deferred_receipt_explains_reason_once_without_persisting_progress(self):
        errors, notices = [], []
        self.coordinator.errorReported.connect(errors.append)
        self.coordinator.notificationRequested.connect(lambda *a: notices.append(a))
        self.coordinator._last_round_active = False
        state = dict(lastReceipt=dict(success=False,
            action='capture_now', reason='initial-structure-ambiguous', fitReportingDeferred=True))
        self.coordinator._notify_session_transitions(state)
        self.coordinator._notify_session_transitions(dict(state, lastReceipt=dict(state['lastReceipt'], action='refresh_map')))
        self.assertEqual(errors, [])
        self.assertEqual(notices, [('地图位置仍在核对', '存在多个结构候选，请保持地图打开并等待画面稳定。', 'warning')])


def load_tests(loader, tests, pattern):
    # Reuse fixture setup, not all inherited test methods.
    return unittest.TestSuite(cls(name) for cls in (FitRuntimeTests, FitCoordinatorTests, FitCloseConsumerTests)
        for name in cls.__dict__ if name.startswith('test_'))


class FitCloseConsumerTests(unittest.TestCase):
    """Actual problem store + coordinator; capture/solver and native UI are fixtures."""
    @classmethod
    def setUpClass(cls):
        FitCoordinatorTests.setUpClass()

    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        from client_problems import ProblemsService
        self.r = FitRuntimeTests('test_fixture_assertions_really_raise')
        self.r.setUp(); self.addCleanup(self.r.doCleanups)
        self.c = FitCoordinatorTests('test_deferred_receipt_explains_reason_once_without_persisting_progress')
        self.c.setUp(); self.addCleanup(self.c.tearDown)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.problems = ProblemsService(SimpleNamespace(work_root=self.temp.name))
        self.c.coordinator.errorReported.connect(self.problems.record)
        self.notices = []
        self.c.coordinator.notificationRequested.connect(
            lambda *args: self.notices.append(args) if args[2] in {"warning", "error"} else None)
        self.r.dispatch('start_round')
        self.c.coordinator._last_round_active = True
        self.c.runtime.snapshot = self.r.runtime.snapshot
        self.r.runtime.set_event_callback(self.c.coordinator._on_runtime_event)
        self.addCleanup(self.drain)

    def drain(self):
        self.problems.shutdown()
        import time
        deadline = time.monotonic() + 3
        while self.problems.pendingWrites and time.monotonic() < deadline:
            self.problems.pool.waitForDone()
            self.c.app.processEvents()
        self.assertFalse(self.problems.pendingWrites)

    def consume(self, receipt):
        state = self.r.runtime.snapshot()
        state['lastReceipt'] = receipt
        self.c.coordinator._notify_session_transitions(state)

    def auto_probe(self):
        # Use the production coordinator timer entry, with only the worker
        # scheduling replaced by synchronous fixture dispatch.
        self.c.session.snapshot = self.r.runtime.snapshot()
        self.c.session.dispatch.reset_mock()
        self.c.session.dispatch.side_effect = lambda action: self.consume(self.r.dispatch(action))
        self.c.coordinator._track_map()
        self.c.session.dispatch.assert_called_once_with('refresh_map')

    def test_automatic_probe_close_has_one_problem_and_final_reason_toast(self):
        from unittest.mock import patch
        self.consume(self.r.fail_fit())
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.auto_probe()
            self.assertEqual(self.problems.recent, [])
            self.assertEqual(len(self.notices), 1)
            self.auto_probe()
        self.assertEqual([p['title'] for p in self.problems.recent], ['本次开图未能贴合'])
        self.assertEqual(self.problems.recent[0]['count'], 1)
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(self.notices[-1][0], '本次开图未能贴合')

    def test_capture_close_has_one_problem_and_final_reason_toast(self):
        from unittest.mock import patch
        self.consume(self.r.fail_fit())
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            self.consume(self.r.dispatch('capture_now'))
        self.assertEqual([p['title'] for p in self.problems.recent], ['本次开图未能贴合'])
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(self.notices[-1][0], '本次开图未能贴合')

    def test_map_not_observed_without_fit_segment_still_reports(self):
        from unittest.mock import patch
        with patch('map_interface_observation.detect_map_interface', return_value=dict(detected=False)):
            receipt = self.r.dispatch('capture_now')
            self.consume(receipt)
        self.assertTrue(receipt.get('fitReportingDeferred', False))
        self.assertEqual(len(self.problems.recent), 1)
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0][0], '本次开图未完成')

    def test_capture_exception_during_fit_segment_still_reports(self):
        from unittest.mock import patch
        self.consume(self.r.fail_fit())
        with patch.object(self.r.capture, 'capture', side_effect=OSError('capture-test-error')):
            receipt = self.r.dispatch('capture_now')
            self.consume(receipt)
        self.assertTrue(receipt.get('error'))
        self.assertFalse(receipt.get('fitReportingDeferred', False))
        self.assertEqual(len(self.problems.recent), 1)
        self.assertEqual(len(self.notices), 2)
        self.assertEqual(self.notices[-1][2], 'error')
