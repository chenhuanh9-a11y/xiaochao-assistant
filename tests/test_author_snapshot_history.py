"""Retained rollback authority and production activation, synthetic resources only."""
from pathlib import Path
import json
import os
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import test_generic_author_package as fixtures
import author_resource_snapshots as snapshots
import author_snapshot_history as history
import star_map_import as imp
from resource_transaction import resource_write_lock


class Crash(BaseException): pass


class AuthorHistoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.GenericPackageTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.root,self.package=self.fixture.root,self.fixture.profile
        self.first=self.install()
        self.second=self.install(fmt='jpeg')

    def install(self,**kwargs):
        return imp.install_author_package_live(self.fixture.package(**kwargs),self.root,application_idle=True,versioned=True)

    def plan(self):
        return history.inspect_restore(self.root,self.package,self.first['snapshotTransactionId'])

    def restore(self,plan=None,**kwargs):
        plan=plan or self.plan()
        return history.restore_version(self.root,self.package,plan['sourceTransactionId'],application_idle=True,
            expected_plan_sha256=plan['planSha256'],**kwargs)

    def current(self):return snapshots.SnapshotPackage(self.root,self.package)

    def test_history_lists_committed_original_versions_once(self):
        rows=history.list_versions(self.root,self.package)
        self.assertEqual({row['snapshotId'] for row in rows},{self.first['snapshotId'],self.second['snapshotId']})
        self.assertEqual(sum(row['current'] for row in rows),1)
        self.assertEqual(sum(row['canInspectRestore'] for row in rows),1)
        self.restore()
        self.assertEqual(len(history.list_versions(self.root,self.package)),2)

    def test_restore_reuses_version_never_mutates_images_or_base(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file() and ('versions' in p.parts or '_internal' in p.parts)}
        old=self.current();result=self.restore()
        self.assertEqual(result['status'],'author-version-restored')
        self.assertEqual(self.current().activation['snapshotId'],self.first['snapshotId'])
        self.assertNotEqual(self.current().activation['generation'],old.activation['generation'])
        for path,data in before.items():self.assertEqual(path.read_bytes(),data)
        with self.assertRaisesRegex(ValueError,'activation-changed'):old.assert_active()

    def test_restored_runtime_pixels_invalidate_old_generation_and_keep_identity_provider(self):
        from s1_author_displays import S1AuthorDisplays
        from types import SimpleNamespace
        native=SimpleNamespace(root=self.root/'_internal',mode='nightmare',
            catalog=SimpleNamespace(profiles={},_raw={'profiles':[]},default_profile_id='shared-structure'),
            adapters=SimpleNamespace(profiles={}))
        runtime=S1AuthorDisplays(self.root/'_internal',native,season='s0')
        image=runtime.binding(self.package,'map','B1')['path']
        self.assertEqual(runtime.pixels(image,'aligned-overlay').shape,(8,9,4))
        self.restore()
        with self.assertRaisesRegex(ValueError,'activation-changed'):
            runtime.pixels(image,'aligned-overlay')
        restored=S1AuthorDisplays(self.root/'_internal',native,season='s0')
        binding=restored.binding(self.package,'map','B1')
        self.assertEqual(restored.pixels(binding['path'],'aligned-overlay').shape,(8,9,4))
        self.assertTrue(str(binding['path']).endswith('.png'))
        self.assertEqual(native.catalog.default_profile_id,'shared-structure')

    def test_partial_compatibility_is_rechecked_not_silently_promoted(self):
        import test_author_resource_snapshots as fixture_module
        case=fixture_module.AuthorSnapshotTests();case.setUp()
        try:
            first=case.install(case.multi(invalid_second=True))
            case.install(case.package(fmt='jpeg'))
            plan=history.inspect_restore(case.root,case.profile,first['snapshotTransactionId'])
            self.assertEqual(plan['activeLayerCount'],1);self.assertEqual(len(plan['inactiveLayers']),1)
            result=history.restore_version(case.root,case.profile,first['snapshotTransactionId'],
                application_idle=True,expected_plan_sha256=plan['planSha256'])
            self.assertEqual(result['activeLayerCount'],1);self.assertEqual(len(result['inactiveLayers']),1)
            self.assertEqual(len(case.view().document()['layers']),1)
        finally:case.doCleanups()

    def test_upgrade_reset_rollback_preserves_history_authority(self):
        import test_resource_reset_snapshots as reset_fixtures
        import resource_upgrade_reset as reset
        case=reset_fixtures.ResourceResetSnapshotTests();case.setUp()
        try:
            case.author.install(case.author.package(fmt='jpeg'))
            before=history.list_versions(case.root,case.profile)
            old=next(row for row in before if row['canInspectRestore'])
            prepared=case.prepare(case.plan())
            with self.assertRaisesRegex(ValueError,'reset'):
                history.list_versions(case.root,case.profile)
            reset.finish_resource_upgrade(case.root,prepared['transactionId'],setup_succeeded=False)
            self.assertEqual(history.list_versions(case.root,case.profile),before)
            plan=history.inspect_restore(case.root,case.profile,old['sourceTransactionId'])
            history.restore_version(case.root,case.profile,old['sourceTransactionId'],application_idle=True,
                                    expected_plan_sha256=plan['planSha256'])
            self.assertEqual(case.user.read_bytes(),b'personal route')
        finally:case.doCleanups()

    def test_declared_current_cannot_be_restored_as_change(self):
        with self.assertRaisesRegex(ValueError,'already-current'):
            history.inspect_restore(self.root,self.package,self.second['snapshotTransactionId'])

    def test_confirmation_or_idle_required(self):
        plan=self.plan()
        with self.assertRaisesRegex(ValueError,'requires-idle'):
            history.restore_version(self.root,self.package,plan['sourceTransactionId'],expected_plan_sha256=plan['planSha256'])
        with self.assertRaisesRegex(ValueError,'confirmation-required'):
            history.restore_version(self.root,self.package,plan['sourceTransactionId'],application_idle=True)

    def test_changed_current_generation_invalidates_confirmation(self):
        plan=self.plan();self.install(color=(7,8,9,255));view=self.current()
        with self.assertRaisesRegex(ValueError,'plan-changed'):self.restore(plan)
        view.assert_active()

    def test_history_payload_change_rejects_without_overwrite(self):
        plan=self.plan();old=self.root/snapshots._version_path(self.package,self.first['snapshotId'])/'files/0000.png'
        old.write_bytes(b'modified by user');view=self.current()
        with self.assertRaises(ValueError):self.restore(plan)
        self.assertEqual(old.read_bytes(),b'modified by user');view.assert_active()

    def test_missing_base_or_bad_contract_cannot_create_migration_authority(self):
        plan=self.plan();reference=self.root/self.fixture.contract['referenceResource'];original=reference.read_bytes()
        reference.write_bytes(b'different base');view=self.current()
        with self.assertRaisesRegex(ValueError,'no-compatible-layers'):self.restore(plan)
        view.assert_active();reference.write_bytes(original)
        self.assertEqual(self.plan()['activeLayerCount'],1)

    def test_foreign_root_transaction_is_not_a_version_proof(self):
        source=self.root/snapshots.STORE/'transactions'/self.first['snapshotTransactionId']/'journal.json'
        doc=json.loads(source.read_bytes());doc['rootIdentity']=[123,456];source.write_bytes(snapshots._encoded(doc))
        with self.assertRaises(ValueError):self.plan()
        rows=history.list_versions(self.root,self.package)
        row=next(row for row in rows if row['snapshotId']==self.first['snapshotId'])
        self.assertEqual(row['status'],'unverified-history');self.assertFalse(row['canInspectRestore'])

    def test_commit_witness_copy_cannot_authorize_restore(self):
        path=self.root/snapshots.STORE/'transactions'/self.first['snapshotTransactionId']/'committed.json'
        data=path.read_bytes();path.unlink();path.write_bytes(data)
        with self.assertRaisesRegex(ValueError,'commit-outcome-changed'):self.plan()

    def test_uncommitted_orphan_is_visible_but_not_restorable(self):
        fake=self.root/snapshots.STORE/'versions'/self.package/('a'*32);fake.mkdir()
        (fake/'manifest.json').write_bytes(b'unknown')
        rows=history.list_versions(self.root,self.package)
        row=next(row for row in rows if row['snapshotId']=='a'*32)
        self.assertEqual(row['status'],'unverified-history');self.assertFalse(row['canInspectRestore'])

    def test_tombstone_can_restore_a_verified_version(self):
        import author_package_management as management
        plan=management.inspect_author_package_removal(self.root,self.package)
        management.remove_author_package_live(self.root,self.package,application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'],expected_plan_sha256=plan['planSha256'])
        self.restore();self.assertEqual(self.current().activation['snapshotId'],self.first['snapshotId'])

    def test_cancel_leaves_current_generation(self):
        plan=self.plan();view=self.current()
        with self.assertRaises(ValueError):self.restore(plan,cancelled=lambda:True)
        view.assert_active();self.assertFalse((self.root/snapshots.PENDING).exists())

    def test_restore_interruption_uses_existing_recovery(self):
        plan=self.plan();old=self.current()
        with patch.object(snapshots,'_commit_outcome',side_effect=Crash()):
            with self.assertRaises(Crash):self.restore(plan)
        self.assertTrue((self.root/snapshots.PENDING).exists())
        with resource_write_lock(self.root):snapshots.recover_locked(self.root)
        old.assert_active()

    def test_commit_then_cleanup_failure_does_not_undo_restore(self):
        plan=self.plan()
        with patch.object(snapshots,'_clear_pointer',side_effect=OSError('cleanup')):
            with self.assertRaises(OSError):self.restore(plan)
        view=self.current();self.assertEqual(view.activation['snapshotId'],self.first['snapshotId'])
        with resource_write_lock(self.root):result=snapshots.recover_locked(self.root)
        self.assertEqual(result['status'],'author-snapshot-committed');view.assert_active()

    def test_candidate_changed_after_publication_rolls_back_activation(self):
        plan=self.plan();prior=self.current();real=snapshots._activate_locked
        old=self.root/snapshots._version_path(self.package,self.first['snapshotId'])/'files/0000.png'
        def activate(*args,**kwargs):
            check=kwargs['before_commit']
            def changed():old.write_bytes(b'changed');check()
            return real(*args,**dict(kwargs,before_commit=changed))
        with patch.object(snapshots,'_activate_locked',side_effect=activate):
            with self.assertRaises(ValueError):self.restore(plan)
        prior.assert_active();self.assertEqual(old.read_bytes(),b'changed')
        self.assertFalse((self.root/snapshots.PENDING).exists())

    def _during_final_dependency_read(self, action, occurrence=1):
        """Inject after an earlier author image was verified, not before it."""
        real_activate, real_read = snapshots._activate_locked, history._payload_read
        seen = 0
        def activate(*args, **kwargs):
            check = kwargs['before_commit']
            def read(path, *rest):
                nonlocal seen
                result = real_read(path, *rest)
                if path.relative_to(self.root).as_posix().startswith('_internal/'):
                    seen += 1
                    if seen == occurrence:
                        action()
                return result
            def final_check():
                with patch.object(history, '_payload_read', side_effect=read):
                    check()
            return real_activate(*args, **dict(kwargs, before_commit=final_check))
        return patch.object(snapshots, '_activate_locked', side_effect=activate)

    def test_final_dependency_read_cannot_commit_changed_earlier_image(self):
        plan = self.plan(); prior = self.current()
        image = self.root/snapshots._version_path(self.package,self.first['snapshotId'])/'files/0000.png'
        with self._during_final_dependency_read(lambda: image.write_bytes(b'changed-during-check')):
            with self.assertRaisesRegex(ValueError, 'payload-changed'):
                self.restore(plan)
        prior.assert_active()
        self.assertEqual(image.read_bytes(), b'changed-during-check')
        self.assertFalse((self.root/snapshots.PENDING).exists())

    def test_final_observation_rejects_same_bytes_inplace_after_last_image_read(self):
        plan = self.plan(); prior = self.current()
        image = self.root/snapshots._version_path(self.package,self.first['snapshotId'])/'files/0000.png'
        before, data = image.stat(), image.read_bytes()
        def touch():
            image.write_bytes(data)
            os.utime(image, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self._during_final_dependency_read(touch, occurrence=2):
            with self.assertRaisesRegex(ValueError, 'payload-changed'):
                self.restore(plan)
        prior.assert_active(); self.assertEqual(image.read_bytes(), data)
        self.assertFalse((self.root/snapshots.PENDING).exists())

    def test_final_validation_does_not_commit_foreign_activation_same_bytes(self):
        plan = self.plan()
        target = self.root/snapshots.activation_path(self.package)
        foreign = {}
        def replace():
            raw = target.read_bytes()
            other = target.with_suffix('.foreign'); other.write_bytes(raw)
            os.replace(other, target)
            foreign['stamp'] = snapshots._stamp(target)
        with self._during_final_dependency_read(replace):
            with self.assertRaisesRegex(ValueError, 'rollback-blocked'):
                self.restore(plan)
        self.assertEqual(snapshots._stamp(target), foreign['stamp'])
        self.assertTrue((self.root/snapshots.PENDING).exists())
        directory, journal = snapshots._load_pending(self.root)
        self.assertEqual(journal['status'], 'prepared')
        self.assertTrue((directory/'old-0').exists())
        self.assertFalse((directory/'committed.json').exists())

    def test_final_validation_retains_foreign_backup_and_new_pin(self):
        for name, reason in (('old-0', 'rollback-backup-changed'),
                             ('new-activation.json', 'rollback-new-pin-changed')):
            with self.subTest(name=name):
                # Each foreign transaction deliberately remains pending; use an
                # independent installation so no recovery guard is bypassed.
                case = AuthorHistoryTests(); case.setUp()
                try:
                    plan = case.plan(); foreign = {}
                    def replace():
                        directory, _ = snapshots._load_pending(case.root)
                        target = directory/name; raw = target.read_bytes()
                        other = directory/'foreign'; other.write_bytes(raw)
                        os.replace(other, target)
                        foreign['target'], foreign['stamp'] = target, snapshots._stamp(target)
                    with case._during_final_dependency_read(replace, occurrence=2):
                        with self.assertRaisesRegex(ValueError, reason):
                            case.restore(plan)
                    self.assertTrue((case.root/snapshots.PENDING).exists())
                    self.assertEqual(snapshots._stamp(foreign['target']), foreign['stamp'])
                    self.assertFalse((foreign['target'].parent/'committed.json').exists())
                finally:
                    case.doCleanups()

    def test_reset_pending_blocks_list_inspect_and_restore(self):
        plan=self.plan();pending=self.root/'work/map-packages/pending-resource-reset.json';pending.write_bytes(b'pending')
        for call in (lambda:history.list_versions(self.root,self.package),self.plan,lambda:self.restore(plan)):
            with self.assertRaisesRegex(ValueError,'reset'):call()

    def test_wrong_package_and_noncanonical_transaction_rejected(self):
        for package,transaction in (('author-other',self.first['snapshotTransactionId']),
                                    (self.package,'../outside')):
            with self.assertRaises(ValueError):history.inspect_restore(self.root,package,transaction)

    def test_restore_crash_boundaries_keep_verified_old_or_committed_version(self):
        def trial(stop=None, after=False):
            case = AuthorHistoryTests(); case.setUp(); count = 0
            try:
                plan = case.plan(); prior = case.current().activation['snapshotId']
                sources = [(obj, name, getattr(obj, name)) for obj, name in (
                    (snapshots.transaction, '_write_new'), (snapshots, '_write_journal'),
                    (snapshots.transaction, '_move_owned'), (snapshots.transaction, '_directory_sync'),
                    (snapshots.os, 'link'), (history, '_candidate'))]
                def wrapper(function):
                    def invoke(*args, **kwargs):
                        nonlocal count
                        count += 1; boundary = count
                        if boundary == stop and not after: raise Crash()
                        result = function(*args, **kwargs)
                        if boundary == stop and after: raise Crash()
                        return result
                    return invoke
                with ExitStack() as stack:
                    for obj, name, function in sources:
                        stack.enter_context(patch.object(obj, name, side_effect=wrapper(function)))
                    try: case.restore(plan)
                    except Crash: pass
                if (case.root/snapshots.PENDING).exists():
                    with resource_write_lock(case.root): snapshots.recover_locked(case.root)
                view = case.current()
                self.assertIn(view.activation['snapshotId'], {prior, case.first['snapshotId']})
                self.assertEqual(len(view.document()['layers']), 1)
                self.assertFalse((case.root/snapshots.PENDING).exists())
                for row in case.fixture.deps:
                    self.assertEqual(imp._sha(case.root/row['path']), row['sha256'])
                return count
            finally:
                case.doCleanups()
        boundaries = trial()
        self.assertGreaterEqual(boundaries, 20)
        for after in (False, True):
            for boundary in range(1, boundaries+1):
                with self.subTest(after=after, boundary=boundary): trial(boundary, after)
