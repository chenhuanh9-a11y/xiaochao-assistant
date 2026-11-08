"""Real import/removal transactions over private synthetic PNG/JSON/NPZ only."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_author_resource_snapshots as fixture
import author_resource_snapshots as snapshots
import map_package_removal as removal
import map_package_summary as summary
import star_map_import as importer
from resource_transaction import resource_write_lock, assert_resources_ready


class SnapshotRemovalTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AuthorSnapshotTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root, self.package = self.f.root, self.f.profile
        self.base = {p: (self.root / p).read_bytes() for p in self.f.fixture.contract['dependencies']}

    def install(self, **kwargs):
        return self.f.install(self.f.package(**kwargs))

    def remove(self, package='selected', **kwargs):
        package = self.package if package == 'selected' else package
        plan = removal.inspect_package_removal(self.root, package)
        return removal.remove_packages(self.root, package, application_idle=True,
            expected_plan_sha256=plan['planSha256'], **kwargs)

    def assert_base(self):
        self.assertEqual(self.base, {p: (self.root / p).read_bytes() for p in self.base})

    def test_single_delete_cleans_current_old_versions_and_no_private_backup(self):
        self.install(); self.install(fmt='jpeg')
        self.assertGreater(len(list((self.root / snapshots.STORE / 'versions').rglob('manifest.json'))), 1)
        result = self.remove()
        self.assertEqual(result['status'], 'packages-removed')
        self.assertGreater(result['removedFiles'], 10)
        self.assertFalse(snapshots.managed(self.root, self.package))
        self.assertEqual(summary.declared_packages(self.root), [])
        self.assertEqual(list((self.root / snapshots.STORE).rglob('*.*')), [])
        self.assertEqual(list((self.root / removal.STATE).glob('*/objects/*')), [])
        self.assertEqual(self.remove()['status'], 'already-removed')
        self.assert_base()

    def test_clear_all_includes_active_and_deactivated_snapshots(self):
        self.install()
        from author_package_management import inspect_author_package_removal, remove_author_package_live
        plan = inspect_author_package_removal(self.root, self.package)
        remove_author_package_live(self.root, self.package, application_idle=True,
            expected_receipt_sha256=plan['receiptSha256'], expected_plan_sha256=plan['planSha256'])
        self.assertTrue(summary.declared_packages(self.root)[0]['canInspectRemoval'])
        result = self.remove(None)
        self.assertEqual(result['packageIds'], [self.package])
        self.assertFalse(snapshots.managed(self.root, self.package))
        self.assertEqual(list((self.root / snapshots.STORE).rglob('*.*')), [])
        self.assert_base()

    def test_modified_or_unknown_payload_is_preserved_with_ownership_records(self):
        self.install(); view = self.f.view()
        image = next(view.directory.rglob('*.png'))
        image.write_bytes(b'user edited image')
        unknown = view.directory / 'notes.txt'; unknown.write_bytes(b'user notes')
        result = self.remove()
        self.assertEqual(result['status'], 'packages-partially-removed')
        self.assertEqual(image.read_bytes(), b'user edited image')
        self.assertEqual(unknown.read_bytes(), b'user notes')
        self.assertTrue((view.directory / 'manifest.json').exists())
        self.assertTrue(snapshots.managed(self.root, self.package))
        self.assertGreater(result['unverifiedPreservedCount'], 0)
        self.assert_base()

    def test_unknown_snapshot_directory_never_gains_ownership(self):
        self.install()
        unknown = self.root / snapshots.STORE / 'versions' / self.package / ('a' * 32)
        unknown.mkdir(); (unknown / 'note.txt').write_text('keep')
        result = self.remove()
        self.assertGreater(result['unverifiedPreservedCount'], 0)
        self.assertEqual((unknown / 'note.txt').read_text(), 'keep')
        self.assertTrue(snapshots.managed(self.root, self.package))

    def test_upgrade_from_flat_removes_only_owned_legacy_and_snapshot_files(self):
        archive = self.f.package()
        importer.install_author_package_live(archive, self.root, application_idle=True)
        rows, _, _ = importer._installed_inventory(self.root, self.package)
        self.f.install(self.f.package(fmt='jpeg'))
        self.assertTrue(any((self.root / row['path']).exists() for row in rows.values()))
        self.remove()
        self.assertTrue(all(not (self.root / row['path']).exists() for row in rows.values()))
        self.assert_base()

    def test_plan_race_and_cross_package_path_do_not_execute(self):
        self.install(); view = self.f.view()
        plan = removal.inspect_package_removal(self.root, self.package)
        image = next(view.directory.rglob('*.png')); before = image.read_bytes()
        image.write_bytes(before + b'changed')
        with self.assertRaisesRegex(ValueError, 'plan-changed'):
            removal.remove_packages(self.root, self.package, application_idle=True,
                expected_plan_sha256=plan['planSha256'])
        self.assertEqual(image.read_bytes(), before + b'changed')
        self.assertTrue(snapshots.managed(self.root, self.package))
        other = 'author-other'
        self.assertFalse(removal._valid_target(other, image.relative_to(self.root).as_posix()))

    def test_cancel_rolls_back_pinned_objects_and_restores_activation(self):
        self.install(); view = self.f.view()
        before = {p.relative_to(self.root): p.read_bytes() for p in (self.root / snapshots.STORE).rglob('*') if p.is_file()}
        cancel = False
        real = removal.files._rollback_published
        def remove(path, identity, digest):
            nonlocal cancel
            result = real(path, identity, digest)
            if str(path).startswith(str(view.directory)):
                cancel = True
            return result
        with patch.object(removal.files, '_rollback_published', side_effect=remove):
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                self.remove(cancelled=lambda: cancel)
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in (self.root / snapshots.STORE).rglob('*') if p.is_file()})
        assert_resources_ready(self.root)
        self.assert_base()

    def test_cleanup_failure_keeps_pending_gate_then_recovers_exact_targets(self):
        self.install()
        real = removal.files._rollback_published
        def remove(path, identity, digest):
            return False if path.parent.name == 'objects' else real(path, identity, digest)
        with patch.object(removal.files, '_rollback_published', side_effect=remove):
            with self.assertRaisesRegex(ValueError, 'committed-cleanup-required'):
                self.remove()
        with self.assertRaisesRegex(ValueError, 'removal-recovery-required'):
            assert_resources_ready(self.root)
        with resource_write_lock(self.root):
            removal.recover_pending_removal_locked(self.root)
        assert_resources_ready(self.root)
        self.assertEqual(summary.declared_packages(self.root), [])
        self.assert_base()

    def test_single_removal_preserves_other_author_then_clear_all_cleans_it(self):
        self.install()
        other = 'author-second'
        self.f.install(self.f.package(profile=other))
        foreign = snapshots.SnapshotPackage(self.root, other)
        before = {p: p.read_bytes() for p in foreign.directory.rglob('*') if p.is_file()}
        self.remove()
        self.assertEqual(before, {p: p.read_bytes() for p in foreign.directory.rglob('*') if p.is_file()})
        foreign.assert_active()
        self.remove(None)
        self.assertFalse(snapshots.managed(self.root, other))
        self.assert_base()

    def test_process_interruption_restores_snapshot_paths_and_original_identity(self):
        self.install(); view = self.f.view()
        originals = {p: (p.read_bytes(), snapshots.transaction._identity(p.stat()))
                     for p in (self.root / snapshots.STORE).rglob('*') if p.is_file()}
        real = removal.files._rollback_published
        def crash_after_first(path, identity, digest):
            result = real(path, identity, digest)
            if path == self.root / snapshots.activation_path(self.package):
                raise fixture.Crash()
            return result
        with patch.object(removal.files, '_rollback_published', side_effect=crash_after_first):
            with self.assertRaises(fixture.Crash):
                self.remove()
        with resource_write_lock(self.root):
            removal.recover_pending_removal_locked(self.root)
        self.assertEqual(originals, {p: (p.read_bytes(), snapshots.transaction._identity(p.stat())) for p in originals})
        view.assert_active()
        assert_resources_ready(self.root)

    def test_other_package_and_links_are_not_claimed(self):
        self.install(); view = self.f.view()
        foreign = self.root / '_internal' / 'user.txt'; foreign.write_text('private')
        link = view.directory / 'link.txt'
        try:
            link.symlink_to(foreign)
        except OSError:
            self.skipTest('symlinks unavailable')
        result = self.remove()
        self.assertGreater(result['unverifiedPreservedCount'], 0)
        self.assertTrue(link.is_symlink())
        self.assertEqual(foreign.read_text(), 'private')
        self.assertTrue(snapshots.managed(self.root, self.package))
