"""Local help/update flows; no network, installed data, native input or browser."""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from client_info import ClientInfoService, WORK_ROOT


def release(version="9.1", notes="修复地图显示\n保留已有设置"):
    return dict(schemaVersion=1, platform="windows", version=version, versionCode=91,
                releaseNotes=notes, publishedAt="2026-10-30", size=100,
                downloadPageUrl="https://pan.quark.cn/s/example", ucDownloadPageUrl="https://drive.uc.cn/s/example")


class InfoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-info-test-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.calls, self.opened, self.services = [], [], []

    def tearDown(self):
        for service in self.services:
            service.shutdown()
        self.app.processEvents()
        self.temp.cleanup()

    def fetch(self, url):
        self.calls.append(url)
        return {"releases": [release("8.0"), release("9.1")]} if "history" in url else release()

    def service(self, fetch=None):
        service = ClientInfoService(self.root, fetch=fetch or self.fetch,
                                   open_url=lambda url: self.opened.append(url.toString()) or True)
        self.services.append(service)
        return service

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "local worker timeout")

    def checked(self, service):
        service.checkUpdates()
        self.wait(lambda: not service.checking)

    def test_default_export_requests_only_automatic_local_evidence(self):
        import client_info
        service = self.service()
        self.assertTrue(service.includeAutomaticFailureEvidence)
        self.assertFalse(service.includeFailureEvidence)
        destination = self.root / 'default-local-evidence.zip'
        with patch.object(client_info, 'export_safe_diagnostic_bundle',
                          wraps=client_info.export_safe_diagnostic_bundle) as export:
            service.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
            self.wait(lambda: not service.checking)
        self.assertTrue(service.diagnosticsReceipt['success'])
        self.assertEqual(export.call_args.kwargs['failure_roots'], tuple(
            service.paths.memory_root(mode, season)
            for season in ('s0', 's1') for mode in ('nightmare', 'difficult')))
        self.assertTrue(export.call_args.kwargs['automatic_failure_only'])
        self.assertTrue(export.call_args.kwargs['include_automatic_failure_evidence'])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.opened, [])

    def test_explicit_text_only_export_overrides_both_image_choices(self):
        service = self.service()
        service.setIncludeFailureEvidence(True)
        service.setTextOnlyDiagnostics(True)
        self.assertTrue(service.textOnlyDiagnostics)
        self.assertFalse(service.includeFailureEvidence)
        self.assertFalse(service.includeAutomaticFailureEvidence)
        destination = self.root / 'text-only.zip'
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(destination)).toString())
        self.wait(lambda: not service.checking)
        with zipfile.ZipFile(destination) as archive:
            manifest = json.loads(archive.read('diagnostic-manifest.json'))
            self.assertTrue(manifest['textOnly'])
            self.assertEqual(manifest['failureEvidence']['status'], 'not-requested')
        service.setTextOnlyDiagnostics(False)
        self.assertTrue(service.includeAutomaticFailureEvidence)
        self.assertFalse(service.includeFailureEvidence)

    def test_frozen_identity_uses_baked_profile_not_retained_data(self):
        import client_info
        baked = self.root / 'baked'
        baked.mkdir()
        (baked / 'version.json').write_text('{"version":"1.0","displayVersion":"1.0"}')
        commit = '7' * 40
        (baked / 'installation-profile.json').write_text(json.dumps(dict(sourceCommit=commit)))
        (self.root / 'installation-profile.json').write_text(json.dumps(dict(sourceCommit='f' * 40)))
        with patch.object(client_info, 'ROOT', baked), patch('client_paths.RESOURCE_ROOT', baked), \
             patch.object(client_info.sys, 'frozen', True, create=True):
            service = self.service()
        self.assertEqual(service._build_fingerprint, '77777777')
        self.assertEqual(service._source_commit, commit)
        self.assertFalse((self.root / '_internal/installation-profile.json').exists())
        self.assertEqual(json.loads((self.root / 'installation-profile.json').read_text())['sourceCommit'], 'f' * 40)

    def test_missing_or_invalid_baked_identity_never_uses_stale_report(self):
        import client_info
        baked = self.root / 'baked'
        baked.mkdir()
        (baked / 'version.json').write_text('{"version":"1.0","displayVersion":"1.0"}')
        for payload in (None, '{', '{"sourceCommit":"private-user-text"}'):
            profile = baked / 'installation-profile.json'
            if payload is not None:
                profile.write_text(payload)
            with patch.object(client_info, 'ROOT', baked), patch.object(client_info.sys, 'frozen', True, create=True), patch.object(client_info, 'diagnostic_package_metadata', return_value=dict(releaseReportSourceCommit='f'*40)):
                service = self.service()
            self.assertEqual(service._build_fingerprint, 'UNKNOWN')
            self.assertEqual(service._source_commit, '')
            calls = []
            runtime = SimpleNamespace(configure_failure_sampling=lambda **fields: calls.append(fields))
            service.setProviders(coordinator=SimpleNamespace(runtime=runtime),
                                 problems=SimpleNamespace(log_path=self.root/'synthetic-events.jsonl'))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]['source_commit'], '')
            self.assertEqual(calls[0]['build_fingerprint'], 'UNKNOWN')

    def test_retired_help_cards_have_no_runtime_or_bundle_dependency(self):
        service = self.service()
        self.assertFalse(hasattr(service, 'helpTopics'))
        self.assertFalse(hasattr(service, 'helpImages'))
        self.assertFalse(hasattr(service, 'helpNavigate'))
        self.assertFalse(Path(__file__).with_name('help-topics.json').exists())
        spec = Path(__file__).resolve().parents[2] / 'tools/qt-client.spec'
        self.assertNotIn('help-topics.json', spec.read_text('utf-8'))
        # The complete tutorial and its current hotkey tests are retained.
        self.assertTrue(Path(__file__).with_name('tutorial-chapters.json').is_file())

    def test_help_reset_changes_only_random_identity_and_next_production_log(self):
        from client_problems import ProblemsService
        service = self.service()
        problems = ProblemsService(service.paths)
        self.addCleanup(problems.shutdown)
        service.setProviders(problems=problems)
        problems.recordTrace(dict(stage='before-reset', round=4, cycle=2))
        previous = problems.identity.installation.install_id
        self.assertTrue(service.resetDiagnosticInstallId())
        self.wait(lambda: not service.checking)
        current = problems.identity.installation.install_id
        self.assertNotEqual(previous, current, service.message)
        problems.recordTrace(dict(stage='after-reset', round=4, cycle=2))
        problems.shutdown()
        self.wait(lambda: not problems.pendingWrites)
        rows = [json.loads(line) for line in problems.log_path.read_text('utf-8').splitlines()]
        self.assertEqual([row['installId'] for row in rows], [previous, current])
        self.assertEqual(len({row['sessionId'] for row in rows}), 1)
        self.assertEqual([row['seq'] for row in rows], [1, 2])
        self.assertEqual([row['round'] for row in rows], [4, 4])
        self.assertFalse(service.paths.settings_path.exists())

    def test_startup_is_idle_and_check_delivers_real_history(self):
        service = self.service()
        self.app.processEvents()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.opened, [])
        self.assertFalse((self.root / "qt-update-state.json").exists())
        self.checked(service)
        self.assertEqual(service.updatePhase, "available")
        self.assertEqual(service.history[0]["version"], "8.0")
        self.assertEqual(service.release["published_date"], "2026-10-30")
        self.assertIn("\n", service.release["notes"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.opened, [])

    def test_explicit_startup_schedule_is_once_nonmanual_and_disabled_for_probe(self):
        service = self.service()
        announced = []
        service.startupAnnouncement.connect(announced.append)
        self.assertFalse(service.scheduleStartupCheck(False))
        self.app.processEvents()
        self.assertEqual(self.calls, [])
        with patch.object(service._service, "check", wraps=service._service.check) as check:
            self.assertTrue(service.scheduleStartupCheck(True))
            self.assertEqual(service._startup_timer.interval(), 2500)
            self.assertFalse(service.scheduleStartupCheck(True))
            service._startup_timer.setInterval(1)
            self.wait(lambda: service.updatePhase == "available" and len(announced) == 1)
            self.assertEqual(check.call_count, 1)
            self.assertEqual(check.call_args.kwargs, {"manual": False})
            service._startup_check()
            self.assertEqual(check.call_count, 1)
        self.assertEqual(announced[0]["version"], "9.1")
        self.assertEqual(self.opened, [])

    def test_home_preview_uses_applied_context_and_ignores_browsing_changes(self):
        from PySide6.QtCore import Signal
        from PySide6.QtGui import QImage
        from map_resource_model import ResourceMap, ResourceVariant
        class Browse(QObject):
            changed = Signal()
            context = SimpleNamespace(mode="difficult")
            profileId = "unrelated-browse-author"
            preview_model = SimpleNamespace(rows=[])
        path = self.root / "home.png"
        image = QImage(20, 10, QImage.Format_RGBA8888); image.fill(0xffeaf2fd)
        self.assertTrue(image.save(str(path)))
        item = ResourceMap('map-a', 'Actual map', (ResourceVariant('v1', 'Route', {'1': path, '2': path}),))
        profile = SimpleNamespace(profile_id='actual-author', maps=(item,))
        context = SimpleNamespace(error='', pack=SimpleNamespace(default_entry_floor='1'),
            profiles=(profile,), path_stamps={path:(path.stat().st_mtime_ns,path.stat().st_size)})
        service = self.service()
        backend = Browse()
        service.setProviders(coordinator=SimpleNamespace(backend=backend))
        requests=[]; service.homePreviewRequested.connect(lambda *args:requests.append(args))
        with patch('client_info.load_browse_context', return_value=context) as load:
            self.assertTrue(service.setHomeContext('nightmare','actual-author'))
            self.wait(lambda: bool(service.homePreviews))
            self.assertEqual(service.homePreviews[0]['mode'], 'nightmare')
            self.assertEqual(service.homePreviews[0]['profileId'], 'actual-author')
            self.assertIn('nightmare/actual-author/', service.homePreviews[0]['key'])
            backend.changed.emit();self.app.processEvents()
            self.assertFalse(service.setHomeContext('nightmare','actual-author'))
            self.assertEqual(load.call_count, 1)
            service.openHomePreview(service.homePreviews[0]['key'])
            self.assertEqual(requests[0][:3], ('nightmare','actual-author','s0'))
            service.setHomeContext('difficult','actual-author')
            self.wait(lambda: bool(service.homePreviews) and service.homePreviews[0]['mode']=='difficult')
            self.assertEqual(load.call_count, 2)
            service.setHomeContext('difficult','actual-author',force=True)
            self.wait(lambda: bool(service.homePreviews))
            self.assertEqual(load.call_count, 3)
        started, finish = threading.Event(), threading.Event()
        def delayed_load(*args, **kwargs):
            if not started.is_set():
                started.set(); finish.wait(2)
            return context
        published = []
        service.changed.connect(lambda: published.append(service.homePreviews[0]['mode']) if service.homePreviews else None)
        with patch('client_info.load_browse_context', side_effect=delayed_load):
            service.setHomeContext('nightmare','actual-author',force=True)
            self.wait(started.is_set)
            service.setHomeContext('difficult','actual-author')
            finish.set()
            self.wait(lambda: bool(service.homePreviews) and service.homePreviews[0]['mode']=='difficult')
        self.assertEqual(published, ['difficult'])
        self.assertEqual(self.calls, [])

    def test_failed_check_retries_and_optional_history_failure_is_visible(self):
        fail = [True]
        def fetch(url):
            if fail[0]:
                raise OSError("local offline fixture")
            if "history" in url:
                raise OSError("local history fixture")
            return release()
        service = self.service(fetch)
        self.checked(service)
        self.assertTrue(service.hasError)
        self.assertIn("offline", service.message)
        fail[0] = False
        self.checked(service)
        self.assertFalse(service.hasError)
        self.assertEqual(service.release["version"], "9.1")
        self.assertIn("重新检查", service.historyMessage)

    def test_read_skip_persist_and_browser_only_explicit_allowlisted_button(self):
        service = self.service()
        self.checked(service)
        service.markRead()
        self.wait(lambda: not service.checking)
        self.assertFalse(service.hasUnread)
        service.skipVersion()
        self.wait(lambda: not service.checking)
        stored = json.loads((self.root / "qt-update-state.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["seenNotesVersion"], "9.1")
        self.assertEqual(stored["skippedVersion"], "9.1")
        self.assertEqual(self.opened, [])
        service.openDownloadPage("uc")
        self.assertEqual(self.opened, ["https://drive.uc.cn/s/example"])
        service._service.latest = replace(service._service.latest, pages={"uc": "https://evil.example/"})
        service.openDownloadPage("uc")
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(service.hasError)

    def test_swallowed_notice_save_failure_is_not_reported_as_success(self):
        service = self.service()
        self.checked(service)
        with patch.object(service._service, "_save", return_value=None):
            service.markRead()
            self.wait(lambda: not service.checking)
        self.assertTrue(service.hasError)
        self.assertTrue(service.hasUnread)

    def test_diagnostics_receipt_selected_path_and_no_overwrite(self):
        service = self.service()
        service.setTextOnlyDiagnostics(True)
        path = self.root / "chosen.zip"
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertTrue(service.diagnosticsReceipt["success"])
        self.assertEqual(service.diagnosticsReceipt["path"], str(path))
        payload = path.read_bytes()
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(json.loads(archive.read("qt-client-summary.json"))["schema"], "xingtu-qt-client-diagnostics-v1")
            manifest = json.loads(archive.read("diagnostic-manifest.json"))
            self.assertTrue(manifest["textOnly"])
            self.assertEqual(manifest["failureEvidence"]["status"], "not-requested")
            self.assertFalse(any(name.startswith("evidence/") for name in archive.namelist()))
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertFalse(service.diagnosticsReceipt["success"])
        self.assertEqual(path.read_bytes(), payload)
        service.exportDiagnosticsTo("https://example.test/report.zip")
        self.assertTrue(service.hasError)
        self.assertEqual(self.calls, [])

    def test_zip_uses_existing_safe_window_snapshot_and_saved_frame_opt_in(self):
        service = self.service()
        service.setProviders(
            input=SimpleNamespace(enabled=False, snapshot=dict(hwnd="123", readPermission="unknown")),
            coordinator=SimpleNamespace(boundWindow="123", gameWindows=[dict(hwnd="123", pid=456,
                title="private window title", width=1280, height=720, dpi=96, clientBounds=[0, 0, 1280, 720])]))
        service.setIncludeFailureEvidence(True)
        path = self.root / "with-existing-evidence.zip"
        service.exportDiagnosticsTo(QUrl.fromLocalFile(str(path)).toString())
        self.wait(lambda: not service.checking)
        self.assertTrue(service.diagnosticsReceipt["success"])
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read("diagnostic-manifest.json"))
            self.assertEqual(manifest["windowEnvironment"]["candidates"],
                             [dict(width=1280, height=720, dpi=96, clientBounds=[0, 0, 1280, 720])])
            self.assertEqual(manifest["failureEvidence"]["status"], "no-matching-saved-frame")
            self.assertEqual(len(manifest["failureEvidence"]["journals"]), 4)
            self.assertNotIn("private window title", json.dumps(manifest))
            self.assertEqual(manifest["windowEnvironment"]["readPermission"], "unknown")
        self.assertEqual(self.calls, [])

    def test_qml_local_check_history_and_plain_notes_no_warnings(self):
        service = self.service()
        from client_problems import ProblemsService
        problems = ProblemsService(service.paths, parent=service)
        self.addCleanup(problems.shutdown)
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("infoService", service)
        engine.rootContext().setContextProperty("problemService", problems)
        from client_tutorial import TutorialService
        tutorial = TutorialService(self.root / "qt-tutorial-state.json", parent=service)
        self.addCleanup(tutorial.shutdown)
        engine.rootContext().setContextProperty("tutorialService", tutorial)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        component = QQmlComponent(engine)
        component.setData(b"""import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { width: 960; height: 680; visible: false
ClientHelp { objectName: "helpPanel"; anchors.fill: parent } }
""", QUrl.fromLocalFile(str(Path(__file__).with_name("__help_test__.qml"))))
        if component.isLoading():
            self.wait(lambda: not component.isLoading())
        self.assertFalse(component.isError(), str(component.errors()))
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        self.assertEqual(self.calls, [])
        self.assertIsNone(window.findChild(QObject, "helpTopic-quick-start"))
        self.assertIsNone(window.findChild(QObject, "helpTopic-check-feedback"))
        self.assertFalse(window.findChild(QObject, "helpAutomaticFailureSampling").property("visible"))
        self.assertTrue(QMetaObject.invokeMethod(window.findChild(QObject, "helpDiagnosticsOptions"), "clicked"))
        self.app.processEvents()
        auto = window.findChild(QObject, "helpExportAutomaticFailureEvidence")
        self.assertTrue(auto.property('checked'))
        text_only = window.findChild(QObject, "helpTextOnlyDiagnostics")
        self.assertIsNotNone(text_only)
        self.assertFalse(text_only.property('checked'))
        text_only.setProperty('checked', True)
        self.assertTrue(QMetaObject.invokeMethod(text_only, 'toggled'))
        self.app.processEvents()
        self.assertTrue(service.textOnlyDiagnostics)
        self.assertFalse(auto.property('checked'))
        self.assertFalse(service.includeFailureEvidence)
        check = window.findChild(QObject, "helpCheckUpdates")
        self.assertTrue(QMetaObject.invokeMethod(check, "clicked"))
        self.wait(lambda: not service.checking)
        toggle = window.findChild(QObject, "helpHistoryToggle")
        self.assertTrue(QMetaObject.invokeMethod(toggle, "clicked"))
        self.app.processEvents()
        self.assertTrue(window.findChild(QObject, "helpPanel").property("historyOpen"))
        requested = []
        tutorial.openRequested.connect(requested.append)
        self.assertTrue(QMetaObject.invokeMethod(window.findChild(QObject, "helpTutorialStart"), "clicked"))
        self.assertEqual(requested, ["start"])
        self.assertEqual(self.opened, [])
        self.assertEqual(warnings, [])
        skip = window.findChild(QObject, "helpSkipAnnouncement")
        service.skipVersion()
        self.wait(lambda: not service.checking)
        self.assertTrue(service.announcementSkipped)
        self.assertFalse(skip.property('enabled'))
        service._service.latest = replace(service._service.latest, notes='Same version, revised notice')
        service.changed.emit(); self.app.processEvents()
        self.assertFalse(service.announcementSkipped)
        self.assertTrue(skip.property('enabled'))
        window.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
