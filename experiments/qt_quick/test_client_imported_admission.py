"""Real archive/store admission; metadata fixtures grant no recognition authority."""
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from client_imported_resources import ImportedResourceService
from client_imported_admission import admit_import, available_base_contexts
from imported_author_display import SCHEMA as DISPLAY_SCHEMA
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
import test_imported_resource_catalog as catalog_fixture


class ImportedAdmissionTests(unittest.TestCase):
    component = catalog_fixture.ImportedCatalogTests.component
    catalog = catalog_fixture.ImportedCatalogTests.catalog
    context = staticmethod(catalog_fixture.ImportedCatalogTests.context)

    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='import-admission-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.root = self.folder / 'resources'
        self.resolver = ResourceResolver(self.root)

    def archive(self, components, *, payloads=None, name='candidate', suffix='.zip'):
        payloads = payloads or {row['componentId']: b'{}' for row in components}
        manifest = dict(schema='xingtu-resource-package', formatVersion=1,
            packageId=name, resourceVersion='v1',
            clientCompatibility=dict(resourceProtocol=1, requiredCapabilities=[]),
            components=components, files=[dict(componentId=row['componentId'], path=row['entrypoint'],
                bytes=len(payloads[row['componentId']]),
                sha256=hashlib.sha256(payloads[row['componentId']]).hexdigest()) for row in components])
        path = self.folder / (name + suffix)
        with zipfile.ZipFile(path, 'x') as output:
            output.writestr('manifest.json', json.dumps(manifest))
            for row in components:
                output.writestr('data/' + row['componentId'] + '/' + row['entrypoint'], payloads[row['componentId']])
        return path

    @staticmethod
    def declaration(component_id, kind, *, season='s0', mode='nightmare', dependencies=None):
        return dict(componentId=component_id, type=kind, schemaVersion=1,
            contexts=[dict(season=season, mode=mode)], entrypoint='entry.json', dependencies=dependencies or {})

    def author_archive(self, dependency, *, season='s0', mode='nightmare', name='author', suffix='.zip'):
        document = dict(schema=DISPLAY_SCHEMA, profileId='author-fixture', season=season, mode=mode)
        component = self.declaration('art', 'display', season=season, mode=mode, dependencies={'base': dependency})
        return self.archive([component], payloads={'art': json.dumps(document).encode()}, name=name, suffix=suffix)

    def save(self, archive):
        return save_archive(archive, self.root,
            validate_components=lambda views: admit_import(views, self.resolver))

    def active_base(self):
        base = self.component('base', 'recognition-base')
        controls = self.component('controls', 'controls-profile')
        self.catalog({'s0:nightmare': self.context(base, controls)})
        return base

    def test_base_and_controls_must_be_in_same_archive(self):
        for kind in ('recognition-base', 'controls-profile'):
            with self.subTest(kind=kind):
                archive = self.archive([self.declaration('one', kind)], name=kind)
                with self.assertRaisesRegex(ValueError, 'base-controls-pair-required'):
                    self.save(archive)
                self.assertFalse((self.root / 'packages' / kind / 'v1').exists())

    def test_complete_pair_is_saved_and_returns_exact_auto_activation_context(self):
        archive = self.archive([self.declaration('base', 'recognition-base'),
                                self.declaration('controls', 'controls-profile')])
        contexts = []
        saved = save_archive(archive, self.root, validate_components=lambda views:
            contexts.extend(admit_import(views, self.resolver)))
        self.assertEqual(len(contexts), 1)
        self.assertEqual(contexts[0][:2], ('s0', 'nightmare'))
        self.assertEqual(set(contexts[0][2:]), set(saved.component_refs))
        self.assertFalse((self.root / 'catalog.json').exists())  # Storage is not activation.

    def test_saved_but_inactive_base_does_not_admit_author(self):
        base = self.component('base', 'recognition-base')
        archive = self.author_archive(base)
        with self.assertRaisesRegex(ValueError, 'base-context-not-active'):
            self.save(archive)
        self.assertFalse((self.root / 'packages/author/v1').exists())

    def test_active_exact_base_admits_author_and_other_context_stays_unavailable(self):
        base = self.active_base()
        self.assertEqual(available_base_contexts(self.resolver.snapshot()), ('s0:nightmare',))
        saved = self.save(self.author_archive(base))
        self.assertEqual(saved.status, 'saved')
        with self.assertRaisesRegex(ValueError, 'base-context-not-active'):
            self.save(self.author_archive(base, season='s1', name='other-context'))
        self.assertFalse((self.root / 'packages/other-context/v1').exists())

    def test_active_base_version_must_match_author_dependency(self):
        base = self.active_base()
        other = dict(base, resourceVersion='2')
        with self.assertRaisesRegex(ValueError, 'base-version-mismatch'):
            self.save(self.author_archive(other))
        self.assertFalse((self.root / 'packages/author/v1').exists())

    def test_duplicate_import_still_checks_current_base_without_deleting_saved_author(self):
        base = self.active_base()
        archive = self.author_archive(base)
        self.save(archive)
        target = self.root / 'packages/author/v1/data/art/entry.json'
        original = target.read_bytes()
        self.catalog({})
        self.assertEqual(available_base_contexts(self.resolver.snapshot()), ())
        with self.assertRaisesRegex(ValueError, 'base-context-not-active'):
            self.save(archive)
        self.assertEqual(target.read_bytes(), original)

    def test_real_service_refuses_author_before_base_is_imported_and_active(self):
        missing = dict(packageId='missing-base', resourceVersion='1', componentId='base', manifestSha256='a' * 64)
        archive = self.author_archive(missing)
        original = archive.read_bytes()
        service = ImportedResourceService(self.resolver)
        errors = []
        service.errorReported.connect(errors.append)
        try:
            service.importArchive(QUrl.fromLocalFile(str(archive)).toString())
            deadline = time.monotonic() + 8
            while service.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertFalse(service.busy)
            self.assertFalse((self.root / 'packages/author/v1').exists())
            self.assertTrue(errors)
            self.assertIn('基础', service.message)
            self.assertEqual(archive.read_bytes(), original)
        finally:
            service.shutdown()
            service.pool.waitForDone()
            self.app.processEvents()

    def import_with_service(self, archive):
        service = ImportedResourceService(self.resolver)
        errors = []
        service.errorReported.connect(errors.append)
        try:
            self.assertTrue(service.importArchive(QUrl.fromLocalFile(str(archive)).toString()))
            deadline = time.monotonic() + 8
            while service.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertFalse(service.busy)
            return errors, service.message
        finally:
            service.shutdown()
            service.pool.waitForDone()
            self.app.processEvents()

    def test_real_service_checks_filename_role_before_publishing_either_package_kind(self):
        base = self.active_base()
        for suffix, expected_code in (
                ('.xingtu-base', 'resource-package-extension-mismatch'),
                ('.other', 'resource-package-extension-unsupported')):
            with self.subTest(suffix=suffix):
                name = 'wrong-author-' + suffix[1:]
                archive = self.author_archive(base, name=name, suffix=suffix)
                original = archive.read_bytes()
                errors, message = self.import_with_service(archive)
                self.assertTrue(errors)
                self.assertEqual(errors[0]['errorCode'], expected_code)
                self.assertFalse((self.root / 'packages' / name / 'v1').exists())
                self.assertEqual(archive.read_bytes(), original)
        archive = self.archive([self.declaration('base', 'recognition-base'),
                                self.declaration('controls', 'controls-profile')],
                               name='wrong-base', suffix='.xingtu-route')
        errors, message = self.import_with_service(archive)
        self.assertTrue(errors)
        self.assertIn('后缀', message)
        self.assertFalse((self.root / 'packages/wrong-base/v1').exists())

    def test_real_service_imports_route_suffix_with_active_exact_base(self):
        # Metadata fixture tests save/admission only. Base auto-activation and
        # actual display decoding are covered by their real-resource suites.
        base = self.active_base()
        archive = self.author_archive(base, suffix='.xingtu-route')
        errors, _ = self.import_with_service(archive)
        self.assertEqual(errors, [])
        with zipfile.ZipFile(archive) as source:
            expected = source.read('data/art/entry.json')
        self.assertEqual((self.root / 'packages/author/v1/data/art/entry.json').read_bytes(),
                         expected)


if __name__ == '__main__':
    unittest.main()
