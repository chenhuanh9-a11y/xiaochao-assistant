"""Resource-page author actions through the real lease/catalog/image path.

Sources and author pixels are synthetic. Capture is a recording-only fixture;
these tests neither open a game nor claim file-picker or physical-pixel proof.
"""
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickView
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
        super().setUp()
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
