"""Owned Qt save-dialog acceptance reaches the original gallery export schema."""
import json
from pathlib import Path
import unittest
from PySide6.QtCore import QObject, QUrl, QMetaObject, QCoreApplication, QEvent
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
import test_client_custom_gallery as fixture
from test_client_custom_gallery import APP, until


class GallerySaveDialogTests(unittest.TestCase):
    setUp = fixture.CustomGalleryTests.setUp
    cleanup = fixture.CustomGalleryTests.cleanup
    call = fixture.CustomGalleryTests.call

    def test_selected_user_path_is_written_only_after_owned_save_dialog_accepts(self):
        self.call('createGallery', 'Chosen export')
        self.call('addPoint', 20., 20.)
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows:warnings.extend(r.toString() for r in rows))
        engine.rootContext().setContextProperty('customGalleryService',self.service)
        engine.loadData(b'import QtQuick\nimport QtQuick.Window\nWindow { width: 1000; height: 720; visible: true; ClientCustomGallery { anchors.fill: parent } }',QUrl.fromLocalFile(str(Path(__file__).with_name('GallerySaveEvidence.qml'))))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        def close_window():
            window.hide();engine.deleteLater()
            QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);APP.processEvents()
        self.addCleanup(close_window)
        dialog = window.findChild(QObject,'customExportDialog')
        self.assertIsNotNone(dialog)
        destination = self.sandbox/'user-chosen.json'
        self.assertTrue(dialog.setProperty('options',8))  # Qt DontUseNativeDialog: own offscreen surface.
        self.assertTrue(dialog.setProperty('selectedFile',QUrl.fromLocalFile(str(destination))))
        self.assertTrue(QMetaObject.invokeMethod(dialog,'open'))
        until(lambda: bool(dialog.property('visible')))
        self.assertFalse(destination.exists())
        self.assertTrue(QMetaObject.invokeMethod(dialog,'accept'))
        until(lambda: destination.is_file() and not self.service.busy)
        data = json.loads(destination.read_text('utf8'))
        self.assertEqual(data['schema'],'xingtu-custom-gallery-v1')
        self.assertEqual(data['name'],'Chosen export')
        self.assertEqual(len(data['maps']['synthetic-map']['points']),1)
        self.assertFalse(warnings,'\n'.join(warnings))


if __name__=='__main__':unittest.main()
