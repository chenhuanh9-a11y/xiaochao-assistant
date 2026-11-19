"""Selected component metadata and actual asynchronous, guarded Qt pixels."""
from concurrent.futures import CancelledError
import base64
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
from PySide6.QtCore import QSize, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQuick import QQuickView

import numpy as np

from client_imported_browse import imported_home_rows, load_imported_browse_context
from client_imported_gallery_images import ImportedGalleryImages
from backend import Backend
from client_paths import WORK_ROOT
import test_client_imported_author_selection as source_fixture


class ImportedBrowseTests(source_fixture._AuthorSelectionFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        super().setUpClass()

    def setUp(self):
        # Use real files in the existing client-owned test workspace. Only the
        # fixture's temporary-directory placement is changed, not path checks.
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with patch('tempfile.tempdir', str(WORK_ROOT)):
            super().setUp()
        (self.root/'.qt-lab-owner').write_text('qt-quick-isolated-v1', encoding='utf-8')

    def test_selected_metadata_and_pixels_use_actual_components_and_expire_together(self):
        display, _, members = self.package(package_id='author-browse')
        self.seed(displays=[display.component_ref])
        context = load_imported_browse_context(self.resolver(), 'difficult', season='s1')
        self.assertEqual([profile.profile_id for profile in context.profiles], ['s1-native-structure', 'author-fixture'])
        summary = context.profiles[1]
        self.assertEqual(summary.package_id, display.component_ref.package_id)
        self.assertEqual((summary.map_count, summary.available_maps, summary.available_layers), (14, 1, 1))
        self.assertEqual(summary.missing_floors, 55)
        self.assertEqual(summary.first_preview[:2], (self.source.ids[0], '-4'))
        self.assertEqual(context.floor_metadata['-4']['label'], 'B4')
        path = summary.first_preview[2]
        key = context.assets.key(path)
        np.testing.assert_array_equal(context.assets.pixels(key)[1, 2], (3, 2, 1, 0))
        path.write_bytes(b'changed after pixel cache was filled')
        with self.assertRaises(ValueError):
            context.assets.pixels(key)
        path.write_bytes(members['images/route.png'])
        np.testing.assert_array_equal(context.assets.pixels(key)[1, 2], (3, 2, 1, 0))
        home = imported_home_rows(self.resolver(), 'difficult', 'author-fixture', 's1')
        self.assertEqual(len(home), 1)
        self.assertEqual(home[0]['key'], f's1/difficult/author-fixture/{self.source.ids[0]}/variant-01/-4')
        self.assertEqual(home[0]['floorLabel'], 'B4')
        thumbnail = QImage.fromData(base64.b64decode(home[0]['imageUrl'].split(',', 1)[1]))
        self.assertEqual(thumbnail.pixelColor(2, 1).getRgb(), (1, 2, 3, 0))
        self.assertLessEqual(thumbnail.width(), 400)
        self.assertLessEqual(thumbnail.height(), 250)
        with self.assertRaisesRegex(ValueError, 'layer-unregistered'):
            context.assets.pixels(('author-fixture', 'unknown-map', '-4'))
        next_display, _, _ = self.package(package_id='author-browse', resource_version='v2')
        self.activate(next_display)
        with self.assertRaises(ValueError):
            context.assets.pixels(key)

    def test_bad_optional_component_does_not_hide_other_author_and_cancel_stays_cancelled(self):
        good, _, _ = self.package(package_id='author-good', profile_id='author-good')
        bad, _, _ = self.package(package_id='author-bad', profile_id='author-bad')
        self.seed(displays=[good.component_ref, bad.component_ref])
        bad.path('images/route.png').write_bytes(b'broken image')
        cancelled = threading.Event()
        context = load_imported_browse_context(self.resolver(), 'difficult', season='s1',
                                               cancelled=cancelled.is_set)
        # Cards describe declarations. Only requesting the selected layer can
        # establish whether its current pixels are actually usable.
        self.assertEqual([profile.profile_id for profile in context.profiles],
                         ['s1-native-structure', 'author-good', 'author-bad'])
        self.assertIsNone(context.catalog)
        profiles = {profile.profile_id: profile for profile in context.profiles}
        good_key = context.assets.key(profiles['author-good'].first_preview[2])
        bad_key = context.assets.key(profiles['author-bad'].first_preview[2])
        with self.assertRaises(ValueError):
            context.assets.pixels(bad_key)
        np.testing.assert_array_equal(context.assets.pixels(good_key)[1, 2], (3, 2, 1, 0))
        fallback = imported_home_rows(self.resolver(), 'difficult', 'author-bad', 's1')
        structure = imported_home_rows(self.resolver(), 'difficult', 'shared-structure', 's1')
        self.assertTrue(fallback)
        self.assertTrue(all(row['structure'] and row['key'] == '' for row in fallback))
        self.assertEqual([row['imageUrl'] for row in fallback], [row['imageUrl'] for row in structure])
        cancelled.set()
        with self.assertRaises(CancelledError):
            context.assets.pixels(good_key)

    def test_structure_home_and_warm_pixels_use_actual_base_without_author_or_ranking(self):
        import client_imported_providers as providers
        self.seed()
        before = self.resolver().snapshot().catalog_stamp
        with patch.object(providers, 'build_native_engine', wraps=providers.build_native_engine) as engines:
            context = load_imported_browse_context(self.resolver(), 'difficult', season='s1')
            self.assertEqual([profile.profile_id for profile in context.profiles], ['s1-native-structure'])
            summary = context.profiles[0]
            self.assertEqual((summary.map_count, summary.available_maps, summary.available_layers), (14, 14, 56))
            home = imported_home_rows(self.resolver(), 'difficult', 'shared-structure', 's1')
            fallback = imported_home_rows(self.resolver(), 'difficult', 'author-missing', 's1')
            self.assertEqual(engines.call_count, 0)
        for rows, preferred in ((home, 'shared-structure'), (fallback, 'author-missing')):
            self.assertEqual(len(rows), 3)
            self.assertEqual(len({row['title'] for row in rows}), 3)
            for row in rows:
                self.assertTrue(row['structure'])
                self.assertEqual(row['key'], '')
                self.assertEqual(row['profileId'], preferred)
                self.assertEqual(row['floorLabel'], 'B1')
                self.assertEqual(row['displayKind'], '结构底图')
                image = QImage.fromData(base64.b64decode(row['imageUrl'].split(',', 1)[1]))
                self.assertFalse(image.isNull())
                self.assertLessEqual(image.width(), 400)
                self.assertLessEqual(image.height(), 250)
        path = summary.first_preview[2]
        key = context.assets.key(path)
        pixels = context.assets.pixels(key).copy()
        original = path.read_bytes()
        path.write_bytes(b'broken after structure reference cache was warmed')
        with self.assertRaises(ValueError):
            context.assets.pixels(key)
        path.write_bytes(original)
        np.testing.assert_array_equal(context.assets.pixels(key), pixels)
        self.assertEqual(self.resolver().snapshot().catalog_stamp, before)
        self.assertFalse(self.paths.memory_root('difficult', 's1').exists())
        provider = ImportedGalleryImages()
        provider.publish(context.assets)
        url = provider.url(path)
        manifest = self.base_view.root.parent.parent/'manifest.json'
        replacement = manifest.with_name('test-identical-manifest.json')
        replacement.write_bytes(manifest.read_bytes())
        os.replace(replacement, manifest)
        with self.assertRaises(ValueError):
            context.assets.pixels(key)
        self.assertTrue(provider.requestImage(url.split('image://importedgallery/', 1)[1], QSize(), QSize()).isNull())

    def test_backend_refresh_keeps_content_instances_and_resource_change_revokes_old_urls(self):
        display, _, _ = self.package(package_id='author-browse')
        self.seed(displays=[display.component_ref])
        provider = ImportedGalleryImages()
        backend = Backend(self.root, resource_resolver=self.resolver(), gallery_images=provider, defer_loading=True)
        self.addCleanup(backend.shutdown)
        backend.setContext('s1', 'difficult')
        self.assertFalse(backend.busy)
        self.assertEqual(backend.pool.activeThreadCount(), 0)
        self.assertFalse(backend.profiles)
        backend.setActive(True)
        def wait():
            deadline = time.monotonic() + 90
            while backend.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertFalse(backend.busy, backend.message)
            self.assertFalse(backend.hasError, backend.message)
        wait()
        self.assertEqual([row['id'] for row in backend.profiles], ['s1-native-structure', 'author-fixture'])
        self.assertFalse(backend.profiles[0]['userProvided'])
        self.assertEqual(backend.profiles[0]['runtimeProfileId'], 'shared-structure')
        self.assertTrue(backend.profiles[0]['runtimeSelectable'])
        backend.setProfile('author-fixture')
        self.assertEqual({row['value']:row['name'] for row in backend.floorOptions},
            {'':'全部楼层', '-1':'B1', '-2':'B2', '-3':'B3', '-4':'B4'})
        self.assertTrue(all(row['imageUrl'].startswith('image://importedgallery/') for row in backend.model.rows))
        self.assertTrue(backend.profiles[1]['userProvided'])
        backend.select(backend.model.rows[0]['key'])
        key, url = backend.selectedKey, backend.selectedImage
        row_mutations, profile_mutations = backend.model.mutations, backend.gallery_profiles.mutations
        backend.setProfile(backend.profileId)
        backend.refresh()
        wait()
        self.assertEqual((backend.selectedKey, backend.selectedImage), (key, url))
        self.assertEqual((backend.model.mutations, backend.gallery_profiles.mutations),
                         (row_mutations, profile_mutations))
        # A preview using the published URL stays valid while a healthy
        # metadata refresh has cancelled its old worker token.
        size = QSize()
        backend.refresh()
        self.assertFalse(provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize()).isNull())
        wait()
        # A completed hidden worker is drained without writing page models.
        entered, release = threading.Event(), threading.Event()
        original = load_imported_browse_context
        def delayed(*args, **kwargs):
            value = original(*args, **kwargs)
            entered.set()
            if not release.wait(5):
                raise AssertionError('test browse worker was not released')
            return value
        with patch('client_imported_browse.load_imported_browse_context', side_effect=delayed):
            backend.refresh()
            deadline = time.monotonic() + 60
            while not entered.is_set() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
            self.assertTrue(entered.is_set())
            backend.setActive(False)
            release.set()
            wait()
        self.assertEqual((backend.model.mutations, backend.gallery_profiles.mutations),
                         (row_mutations, profile_mutations))
        self.assertEqual(backend.selectedImage, url)
        backend.setActive(True)
        wait()
        next_display, _, _ = self.package(package_id='author-browse', resource_version='v2',
            payloads={'images/route.png':source_fixture.changed_png()})
        self.activate(next_display)
        backend.setActive(False)
        backend.invalidateResources()
        self.assertEqual(backend.selectedKey, key)
        self.assertEqual(backend.selectedImage, '')
        self.assertFalse(backend.busy, 'hidden gallery defers metadata preparation')
        self.assertTrue(provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize()).isNull())
        backend.setActive(True)
        wait()
        self.assertEqual(backend.selectedKey, key)
        self.assertNotEqual(backend.selectedImage, url)
        image = provider.requestImage(backend.selectedImage.split('image://importedgallery/', 1)[1], size, QSize())
        self.assertEqual(image.pixelColor(0, 0).getRgb(), (91, 92, 93, 94))
        backend.setContext('s0', 'nightmare')
        self.assertEqual(backend.selectedImage, '')
        self.assertTrue(provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize()).isNull())
        wait()
        self.assertFalse(backend.profiles)

    def test_qml_requests_worker_pixels_and_clearing_rejects_an_inflight_result(self):
        display, _, _ = self.package(package_id='author-browse')
        self.seed(displays=[display.component_ref])
        context = load_imported_browse_context(self.resolver(), 'difficult', season='s1')
        provider = ImportedGalleryImages()
        provider.publish(context.assets)
        path = context.profiles[1].first_preview[2]
        url = provider.url(path)
        provider.publish(context.assets)
        self.assertEqual(provider.url(path), url, 'idle unchanged publication must preserve the URL')
        self.assertTrue(url.startswith('image://importedgallery/'))
        self.assertNotIn(str(self.resources), url)
        size = QSize()
        image = provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize(2, 2))
        self.assertFalse(image.isNull())
        self.assertLessEqual(max(image.width(), image.height()), 2)
        full = provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize())
        self.assertEqual(full.pixelColor(2, 1).getRgb(), (1, 2, 3, 0))
        self.assertEqual(size, QSize(5, 3))
        view = QQuickView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.engine().addImageProvider('importedgallery', provider)
        qml = self.root/'gallery-image.qml'
        qml.write_text('import QtQuick\nImage { width: 100; height: 100; readonly property int loadStatus: status; asynchronous: false; cache: false; source: "' + url + '" }', encoding='utf-8')
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        threads = []
        original = context.assets.pixels
        def delayed(key):
            pixels = original(key)
            threads.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise AssertionError('test image worker was not released')
            return pixels
        with patch.object(context.assets, 'pixels', side_effect=delayed):
            view.setSource(QUrl.fromLocalFile(str(qml)))
            self.assertEqual(view.errors(), [])
            self.assertTrue(entered.wait(3), 'Image should call the asynchronous provider worker')
            provider.publish(context.assets)
            self.assertNotEqual(provider.url(path), url, 'publication must replace an inflight URL')
            provider.clear()
            release.set()
            deadline = time.monotonic() + 5
            while view.rootObject().property('loadStatus') == 2 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.005)
        self.assertTrue(threads)
        self.assertTrue(all(thread != threading.get_ident() for thread in threads))
        self.assertEqual(view.rootObject().property('loadStatus'), 3)
        self.assertTrue(provider.requestImage(url.split('image://importedgallery/', 1)[1], size, QSize()).isNull())
        self.assertEqual(size, QSize(0, 0))
        provider.publish(context.assets)
        view.rootObject().setProperty('source', provider.url(path))
        deadline = time.monotonic() + 5
        while view.rootObject().property('loadStatus') == 2 and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(view.rootObject().property('loadStatus'), 1)
