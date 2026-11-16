"""Real saved packages/catalogs, copying and existing removal transactions.

Small JSON payloads prove storage and selection integrity, not map recognition,
the product UI, a Setup lifecycle or actual user installation acceptance.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

import imported_resource_activation as activation
from imported_resource_catalog import CatalogSnapshot, SCHEMA
from imported_resource_packages import PackageView
from imported_resource_selection import inspect_selection
import imported_resource_migration as migration
import resource_transaction as locks
from test_imported_resource_removal import imported, wire


class ImportedResourceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='resource-migration-')
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve()
        self.source = self.parent / 'old-shared-resources'
        self.target = self.parent / 'program' / 'resources'
        self.target.parent.mkdir()
        self.archive, refs = imported(self.source, 'base-package')
        self.base, self.controls = refs
        self.route_archive, refs = imported(self.source, 'author-package', base=self.base)
        self.author = refs[0]
        self.history_archive, refs = imported(self.source, 'saved-history', season='s1')
        self.history = refs[0]
        self.document = dict(schema=SCHEMA, generation=str(uuid.uuid4()), contexts={
            's0:nightmare': dict(recognitionBase=wire(self.base), controls=wire(self.controls),
                                 reviewedGroups=None)}, displays=[wire(self.author)])
        with locks.resource_write_lock(self.source):
            activation.activate_catalog_locked(self.source, self.document, expected_catalog_stamp=None,
                validate=lambda value: inspect_selection(self.source, value))
        self.originals = self.bytes_at(self.source)
        self.archives = {path: path.read_bytes() for path in
                         (self.archive, self.route_archive, self.history_archive)}

    @staticmethod
    def bytes_at(root):
        return {path.relative_to(root).as_posix(): path.read_bytes()
                for path in root.rglob('*') if path.is_file()
                and path.relative_to(root).as_posix() != 'work/map-packages/import.lock'}

    def package(self, root, ref):
        return root / 'packages' / ref.package_id / ref.resource_version

    def assert_archives(self):
        for path, raw in self.archives.items():
            self.assertEqual(path.read_bytes(), raw)

    def test_inspect_does_not_create_target_and_counts_real_payload_bytes(self):
        result = migration.inspect_migration(self.source, self.target)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(result['versions']), 3)
        expected = {name: raw for name, raw in self.originals.items()
                    if name == 'catalog.json' or name.startswith('packages/')}
        self.assertEqual(result['totalBytes'], sum(map(len, expected.values())))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def test_real_copy_preserves_catalog_inactive_history_source_and_downloads(self):
        unknown = self.source / 'my-original-package.zip'
        unknown.write_bytes(b'user retained original archive')
        result = migration.migrate_resources(self.source, self.target)
        self.assertTrue(result['migrated'])
        self.assertTrue(result['sourceRetained'])
        self.assertIn('my-original-package.zip', [row['path'] for row in result['preserved']])
        self.assertEqual((self.target / 'catalog.json').read_bytes(),
                         (self.source / 'catalog.json').read_bytes())
        inspect_selection(self.target, self.document)
        for ref in (self.base, self.author, self.history):
            self.assertTrue(PackageView(self.target, ref).validate_inventory())
        self.assertFalse((self.target / 'transactions').exists())
        self.assertFalse((self.target / 'my-original-package.zip').exists())
        self.assertEqual({k: v for k, v in self.bytes_at(self.source).items()
                          if k != 'my-original-package.zip'}, self.originals)
        self.assert_archives()
        self.assertEqual(list(self.target.parent.glob('.resources.migration-*')), [])

    def test_cancel_after_actual_copy_leaves_source_and_no_published_target(self):
        cancelled = threading.Event()
        copied = []
        def progress(value):
            if value['stage'] == 'copying':
                copied.append(value['copiedBytes'])
                cancelled.set()
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            migration.migrate_resources(self.source, self.target,
                cancelled=cancelled.is_set, on_progress=progress)
        self.assertTrue(copied and copied[0] > 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.bytes_at(self.source), self.originals)
        self.assertEqual(list(self.target.parent.glob('.resources.migration-*')), [])

    def test_existing_target_is_never_merged_or_overwritten(self):
        self.target.mkdir()
        sentinel = self.target / 'keep.txt'
        sentinel.write_bytes(b'current local resources')
        for operation in (migration.inspect_migration, migration.migrate_resources):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, 'target-exists'):
                    operation(self.source, self.target)
        self.assertEqual(sentinel.read_bytes(), b'current local resources')
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def test_pending_catalog_and_removal_prevent_copy_without_touching_source(self):
        for name in ('pending-catalog.json', 'pending-removal.json'):
            with self.subTest(name=name):
                pending = self.source / 'transactions' / name
                pending.write_bytes(b'{"incomplete":true}')
                original = self.bytes_at(self.source)
                with self.assertRaisesRegex(ValueError, 'recovery-required'):
                    migration.migrate_resources(self.source, self.target)
                self.assertEqual(self.bytes_at(self.source), original)
                self.assertFalse(self.target.exists())
                pending.unlink()

    def test_damaged_payload_and_unknown_package_object_refuse_whole_migration(self):
        payload = self.package(self.source, self.history) / 'data/base/index.json'
        original = payload.read_bytes()
        payload.write_bytes(b'changed history payload')
        with self.assertRaisesRegex(ValueError, 'source-version-invalid'):
            migration.migrate_resources(self.source, self.target)
        payload.write_bytes(original)
        unknown = self.package(self.source, self.history) / 'keep-me.txt'
        unknown.write_bytes(b'unknown user data')
        with self.assertRaisesRegex(ValueError, 'source-version-invalid'):
            migration.inspect_migration(self.source, self.target)
        self.assertEqual(unknown.read_bytes(), b'unknown user data')
        self.assertFalse(self.target.exists())

    def test_linked_package_path_is_rejected_and_external_object_preserved(self):
        payload = self.package(self.source, self.history) / 'data/base/index.json'
        outside = self.parent / 'outside.json'
        original = payload.read_bytes()
        outside.write_bytes(original)
        payload.unlink()
        try:
            payload.symlink_to(outside)
        except OSError as error:
            self.skipTest('symlink creation unavailable: ' + str(error))
        with self.assertRaises(ValueError):
            migration.migrate_resources(self.source, self.target)
        self.assertEqual(outside.read_bytes(), original)
        self.assertFalse(self.target.exists())

    def test_unknown_staging_object_prevents_cleanup_and_is_reported(self):
        inserted = []
        def progress(value):
            if value['stage'] == 'copying' and not inserted:
                stage = next(self.target.parent.glob('.resources.migration-*'))
                unknown = stage / 'unexpected-user-file.txt'
                unknown.write_bytes(b'preserve uncertain object')
                inserted.append(unknown)
        with self.assertRaises(ValueError) as caught:
            migration.migrate_resources(self.source, self.target, on_progress=progress)
        self.assertTrue(caught.exception.details['stagingRetained'])
        self.assertEqual(inserted[0].read_bytes(), b'preserve uncertain object')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def test_injected_io_failure_after_real_write_cleans_only_own_stage(self):
        original_write = migration._OwnedStage.write
        def no_space(stage, relative, raw):
            original_write(stage, relative, raw)
            raise OSError(errno.ENOSPC, 'injected destination disk-full result')
        with patch.object(migration._OwnedStage, 'write', no_space):
            with self.assertRaises(OSError) as caught:
                migration.migrate_resources(self.source, self.target)
        self.assertEqual(caught.exception.errno, errno.ENOSPC)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.resources.migration-*')), [])
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def _assert_replaced_empty_stage_directory_is_retained(self, *, child):
        stage = migration._OwnedStage(self.target)
        if child:
            stage.write('nested/payload.json', b'{}')
        victim = stage.path / 'nested' if child else stage.path
        retained = stage.path.parent / ('retained-original-' + uuid.uuid4().hex)
        original_identity = migration.publication._identity(victim.stat())
        original_stat, original_lstat = Path.stat, Path.lstat
        original_verify = stage.verify
        original_rollback = migration.files._rollback_published
        armed, replaced = False, False

        def observed(path, operation, *args, **kwargs):
            nonlocal armed, replaced
            info = operation(path, *args, **kwargs)
            if armed and path == victim:
                # Schedule an actual rename + empty replacement after this
                # pathname observation, before cleanup can use its result.
                armed = False
                victim.rename(retained)
                victim.mkdir()
                replaced = True
            return info

        def verified():
            nonlocal armed
            result = original_verify()
            if not child:
                armed = True
            return result

        def removed(path, identity, digest):
            nonlocal armed
            result = original_rollback(path, identity, digest)
            if child and result:
                armed = True  # The owned child is now empty and ready to prune.
            return result

        with patch.object(stage, 'verify', verified), \
                patch.object(Path, 'stat', lambda path, *a, **kw: observed(path, original_stat, *a, **kw)), \
                patch.object(Path, 'lstat', lambda path, *a, **kw: observed(path, original_lstat, *a, **kw)), \
                patch.object(migration.files, '_rollback_published', removed):
            cleaned = stage.cleanup()
        self.assertTrue(replaced, 'the real directory replacement must have occurred')
        self.assertFalse(cleaned, 'cleanup cannot claim removal of the original directory')
        self.assertTrue(victim.is_dir(), 'the unknown empty replacement must remain')
        self.assertTrue(retained.is_dir(), 'the moved original must remain')
        self.assertEqual(migration.publication._identity(retained.stat()), original_identity)
        self.assertNotEqual(migration.publication._identity(victim.stat()), original_identity)

    def test_cleanup_retains_unknown_empty_replacement_of_owned_child_directory(self):
        self._assert_replaced_empty_stage_directory_is_retained(child=True)

    def test_cleanup_retains_unknown_empty_replacement_of_stage_root(self):
        self._assert_replaced_empty_stage_directory_is_retained(child=False)

    def test_same_handle_directory_delete_never_uses_pathname_rmdir(self):
        stage = migration._OwnedStage(self.target)
        retained = stage.path.parent / ('retained-original-' + uuid.uuid4().hex)
        checks, blocked, replaced = 0, False, False

        def namespace():
            nonlocal checks, blocked, replaced
            checks += 1
            if checks == 2:  # This check runs with the original DELETE handle.
                try:
                    stage.path.rename(retained)
                except OSError:
                    blocked = True
                else:
                    stage.path.mkdir()
                    replaced = True
            return None

        with patch.object(Path, 'rmdir', side_effect=AssertionError('unsafe pathname deletion')):
            reason = locks.remove_owned_empty_directory(stage.path, stage.identity,
                                                         validate_namespace=namespace)
        if os.name != 'nt':
            self.assertEqual(reason, 'empty-directory-platform-unsupported')
            self.assertTrue(stage.path.is_dir())
            return
        self.assertIsNone(reason)
        self.assertEqual(checks, 2)
        self.assertTrue(blocked or replaced, 'the real rename must have been attempted')
        if replaced:
            self.assertTrue(stage.path.is_dir(), 'a replacement cannot be deleted through the old handle')
            self.assertNotEqual(migration.publication._identity(stage.path.stat()), stage.identity)
        else:
            self.assertFalse(stage.path.exists())
        self.assertFalse(retained.exists(), 'the handle deletes only the original owned object')

    def test_late_existing_target_keeps_its_objects_and_cleans_own_stage(self):
        sentinel = self.target / 'another-operation.txt'
        def progress(value):
            if value['stage'] == 'copying' and not self.target.exists():
                self.target.mkdir()
                sentinel.write_bytes(b'new current resources')
        with self.assertRaisesRegex(ValueError, 'target-exists'):
            migration.migrate_resources(self.source, self.target, on_progress=progress)
        self.assertEqual(sentinel.read_bytes(), b'new current resources')
        self.assertEqual(list(self.target.parent.glob('.resources.migration-*')), [])
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def test_notification_error_after_publication_is_reported_as_migrated(self):
        def progress(value):
            if value['stage'] == 'migrated':
                raise RuntimeError('closed notification consumer')
        result = migration.migrate_resources(self.source, self.target, on_progress=progress)
        self.assertTrue(result['migrated'])
        self.assertTrue(result['notificationFailed'])
        self.assertTrue(PackageView(self.target, self.base).validate_inventory())

    def test_cancellation_after_publication_reports_completed_migration(self):
        cancelled = threading.Event()
        def progress(value):
            if value['stage'] == 'migrated':
                cancelled.set()
        result = migration.migrate_resources(self.source, self.target,
            cancelled=cancelled.is_set, on_progress=progress)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(result['status'], 'migrated')
        self.assertTrue(PackageView(self.target, self.base).validate_inventory())

    def test_actual_cross_volume_copy_publishes_on_destination_volume(self):
        checkout = Path(__file__).resolve().parents[1]
        if os.stat(checkout).st_dev == os.stat(self.source).st_dev:
            self.skipTest('checkout and temporary source are on the same volume')
        destination_temp = tempfile.TemporaryDirectory(prefix='migration-cross-volume-', dir=checkout)
        self.addCleanup(destination_temp.cleanup)
        target = Path(destination_temp.name) / 'resources'
        result = migration.migrate_resources(self.source, target)
        self.assertEqual(result['status'], 'migrated')
        self.assertNotEqual(os.stat(self.source).st_dev, os.stat(target).st_dev)
        self.assertEqual((target / 'catalog.json').read_bytes(),
                         (self.source / 'catalog.json').read_bytes())
        self.assertTrue(PackageView(target, self.history).validate_inventory())
        self.assertEqual(self.bytes_at(self.source), self.originals)

    def test_other_process_holding_old_store_prevents_migration(self):
        code = ('import sys\nfrom resource_transaction import resource_runtime_lease\n'
                'with resource_runtime_lease(sys.argv[1]):\n'
                ' print("held",flush=True)\n sys.stdin.readline()\n')
        process = subprocess.Popen([sys.executable, '-u', '-c', code, str(self.source)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), 'held')
            with self.assertRaisesRegex(ValueError, 'resource-busy'):
                migration.migrate_resources(self.source, self.target)
            self.assertFalse(self.target.exists())
            self.assertEqual(self.bytes_at(self.source), self.originals)
        finally:
            process.communicate('\n', timeout=15)
        self.assertEqual(process.returncode, 0)

    def test_parseable_catalog_with_wrong_selected_component_is_rejected(self):
        bad = dict(self.document, displays=[wire(self.base)])
        # Replace only this fixture's active catalog name; retain its old commit
        # witness so this is a real superseding file, not a changed witness.
        catalog = self.source / 'catalog.json'
        catalog.unlink()
        catalog.write_text(json.dumps(bad), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'display-component-type-mismatch'):
            migration.migrate_resources(self.source, self.target)
        self.assertFalse(self.target.exists())

    def test_cleanup_uses_real_removal_transactions_and_retains_shared_root(self):
        migration.migrate_resources(self.source, self.target)
        before_target = self.bytes_at(self.target)
        unknown = self.source / 'my-original-package.zip'
        unknown.write_bytes(b'keep source archive')
        result = migration.cleanup_migrated_resources(self.source, self.target)
        self.assertEqual(len(result['removedVersions']), 3, result)
        self.assertTrue(result['sourceRetained'])
        self.assertEqual(result['status'], 'cleanup-partial')
        self.assertTrue(self.source.is_dir())
        self.assertEqual(unknown.read_bytes(), b'keep source archive')
        self.assertEqual(self.bytes_at(self.target), before_target)
        self.assertEqual(CatalogSnapshot(self.source).contexts, {})
        self.assertTrue((self.source / 'transactions').is_dir())
        self.assert_archives()

    def test_cleanup_preserves_modified_unknown_and_not_copied_versions(self):
        migration.migrate_resources(self.source, self.target)
        modified = self.package(self.source, self.author) / 'data/display/index.json'
        modified.write_bytes(b'modified after migration')
        unknown = self.package(self.source, self.history) / 'user-added.txt'
        unknown.write_bytes(b'preserve entire version')
        _, fresh = imported(self.source, 'not-copied', season='s1')
        result = migration.cleanup_migrated_resources(self.source, self.target)
        self.assertEqual([row['packageId'] for row in result['removedVersions']], ['base-package'], result)
        self.assertEqual(len(result['preserved']), 3, result)
        self.assertEqual(modified.read_bytes(), b'modified after migration')
        self.assertEqual(unknown.read_bytes(), b'preserve entire version')
        self.assertTrue(PackageView(self.source, fresh[0]).validate_inventory())

    def test_cleanup_never_deletes_source_when_target_copy_is_damaged(self):
        migration.migrate_resources(self.source, self.target)
        payload = self.package(self.target, self.history) / 'data/base/index.json'
        payload.write_bytes(b'bad target copy')
        result = migration.cleanup_migrated_resources(self.source, self.target)
        self.assertEqual(len(result['removedVersions']), 2, result)
        self.assertTrue(PackageView(self.source, self.history).validate_inventory())
        self.assertTrue(any('saved-history' in row['path'] for row in result['preserved']))

    def test_cleanup_pending_and_pre_cancel_preserve_all_old_versions(self):
        migration.migrate_resources(self.source, self.target)
        pending = self.source / 'transactions/pending-removal.json'
        pending.write_bytes(b'{"incomplete":true}')
        original = self.bytes_at(self.source)
        result = migration.cleanup_migrated_resources(self.source, self.target)
        self.assertEqual(result['removedVersions'], [])
        self.assertEqual(result['status'], 'cleanup-incomplete')
        self.assertEqual(self.bytes_at(self.source), original)
        pending.unlink()
        result = migration.cleanup_migrated_resources(self.source, self.target, cancelled=lambda: True)
        self.assertEqual(result['removedVersions'], [])
        self.assertTrue(result['cancelled'])

    def test_cleanup_notification_failure_does_not_erase_completed_outcomes(self):
        migration.migrate_resources(self.source, self.target)
        def progress(value):
            raise RuntimeError('closed notification consumer')
        result = migration.cleanup_migrated_resources(self.source, self.target, on_progress=progress)
        self.assertEqual(result['status'], 'cleanup-complete', result)
        self.assertEqual(len(result['removedVersions']), 3)
        self.assertEqual(result['preserved'], [])
        self.assertTrue(result['notificationFailed'])


if __name__ == '__main__':
    unittest.main()
