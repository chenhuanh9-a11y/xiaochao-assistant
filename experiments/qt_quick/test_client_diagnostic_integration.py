import os
"""Real help/settings/problems/export composition in owned Temp data only."""
from copy import deepcopy
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from PySide6.QtCore import QObject, QUrl, Qt, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlComponent
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtTest import QTest

APP = QGuiApplication.instance() or QGuiApplication([])
import test_client_info as fixtures
from client_settings import SettingsService
from client_problems import ProblemsService
from client_problem_messages import operation_problem
from client_session import SessionService


class RuntimeBoundary:
    def __init__(self):
        self.automatic = False
        self.manual = False

    def configure_failure_sampling(self, **values):
        self.configured = values

    def set_failure_sampling_enabled(self, enabled):
        self.manual = enabled

    def set_automatic_failure_sampling_enabled(self, enabled):
        self.automatic = enabled


class DiagnosticIntegrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.InfoTests.app = APP
        self.fixture = fixtures.InfoTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.info = self.fixture.service()
        self.settings = SettingsService(self.fixture.root)
        self.addCleanup(self.settings.shutdown)
        self.problems = ProblemsService(self.info.paths, build_fingerprint='FIXTURE')
        self.addCleanup(self.problems.shutdown)
        self.runtime = RuntimeBoundary()
        self.info.setProviders(settings=self.settings, problems=self.problems,
            input=SimpleNamespace(enabled=True, snapshot=dict(hwnd='0', hookInstalled=True, hookRunning=True)),
            capture=SimpleNamespace(snapshot=dict(lastCaptureObservation=dict(status='captured', durationMs=3.5, backend='fixture'))),
            session=SimpleNamespace(snapshot=dict(mode='nightmare', mapOpen=False)),
            coordinator=SimpleNamespace(runtime=self.runtime, boundWindow='0', gameWindows=[], _binding_identity=None))

    def test_invalid_settings_fail_closed_and_shutdown_disables_sampling(self):
        self.assertTrue(self.runtime.automatic)
        self.assertFalse(self.runtime.manual)
        self.settings.path.write_text('{broken', encoding='utf-8')
        self.assertFalse(self.settings.reload())
        self.assertFalse(self.info.automaticFailureSampling)
        self.assertFalse(self.runtime.automatic)
        self.settings.path.write_text('{}', encoding='utf-8')
        self.assertTrue(self.settings.reload())
        self.assertTrue(self.runtime.automatic)
        self.info.shutdown()
        self.assertFalse(self.runtime.automatic)
        self.assertFalse(self.runtime.manual)
        self.settings.changed.emit()
        self.assertFalse(self.runtime.automatic)

    def test_help_switch_persists_only_sampling_choice_and_restart_keeps_it(self):
        self.settings.setValue('interfaceOpacity', .7)
        engine = QQmlEngine()
        engine.rootContext().setContextProperty('infoService', self.info)
        engine.rootContext().setContextProperty('problemService', self.problems)
        from client_tutorial import TutorialService
        tutorial = TutorialService(self.fixture.root / 'tutorial-state.json')
        self.addCleanup(tutorial.shutdown)
        engine.rootContext().setContextProperty('tutorialService', tutorial)
        component = QQmlComponent(engine)
        component.setData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { width: 960; height: 680; visible: false
ClientHelp { anchors.fill: parent } }
''', QUrl.fromLocalFile(str(Path(__file__).with_name('__diagnostic_test__.qml'))))
        self.fixture.wait(lambda: not component.isLoading())
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        try:
            switch = window.findChild(QQuickItem, 'helpAutomaticFailureSampling')
            self.assertTrue(switch.property('checked'))
            self.assertTrue(self.runtime.automatic)
            window.show()
            def click_visible(item):
                scroll = window.findChild(QQuickItem, 'helpPageScroll')
                flick = scroll.property('contentItem')
                QTest.qWait(60)
                position = item.mapToItem(flick, QPointF(0, 0)).y() + flick.property('contentY')
                flick.setProperty('contentY', max(0, min(position - 40,
                    flick.property('contentHeight') - flick.height())))
                QTest.qWait(40)
                point = item.mapToScene(item.boundingRect().center()).toPoint()
                self.assertTrue(item.isVisible() and item.isEnabled())
                self.assertTrue(0 <= point.y() < window.height(), (item.objectName(), point))
                QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
                QTest.qWait(30)
            self.assertFalse(switch.isVisible())
            click_visible(window.findChild(QQuickItem, 'helpDiagnosticsOptions'))
            self.assertTrue(switch.isVisible())
            parents, item = [], switch
            while item is not None:
                parents.append(item)
                item = item.parentItem()
            for _ in range(4):
                for item in reversed(parents):
                    item.ensurePolished()
                window.requestUpdate()
                QTest.qWait(20)
            click_visible(switch)
            self.fixture.wait(lambda: not self.info.checking and not self.settings.busy)
            self.assertFalse(self.info.automaticFailureSampling)
            self.assertFalse(self.runtime.automatic)
            disk = json.loads(self.settings.path.read_text('utf-8'))
            self.assertFalse(disk['automaticFailureEvidence'])
            self.assertNotIn('interfaceOpacity', disk)
            self.assertEqual(self.settings.values['interfaceOpacity'], .7)
            export_choice = window.findChild(QQuickItem, 'helpExportAutomaticFailureEvidence')
            self.assertTrue(export_choice.property('checked'))
            self.assertTrue(self.info.includeAutomaticFailureEvidence)
            click_visible(export_choice)
            self.fixture.wait(lambda: not self.info.includeAutomaticFailureEvidence)
            restarted = SettingsService(self.fixture.root)
            try:
                self.assertFalse(restarted.persistedValues['automaticFailureEvidence'])
            finally:
                restarted.shutdown()
        finally:
            window.deleteLater()
            APP.processEvents()

    def test_default_export_has_real_environment_history_and_no_extra_capture(self):
        event = dict(title='sample', summary='sample', raw='ValueError: sample', phase='operation',
                     action='capture_now', kind='error', exceptionType='ValueError',
                     exceptionOrigin='session-worker:runtime-snapshot', traceback='ValueError: sample')
        self.problems.record(event)
        self.problems.record(deepcopy(event))
        destination = self.fixture.root / 'composed.zip'
        self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
        self.fixture.wait(lambda: not self.info.checking)
        self.assertTrue(self.info.diagnosticsReceipt['success'], self.info.diagnosticsReceipt)
        with zipfile.ZipFile(destination) as archive:
            summary = json.loads(archive.read('qt-client-summary.json'))
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
        self.assertEqual(summary['recentProblems'][0]['count'], 2)
        self.assertEqual(summary['recentProblems'][0]['exceptionOrigin'], event['exceptionOrigin'])
        self.assertEqual(summary['problemHistoryCoverage']['counts'], 'observed-only')
        self.assertFalse(summary['problemHistoryCoverage']['completeHistory'])
        environment = summary['runtimeEnvironment']
        self.assertTrue(environment['qtVersion'])
        self.assertTrue(environment['input']['hookInstalled'])
        self.assertEqual(environment['capture']['lastCaptureObservation']['durationMs'], 3.5)
        self.assertEqual(environment['gameProcessIntegrity']['status'], 'unavailable')
        self.assertEqual(manifest['runtimeProcessIntegrity'], environment['qtProcessIntegrity'])
        self.assertEqual(manifest['requestedExecutionLevel'], 'unavailable')
        self.assertIn('runtime token', manifest['requestedExecutionLevelReason'])
        self.assertIn('nightmare', summary['automaticFailureCleanup'])
        self.assertEqual(manifest['failureEvidence']['imageCount'], 0)

    def test_measured_recognition_reaches_production_log_and_export_separate_from_capture(self):
        from client_coordinator import ClientCoordinator
        from types import SimpleNamespace
        target=SimpleNamespace(_closed=False,_closing=False)
        target._trace=lambda stage,**values:self.problems.recordTrace(dict(stage=stage,**values))
        measured=dict(stage='pose-fit',reason='structure-current-geometry-failed',
            inputs=dict(frameId='a'*32,cycleId=7),fit=dict(success=False,
                diagnostics=dict(seedCount=2,queryChains=3,sourceChains=5,candidateCount=4,bestResidual=.2)))
        ClientCoordinator._on_runtime_event(target,dict(event='recognition-diagnostic',recognitionDiagnostics=measured))
        self.problems.shutdown(); self.fixture.wait(lambda:not self.problems.pendingWrites)
        self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(self.fixture.root/'measured.zip')).toString())
        self.fixture.wait(lambda:not self.info.checking)
        with zipfile.ZipFile(self.fixture.root/'measured.zip') as archive:
            payload=archive.read('logs/'+self.problems.recognition_path.name)
            rows=[json.loads(line) for line in payload.splitlines()]
        row=next(row for row in rows if 'recognitionDiagnostics' in row)
        self.assertEqual(row['recognitionDiagnostics'],measured)
        self.assertNotIn('captureDiagnostics',row)
        self.assertLessEqual(len(json.dumps(row['recognitionDiagnostics'],separators=(',',':')).encode()),2048)


    def test_s1_no_material_reasons_reach_persisted_log_and_text_only_export(self):
        from client_coordinator import ClientCoordinator
        from recognition_diagnostics import s1_scan_failure
        report = dict(status='complete', scanComplete=True, elapsedSeconds=.8,
            checkedMapIds=['107002'], supportedMapIds=[], unscannedMapIds=[],
            outcomes=[dict(mapId='107002', outcome='unknown',
                reason='initial-structure-current-geometry-failed')])
        measured = dict(stage='s1-scan-no-material', reason='no-current-supported-candidates',
            inputs=dict(attemptId='a'*32, cycleId=28, floor='-1', sourceFrameGeneration=28),
            structureScanFailure=s1_scan_failure(report, '107002'))
        target = SimpleNamespace(_closed=False, _closing=False)
        target._trace = lambda stage, **values: self.problems.recordTrace(dict(stage=stage, **values))
        ClientCoordinator._on_runtime_event(target, dict(event='recognition-diagnostic',
            reason='no-current-supported-candidates', recognitionDiagnostics=measured))
        self.problems.shutdown()
        self.fixture.wait(lambda: not self.problems.pendingWrites)
        persisted = [json.loads(line) for line in self.problems.recognition_path.read_bytes().splitlines()]
        self.assertEqual(persisted[0]['recognitionDiagnostics'], measured)
        self.info.setTextOnlyDiagnostics(True)
        destination = self.fixture.root / 's1-no-material.zip'
        self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
        self.fixture.wait(lambda: not self.info.checking)
        self.assertTrue(self.info.diagnosticsReceipt['success'], self.info.diagnosticsReceipt)
        with zipfile.ZipFile(destination) as archive:
            rows = [json.loads(line) for line in archive.read(
                'logs/'+self.problems.recognition_path.name).splitlines()]
            self.assertFalse(any(name.endswith('.png') for name in archive.namelist()))
        row = next(row for row in rows if 'recognitionDiagnostics' in row)
        self.assertEqual(row['recognitionDiagnostics'], measured)

    def test_failed_sampling_save_keeps_effective_choice(self):
        with patch.object(self.settings, '_write', side_effect=OSError('fixture denied')):
            self.assertTrue(self.info.setAutomaticFailureSampling(False))
            self.fixture.wait(lambda: not self.info.checking and not self.settings.busy)
        self.assertTrue(self.info.automaticFailureSampling)
        self.assertTrue(self.runtime.automatic)
        self.assertIn('fixture denied', self.info.message)
        self.assertFalse(self.settings.values['automaticFailureEvidence'])
        self.assertTrue(self.settings.persistedValues['automaticFailureEvidence'])

    def test_sampling_and_export_allow_independent_choices(self):
        import client_info
        exporter = client_info.export_safe_diagnostic_bundle
        self.assertTrue(self.info.automaticFailureSampling)
        self.assertTrue(self.info.includeAutomaticFailureEvidence)
        self.assertTrue(self.info.setAutomaticFailureSampling(False))
        self.fixture.wait(lambda: not self.info.checking)
        for manual, automatic in ((False, False), (False, True), (True, False), (True, True)):
            with self.subTest(manual=manual, automatic=automatic):
                self.info.setIncludeFailureEvidence(manual)
                self.info.setIncludeAutomaticFailureEvidence(automatic)
                destination = self.fixture.root / ('choices-%s-%s.zip' % (manual, automatic))
                with patch.object(client_info, 'export_safe_diagnostic_bundle', wraps=exporter) as export:
                    self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
                    self.fixture.wait(lambda: not self.info.checking)
                self.assertTrue(self.info.diagnosticsReceipt['success'])
                arguments = export.call_args.kwargs
                self.assertEqual(bool(arguments['failure_roots']), manual or automatic)
                self.assertEqual(arguments['automatic_failure_only'], not manual)
                self.assertEqual(arguments['include_automatic_failure_evidence'], automatic)
        restarted = fixtures.InfoTests.service(self.fixture)
        self.assertTrue(restarted.includeAutomaticFailureEvidence)

    @unittest.skipUnless(os.name == 'nt', 'automatic mutation requires Windows exclusive handles')
    def test_saved_automatic_image_is_exported_by_default_and_text_only_excludes_it(self):
        import numpy as np
        from client_failure_samples import FailureSampleRecorder
        self.problems.record(dict(title='sample failure', summary='fixture', kind='error'))
        self.fixture.wait(lambda: not self.problems.pendingWrites)
        stamp = self.problems.recent[0]['timestampUTC']
        recorder = FailureSampleRecorder()
        recorder.automatic_enabled = True
        sample = recorder.record_automatic(root=self.info.paths.memory_root('nightmare'),
            reason='current-geometry-failed', frame=np.zeros((8, 8, 3), dtype=np.uint8),
            diagnostic=dict(evidenceKind='live-client-capture', mode='nightmare',
                eventPath=str(self.problems.log_path), frameCapturedAtUtc=stamp,
                sessionId=self.problems.recent[0]['sessionId'],pid=self.problems.recent[0]['pid'],
                buildFingerprint='FIXTURE'), current=lambda: True,
            crop_bounds=(0, 0, 8, 8), crop_provenance='color-component-unvalidated')
        self.assertTrue(sample['saved'], sample)
        for index, include in enumerate((True, False, True)):
            if index:
                self.info.setTextOnlyDiagnostics(not include)
            destination = self.fixture.root / ('picture-%s-%s.zip' % (index, include))
            self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
            self.fixture.wait(lambda: not self.info.checking)
            self.assertTrue(self.info.diagnosticsReceipt['success'], self.info.diagnosticsReceipt)
            with zipfile.ZipFile(destination) as archive:
                evidence = json.loads(archive.read('diagnostic-manifest.json'))['failureEvidence']
            self.assertEqual(evidence['imageCount'], int(include), evidence)


    def test_export_keeps_queued_evicted_repeat_counts_without_waiting_for_disk(self):
        import client_problems
        entered, release = threading.Event(), threading.Event()
        original = client_problems._write_batch

        def blocked_write(path, rows):
            entered.set()
            if not release.wait(5):
                raise TimeoutError('fixture writer barrier')
            return original(path, rows)

        event = dict(title='repeat target', summary='sample', raw='sample', kind='error')
        with patch.object(client_problems, '_write_batch', side_effect=blocked_write):
            try:
                for _ in range(50):
                    self.problems.record(event)
                group_id = self.problems.recent[0]['problemGroupId']
                self.fixture.wait(entered.is_set)
                for index in range(32):
                    self.problems.record(dict(event, title='other ' + str(index)))
                self.problems.record(event)
                destination = self.fixture.root / 'queued.zip'
                self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
                self.fixture.wait(lambda: not self.info.checking)
                self.assertTrue(self.problems.pendingWrites)
                with zipfile.ZipFile(destination) as archive:
                    problems = json.loads(archive.read('qt-client-summary.json'))['recentProblems']
                target = next(row for row in problems if row.get('problemGroupId') == group_id)
                self.assertEqual(target['count'], 51)
            finally:
                release.set()
                self.fixture.wait(lambda: not self.problems.pendingWrites)

    def test_plain_revoke_does_not_invent_exception_fields(self):
        problem = operation_problem(dict(action='capture_now', reason='runtime-error', success=False), {})
        self.assertNotIn('exceptionType', problem)
        self.assertEqual(problem['traceback'], '')
        exception = operation_problem(dict(action='capture_now', reason='sample', error=True,
            exceptionType='ValueError', exceptionOrigin='session-worker:runtime-snapshot',
            exceptionAction='capture_now', traceback='ValueError: sample'), {})
        self.assertEqual(exception['exceptionOrigin'], 'session-worker:runtime-snapshot')

    def test_worker_snapshot_exception_survives_problem_and_export(self):
        from test_client_session import Runtime

        runtime = Runtime()
        session = SessionService(runtime)
        self.addCleanup(session.shutdown)
        session.refreshRuntime()
        with patch.object(runtime, 'snapshot', side_effect=ValueError('snapshot fixture')):
            session.dispatch('capture_now')
            self.fixture.wait(lambda: not session.busy)
        receipt = session.snapshot['lastReceipt']
        self.assertTrue(receipt['error'])
        self.assertEqual(receipt['exceptionOrigin'], 'session-worker:runtime-snapshot')
        self.problems.record(operation_problem(receipt, {}))
        destination = self.fixture.root / 'exception.zip'
        self.info.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
        self.fixture.wait(lambda: not self.info.checking)
        with zipfile.ZipFile(destination) as archive:
            problem = json.loads(archive.read('qt-client-summary.json'))['recentProblems'][0]
        self.assertEqual(problem['exceptionType'], 'ValueError')
        self.assertEqual(problem['exceptionOrigin'], receipt['exceptionOrigin'])
        self.assertIn('ValueError', problem['traceback'])
        self.assertNotIn('snapshot fixture', problem['traceback'])


if __name__ == '__main__':
    unittest.main()
