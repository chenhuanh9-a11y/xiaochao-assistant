"""Real gallery readers after real package transactions, over synthetic PNG/JSON.

No FakeBackend, reader replacement, installed resources, native picker or game.
The shared resource fixture supplies the marked installation and archive hashes;
SessionService supplies its real idle lease. This is a service/model integration
check, complementary to the dialog entry tests in test_client_gallery_actions.
"""
import io
import json
import time
import unittest
import zipfile

from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication, QImage

# Keep this module safe as the first member of a combined Qt UI test run.
APP = QGuiApplication.instance() or QGuiApplication([])

import test_client_resources as fixtures
from backend import Backend
from client_package_manager import PackageManagerService
from client_session import SessionService


class GalleryResourceRefreshTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ResourceServiceTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.resources = self.fixture.service
        # Replace the fixture's opaque dependency bytes with parseable synthetic
        # declarations. No production maps or author assets are copied.
        for path in fixtures.importer.BASE_DEPENDENCIES:
            self.write_json(path, {})
        self.write_json('_internal/references/nightmare-map-pack.json', dict(
            packId='synthetic-gallery', floors=['1'], defaultEntryFloor='1', maps=[dict(
                id='map-01', name='Synthetic map', resourceBase='resource-root',
                routeProfileId='suju', layers={'1': 'assets/suju/map-01-1.png'})]))
        self.write_json('_internal/references/nightmare-author-route-catalog.json',
                        self.catalog('suju', 'assets/suju/map-01-1.png'))
        self.fixture.dependencies = [fixtures.row(path, (self.root / path).read_bytes())
                                    for path in sorted(fixtures.importer.BASE_DEPENDENCIES)]
        self.session = SessionService()
        self.addCleanup(self.session.shutdown)
        self.resources.setLeaseProvider(self.session)
        self.backend = Backend(self.root)
        self.addCleanup(self.backend.shutdown)
        self.manager = PackageManagerService(self.root, self.resources)
        self.manager.setLeaseProvider(self.session)
        self.addCleanup(self.close_manager)
        # Production resource-change fanout: no manual refresh after a write.
        self.manager.resourcesChanged.connect(self.resources.resourcesChanged.emit)
        self.resources.resourcesChanged.connect(self.manager.requestRefresh)
        self.resources.resourcesChanged.connect(self.backend.refresh)
        self.problems = []
        self.manager.errorReported.connect(self.problems.append)
        self.until(lambda: not self.backend.busy)
        self.assertFalse(self.backend.hasError, self.backend.message)

    def close_manager(self):
        self.manager.shutdown()
        self.assertTrue(self.manager.pool.waitForDone(15000))
        fixtures.APP.processEvents()

    def until(self, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            fixtures.APP.processEvents()
            if predicate():
                return
            time.sleep(.002)
        self.fail('Qt workers timed out: ' + self.resources.message + '; ' + self.backend.message)

    def settled(self):
        self.until(lambda: not self.resources.busy and not self.manager.busy and not self.backend.busy)
        self.assertFalse(self.fixture.problems + self.problems)
        self.assertFalse(self.session.snapshot.get('resourceLocked'))

    def write_json(self, path, data):
        self.fixture.write(path, json.dumps(data).encode())

    @staticmethod
    def catalog(profile, path):
        return dict(schemaVersion=1, defaultProfileId=profile, profiles=[dict(
            id=profile, displayName=profile, authorName=profile, routeFamilyName='synthetic',
            runtimeEnabled=False, settingsSelectable=False, maps={'map-01': [dict(
                id='v1', displayName='Synthetic route', identityStatus='display-only',
                displayPolicy='preview-only', layers={'1': path})]})])

    @staticmethod
    def png(color):
        data = io.BytesIO()
        Image.new('RGB', (8, 6), color).save(data, format='PNG')
        return data.getvalue()

    def optional_package(self, name, color):
        image = 'assets/authors/nightingale/map-01-1.png'
        files = {'_internal/' + image: self.png(color),
                 '_internal/references/author-packs/nightingale/catalog.json':
                     json.dumps(self.catalog('nightingale', image)).encode()}
        manifest = dict(schemaVersion=1, revision=1, packageId='nightmare-nightingale',
                        clientCompatibility=fixtures.importer.CLIENT_COMPATIBILITY,
                        requiresFiles=self.fixture.dependencies,
                        files=[fixtures.row(path, data) for path, data in files.items()])
        archive = self.fixture.folder / name
        with zipfile.ZipFile(archive, 'w') as target:
            target.writestr('map-package.json', json.dumps(manifest))
            for path, data in files.items():
                target.writestr('data/' + path, data)
        return archive

    def install(self, archive, *, update=False):
        before = len(self.fixture.completions)
        self.resources.importArchive(QUrl.fromLocalFile(str(archive)).toString(), 's0')
        if update:
            self.until(lambda: not self.resources.busy)
            self.assertEqual(self.resources.pendingKind, 'overwrite', self.resources.message)
            self.resources.confirmOperation(True)
        self.until(lambda: len(self.fixture.completions) > before or bool(self.fixture.problems))
        self.settled()

    def remove(self, package):
        self.manager.requestRemove(package)
        self.until(lambda: not self.manager.busy)
        self.assertEqual(self.manager.pendingKind, 'remove', self.manager.message)
        self.manager.confirmOperation(True)
        self.settled()
        self.assertFalse(any(row['packageId'] == package for row in self.manager.packageRows))

    def assert_color(self, url, color):
        self.assertTrue(url)
        image = QImage(QUrl(url).toLocalFile())
        self.assertFalse(image.isNull(), url)
        self.assertEqual((image.width(), image.height()), (8, 6))
        self.assertEqual(image.pixelColor(0, 0).name(), color)
        self.assertIn('season=s0', url)

    def assert_selected_preview(self, color):
        self.assertEqual(self.backend.mapCount, 1)
        self.assertEqual(len(self.backend.model.rows), 1)
        self.assertEqual(self.backend.preview_model.rows, self.backend.model.rows)
        self.backend.selectFirstLayer()
        self.assert_color(self.backend.selectedImage, color)
        profile = next(row for row in self.backend.profiles if row['id'] == self.backend.profileId)
        self.assertEqual((profile['available'], profile['availableMaps']), (1, 1))
        self.assert_color(profile['previewUrl'], color)
        return self.backend.selectedKey, self.backend.selectedImage

    def test_import_and_delete_optional_author_refresh_actual_profiles_rows_and_preview(self):
        self.assertNotIn('nightingale', [row['id'] for row in self.backend.profiles])
        self.install(self.optional_package('author.zip', 'red'))
        self.assertIn('nightingale', [row['id'] for row in self.backend.profiles])
        self.backend.setProfile('nightingale')
        self.assert_selected_preview('#ff0000')
        self.remove('nightmare-nightingale')
        self.assertNotIn('nightingale', [row['id'] for row in self.backend.profiles])
        self.assertFalse(any('/nightingale/' in row['key'] for row in self.backend.model.rows))
        self.assertEqual(self.backend.preview_model.rows, [])
        self.assertEqual(self.backend.selectedImage, '')
        self.assertEqual(self.backend.selectedKey, '')
        self.assertEqual(self.backend.mapCount, 0)

    def test_s0_update_while_browsing_s1_stays_isolated_and_restores_current_preview(self):
        self.install(self.optional_package('old.zip', 'red'))
        self.backend.setProfile('nightingale')
        key, old_url = self.assert_selected_preview('#ff0000')
        self.backend.setSeason('s1')
        self.until(lambda: not self.backend.busy)
        observed = []
        def observe():
            observed.append((self.backend.season, list(self.backend.profiles),
                             list(self.backend.model.rows), list(self.backend.preview_model.rows),
                             self.backend.selectedImage))
        self.backend.changed.connect(observe)
        try:
            self.install(self.optional_package('new.zip', 'blue'), update=True)
        finally:
            self.backend.changed.disconnect(observe)
        self.assertTrue(observed, 'The resource write must reach the actual browse reader')
        self.assertTrue(all(state == ('s1', [], [], [], '') for state in observed), observed)
        self.assertEqual(self.backend.season, 's1')
        self.assertIn('资源待补齐', self.backend.message)
        self.assertEqual(self.backend.profiles, [])
        self.assertEqual(self.backend.model.rows, [])
        self.assertEqual(self.backend.preview_model.rows, [])
        self.assertEqual(self.backend.selectedImage, '')
        self.backend.setSeason('s0')
        self.until(lambda: not self.backend.busy)
        self.assertEqual(self.backend.profileId, 'nightingale')
        self.assertEqual(self.backend.selectedKey, key)
        restored_key, new_url = self.assert_selected_preview('#0000ff')
        self.assertEqual(restored_key, key)
        self.assertNotEqual(new_url, old_url, 'Changed pixels must invalidate the Qt image cache URL')

    def test_base_author_delete_hides_card_retains_declaration_and_reimport_restores_it(self):
        self.assertEqual(self.backend.profiles, [])
        self.assertEqual(self.backend.profileId, '')
        self.install(self.fixture.package('base.zip', self.png('red')))
        self.assert_selected_preview('#ff0000')
        self.remove('nightmare-suju')
        # Repair metadata survives, but missing authors must not occupy cards.
        self.assertEqual([row.profile_id for row in self.backend.context.profiles], ['suju'])
        self.assertEqual(self.backend.context.profiles[0].available_layers, 0)
        self.assertEqual(self.backend.profiles, [])
        self.assertEqual(self.backend.profileId, '')
        self.assertEqual(self.backend.mapCount, 0)
        self.assertEqual(self.backend.model.rows, [])
        self.assertEqual(self.backend.preview_model.rows, [])
        self.assertEqual(self.backend.selectedImage, '')
        self.assertEqual(self.backend.selectedKey, '')
        self.install(self.fixture.package('base-again.zip', self.png('blue')))
        self.assertEqual([row['id'] for row in self.backend.profiles], ['suju'])
        self.assert_selected_preview('#0000ff')

    def test_selected_author_removal_immediately_falls_back_without_stale_preview(self):
        self.install(self.fixture.package('base.zip', self.png('red')))
        self.install(self.optional_package('optional.zip', 'blue'))
        self.backend.setProfile('suju')
        self.assert_selected_preview('#ff0000')
        self.remove('nightmare-suju')
        self.assertEqual(self.backend.profileId, 'nightingale')
        self.assertEqual(self.backend.selectedKey, '')
        self.assertEqual(self.backend.selectedImage, '')
        key, url = self.assert_selected_preview('#0000ff')
        self.backend.refresh()
        self.until(lambda: not self.backend.busy)
        self.assertEqual(self.backend.selectedKey, key)
        self.assertEqual(self.backend.selectedImage, url)
        self.assertEqual([row['id'] for row in self.backend.profiles], ['nightingale'])

    def test_selected_author_removal_falls_back_to_available_author_across_seasons(self):
        self.install(self.fixture.package('base.zip', self.png('red')))
        self.install(self.optional_package('optional.zip', 'blue'))
        self.backend.setProfile('suju')
        self.assert_selected_preview('#ff0000')
        self.backend.setSeason('s1')
        self.until(lambda: not self.backend.busy)
        self.remove('nightmare-suju')
        self.assertEqual(self.backend.profiles, [])
        self.backend.setSeason('s0')
        self.until(lambda: not self.backend.busy)
        self.assertEqual([row['id'] for row in self.backend.profiles], ['nightingale'])
        self.assertEqual(self.backend.profileId, 'nightingale')
        self.assert_selected_preview('#0000ff')
        self.backend.setProfile('suju')
        self.assertEqual(self.backend.profileId, 'nightingale')


if __name__ == '__main__':
    unittest.main()
