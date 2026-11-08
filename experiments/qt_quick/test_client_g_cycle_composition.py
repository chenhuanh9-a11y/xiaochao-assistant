"""Integrated evidence/startup/retirement contracts; synthetic and offscreen only."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
import client
from client_live_runtime import LiveRuntime
from diagnostic_privacy import safe_record
from test_client_live_runtime import FixtureCapture, fixture_engine


class EvidenceCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_evidence_flags_preserve_compatibility_report_and_first_frame_boundary(self):
        frame_ready = client.QtClient._main_frame_ready
        for enabled, full in ((False, False), (True, False), (True, True)):
            with self.subTest(enabled=enabled, full=full), tempfile.TemporaryDirectory() as folder:
                report = dict(status='retirement-deferred', reasons=['retired-mode-still-required'])
                args = SimpleNamespace(sandbox=Path(folder), probe_output=None,
                    resource_startup_report=report, g_cycle_evidence=enabled, g_cycle_full_input=full)
                instance = Mock()
                instance._closed, instance._main_frame_seen = False, False
                observations, supplied = [], {}

                def observe_frame():
                    try:
                        observations.append((close.call_count,
                            instance.resources.setStartupCompatibilityReport.call_args))
                        frame_ready(instance)
                        observations.append(close.call_count)
                    finally:
                        self.app.quit()

                def construct(*a, **kw):
                    supplied.update(kw)
                    QTimer.singleShot(0, observe_frame)
                    return instance

                with patch.object(client, 'PRODUCTION', False), \
                     patch.object(client, 'owned_sandbox', side_effect=lambda p: p), \
                     patch.object(client, 'QtClient', side_effect=construct), \
                     patch('qt_startup_splash.close_startup_splash') as close:
                    self.assertEqual(client._run_primary_client(self.app, args, Mock()), 0)
                self.assertIs(supplied['g_cycle_evidence'], enabled)
                self.assertIs(supplied['g_cycle_full_input'], full)
                self.assertEqual(observations[0][0], 0)
                self.assertEqual(observations[0][1].args, (report,))
                self.assertEqual(observations[1], 1)
                instance.performance.sample.assert_called_once()
                instance.resources.setStartupCompatibilityReport.assert_called_once_with(report)
                instance.shutdown.assert_called_once()

    def test_performance_and_evidence_whitelists_coexist_without_private_fields(self):
        record = dict(stage='g-cycle-evidence', reason='saved-missing-input',
            performance=dict(scope='own-process-only', workingSetBytes=123,
                startupMilestones=[dict(stage='resource-check'), dict(stage='main-frame-submitted')],
                frameEndpoint='frame-submitted-not-visible-pixels',
                gpuMeasured=False, childProcessesMeasured=False, path='private-path'),
            gCycleEvidence=dict(evidencePhase='completion', status='saved-missing-input',
                caseId='a' * 32, diagnosticId='b' * 32, outcome='failure',
                missingReason='no-algorithm-roi', path='private-path',
                commandLine='private-command', username='private-user'))
        clean = safe_record(record)
        self.assertEqual(clean['performance']['workingSetBytes'], 123)
        self.assertEqual(clean['performance']['startupMilestones'], record['performance']['startupMilestones'])
        self.assertEqual(clean['performance']['frameEndpoint'], 'frame-submitted-not-visible-pixels')
        self.assertEqual(clean['gCycleEvidence']['caseId'], 'a' * 32)
        self.assertEqual(clean['gCycleEvidence']['missingReason'], 'no-algorithm-roi')
        self.assertEqual(clean['gCycleEvidence']['evidencePhase'], 'completion')
        self.assertNotIn('private-', json.dumps(clean))

    def test_retired_runtime_rejects_evidence_dispatch_without_capture_or_fit(self):
        capture = FixtureCapture()
        runtime = LiveRuntime(engine_factories={'nightmare': lambda: fixture_engine()},
                              capture_provider=capture, evidence_kind='synthetic')
        self.assertTrue(runtime.dispatch('set_context', dict(season='s1', mode='difficulty2'))['success'])
        recorder = Mock(accepting=True)
        recorder.submit.return_value = dict(status='queued')
        runtime.g_cycle_evidence = recorder
        with patch.object(capture, 'capture', side_effect=AssertionError('retired capture')) as acquire, \
             patch('client_live_runtime._AlignmentHost.fit', side_effect=AssertionError('retired fit')) as fit:
            for action in ('map', 'close_map', 'recover_map_open'):
                result = runtime.dispatch(action, dict(openIntent=True, inputSequence=1))
                self.assertFalse(result['success'], result)
                self.assertEqual(result['reason'], 'retired-mode')
        acquire.assert_not_called()
        fit.assert_not_called()
        self.assertEqual(capture.enable_count, 0)
        self.assertFalse(runtime.snapshot()['roundActive'])
        self.assertEqual(recorder.submit.call_count, 3)
        for call in recorder.submit.call_args_list:
            self.assertIsNone(call.args[0])
            self.assertEqual(call.args[1]['reason'], 'retired-mode')
            self.assertEqual(call.args[1]['outcome'], 'failure')
        self.assertTrue(runtime.dispatch('set_context', dict(season='s0', mode='nightmare'))['success'])


if __name__ == '__main__':
    unittest.main()
