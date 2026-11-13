"""Real archive/store admission; metadata fixtures grant no recognition authority."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import time
import unittest
import zipfile

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PIL import Image
from client_imported_resources import ImportedResourceService
from client_imported_admission import admit_import, available_base_contexts
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_live_runtime import LiveRuntime
from client_paths import RuntimePaths
from client_session import SessionService
from export_author_display_component import INPUT_SCHEMA, export_component
from imported_author_display import SCHEMA as DISPLAY_SCHEMA
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import ComponentRef
from original_qt_resource_fixtures import OriginalS0NightmareSourceFixture
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
        self.paths = RuntimePaths(self.folder, False)
        self.root = self.paths.imported_resource_root
        self.resolver = ResourceResolver(self.root, supported_capabilities=SUPPORTED_CAPABILITIES)

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
        source = OriginalS0NightmareSourceFixture(self.folder / 'original-s0n-source')
        source.export()
        saved = save_archive(source.output, self.root)
        refs = {ref.component_id: dict(packageId=ref.package_id,
            resourceVersion=ref.resource_version, componentId=ref.component_id,
            manifestSha256=ref.manifest_sha256) for ref in saved.component_refs}
        base, controls = refs['base'], refs['controls']
        self.catalog({'s0:nightmare': self.context(base, controls)})
        self.base_source = source.source
        return base

    def real_author_archive(self, base):
        base_ref = ComponentRef(base['packageId'], base['resourceVersion'],
            base['componentId'], base['manifestSha256'])
        images = self.folder / 'author-images'
        images.mkdir()
        stream = BytesIO()
        Image.new('RGBA', (5, 3), (64, 128, 192, 255)).save(stream, format='PNG')
        (images / 'route.png').write_bytes(stream.getvalue())
        registration = self.folder / 'author-registration.json'
        registration.write_text(json.dumps(dict(schema=INPUT_SCHEMA,
            profileId='author-route-fixture', authorName='Fixture Author',
            displayName='Fixture Route', layers=[dict(mapId=self.base_source.ids[0],
                floor='1', image='route.png',
                matrixDisplayToReference=[[1, 0, 0], [0, 1, 0], [0, 0, 1]])]),
            ensure_ascii=False), encoding='utf-8')
        output = self.folder / 'author-route.xingtu-route'
        export_component(self.root, base_ref, registration, images, output,
            package_id='author-route', resource_version='fixture-v1')
        return output

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
        factories = build_imported_engine_factories(self.resolver, self.paths)
        runtime = LiveRuntime(engine_factories=factories, evidence_kind='synthetic')
        session = SessionService(runtime)
        service = ImportedResourceService(self.resolver, paths=self.paths,
            engine_factories=factories)
        service.setLeaseProvider(session)
        errors = []
        service.errorReported.connect(errors.append)
        try:
            self.assertTrue(service.importArchive(QUrl.fromLocalFile(str(archive)).toString()))
            deadline = time.monotonic() + 90
            while service.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertFalse(service.busy)
            return errors, service.message
        finally:
            service.shutdown()
            service.pool.waitForDone()
            self.app.processEvents()
            session.shutdown()
            session._pool.waitForDone()
            runtime.shutdown()
            factories.invalidate()

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
        base = self.active_base()
        archive = self.real_author_archive(base)
        errors, _ = self.import_with_service(archive)
        self.assertEqual(errors, [])
        with zipfile.ZipFile(archive) as source:
            manifest = json.loads(source.read('manifest.json'))
            component = manifest['components'][0]
            relative = f"data/{component['componentId']}/{component['entrypoint']}"
            expected = source.read(relative)
        installed = (self.root / 'packages' / 'author-route' / 'fixture-v1' /
                     relative)
        self.assertEqual(installed.read_bytes(), expected)


if __name__ == '__main__':
    unittest.main()
