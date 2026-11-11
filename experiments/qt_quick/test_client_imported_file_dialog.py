"""Real QML file-picker import through activation and the idle-session lease.

The offscreen run opens Qt's actual non-native FileDialog, selects a visible base-package
row with a pointer, and clicks its Open button. It does not emulate native OS
picker rendering or call the resource service's import slot directly.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')

from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtQuick import QQuickItem, QQuickView, QQuickWindow
from PySide6.QtTest import QTest

from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_resources import ImportedResourceService
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from imported_resource_catalog import ResourceResolver
from test_client_live_runtime import FixtureCapture
import s0_nightmare_group_source_fixture as source_fixture


class ImportedFileDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        source_path = os.environ.get('XINGTU_TEST_FILE_DIALOG_BASE_SOURCE')
        if source_path:
            cls.source = SimpleNamespace(output=Path(source_path).resolve())
            if not cls.source.output.is_file():
                raise FileNotFoundError(cls.source.output)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='s0n-file-dialog-source-')
            cls.addClassCleanup(temporary.cleanup)
            cls.source = source_fixture.S0NightmareGroupSourceFixture(
                Path(temporary.name).resolve())
            cls.source.export()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='s0n-file-dialog-import-')
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
        self.service = ImportedResourceService(self.resolver, paths=self.paths,
                                               engine_factories=self.factories)
        self.service.setLeaseProvider(self.session)
        self.errors, self.changes, self.completions = [], [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.service.importCompleted.connect(lambda *args: self.completions.append(args))
        self.addCleanup(self.close)

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()
        self.app.processEvents()

    def wait_for(self, predicate, message, timeout=90):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), message)

    def wait_idle(self):
        self.wait_for(lambda: not self.service.busy, self.service.message)
        self.app.processEvents()
        self.assertFalse(self.service.busy, self.service.message)

    @staticmethod
    def find_item(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = ImportedFileDialogTests.find_item(child, name)
            if found is not None:
                return found
        return None

    def pointer_click(self, view, item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        target = item.window() if isinstance(item, QQuickItem) else view
        self.assertIsNotNone(target, 'the selected QML item has no owning window')
        QTest.mouseClick(target, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, point)

    @staticmethod
    def visible_quick_items():
        items = []
        for window in QGuiApplication.allWindows():
            if not isinstance(window, QQuickWindow) or not window.isVisible():
                continue
            stack = [window.contentItem()]
            while stack:
                item = stack.pop()
                if item is None:
                    continue
                try:
                    if not item.isVisible():
                        continue
                    children = item.childItems()
                except RuntimeError:
                    continue
                items.append(item)
                stack.extend(children)
        return items

    def test_import_button_opens_real_file_dialog_and_auto_activates_s0_nightmare(self):
        if os.name == 'nt':
            for name in ('msyh.ttc', 'msyhbd.ttc'):
                font = Path(os.environ['WINDIR']) / 'Fonts' / name
                if font.is_file():
                    QFontDatabase.addApplicationFont(str(font))

        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty('resourceService', self.service)
        qml_path = Path(__file__).with_name('ClientImportedResources.qml')
        view.setSource(QUrl.fromLocalFile(str(qml_path)))
        self.assertEqual(view.errors(), [])
        view.resize(760, 520)
        view.show()
        self.app.processEvents()

        root = view.rootObject()
        button = self.find_item(root, 'importPackagesButton')
        self.assertIsNotNone(button, 'the resource page import button was not created')
        self.assertTrue(button.isVisible())
        self.assertTrue(button.isEnabled())
        dialog = root.findChild(QObject, 'resourceArchiveDialog')
        self.assertIsNotNone(dialog, 'the page did not expose its actual QML FileDialog')

        # Resolve the enum from this actual QML FileDialog meta-object rather
        # than assuming Qt's numeric flag value.
        enum_index = dialog.metaObject().indexOfEnumerator('FileDialogOptions')
        self.assertGreaterEqual(enum_index, 0, 'FileDialogOptions enum is unavailable')
        file_dialog_options = dialog.metaObject().enumerator(enum_index)
        dont_use_native, found = file_dialog_options.keyToValue('DontUseNativeDialog')
        self.assertTrue(found, 'DontUseNativeDialog is missing from the QML enum')
        self.assertTrue(dialog.setProperty('options', dont_use_native),
                        'could not configure this FileDialog for offscreen presentation')

        self.pointer_click(view, button)
        self.wait_for(lambda: dialog.property('visible'),
                      'the real QML FileDialog did not become visible after the pointer click',
                      timeout=8)
        self.assertTrue(dialog.property('visible'))

        source = self.source.output.resolve()
        self.assertTrue(source.is_file(), source)
        source_folder = QUrl.fromLocalFile(str(source.parent))
        self.assertTrue(dialog.setProperty('currentFolder', source_folder),
                        'the actual FileDialog currentFolder property is not writable')
        self.wait_for(lambda: dialog.property('currentFolder') == source_folder,
                      'the visible FileDialog did not navigate to the source folder', timeout=5)
        def matching_file_row():
            for item in self.visible_quick_items():
                try:
                    file_name = item.property('fileName')
                    text = item.property('text')
                    if file_name == source.name or text == source.name:
                        return item
                except RuntimeError:
                    continue
            return None

        deadline = time.monotonic() + 8
        while matching_file_row() is None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        file_row = matching_file_row()
        visible_tree = []
        for item in self.visible_quick_items():
            try:
                visible_tree.append((item.objectName(), item.metaObject().className(),
                                     item.property('text'), item.property('fileName'),
                                     item.width(), item.height()))
            except RuntimeError:
                continue
        self.assertIsNotNone(file_row, 'the visible FileDialog did not load the source base-package row: '
                             + repr(visible_tree))
        self.assertIsInstance(file_row, QQuickItem)
        self.assertTrue(file_row.isVisible())
        file_url = file_row.property('fileUrl')
        self.assertTrue(file_url.isValid() and file_url.isLocalFile()
                        and Path(file_url.toLocalFile()).resolve() == source,
                        f'visible file row URL mismatch: {file_url!r}')
        self.pointer_click(view, file_row)
        def selected_source():
            selected = dialog.property('selectedFile')
            return (selected.isValid() and selected.isLocalFile()
                    and Path(selected.toLocalFile()).resolve() == source)

        self.wait_for(selected_source,
                      'pointer selection did not update the FileDialog selected URL', timeout=5)
        self.assertTrue(selected_source())
        selected_url = dialog.property('selectedFile').toString()

        open_button = next((item for item in self.visible_quick_items()
                            if item.isVisible() and item.property('text') in ('Open', '打开')),
                           None)
        self.assertIsNotNone(open_button, 'the actual FileDialog Open button was not found')
        self.assertTrue(open_button.isEnabled())

        import client_imported_activation as activation
        real_activate_contexts = activation.activate_contexts
        activation_entered, release_activation = threading.Event(), threading.Event()

        def delayed_real_activation(*args, **kwargs):
            activation_entered.set()
            if not release_activation.wait(30):
                raise AssertionError('test did not release the real base activation helper')
            return real_activate_contexts(*args, **kwargs)

        model = self.service.packageModel
        try:
            with patch.object(activation, 'activate_contexts',
                              side_effect=delayed_real_activation):
                # Install the observation hook before the real Open action can
                # start a fast import and reach activation on the worker.
                self.pointer_click(view, open_button)
                self.wait_for(lambda: not dialog.property('visible'),
                              'clicking the actual FileDialog Open button did not close it', timeout=5)
                self.wait_for(lambda: self.service.busy or bool(self.errors),
                              'the accepted QML FileDialog did not start the import service', timeout=5)
                self.assertTrue(self.service.busy, dict(
                    errors=self.errors, message=self.service.message, selectedUrl=selected_url))
                self.wait_for(lambda: activation_entered.is_set() or bool(self.errors)
                              or not self.service.busy,
                              'FileDialog acceptance neither reached automatic activation nor reported an import error')
                self.assertTrue(activation_entered.is_set(), dict(
                    errors=self.errors, message=self.service.message,
                    kind=self.service._kind, completions=self.completions,
                    busy=self.service.busy,
                ))
                self.assertTrue(self.service.busy)
                self.assertEqual(self.service._kind, 'activate')
                self.assertTrue(self.service.pendingWrites)
                self.assertTrue(self.service._automatic_import)
                self.assertTrue(self.service._lease)
                self.assertIn(self.service._lease, self.session._leases)
                self.assertEqual(self.session.acquireResourceLease(), '',
                                 'a concurrent idle-session lease was granted during import activation')
                self.assertEqual(len(self.session._leases), 1)
                self.assertIsNone(self.service._activation_plan,
                                  'an empty automatic base plan should commit without a second UI action')
        finally:
            release_activation.set()
        self.wait_idle()

        self.assertFalse(self.errors, self.errors)
        self.assertEqual(self.changes, [True])
        self.assertEqual([item[0] for item in self.completions], ['基础资源已启用'], self.completions)
        self.assertFalse(self.service._lease)
        self.assertFalse(self.session._leases)
        self.assertEqual(self.service.availableContexts, ['s0:nightmare'])
        self.assertTrue(self.service.contextAvailable('s0', 'nightmare'))
        self.assertIs(self.service.packageModel, model)
        row = self.service.packageRows[0]
        self.assertEqual(row['status'], 'selected')
        self.assertEqual(row['selectedContexts'], ['s0:nightmare'])
        self.assertEqual(row['activatableContexts'], ['s0:nightmare'])
        self.assertIn('S0 噩梦', self.service.message)
        self.assertTrue((self.resolver.root / 'catalog.json').is_file())
        self.assertTrue((self.resolver.root / 'packages' / row['packageId'] /
                         row['resourceVersion']).is_dir())
        staging = self.resolver.root / 'staging'
        self.assertTrue(staging.is_dir())
        self.assertEqual(list(staging.iterdir()), [], 'completed import left staging objects behind')

        context = self.resolver.snapshot().resolve_context('s0', 'nightmare')
        self.assertFalse(context.gaps, context.gaps)
        self.assertIsNotNone(context.base)
        self.assertIsNotNone(context.controls)
        self.assertEqual(context.base.component_ref.package_id,
                         context.controls.component_ref.package_id)
        self.assertEqual(context.base.component_ref.resource_version,
                         context.controls.component_ref.resource_version)
        self.assertEqual(context.base.component_ref.package_id, row['packageId'])

        engine = self.factories.get_engine('nightmare', season='s0')
        report = self.factories.capabilities('s0')['nightmare']
        self.assertEqual(len(engine.pack.by_id), 26)
        self.assertEqual(len(engine.pack.floors), 3)
        self.assertTrue(report['dataReady'], report)
        self.assertTrue(engine.qt_native_base)
        self.assertFalse(engine.memory_store.root.exists(),
                         'resource import or default engine preparation wrote learning data')
        self.assertEqual(self.capture.count, 0)
        self.assertEqual(self.capture.enable_count, 0)
        self.assertFalse(self.service.activationPlanOpen)
        self.assertFalse(self.service.busy)

        receipt_path = os.environ.get('XINGTU_TEST_FILE_DIALOG_RECEIPT')
        if receipt_path:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            receipt = dict(
                sourceZip=str(source), sourceBytes=source.stat().st_size,
                sourceSha256=digest, selectedUrl=selected_url,
                context='s0:nightmare', packageId=row['packageId'],
                resourceVersion=row['resourceVersion'], catalogGeneration=self.resolver.snapshot().generation,
                mapCount=len(engine.pack.by_id), floorCount=len(engine.pack.floors),
                dataReady=report['dataReady'], memoryStoreExists=engine.memory_store.root.exists(),
                resourcesChanged=len(self.changes), captureCount=self.capture.count,
                activationLeaseReleased=not bool(self.session._leases),
            )
            output = Path(receipt_path).resolve()
            ignored_work = (Path(__file__).resolve().parents[2] / 'work').resolve()
            if not output.is_relative_to(ignored_work):
                raise ValueError('XINGTU_TEST_FILE_DIALOG_RECEIPT must stay inside ignored work/')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n',
                              encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
