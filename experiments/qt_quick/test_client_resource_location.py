"""Actual Qt migration worker and original-package consumers, isolated paths."""
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QMetaObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickView

from client_imported_activation import activate_context
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture
from resource_transaction import resource_runtime_lease


class ResourceLocationServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.source_temp = tempfile.TemporaryDirectory(prefix='location-source-')
        cls.addClassCleanup(cls.source_temp.cleanup)
        cls.source = OriginalS0DifficultSourceFixture(Path(cls.source_temp.name))
        cls.source.export()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='location-client-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        profile = root/'profile'
        profile.mkdir()
        environment = patch.dict(os.environ, LOCALAPPDATA=str(profile))
        environment.start()
        self.addCleanup(environment.stop)
        program = root/'install/program'
        program.mkdir(parents=True)
        self.paths = RuntimePaths(program, True)
        self.legacy = self.paths.legacy_imported_resource_root
        saved = save_archive(self.source.output, self.legacy)
        self.refs = {ref.component_id: ref for ref in saved.component_refs}
        old_resolver = ResourceResolver(self.legacy, supported_capabilities=SUPPORTED_CAPABILITIES)
        old_paths = SimpleNamespace(imported_resource_root=self.legacy, memory_root=self.paths.memory_root)
        with resource_runtime_lease(self.legacy):
            activate_context(old_resolver, old_paths, self.refs['base'], self.refs['controls'],
                             season='s0', mode='difficult')
        self.before = (self.legacy/'catalog.json').read_bytes()
        self.resolver = ResourceResolver(self.paths.imported_resource_root,
                                         supported_capabilities=SUPPORTED_CAPABILITIES)
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.runtime = LiveRuntime(engine_factories=self.factories, evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.resolver, paths=self.paths, engine_factories=self.factories)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.addCleanup(self.close)
        self.service.listPackages()
        self.wait()

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()

    def wait(self):
        deadline = time.monotonic()+90
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.service.busy)
        self.app.processEvents()

    def test_actual_worker_migrates_selection_and_explicit_cleanup_keeps_target(self):
        note = self.legacy/'personal-note.txt'
        note.write_bytes(b'unknown file preserved')
        self.assertEqual(self.service.resourceDirectory, str(self.paths.installation_root/'resources'))
        self.assertTrue(self.service.canMigrateLegacyResources)
        self.assertFalse(self.resolver.root.exists())
        self.assertTrue(self.service.migrateLegacyResources())
        self.assertTrue(self.service.pendingWrites)
        self.wait()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.changes, [True])
        self.assertEqual((self.resolver.root/'catalog.json').read_bytes(), self.before)
        self.assertEqual((self.legacy/'catalog.json').read_bytes(), self.before)
        self.assertIn('s0:difficult', self.service.availableContexts)
        self.assertTrue(self.factories.get_engine('difficult').qt_native_base.ready)
        self.assertTrue(self.service.canCleanupLegacyResources)
        self.assertTrue(self.service.cleanupLegacyResources())
        self.wait()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.changes, [True])
        self.assertEqual((self.resolver.root/'catalog.json').read_bytes(), self.before)
        self.assertEqual(note.read_bytes(), b'unknown file preserved')
        old_package = self.legacy/'packages'/self.refs['base'].package_id/self.refs['base'].resource_version
        self.assertFalse(old_package.exists())
        self.assertFalse(self.service.pendingWrites)

    def test_qml_requires_confirmation_and_uses_the_real_migration_slot(self):
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientImportedResources.qml'))))
        self.assertEqual(view.errors(), [])
        view.resize(1000, 720)
        view.show()
        self.app.processEvents()
        panel = view.rootObject()
        button = panel.findChild(QObject, 'migrateLegacyResourcesButton')
        dialog = panel.findChild(QObject, 'legacyResourceLocationDialog')
        confirm = panel.findChild(QObject, 'confirmLegacyResourceLocationButton')
        self.assertIsNotNone(button)
        self.assertIsNotNone(dialog)
        self.assertIsNotNone(confirm)
        self.assertTrue(button.property('enabled'))
        self.assertTrue(QMetaObject.invokeMethod(button, 'clicked'))
        self.app.processEvents()
        self.assertTrue(dialog.property('visible'))
        self.assertFalse(self.service.busy)
        self.assertFalse(self.resolver.root.exists())
        self.assertTrue(QMetaObject.invokeMethod(confirm, 'clicked'))
        self.wait()
        self.assertEqual(self.errors, [])
        self.assertTrue(self.service.contextAvailable('s0', 'difficult'))
        self.assertEqual((self.legacy/'catalog.json').read_bytes(), self.before)


if __name__ == '__main__':
    unittest.main()
