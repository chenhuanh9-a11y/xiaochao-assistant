"""Rollback of unpublished paths, real temporary files and injected failures.

No player data, Windows installation or power-loss durability claim.
"""
from pathlib import Path
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import managed_base_resources as managed
import test_legacy_receipt_startup_retention as fixtures
import test_managed_base_resources as base_fixtures


class AbsentBaseRollbackTests(unittest.TestCase):
    unpublished = '_internal/references/difficult-native/difficult-map-01-1-shared.npz'
    fail_at = '_internal/references/catalog.json'
    first = '_internal/difficult-native-manifest.json'

    def setUp(self):
        self.f = fixtures.LegacyReceiptStartupRetentionTests('runTest')
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.root = self.f.root
        self.receipt = self.f.save(self.f.receipt('nightmare-suju'))
        self.f.f.write(self.root / 'work/user-routes/keep.json', b'user route')
        self.before = self.f.protected()

    def fail_publication(self, error):
        original = managed.os.link
        def link(src, dst, *args, **kwargs):
            if Path(dst) == self.root / self.fail_at:
                raise error
            return original(src, dst, *args, **kwargs)
        return patch.object(managed.os, 'link', side_effect=link)

    def interrupt_publication(self):
        with self.fail_publication(KeyboardInterrupt('simulated publication loss')):
            with self.assertRaises(KeyboardInterrupt):
                managed.repair_managed_base_resources(self.root)
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())
        self.assertFalse((self.root / self.unpublished).parent.exists())

    def assert_rolled_back(self):
        self.f.unchanged(self.before)
        self.assertFalse((self.root / managed.STATE).exists())
        self.assertFalse((self.root / managed.PENDING).exists())
        for relative in self.f.f.resources:
            self.assertFalse((self.root / '_internal' / relative).exists(), relative)
        self.assertFalse((self.root / self.unpublished).parent.exists())

    def test_publication_error_keeps_original_failure_and_does_not_create_unused_directory(self):
        original_sync = managed.transaction._directory_sync
        with self.fail_publication(OSError('original publication error')), \
                patch.object(managed.transaction, '_directory_sync', wraps=original_sync) as sync:
            with self.assertRaisesRegex(OSError, 'original publication error'):
                managed.repair_managed_base_resources(self.root)
        self.assertNotIn((self.root / self.unpublished).parent, [args[0] for args, _ in sync.call_args_list])
        self.assert_rolled_back()
        self.assertEqual(managed.repair_managed_base_resources(self.root)['status'], 'repaired')
        self.f.unchanged(self.before)

    def test_process_loss_recovers_without_syncing_never_created_parent(self):
        self.interrupt_publication()
        self.assertEqual(managed.recover_managed_base_resources(self.root)['status'], 'rolled-back')
        self.assert_rolled_back()

    def test_unknown_file_at_unpublished_path_is_preserved_with_pending_marker(self):
        self.interrupt_publication()
        target = self.root / self.unpublished
        self.f.f.write(target, b'new unrelated user content')
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-rollback-incomplete'):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(target.read_bytes(), b'new unrelated user content')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file(), 'whole preflight must precede rollback writes')
        self.f.unchanged(self.before)

    def test_existing_parent_sync_failure_remains_pending_and_can_resume(self):
        self.interrupt_publication()
        original_sync = managed.transaction._directory_sync
        def sync(path):
            if Path(path) == self.root / '_internal':
                raise OSError('real mutation sync failure')
            return original_sync(path)
        with patch.object(managed.transaction, '_directory_sync', side_effect=sync), \
                self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-rollback-incomplete'):
            managed.recover_managed_base_resources(self.root)
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertFalse((self.root / self.first).exists())
        with patch.object(managed.transaction, '_directory_sync', wraps=original_sync) as sync_again:
            self.assertEqual(managed.recover_managed_base_resources(self.root)['status'], 'rolled-back')
        self.assertIn(self.root / '_internal', [args[0] for args, _ in sync_again.call_args_list])
        self.assert_rolled_back()

    def test_process_loss_after_unlink_resumes_with_existing_parent_barrier(self):
        self.interrupt_publication()
        original_delete = managed.files._rollback_published
        def interrupt(path, *args):
            result = original_delete(path, *args)
            if Path(path) == self.root / self.first:
                raise KeyboardInterrupt('simulated loss before directory sync')
            return result
        with patch.object(managed.files, '_rollback_published', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            managed.recover_managed_base_resources(self.root)
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertFalse((self.root / self.first).exists())
        original_sync = managed.transaction._directory_sync
        with patch.object(managed.transaction, '_directory_sync', wraps=original_sync) as sync:
            self.assertEqual(managed.recover_managed_base_resources(self.root)['status'], 'rolled-back')
        self.assertIn(self.root / '_internal', [args[0] for args, _ in sync.call_args_list])
        self.assert_rolled_back()

    def test_linked_unpublished_parent_does_not_gain_noop_exception(self):
        self.interrupt_publication()
        target = self.root / self.unpublished
        outside = self.f.f.folder / 'outside-owned-fixture'; outside.mkdir()
        external = outside / target.name; external.write_bytes(b'outside fixture, preserve')
        try:
            target.parent.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('directory symlink unavailable on this test platform')
        with self.assertRaises(ValueError):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(external.read_bytes(), b'outside fixture, preserve')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())

    def test_dangling_parent_is_not_treated_as_never_created(self):
        self.interrupt_publication()
        parent = (self.root / self.unpublished).parent
        try:
            parent.symlink_to(self.f.f.folder / 'nonexistent-fixture', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('directory symlink unavailable on this test platform')
        self.assertFalse(parent.exists())
        with self.assertRaises(ValueError):
            managed.recover_managed_base_resources(self.root)
        self.assertTrue(parent.is_symlink())
        self.assertTrue((self.root / managed.PENDING).exists())

    def test_parent_replaced_with_regular_file_still_blocks(self):
        self.interrupt_publication()
        parent = (self.root / self.unpublished).parent
        parent.write_bytes(b'unrelated object, preserve')
        with self.assertRaises((OSError, ValueError)):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(parent.read_bytes(), b'unrelated object, preserve')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())

    @contextmanager
    def windows_lookup_and_sync(self):
        """Model missing descendants under a file plus Windows' no-op sync.

        Keep actual fixture bytes and directory types. This is a portable fault
        contract, not a replacement for the native Windows CI case above.
        """
        original_lstat = Path.lstat
        def lstat(path, *args, **kwargs):
            try:
                return original_lstat(path, *args, **kwargs)
            except NotADirectoryError as error:
                raise FileNotFoundError(str(error)) from error
        with patch.object(Path, 'lstat', lstat), \
                patch.object(managed.transaction, '_directory_sync', return_value=None):
            yield

    def test_non_directory_parent_blocks_even_when_sync_is_noop(self):
        self.interrupt_publication()
        parent = (self.root / self.unpublished).parent
        parent.write_bytes(b'unknown parent object')
        with self.windows_lookup_and_sync(), self.assertRaises((OSError, ValueError)):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(parent.read_bytes(), b'unknown parent object')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())

    def test_non_directory_ancestor_cannot_be_hidden_by_absent_immediate_parent(self):
        self.interrupt_publication()
        ancestor = self.root / '_internal/references'
        ancestor.rmdir(); ancestor.write_bytes(b'unknown ancestor object')
        with self.windows_lookup_and_sync(), self.assertRaises((OSError, ValueError)):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(ancestor.read_bytes(), b'unknown ancestor object')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())

    def test_parent_type_is_rechecked_after_whole_preflight(self):
        self.interrupt_publication()
        original_target = managed.files._target
        calls = 0
        def target(root, relative):
            nonlocal calls
            path = original_target(root, relative)
            if Path(root) == self.root and relative == self.unpublished:
                calls += 1
                if calls == 2:
                    path.parent.write_bytes(b'late unknown parent object')
            return path
        with self.windows_lookup_and_sync(), patch.object(managed.files, '_target', side_effect=target), \
                self.assertRaises((OSError, ValueError)):
            managed.recover_managed_base_resources(self.root)
        self.assertEqual(calls, 2)
        self.assertEqual((self.root / self.unpublished).parent.read_bytes(), b'late unknown parent object')
        self.assertTrue((self.root / managed.PENDING).exists())
        self.assertTrue((self.root / self.first).is_file())

    def test_genuinely_uncreated_parent_still_recovers_with_noop_sync(self):
        self.interrupt_publication()
        with self.windows_lookup_and_sync():
            self.assertEqual(managed.recover_managed_base_resources(self.root)['status'], 'rolled-back')
        self.assert_rolled_back()

    def test_missing_parent_with_owned_old_object_must_be_recreated_and_restored(self):
        case = base_fixtures.ManagedBaseResourcesTests('runTest')
        case.setUp(); self.addCleanup(case.doCleanups)
        case.project(); managed.repair_managed_base_resources(case.target)
        paths = [case.target / '_internal' / name for name in case.resources]
        paths.append(case.target / managed.STATE)
        before = {path: (path.read_bytes(), path.stat().st_ino) for path in paths}
        case.upgrade_candidate()
        target = case.target / self.unpublished
        original_move = managed.transaction._move_owned
        def interrupt(source, *args, **kwargs):
            original_move(source, *args, **kwargs)
            if Path(source) == target:
                target.parent.rmdir()
                raise KeyboardInterrupt('simulated loss after retaining old object')
        with patch.object(managed.transaction, '_move_owned', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            managed.repair_managed_base_resources(case.target)
        self.assertFalse(target.parent.exists())
        self.assertEqual(managed.recover_managed_base_resources(case.target)['status'], 'rolled-back')
        for path, (data, inode) in before.items():
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(path.stat().st_ino, inode)
        self.assertFalse((case.target / managed.PENDING).exists())

    def test_owned_backup_corruption_blocks_even_when_target_parent_is_absent(self):
        case = base_fixtures.ManagedBaseResourcesTests('runTest')
        case.setUp(); self.addCleanup(case.doCleanups)
        case.project(); managed.repair_managed_base_resources(case.target)
        case.upgrade_candidate()
        target = case.target / self.unpublished
        original_move = managed.transaction._move_owned
        retained = []
        def interrupt(source, *args, **kwargs):
            original_move(source, *args, **kwargs)
            if Path(source) == target:
                retained.append(Path(args[0])); target.parent.rmdir()
                raise KeyboardInterrupt('simulated loss after retaining old object')
        with patch.object(managed.transaction, '_move_owned', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            managed.repair_managed_base_resources(case.target)
        retained[0].write_bytes(b'changed retained object')
        with self.assertRaisesRegex(managed.ManagedBaseResourceError, 'base-rollback-incomplete'):
            managed.recover_managed_base_resources(case.target)
        self.assertFalse(target.parent.exists())
        self.assertEqual(retained[0].read_bytes(), b'changed retained object')
        self.assertTrue((case.target / managed.PENDING).exists())


if __name__ == '__main__':
    unittest.main()
