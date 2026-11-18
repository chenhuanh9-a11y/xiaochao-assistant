"""Resource-page author actions through the real lease/catalog/image path.

Sources and author pixels are synthetic. Capture is a recording-only fixture;
these tests neither open a game nor claim file-picker or physical-pixel proof.
"""
import os
import json
from pathlib import Path
import shutil
import threading
import time
import zipfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest

import numpy as np

import client_imported_activation as activation
from client_imported_providers import build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_route_preferences import prepare_display_layer
from client_session import SessionService
import test_client_imported_author_selection as source_fixture
from test_client_live_runtime import FixtureCapture


class ClientImportedAuthorUiTests(source_fixture._AuthorSelectionFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        if os.name == 'nt':
            for name in ('msyh.ttc', 'msyhbd.ttc'):
                QFontDatabase.addApplicationFont(str(Path(os.environ['WINDIR'])/'Fonts'/name))
        super().setUpClass()

    def setUp(self):
        from client_paths import WORK_ROOT
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with patch('tempfile.tempdir', str(WORK_ROOT)):
            super().setUp()
        (self.root / '.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')
        self.active_resolver = self.resolver()
        self.factories = build_imported_engine_factories(self.active_resolver, self.paths)
        self.runtime = LiveRuntime(engine_factories=self.factories,
                                  capture_provider=FixtureCapture(), evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.active_resolver, paths=self.paths,
                                               engine_factories=self.factories)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes = [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        # The real coordinator performs this same runtime boundary after
        # clearing its views. It owns provider invalidation, including retry.
        self.service.resourcesChanged.connect(lambda: self.runtime.invalidate('resources-changed'))
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

    def list_packages(self):
        self.service.listPackages()
        self.wait()

    @staticmethod
    def key(view):
        ref = view.component_ref
        return f'{ref.package_id}/{ref.resource_version}'

    @staticmethod
    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = ClientImportedAuthorUiTests.find(child, name)
            if found is not None:
                return found
        return None

    def test_import_makes_author_available_without_separate_management_activation(self):
        from client_imported_browse import load_imported_browse_context
        self.seed()
        display, document, _ = self.package(package_id='author-import-visibility')
        incoming = self.root / 'incoming.xingtu-route'
        shutil.copy2(self.root / f'author-{self.counter}.zip', incoming)
        self.assertEqual(self.active_resolver.snapshot().displays, ())
        notifications = []
        self.service.importCompleted.connect(lambda title, message: notifications.append(title))
        self.service.importArchive(QUrl.fromLocalFile(str(incoming)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        context = load_imported_browse_context(self.active_resolver, 'difficult', season='s1')
        self.assertIn('author-fixture', [p.profile_id for p in context.profiles if p.available_layers])
        self.assertEqual(self.active_resolver.snapshot().displays, (display.component_ref,))
        self.assertEqual(notifications, ['作者路线已启用'])
        self.assertEqual(self.changes, [True])
        engine = self.factories.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        np.testing.assert_array_equal(layer['pixels'][1, 2], (3, 2, 1, 0))
        np.testing.assert_allclose(layer['matrixDisplayToReference'], document['layers'][0]['matrixDisplayToReference'])
        catalog = (self.resources / 'catalog.json').read_bytes()
        self.service.importArchive(QUrl.fromLocalFile(str(incoming)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual((self.resources / 'catalog.json').read_bytes(), catalog)
        self.assertEqual(self.changes, [True])

    def _removal_gallery(self):
        from backend import Backend
        from client_imported_gallery_images import ImportedGalleryImages
        from client_settings import SettingsService
        images = ImportedGalleryImages()
        backend = Backend(self.root, resource_resolver=self.active_resolver,
                          gallery_images=images, defer_loading=True)
        settings = SettingsService(self.root)
        self.addCleanup(settings.shutdown)
        self.addCleanup(backend.shutdown)
        self.service.resourcesChanged.connect(backend.invalidateResources)
        backend.setContext('s1', 'difficult')
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.engine().addImageProvider('importedgallery', images)
        context = view.rootContext()
        for name, value in dict(resourceService=self.service, backend=backend,
                                session=self.session, settingsService=settings).items():
            context.setContextProperty(name, value)
        source = QUrl.fromLocalFile(str(Path(__file__).with_name('RemovalGalleryHarness.qml')))
        component = QQmlComponent(view.engine())
        component.setData(b'''import QtQuick
Item {
    ClientImportedResources { id: packages; anchors.fill: parent; visible: false }
    ClientGallery { anchors.fill: parent; packageActions: packages }
}''', source)
        self.assertTrue(component.isReady(), [error.toString() for error in component.errors()])
        root = component.create(context)
        self.assertIsNotNone(root, [error.toString() for error in component.errors()])
        view.setContent(source, component, root)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.resize(1000, 750)
        view.show()
        self._wait_until(lambda: not backend.busy and len(backend.profiles) > 0)
        self.assertFalse(backend.hasError, backend.message)
        return view, backend, images

    def test_mixed_legacy_transport_is_rejected_before_saving_or_activating(self):
        display, _, _ = self.package(package_id='author-mixed-source')
        with zipfile.ZipFile(self.source.output) as base_zip, zipfile.ZipFile(
                self.root / f'author-{self.counter}.zip') as route_zip:
            manifest = json.loads(base_zip.read('manifest.json'))
            route_manifest = json.loads(route_zip.read('manifest.json'))
            manifest['packageId'] = 'mixed-import'
            manifest['components'].extend(route_manifest['components'])
            manifest['files'].extend(route_manifest['files'])
            members = {name: base_zip.read(name) for name in base_zip.namelist() if name != 'manifest.json'}
            members.update({name: route_zip.read(name) for name in route_zip.namelist() if name != 'manifest.json'})
        self.seed()
        before = (self.resources / 'catalog.json').read_bytes()
        incoming = self.root / 'mixed.zip'
        with zipfile.ZipFile(incoming, 'x') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            for name, raw in members.items():
                archive.writestr(name, raw)
        self.service.importArchive(QUrl.fromLocalFile(str(incoming)).toString())
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-package-kind-unsupported')
        self.assertFalse((self.resources / 'packages/mixed-import' / manifest['resourceVersion']).exists())
        self.assertEqual((self.resources / 'catalog.json').read_bytes(), before)
        self.assertEqual(self.active_resolver.snapshot().displays, ())
        self.assertTrue(display.path('images/route.png').exists())

    def _wait_until(self, predicate):
        deadline = time.monotonic() + 90
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), self.service.message)

    def _click(self, view, name):
        self._wait_until(lambda: self.find(view.contentItem(), name) is not None)
        button = self.find(view.contentItem(), name)
        self.assertTrue(button.isVisible(), name)
        self.assertTrue(button.isEnabled(), name)
        QTest.qWait(30)
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, point)

    def test_gallery_removes_exact_author_version_then_base_and_can_reimport(self):
        from PySide6.QtCore import QSize
        old, _, _ = self.package(package_id='author-remove-ui')
        current, _, _ = self.package(package_id='author-remove-ui', resource_version='v2')
        current_directory = current.root.parent.parent
        route_source = self.root / f'author-{self.counter}.zip'
        other, _, _ = self.package(package_id='author-retained-ui', profile_id='author-retained-ui')
        self.seed(displays=(current.component_ref, other.component_ref))
        self.list_packages()
        view, backend, images = self._removal_gallery()
        self._click(view, 'galleryAuthor_author-fixture')
        self._wait_until(lambda: backend.profileId == 'author-fixture')
        old_url = backend.selectedImage
        self.assertEqual(next(row for row in backend.profiles if row['id'] == 'author-fixture')['packageKey'], self.key(current))
        before = (self.resources / 'catalog.json').read_bytes()
        self._click(view, 'galleryRemovePackage')
        self.wait()
        self.assertTrue(self.service.removalPlanOpen)
        self.assertEqual(self.service._removal_plan['resourceVersion'], 'v2')
        self._click(view, 'cancelRemovalPlanButton')
        self.assertFalse(self.service.removalPlanOpen)
        self.assertEqual((self.resources / 'catalog.json').read_bytes(), before)
        self.assertTrue(current.path('images/route.png').is_file())
        self._click(view, 'galleryRemovePackage')
        self.wait()
        self._click(view, 'confirmRemovalPlanButton')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self._wait_until(lambda: not backend.busy)
        self.assertEqual(self.active_resolver.snapshot().displays, (other.component_ref,))
        self.assertTrue(old.path('images/route.png').is_file())
        self.assertTrue(other.path('images/route.png').is_file())
        self.assertTrue(route_source.is_file())
        self.assertFalse(current_directory.exists())
        self.assertNotIn('author-fixture', [row['id'] for row in backend.profiles])
        self.assertTrue(images.requestImage(old_url.split('image://importedgallery/', 1)[1], QSize(), QSize()).isNull())
        self.assertTrue(self.service.contextAvailable('s1', 'difficult'))
        self._click(view, 'galleryAuthor_s1-native-structure')
        self._wait_until(lambda: backend.profileId == 's1-native-structure')
        self._click(view, 'galleryRemovePackage')
        self.wait()
        self.assertIn('S1 困难', self.service.removalPlanSummary)
        self._click(view, 'confirmRemovalPlanButton')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(self.service.contextAvailable('s1', 'difficult'))
        self.assertEqual(self.active_resolver.snapshot().displays, ())
        self.assertTrue(other.path('images/route.png').is_file())
        self._wait_until(lambda: not backend.busy)
        self.assertEqual(backend.profiles, [])
        self.assertTrue(self.service.packageModel is self.service.model)
        # Re-import the same base and route through the public service, rather
        # than bypassing the UI's automatic activation after saving.
        incoming_base = self.root / 'reimport.xingtu-base'
        shutil.copy2(self.source.output, incoming_base)
        self.service.importArchive(QUrl.fromLocalFile(str(incoming_base)).toString())
        self.wait()
        self.assertTrue(self.service.contextAvailable('s1', 'difficult'), self.errors)
        incoming_route = self.root / 'reimport.xingtu-route'
        shutil.copy2(route_source, incoming_route)
        self.service.importArchive(QUrl.fromLocalFile(str(incoming_route)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self._wait_until(lambda: not backend.busy)
        self.assertIn('author-fixture', [row['id'] for row in backend.profiles])

    def test_pointer_activation_and_keyboard_repeat_keep_models_buttons_and_engine(self):
        self.seed()
        display, document, _ = self.package(package_id='author-ui')
        self.list_packages()
        self.assertFalse(self.errors)
        self.assertEqual(self.active_resolver.snapshot().displays, ())
        key = self.key(display)
        model = self.service.displayModelForPackage(key)
        self.assertIs(self.service.displayModelForPackage(key), model)
        self.assertEqual(model.rows[0]['modelData']['label'], '启用 S1 困难作者显示')
        mutations = model.mutations
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name('ClientImportedResources.qml'))))
        self.assertEqual(view.errors(), [])
        view.resize(650, 470)
        view.show()
        name = f'activateDisplayButton-{key}-{display.component_ref.component_id}'
        button = None
        deadline = time.monotonic() + 5
        while button is None and time.monotonic() < deadline:
            self.app.processEvents()
            button = self.find(view.rootObject(), name)
            time.sleep(.005)
        self.assertIsNotNone(button)
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, point)
        self.assertTrue(self.service.pendingWrites)
        self.assertEqual(self.session.acquireResourceLease(), '')
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.active_resolver.snapshot().displays, (display.component_ref,))
        self.assertIn('作者显示组件已启用', self.service.message)
        self.assertIn('尚未复核', self.service.message)
        engine = self.factories.get_engine('difficult', season='s1')
        layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
        self.assertEqual(layer['displayKind'], 'author-route')
        np.testing.assert_array_equal(layer['pixels'][1, 2], (3, 2, 1, 0))
        np.testing.assert_allclose(layer['matrixDisplayToReference'], document['layers'][0]['matrixDisplayToReference'])
        self.assertTrue(engine.qt_rank_ready)
        self.assertFalse(engine.qt_live_ready)
        self.assertFalse(engine.memory_store.root.exists())
        self.assertIs(self.service.displayModelForPackage(key), model)
        self.assertEqual(model.mutations, mutations)
        self.assertIs(self.find(view.rootObject(), name), button)
        self.assertIs(self.service.packageModel, self.service.model)
        before = (self.resources/'catalog.json').read_bytes()
        button.forceActiveFocus()
        QTest.keyClick(view, Qt.Key_Space)
        self.assertTrue(self.service.busy)
        self.wait()
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertEqual(self.changes, [True])
        self.assertIs(self.factories.get_engine('difficult', season='s1'), engine)
        self.assertIs(self.find(view.rootObject(), name), button)
        self.assertEqual(model.mutations, mutations)
        self.assertIn('已经启用', self.service.message)
        evidence = os.environ.get('XINGTU_TEST_AUTHOR_UI_EVIDENCE')
        if evidence:
            directory = Path(evidence).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            for width, height in ((650, 470), (1000, 720)):
                view.resize(width, height)
                QTest.qWait(150)
                image = view.grabWindow()
                self.assertFalse(image.isNull())
                self.assertTrue(image.save(str(directory/f'author-selected-{width}x{height}-{time.time_ns()}.png')))

    def test_missing_base_unknown_component_and_damaged_old_metadata_keep_selection(self):
        self.seed(active_base=False)
        first, _, _ = self.package(package_id='author-ui')
        self.list_packages()
        key = self.key(first)
        before = (self.resources/'catalog.json').read_bytes()
        self.assertFalse(self.service.activateDisplayComponent(key, 'unknown'))
        self.assertTrue(self.service.activateDisplayComponent(key, first.component_ref.component_id))
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-display-base-context-not-active')
        self.assertIn('导入并启用对应模式的基础结构包', self.service.message)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertFalse(self.changes)
        self.seed(displays=[first.component_ref])
        second, _, _ = self.package(package_id='author-ui', resource_version='v2')
        self.list_packages()
        first.path('display.json').write_bytes(b'{}')
        before = (self.resources/'catalog.json').read_bytes()
        self.assertTrue(self.service.activateDisplayComponent(self.key(second), second.component_ref.component_id))
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-previous-display-metadata-unavailable')
        self.assertIn('原作者版本的声明', self.service.message)
        self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
        self.assertFalse(self.changes)
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        self.session.releaseResourceLease(lease)

    def test_cancel_and_shutdown_drain_removal_without_late_notification(self):
        import imported_resource_removal as removal
        display, _, _ = self.package(package_id='author-cancel-removal')
        self.seed(displays=(display.component_ref,))
        self.list_packages()
        before = (self.resources / 'catalog.json').read_bytes()
        for closing in (False, True):
            with self.subTest(closing=closing):
                self.assertTrue(self.service.requestRemoval(self.key(display)))
                self.wait()
                entered, release = threading.Event(), threading.Event()
                real = removal.remove_saved_version
                def delayed(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('test scheduling boundary was not released')
                    return real(*args, **kwargs)
                errors = len(self.errors)
                with patch('imported_resource_removal.remove_saved_version', side_effect=delayed):
                    self.assertTrue(self.service.confirmRemoval(self.service.removalPlanId))
                    try:
                        self.assertTrue(entered.wait(3))
                        self.assertTrue(self.service.pendingWrites)
                        self.service.shutdown() if closing else self.service.cancel()
                    finally:
                        release.set()
                    self.wait()
                self.assertEqual((self.resources / 'catalog.json').read_bytes(), before)
                self.assertTrue(display.path('images/route.png').is_file())
                self.assertFalse(self.changes)
                self.assertFalse(self.service.pendingWrites)
                self.assertEqual(len(self.errors), errors if closing else errors + 1)
                lease = self.session.acquireResourceLease()
                self.assertTrue(lease)
                self.session.releaseResourceLease(lease)

    def test_cancel_and_shutdown_drain_real_author_activation_without_late_notification(self):
        self.seed()
        display, _, _ = self.package(package_id='author-ui')
        self.list_packages()
        before = (self.resources/'catalog.json').read_bytes()
        for closing in (False, True):
            with self.subTest(closing=closing):
                entered, release = threading.Event(), threading.Event()
                real = activation.activate_display
                def delayed(*args, **kwargs):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError('test adapter was not released')
                    return real(*args, **kwargs)
                errors = len(self.errors)
                with patch('client_imported_activation.activate_display', side_effect=delayed):
                    self.assertTrue(self.service.activateDisplayComponent(self.key(display), display.component_ref.component_id))
                    try:
                        self.assertTrue(entered.wait(3))
                        self.service.shutdown() if closing else self.service.cancel()
                    finally:
                        release.set()
                    self.wait()
                self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
                self.assertFalse(self.changes)
                self.assertFalse(self.service.pendingWrites)
                self.assertEqual(len(self.errors), errors if closing else errors + 1)
                if not closing:
                    self.assertTrue(self.errors[-1]['errorCode'].endswith('-cancelled'))
                lease = self.session.acquireResourceLease()
                self.assertTrue(lease)
                self.session.releaseResourceLease(lease)

    def test_restored_author_bytes_retry_a_failed_preparation_without_rewriting_catalog(self):
        display, _, members = self.package(package_id='author-ui')
        self.seed(displays=[display.component_ref])
        manifest = display.root.parent.parent/'manifest.json'
        cases = ((display.path('images/route.png'), members['images/route.png']),
                 (manifest, manifest.read_bytes()))
        for damaged, original in cases:
            with self.subTest(file=damaged.name):
                self.factories.invalidate()  # Model a new startup preparation.
                self.changes.clear()
                damaged.write_bytes(b'broken before startup preparation')
                old_engine = self.factories.get_engine('difficult', season='s1')
                old_layer = prepare_display_layer(old_engine, self.source.ids[0], '-4', 'author-fixture')
                self.assertEqual(old_layer['displayKind'], 'shared-structure')
                self.assertTrue(old_engine.qt_resource_inventory['optionalResourceGaps'])
                damaged.write_bytes(original)
                self.list_packages()
                before = (self.resources/'catalog.json').read_bytes()
                self.assertTrue(self.service.activateDisplayComponent(self.key(display), display.component_ref.component_id))
                self.wait()
                self.assertFalse(self.errors, self.errors)
                engine = self.factories.get_engine('difficult', season='s1')
                layer = prepare_display_layer(engine, self.source.ids[0], '-4', 'author-fixture')
                self.assertEqual(layer['displayKind'], 'author-route')
                np.testing.assert_array_equal(layer['pixels'][1, 2], (3, 2, 1, 0))
                self.assertIsNot(engine, old_engine)
                self.assertEqual((self.resources/'catalog.json').read_bytes(), before)
                self.assertEqual(self.changes, [True])
