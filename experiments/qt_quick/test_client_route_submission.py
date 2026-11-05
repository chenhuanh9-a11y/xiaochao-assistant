"""Qt service + real QML input + localhost submission HTTP; no game/online IO."""
from pathlib import Path
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtGui import QKeyEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from client_paths import WORK_ROOT
from client_custom_gallery import CustomGalleryService
from client_route_plaza import RoutePlazaService
from test_client_route_plaza import APP, HERE, Router, item, until
from test_route_submission import SubmissionServer, VERSION, REVISION, write_submission_resource


class SubmissionQtTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-submit-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
        library = write_submission_resource(self.root / "maps")
        self.server = SubmissionServer()
        self.env = patch.dict("os.environ", XINGTU_ACCOUNT_API_URL=self.server.url)
        self.env.start()
        self.galleries = CustomGalleryService(self.root, library=library, default_path=self.root / "none")
        until(lambda: not self.galleries.busy and bool(self.galleries.snapshot["galleries"]))
        self.galleries.createGallery("我画的路线")
        until(lambda: not self.galleries.busy)
        self.gid = self.galleries.snapshot["selectedGallery"]
        self.galleries.addPoint(45, 45)
        until(lambda: not self.galleries.busy)
        self.galleries.addPoint(75, 15)
        until(lambda: not self.galleries.busy)
        self.service = RoutePlazaService(self.root, self.galleries)
        self.service.setActive(True)
        until(lambda: not self.service.snapshot["busy"])
        self.threads = []
        self.service.changed.connect(lambda: self.threads.append(threading.get_ident()))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.release.set()
        self.service.shutdown(); self.galleries.shutdown()
        APP.processEvents(); self.env.stop(); self.server.close(); self.temp.cleanup()

    def sign_in(self):
        self.service.openAccount()
        self.service.submitAccount("alice", "", "", "", "secret1", "")
        until(lambda: not self.service.snapshot["busy"])

    def submit(self):
        self.service.openSubmission("")
        self.service.submitGallery(self.gid, "自己画的路线", "说明文字")

    def finish(self):
        until(lambda: not self.service.snapshot["busy"])

    def gate(self, path):
        self.server.gate = path
        self.server.release.clear()

    def test_login_continues_same_submission_then_mine_withdraw_update(self):
        self.service.openSubmission("")
        self.assertTrue(self.service.snapshot["accountOpen"])
        self.service.submitGallery(self.gid, "自己画的路线", "说明文字")
        self.service.submitAccount("alice", "", "", "", "secret1", "")
        self.finish()
        self.assertFalse(self.service.snapshot["editorOpen"])
        self.assertEqual(self.server.records[VERSION]["status"], "pending")
        self.service.selectTab("mine"); self.finish()
        row = self.service.submissions.rows[0]
        self.assertEqual(row["statusLabel"], "审核中")
        self.assertFalse(row["editable"]); self.assertTrue(row["withdrawable"])
        self.service.withdrawSubmission(VERSION); self.finish()
        self.assertEqual(self.service.submissions.rows[0]["statusLabel"], "已撤回")
        self.service.openSubmission(VERSION)
        self.assertEqual(self.service.snapshot["editorTitle"], "自己画的路线")
        self.service.submitGallery(self.gid, "修改后的路线", "修订说明"); self.finish()
        self.assertEqual(self.server.records[REVISION]["status"], "pending")
        self.assertEqual(self.threads, [threading.get_ident()] * len(self.threads))

    def test_legacy_editor_without_submission_navigation_fails_before_draft(self):
        # A valid older editor package remains usable, but cannot supply Android
        # navigation. Exercise the published gallery snapshot, not a mocked converter.
        manifest_path = self.root / "maps" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        del manifest["maps"][0]["routeSubmission"]
        manifest_path.write_text(json.dumps(manifest))
        self.galleries.refresh()
        until(lambda: not self.galleries.busy)
        self.assertTrue(self.galleries.snapshot["available"])
        self.assertFalse(self.galleries.snapshot["dataError"])
        original = self.galleries.path.read_bytes()
        self.sign_in(); self.submit(); self.finish()
        self.assertTrue(self.service.snapshot["error"])
        self.assertIn("缺少投稿导航资源", self.service.snapshot["message"])
        self.assertEqual(self.server.records, {})
        self.assertEqual(self.server.uploads, [])
        self.assertFalse(any(method in ("POST", "PUT") and path.startswith("/v1/community/")
                             for method, path, *_ in self.server.calls))
        self.assertEqual(self.galleries.path.read_bytes(), original)
        self.service.closeSubmission()
        self.assertFalse(self.service.snapshot["submissionLocked"])

    def test_upload_is_independent_of_gallery_writer_cancel_and_exact_retry(self):
        self.sign_in(); self.gate("/object"); self.submit()
        until(self.server.entered.is_set)
        self.assertTrue(self.service.snapshot["busy"])
        self.assertFalse(self.galleries.busy)
        self.assertTrue(self.service.snapshot["submissionLocked"])
        self.assertGreater(self.service.snapshot["progress"]["total"], 0)
        self.service.submitGallery(self.gid, "重复不能创建", "")
        before = time.monotonic()
        self.service.cancelSubmission()
        self.assertLess(time.monotonic() - before, .15)
        self.server.release.set(); self.finish()
        self.assertEqual(self.server.records[VERSION]["status"], "draft")
        self.assertTrue(self.service.snapshot["error"])
        old_package = self.server.uploads[0][0]
        self.service.submitGallery(self.gid, "输入锁定后会忽略", ""); self.finish()
        self.assertFalse(self.service.snapshot["error"], self.service.snapshot)
        self.assertEqual(self.server.uploads[-1][0], old_package)
        self.assertEqual(self.server.records[VERSION]["title"], "自己画的路线")

    def test_cancel_cannot_relabel_accepted_submit_as_cancelled(self):
        self.sign_in(); self.gate(f"/v1/community/map-galleries/{VERSION}/submit"); self.submit()
        until(self.server.entered.is_set)
        self.service.cancelSubmission()
        self.server.release.set(); self.finish()
        self.assertFalse(self.service.snapshot["error"], self.service.snapshot)
        self.assertIn("已提交", self.service.snapshot["message"])

    def test_hidden_completion_does_not_reopen_form_or_write_hidden_list(self):
        self.sign_in(); self.gate("/object"); self.submit()
        until(self.server.entered.is_set)
        mutations = self.service.submissions.mutations
        self.service.setActive(False)
        self.server.release.set(); self.finish()
        self.assertFalse(self.service.snapshot["editorOpen"])
        self.assertFalse(self.service.snapshot["accountOpen"])
        self.assertEqual(self.service.submissions.mutations, mutations)
        self.service.setActive(True)
        self.service.selectTab("mine"); self.finish()
        self.assertEqual(self.service.submissions.rows[0]["status"], "draft")

    def test_dismissed_login_and_account_change_revoke_private_intents(self):
        self.service.openSubmission("")
        self.service.submitGallery(self.gid, "关闭前的投稿", "")
        self.gate("/v1/auth/login")
        self.service.submitAccount("alice", "", "", "", "secret1", "")
        until(self.server.entered.is_set)
        self.service.closeAccount(); self.service.closeSubmission()
        self.server.release.set(); self.finish()
        self.assertEqual(self.server.records, {})
        self.assertFalse(self.service.snapshot["accountOpen"])
        self.service.selectTab("mine"); self.finish()
        self.service.logout(); self.finish()
        self.assertEqual(self.service.submissions.rows, [])
        self.assertIsNone(self.service._submission)

    def test_resource_refresh_and_gallery_edits_revoke_later_upload_stages(self):
        self.sign_in(); self.gate("/object"); self.submit()
        until(self.server.entered.is_set)
        self.galleries.removePoint(1)
        until(lambda: not self.galleries.busy)
        self.server.release.set(); self.finish()
        self.assertEqual(self.server.records[VERSION]["status"], "draft")
        self.assertFalse(any(call[1].endswith("/complete") for call in self.server.calls))
        self.service.submitGallery(self.gid, "另一个", "")
        self.assertIn("已变化", self.service.snapshot["message"])
        self.service.closeSubmission()
        self.server.entered.clear(); self.server.release.clear()
        self.submit(); until(self.server.entered.is_set)
        self.galleries.refresh()
        until(lambda: not self.galleries.busy)
        self.server.release.set(); self.finish()
        self.assertTrue(self.service.snapshot["error"])
        self.assertFalse(any(call[1].endswith("/submit") for call in self.server.calls))

    def test_close_drains_accepted_upload_and_never_submits(self):
        self.sign_in(); self.gate("/object"); self.submit()
        until(self.server.entered.is_set)
        self.service.cancel()
        self.assertGreater(self.service.pool.activeThreadCount(), 0)
        self.server.release.set()
        self.service.shutdown()
        self.assertEqual(self.service.pool.activeThreadCount(), 0)
        APP.processEvents()
        self.assertFalse(self.service.snapshot["busy"])
        self.assertFalse(any(call[1].endswith("/submit") for call in self.server.calls))

    def test_failure_reason_and_stable_mine_rows(self):
        self.sign_in(); self.submit(); self.finish()
        self.service.selectTab("mine"); self.finish()
        mutations = self.service.submissions.mutations
        self.service.loadMine(); self.finish()
        self.assertEqual(mutations, self.service.submissions.mutations)
        self.server.conflict = True
        self.service.withdrawSubmission(VERSION); self.finish()
        self.assertIn("投稿状态已变化", self.service.snapshot["message"])
        self.assertEqual(self.service.submissions.rows[0]["status"], "pending")

    def test_submit_bound_draft_from_mine_without_new_upload(self):
        self.sign_in(); self.server.fail = "/submit"
        self.submit(); self.finish()
        self.assertTrue(self.service.snapshot["error"])
        self.service.closeSubmission()
        self.service.selectTab("mine"); self.finish()
        self.assertTrue(self.service.submissions.rows[0]["canSubmit"])
        uploads = len(self.server.uploads)
        self.server.fail = ""
        self.service.submitDraft(VERSION); self.finish()
        self.assertEqual(len(self.server.uploads), uploads)
        self.assertEqual(self.service.submissions.rows[0]["status"], "pending")

    def test_qml_mouse_form_submit_mine_and_withdraw(self):
        self.sign_in()
        from client_settings import SettingsService
        settings = SettingsService(self.root)
        self.addCleanup(settings.shutdown)
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("routePlazaService", self.service)
        engine.rootContext().setContextProperty("settingsService", settings)
        router = Router()
        engine.rootContext().setContextProperty("clientCoordinator", router)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 900; height: 760
ClientRoutePlaza { anchors.fill: parent; anchors.margins: 24 }
ClientAccount {}
}''', QUrl.fromLocalFile(str(HERE / "submission-harness.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        def click(name):
            target = item(window, name)
            self.assertIsNotNone(target, name)
            QTest.qWait(40)  # Qt Quick layout polish before a physical hit.
            point = target.mapToScene(QPointF(target.width() / 2, target.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
            APP.processEvents()
        try:
            click("routePointNumbersSwitch")
            until(lambda: not settings.busy and settings.persistedValues["routePointNumbers"])
            click("plazaSubmit")
            until(lambda: item(window, "routeSubmissionDialog").property("opened"))
            title = item(window, "submissionTitle")
            click("submissionTitle")
            for char in "My route":
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyPress, ord(char.upper()), Qt.NoModifier, char))
                QCoreApplication.sendEvent(window, QKeyEvent(QEvent.KeyRelease, ord(char.upper()), Qt.NoModifier, char))
            self.assertEqual(title.property("text"), "My route")
            item(window, "submissionDescription").setProperty("text", "作品说明")
            click("submissionSubmit"); self.finish()
            until(lambda: not item(window, "routeSubmissionDialog").property("visible"))
            click("plazaMine"); self.finish()
            until(lambda: item(window, "submissionWithdraw_" + VERSION) is not None)
            click("submissionWithdraw_" + VERSION); self.finish()
            self.assertEqual(self.service.submissions.rows[0]["statusLabel"], "已撤回")
            self.assertTrue(settings.persistedValues["routePointNumbers"])
            self.assertFalse(warnings, warnings)
        finally:
            window.close(); engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


class SubmissionCompositionTests(unittest.TestCase):
    def test_complete_client_input_guard_upload_and_close_drain(self):
        import json
        import os
        import subprocess
        import sys
        run = subprocess.run([sys.executable, "-c", "from test_client_route_plaza import composition; composition(submitting=True)"],
                             env=dict(os.environ), capture_output=True, text=True, timeout=40)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertTrue(result["closed"])
        self.assertTrue(result["inputGuard"])
        self.assertEqual(result["qmlErrors"], [])
        self.assertEqual(result["startupRequests"], [])


if __name__ == "__main__":
    unittest.main()
