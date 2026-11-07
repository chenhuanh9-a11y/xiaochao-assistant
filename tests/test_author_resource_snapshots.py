"""Synthetic author displays, real import/read/recovery; not game/GUI acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import test_generic_author_package as fixtures
import author_resource_snapshots as snapshots
import author_package_management as management
import generic_author_base as base
import generic_author_package as generic
import map_package_recovery_plan as recovery
import star_map_import as imp
from s1_author_displays import InstalledAuthorPackage
from resource_transaction import resource_write_lock, assert_resources_ready


class Crash(BaseException):
    pass


class MetadataObservationTests(unittest.TestCase):
    """Portable API-semantics models plus a native temp-file diagnostic.

    Models do not claim Windows execution. Windows CI runs the same production
    reader and prints the actual stat/fstat fields for its Python/filesystem.
    """
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'metadata.json'
        self.path.write_bytes(b'{"value":1}')

    @staticmethod
    def changed(info, **fields):
        from types import SimpleNamespace
        values = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        return SimpleNamespace(**(values | fields))

    def test_windows_ctime_domains_do_not_reject_unchanged_file(self):
        # CPython 3.13 fileutils.c returns ChangeTime for fstat; posixmodule.c
        # win32_xstat overwrites ctime with birthtime for pathname stat.
        real_stat = Path.stat
        def creation_time(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == self.path:
                return self.changed(info, st_ctime_ns=1)
            return info
        with patch.object(Path, 'stat', creation_time):
            data, row = snapshots._metadata(self.path)
        self.assertEqual(data, b'{"value":1}')
        self.assertEqual(row['identity'], snapshots.transaction._identity(real_stat(self.path)))

    def test_each_handle_change_field_still_rejects_during_read(self):
        real_fstat = os.fstat
        for changed_call in (2, 3):  # read-end handle and reopened pathname handle
            for field in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'):
                with self.subTest(observation=changed_call, field=field):
                    calls = 0
                    def changed_observation(fd):
                        nonlocal calls
                        calls += 1
                        info = real_fstat(fd)
                        if calls == changed_call:
                            return self.changed(info, **{field: getattr(info, field) + 1})
                        return info
                    with patch.object(os, 'fstat', changed_observation):
                        with self.assertRaisesRegex(ValueError, 'metadata-changed'):
                            snapshots._metadata(self.path)

    def test_same_bytes_foreign_path_replacement_rejected(self):
        foreign = self.path.with_name('foreign.json')
        foreign.write_bytes(self.path.read_bytes())
        original = self.path.stat()
        os.utime(foreign, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertNotEqual(snapshots.transaction._identity(original),
                            snapshots.transaction._identity(foreign.stat()))
        real_no_links = snapshots.files._no_links
        calls = 0
        def replace_after_read(path):
            nonlocal calls
            if path == self.path:
                calls += 1
                if calls == 2:
                    os.replace(foreign, path)
            return real_no_links(path)
        with patch.object(snapshots.files, '_no_links', replace_after_read):
            with self.assertRaisesRegex(ValueError, 'metadata-changed'):
                snapshots._metadata(self.path)
        self.assertEqual(self.path.read_bytes(), b'{"value":1}')

    def test_real_same_length_modification_during_read_rejected(self):
        real_fstat = os.fstat
        calls = 0
        def modify_after_read(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                original = self.path.stat()
                self.path.write_bytes(b'{"value":2}')
                os.utime(self.path, ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000))
            return real_fstat(fd)
        with patch.object(os, 'fstat', modify_after_read):
            with self.assertRaisesRegex(ValueError, 'metadata-changed'):
                snapshots._metadata(self.path)

    @unittest.skipUnless(os.name == 'nt', 'native Windows stat/fstat diagnostic')
    def test_native_windows_observation(self):
        # Owned temporary bytes only; no installed data or OS settings touched.
        def fields(info):
            return {key: getattr(info, key, None) for key in
                    ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_birthtime_ns')}
        import time
        time.sleep(0.02)  # Separate NTFS creation and last-change timestamps.
        self.path.write_bytes(b'{"value":2}')
        with self.path.open('rb') as handle:
            before = fields(os.fstat(handle.fileno()))
            handle.read()
            after = fields(os.fstat(handle.fileno()))
        pathname = fields(self.path.stat())
        with self.path.open('rb') as handle:
            reopened = fields(os.fstat(handle.fileno()))
        import sys
        print('WINDOWS_METADATA_OBSERVATIONS ' + json.dumps(dict(
            python=sys.version, before=before, after=after, pathname=pathname, reopened=reopened)), flush=True)
        self.assertEqual(before, after)
        self.assertEqual(after, reopened)
        self.assertEqual(snapshots._metadata(self.path)[0], b'{"value":2}')


class AuthorSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GenericPackageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.profile = self.fixture.root, self.fixture.profile

    def package(self, **kwargs):
        return self.fixture.package(**kwargs)

    def inspect(self, archive):
        return imp.inspect_package(archive, self.root, versioned=True)

    def install(self, archive, **kwargs):
        return imp.install_author_package_live(archive, self.root, application_idle=True, versioned=True, **kwargs)

    def view(self):
        return snapshots.SnapshotPackage(self.root, self.profile)

    def pending(self):
        return self.root / snapshots.PENDING

    def multi(self, *, invalid_second=False):
        """Add a second real synthetic provider layer, with its own reference."""
        root = self.root / '_internal'
        paths = ['references/nightmare-map-pack.json', 'references/nightmare-shared-structure-index.json',
                 'references/versions.json', 'references/nightmare-canonical-coordinate-pack.json']
        ref = 'references/entries/map-two-B1.npz'
        original = root/'references/entries/map-B1.npz'
        with np.load(original) as source:
            meta = json.loads(str(source['metadata']))
            meta['binding']['mapId'] = 'map-two'
            np.savez(root/ref, metadata=json.dumps(meta), shared_mask=source['shared_mask'])
        for name in paths:
            path = root/name
            value = json.loads(path.read_bytes())
            if name.endswith('nightmare-map-pack.json'):
                item = copy.deepcopy(value['maps'][0]); item['id']='map-two'; item['layers']['B1']=ref
                value['maps'].append(item)
            elif name.endswith('nightmare-shared-structure-index.json'):
                item=copy.deepcopy(value['entries'][0]);item.update(mapId='map-two',resourcePath=ref)
                value['entries'].append(item)
            elif name.endswith('versions.json'):
                item=copy.deepcopy(value['entries'][0]);item.update(mapId='map-two',typedReference=ref)
                item['sharedLookup'].update(mapId='map-two',resourcePath=ref);value['entries'].append(item)
            else:
                item=copy.deepcopy(value['maps']['map']);item['floors']['B1']['referenceResource']=ref
                value['maps']['map-two']=item
            path.write_bytes(fixtures.encoded(value))
        self.fixture.contract=base.resolve_layer(self.root,'s0','nightmare','map','B1')
        self.fixture.deps=[fixtures.record(p,(self.root/p).read_bytes()) for p in self.fixture.contract['dependencies']]
        archive=self.package()
        with zipfile.ZipFile(archive) as source:
            payload={name:source.read(name) for name in source.namelist()}
        meta=json.loads(payload['map-package.json'])
        binding_name='data/'+generic.bindings_path(self.profile)
        doc=json.loads(payload[binding_name]);first=doc['layers'][0]
        contract=base.resolve_layer(self.root,'s0','nightmare','map-two','B1')
        layer=copy.deepcopy(first);layer.update({k:v for k,v in contract.items() if k!='dependencies'})
        layer['mapId']='map-two';layer['displayPath']=first['displayPath'].replace('/map/','/map-two/')
        layer['referenceSha256']=imp._sha(self.root/layer['referenceResource'])
        if invalid_second:layer['structureVersionKey']='other-layout'
        doc['layers'].append(layer)
        payload['data/'+layer['displayPath']]=payload['data/'+first['displayPath']]
        payload[binding_name]=fixtures.encoded(doc)
        meta['files']=[fixtures.record(k[5:],v) for k,v in payload.items() if k.startswith('data/')]
        deps={r['path']:r for r in self.fixture.deps}
        deps.update({p:fixtures.record(p,(self.root/p).read_bytes()) for p in contract['dependencies']})
        meta['requiresFiles']=list(deps.values());payload['map-package.json']=fixtures.encoded(meta)
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as sink:
            for key,value in payload.items():sink.writestr(key,value)
        return archive

    def interrupt_publish(self, archive):
        with patch.object(snapshots,'_commit_outcome',side_effect=Crash()):
            with self.assertRaises(Crash):self.install(archive)
        self.assertTrue(self.pending().exists())

    def recover(self):
        with resource_write_lock(self.root):
            plan=recovery.inspect_locked(self.root)
            return recovery.execute_locked(self.root,plan,'recover')

    def test_install_pin_duplicate_and_runtime(self):
        archive=self.package();result=self.install(archive);view=self.view()
        self.assertEqual(result['storageFormat'],snapshots.SNAPSHOT_SCHEMA)
        self.assertFalse((self.root/generic.bindings_path(self.profile)).exists())
        self.assertEqual(len(InstalledAuthorPackage(self.root/'_internal',self.profile).document['layers']),1)
        active=(self.root/snapshots.activation_path(self.profile)).read_bytes()
        self.assertTrue(self.install(archive)['receiptPreserved'])
        self.assertEqual(active,(self.root/snapshots.activation_path(self.profile)).read_bytes())
        self.assertEqual(imp.verified_installed_package(self.root,self.profile)['status'],'installed')

    def test_same_id_format_change_keeps_previous_bytes(self):
        self.install(self.package());old=self.view();previous={p:p.read_bytes() for p in old.directory.rglob('*') if p.is_file()}
        archive=self.package(fmt='jpeg');plan=self.inspect(archive)
        self.assertEqual(plan['formatChangedLayers'],[['s0','nightmare','map','B1']])
        self.install(archive)
        self.assertTrue(self.view().document()['layers'][0]['displayPath'].endswith('.jpeg'))
        for path,data in previous.items():self.assertEqual(path.read_bytes(),data)
        with self.assertRaisesRegex(ValueError,'activation-changed'):old.read(generic.bindings_path(self.profile))

    def test_same_id_layer_removal_keeps_payload(self):
        self.install(self.multi());old=self.view();archive=self.package();plan=self.inspect(archive)
        self.assertEqual(plan['removedLayers'],[['s0','nightmare','map-two','B1']])
        self.install(archive);self.assertEqual(len(self.view().document()['layers']),1)
        self.assertTrue(old.directory.exists())

    def test_partial_binding_failure_keeps_healthy_layer(self):
        archive=self.multi(invalid_second=True);plan=self.inspect(archive)
        self.assertEqual(len(plan['inactiveLayers']),1);self.assertTrue(plan['requiresConfirmation'])
        self.install(archive);view=self.view();doc=view.document()
        self.assertEqual([r['mapId'] for r in doc['layers']],['map'])
        self.assertFalse(doc['inactiveLayers'][0]['bindingVerified'])
        self.assertEqual(imp.verified_installed_package(self.root,self.profile)['status'],'partially-compatible')

    def test_missing_base_not_authority_and_rechecks_when_restored(self):
        archive=self.multi();ref=self.root/'_internal/references/entries/map-two-B1.npz';data=ref.read_bytes();ref.unlink()
        self.install(archive);self.assertEqual(len(self.view().document()['layers']),1)
        ref.write_bytes(data);self.assertEqual(len(self.view().document()['layers']),2)

    def test_damaged_one_installed_image_does_not_hide_healthy_layer(self):
        self.install(self.multi());view=self.view();doc=view.document();path=view.path(doc['layers'][1]['displayPath']);path.write_bytes(b'edited')
        self.assertEqual(len(self.view().document()['layers']),1)
        self.assertEqual(self.view().document()['inactiveLayers'][0]['reason'],'display-damaged')
        self.install(self.package());self.assertEqual(path.read_bytes(),b'edited')

    def test_damaged_archive_rejected_whole_and_no_active_change(self):
        self.install(self.package());view=self.view()
        archive=self.package(mutate=lambda d:d['layers'][0].update(displaySha256='a'*64))
        with self.assertRaises(ValueError):self.install(archive)
        view.assert_active();self.assertFalse(self.pending().exists())

    def test_legacy_migration_preserves_edited_images(self):
        archive=self.package();imp.install_author_package_live(archive,self.root,application_idle=True)
        image=self.root/generic.image_path(self.profile,'s0','nightmare','map','B1','png');image.write_bytes(b'edited')
        original_binding=(self.root/generic.bindings_path(self.profile)).read_bytes()
        plan=self.inspect(self.package(fmt='jpeg'));self.assertTrue(plan['legacyFilesPreserved'])
        self.install(self.package(fmt='jpeg'))
        self.assertEqual(image.read_bytes(),b'edited');self.assertEqual((self.root/generic.bindings_path(self.profile)).read_bytes(),original_binding)
        self.assertFalse((self.root/f'work/map-packages/{self.profile}.json').exists())
        self.assertTrue(list((self.root/snapshots.STORE/'transactions').glob('*/old-0')))

    def test_malformed_legacy_receipt_is_not_moved(self):
        archive=self.package()
        with zipfile.ZipFile(archive) as z:data=z.read('map-package.json')
        receipt=self.root/f'work/map-packages/{self.profile}.json';receipt.parent.mkdir(parents=True);receipt.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'invalid-installed-package-receipt'):self.install(archive)
        self.assertEqual(receipt.read_bytes(),data);self.assertFalse(self.pending().exists())

    def test_flat_mutation_cannot_shadow_snapshot(self):
        archive=self.package();self.install(archive)
        with self.assertRaisesRegex(ValueError,'use-versioned-import'):
            imp.install_author_package_live(archive,self.root,application_idle=True)

    def test_deactivate_tombstone_retains_damaged_and_unknown_bytes(self):
        self.install(self.package());view=self.view();foreign=view.directory/'user-route.json';foreign.write_text('user data')
        plan=management.inspect_author_package_removal(self.root,self.profile)
        result=management.remove_author_package_live(self.root,self.profile,application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'],expected_plan_sha256=plan['planSha256'])
        self.assertTrue(result['retainedPreviousVersions']);self.assertEqual(foreign.read_text(),'user data')
        self.assertEqual(imp.verified_installed_package(self.root,self.profile)['status'],'not-installed')
        self.install(self.package(fmt='jpeg'));self.assertEqual(imp.verified_installed_package(self.root,self.profile)['status'],'installed')

    def test_new_import_interruption_recovers_without_payload_deletion(self):
        self.interrupt_publish(self.package())
        with self.assertRaisesRegex(ValueError,'author-snapshot-recovery-required'):assert_resources_ready(self.root)
        self.assertEqual(self.recover()['status'],'author-snapshot-rolled-back')
        self.assertFalse((self.root/snapshots.activation_path(self.profile)).exists());self.assertFalse(self.pending().exists())
        self.assertTrue(list((self.root/snapshots.STORE/'versions').rglob('manifest.json')))

    def test_update_interruption_restores_previous_pin(self):
        self.install(self.package());old=self.view();self.interrupt_publish(self.package(fmt='jpeg'))
        self.recover();old.assert_active();self.assertEqual(len(old.document()['layers']),1)

    def test_copied_foreign_pending_blocks_before_first_rollback_write(self):
        self.interrupt_publish(self.package());active=self.root/snapshots.activation_path(self.profile);before=active.read_bytes()
        pending=self.pending();data=pending.read_bytes();pending.unlink();pending.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'pending-changed'):self.recover()
        self.assertEqual(active.read_bytes(),before);self.assertEqual(pending.read_bytes(),data)

    def test_foreign_activation_blocks_rollback_and_preserves_backups(self):
        self.install(self.package());self.interrupt_publish(self.package(fmt='jpeg'));active=self.root/snapshots.activation_path(self.profile)
        active.unlink();active.write_bytes(b'foreign')
        with self.assertRaisesRegex(ValueError,'rollback-blocked'):self.recover()
        self.assertEqual(active.read_bytes(),b'foreign');self.assertTrue(self.pending().exists())

    def test_staged_activation_replacement_never_publishes_foreign(self):
        real=imp._check_cancelled
        def check(cancelled=None):
            parent=self.root/snapshots.STORE/'transactions'
            for pin in parent.glob('*/new-activation.json') if parent.exists() else ():
                if self.pending().exists():pin.unlink();pin.write_bytes(b'foreign')
            return real(cancelled)
        with patch.object(imp,'_check_cancelled',side_effect=check):
            with self.assertRaisesRegex(ValueError,'staged-activation-changed'):self.install(self.package())
        self.assertFalse((self.root/snapshots.activation_path(self.profile)).exists());self.assertFalse(self.pending().exists())

    def test_same_bytes_foreign_new_pin_witness_never_published(self):
        real = imp._check_cancelled
        replaced = False
        def check(cancelled=None):
            nonlocal replaced
            parent = self.root/snapshots.STORE/'transactions'
            for pin in parent.glob('*/new-activation.json') if parent.exists() else ():
                if self.pending().exists() and not replaced:
                    foreign = pin.with_name('foreign-pin.json')
                    foreign.write_bytes(pin.read_bytes())
                    original = pin.stat()
                    os.utime(foreign, ns=(original.st_atime_ns, original.st_mtime_ns))
                    self.assertNotEqual(snapshots.transaction._identity(original),
                                        snapshots.transaction._identity(foreign.stat()))
                    os.replace(foreign, pin)
                    replaced = True
            return real(cancelled)
        with patch.object(imp, '_check_cancelled', side_effect=check):
            with self.assertRaisesRegex(ValueError, 'staged-activation-changed'):
                self.install(self.package())
        self.assertTrue(replaced)
        self.assertFalse((self.root/snapshots.activation_path(self.profile)).exists())
        self.assertFalse(self.pending().exists())

    def test_unsafe_activation_ancestor_does_not_move_legacy_receipt(self):
        archive=self.package();imp.install_author_package_live(archive,self.root,application_idle=True)
        receipt=self.root/f'work/map-packages/{self.profile}.json';data=receipt.read_bytes()
        parent=self.root/snapshots.STORE;parent.mkdir();(parent/'active').write_bytes(b'foreign')
        with self.assertRaises((OSError,ValueError)):self.install(archive)
        self.assertEqual(receipt.read_bytes(),data);self.assertFalse(self.pending().exists())

    def test_changed_dependency_during_stage_requires_reinspection(self):
        archive=self.package();real=snapshots.transaction._write_new;changed=False
        def write(path,data):
            nonlocal changed
            result=real(path,data)
            if path.name=='manifest.json' and not changed:
                changed=True;(self.root/self.fixture.contract['referenceResource']).write_bytes(b'changed')
            return result
        with patch.object(snapshots.transaction,'_write_new',side_effect=write):
            with self.assertRaisesRegex(ValueError,'plan-changed'):self.install(archive)
        self.assertFalse((self.root/snapshots.activation_path(self.profile)).exists());self.assertFalse(self.pending().exists())

    def test_committed_marker_cleanup_failure_never_rolls_back(self):
        self.install(self.package());old=self.view()
        with patch.object(snapshots,'_clear_pointer',side_effect=OSError('cleanup failure')):
            with self.assertRaises(OSError):self.install(self.package(fmt='jpeg'))
        current=self.view();self.assertNotEqual(old.activation,current.activation)
        self.assertEqual(self.recover()['status'],'author-snapshot-committed');current.assert_active()

    def test_changed_recovery_plan_rejected(self):
        self.interrupt_publish(self.package())
        with resource_write_lock(self.root):
            plan=recovery.inspect_locked(self.root);plan['planSha256']='a'*64
            with self.assertRaisesRegex(ValueError,'plan-changed'):recovery.execute_locked(self.root,plan,'recover')
        self.assertTrue(self.pending().exists())

    def test_full_purge_plans_snapshot_payload_and_activation(self):
        self.install(self.package())
        import map_package_removal
        with resource_write_lock(self.root):
            plan = map_package_removal._plan_locked(self.root, self.profile)
        self.assertGreater(plan['fileCount'], 0)
        self.assertIn(snapshots.activation_path(self.profile), {row['path'] for row in plan['entries']})

    def test_committed_status_cannot_be_downgraded_to_rollback(self):
        self.install(self.package())
        with patch.object(snapshots,'_clear_pointer',side_effect=OSError('cleanup failure')):
            with self.assertRaises(OSError):self.install(self.package(fmt='jpeg'))
        current=self.view();directory,journal=snapshots._load_pending(self.root)
        journal['status']='prepared';snapshots._write_journal(directory,journal)
        self.assertEqual(self.recover()['status'],'author-snapshot-committed');current.assert_active()

    def test_corrupt_journal_old_null_rejected_before_rollback(self):
        self.install(self.package());self.interrupt_publish(self.package(fmt='jpeg'))
        current=self.view();directory,journal=snapshots._load_pending(self.root)
        journal['changes'][-1]['old']=None;snapshots._write_journal(directory,journal)
        with self.assertRaisesRegex(ValueError,'pending-changed'):self.recover()
        current.assert_active();self.assertTrue(self.pending().exists())

    def test_missing_new_witness_blocks_with_published_activation(self):
        self.install(self.package());self.interrupt_publish(self.package(fmt='jpeg'))
        current=self.view();directory,_=snapshots._load_pending(self.root);(directory/'new-activation.json').unlink()
        with self.assertRaisesRegex(ValueError,'rollback-new-pin-changed'):self.recover()
        current.assert_active();self.assertTrue(self.pending().exists())

    def test_initial_new_pin_capture_does_not_claim_unknown_bytes(self):
        real=snapshots.transaction._write_new
        def write(path,data):
            result=real(path,data)
            if path.name=='new-activation.json':path.unlink();path.write_bytes(b'foreign')
            return result
        with patch.object(snapshots.transaction,'_write_new',side_effect=write):
            with self.assertRaisesRegex(ValueError,'staged-activation-changed'):self.install(self.package())
        self.assertFalse((self.root/snapshots.activation_path(self.profile)).exists());self.assertFalse(self.pending().exists())

    def test_prior_activation_swapped_during_new_pin_write_not_claimed(self):
        self.install(self.package());active=self.root/snapshots.activation_path(self.profile)
        real=snapshots.transaction._write_new
        def write(path,data):
            result=real(path,data)
            if path.name=='new-activation.json':active.unlink();active.write_bytes(b'foreign')
            return result
        with patch.object(snapshots.transaction,'_write_new',side_effect=write):
            with self.assertRaises(ValueError):self.install(self.package(fmt='jpeg'))
        self.assertEqual(active.read_bytes(),b'foreign')

    def test_committed_foreign_target_is_not_cleaned_up(self):
        with patch.object(snapshots,'_clear_pointer',side_effect=OSError('cleanup failure')):
            with self.assertRaises(OSError):self.install(self.package())
        active=self.root/snapshots.activation_path(self.profile);active.unlink();active.write_bytes(b'foreign')
        with self.assertRaisesRegex(ValueError,'committed-target-changed'):self.recover()
        self.assertEqual(active.read_bytes(),b'foreign');self.assertTrue(self.pending().exists())

    def test_declarations_include_snapshot_and_tombstone_without_image_decode(self):
        self.install(self.package());from map_package_summary import declared_packages
        with patch.object(snapshots.SnapshotPackage,'document',side_effect=AssertionError('not a cheap declaration')):
            rows=declared_packages(self.root)
        row=next(row for row in rows if row['packageId']==self.profile)
        self.assertEqual(row['storageFormat'],snapshots.SNAPSHOT_SCHEMA);self.assertTrue(row['canInspectRemoval'])
        plan=management.inspect_author_package_removal(self.root,self.profile)
        management.remove_author_package_live(self.root,self.profile,application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'],expected_plan_sha256=plan['planSha256'])
        row=next(row for row in declared_packages(self.root) if row['packageId']==self.profile)
        self.assertEqual(row['status'],'not-installed');self.assertTrue(row['canInspectRemoval'])

    def test_snapshot_pending_startup_plan_supported(self):
        self.interrupt_publish(self.package())
        from client_resource_startup_recovery import _validate_plan, _confirmation_message
        with resource_write_lock(self.root):plan=recovery.inspect_locked(self.root)
        self.assertEqual(_validate_plan(plan,'author-snapshot'),plan)
        self.assertIn('作者图版本切换',_confirmation_message(plan,'author-snapshot'))

    @unittest.skipUnless(hasattr(os,'mkfifo'), 'POSIX FIFO type guard')
    def test_metadata_fifo_rejected_without_waiting_for_writer(self):
        # A bounded subprocess makes a regression fail instead of hanging the suite.
        import subprocess
        import sys
        fifo=self.root/'metadata-fifo';os.mkfifo(fifo)
        result=subprocess.run([sys.executable,'-c',
            'from pathlib import Path; import author_resource_snapshots as s; s._read_json(Path(__import__("sys").argv[1]))',str(fifo)],
            capture_output=True,timeout=5)
        self.assertNotEqual(result.returncode,0);self.assertIn(b'invalid-metadata-file',result.stderr)

    def test_warm_runtime_pixels_reject_old_generation_after_update(self):
        from s1_author_displays import S1AuthorDisplays
        from types import SimpleNamespace
        self.install(self.package())
        native=SimpleNamespace(root=self.root/'_internal',mode='nightmare',
            catalog=SimpleNamespace(profiles={},_raw={'profiles':[]},default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        runtime=S1AuthorDisplays(self.root/'_internal',native,season='s0')
        image=runtime.binding(self.profile,'map','B1')['path']
        self.assertEqual(runtime.pixels(image,'aligned-overlay').shape,(8,9,4))
        self.install(self.package(fmt='jpeg'))
        self.assertTrue(image.exists())
        with self.assertRaisesRegex(ValueError,'activation-changed'):runtime.pixels(image,'aligned-overlay')

    def test_one_bad_activation_record_does_not_hide_other_snapshot_profile(self):
        self.install(self.package());other='author-other';self.install(self.package(profile=other))
        bad=self.root/snapshots.activation_path(other);bad.write_bytes(b'bad-json')
        self.assertIn(self.profile,generic.profile_ids(self.root/'_internal'))
        self.assertEqual(len(InstalledAuthorPackage(self.root/'_internal',self.profile).document['layers']),1)
        self.assertEqual(imp.verified_installed_package(self.root,other)['status'],'unverified')

    def test_legacy_other_import_cannot_change_snapshot_dependency(self):
        self.install(self.package());row=self.fixture.deps[0];new=dict(row,sha256='a'*64)
        with self.assertRaisesRegex(ValueError,'shared-package-dependency-conflict'):
            imp._check_other_package_constraints(self.root,'nightmare-suju',{row['path'].casefold():new},[])
        # Logical author paths are never falsely owned as flat installed files.
        path=generic.image_path(self.profile,'s0','nightmare','map','B1','png')
        imp._check_other_package_constraints(self.root,'nightmare-suju',{},[dict(path=path,bytes=0,sha256='a'*64)])

    def test_crash_matrix_keeps_old_or_committed_generation(self):
        from contextlib import ExitStack
        def run(stop=None,after=False):
            fixture=fixtures.GenericPackageTests();fixture.setUp();count=0
            try:
                old=fixture.package()
                imp.install_author_package_live(old,fixture.root,application_idle=True,versioned=True)
                new=fixture.package(fmt='jpeg')
                sources=[(obj,name,getattr(obj,name)) for obj,name in (
                    (snapshots.transaction,'_write_new'),(snapshots,'_write_journal'),
                    (snapshots.transaction,'_move_owned'),(snapshots.transaction,'_directory_sync'),(snapshots.os,'link'))]
                def wrapper(function):
                    def execute(*args,**kwargs):
                        nonlocal count
                        count+=1;boundary=count
                        if boundary==stop and not after:raise Crash()
                        result=function(*args,**kwargs)
                        if boundary==stop and after:raise Crash()
                        return result
                    return execute
                with ExitStack() as stack:
                    for obj,name,function in sources:
                        stack.enter_context(patch.object(obj,name,side_effect=wrapper(function)))
                    try:imp.install_author_package_live(new,fixture.root,application_idle=True,versioned=True)
                    except Crash:pass
                if (fixture.root/snapshots.PENDING).exists():
                    with resource_write_lock(fixture.root):snapshots.recover_locked(fixture.root)
                view=snapshots.SnapshotPackage(fixture.root,fixture.profile)
                self.assertEqual(len(view.document()['layers']),1)
                self.assertIn(view.manifest['receipt']['archiveSha256'],{imp._sha(old),imp._sha(new)})
                self.assertFalse((fixture.root/snapshots.PENDING).exists())
                for row in fixture.deps:self.assertEqual(imp._sha(fixture.root/row['path']),row['sha256'])
                return count
            finally:fixture.doCleanups()
        boundaries=run()
        for after in (False,True):
            for boundary in range(1,boundaries+1):
                with self.subTest(boundary=boundary,after=after):run(boundary,after)
