"""S1D resource-page activation without general live authorization."""
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from imported_resource_catalog import ResourceResolver
from test_client_live_runtime import FixtureCapture
import test_export_s1_native_base_package as source_fixture
from original_qt_resource_fixtures import OriginalS1SourceFixture


class ImportedS1ActivationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QGuiApplication.instance()
        if existing is not None and not isinstance(existing, QGuiApplication):
            raise RuntimeError('QML activation tests require a QGuiApplication singleton')
        cls.app = existing or QGuiApplication([])
        temporary = tempfile.TemporaryDirectory(prefix='s1-activation-ui-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = OriginalS1SourceFixture(Path(temporary.name).resolve())
        cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s1-activation-ui-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root,
                                         supported_capabilities=SUPPORTED_CAPABILITIES)
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.runtime = LiveRuntime(engine_factories=self.factories,
                                   capture_provider=FixtureCapture(), evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.resolver, paths=self.paths)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.addCleanup(self.close)

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()
        self.app.processEvents()

    def wait(self):
        deadline = time.monotonic() + 90
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.service.busy, self.service.message)

    def save(self):
        # Dormant history is the precondition for explicit switch/retry tests;
        # product import activates automatically in its own regressions.
        from imported_resource_archive import save_archive
        save_archive(self.source.output, self.resolver.root)
        self.service.listPackages()
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(self.changes)
        return self.service.packageRows[0]['key']

    def test_product_import_activates_s1_base_and_controls_without_live_authority(self):
        self.service.importArchive(QUrl.fromLocalFile(str(self.source.output)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.availableContexts, ['s1:difficult'])
        self.assertFalse(self.resolver.snapshot().resolve_context('s1', 'difficult').gaps)
        engine = self.factories.get_engine('difficult', season='s1')
        self.assertTrue(engine.qt_native_base.ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse(engine.memory_store.root.exists())

    def test_qml_pointer_click_activates_data_and_preserves_unavailable_floor_notice(self):
        key = self.save()
        row = self.service.packageRows[0]
        self.assertTrue(row['canActivate'])
        self.assertEqual(row['activatableContexts'], ['s1:difficult'])
        if os.name == 'nt':
            for name in ('msyh.ttc', 'msyhbd.ttc'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR']) / 'Fonts' / name))
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientImportedResources.qml'))))
        self.assertEqual(view.errors(), [])
        view.resize(650, 470)
        view.show()

        def find(item, name):
            if item.objectName() == name:
                return item
            for child in item.childItems():
                found = find(child, name)
                if found is not None:
                    return found
            return None

        button_name = 'activatePackageButton-' + key + '-s1-difficult'
        button, deadline = None, time.monotonic() + 5
        while button is None and time.monotonic() < deadline:
            self.app.processEvents()
            button = find(view.rootObject(), button_name)
            time.sleep(.005)
        self.assertIsNotNone(button)
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.property('text'), '启用 S1 困难数据')
        notice = find(view.rootObject(), 'activationNotice-' + key + '-s1-difficult')
        self.assertIsNotNone(notice)
        self.assertTrue(notice.isVisible())
        self.assertIn('自动楼层识别', notice.property('text'))
        evidence = os.environ.get('XINGTU_TEST_S1_ACTIVATION_UI_EVIDENCE')
        if evidence:
            directory = Path(evidence).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            for width, height in ((650, 470), (1000, 720)):
                view.resize(width, height)
                QTest.qWait(100)
                pixels = view.grabWindow()
                self.assertFalse(pixels.isNull())
                self.assertTrue(pixels.save(str(directory / f's1-activation-{width}x{height}.png')))
        model = self.service.packageModel
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        self.assertTrue(self.service.busy, 'the real QML pointer event did not start activation')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertIs(self.service.packageModel, model)
        self.assertIs(find(view.rootObject(), button_name), button)
        self.assertIn('S1 困难', self.service.message)
        self.assertIn('自动楼层识别', self.service.message)
        self.assertIn('不可用', self.service.message)
        engine = self.factories.get_engine('difficult', season='s1')
        self.assertEqual(len(engine.pack.maps), 14)
        self.assertFalse(engine.qt_live_ready)
        self.assertTrue(engine.qt_native_context.structure_candidate_capability['candidateReady'])
        self.assertFalse(engine.qt_native_context.structure_candidate_capability['liveValidated'])
        self.assertFalse(engine.memory_store.root.exists())
        before = (self.resolver.root / 'catalog.json').read_bytes()
        self.assertTrue(self.service.activatePackage(key, 's1', 'difficult'))
        self.wait()
        self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), before)
        self.assertEqual(self.changes, [True])
        self.assertIn('不可用', self.service.message)

    def test_wrong_context_and_busy_session_do_not_publish_selection(self):
        key = self.save()
        self.assertFalse(self.service.activatePackage(key, 's1', 'nightmare'))
        self.assertFalse(self.service.activatePackage(key, 's0', 'nightmare'))
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        try:
            self.assertTrue(self.service.activatePackage(key, 's1', 'difficult'))
            self.wait()  # Planning holds no lease; publication still requires it.
            self.assertIn('结束本局', self.service.message)
            self.assertFalse(self.service.pendingWrites)
            self.assertFalse((self.resolver.root / 'catalog.json').exists())
        finally:
            self.session.releaseResourceLease(lease)


if __name__ == '__main__':
    unittest.main()
