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
from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from imported_resource_catalog import ResourceResolver
from client_imported_resources import ImportedResourceService
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture as S0DifficultSourceFixture


def resource_zip(path, *, source, payload=None, version='v1'):
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members['manifest.json'])
    manifest.update(packageId='fixture', resourceVersion=version)
    if payload is not None:
        members['data/base/resource-context.json'] = payload
        row = next(row for row in manifest['files'] if row['componentId'] == 'base'
                   and row['path'] == 'resource-context.json')
        row.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    members['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, 'w') as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)


class ImportedResourceServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        temporary = tempfile.TemporaryDirectory(prefix='service-base-source-')
        cls.addClassCleanup(temporary.cleanup)
        cls.source = S0DifficultSourceFixture(Path(temporary.name))
        cls.source.export()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = RuntimePaths(self.root, False)
        self.resolver = ResourceResolver(self.paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
        self.factories = build_imported_engine_factories(self.resolver, self.paths)
        self.runtime = LiveRuntime(engine_factories=self.factories, evidence_kind='synthetic')
        self.session = SessionService(self.runtime)
        self.service = ImportedResourceService(self.resolver, paths=self.paths, engine_factories=self.factories)
        self.service.setLeaseProvider(self.session)
        self.addCleanup(self.close)
        self.errors, self.completions, self.changes = [], [], []
        self.service.errorReported.connect(self.errors.append)
        self.service.importCompleted.connect(lambda *args: self.completions.append(args))
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.archive = self.root / 'resource.zip'
        resource_zip(self.archive, source=self.source.output)
        with zipfile.ZipFile(self.archive) as archive:
            self.original_context = archive.read('data/base/resource-context.json')

    def close(self):
        self.service.shutdown()
        self.service.pool.waitForDone()
        self.app.processEvents()
        self.session.shutdown()
        self.session._pool.waitForDone()
        self.runtime.shutdown()

    def wait(self):
        deadline = time.monotonic() + 90
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

    def test_delete_base_confirmation_respects_lease_and_reimport_restores_mode(self):
        self.import_zip()
        self.assertFalse(self.errors, self.errors)
        self.assertTrue(self.service.contextAvailable('s0', 'difficult'))
        model = self.service.packageModel
        self.assertTrue(self.service.requestRemoval('fixture/v1'))
        self.wait()
        plan_id = self.service.removalPlanId
        self.assertIn('S0 困难', self.service.removalPlanSummary)
        before = (self.resolver.root / 'catalog.json').read_bytes()
        lease = self.session.acquireResourceLease()
        self.assertTrue(lease)
        try:
            self.assertFalse(self.service.confirmRemoval(plan_id))
            self.assertTrue(self.service.removalPlanOpen)
            self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), before)
        finally:
            self.session.releaseResourceLease(lease)
        self.assertTrue(self.service.cancelRemoval(plan_id))
        self.assertFalse(self.service.confirmRemoval(plan_id))
        self.assertTrue(self.service.requestRemoval('fixture/v1'))
        self.wait()
        self.assertTrue(self.service.confirmRemoval(self.service.removalPlanId))
        self.assertTrue(self.service.pendingWrites)
        self.wait()
        self.assertFalse(self.errors, self.errors)
        self.assertFalse(self.service.contextAvailable('s0', 'difficult'))
        self.assertEqual(self.service.packageRows, [])
        self.assertIs(self.service.packageModel, model)
        self.assertTrue(self.archive.is_file())
        self.assertEqual(self.changes, [True, True])
        self.import_zip()
        self.assertFalse(self.errors, self.errors)
        self.assertTrue(self.service.contextAvailable('s0', 'difficult'))
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')

    def test_actual_import_duplicate_and_verify_keep_base_and_controls_active(self):
        self.import_zip()
        target = self.resolver.root / 'packages/fixture/v1/data/base/resource-context.json'
        self.assertEqual(target.read_bytes(), self.original_context)
        self.assertTrue((self.resolver.root / 'catalog.json').exists())
        self.assertEqual(self.changes, [True])
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertEqual(self.service.packageRows[0]['selectedContexts'], ['s0:difficult'])
        mutations = self.service.model.mutations
        self.import_zip()
        self.assertEqual(self.service.model.mutations, mutations)
        self.assertIn('已经启用', self.service.message)
        self.service.refresh()
        self.wait()
        self.assertEqual(self.service.packageRows[0]['status'], 'selected')
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.completions), 2)

    def test_legacy_zip_rejection_preserves_selected_archive(self):
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('map-package.json', '{}')
        original = self.archive.read_bytes()
        self.import_zip()
        self.assertEqual(self.errors[-1]['errorCode'], 'legacy-format-unsupported')
        self.assertEqual(self.service.message, '旧图包格式已停用，请获取新版资源包；已有文件已保留。')
        self.assertEqual(self.archive.read_bytes(), original)
        self.assertFalse((self.resolver.root / 'packages').exists())
        self.assertEqual(self.changes, [])

    def test_conflicting_saved_version_does_not_replace_original(self):
        self.import_zip()
        resource_zip(self.archive, source=self.source.output, payload=b'{"changed":true}')
        self.import_zip()
        target = self.resolver.root / 'packages/fixture/v1/data/base/resource-context.json'
        self.assertEqual(target.read_bytes(), self.original_context)
        self.assertTrue(self.errors)
        self.assertEqual(len(self.completions), 1)

    def test_download_identity_checked_before_store(self):
        raw = self.archive.read_bytes()
        expected = dict(packageId='wrong-package', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.assertTrue(self.service.importVerifiedArchive(QUrl.fromLocalFile(str(self.archive)).toString(), expected, 0))
        self.wait()
        self.assertEqual(self.errors[-1]['errorCode'], 'resource-download-binding-invalid')
        self.assertFalse(self.resolver.root.exists())

    def test_cancelled_integrity_scan_preserves_active_context_and_model(self):
        from imported_resource_inventory import ImportedResourceInventoryError
        self.import_zip()
        before_rows = self.service.packageRows
        before_catalog = (self.resolver.root / 'catalog.json').read_bytes()
        before_mutations = self.service.model.mutations
        entered = threading.Event()
        def cancelled_scan(*args, cancelled=None, **kwargs):
            entered.set()
            deadline = time.monotonic() + 8
            while not cancelled() and time.monotonic() < deadline:
                time.sleep(.005)
            if not cancelled():
                raise AssertionError('test did not cancel inventory scan')
            raise ImportedResourceInventoryError('resource-inventory-cancelled')
        with patch('imported_resource_inventory.list_saved_versions', side_effect=cancelled_scan):
            self.service.refresh()
            self.assertTrue(entered.wait(5))
            self.service.cancel()
            self.wait()
        self.assertEqual(self.service.availableContexts, ['s0:difficult'])
        self.assertEqual(self.service.packageRows, before_rows)
        self.assertEqual(self.service.model.mutations, before_mutations)
        self.assertEqual((self.resolver.root / 'catalog.json').read_bytes(), before_catalog)
        self.assertFalse(self.errors, self.errors)

    def test_non_entrypoint_structure_damage_disables_mode_and_refuses_author(self):
        from imported_author_display import SCHEMA as DISPLAY_SCHEMA
        self.import_zip()
        base = self.resolver.snapshot().resolve_context('s0', 'difficult').base
        relative = next(path for path in base.inventory if 'legacy-clean-map29/fixed/' in path and path.endswith('.npz'))
        path = base.path(relative)
        raw = bytearray(path.read_bytes())
        raw[0] ^= 1
        path.write_bytes(raw)
        self.service.refresh()
        self.wait()
        self.assertEqual(self.service.packageRows[0]['status'], 'damaged')
        with self.subTest(boundary='mode-availability'):
            self.assertEqual(self.service.availableContexts, [])
        # Routing metadata is sufficient to exercise author admission; actual
        # display decoding is not replaced or claimed by this negative case.
        payload = json.dumps(dict(schema=DISPLAY_SCHEMA, profileId='author-fixture',
                                  season='s0', mode='difficult')).encode()
        ref = base.component_ref
        dependency = dict(packageId=ref.package_id, resourceVersion=ref.resource_version,
                          componentId=ref.component_id, manifestSha256=ref.manifest_sha256)
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId='damaged-base-author', resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=[dict(componentId='art', type='display', schemaVersion=1,
                contexts=[dict(season='s0', mode='difficult')], entrypoint='display.json',
                dependencies={'base': dependency})],
            files=[dict(componentId='art', path='display.json', bytes=len(payload),
                        sha256=hashlib.sha256(payload).hexdigest())])
        archive = self.root / 'damaged-base-author.zip'
        with zipfile.ZipFile(archive, 'x') as output:
            output.writestr('manifest.json', json.dumps(manifest))
            output.writestr('data/art/display.json', payload)
        self.service.importArchive(QUrl.fromLocalFile(str(archive)).toString())
        self.wait()
        with self.subTest(boundary='author-admission'):
            self.assertFalse((self.resolver.root / 'packages/damaged-base-author/v1').exists())
        self.assertEqual(self.changes, [True])

    def test_completed_save_remains_success_when_followup_inventory_fails(self):
        with patch('imported_resource_inventory.list_saved_versions', side_effect=OSError('test read interruption')):
            self.import_zip()
        self.assertTrue((self.resolver.root / 'packages/fixture/v1/manifest.json').is_file())
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.completions), 1)
        self.assertIn('列表暂时无法读取', self.service.message)

    def test_automatic_import_checks_availability_once_at_final_completion(self):
        from client_imported_admission import available_base_contexts
        with patch('client_imported_admission.available_base_contexts',
                   wraps=available_base_contexts) as available:
            self.import_zip()
        self.assertEqual(available.call_count, 1)
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.completions), 1)
        self.assertTrue(self.completions[0][0].endswith('已启用'))
        self.assertTrue(self.service.contextAvailable('s0', 'difficult'))
        self.assertEqual(len(self.service.packageRows), 1)
        self.assertFalse(self.service.activationPlanOpen)

    def test_cancel_after_save_reads_final_inventory_before_reporting(self):
        from imported_resource_archive import save_archive
        from client_imported_admission import available_base_contexts
        saved, resume = threading.Event(), threading.Event()

        def save_then_wait(*args, **kwargs):
            result = save_archive(*args, **kwargs)
            saved.set()
            if not resume.wait(30):
                raise RuntimeError('test save boundary timed out')
            return result

        with patch('imported_resource_archive.save_archive', side_effect=save_then_wait), \
                patch('client_imported_admission.available_base_contexts',
                      wraps=available_base_contexts) as available:
            try:
                self.assertTrue(self.service.importArchive(QUrl.fromLocalFile(str(self.archive)).toString()))
                deadline = time.monotonic() + 30
                while not saved.is_set() and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertTrue(saved.is_set())
                self.service.cancel()
            finally:
                resume.set()
            self.wait()
        self.assertEqual(available.call_count, 1)
        self.assertEqual(self.errors, [])
        self.assertEqual([row[0] for row in self.completions], ['启用已取消'])
        self.assertEqual(len(self.service.packageRows), 1)
        self.assertEqual(self.service.availableContexts, [])
        self.assertFalse(self.service.activationPlanOpen)
        self.assertFalse((self.resolver.root / 'catalog.json').exists())
        self.assertTrue(self.archive.is_file())

    def test_lease_denial_keeps_saved_package_and_reads_final_inventory(self):
        from client_imported_admission import available_base_contexts
        held = self.session.acquireResourceLease()
        self.assertTrue(held)
        try:
            with patch('client_imported_admission.available_base_contexts',
                       wraps=available_base_contexts) as available:
                self.import_zip()
            self.assertEqual(available.call_count, 1)
            self.assertEqual(len(self.completions), 1)
            self.assertTrue(self.completions[0][0].endswith('未启用'))
            self.assertEqual(len(self.service.packageRows), 1)
            self.assertEqual(self.service.availableContexts, [])
            self.assertFalse(self.service.activationPlanOpen)
            self.assertFalse((self.resolver.root / 'catalog.json').exists())
        finally:
            self.session.releaseResourceLease(held)
        retry = self.session.acquireResourceLease()
        self.assertTrue(retry)
        self.session.releaseResourceLease(retry)

    def test_shutdown_drains_accepted_save_without_late_ui_completion(self):
        from imported_resource_inventory import list_saved_versions
        class Publication(QObject):
            reached = Signal()
        publication, release = Publication(), threading.Event()
        observed, scan_releases = [], []
        saved_manifest = self.resolver.root / 'packages/fixture/v1/manifest.json'
        def shutdown_after_publication():
            try:
                before = (self.service.pendingWrites, QThread.currentThread() == self.app.thread(),
                          saved_manifest.is_file())
                self.service.shutdown()
                observed.append(before)
            finally:
                release.set()
        publication.reached.connect(shutdown_after_publication, Qt.QueuedConnection)
        def read_after_publication(*args, **kwargs):
            publication.reached.emit()
            released = release.wait(5)
            scan_releases.append(released)
            if not released:
                raise AssertionError('fixture did not release saved-version scan')
            return list_saved_versions(*args, **kwargs)
        with patch('imported_resource_inventory.list_saved_versions', side_effect=read_after_publication):
            self.service.importArchive(QUrl.fromLocalFile(str(self.archive)).toString())
            try:
                # Drive the same event loop as the UI while the real archive
                # is validated. Publication, rather than import duration,
                # determines when the shutdown under test takes place.
                self.wait()
            finally:
                release.set()
                self.service.pool.waitForDone()
                self.app.processEvents()
        self.assertEqual(observed, [(True, True, True)])
        self.assertEqual(scan_releases, [True])
        self.assertFalse(self.service.pendingWrites)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.completions, [])
        self.assertEqual(self.changes, [])
        self.assertTrue(saved_manifest.is_file())


if __name__ == '__main__':
    unittest.main()
