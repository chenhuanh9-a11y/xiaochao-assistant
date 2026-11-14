"""Current original-layout admission and lifetime over real tiny ZIP/files."""
import json
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

import numpy as np

from original_qt_resource_fixtures import OriginalNightmareBaseFixture, OriginalS1SourceFixture
from imported_resource_archive import save_archive
from imported_resource_packages import PackageView
from imported_resource_catalog import ResourceResolver, ResourceCatalogError
from client_imported_providers import SUPPORTED_CAPABILITIES, build_imported_engine_factories
from client_imported_activation import activate_context
from client_paths import RuntimePaths


class OriginalQtResourceFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OriginalNightmareBaseFixture.setUpClass()
        cls.addClassCleanup(OriginalNightmareBaseFixture.doClassCleanups)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='original-qt-flow-')
        self.addCleanup(temporary.cleanup)
        self.paths = RuntimePaths(Path(temporary.name), False)
        self.root = self.paths.imported_resource_root
        self.saved = save_archive(OriginalNightmareBaseFixture.archive, self.root)
        self.refs = {ref.component_id:ref for ref in self.saved.component_refs}
        self.resolver = ResourceResolver(self.root, supported_capabilities=SUPPORTED_CAPABILITIES)

    def activate(self):
        return activate_context(self.resolver, self.paths, self.refs['base'], self.refs['controls'],
            season='s0', mode='nightmare')

    def variant(self, version, edit, *, archive_path=None):
        with zipfile.ZipFile(archive_path or OriginalNightmareBaseFixture.archive) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        edit(members)
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = version
        for row in manifest['files']:
            raw = members[f"data/{row['componentId']}/{row['path']}"]
            row.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
        path = self.paths.installation_root/(version+'.zip')
        with zipfile.ZipFile(path,'x') as archive:
            for name,raw in members.items():
                archive.writestr(name,raw)
        saved = save_archive(path,self.root)
        return {ref.component_id:ref for ref in saved.component_refs}

    def test_no_fast_layer_payload_must_decode_before_catalog_activation(self):
        fixture = OriginalNightmareBaseFixture.fixture
        relative = fixture.rows[0]['resourcePath']
        with zipfile.ZipFile(fixture.output) as archive:
            self.assertFalse(any('/fast-structure/' in name for name in archive.namelist()))
        for name,mask in [('float-mask',np.ones((8,8),np.float32)),
                          ('nonbinary-mask',np.full((8,8),2,np.uint8)),
                          ('empty-mask',np.zeros((8,8),np.uint8)),
                          ('wrong-shape',np.ones((4,4),np.uint8))]:
            with self.subTest(name=name):
                def invalid(members):
                    key = 'data/base/'+relative
                    with np.load(BytesIO(members[key]),allow_pickle=False) as archive:
                        arrays = {field:archive[field].copy() for field in archive.files}
                    arrays['shared_mask'] = mask
                    target = BytesIO()
                    np.savez_compressed(target,**arrays)
                    members[key] = target.getvalue()
                refs = self.variant(name,invalid)
                with self.assertRaises((OSError,ValueError)):
                    activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                        season='s0',mode='nightmare')
                self.assertFalse((self.root/'catalog.json').exists())
                self.assertFalse((self.root/'transactions/pending-catalog.json').exists())
                self.assertIsNone(self.resolver.snapshot().resolve_context('s0','nightmare').base)
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        for row in fixture.rows:
            self.assertIsNotNone(engine.qt_shared_provider(row['profile'],row['mapId'],row['floor']))

    def test_consumed_declared_optional_schema_is_rejected_before_activation(self):
        from export_native_base_package import ENTRIES
        for name in ('entry-passports.json','side-door-anchors.json','side-entry-search.npz'):
            with self.subTest(name=name):
                def invalid(members):
                    key = 'data/base/'+ENTRIES+'/'+name
                    if name.endswith('.json'):
                        value = json.loads(members[key]); value['schemaVersion'] = 999
                        members[key] = json.dumps(value).encode()
                    else:
                        with np.load(BytesIO(members[key]),allow_pickle=False) as archive:
                            arrays = {field:archive[field].copy() for field in archive.files}
                        value = json.loads(str(arrays['metadata'].item())); value['schemaVersion'] = 999
                        arrays['metadata'] = np.asarray(json.dumps(value))
                        target = BytesIO(); np.savez_compressed(target,**arrays)
                        members[key] = target.getvalue()
                refs = self.variant('bad-optional-'+name.replace('.','-'),invalid)
                with self.assertRaises((OSError,ValueError)):
                    activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                        season='s0',mode='nightmare')
                self.assertFalse((self.root/'catalog.json').exists())

    def test_absent_and_legitimately_empty_optional_sources_remain_available(self):
        from export_native_base_package import ENTRIES
        names = ('entry-passports.json','side-door-anchors.json','side-entry-search.npz')
        for kind in ('missing','empty'):
            with self.subTest(kind=kind):
                def optional(members):
                    if kind == 'missing':
                        paths = {ENTRIES+'/'+name for name in names}
                        manifest = json.loads(members['manifest.json'])
                        manifest['files'] = [row for row in manifest['files']
                            if row['componentId'] != 'base' or row['path'] not in paths]
                        members['manifest.json'] = json.dumps(manifest).encode()
                        for path in paths:
                            members.pop('data/base/'+path)
                    else:
                        for name in names[:2]:
                            key='data/base/'+ENTRIES+'/'+name
                            value=json.loads(members[key]);value['entries']=[]
                            if name == 'entry-passports.json': value['coverage']=[]
                            members[key]=json.dumps(value).encode()
                        key='data/base/'+ENTRIES+'/'+names[2]
                        with np.load(BytesIO(members[key]),allow_pickle=False) as archive:
                            arrays={field:archive[field].copy() for field in archive.files}
                        arrays['metadata']=np.asarray(json.dumps(dict(schemaVersion=1,entries=[])))
                        target=BytesIO();np.savez_compressed(target,**arrays);members[key]=target.getvalue()
                refs=self.variant('optional-'+kind,optional)
                result=activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                    season='s0',mode='nightmare')
                self.assertEqual(result['status'],'catalog-activated')
                factories=build_imported_engine_factories(self.resolver,self.paths)
                try:
                    self.assertTrue(factories.get_engine('nightmare').qt_rank_ready)
                finally:
                    factories.invalidate()

    def test_session_recovers_a_published_engine_after_transient_lazy_read_failure(self):
        from PySide6.QtGui import QGuiApplication
        from imported_resource_reader import ComponentResourceReader
        from client_live_runtime import LiveRuntime
        from client_session import SessionService
        from client_region_capture import ClientCaptureContext, RegionCapture
        from test_s0_map_geometry_proof import s0_bright
        from export_native_base_package import ENTRIES
        def coverage(members):
            key='data/base/'+ENTRIES+'/entry-passports.json'
            value=json.loads(members[key]);value['coverage']=[dict(mapId='fixture-map',floor='1',complete=True)]
            members[key]=json.dumps(value).encode()
        refs=self.variant('runtime-retry',coverage)
        activate_context(self.resolver,self.paths,refs['base'],refs['controls'],season='s0',mode='nightmare')
        factories=build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        frame=s0_bright('nightmare',1)
        class Capture:
            enabled=False
            context=ClientCaptureContext(frame.shape[1::-1],(0,0,frame.shape[1],frame.shape[0]),96)
            snapshot=property(lambda s:dict(enabled=s.enabled,generation=1,evidenceKind='synthetic'))
            def enable(s):s.enabled=True;return True
            def disable(s):s.enabled=False
            def shutdown(s):s.disable()
            def is_current(s,context,**kwargs):return context==s.context
            def capture(s,cancelled=None):
                bounds=(0,0,frame.shape[1],frame.shape[0])
                return RegionCapture(frame.copy(),bounds,bounds,s.context,'synthetic')
        app=QGuiApplication.instance() or QGuiApplication([])
        runtime=LiveRuntime(engine_factories=factories,capture_provider=Capture(),evidence_kind='synthetic',
            allowed_read_root=self.paths.installation_root)
        session=SessionService(runtime)
        def close():
            session.shutdown();session._pool.waitForDone();app.processEvents();runtime.shutdown()
        self.addCleanup(close)
        def settle():
            deadline=time.monotonic()+30
            while session.busy and time.monotonic()<deadline:
                app.processEvents();time.sleep(.001)
            self.assertFalse(session.busy);session._pool.waitForDone();app.processEvents()
        def dispatch(action,payload=None):
            previous=session._last_receipt
            if payload is None:session.dispatch(action)
            else:session.dispatchWithPayload(action,payload)
            settle()
            self.assertIsNot(session._last_receipt,previous,'the command was not dispatched')
            receipt=session.snapshot['lastReceipt'];self.assertEqual(receipt['action'],action)
            return receipt
        session.reconfigure('nightmare','shared-structure','s0');settle()
        dispatch('inspect_engine_resources');self.assertTrue(dispatch('start_round')['success'])
        old=runtime._engine
        original=ComponentResourceReader.__call__
        injected=[]
        def once(reader,path):
            if reader is old.qt_native_base.reader and Path(path).name=='difficult-exit-icon.json' and not injected:
                injected.append(True);raise PermissionError('one actual lazy resource read')
            return original(reader,path)
        with patch.object(ComponentResourceReader,'__call__',once):
            failed=dispatch('capture_now')
        self.assertTrue(injected,'the actual Session never entered the lazy original resource consumer')
        self.assertFalse(failed['success']);self.assertTrue(failed.get('error'))
        self.assertFalse(runtime.snapshot()['roundActive'])
        self.assertFalse(runtime.snapshot()['savedFrameCapabilities']['nightmare']['available'])
        self.assertFalse(old.qt_rank_ready)
        self.assertNotIn('s0:nightmare',factories._engines)
        # No end_round or direct factory retry: normal Session preparation is
        # the next request, and its next capture executes the original ranker.
        self.assertTrue(dispatch('inspect_engine_resources')['success'])
        self.assertTrue(dispatch('start_round')['success'])
        self.assertIsNot(runtime._engine,old)
        resumed=dispatch('capture_now')
        self.assertFalse(resumed.get('error',False),resumed)
        self.assertIsNotNone(runtime._engine.side_entry_identity._exit_recognizer)
        self.assertTrue(factories.capabilities()['nightmare']['available'])
        # Diagnostic commands are unavailable after a resource failure. The
        # existing explicit preparation restores availability, and the next
        # rank must also retire the diagnostic adapter's own stale cache.
        dispatch('end_round');factories.invalidate();dispatch('inspect_engine_resources')
        old=factories._engines['s0:nightmare']
        from PIL import Image
        path=self.paths.installation_root/'diagnostic.png'
        Image.fromarray(frame[:,:,::-1]).save(path)
        payload=dict(path=str(path),floor='1')
        injected.clear()
        with patch.object(ComponentResourceReader,'__call__',once):
            failed=dispatch('rank_saved_frame',payload)
        self.assertTrue(injected)
        self.assertFalse(failed['success']);self.assertTrue(failed['offline'].get('error'))
        self.assertFalse(factories.capabilities()['nightmare']['available'])
        self.assertIs(runtime._offline._engines['s0:nightmare'],old)
        self.assertTrue(dispatch('inspect_engine_resources')['success'])
        self.assertIs(runtime._offline._engines['s0:nightmare'],old)
        resumed=dispatch('rank_saved_frame',payload)
        self.assertTrue(resumed['success'],resumed)
        replacement=runtime._offline._engines['s0:nightmare']
        self.assertIsNot(replacement,old)
        self.assertIs(replacement,factories._engines['s0:nightmare'])
        self.assertIsNotNone(replacement.side_entry_identity._exit_recognizer)

    def test_original_contexts_preserve_legitimate_optional_capability_gaps(self):
        from original_qt_resource_fixtures import OriginalS0DifficultSourceFixture
        from map_identity_engine import MapIdentityEngine
        from difficult_native_identity import NativeDifficultCandidateEngine
        sources = [('s0','nightmare',None),
            ('s0','difficult',OriginalS0DifficultSourceFixture(self.paths.installation_root/'s0-difficult')),
            ('s1','difficult',OriginalS1SourceFixture(self.paths.installation_root/'s1-difficult')),
            ('s1','nightmare',OriginalS1SourceFixture(self.paths.installation_root/'s1-nightmare',mode='nightmare'))]
        for season,mode,source in sources:
            with self.subTest(season=season,mode=mode):
                if source is None:
                    refs=self.refs
                else:
                    source.export()
                    saved=save_archive(source.output,self.root)
                    refs={ref.component_id:ref for ref in saved.component_refs}
                result=activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                    season=season,mode=mode)
                self.assertEqual(result['status'],'catalog-activated')
                factories=build_imported_engine_factories(self.resolver,self.paths)
                try:
                    engine=factories.get_engine(mode,season)
                    self.assertIs(type(engine),MapIdentityEngine if (season,mode)==('s0','nightmare')
                        else NativeDifficultCandidateEngine)
                    self.assertTrue(engine.qt_rank_ready)
                    self.assertTrue(engine.qt_resource_context_current())
                finally:
                    factories.invalidate()

    def test_s1_identity_season_must_match_the_requested_context(self):
        for declared in ('s0',None):
            with self.subTest(declared=declared):
                source = OriginalS1SourceFixture(self.paths.installation_root/('s1-season-'+str(declared)),
                    resource_version='season-'+str(declared))
                source.export()

                def invalid(members):
                    key = 'data/base/references/map-pack.json'
                    identity = json.loads(members[key])
                    if declared is None:
                        identity.pop('season',None)
                    else:
                        identity['season'] = declared
                    members[key] = json.dumps(identity).encode()

                # The producer now rejects invalid semantics before publication.
                # Preserve activation coverage using an externally changed ZIP
                # with correct manifest hashes and actual archive admission.
                refs = self.variant('bad-season-'+str(declared),invalid,archive_path=source.output)
                with self.assertRaises((OSError,ValueError)):
                    activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                        season='s1',mode='difficult')
                self.assertFalse((self.root/'catalog.json').exists())
                self.assertFalse((self.root/'transactions/pending-catalog.json').exists())

    def test_activation_consumes_stored_preparation_without_client_recomputation(self):
        source = OriginalS1SourceFixture(self.paths.installation_root/'wrong-fast')
        mid,floor = next(iter(source.prepared_resources))
        source.export()

        def invalid(members):
            key = 'data/base/'+source.prepared_resources[mid,floor]
            with np.load(BytesIO(members[key]),allow_pickle=False) as archive:
                arrays = {field:archive[field].copy() for field in archive.files}
            arrays['distance'][:] = 12345
            target = BytesIO()
            np.savez_compressed(target,**arrays)
            members[key] = target.getvalue()

        refs = self.variant('stored-fast',invalid,archive_path=source.output)
        # Numeric source equivalence is checked by the producer. The client
        # checks the declared bytes/schema/bindings and consumes stored arrays;
        # hardware-dependent rederivation must not decide package availability.
        with patch('fast_structure_alignment.prepare', side_effect=AssertionError('client rederivation')):
            result = activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                season='s1',mode='difficult')
        self.assertEqual(result['status'], 'catalog-activated')
        factories = build_imported_engine_factories(self.resolver,self.paths)
        try:
            engine = factories.get_engine('difficult',season='s1')
            reference = engine.qt_shared_provider('shared-structure',mid,floor)
            prepared = engine.qt_fast_structure_source.read(reference,strict=True)
            self.assertTrue(np.all(prepared.distance == 12345))
        finally:
            factories.invalidate()
        self.assertFalse((self.root/'transactions/pending-catalog.json').exists())

    def test_s0_control_rounding_uses_portable_comparison_without_generation(self):
        def rounded(members):
            key = 'data/controls/templates.npy'
            vectors = np.load(BytesIO(members[key]),allow_pickle=False).copy()
            vectors[0,0] = np.nextafter(vectors[0,0],np.float32(np.inf))
            target = BytesIO()
            np.save(target,vectors,allow_pickle=False)
            members[key] = target.getvalue()

        refs = self.variant('rounded-controls',rounded)
        with patch('s0_floor_templates.make_template', side_effect=AssertionError('client generation')):
            result = activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                season='s0',mode='nightmare')
        self.assertEqual(result['status'],'catalog-activated')

    def test_invalid_s0_template_data_still_fails_before_activation(self):
        for case in ('nonfinite','wrong-dtype'):
            with self.subTest(case=case):
                def invalid(members):
                    key = 'data/controls/templates.npy'
                    vectors = np.load(BytesIO(members[key]),allow_pickle=False).copy()
                    if case == 'nonfinite':
                        vectors[0,0] = np.nan
                    else:
                        vectors = vectors.astype(np.float64)
                    target = BytesIO()
                    np.save(target,vectors,allow_pickle=False)
                    members[key] = target.getvalue()

                refs = self.variant('invalid-controls-'+case,invalid)
                with self.assertRaises((OSError,ValueError)):
                    activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                        season='s0',mode='nightmare')
                self.assertFalse((self.root/'catalog.json').exists())

    def test_entry_complexity_and_nested_coordinates_are_rejected_at_admission(self):
        from export_native_base_package import ENTRIES
        def main_chains(members):
            key = 'data/base/'+ENTRIES+'/entries.json'
            value = json.loads(members[key])
            value['entries'][0]['chains'] *= 513
            members[key] = json.dumps(value).encode()
        def side_turns(members):
            key = 'data/base/'+ENTRIES+'/side-entries.json'
            value = json.loads(members[key])
            value['entries'][0]['turns'] *= 257
            members[key] = json.dumps(value).encode()
        def nested_walls(members):
            key = 'data/base/'+ENTRIES+'/entry-passports.json'
            value = json.loads(members[key])
            value['entries'] = [dict(center=[0,0],localInstances=[],knownWallLines=[
                dict(axis=[0]*2048,sign=[1]*2048,normal=[0]*2048,lo=[0]*2048,hi=[1]*2048)])]
            members[key] = json.dumps(value).encode()
        def search(members,records=False):
            key = 'data/base/'+ENTRIES+'/side-entry-search.npz'
            with np.load(BytesIO(members[key]),allow_pickle=False) as archive:
                arrays = {name:archive[name].copy() for name in archive.files}
            metadata = json.loads(str(arrays['metadata'].item()))
            if records:
                metadata['entries'] *= 257
                arrays['metadata'] = np.asarray(json.dumps(metadata))
            else:
                arrays[metadata['entries'][0]['key']+'_pairs'] = np.zeros((1025,2),np.int64)
            target = BytesIO()
            np.savez_compressed(target,**arrays)
            members[key] = target.getvalue()
        for name,edit in [('main-chains',main_chains),('side-turns',side_turns),
                          ('nested-walls',nested_walls),('search-pairs',search),
                          ('search-records',lambda members:search(members,True))]:
            with self.subTest(name=name):
                refs = self.variant(name,edit)
                with self.assertRaises((OSError,ValueError)):
                    activate_context(self.resolver,self.paths,refs['base'],refs['controls'],
                        season='s0',mode='nightmare')
                self.assertFalse((self.root/'catalog.json').exists())
                self.assertFalse((self.root/'transactions/pending-catalog.json').exists())

    def test_actual_original_factory_never_falls_back_to_unguarded_resource_loaders(self):
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        with patch('author_structure_resources.AuthorStructureResourceStore',
                   side_effect=AssertionError('unguarded legacy store')), \
             patch('nightmare_candidate.imread_unicode',side_effect=AssertionError('bare image read')), \
             patch('marked_map_retrieval.MarkedMapRetriever',side_effect=AssertionError('poster loader')):
            engine = factories.get_engine('nightmare')
            for row in OriginalNightmareBaseFixture.fixture.rows:
                self.assertIsNotNone(engine.qt_shared_provider(row['profile'],row['mapId'],row['floor']))
            frame = np.zeros((720,1280,3),np.uint8)
            engine.rank(frame)
            engine.side_entry_identity._entry_observations(frame)

    def test_original_display_keeps_the_existing_managed_owner_binding(self):
        from client_route_preferences import prepare_display_layer, shared_structure_display_current
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        provider = engine.qt_shared_provider
        self.assertIs(engine.qt_assert_resource_current,provider._check_current)
        self.assertIsNotNone(provider._managed_display_context)
        layer = prepare_display_layer(engine,'fixture-map','1','shared-structure')
        self.assertTrue(shared_structure_display_current(engine,'fixture-map','1',layer))
        state = engine.qt_fast_structure_source._read_state()
        self.assertEqual(engine.qt_fast_structure_source._read_state(),state)

    def test_original_rank_and_display_share_live_read_operation_and_verify_before_return(self):
        from client_route_preferences import prepare_display_layer
        from client_live_runtime import _AlignmentHost
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        frame = np.zeros((720,1280,3),np.uint8)
        original = PackageView._read_manifest_bytes
        calls = []
        def read(view, root):
            calls.append(view.component_ref)
            return original(view, root)
        with patch.object(PackageView,'_read_manifest_bytes',read):
            with engine.qt_resource_read_scope() as operation:
                admitted = len(calls)
                engine.rank(frame, floor='1')
                prepare_display_layer(engine,'fixture-map','1','shared-structure')
                _AlignmentHost(engine).fit(frame,'fixture-map','1',lambda:False)
                # The real donor rank, display and Host all use their normal
                # provider APIs; nested metadata does not reopen manifests.
                self.assertEqual(len(calls),admitted)
                engine.qt_verify_resource_read_scope()
                self.assertGreater(len(calls),admitted)
                operation.verify()
        with self.assertRaisesRegex(ValueError,'resource-context-superseded'):
            with engine.qt_resource_read_scope():
                factories.invalidate()

    def test_live_read_operation_rejects_replaced_engine_resource_consumer(self):
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        for field in ('qt_shared_provider','qt_fast_structure_source','qt_adapters','qt_optional_displays'):
            original = getattr(engine,field)
            try:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    with engine.qt_resource_read_scope():
                        setattr(engine,field,object())
                        engine.qt_verify_resource_read_scope()
            finally:
                setattr(engine,field,original)

    def test_standalone_original_host_owns_read_scope_and_rejects_late_payload_damage(self):
        from client_live_runtime import _AlignmentHost
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        frame = np.zeros((720,1280,3),np.uint8)
        original = PackageView._read_manifest_bytes
        calls = []
        def metadata(view, root):
            calls.append(view.component_ref)
            return original(view,root)
        with patch.object(PackageView,'_read_manifest_bytes',metadata):
            _AlignmentHost(engine).fit(frame,'fixture-map','1',lambda:False)
        self.assertLessEqual(len(calls),6)
        self.assertGreaterEqual(len(calls),4)
        reader = engine.qt_native_base.reader
        target = reader.view.path(engine.qt_native_base.interfaces['identityPack'])
        raw = target.read_bytes()
        fit_owned = _AlignmentHost._fit_owned
        def change_after_math(host,*args,**kwargs):
            result = fit_owned(host,*args,**kwargs)
            # Only the resource-update timing is substituted. The real Host
            # mathematics ran, then an actually consumed file changed before
            # its standalone fit could return to a consumer.
            reader(target)
            target.write_bytes(b'X' * len(raw))
            return result
        try:
            with patch.object(_AlignmentHost,'_fit_owned',change_after_math):
                with self.assertRaisesRegex(ValueError,'file-integrity-mismatch'):
                    _AlignmentHost(engine).fit(frame,'fixture-map','1',lambda:False)
        finally:
            target.write_bytes(raw)

    def test_live_operation_checks_new_payload_and_rejects_changes_before_commit(self):
        self.activate()
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        reader = engine.qt_native_base.reader
        relative = engine.qt_native_base.interfaces['identityPack']
        target = reader.view.path(relative)
        original = target.read_bytes()
        with self.assertRaisesRegex(ValueError,'file-integrity-mismatch'):
            with engine.qt_resource_read_scope():
                reader(target)
                target.write_bytes(b'X' * len(original))
                engine.qt_verify_resource_read_scope()
        target.write_bytes(original)
        with engine.qt_resource_read_scope():
            self.assertEqual(reader(target),original)
        # A different catalog generation cannot validate old scope/model data.
        with self.assertRaisesRegex(ValueError,'resource-catalog-superseded'):
            with engine.qt_resource_read_scope():
                document = json.loads((self.root/'catalog.json').read_bytes())
                document['generation'] = str(uuid4())
                (self.root/'catalog.json').write_text(json.dumps(document),encoding='utf8')
                engine.qt_verify_resource_read_scope()

    def test_original_prepared_cache_uses_the_shared_bound_and_owned_ram(self):
        from imported_resource_reader import ComponentResourceReader
        from imported_fast_structure import decode_prepared
        from fast_structure_alignment import _prepared, _prepared_lock
        source_fixture = OriginalS1SourceFixture(self.paths.installation_root/'prepared-cache')
        source_fixture.export()
        saved = save_archive(source_fixture.output,self.root)
        refs = {ref.component_id:ref for ref in saved.component_refs}
        activate_context(self.resolver,self.paths,refs['base'],refs['controls'],season='s1',mode='difficult')
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('difficult',season='s1')
        source,provider = engine.qt_fast_structure_source,engine.qt_shared_provider
        self.assertIs(source._prepared,_prepared)
        self.assertIs(source._prepared_lock,_prepared_lock)
        key = next(iter(source.entries))
        reference = provider(key[0],key[1],key[3])
        original_read = ComponentResourceReader.__call__
        with patch('imported_fast_structure.decode_prepared',wraps=decode_prepared) as decode, \
             patch.object(ComponentResourceReader,'__call__',autospec=True,side_effect=original_read) as read:
            first = source.read(reference)
            cold_reads = read.call_count
            self.assertIs(source.read(reference),first)
            self.assertEqual(decode.call_count,1)
            self.assertEqual(read.call_count,cold_reads)
            with self.assertRaises(ValueError):
                first.distance.flags.writeable = True
            def denied(reader,path):
                if reader is source.reader and Path(path).name.endswith('-fast.npz'):
                    raise PermissionError('explicit integrity read denied')
                return original_read(reader,path)
            with patch.object(ComponentResourceReader,'__call__',denied):
                with self.assertRaises(PermissionError):
                    source.read(reference,strict=True)
            self.assertFalse(any(k[:2] == ('imported-original',id(source)) for k in _prepared))
            rebuilt = source.read(reference)
            self.assertIsNot(rebuilt,first)
            reference.metadata['cacheMetadataProbe'] = 'changed'
            try:
                with self.assertRaisesRegex(ValueError,'metadata-mismatch'):
                    source.read(reference)
            finally:
                reference.metadata.pop('cacheMetadataProbe')
            source.read(reference)
            for other in list(source.entries)[1:6]:
                source.read(provider(other[0],other[1],other[3]))
            self.assertLessEqual(len(_prepared),4)
        # Resource invalidation retires this actual managed owner, while an
        # already held immutable result remains safe for its existing caller.
        document = json.loads((self.root/'catalog.json').read_bytes())
        document['generation'] = str(uuid4())
        (self.root/'catalog.json').write_text(json.dumps(document),encoding='utf8')
        factories.invalidate()
        with self.assertRaises((OSError,ValueError)):
            source.read(reference)
        self.assertFalse(any(cache_key[:2] == ('imported-original',id(source)) for cache_key in _prepared))
        self.assertFalse(first.distance.flags.writeable)

    def test_export_retains_original_structure_bytes_and_separates_author_pixels(self):
        fixture = OriginalNightmareBaseFixture.fixture
        with zipfile.ZipFile(fixture.output) as archive:
            names = archive.namelist()
            self.assertNotIn('data/base/assets/author.png', names)
            for path in fixture.rows:
                relative = path['resourcePath']
                self.assertEqual(archive.read('data/base/' + relative), (fixture.source/relative).read_bytes())
        self.assertFalse((self.root/'catalog.json').exists())

    def test_api_and_cli_default_to_the_current_original_layout(self):
        import export_native_base_package as producer
        fixture = OriginalNightmareBaseFixture.fixture
        output = self.paths.installation_root/'default-api.xingtu-base'
        producer.export_package(fixture.source,output,package_id='default-api',resource_version='v1')
        command = [sys.executable,'-B',str(Path(producer.__file__)),
            '--source-root',str(fixture.source),'--output',str(output.with_name('default-cli.xingtu-base')),
            '--package-id','default-cli','--resource-version','v1']
        environment = dict(os.environ)
        environment.pop('PYTHONPATH',None)
        completed = subprocess.run(command,capture_output=True,text=True,timeout=30,env=environment)
        self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr)
        for path in (output,output.with_name('default-cli.xingtu-base')):
            with zipfile.ZipFile(path) as archive:
                context = json.loads(archive.read('data/base/resource-context.json'))
            self.assertEqual(context['schema'],'xingtu-original-qt-base-v1')
        retired = self.paths.installation_root/'retired.xingtu-base'
        with self.assertRaisesRegex(ValueError,'historical-normalized-base-export-retired'):
            producer.export_package(fixture.source,retired,package_id='retired',resource_version='v1',original_qt=False)
        self.assertFalse(retired.exists())

    def test_real_factory_is_original_ranker_and_catalog_replacement_retires_cached_owners(self):
        from map_identity_engine import MapIdentityEngine
        self.activate()
        factories = build_imported_engine_factories(self.resolver, self.paths)
        self.addCleanup(factories.invalidate)
        engine = factories.get_engine('nightmare')
        self.assertIs(type(engine), MapIdentityEngine)
        reference = engine.qt_shared_provider('zhan10-speed', 'fixture-map', '1')
        self.assertIsNotNone(reference)
        # A real catalog replacement expires the saved snapshot even when its
        # selection text happens to match. No rank/pose answer is supplied.
        document = json.loads((self.root/'catalog.json').read_bytes())
        document['generation'] = str(uuid4())
        (self.root/'catalog.json').write_text(json.dumps(document), encoding='utf8')
        with self.assertRaises((OSError, ValueError)):
            engine.rank(np.zeros((720,1280,3),np.uint8))
        with self.assertRaises((OSError, ValueError)):
            engine.qt_shared_provider('zhan10-speed', 'fixture-map', '1')
        replacement = factories.get_engine('nightmare')
        self.assertIsNot(replacement, engine)
        self.assertIsNotNone(replacement.qt_shared_provider('zhan10-speed', 'fixture-map', '1'))

    def test_declared_raw_path_cannot_escape_component(self):
        from imported_original_base import require_original_base
        view = PackageView(self.root, self.refs['base'])
        reader, context = require_original_base(view, season='s0', mode='nightmare')
        with self.assertRaises((OSError, ValueError)):
            reader(view.root/'../../outside.json')

    def test_saved_old_typed_selection_is_unavailable_without_an_algorithm_fallback(self):
        import hashlib
        from imported_resource_activation import activate_catalog_locked
        from imported_resource_selection import inspect_selection
        from client_imported_admission import available_base_contexts
        with zipfile.ZipFile(OriginalNightmareBaseFixture.archive) as archive:
            members = {name:archive.read(name) for name in archive.namelist()}
        manifest = json.loads(members['manifest.json'])
        manifest['resourceVersion'] = 'old-typed-version'
        context = json.loads(members['data/base/resource-context.json'])
        context['schema'] = 'xingtu-native-base-v1'
        raw = json.dumps(context).encode()
        members['data/base/resource-context.json'] = raw
        row = next(row for row in manifest['files'] if row['componentId'] == 'base'
                   and row['path'] == 'resource-context.json')
        row.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        members['manifest.json'] = json.dumps(manifest).encode()
        path = self.paths.installation_root/'old-typed.zip'
        with zipfile.ZipFile(path, 'x') as archive:
            for name,raw in members.items():
                archive.writestr(name,raw)
        saved = save_archive(path,self.root)
        refs = {ref.component_id:ref for ref in saved.component_refs}
        wire = lambda ref:dict(packageId=ref.package_id,resourceVersion=ref.resource_version,
            componentId=ref.component_id,manifestSha256=ref.manifest_sha256)
        document = dict(schema='xingtu-resource-catalog-v1',generation=str(uuid4()),displays=[],
            contexts={'s0:nightmare':dict(recognitionBase=wire(refs['base']),
                controls=wire(refs['controls']),reviewedGroups=None)})
        # Model an existing saved catalog. Ordinary selection validation proves
        # its identity/files; the new provider must decline its old data schema.
        activate_catalog_locked(self.root,document,expected_catalog_stamp=None,
            validate=lambda d:inspect_selection(self.root,d,supported_capabilities=SUPPORTED_CAPABILITIES))
        self.assertEqual(available_base_contexts(self.resolver.snapshot()), ())
        factories = build_imported_engine_factories(self.resolver,self.paths)
        self.addCleanup(factories.invalidate)
        from client_providers import ProviderUnavailable
        with self.assertRaisesRegex(ProviderUnavailable,'original-qt-base-required'):
            factories.get_engine('nightmare')


if __name__ == '__main__':
    unittest.main()
