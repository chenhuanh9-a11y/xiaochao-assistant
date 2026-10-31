"""Offscreen production QML callbacks and real service queue; isolated files only."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QGuiApplication
from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtQml import QQmlEngine, QQmlComponent

APP = QGuiApplication.instance() or QGuiApplication([])
import test_client_resources as fixtures
import package_lifecycle as lifecycle


class CleanupPageTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ResourceServiceTests("runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    @unittest.skipUnless(isinstance(APP, QGuiApplication), "run this QML test in its own GUI application process")
    def test_actual_qml_clear_button_and_dialog_reach_one_cleanup(self):
        self.f.install()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("resourceService", self.f.service)
        component = QQmlComponent(engine)
        source = b'''import QtQuick
import QtQuick.Controls.Basic
import "."
ApplicationWindow { width: 1080; height: 720; visible: true
    ClientResources { anchors.fill: parent; anchors.margins: 20 }
}'''
        component.setData(source, QUrl.fromLocalFile(str(Path(__file__).with_name("cleanup-fixture.qml"))))
        self.assertFalse(component.isError(), "\n".join(e.toString() for e in component.errors()))
        window = component.create()
        self.assertIsNotNone(window, "\n".join(e.toString() for e in component.errors()))
        try:
            self.f.settle()
            button = window.findChild(QObject, "cleanupAllPackagesButton")
            self.assertIsNotNone(button)
            self.assertTrue(QMetaObject.invokeMethod(button, "clicked"))
            self.f.settle()
            self.assertEqual(len(self.f.confirmations), 1)
            dialog = window.findChild(QObject, "resourceConfirmationDialog")
            self.assertIsNotNone(dialog)
            self.assertTrue(dialog.property("visible"))
            self.assertTrue(QMetaObject.invokeMethod(dialog, "accept"))
            self.f.settle()
            self.assertFalse((self.f.root / fixtures.IMAGE).exists())
            self.assertEqual(len(self.f.changes), 2)
            self.assertFalse(self.f.lease.active)
        finally:
            window.close()
            window.deleteLater()
            APP.processEvents()

    def test_finish_cleanup_after_update_keeps_new_package(self):
        self.f.install(self.f.package("old.zip", b"old"))
        real = fixtures.importer._rollback_published
        def held(path, *args):
            if "/lifecycle/" in Path(path).as_posix() and "/old/" in Path(path).as_posix():
                raise OSError("fixture retained object busy")
            return real(path, *args)
        with patch.object(fixtures.importer, "_rollback_published", side_effect=held):
            self.f.install(self.f.package("new.zip", b"new"))
        self.assertTrue(self.f.service.cleanupPending)
        self.assertEqual((self.f.root / fixtures.IMAGE).read_bytes(), b"new")
        self.f.service.requestFinishCleanup()
        self.f.settle()
        self.assertTrue(self.f.service._pending["pendingOnly"])
        self.f.service.confirmOperation(True)
        self.f.settle()
        self.assertFalse(self.f.service.cleanupPending)
        self.assertEqual((self.f.root / fixtures.IMAGE).read_bytes(), b"new")
        self.assertFalse(list((self.f.root / lifecycle.STATE).iterdir()))
        self.assertFalse(self.f.lease.active)


if __name__ == "__main__":
    unittest.main()
