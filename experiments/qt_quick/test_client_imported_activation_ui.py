"""Qt resource owner -> idle session lease -> actual package activation.

No native input, capture, replacement engine or game acceptance. A delayed
adapter only controls cancellation timing; its real implementation still runs.
"""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QMetaObject, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from imported_resource_catalog import ResourceResolver
import test_imported_native_base as base_fixture
from test_client_live_runtime import FixtureCapture


class ImportedActivationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        base_fixture.ImportedNativeBaseTests.setUpClass()
        cls.addClassCleanup(base_fixture.ImportedNativeBaseTests.doClassCleanups)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='resource-activation-ui-')
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
        self.wait_until(lambda:not self.service.busy)

    def wait_until(self, condition):
        deadline = time.monotonic() + 40
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(condition(), self.service.message)

    def save(self):
        archive = base_fixture.ImportedNativeBaseTests.archive
        self.service.importArchive(QUrl.fromLocalFile(str(archive)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(self.changes)
        self.assertTrue(self.service.packageRows[0]['canActivate'])
        return self.service.packageRows[0]['key']

    def test_save_activate_actual_engine_and_repeat_without_catalog_change(self):
        key = self.save()
        model = self.service.packageModel
        self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
        self.assertFalse(self.service.pendingWrites)  # Read-only planning first.
        self.wait_until(lambda:self.service.pendingWrites)
        self.assertTrue(self.service.pendingWrites)
        self.assertEqual(self.session.acquireResourceLease(), '')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertIs(self.service.packageModel, model)
        self.assertIn('S0 噩梦', self.service.message)
        engine = self.factories.get_engine('nightmare')
        self.assertTrue(engine.qt_native_base.ready)
        self.assertEqual(engine.qt_controls_profile.for_context('s0', 'nightmare').contract.floor_ids,
                         ('B1', '1', '2'))
        catalog = (self.resolver.root / 'catalog.json').read_bytes()
        self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
        self.wait()
        self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), catalog)
        self.assertEqual(self.changes, [True])
        self.assertIs(self.factories.get_engine('nightmare'), engine)
        self.assertIn('已经启用', self.service.message)
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        self.session.releaseResourceLease(lease)

    def test_active_round_and_unknown_selection_cannot_start_activation(self):
        key = self.save()
        self.assertFalse(self.service.activatePackage('unknown', 's0', 'nightmare'))
        self.assertFalse(self.service.activatePackage(key, 's1', 'nightmare'))
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
        self.wait()
        # The actual start command prepares this imported engine. No frame is
        # captured: the injected capture only records enable/disable state.
        self.assertTrue(self.runtime.dispatch('start_round', {})['success'])
        self.session.refreshRuntime()
        self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
        self.wait()  # Read-only planning is allowed; actual admission is not.
        self.assertIn('结束本局', self.service.message)
        self.assertFalse(self.service.pendingWrites)
        self.assertEqual(self.changes, [True])

    def test_cancelled_activation_releases_lease_and_keeps_saved_version(self):
        from client_imported_activation import activate_context
        key = self.save()
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test adapter was not released')
            return activate_context(*args, **kwargs)
        with patch('client_imported_activation.activate_context', side_effect=delayed):
            self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
            try:
                self.wait_until(entered.is_set)
                self.service.cancel()
            finally:
                release.set()
            self.wait()
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertTrue((self.resolver.root / 'packages').is_dir())
        self.assertTrue(self.errors[-1]['errorCode'].endswith('-cancelled'), self.errors)
        self.assertFalse(self.changes)
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        self.session.releaseResourceLease(lease)

    def test_shutdown_drains_activation_and_suppresses_late_ui_signals(self):
        from client_imported_activation import activate_context
        key = self.save()
        before = self.service.packageRows
        entered, release = threading.Event(), threading.Event()
        def delayed(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test adapter was not released')
            return activate_context(*args, **kwargs)
        with patch('client_imported_activation.activate_context', side_effect=delayed):
            self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
            try:
                self.wait_until(entered.is_set)
                self.service.shutdown()
            finally:
                release.set()
                self.service.pool.waitForDone()
                self.app.processEvents()
        self.assertFalse(self.service.pendingWrites)
        self.assertFalse(self.errors)
        self.assertFalse(self.changes)
        self.assertEqual(self.service.packageRows, before)
        self.assertFalse((self.resolver.root / 'catalog.json').exists())

    def test_committed_activation_remains_success_when_followup_list_fails(self):
        key = self.save()
        with patch('imported_resource_inventory.list_saved_versions', side_effect=OSError('controlled list failure')):
            self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
            self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertIsNotNone(self.resolver.snapshot().generation)
        self.assertIn('列表暂时无法读取', self.service.message)
        self.service.listPackages()
        self.wait()
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')

    def test_retained_pending_operation_exposes_recovery_and_releases_lease(self):
        import imported_resource_activation as publication
        key = self.save()
        activate = publication.activate_catalog_locked
        def interrupted(*args, **kwargs):
            def stage(name):
                if name == 'previous-retained':
                    raise OSError('controlled publication interruption')
            return activate(*args, on_stage=stage, **kwargs)
        with patch.object(publication, 'activate_catalog_locked', side_effect=interrupted), \
             patch.object(publication, 'recover_catalog_locked', side_effect=OSError('controlled recovery interruption')):
            self.assertTrue(self.service.activatePackage(key, 's0', 'nightmare'))
            self.wait()
        self.assertTrue(self.errors)
        self.assertTrue(self.service.recoveryRequired)
        self.assertFalse(self.service.pendingWrites)
        self.assertFalse(self.changes)
        self.service.requestRecovery()
        self.wait()
        self.assertFalse(self.service.recoveryRequired)
        self.assertFalse((self.resolver.root / 'transactions/pending-catalog.json').exists())
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertEqual(self.changes, [True])

    def test_actual_qml_button_activates_the_saved_package(self):
        key = self.save()
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

        button, deadline = None, time.monotonic() + 5
        while button is None and time.monotonic() < deadline:
            self.app.processEvents()
            button = find(view.rootObject(), 'activatePackageButton-' + key)
            time.sleep(.005)
        self.assertIsNotNone(button, 'the real QML delegate did not expose its activation button')
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        evidence = os.environ.get('XINGTU_TEST_ACTIVATION_UI_EVIDENCE')
        if evidence:
            directory = Path(evidence).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            for width, height in ((650, 470), (1000, 720)):
                view.resize(width, height)
                for _ in range(10):
                    self.app.processEvents()
                    time.sleep(.01)
                pixels = view.grabWindow()
                self.assertFalse(pixels.isNull())
                self.assertTrue(pixels.save(str(directory / f'activation-{width}x{height}.png')))
        self.assertTrue(QMetaObject.invokeMethod(button, 'clicked'))
        self.assertTrue(self.service.busy)
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertTrue(self.factories.get_engine('nightmare').qt_native_base.ready)


if __name__ == '__main__':
    unittest.main()
