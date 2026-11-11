"""Gallery entry points use real Qt dialogs/services over private synthetic files.

The browse rows are fixtures; no native Windows picker or game is exercised.
"""
from pathlib import Path
import json
import shutil
import sys
import time
import unittest

from test_client_gallery_layout import APP, FakeBackend, FakeState, FakeCoordinator
import test_client_resources as resources_fixture
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
import test_s1_author_package as s1_fixture
from client_package_manager import PackageManagerService
from PySide6.QtCore import QObject, Property, QPointF, Qt, QUrl, QMetaObject, QCoreApplication, QEvent
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest


class SharedLease(resources_fixture.Lease):
    def acquireResourceLease(self):
        # The real SessionService refuses competing leases instead of asserting.
        return "" if self.active else super().acquireResourceLease()


class GalleryBackend(FakeBackend):
    def __init__(self, image):
        self.catalog = [
            dict(id="author-a", name="测试速菊", packageId="nightmare-suju", available=40,
                 availableMaps=40, mapCount=40, previewUrl=image),
            dict(id="author-b", name="未登记资源", packageId="", available=12,
                 availableMaps=12, mapCount=40, previewUrl=image),
        ]
        super().__init__(image)
        self.error = ""

    profiles = Property("QVariantList", lambda self: self.catalog, notify=FakeBackend.changed)
    hasError = Property(bool, lambda self: bool(self.error), notify=FakeBackend.changed)
    message = Property(str, lambda self: self.error or "隔离测试资源", notify=FakeBackend.changed)


class GalleryPackageActionsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = resources_fixture.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.lease = SharedLease()
        self.fixture.service.setLeaseProvider(self.fixture.lease)
        self.fixture.install()
        self.manager = PackageManagerService(self.fixture.root, self.fixture.service)
        self.manager.setLeaseProvider(self.fixture.lease)
        self.manager.resourcesChanged.connect(self.fixture.service.resourcesChanged.emit)
        self.fixture.service.resourcesChanged.connect(self.manager.requestRefresh)
        self.confirmations = []
        self.manager.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        self.addCleanup(self.close_manager)
        image = QImage(120, 100, QImage.Format_RGBA8888)
        image.fill(0xffcedbed)
        image_path = self.fixture.folder / "synthetic.png"
        self.assertTrue(image.save(str(image_path)))
        self.backend = GalleryBackend(QUrl.fromLocalFile(str(image_path)).toString())
        self.fixture.service.resourcesChanged.connect(self.backend.refresh)
        self.state, self.coordinator = FakeState(), FakeCoordinator()
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda rows: self.warnings.extend(str(row) for row in rows))
        for name, value in dict(backend=self.backend, resourceModel=self.backend.model,
                session=self.state, settingsService=self.state, resourceService=self.fixture.service,
                packageManagerService=self.manager, clientCoordinator=self.coordinator).items():
            self.engine.rootContext().setContextProperty(name, value)
        # Same persistent-page ownership as Client.qml: the management page is
        # hidden, while its chooser/confirmation belong to the main window.
        self.engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow {
    width: 1000; height: 650; visible: true
    ClientGallery { objectName: "galleryPage"; anchors.fill: parent; packageActions: resources }
    ClientResources { id: resources; objectName: "resourcePage"; anchors.fill: parent; visible: false }
}''', QUrl.fromLocalFile(str(Path(__file__).with_name("GalleryActionsFixture.qml"))))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.window = self.engine.rootObjects()[0]
        self.addCleanup(self.close_window)
        self.page = self.window.findChild(QObject, "galleryPage")
        self.dialog = self.window.findChild(QObject, "resourceArchiveDialog")
        self.assertTrue(self.dialog.setProperty("options", 8))  # Qt-owned fallback chooser.
        self.resources_page = self.window.findChild(QObject, "resourcePage")
        # Hidden persistent chooser construction must not scan the library.
        QTest.qWait(160)
        self.assertFalse(self.fixture.service.busy)
        self.assertEqual(self.manager.packageRows, [])
        # Existing action tests explicitly request the inventory they inspect.
        QMetaObject.invokeMethod(self.resources_page, "refreshPackages")
        self.until(lambda: len(self.manager.packageRows) == 1 and not self.manager.busy)
        QTest.qWait(60)

    def test_activation_waits_for_idle_and_never_refreshes_hidden_or_same_page(self):
        from unittest.mock import patch
        service = self.fixture.service
        with patch.object(service, '_declarations', wraps=service._declarations) as scan:
            self.resources_page.setProperty('refreshAllowed', False)
            self.resources_page.setProperty('active', True)
            QTest.qWait(200)
            self.assertEqual(scan.call_count, 0)
            # Leaving before idle cancels the intent, not a later hidden scan.
            self.resources_page.setProperty('active', False)
            self.resources_page.setProperty('refreshAllowed', True)
            QTest.qWait(200)
            self.assertEqual(scan.call_count, 0)
            self.resources_page.setProperty('active', True)
            self.until(lambda: scan.call_count == 1 and not service.busy and not self.manager.busy)
            self.resources_page.setProperty('active', True)
            QTest.qWait(200)
            self.assertEqual(scan.call_count, 1)
            self.resources_page.setProperty('active', False)
            self.resources_page.setProperty('active', True)
            self.until(lambda: scan.call_count == 2 and not service.busy and not self.manager.busy)

    def test_failed_activation_scan_does_not_retry_forever(self):
        from unittest.mock import patch
        service = self.fixture.service
        with patch.object(service, '_declarations', side_effect=ValueError('controlled-read-failure')) as scan:
            self.resources_page.setProperty('active', True)
            self.until(lambda: scan.call_count == 1 and not service.busy and not self.manager.busy)
            QTest.qWait(350)
            self.assertEqual(scan.call_count, 1)
            self.assertFalse(self.resources_page.property('activationRefreshPending'))

    def close_window(self):
        self.window.hide()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def close_manager(self):
        self.manager.shutdown()
        self.manager.pool.waitForDone(15000)
        APP.processEvents()

    def until(self, predicate):
        for _ in range(500):
            APP.processEvents()
            if predicate():
                return
            time.sleep(.01)
        self.fail("Qt action timed out")

    def item(self, name):
        def find(item):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                result = find(child)
                if result is not None:
                    return result
        result = find(self.window.contentItem())
        self.assertIsNotNone(result, name)
        return result

    def click(self, name):
        if name.startswith("resourceConfirm"):
            confirmation = self.window.findChild(QObject, "resourceConfirmationDialog")
            self.until(lambda: bool(confirmation.property("opened")))
        item = self.item(name)
        self.assertTrue(item.isVisible() and item.isEnabled(), name)
        def inside_window():
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            return 0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height()
        self.until(inside_window)  # Opening a popup also schedules its layout pass.
        point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
        self.assertTrue(0 <= point.x() < self.window.width() and 0 <= point.y() < self.window.height(), (name, point))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
        QTest.qWait(50)

    def test_retired_ordinary_entry_is_absent_in_actual_gallery(self):
        self.backend.setContext('s1', 'nightmare')
        APP.processEvents()
        selector = self.item('galleryContext')
        self.assertFalse(selector.property('ordinaryAvailable'))
        self.assertFalse(self.item('galleryContextDifficulty2').isVisible())
        selector.requestContext('s1', 'difficulty2')
        self.assertEqual(self.backend.mode, 'nightmare')

    def show_import(self):
        grid = self.item("galleryAuthorGrid")
        self.assertTrue(QMetaObject.invokeMethod(grid, "positionViewAtEnd"))
        QTest.qWait(50)
        self.click("galleryImportCard")
        self.until(lambda: bool(self.dialog.property("visible")))

    def assert_clean_qml(self):
        self.assertFalse(self.warnings, "\n".join(self.warnings))

    def test_delete_confirmation_cancel_escape_and_frozen_exact_target(self):
        self.assertEqual(self.item("galleryAuthorGrid").property("count"), 3)
        self.click("galleryRemovePackage")
        self.until(lambda: self.manager.pendingKind == "remove")
        self.assertIn(self.fixture.service.packageTitle("nightmare-suju"), self.confirmations[-1][0])
        self.assertTrue((self.fixture.root / resources_fixture.IMAGE).exists())
        # A second entry while inspection/confirmation owns the lease is inert.
        QMetaObject.invokeMethod(self.page, "removeSelectedPackage")
        self.assertEqual(len(self.confirmations), 1)
        self.click("resourceConfirmCancelButton")
        self.assertFalse(self.fixture.lease.active)
        self.assertTrue((self.fixture.root / resources_fixture.IMAGE).exists())
        self.assertIn("已取消", self.item("galleryOperationMessage").property("text"))
        self.click("galleryRefresh")
        self.assertEqual(self.item("galleryOperationMessage").property("text"), self.backend.message)
        self.click("galleryRemovePackage")
        self.until(lambda: self.manager.pendingKind == "remove")
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.until(lambda: self.manager.pendingKind == "")
        self.assertTrue((self.fixture.root / resources_fixture.IMAGE).exists())
        self.click("galleryRemovePackage")
        self.until(lambda: self.manager.pendingKind == "remove")
        self.backend.setProfile("author-b")  # Simulated late browse-state change.
        self.assertEqual(self.item("galleryOperationMessage").property("text"), self.backend.message)
        self.click("resourceConfirmAcceptButton")
        self.until(lambda: not self.manager.busy and not self.manager.packageRows)
        self.assertFalse((self.fixture.root / resources_fixture.IMAGE).exists())
        self.assertFalse(self.item("galleryRemovePackage").isEnabled())
        self.assertIn(("refresh", None), self.backend.calls)
        self.assertEqual(self.coordinator.calls, [])  # Never navigates away.
        self.assert_clean_qml()

    def test_modified_unknown_files_and_unregistered_seasons_are_protected(self):
        self.fixture.write(resources_fixture.IMAGE, b"user-edited-synthetic")
        unknown = self.fixture.root / "_internal/assets/suju/unknown.png"
        unknown.write_bytes(b"unknown-synthetic")
        self.manager.refresh()
        self.until(lambda: not self.manager.busy)
        # Lightweight metadata only enables an inspection, never deletion.
        self.assertTrue(self.item("galleryRemovePackage").isEnabled())
        self.assertFalse(self.confirmations)
        self.click("galleryRemovePackage")
        self.until(lambda: self.manager.pendingKind == "remove")
        self.assertTrue(self.manager._pending["preserved"])
        self.manager.confirmOperation(False)
        self.assertEqual((self.fixture.root / resources_fixture.IMAGE).read_bytes(), b"user-edited-synthetic")
        self.assertEqual(unknown.read_bytes(), b"unknown-synthetic")
        self.assertEqual(len(self.confirmations), 1)
        self.backend.setProfile("author-b")
        self.assertFalse(self.item("galleryRemovePackage").isEnabled())
        self.backend.setProfile("author-a")
        for season in ("s1", "s7"):
            self.backend.change("_season", season)
            self.assertFalse(self.item("galleryRemovePackage").isEnabled())
            self.assertEqual(self.item("galleryImportCard").isEnabled(), season == "s1")
        self.assert_clean_qml()

    def test_s1_card_imports_through_shared_dialog_with_frozen_browse_season(self):
        author = s1_fixture.S1AuthorPackageTests()
        author.setUp()
        self.addCleanup(author.doCleanups)
        shutil.copytree(author.root, self.fixture.root, dirs_exist_ok=True)
        self.fixture.write("_internal/version.json", json.dumps(dict(
            displayVersion="1.0.17", numericVersion="1.0.0.21")).encode())
        self.backend.change("_season", "s1")
        self.fixture.completions.clear()
        self.fixture.changes.clear()
        self.dialog.setProperty("currentFolder", QUrl.fromLocalFile(str(author.archive.parent)))
        self.dialog.setProperty("selectedFile", QUrl.fromLocalFile(str(author.archive)))
        self.show_import()
        self.assertEqual(self.dialog.property("targetSeason"), "s1")
        self.assertFalse(self.item("galleryImportCard").isEnabled())
        # A browse change while the chooser is open must not relabel its ZIP.
        self.backend.change("_season", "s0")
        QMetaObject.invokeMethod(self.dialog, "accept")
        self.until(lambda: bool(self.fixture.completions) or bool(self.fixture.problems))
        self.until(lambda: not self.fixture.service.busy and not self.manager.busy)
        self.assertFalse(self.fixture.problems, self.fixture.problems)
        self.assertEqual(len(self.fixture.completions), 1)
        receipt = json.loads((self.fixture.root / "work/map-packages/s1-jiaye-handnotes.json").read_text())
        self.assertEqual(receipt["season"], "s1")
        self.assertEqual(len(receipt["files"]), 88)
        self.assertTrue(self.fixture.changes)
        self.assertIn(("refresh", None), self.backend.calls)
        self.assertEqual(self.coordinator.calls, [])
        self.assertFalse(self.fixture.lease.active)
        self.assert_clean_qml()

    def test_plus_card_empty_narrow_keyboard_open_cancel_and_accept_import(self):
        self.click("galleryRemovePackage")
        self.until(lambda: self.manager.pendingKind == "remove")
        self.click("resourceConfirmAcceptButton")
        self.until(lambda: not self.manager.busy and not self.manager.packageRows)
        self.assertFalse((self.fixture.root / resources_fixture.IMAGE).exists())
        self.fixture.completions.clear()
        self.fixture.changes.clear()
        self.backend.calls.clear()
        self.show_import()
        self.assertEqual(self.dialog.property("targetSeason"), "s0")
        self.assertFalse(self.item("galleryImportCard").isEnabled())
        before = self.fixture.service.operationGeneration
        QMetaObject.invokeMethod(self.dialog, "reject")
        self.until(lambda: not self.dialog.property("visible"))
        self.assertEqual(self.fixture.service.operationGeneration, before)
        self.assertEqual(self.item("galleryOperationMessage").property("text"), self.backend.message)
        self.backend.catalog = []
        self.backend.selected = {}
        self.backend.changed.emit()
        self.window.resize(514, 440)
        QTest.qWait(80)
        self.assertEqual(self.item("galleryAuthorGrid").property("count"), 1)
        self.assertTrue(self.item("galleryImportCard").isVisible())
        archive = self.fixture.package("selected.zip")
        self.dialog.setProperty("currentFolder", QUrl.fromLocalFile(str(archive.parent)))
        self.dialog.setProperty("selectedFile", QUrl.fromLocalFile(str(archive)))
        self.window.requestActivate()
        QTest.qWait(50)
        self.item("galleryImportCard").forceActiveFocus(Qt.OtherFocusReason)
        QTest.keyClick(self.window, Qt.Key_Space)
        self.until(lambda: bool(self.dialog.property("visible")))
        self.assertEqual(self.fixture.service.operationGeneration, before)
        # The real visible Qt chooser accepts the selected temporary archive.
        QMetaObject.invokeMethod(self.dialog, "accept")
        self.until(lambda: self.fixture.service.operationGeneration > before and not self.fixture.service.busy)
        self.until(lambda: not self.manager.busy and len(self.manager.packageRows) == 1)
        self.assertEqual((self.fixture.root / resources_fixture.IMAGE).read_bytes(), b"route")
        self.assertEqual(len(self.fixture.completions), 1)
        self.assertTrue(self.fixture.changes)
        self.assertIn(("refresh", None), self.backend.calls)
        self.assertEqual(self.manager.packageRows[0]["packageId"], "nightmare-suju")
        self.assertEqual(self.coordinator.calls, [])
        self.assert_clean_qml()

    def test_invalid_archive_reports_failure_and_new_browse_error_is_not_hidden(self):
        archive = self.fixture.folder / "invalid.zip"
        archive.write_bytes(b"not-a-zip")
        self.dialog.setProperty("selectedFile", QUrl.fromLocalFile(str(archive)))
        self.show_import()
        before = self.fixture.service.operationGeneration
        self.fixture.completions.clear()
        QMetaObject.invokeMethod(self.dialog, "accept")
        self.until(lambda: self.fixture.service.operationGeneration > before and not self.fixture.service.busy)
        self.assertFalse(self.fixture.completions)
        self.assertTrue(self.fixture.problems)
        self.assertEqual(self.item("galleryOperationMessage").property("text"), self.fixture.service.message)
        self.assertEqual((self.fixture.root / resources_fixture.IMAGE).read_bytes(), b"route")
        self.backend.error = "资源读取失败：测试的读取异常"
        self.backend.changed.emit()
        self.assertEqual(self.item("galleryOperationMessage").property("text"), self.backend.error)
        self.assert_clean_qml()


if __name__ == "__main__":
    unittest.main()
