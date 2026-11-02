"""Synthetic full-client frames through the real preparation/receipt/message chain."""
import unittest
from unittest.mock import patch
import numpy as np
from client_live_runtime import LiveRuntime
from client_region_capture import ClientCaptureContext, RegionCapture
from client_problem_messages import operation_problem
from test_client_live_runtime import FixtureCapture, fixture_engine


class SizedCapture(FixtureCapture):
    generation = 1
    def __init__(self, width, height):
        super().__init__()
        self.frame = np.zeros((height, width, 3), np.uint8)
        self.roi = (0, 0, width, height)
    def capture(self, cancelled=None):
        self.count += 1
        h, w = self.frame.shape[:2]
        return RegionCapture(self.frame.copy(), self.roi, self.roi,
            ClientCaptureContext((w, h), (0, 0, w, h), 96), 'synthetic-fixture')
    def is_current(self, context, generation=None):
        return generation == self.generation


class WindowFeedbackTests(unittest.TestCase):
    def run_map(self, width, height, capture=None):
        capture = capture or SizedCapture(width, height)
        runtime = LiveRuntime(engine_factories={'nightmare': fixture_engine},
            capture_provider=capture, evidence_kind='synthetic')
        runtime.dispatch('start_round')
        receipt = runtime.dispatch('map', {'generation': 31})
        return runtime, receipt, operation_problem(receipt, runtime.snapshot())

    def test_other_problem_branches_keep_preparation_diagnostic_object(self):
        for reason in ('current-frame-floor-conflict', 'capture-context-changed',
                       'candidate-awaiting-confirmation', 'unknown-operation-failure'):
            with self.subTest(reason=reason):
                problem = operation_problem(dict(reason=reason, observedFloor='1', targetFloor='2'), {})
                self.assertEqual(problem['preparationObservation'], {})
                self.assertIsInstance(problem['preparationObservation'], dict)
                if reason == 'current-frame-floor-conflict':
                    self.assertEqual(problem['title'], '目标楼层与当前画面不一致')
                    self.assertIn('当前画面仍是 F1，目标为 F2', problem['summary'])

    def test_width_height_aspect_and_valid_size(self):
        for w, h, reason, title in [(999, 600, 'capture-shape-width', '游戏窗口尺寸过小'),
                (1000, 599, 'shape-accepted', '游戏窗口尺寸过小'),
                (1000, 801, 'capture-shape-aspect', '游戏窗口比例过窄'),
                (1000, 800, 'shape-accepted', '地图画面尚未确认'),
                (1000, 600, 'shape-accepted', '地图画面尚未确认')]:
            with self.subTest(size=(w,h)):
                runtime, receipt, problem = self.run_map(w,h)
                self.assertEqual(receipt['reason'], 'map-open-preparation-deadline')
                evidence = receipt['preparationObservation']
                self.assertEqual((evidence['frameWidth'], evidence['frameHeight']), (w,h))
                self.assertEqual(evidence['shapeReason'], reason)
                self.assertEqual(problem['title'], title)
                self.assertIn(f'{w} × {h}', problem['summary'])
                self.assertNotIn('16:9', problem['summary'])
                self.assertFalse(runtime.snapshot()['core']['displayAuthorized'])

    def test_valid_size_unlocalized_floor_is_not_small(self):
        with patch('map_interface_observation.detect_map_interface', return_value={'detected': True}):
            _, receipt, problem = self.run_map(1280,720)
        self.assertEqual(receipt['reason'], 'current-floor-unavailable')
        self.assertEqual(problem['title'], '尚未定位楼层控件')
        self.assertIn('1280 × 720', problem['summary'])
        self.assertNotIn('过小', problem['summary'])

    def test_no_frame_never_invents_size(self):
        capture = SizedCapture(999,600)
        capture.capture = lambda cancelled=None: (_ for _ in ()).throw(OSError('fixture unavailable'))
        _, receipt, problem = self.run_map(999,600,capture)
        self.assertFalse(receipt.get('preparationObservation'))
        self.assertNotIn('999', problem['summary'])

    def test_generation_change_discards_geometry(self):
        capture = SizedCapture(999,600)
        original = capture.capture
        def changed(cancelled=None):
            result = original(cancelled)
            capture.generation += 1
            return result
        capture.capture = changed
        _, receipt, problem = self.run_map(999,600,capture)
        self.assertFalse(receipt.get('preparationObservation'))
        self.assertNotIn('999', problem['summary'])

    def test_failed_latest_capture_does_not_reuse_earlier_small_frame(self):
        capture = SizedCapture(999,600)
        original = capture.capture
        def failing(cancelled=None):
            if capture.count:
                raise OSError('latest capture failed')
            return original(cancelled)
        capture.capture = failing
        _, receipt, problem = self.run_map(999,600,capture)
        self.assertFalse(receipt.get('preparationObservation'))
        self.assertNotIn('999', problem['summary'])

    def test_new_request_cannot_reuse_previous_size(self):
        capture = SizedCapture(999,600)
        runtime, first, _ = self.run_map(999,600,capture)
        self.assertEqual(first['preparationObservation']['frameWidth'],999)
        capture.capture = lambda cancelled=None: (_ for _ in ()).throw(OSError('no frame'))
        second = runtime.dispatch('map', {'generation': 32})
        self.assertFalse(second.get('preparationObservation'))
        self.assertEqual(second['requestContext']['generation'],32)

    def test_difficult_does_not_inherit_nightmare_size_limits(self):
        from map_interface_observation import map_preparation_diagnostic
        row = map_preparation_diagnostic(np.zeros((599,999,3),np.uint8),
            {'mapOpen':False}, {}, mode='difficult')
        self.assertNotIn('shapeReason',row)
        problem = operation_problem(dict(reason='map-open-preparation-deadline', preparationObservation=row),{})
        self.assertEqual(problem['title'],'地图画面尚未确认')

    def test_qt_notification_and_recent_problem_receive_same_evidence(self):
        from test_client_coordinator import CoordinatorTests
        harness = CoordinatorTests('test_round_notifications_follow_preparation_and_activation_once_despite_stale_session')
        harness.setUpClass()
        harness.setUp()
        try:
            _, receipt, expected = self.run_map(999,600)
            notices, problems = [], []
            harness.coordinator.notificationRequested.connect(lambda *args: notices.append(args))
            harness.coordinator.errorReported.connect(problems.append)
            state = dict(harness.session.snapshot, lastReceipt=receipt)
            harness.coordinator._notify_session_transitions(state)
            self.assertEqual(notices[-1],(expected['title'],expected['summary'],expected['kind']))
            self.assertEqual(problems[-1]['preparationObservation'],receipt['preparationObservation'])
        finally:
            harness.doCleanups()

if __name__ == '__main__':
    unittest.main()
