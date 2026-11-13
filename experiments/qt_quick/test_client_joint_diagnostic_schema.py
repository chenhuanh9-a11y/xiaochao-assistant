"""Joined feature evidence through coordinator, ProblemsService, disk and export.

Owned temporary data and synthetic events; not a Windows/game capture test.
"""
import json
from types import SimpleNamespace
import unittest
import zipfile

from client_coordinator import ClientCoordinator
from client_fit_reporting import FitReporting
from client_problem_messages import operation_problem
from diagnostic_export import export_safe_diagnostic_bundle
from diagnostic_privacy import safe_record
import test_client_problems as fixtures


class JointDiagnosticSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProblemTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.ProblemTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service

    def test_fit_events_reach_problem_trace_disk_and_export(self):
        report, _ = FitReporting().failure((1,), 'structure-current-geometry-failed', 3.5,
            dict(availability='not-provided-by-solver'))
        report['lastDiagnostics'].update(username='PrivatePlayer', path='C:/PrivatePlayer',
            strategy='PrivatePlayer', selectedRouteCurrentFrameResidual=dict(verified=False, secret='PrivatePlayer'))
        target = SimpleNamespace(_closed=False, _closing=False, _diagnostics=None,
            _notified=set(), runtime=SimpleNamespace(snapshot=lambda: {}),
            traceReported=SimpleNamespace(emit=self.service.recordTrace),
            errorReported=SimpleNamespace(emit=self.service.record),
            notificationRequested=SimpleNamespace(emit=lambda *args: None))
        target._trace = lambda stage, **values: ClientCoordinator._trace(target, stage, **values)
        target._failure_notice_scope = ClientCoordinator._failure_notice_scope
        target._failure_event_is_current = lambda event: ClientCoordinator._failure_event_is_current(target, event)
        for name in ('map-fit-retry', 'map-fit-waiting', 'map-fit-incomplete'):
            ClientCoordinator._on_runtime_event(target, dict(event=name,
                reason='structure-current-geometry-failed', mode='nightmare', roundId=1,
                cycleId=1, epoch=1, generation=1,
                **({} if name == 'map-fit-waiting' else {'fitReport': report})))
        self.fixture.drain()
        rows = [json.loads(line) for line in self.service.log_path.read_bytes().splitlines()]
        traces = {row.get('stage'): row for row in rows if row.get('stage', '').startswith('map-fit-')}
        self.assertEqual(set(traces), {'map-fit-retry', 'map-fit-waiting', 'map-fit-incomplete'})
        self.assertNotIn('fitReport', traces['map-fit-waiting'])
        for name in ('map-fit-retry', 'map-fit-incomplete'):
            row = traces[name]
            self.assertEqual(row['fitReport']['attemptCount'], 1)
            self.assertEqual(row['fitReport']['firstReason'], 'structure-current-geometry-failed')
            self.assertEqual(row['fitReport']['firstDiagnostics']['availability'], 'not-provided-by-solver')
        problem = next(row for row in rows if row.get('reason') == 'map-fit-incomplete')
        self.assertEqual(problem['fitReport']['retryCount'], 0)
        result = export_safe_diagnostic_bundle(self.fixture.root, self.fixture.root/'work/nightmare-live',
            app_version='1.0.6', build_fingerprint='TEST0001', destination=self.fixture.root/'joint.zip',
            summary={'recentProblems': self.service.recent})
        with zipfile.ZipFile(result['path']) as archive:
            exported = b''.join(archive.read(name) for name in archive.namelist() if name.endswith('.jsonl'))
        self.assertIn(b'map-fit-incomplete', exported)
        self.assertIn(b'fitReport', exported)
        self.assertNotIn(b'PrivatePlayer', exported)
        self.assertNotIn('PrivatePlayer', self.service.log_path.read_text())

    def test_retry_counters_do_not_turn_trace_into_per_frame_log(self):
        for count in range(1, 101):
            self.service.recordTrace(dict(stage='map-fit-retry', role='runtime',
                reason='structure-current-geometry-failed', round=1, cycle=1,
                fitReport=dict(attemptCount=count, retryCount=count-1, lastAt=float(count))))
        self.service.recordTrace(dict(stage='map-fit-incomplete', role='runtime',
            reason='map-fit-incomplete', round=1, cycle=1,
            fitReport=dict(attemptCount=100, retryCount=99, lastAt=100.)))
        self.fixture.drain()
        rows = [json.loads(line) for line in self.service.log_path.read_text().splitlines()]
        self.assertEqual(len([r for r in rows if r.get('stage') == 'map-fit-retry']), 1)
        final = next(r for r in rows if r.get('stage') == 'map-fit-incomplete')
        self.assertEqual(final['fitReport']['attemptCount'], 100)

    def test_preparation_geometry_survives_real_problem_storage(self):
        observation = dict(frameWidth=800, frameHeight=600, mode='nightmare',
            observationReason='capture-shape-width', mapDetected=False, floorReason='controls-unlocalized',
            floorLayout='unlocalized', shapeReason='capture-shape-width', aspectRatio=4/3,
            floorControlSizeEligible=False, windowTitle='PrivatePlayer', unknown='PrivatePlayer')
        self.service.record(operation_problem(dict(reason='map-open-preparation-deadline', action='map',
            preparationObservation=observation), dict(mode='nightmare')))
        self.fixture.drain()
        row = json.loads(self.service.log_path.read_text().splitlines()[0])
        self.assertEqual(row['preparationObservation']['frameWidth'], 800)
        self.assertEqual(row['preparationObservation']['shapeReason'], 'capture-shape-width')
        self.assertIs(row['preparationObservation']['floorControlSizeEligible'], False)
        self.assertNotIn('PrivatePlayer', json.dumps(row))

    def test_new_fields_are_bounded_and_foreground_exception_is_known(self):
        huge = {'firstDiagnostics': {'strategy': 'PrivatePlayer' * 100000},
            'attemptCount': 2, 'firstReason': 'PrivatePlayer', 'unknown': ['PrivatePlayer'] * 1000}
        self.service.record(dict(kind='warning', reason='map-fit-incomplete', fitReport=huge,
            preparationObservation={'frameWidth': 800, 'unknown': 'PrivatePlayer'}))
        row = self.service.recent[0]
        self.assertLessEqual(len(json.dumps(row['fitReport']).encode()), 2048)
        self.assertNotIn('PrivatePlayer', json.dumps(row['fitReport']))
        self.assertNotIn('unknown', row['fitReport'])
        self.assertEqual(safe_record({'exceptionType': 'CaptureForegroundUnavailable'})['exceptionType'],
            'CaptureForegroundUnavailable')


if __name__ == '__main__':
    unittest.main()
