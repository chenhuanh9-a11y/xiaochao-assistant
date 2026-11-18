"""S0D original-layout resource-page pointer flow through import and activation.

The test delays the real activation helper briefly to observe the service's
worker and Session idle lease. The helper itself, catalog transaction and
default engine factory are never replaced.
"""
import os
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture
from test_client_live_runtime import FixtureCapture


class ImportedS0DifficultActivationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        source_path = os.environ.get('XINGTU_TEST_S0D_ACTIVATION_UI_SOURCE')
        if source_path:
            cls.source = SimpleNamespace(output=Path(source_path).resolve())
        else:
            temporary = tempfile.TemporaryDirectory(prefix='s0d-activation-ui-source-')
            cls.addClassCleanup(temporary.cleanup)
            cls.source = OriginalS0DifficultSourceFixture(Path(temporary.name).resolve())
            cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s0d-activation-ui-active-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name).resolve(), False)
        self.resolver = ResourceResolver(
            self.paths.imported_resource_root,
            supported_capabilities=SUPPORTED_CAPABILITIES,
        )
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.capture = FixtureCapture()
        self.runtime = LiveRuntime(
            engine_factories=self.factories,
            capture_provider=self.capture,
            evidence_kind='synthetic',
        )
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

    @staticmethod
    def find(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = ImportedS0DifficultActivationUiTests.find(child, name)
            if found is not None:
                return found
        return None

    def pointer_click(self, view, item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)

    def test_import_finishes_with_base_and_controls_active_without_second_action(self):
        self.service.importArchive(QUrl.fromLocalFile(str(self.source.output)).toString())
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.availableContexts, ['s0:difficult'])
        current = self.resolver.snapshot().resolve_context('s0', 'difficult')
        self.assertFalse(current.gaps)
        self.assertEqual(current.base.component_ref.package_id, current.controls.component_ref.package_id)
        self.assertEqual(current.base.component_ref.resource_version, current.controls.component_ref.resource_version)
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        engine = self.factories.get_engine('difficult', season='s0')
        self.assertTrue(engine.qt_native_base.rank_ready)
        self.assertIn('已启用', self.service.message)

    def test_resource_page_pointer_saves_activates_original_engine_and_keeps_ui_stable(self):
        import_started = time.perf_counter()
        self.service.importArchive(QUrl.fromLocalFile(str(self.source.output)).toString())
        self.wait()
        import_ms = (time.perf_counter() - import_started) * 1000
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertTrue((self.resolver.root / 'catalog.json').exists(),
                        'import must activate base and controls without another action')

        row = self.service.packageRows[0]
        key = row['key']
        self.assertTrue(row['canActivate'])
        self.assertEqual(row['activatableContexts'], ['s0:difficult'])
        option = next(item for item in self.service.activationOptions
                      if item['context'] == 's0:difficult')
        self.assertEqual(option['label'], '启用 S0 困难')
        self.assertEqual(option['objectSuffix'], '-s0-difficult')
        self.assertEqual(option['notice'], '可扫描结构候选，地图需手动确认。')

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

        button_name = f'activatePackageButton-{key}-s0-difficult'
        notice_name = f'activationNotice-{key}-s0-difficult'
        button = None
        deadline = time.monotonic() + 5
        while button is None and time.monotonic() < deadline:
            self.app.processEvents()
            button = self.find(view.rootObject(), button_name)
            time.sleep(.005)
        self.assertIsNotNone(button, 'the real QML delegate did not expose S0D activation')
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.property('text'), '重新校验此版本')
        notice = self.find(view.rootObject(), notice_name)
        self.assertIsNotNone(notice)
        self.assertTrue(notice.isVisible())
        self.assertEqual(notice.property('text'), option['notice'])
        # The package is eligible for S0D only; the generic repeater must keep
        # unrelated activation actions hidden for this package row.
        unrelated = self.find(view.rootObject(), f'activatePackageButton-{key}')
        self.assertIsNotNone(unrelated)
        self.assertFalse(unrelated.isVisible())
        s1_button = self.find(view.rootObject(), f'activatePackageButton-{key}-s1-difficult')
        self.assertIsNotNone(s1_button)
        self.assertFalse(s1_button.isVisible())

        evidence = os.environ.get('XINGTU_TEST_S0D_ACTIVATION_UI_EVIDENCE')
        if evidence:
            directory = Path(evidence).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            self._screenshot_paths = []
            for width, height in ((650, 470), (1000, 720)):
                view.resize(width, height)
                QTest.qWait(120)
                pixels = view.grabWindow()
                self.assertFalse(pixels.isNull())
                image = directory / f's0d-activation-{width}x{height}-{os.getpid()}-{time.time_ns()}.png'
                self.assertTrue(pixels.save(str(image)))
                self._screenshot_paths.append(str(image))

        model = self.service.packageModel
        import client_imported_activation as activation
        real_activate_context = activation.activate_context
        entered, release = threading.Event(), threading.Event()

        def delayed_real_activation(*args, **kwargs):
            entered.set()
            if not release.wait(20):
                raise AssertionError('test did not release the real activation helper')
            return real_activate_context(*args, **kwargs)

        with patch.object(activation, 'activate_context', side_effect=delayed_real_activation):
            try:
                activation_started = time.perf_counter()
                self.pointer_click(view, button)
                deadline = time.monotonic() + 5
                while not entered.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()  # Deliver the read-only plan before activation.
                    time.sleep(.005)
                self.assertTrue(entered.is_set(), 'pointer click did not reach the activation worker')
                self.assertTrue(self.service.busy)
                self.assertTrue(self.service.pendingWrites)
                self.assertTrue(self.service._lease)
                self.assertIn(self.service._lease, self.session._leases)
                self.assertEqual(self.session.acquireResourceLease(), '',
                                 'another idle lease was granted during activation')
                self.app.processEvents()
                self.assertIs(self.find(view.rootObject(), button_name), button,
                              'the QML action should remain the same item while busy')
            finally:
                release.set()
        self.wait()
        activation_ms = (time.perf_counter() - activation_started) * 1000

        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], ['s0:difficult'])
        self.assertIs(self.service.packageModel, model)
        self.assertIs(self.find(view.rootObject(), button_name), button,
                      'the package action should keep its QML item instance after selection')
        self.assertIn('S0 困难', self.service.message)
        self.assertIn('可扫描结构候选', self.service.message)
        self.assertIn('地图需手动确认', self.service.message)

        catalog_path = self.resolver.root / 'catalog.json'
        selected_catalog = catalog_path.read_bytes()
        context = self.resolver.snapshot().contexts['s0:difficult']
        self.assertIsNotNone(context['recognitionBase'])
        self.assertIsNotNone(context['controls'])

        engine = self.factories.get_engine('difficult')
        report = self.factories.capabilities()['difficult']
        self.assertEqual(len(engine.pack.by_id), 30)
        self.assertTrue(report['dataReady'], report)
        self.assertTrue(report['rankReady'], report)
        self.assertTrue(report['available'], report)
        self.assertTrue(report['liveReady'], report)
        self.assertTrue(engine.qt_rank_ready)
        self.assertTrue(engine.qt_live_ready)
        map_id = next(iter(engine.pack.by_id))
        floor = str(engine.pack.floors[0])
        self.assertTrue(engine.qt_native_context.has_reference_binding(map_id, floor))
        self.assertIsNotNone(engine.qt_shared_provider('shared-structure', map_id, floor))
        self.assertFalse(engine.memory_store.root.exists())

        # The optional real-package receipt records the decoded bitmap shapes
        # without replacing source dimensions with fixture-sized assumptions.
        bitmap_dimensions = []
        if os.environ.get('XINGTU_TEST_S0D_ACTIVATION_UI_RECEIPT'):
            from client_route_preferences import prepare_display_layer
            for map_id in sorted(engine.pack.by_id):
                for floor in engine.pack.floors:
                    display = prepare_display_layer(engine, map_id, str(floor), 'shared-structure')
                    bitmap_dimensions.append(dict(mapId=map_id, floor=str(floor),
                                                  shape=list(display['pixels'].shape)))
            self.assertEqual(len(bitmap_dimensions), 60)

        self.assertTrue(button.isEnabled())
        self.pointer_click(view, button)
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual(catalog_path.read_bytes(), selected_catalog)
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertIs(self.find(view.rootObject(), button_name), button,
                      'repeat activation should keep the same action item')

        receipt_path = os.environ.get('XINGTU_TEST_S0D_ACTIVATION_UI_RECEIPT')
        if receipt_path:
            archive = Path(self.source.output)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            receipt = dict(
                sourceZip=str(archive), sourceBytes=archive.stat().st_size,
                sourceSha256=digest,
                importToActivationCompleteMs=round(import_ms, 3), pointerToRevalidationCompleteMs=round(activation_ms, 3),
                resourcesChangedAfterFirstActivation=1, resourcesChangedAfterRepeat=len(self.changes),
                errors=len(self.errors), packageKey=key,
                selectedContexts=self.service.packageRows[0]['selectedContexts'],
                catalogGeneration=self.resolver.snapshot().generation,
                engineMapCount=len(engine.pack.by_id), bitmapDimensions=bitmap_dimensions,
                dataReady=report['dataReady'], rankReady=report['rankReady'],
                available=report['available'], liveReady=report['liveReady'],
                candidateReady=engine.qt_native_context.structure_candidate_capability['candidateReady'],
                memoryStoreExists=engine.memory_store.root.exists(),
                captureCount=self.capture.count, captureEnableCount=self.capture.enable_count,
                screenshots=getattr(self, '_screenshot_paths', []),
            )
            output = Path(receipt_path).resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
