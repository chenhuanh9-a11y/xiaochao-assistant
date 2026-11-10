"""Qt worker -> real ZIP/store/inventory; no installed tree or game IO."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from imported_resource_catalog import ResourceResolver
from client_imported_resources import ImportedResourceService


def resource_zip(path, *, payload=b'{}', version='v1'):
    manifest = dict(schema='xingtu-resource-package', formatVersion=1,
        packageId='fixture', resourceVersion=version,
        clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
        components=[dict(componentId='base', type='recognition-base', schemaVersion=1,
            contexts=[dict(season='s0', mode='nightmare')], entrypoint='context.json', dependencies={})],
        files=[dict(componentId='base', path='context.json', bytes=len(payload),
                    sha256=hashlib.sha256(payload).hexdigest())])
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('data/base/context.json', payload)


class ImportedResourceServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.resolver = ResourceResolver(self.root / 'resources')
        self.service = ImportedResourceService(self.resolver)
        self.addCleanup(self.close)
        self.errors, self.completions, self.changes = [], [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.importCompleted.connect(lambda *args: self.completions.append(args))
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.archive = self.root / 'resource.zip'
        resource_zip(self.archive)

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()

    def wait(self):
        deadline = time.monotonic() + 8
        while self.service.busy and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.service.busy)

    def import_zip(self):
        self.service.importArchive(QUrl.fromLocalFile(str(self.archive)).toString())
        self.wait()

    def test_empty_read_creates_nothing_and_keeps_model_identity(self):
        model = self.service.packageModel
        self.service.listPackages()
        self.wait()
        self.assertEqual(self.service.packageRows, [])
        self.assertFalse(self.resolver.root.exists())
        self.service.refresh()
        self.wait()
        self.assertIs(self.service.packageModel, model)
        self.assertEqual(model.mutations, 0)

    def test_actual_save_duplicate_and_verify_never_activate(self):
        self.import_zip()
        target = self.resolver.root / 'packages/fixture/v1/data/base/context.json'
        self.assertEqual(target.read_bytes(), b'{}')
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertEqual(self.changes, [])
        self.assertEqual(self.service.packageRows[0]['status'], 'saved-unverified')
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], [])
        mutations = self.service.model.mutations
        self.import_zip()
        self.assertEqual(self.service.model.mutations, mutations)
        self.assertIn('已经保存', self.service.message)
        self.service.refresh()
        self.wait()
        self.assertEqual(self.service.packageRows[0]['status'], 'saved')
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.completions), 2)

    def test_legacy_zip_rejection_preserves_selected_archive(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('map-package.json', '{}')
        original = self.archive.read_bytes()
        self.import_zip()
        self.assertEqual(self.errors[-1]['errorCode'], 'legacy-format-unsupported')
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse((self.resolver.root / 'packages').exists())
        self.assertEqual(self.changes, [])

    def test_conflicting_saved_version_does_not_replace_original(self):
        self.import_zip()
        resource_zip(self.archive, payload=b'{"changed":true}')
        self.import_zip()
        target = self.resolver.root / 'packages/fixture/v1/data/base/context.json'
        self.assertEqual(target.read_bytes(), b'{}')
        self.assertTrue(self.errors)
        self.assertEqual(len(self.completions), 1)

    def test_download_identity_checked_before_store(self):
        raw = self.archive.read_bytes()
        expected = dict(packageId='wrong-package', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.assertTrue(self.service.importVerifiedArchive(QUrl.fromLocalFile(str(self.archive)).toString(), expected, 0))
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-download-binding-invalid')
        self.assertFalse(self.resolver.root.exists())

    def test_completed_save_remains_success_when_followup_inventory_fails(self):
        with patch('imported_resource_inventory.list_saved_versions', side_effect=OSError('test read interruption')):
            self.import_zip()
        self.assertTrue((self.resolver.root / 'packages/fixture/v1/manifest.json').is_file())
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.completions), 1)
        self.assertIn('列表暂时无法读取', self.service.message)

    def test_shutdown_drains_accepted_save_without_late_ui_completion(self):
        from imported_resource_inventory import list_saved_versions
        published, release = threading.Event(), threading.Event()
        def read_after_publication(*args, **kwargs):
            published.set()
            if not release.wait(5):
                raise AssertionError('fixture did not release saved-version scan')
            return list_saved_versions(*args, **kwargs)
        with patch('imported_resource_inventory.list_saved_versions', side_effect=read_after_publication):
            self.service.importArchive(QUrl.fromLocalFile(str(self.archive)).toString())
            try:
                self.assertTrue(published.wait(3))
                self.assertTrue(self.service.pendingWrites)
                self.service.shutdown()
            finally:
                release.set()
                self.service.pool.waitForDone()
                self.app.processEvents()
        self.assertFalse(self.service.pendingWrites)
        self.assertEqual(self.completions, [])
        self.assertEqual(self.changes, [])
        self.assertTrue((self.resolver.root / 'packages/fixture/v1/manifest.json').is_file())


if __name__ == '__main__':
    unittest.main()
