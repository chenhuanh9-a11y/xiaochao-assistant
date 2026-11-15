"""Dual display packages through original resources and the real Qt consumer."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
from PIL import Image

from original_qt_resource_fixtures import OriginalNightmareBaseFixture
from client_paths import RuntimePaths
from client_imported_activation import activate_context, activate_display
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from imported_resource_archive import save_archive
from imported_resource_catalog import ResourceResolver
from client_route_preferences import prepare_display_layer, shared_structure_display_current
from client_providers import ProviderUnavailable
from resource_transaction import resource_runtime_lease


class RenderedMapPackagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OriginalNightmareBaseFixture.setUpClass()
        cls.addClassCleanup(OriginalNightmareBaseFixture.doClassCleanups)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rendered-map-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.images = self.root / 'images'
        self.images.mkdir()
        image = Image.new('RGBA', (8, 8), (31, 77, 129, 255))
        image.putpixel((2, 3), (5, 9, 17, 93))
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        (self.images / 'render.png').write_bytes(buffer.getvalue())
        self.map_id = OriginalNightmareBaseFixture.fixture.rows[0]['mapId']
        self.floor = OriginalNightmareBaseFixture.fixture.rows[0]['floor']
        self.matrix = [[1, 0, 2], [0, 1, 3], [0, 0, 1]]
        self.registration = self.root / 'registration.json'
        self.registration.write_text(json.dumps(dict(schema='xingtu-author-display-registration-v1',
            profileId='author-rendered-map', authorName='无路线渲染图', displayName='无路线渲染图',
            layers=[dict(mapId=self.map_id, floor=self.floor, image='render.png',
                matrixDisplayToReference=self.matrix)])), encoding='utf8')

    def export(self):
        from export_rendered_map_packages import export_dual_pack
        self.base_output, self.route_output = self.root/'rendered.xingtu-base', self.root/'rendered.xingtu-route'
        return export_dual_pack(OriginalNightmareBaseFixture.archive, self.registration,
            self.images, self.base_output, self.route_output, resource_version='rendered-v1',
            route_package_id='rendered-map-test', route_resource_version='rendered-v1')

    def engine(self, archive, name='store'):
        paths = RuntimePaths(self.root/name, False)
        saved = save_archive(archive, paths.imported_resource_root)
        refs = {ref.component_id:ref for ref in saved.component_refs}
        resolver = ResourceResolver(paths.imported_resource_root, supported_capabilities=SUPPORTED_CAPABILITIES)
        activate_context(resolver, paths, refs['base'], refs['controls'], season='s0', mode='nightmare')
        factories = build_imported_engine_factories(resolver, paths)
        self.addCleanup(factories.invalidate)
        return factories.get_engine('nightmare'), resolver, paths

    def test_attached_default_reaches_actual_display_pixels(self):
        self.export()
        engine, _, _ = self.engine(self.base_output)
        layer = prepare_display_layer(engine, self.map_id, self.floor, 'shared-structure')
        self.assertEqual(layer['displayKind'], 'base-rendered')
        self.assertEqual(tuple(layer['pixels'][3, 2]), (17, 9, 5, 93))
        from client_settings import DEFAULTS
        default = prepare_display_layer(engine,self.map_id,self.floor,DEFAULTS['routeProfileId'])
        self.assertEqual(default['displayKind'],'base-rendered')
        np.testing.assert_array_equal(default['pixels'],layer['pixels'])
        np.testing.assert_array_equal(layer['matrixDisplayToReference'], self.matrix)
        self.assertTrue(shared_structure_display_current(engine,self.map_id,self.floor,layer))
        from client_route_reprojection import prepare_route_reprojection
        canonical = np.array([[.8,0,20],[0,.8,30],[0,0,1.]])
        before = canonical.copy()
        reprojected = prepare_route_reprojection(engine,self.map_id,self.floor,'shared-structure',canonical)
        basis = engine.qt_adapters.matrix_profile_to_canonical('shared-structure',self.map_id,self.floor)
        np.testing.assert_allclose(reprojected['matrixSourceToClient'],canonical@basis@np.asarray(self.matrix))
        np.testing.assert_array_equal(canonical,before)
        self.assertNotIn('authorization',reprojected)
        self.assertFalse(engine.memory_store.root.exists())
        # Qt's actual image provider owns and reads these pixels, not a mock.
        from PySide6.QtCore import QSize
        from client_coordinator import LiveImages
        images = LiveImages()
        url = images.publish('reference:rendered', layer['pixels'][..., [2,1,0,3]])
        image = images.requestImage(url.removeprefix('image://live/'), QSize(), QSize())
        self.assertEqual(image.pixelColor(2, 3).getRgb(), (5,9,17,93))
        images.clear()
        self.assertTrue(images.requestImage(url.removeprefix('image://live/'), QSize(), QSize()).isNull())

    def test_independent_package_is_opt_in_and_survives_restart_and_base_switch(self):
        self.export()
        engine, resolver, paths = self.engine(OriginalNightmareBaseFixture.archive)
        self.assertEqual(prepare_display_layer(engine,self.map_id,self.floor,'shared-structure')['displayKind'],
                         'shared-structure')
        display, = save_archive(self.route_output, paths.imported_resource_root).component_refs
        with resource_runtime_lease(resolver.root):
            activate_display(resolver,paths,display)
        factories = build_imported_engine_factories(resolver,paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        selected = prepare_display_layer(engine,self.map_id,self.floor,'author-rendered-map')
        self.assertEqual(selected['displayKind'],'author-route')
        self.assertEqual(tuple(selected['pixels'][3,2]),(17,9,5,93))
        attached = {r.component_id:r for r in save_archive(self.base_output,paths.imported_resource_root).component_refs}
        activate_context(resolver,paths,attached['base'],attached['controls'],season='s0',mode='nightmare')
        restarted = build_imported_engine_factories(ResourceResolver(resolver.root,
            supported_capabilities=SUPPORTED_CAPABILITIES),paths)
        self.addCleanup(restarted.invalidate)
        fresh = restarted.get_engine('nightmare')
        self.assertIn(display,resolver.snapshot().displays)
        actual = prepare_display_layer(fresh,self.map_id,self.floor,'author-rendered-map')
        self.assertEqual(actual['displayKind'],'author-route')
        np.testing.assert_array_equal(actual['pixels'],selected['pixels'])
        np.testing.assert_array_equal(actual['matrixDisplayToReference'],self.matrix)
        self.assertEqual(tuple(fresh.pack.by_id),tuple(engine.pack.by_id))
        self.assertEqual(fresh.pack.floors,engine.pack.floors)

    def test_explicit_author_and_custom_selection_precede_attached_default(self):
        self.export()
        engine, resolver, paths = self.engine(self.base_output)
        document = json.loads(self.registration.read_text())
        document.update(profileId='author-explicit', authorName='合成作者', displayName='明确选择')
        document['layers'][0]['image'] = 'author.png'
        Image.new('RGBA',(4,4),(21,22,23,255)).save(self.images/'author.png')
        other_registration = self.root/'author.json'
        other_registration.write_text(json.dumps(document),encoding='utf8')
        from export_author_display_component import export_component
        ref = engine.qt_native_base.component_ref
        other = self.root/'author.xingtu-route'
        export_component(resolver.root,ref,other_registration,self.images,other,
            package_id='explicit-author',resource_version='v1')
        display, = save_archive(other, resolver.root).component_refs
        with resource_runtime_lease(resolver.root):
            activate_display(resolver,paths,display)
        factories = build_imported_engine_factories(resolver,paths)
        self.addCleanup(factories.invalidate)
        selected = factories.get_engine('nightmare')
        layer = prepare_display_layer(selected,self.map_id,self.floor,'author-explicit')
        self.assertEqual(layer['displayKind'],'author-route')
        self.assertEqual(tuple(layer['pixels'][0,0]),(23,22,21,255))
        # Only the custom drawing store is substituted; real resource reads,
        # selection and its final pixel consumer remain the production code.
        pixels = np.full((2,2,4),(1,2,3,255),np.uint8)
        selected.qt_custom_display = lambda profile,mid,fl,reference: (dict(pixels=pixels,
            matrixDisplayToReference=np.eye(3),resourceIdentity='synthetic-custom',displayKind='custom',
            displayLabel='自建路线',routeProfileId='shared-structure') if profile=='custom:fixture' else None)
        custom = prepare_display_layer(selected,self.map_id,self.floor,'custom:fixture')
        self.assertEqual(custom['displayKind'],'custom')
        self.assertIs(custom['pixels'],pixels)

    def variant(self, edit):
        with zipfile.ZipFile(self.base_output) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        edit(members)
        manifest = json.loads(members['manifest.json'])
        for row in manifest['files']:
            raw = members['data/'+row['componentId']+'/'+row['path']]
            row.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
        path = self.root/('invalid-'+str(len(list(self.root.glob('invalid-*'))))+'.xingtu-base')
        with zipfile.ZipFile(path,'x') as archive:
            for name,raw in members.items():
                archive.writestr(name,raw)
        return path

    def test_semantic_bad_images_and_references_reject_before_activation(self):
        self.export()
        def edit_document(members,edit):
            name = 'data/base/rendered-display/display.json'
            document = json.loads(members[name]); edit(document)
            members[name] = json.dumps(document).encode()
        variants = [
            ('wrong-map',lambda d:d['layers'][0].update(mapId='foreign-map')),
            ('wrong-floor',lambda d:d['layers'][0].update(floor='foreign-floor')),
            ('wrong-reference',lambda d:d['layers'][0].update(coordinateSpace='foreign-space')),
            ('invalid-matrix',lambda d:d['layers'][0].update(matrixDisplayToReference=[[0,0,0]]*3)),
            ('self-dependency',lambda d:d.update(baseDependency='base'))]
        for name,edit in variants:
            with self.subTest(name=name):
                archive = self.variant(lambda m:edit_document(m,edit))
                with self.assertRaises((OSError,ValueError)):
                    self.engine(archive,name)
                self.assertFalse((RuntimePaths(self.root/name,False).imported_resource_root/'catalog.json').exists())
        def corrupt(members):
            name = next(n for n in members if n.startswith('data/base/rendered-display/images/'))
            members[name] = b'not a bitmap'
        with self.assertRaises((OSError,ValueError)):
            self.engine(self.variant(corrupt),'bad-image')

    def test_missing_base_and_old_capability_reject(self):
        self.export()
        paths = RuntimePaths(self.root/'empty',False)
        display, = save_archive(self.route_output,paths.imported_resource_root).component_refs
        resolver = ResourceResolver(paths.imported_resource_root,supported_capabilities=SUPPORTED_CAPABILITIES)
        with resource_runtime_lease(resolver.root):
            with self.assertRaises((OSError,ValueError)):
                activate_display(resolver,paths,display)
        _, _, attached_paths = self.engine(self.base_output,'attached')
        old = ResourceResolver(attached_paths.imported_resource_root,
            supported_capabilities=SUPPORTED_CAPABILITIES-{'original-qt-rendered-display-v1'})
        self.assertTrue(old.snapshot().resolve_context('s0','nightmare').gaps)

    def test_cache_rechecks_actual_bytes_and_old_engine_cannot_return_after_switch(self):
        self.export()
        engine,resolver,paths = self.engine(self.base_output)
        prepare_display_layer(engine,self.map_id,self.floor,'shared-structure')
        package = engine.qt_rendered_display.display
        path = package.image_path(self.map_id,self.floor)
        path.write_bytes(b'corrupt')
        with self.assertRaises((OSError,ValueError)):
            prepare_display_layer(engine,self.map_id,self.floor,'shared-structure')
        # A different intact base version invalidates the previous resource
        # owner even if a decoded color buffer is still retained by it.
        saved = save_archive(OriginalNightmareBaseFixture.archive,resolver.root)
        refs = {r.component_id:r for r in saved.component_refs}
        activate_context(resolver,paths,refs['base'],refs['controls'],season='s0',mode='nightmare')
        with self.assertRaises((OSError,ValueError)):
            prepare_display_layer(engine,self.map_id,self.floor,'shared-structure')

    def test_two_forms_share_images_and_preserve_all_structure_control_bytes(self):
        self.export()
        with zipfile.ZipFile(OriginalNightmareBaseFixture.archive) as old, \
                zipfile.ZipFile(self.base_output) as base, zipfile.ZipFile(self.route_output) as route:
            for name in old.namelist():
                if name not in {'manifest.json','data/base/resource-context.json'}:
                    self.assertEqual(old.read(name),base.read(name),name)
            manifest = json.loads(route.read('manifest.json'))
            component, = manifest['components']
            doc = json.loads(route.read('data/'+component['componentId']+'/display.json'))
            for row in doc['layers']:
                self.assertEqual(route.read('data/'+component['componentId']+'/'+row['displayPath']),
                    base.read('data/base/rendered-display/'+row['displayPath']))

    def test_empty_store_and_uncovered_floor_keep_existing_behavior(self):
        empty = RuntimePaths(self.root/'empty',False)
        resolver = ResourceResolver(empty.imported_resource_root,supported_capabilities=SUPPORTED_CAPABILITIES)
        factories = build_imported_engine_factories(resolver,empty)
        self.addCleanup(factories.invalidate)
        with self.assertRaises(ProviderUnavailable):
            factories.get_engine('nightmare')
        self.export()
        engine,_,_ = self.engine(self.base_output)
        other = next(row for row in OriginalNightmareBaseFixture.fixture.rows
            if (row['mapId'],row['floor']) != (self.map_id,self.floor))
        layer = prepare_display_layer(engine,other['mapId'],other['floor'],'shared-structure')
        self.assertEqual(layer['displayKind'],'shared-structure')

    def test_registered_image_digest_is_not_bypassed(self):
        self.export()
        with zipfile.ZipFile(self.base_output) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        image = next(row for row in manifest['files'] if row['path'].startswith('rendered-display/images/'))
        image['sha256'] = '0'*64
        members['manifest.json'] = json.dumps(manifest).encode()
        output = self.root/'wrong-digest.xingtu-base'
        with zipfile.ZipFile(output,'x') as archive:
            for name,raw in members.items(): archive.writestr(name,raw)
        with self.assertRaises((OSError,ValueError)):
            self.engine(output)

    def test_late_real_rendered_pixels_cannot_revive_after_lifecycle_change(self):
        self.export()
        actual,_,_ = self.engine(self.base_output)
        from test_client_live_runtime import LiveRuntimeContracts
        from client_route_reprojection import prepare_route_reprojection
        # Capture, observation and solver are the existing synthetic fixture.
        # Its display reads the actual imported package and original reader;
        # neither the display function nor resource validation is mocked.
        for action in ('close_map','select_candidate','set_floor','end_round'):
            with self.subTest(action=action):
                case = LiveRuntimeContracts()
                case.setUp()
                try:
                    for name in ('qt_rendered_display','qt_shared_provider','qt_adapters',
                                 'qt_resource_read_scope','qt_verify_resource_read_scope','qt_assert_resource_current'):
                        setattr(case.engine,name,getattr(actual,name))
                    case.fit['selectedRouteCurrentFrameResidual']['coordinatePackId'] = actual.qt_adapters.canonical_pack_id
                    case.dispatch('set_route_profile',profileId='shared-structure')
                    case.begin_and_capture()
                    if action=='select_candidate':
                        second = SimpleNamespace(id='second-map',name='Second synthetic map')
                        case.engine.pack.maps.append(second)
                        case.engine.pack.by_id[second.id] = second
                        case.runtime._update_candidates([], '1')
                        # Opening the chooser intentionally retains current
                        # pixels. Selecting a different map is the invalidation.
                        self.assertTrue(case.dispatch('reselect_map')['success'])
                    before = case.runtime.snapshot()
                    entered,release = threading.Event(),threading.Event()
                    output=[]
                    def delayed(*args,**kwargs):
                        layer = prepare_route_reprojection(*args,**kwargs)
                        self.assertEqual(layer['layer']['displayKind'],'base-rendered')
                        entered.set()
                        if not release.wait(5): raise TimeoutError('test display was not released')
                        return layer
                    with patch('client_route_reprojection.prepare_route_reprojection',side_effect=delayed):
                        worker = threading.Thread(target=lambda:output.append(case.dispatch(
                            'set_route_profile',profileId='missing-author')))
                        worker.start()
                        action_worker = None
                        action_result = []
                        action_done = threading.Event()
                        try:
                            self.assertTrue(entered.wait(5))
                            if action=='set_floor':
                                # The floor intent invalidates before its new
                                # capture waits for the running display work.
                                epoch = case.runtime._epoch
                                def change_floor():
                                    action_result.append(case.dispatch(action,floor='2'))
                                    action_done.set()
                                action_worker = threading.Thread(target=change_floor)
                                action_worker.start()
                                deadline = time.monotonic()+2
                                while case.runtime._epoch==epoch and time.monotonic()<deadline:
                                    time.sleep(.001)
                                self.assertNotEqual(case.runtime._epoch,epoch)
                            else:
                                self.assertTrue(case.dispatch(action,**({'index':1} if action=='select_candidate' else {}))['success'])
                        finally:
                            release.set();worker.join(5)
                            if action_worker is not None: action_worker.join(5)
                    self.assertFalse(worker.is_alive())
                    self.assertTrue(output[0].get('stale'),output)
                    if action_worker is not None:
                        self.assertFalse(action_worker.is_alive())
                        self.assertEqual(action_result[0]['reason'],'current-frame-floor-conflict',action_result)
                    self.assertIsNone(case.runtime._render)
                    self.assertIsNone(case.runtime.render_payload(before['render']['token']))
                    if action=='close_map':
                        self.assertEqual(case.runtime.snapshot()['core']['poses'],before['core']['poses'])
                    else:
                        self.assertIsNone(case.runtime.reference_payload())
                finally:
                    case.doCleanups()
