"""Original component declarations -> actual Backend worker/models -> pixels."""
from concurrent.futures import CancelledError
import os
import json
from pathlib import Path
import threading
import time
import unittest
from uuid import uuid4
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
import cv2
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication

from backend import Backend
from client_paths import WORK_ROOT
from client_imported_browse import load_imported_browse_context, imported_home_rows
from client_imported_gallery_images import ImportedGalleryImages
from imported_resource_catalog import ResourceResolver
from imported_resource_packages import PackageView
from client_imported_providers import SUPPORTED_CAPABILITIES
from test_imported_author_display import _AuthorDisplayFixture
import test_imported_author_displays as author_fixtures
from original_qt_resource_fixtures import OriginalS0NightmareSourceFixture


class OriginalBrowseTests(_AuthorDisplayFixture):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        super().setUpClass()

    def setUp(self):
        WORK_ROOT.mkdir(parents=True,exist_ok=True)
        with patch('tempfile.tempdir',str(WORK_ROOT)):
            super().setUp()
        (self.root/'.qt-lab-owner').write_text('qt-quick-isolated-v1',encoding='utf-8')

    def seed(self,views):
        factory,_ = author_fixtures.ImportedAuthorDisplaysTests.factory(self,views)
        factory.invalidate()

    def resolver(self):
        return ResourceResolver(self.resources,supported_capabilities=SUPPORTED_CAPABILITIES)

    def context(self,**kwargs):
        return load_imported_browse_context(self.resolver(),'difficult',season='s1',**kwargs)

    def test_cards_read_only_metadata_without_recognition_or_image_payloads(self):
        view,_,_ = self.package()
        self.seed([view])
        reads=[]
        original = PackageView._read_payload
        def payload(root,inventory,component,relative):
            reads.append(relative)
            if Path(relative).suffix.lower() in {'.npz','.png','.jpg','.jpeg'}:
                raise AssertionError('pixel/structure payload read before cards: '+relative)
            return original(root,inventory,component,relative)
        with (patch.object(PackageView,'_read_payload',staticmethod(payload)),
              patch('client_imported_providers.load_native_resources',side_effect=AssertionError('recognition preparation')),
              patch('imported_author_display.decode_author_display',side_effect=AssertionError('all-author decoder'))):
            context=self.context()
        self.assertEqual([p.profile_id for p in context.profiles],['s1-native-structure','author-fixture'])
        self.assertTrue(reads)
        self.assertIsNone(context.catalog,'browse declarations do not grant author qualification')
        self.assertEqual(context.profiles[1].available_layers,1)

    def test_actual_backend_worker_publishes_models_and_provider_reads_selected_pixels(self):
        view,_,_=self.package()
        self.seed([view])
        images=ImportedGalleryImages()
        backend=Backend(self.root,resource_resolver=self.resolver(),gallery_images=images,defer_loading=True)
        self.addCleanup(backend.shutdown)
        worker_threads=[]
        original=load_imported_browse_context
        def observed(*args,**kwargs):
            worker_threads.append(threading.get_ident())
            return original(*args,**kwargs)
        backend.setContext('s1','difficult')
        with patch('client_imported_browse.load_imported_browse_context',observed):
            backend.setActive(True)
            deadline=time.monotonic()+15
            while backend.busy and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.002)
            self.app.processEvents()
        self.assertFalse(backend.busy,backend.message)
        self.assertFalse(backend.hasError,backend.message)
        self.assertTrue(worker_threads)
        self.assertNotIn(threading.get_ident(),worker_threads)
        backend.setProfile('author-fixture')
        self.assertEqual(len(backend.model.rows),1)
        url=backend.model.rows[0]['imageUrl']
        identifier=url.split('image://importedgallery/',1)[1]
        image=images.requestImage(identifier,QSize(),QSize())
        self.assertFalse(image.isNull())
        self.assertEqual(image.pixelColor(2,1).getRgb(),(1,2,3,0))
        view.path('images/route.png').write_bytes(b'broken after preview')
        self.assertTrue(images.requestImage(identifier,QSize(),QSize()).isNull())
        backend.invalidateResources()
        self.assertTrue(images.requestImage(identifier,QSize(),QSize()).isNull())

    def test_optional_bad_header_is_isolated_and_bad_pixels_are_deferred(self):
        good,_,_=self.package(package_id='good',profile_id='author-good')
        bad,_,_=self.package(package_id='bad',profile_id='author-bad')
        self.seed([good,bad])
        bad.path(bad.component['entrypoint']).write_bytes(b'broken header')
        context=self.context()
        self.assertEqual([p.profile_id for p in context.profiles],['s1-native-structure','author-good'])
        self.assertTrue(context.warnings)
        good.path('images/route.png').write_bytes(b'bad image is not a metadata read')
        context=self.context()
        self.assertEqual(context.profiles[1].profile_id,'author-good')
        with self.assertRaises(ValueError):
            context.assets.pixels(context.assets.key(context.profiles[1].first_preview[2]))
        structure=context.profiles[0].first_preview[2]
        self.assertEqual(context.assets.pixels(context.assets.key(structure)).shape[-1],4)

    def test_pixels_validate_reference_affine_bytes_and_catalog_after_decode(self):
        view,_,members=self.package()
        self.seed([view])
        context=self.context()
        path=context.profiles[1].first_preview[2]
        key=context.assets.key(path)
        pixels=context.assets.pixels(key)
        np.testing.assert_array_equal(pixels[1,2],(3,2,1,0))
        structure=context.profiles[0].maps[0].variants[0].layers[key[2]]
        raw=structure.read_bytes()
        try:
            structure.write_bytes(b'X'*len(raw))
            with self.assertRaises(ValueError):
                context.assets.pixels(key)
        finally:
            structure.write_bytes(raw)
        decode=cv2.imdecode
        def late_change(*args,**kwargs):
            result=decode(*args,**kwargs)
            author_fixtures.ImportedAuthorDisplaysTests.supersede_catalog(self)
            return result
        with patch('cv2.imdecode',late_change),self.assertRaises(ValueError):
            context.assets.pixels(key)

    def test_cancelled_metadata_reader_and_replaced_author_manifest_cannot_return_pixels(self):
        view,_,_=self.package()
        self.seed([view])
        cancelled=threading.Event()
        context=self.context(cancelled=cancelled.is_set)
        key=context.assets.key(context.profiles[1].first_preview[2])
        cancelled.set()
        with self.assertRaises(CancelledError):
            context.assets.pixels(key)
        cancelled.clear()
        manifest=view.root.parent.parent/'manifest.json'
        replacement=manifest.with_suffix('.replacement')
        replacement.write_bytes(manifest.read_bytes())
        replacement.replace(manifest)
        with self.assertRaises(ValueError):
            context.assets.pixels(key)

    def test_author_declarations_preserve_authority_affine_and_actual_reference_checks(self):
        bad_affine,_,_=self.package(lambda d:d['layers'][0].update(
            matrixDisplayToReference=[[1,0,0],[0,0,0],[0,0,1]]),profile_id='author-bad-affine')
        bad_authority,_,_=self.package(lambda d:d.update(identityAuthority=True),profile_id='author-bad-authority')
        bad_space,_,_=self.package(lambda d:d['layers'][0].update(coordinateSpace='wrong-space'),profile_id='author-bad-space')
        self.seed([bad_affine,bad_authority,bad_space])
        context=self.context()
        self.assertEqual([p.profile_id for p in context.profiles],['s1-native-structure','author-bad-space'])
        self.assertTrue(context.warnings)
        # The typed index does not declare the canvas; actual requested NPZ
        # supplies that evidence, so the card cannot grant pixel permission.
        with self.assertRaisesRegex(ValueError,'reference-binding-mismatch:coordinateSpace'):
            context.assets.pixels(context.assets.key(context.profiles[1].first_preview[2]))

    def test_optional_manifest_replacement_during_metadata_keeps_base_but_required_replacement_fails(self):
        view,_,_=self.package()
        self.seed([view])
        original=PackageView._read_payload
        def replace_after_read(target):
            replaced=False
            package_root=target.root.parent.parent
            component_id=target.component_ref.component_id
            def payload(root,inventory,component,relative):
                nonlocal replaced
                raw=original(root,inventory,component,relative)
                if root==package_root and component==component_id and not replaced:
                    replaced=True
                    manifest=package_root/'manifest.json'
                    temporary=manifest.with_suffix('.replacement')
                    temporary.write_bytes(manifest.read_bytes())
                    temporary.replace(manifest)
                return raw
            return payload
        with patch.object(PackageView,'_read_payload',staticmethod(replace_after_read(view))):
            context=self.context()
        self.assertEqual([p.profile_id for p in context.profiles],['s1-native-structure'])
        self.assertTrue(context.warnings)
        with patch.object(PackageView,'_read_payload',staticmethod(replace_after_read(self.base_view))):
            with self.assertRaises(ValueError):
                self.context()

    def test_selected_structure_and_author_paths_reject_links_inserted_after_cards(self):
        view,_,_=self.package()
        self.seed([view])
        context=self.context()
        author_key=context.assets.key(context.profiles[1].first_preview[2])
        structure=context.profiles[0].maps[0].variants[0].layers[author_key[2]]
        for path in (context.profiles[1].first_preview[2],structure):
            with self.subTest(path=path.name):
                backup=path.with_suffix(path.suffix+'.original')
                path.rename(backup)
                try:
                    try:
                        path.symlink_to(backup)
                    except OSError as error:
                        self.skipTest('OS does not permit creating a test symlink: '+str(error))
                    with self.assertRaises(ValueError):
                        context.assets.pixels(author_key)
                finally:
                    if path.is_symlink():
                        path.unlink()
                    backup.rename(path)

    def test_home_bad_author_pixels_fall_back_to_actual_structure_but_bad_required_reference_is_rejected(self):
        view,_,_=self.package()
        self.seed([view])
        context=self.context()
        author_key=context.assets.key(context.profiles[1].first_preview[2])
        view.path('images/route.png').write_bytes(b'broken selected author image')
        rows=imported_home_rows(self.resolver(),'difficult','author-fixture','s1')
        self.assertEqual(len(rows),3)
        self.assertTrue(all(row['structure'] and row['key']=='' for row in rows))
        self.assertTrue(all(row['imageUrl'].startswith('data:image/png;base64,') for row in rows))
        structure=context.profiles[0].maps[0].variants[0].layers[author_key[2]]
        structure.write_bytes(b'broken required selected reference')
        with self.assertRaises(ValueError):
            imported_home_rows(self.resolver(),'difficult','author-fixture','s1')

    def test_home_optional_failure_does_not_swallow_late_base_catalog_or_cancellation(self):
        from author_display_validation import validate_display_image
        view,_,_=self.package()
        self.seed([view])
        context=self.context()
        key=context.assets.key(context.profiles[1].first_preview[2])
        required=context.profiles[0].maps[0].variants[0].layers[key[2]]
        required_raw=required.read_bytes()
        manifest=self.base_view.root.parent.parent/'manifest.json'
        cancelled=threading.Event()
        for change in ('required-bytes','required-manifest','catalog','cancel'):
            with self.subTest(change=change):
                def late_optional_failure(*args,**kwargs):
                    validate_display_image(*args,**kwargs)
                    if change=='required-bytes':
                        required.write_bytes(b'changed during actual author decode')
                    elif change=='required-manifest':
                        replacement=manifest.with_suffix('.replacement')
                        replacement.write_bytes(manifest.read_bytes())
                        replacement.replace(manifest)
                    elif change=='catalog':
                        author_fixtures.ImportedAuthorDisplaysTests.supersede_catalog(self)
                    else:
                        cancelled.set()
                    raise ValueError('optional display failed after actual image validation')
                try:
                    with patch('author_display_validation.validate_display_image',late_optional_failure):
                        with self.assertRaises(CancelledError if change=='cancel' else ValueError):
                            imported_home_rows(self.resolver(),'difficult','author-fixture','s1',cancelled=cancelled.is_set)
                finally:
                    required.write_bytes(required_raw)
                    cancelled.clear()


class S0NightmareOriginalBrowseTests(unittest.TestCase):
    def test_original_schema_two_without_floor_or_mode_fields_uses_original_structure_declarations(self):
        import tempfile
        from imported_resource_archive import save_archive
        temporary=tempfile.TemporaryDirectory(prefix='original-s0n-browse-')
        self.addCleanup(temporary.cleanup)
        fixture=OriginalS0NightmareSourceFixture(Path(temporary.name),ids=('nightmare-01',))
        source=fixture.source
        pack_path=source.sourceRoot/'references/nightmare-map-pack.json'
        pack=json.loads(pack_path.read_bytes())
        pack.pop('mode',None)
        pack.pop('floors',None)
        pack['defaultEntryFloor']=2
        self.assertEqual(pack['schemaVersion'],2)
        self.assertNotIn('mode',pack)
        self.assertNotIn('floors',pack)
        pack_path.write_text(json.dumps(pack),encoding='utf-8')
        source.refresh_inventory()
        fixture.export()
        root=source.root/'installed'
        saved=save_archive(fixture.output,root)
        refs={ref.component_id:ref for ref in saved.component_refs}
        wire=author_fixtures.ImportedAuthorDisplaysTests.wire
        catalog=dict(schema='xingtu-resource-catalog-v1',generation=str(uuid4()),
            contexts={'s0:nightmare':dict(recognitionBase=wire(refs['base']),controls=wire(refs['controls']),reviewedGroups=None)},
            displays=[])
        (root/'catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
        context=load_imported_browse_context(ResourceResolver(root,supported_capabilities=SUPPORTED_CAPABILITIES),'nightmare')
        self.assertEqual(context.pack.floors,('1','2','B1'))
        self.assertEqual(context.pack.default_entry_floor,'2')
        self.assertEqual(context.profiles[0].available_layers,3)
        self.assertIsNone(context.catalog)
        first=context.profiles[0].first_preview[2]
        self.assertEqual(context.assets.pixels(context.assets.key(first)).shape,(8,8,4))
