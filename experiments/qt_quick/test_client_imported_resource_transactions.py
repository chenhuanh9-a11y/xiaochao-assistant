"""Actual archive -> catalog transaction -> visible QML gallery regression.

Only native input/capture and networking are isolated. The production resource
service, transaction journal, providers, coordinator and QML remain in use.
Synthetic original-layout canvases prove resource flow, never game accuracy.
"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtQuick import QQuickItem

import client
import imported_resource_activation as activation
from client_paths import WORK_ROOT
from original_qt_resource_fixtures import OriginalS0NightmareSourceFixture
from test_imported_resource_activation import CHILD


class CatalogReadWaitTests(unittest.TestCase):
    def test_healthy_currentness_checks_acquire_no_resource_lock_or_create_store(self):
        from imported_resource_catalog import CatalogSnapshot, SCHEMA
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / 'absent-resources'
            installed = Path(folder) / 'installed-resources'
            installed.mkdir()
            (installed / 'catalog.json').write_text(json.dumps(dict(schema=SCHEMA,
                generation=str(uuid.uuid4()), contexts={}, displays=[])), encoding='utf8')
            with patch('resource_transaction.resource_read_lock',
                       side_effect=AssertionError('healthy read acquired lock')) as lock:
                for root in (missing, installed):
                    snapshot = CatalogSnapshot(root)
                    snapshot.assert_current()
                    snapshot.resolve_context('s0', 'nightmare')
                lock.assert_not_called()
            self.assertFalse(missing.exists())


class ImportedResourceTransactionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        from client_route_plaza import RoutePlazaService
        from client_input import NativeShortcutBackend
        from test_client_input import Native
        from test_client_live_runtime import FixtureCapture
        WORK_ROOT.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix='qt-catalog-transaction-', dir=WORK_ROOT)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
        self.archive = Path(os.environ.get('XINGTU_TEST_IMPORT_ARCHIVE', ''))
        if not self.archive.is_file():
            source = OriginalS0NightmareSourceFixture(self.root / 'source', ids=['nightmare-01'])
            source.export()
            self.archive = source.output
        capture = FixtureCapture()
        capture.is_current = lambda *args, **kwargs: True
        stack = self.enterContext(ExitStack())
        stack.enter_context(patch.object(client, 'owned_sandbox', side_effect=lambda path: Path(path)))
        stack.enter_context(patch('client_input.NativeShortcutBackend',
                                  side_effect=lambda: NativeShortcutBackend(Native())))
        stack.enter_context(patch('client_capture.ClientCaptureProvider', return_value=capture))
        stack.enter_context(patch('client_coordinator.another_assistant_running', return_value=False))
        stack.enter_context(patch('client_route_plaza.RoutePlazaService',
            side_effect=lambda *args, **kwargs: RoutePlazaService(*args, **kwargs, listing=lambda query: [])))
        self.client = client.QtClient(self.app, self.root, probe_output=self.root / 'unused-probe.json')
        self.addCleanup(self.close)
        self.errors = []
        self.client.resources.errorReported.connect(self.errors.append)
        self.client.coordinator.navigate('gallery')
        self.until(lambda: self.client.main.findChild(QObject, 'galleryAuthorGrid') is not None
                   and not self.client.backend.busy and not self.client.resources.busy
                   and not self.client.session.busy)

    def close(self):
        self.client.shutdown()
        # Qt owns image-provider workers separately from service QThreadPools.
        # After shutdown has cleared the publication, let already admitted
        # pixel reads release their handles before removing this test's tree.
        deadline = time.monotonic() + 10
        while self.client.gallery_images._inflight and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(self.client.gallery_images._inflight, 0)

    def until(self, predicate, timeout=None):
        if timeout is None:
            timeout = float(os.environ.get('XINGTU_TEST_IMPORT_TIMEOUT', '45'))
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate(), dict(message=self.client.backend.message,
            resourceMessage=self.client.resources.message, errors=self.errors))

    def settle(self):
        self.until(lambda: not self.client.resources.busy and not self.client.backend.busy
                   and not self.client.route_choices.busy and not self.client.session.busy)

    def import_archive(self):
        self.client.resources.importArchive(QUrl.fromLocalFile(str(self.archive)).toString())

    def assert_visible_gallery(self):
        self.assertFalse(self.client.backend.hasError, self.client.backend.message)
        self.assertGreater(self.client.backend.mapCount, 0)
        listing = self.client.main.findChild(QObject, 'galleryAuthorGrid')
        self.assertGreater(listing.property('count'), 1)  # structure card plus import card
        def ready_images():
            images = [item for item in self.client.main.findChildren(QQuickItem)
                      if isinstance(item.property('source'), QUrl)
                      and item.property('source').toString().startswith('image://importedgallery/')]
            states = []
            for item in images:
                expression = QQmlExpression(QQmlEngine.contextForObject(item), item, 'status === 1')
                value = expression.evaluate()
                self.assertFalse(expression.hasError(), expression.error().toString())
                states.append(value[0] if isinstance(value, tuple) else value)
            return bool(states) and all(states)
        self.until(ready_images, timeout=15)
        evidence = os.environ.get('XINGTU_TEST_RESOURCE_UI_EVIDENCE')
        if evidence:
            target = Path(evidence)
            target.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.client.main.grabWindow().save(str(target / (self._testMethodName + '.png'))))

    def hold_publication(self, *, failure=None):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        publish = activation.activate_catalog_locked
        def paused(*args, **kwargs):
            original_stage = kwargs.pop('on_stage', None)
            def stage(name):
                if original_stage:
                    original_stage(name)
                if name == 'pending':
                    entered.set()
                    if not release.wait(15):
                        raise AssertionError('test did not release catalog writer')
                    if failure is not None:
                        raise failure
            return publish(*args, **kwargs, on_stage=stage)
        return entered, release, patch.object(activation, 'activate_catalog_locked', side_effect=paused)

    def test_visible_gallery_waits_for_active_import_without_recovery_error(self):
        import client_imported_browse as browse
        started, timings = time.monotonic(), []
        def stage(name):
            timings.append(dict(stage=name, elapsedSeconds=round(time.monotonic() - started, 3)))
        entered, release, pause = self.hold_publication()
        load = browse.load_imported_browse_context
        barrier, metadata_lock, metadata_threads = threading.Barrier(2), threading.Lock(), []
        def overlapping_metadata(*args, **kwargs):
            # Gallery metadata and home previews must both enter their public
            # loader before waiting for the real catalog writer. The gallery
            # does not need an eager native-engine build to display its cards.
            if not entered.is_set():
                return load(*args, **kwargs)
            with metadata_lock:
                metadata_threads.append(threading.get_ident())
                first_two = len(metadata_threads) <= 2
            if first_two:
                barrier.wait(timeout=15)
            return load(*args, **kwargs)
        with pause, patch.object(browse, 'load_imported_browse_context', side_effect=overlapping_metadata):
            stage('import-submitted')
            self.import_archive()
            self.until(entered.is_set)
            stage('catalog-pending')
            self.client.backend.refresh()
            self.client.info.setHomeContext('nightmare', 'shared-structure', force=True)
            # Run the visible consumer while the real journal is pending. It
            # must wait for its writer, rather than cache an interrupted error.
            deadline = time.monotonic() + .25
            while time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            try:
                self.assertTrue(self.client.backend.busy, self.client.backend.message)
                self.assertFalse(self.client.backend.hasError, self.client.backend.message)
                self.assertIsNotNone(self.client.info._home_preview_job)
                footer = self.client.main.findChild(QObject, 'galleryOperationMessage')
                self.assertNotIn('recovery-required', footer.property('text'))
            finally:
                stage('writer-released')
                release.set()
            self.settle()
            stage('metadata-and-session-settled')
        self.assertEqual(self.errors, [])
        self.assertGreaterEqual(len(set(metadata_threads)), 2)
        self.assert_visible_gallery()
        stage('qml-images-ready')
        self.until(lambda: bool(self.client.info.homePreviews))
        self.assertTrue(all(row['imageUrl'].startswith('data:image/png;base64,')
                            for row in self.client.info.homePreviews))
        stage('home-previews-ready')
        evidence = os.environ.get('XINGTU_TEST_RESOURCE_UI_EVIDENCE')
        if evidence:
            result = dict(archive=str(self.archive), archiveBytes=self.archive.stat().st_size,
                          mapCount=self.client.backend.mapCount, parallelMetadataThreads=len(set(metadata_threads)),
                          stages=timings)
            (Path(evidence) / 'visible-import-timings.json').write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
            print(json.dumps(result, ensure_ascii=False))

    def test_cancel_during_pending_import_rolls_back_and_read_finishes_cleanly(self):
        entered, release, pause = self.hold_publication()
        with pause:
            self.import_archive()
            self.until(entered.is_set)
            self.client.backend.refresh()
            self.client.resources.cancel()
            release.set()
            self.settle()
        self.assertFalse(self.client.backend.hasError, self.client.backend.message)
        self.assertFalse(self.client.resources.recoveryRequired)
        self.assertEqual(self.client.backend.mapCount, 0)
        self.assertTrue(self.archive.is_file())
        self.assertFalse((self.client.resource_resolver.root / activation.PENDING_CATALOG).exists())
        self.assertTrue(self.client.resources.packageRows, 'saved version survives activation cancellation')

    def test_failed_catalog_write_rolls_back_without_caching_recovery_error(self):
        entered, release, pause = self.hold_publication(failure=OSError('isolated write interruption'))
        with pause:
            self.import_archive()
            self.until(entered.is_set)
            self.client.backend.refresh()
            release.set()
            self.settle()
        self.assertFalse(self.client.backend.hasError, self.client.backend.message)
        self.assertFalse(self.client.resources.recoveryRequired)
        self.assertEqual(self.client.backend.mapCount, 0)
        self.assertTrue(self.errors, 'the actual failed activation must still be reported')
        self.assertTrue(self.client.resources.packageRows)
        self.assertTrue(self.archive.is_file())
        self.assertFalse((self.client.resource_resolver.root / activation.PENDING_CATALOG).exists())

    def test_late_old_metadata_cannot_replace_the_imported_qml_cards(self):
        from client_imported_browse import load_imported_browse_context
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        first = [True]
        def delayed(*args, **kwargs):
            context = load_imported_browse_context(*args, **kwargs)
            if first[0]:
                first[0] = False
                entered.set()
                if not release.wait(15):
                    raise AssertionError('test did not release old metadata result')
            return context
        with patch('client_imported_browse.load_imported_browse_context', side_effect=delayed):
            self.client.backend.refresh()
            self.until(entered.is_set)
            self.import_archive()
            self.settle()
            self.assert_visible_gallery()
            before = self.client.backend.mapCount
            release.set()
            self.until(lambda: self.client.backend.stale_results > 0)
        self.assertEqual(self.client.backend.mapCount, before)
        self.assert_visible_gallery()

    def test_interrupted_writer_stays_guarded_until_verified_service_recovery(self):
        self.import_archive()
        self.settle()
        self.assert_visible_gallery()
        root = self.client.resource_resolver.root
        old = json.loads((root / 'catalog.json').read_bytes())
        proposed = dict(old, generation=str(uuid.uuid4()))
        result = subprocess.run([sys.executable, '-c', CHILD, str(root), json.dumps(proposed),
                                 'catalog-published', 'hardlink-v1'],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        self.client.backend.refresh()
        self.client.resources.listPackages()
        self.settle()
        self.assertTrue(self.client.backend.hasError)
        self.assertTrue(self.client.resources.recoveryRequired)
        self.assertEqual(self.client.backend.mapCount, 0)
        self.client.resources.requestRecovery()
        self.settle()
        self.assertEqual(self.client.resource_resolver.snapshot().generation, old['generation'])
        self.assertFalse(self.client.resources.recoveryRequired)
        self.assertTrue(self.archive.is_file())
        self.assert_visible_gallery()


if __name__ == '__main__':
    unittest.main()
