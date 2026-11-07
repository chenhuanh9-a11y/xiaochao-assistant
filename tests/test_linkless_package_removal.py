"""No-link removal uses real moves, journals and subprocess termination.

Installed receipts/assets are synthetic fixture packages. Their installation
uses its own separately tested protocol; only removal runs with links disabled.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('app', 'tools', 'tests')]
import map_package_removal as removal
import map_package_upgrade as upgrade
import map_package_recovery_plan as recovery
import resource_transaction as lease
import test_map_package_removal as fixtures
from test_live_author_import import SUJU, WANG


def unsupported(*args, **kwargs):
    raise OSError(errno.EOPNOTSUPP, 'fixture filesystem has no hard links')


class LinklessRemovalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PackageRemovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.install()
        self.root = self.fixture.root
        self.original = self.fixture.snapshot()
        self.identities = {name: upgrade._identity((self.root / name).stat()) for name in self.original}

    def remove(self, **kwargs):
        with patch.object(os, 'link', side_effect=unsupported):
            return self.fixture.remove(**kwargs)

    def recover(self):
        with patch.object(os, 'link', side_effect=unsupported), lease.resource_write_lock(self.root):
            plan = recovery.inspect_locked(self.root)
            return recovery.execute_locked(self.root, plan, 'recover')

    def assert_original(self):
        self.assertEqual(self.fixture.snapshot(), self.original)
        for name, identity in self.identities.items():
            self.assertEqual(upgrade._identity((self.root / name).stat()), identity, name)

    def interrupt(self):
        original = upgrade._move_owned
        def move(source, target, row, identity, **kwargs):
            original(source, target, row, identity, **kwargs)
            if Path(target).parent.name == 'objects':
                raise KeyboardInterrupt('fixture process interruption')
        with patch.object(upgrade, '_move_owned', side_effect=move):
            with self.assertRaises(KeyboardInterrupt):
                self.remove()

    def child_exit(self, restore=False):
        script = '''
import errno, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import map_package_removal as removal
import map_package_upgrade as upgrade
from resource_transaction import resource_write_lock
root = Path(sys.argv[2]); restore = sys.argv[3] == 'restore'
def unsupported(*a, **k):
    raise OSError(errno.EOPNOTSUPP, 'unsupported fixture')
os.link = unsupported
original = upgrade._move_owned
def move(source, target, row, identity, **kwargs):
    original(source, target, row, identity, **kwargs)
    stop = Path(source).parent.name == 'objects' if restore else Path(target).parent.name == 'objects'
    if stop:
        os._exit(41)
upgrade._move_owned = move
if restore:
    with resource_write_lock(root):
        removal.recover_pending_removal_locked(root)
else:
    plan = removal.inspect_package_removal(root, 'nightmare-suju')
    removal.remove_packages(root, 'nightmare-suju', application_idle=True, expected_plan_sha256=plan['planSha256'])
'''
        child = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT / 'app'), str(self.root),
                                'restore' if restore else 'remove'], capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 41, child.stderr)

    def test_remove_owned_files_and_cleanup_with_no_links(self):
        unknown = self.root / '_internal/assets/suju/user.png'
        unknown.parent.mkdir(parents=True, exist_ok=True)
        unknown.write_bytes(b'unknown owned by user')
        result = self.remove()
        self.assertEqual(result['status'], 'packages-removed')
        self.assertFalse((self.root / SUJU).exists())
        self.assertFalse(self.fixture.f.receipt().exists())
        self.assertEqual(unknown.read_bytes(), b'unknown owned by user')
        self.assertFalse((self.root / removal.PENDING).exists())
        self.assertFalse(list((self.root / removal.STATE).glob('*/objects/*')))
        self.fixture.f.assert_base_preserved()
        self.assertEqual(self.remove()['status'], 'already-removed')

    def test_modified_payload_and_receipt_provenance_are_preserved(self):
        (self.root / SUJU).write_bytes(b'changed by user')
        self.assertEqual(self.remove()['status'], 'preserved-items')
        self.assertEqual((self.root / SUJU).read_bytes(), b'changed by user')
        self.assertTrue(self.fixture.f.receipt().exists())

    def test_cancellation_after_first_move_restores_original_objects(self):
        cancelled = threading.Event()
        original = upgrade._move_owned
        def move(source, target, row, identity, **kwargs):
            original(source, target, row, identity, **kwargs)
            if Path(target).parent.name == 'objects':
                cancelled.set()
        with patch.object(upgrade, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(ValueError, 'import-cancelled'):
                self.remove(cancelled=cancelled.is_set)
        self.assert_original()
        self.assertFalse((self.root / removal.PENDING).exists())

    def test_ordinary_error_after_move_restores_originals(self):
        original = upgrade._move_owned
        def move(source, target, row, identity, **kwargs):
            original(source, target, row, identity, **kwargs)
            if Path(target).parent.name == 'objects':
                raise OSError(errno.EIO, 'error after quarantine move')
        with patch.object(upgrade, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(OSError, 'error after quarantine move'):
                self.remove()
        self.assert_original()

    def test_actual_process_exit_is_recovered_via_dispatcher(self):
        self.child_exit()
        self.assertTrue((self.root / removal.PENDING).exists())
        with self.assertRaisesRegex(lease.ResourceTransactionError, 'removal-recovery-required'):
            lease.assert_resources_ready(self.root)
        self.assertEqual(self.recover()['status'], 'removal-rolled-back')
        self.assert_original()

    def test_actual_process_exit_during_restore_can_resume(self):
        self.child_exit()
        self.child_exit(restore=True)
        self.assertEqual(self.recover()['status'], 'removal-rolled-back')
        self.assert_original()

    def test_same_byte_new_object_at_old_path_is_not_deleted_or_overwritten(self):
        self.interrupt()
        path = self.root / SUJU
        path.write_bytes(self.original[SUJU])
        identity = upgrade._identity(path.stat())
        self.assertNotEqual(identity, self.identities[SUJU])
        with self.assertRaisesRegex(ValueError, 'removal-rollback-conflict'):
            self.recover()
        self.assertEqual(path.read_bytes(), self.original[SUJU])
        self.assertEqual(upgrade._identity(path.stat()), identity)
        self.assertTrue((self.root / removal.PENDING).exists())

    def test_replaced_pending_marker_with_same_bytes_is_not_adopted(self):
        self.interrupt()
        pointer = self.root / removal.PENDING
        replacement = self.root / 'external-marker'
        replacement.write_bytes(pointer.read_bytes())
        os.replace(replacement, pointer)
        with self.assertRaisesRegex(ValueError, 'removal-pending-pointer-changed'):
            self.recover()
        self.assertFalse((self.root / SUJU).exists())
        self.assertTrue(pointer.exists())

    def test_changed_quarantine_is_preserved_with_gate(self):
        self.interrupt()
        directory, _ = removal._load_pending(self.root)
        retained = directory / 'objects/0'
        retained.write_bytes(b'changed private object')
        with self.assertRaisesRegex(ValueError, 'removal-rollback-source-missing-or-changed'):
            self.recover()
        self.assertEqual(retained.read_bytes(), b'changed private object')
        self.assertFalse((self.root / SUJU).exists())

    def test_new_target_before_commit_remains_and_blocks_restore(self):
        original = upgrade._write_journal
        def write(directory, journal):
            original(directory, journal)
            if directory.parent.name == 'removals' and journal['status'] == 'prepared':
                (self.root / SUJU).write_bytes(b'foreign concurrent file')
        with patch.object(upgrade, '_write_journal', side_effect=write):
            with self.assertRaisesRegex(ValueError, 'removal-rollback-conflict'):
                self.remove()
        self.assertEqual((self.root / SUJU).read_bytes(), b'foreign concurrent file')
        self.assertTrue((self.root / removal.PENDING).exists())

    def test_committed_journal_sync_error_never_restores_deleted_files(self):
        original = upgrade._write_journal
        def write(directory, journal):
            original(directory, journal)
            if directory.parent.name == 'removals' and journal['status'] == 'committed':
                raise OSError(errno.EIO, 'committed journal write uncertain')
        with patch.object(upgrade, '_write_journal', side_effect=write):
            with self.assertRaisesRegex(ValueError, 'removal-committed-cleanup-required'):
                self.remove()
        self.assertFalse((self.root / SUJU).exists())
        self.assertEqual(self.recover()['status'], 'removal-committed')
        self.assertFalse((self.root / SUJU).exists())

    def test_cancellation_after_durable_commit_does_not_reverse_removal(self):
        original = upgrade._write_journal
        cancelled = threading.Event()
        def write(directory, journal):
            original(directory, journal)
            if directory.parent.name == 'removals' and journal['status'] == 'committed':
                cancelled.set()
        with patch.object(upgrade, '_write_journal', side_effect=write):
            self.assertEqual(self.remove(cancelled=cancelled.is_set)['status'], 'packages-removed')
        self.assertFalse((self.root / SUJU).exists())

    def test_partial_terminal_cleanup_preserves_later_user_data(self):
        original = removal.files._rollback_published
        failed = []
        def delete(path, identity, digest):
            if Path(path).parent.name == 'objects' and Path(path).name == '1' and not failed:
                failed.append(True)
                return False
            return original(path, identity, digest)
        with patch.object(removal.files, '_rollback_published', side_effect=delete):
            with self.assertRaisesRegex(ValueError, 'removal-committed-cleanup-required'):
                self.remove()
        (self.root / SUJU).write_bytes(b'user data created after commit')
        self.assertEqual(self.recover()['status'], 'removal-committed')
        self.assertEqual((self.root / SUJU).read_bytes(), b'user data created after commit')
        self.assertFalse((self.root / removal.PENDING).exists())

    def test_unknown_quarantine_prevents_false_success(self):
        original = removal._cleanup_objects
        def cleanup(root, directory, journal):
            (directory / 'objects/unknown').write_bytes(b'not transaction data')
            return original(root, directory, journal)
        with patch.object(removal, '_cleanup_objects', side_effect=cleanup):
            with self.assertRaisesRegex(ValueError, 'removal-committed-cleanup-required'):
                self.remove()
        directory, _ = removal._load_pending(self.root)
        self.assertEqual((directory / 'objects/unknown').read_bytes(), b'not transaction data')
        self.assertTrue((self.root / removal.PENDING).exists())

    def test_linkless_upgrade_then_remove_includes_verified_publication_history(self):
        with patch.object(os, 'link', side_effect=unsupported):
            result = self.fixture.install({SUJU: b'new linkless version'}, name='new-linkless.zip')
            history = self.root / upgrade.STATE / 'upgrades' / result['upgradeTransactionId']
            self.assertTrue((history / 'pending-pointer.json.publication.json').exists())
            plan = removal.inspect_package_removal(self.root, 'nightmare-suju')
            self.assertTrue(any(row['path'].endswith('pending-pointer.json.publication.json') for row in plan['entries']))
            self.assertEqual(self.fixture.remove()['status'], 'packages-removed')
        self.assertFalse(history.exists())
        self.assertFalse((self.root / SUJU).exists())
        self.fixture.f.assert_base_preserved()

    def retained_addition_history(self):
        original = removal.files._rollback_published
        def leave_terminal_metadata(path, identity, digest):
            if (any(part.startswith('staging-') for part in path.parts)
                    and path.name in {'addition-journal.json', upgrade.ADDITIONS_OUTCOME,
                                      'addition-journal.json.publication.json'}
                    and not (self.root / upgrade.ADDITIONS_PENDING).exists()):
                return False
            return original(path, identity, digest)
        with patch.object(os, 'link', side_effect=unsupported), \
             patch.object(removal.files, '_rollback_published', side_effect=leave_terminal_metadata):
            self.fixture.install({WANG: b'new linkless package'}, package='nightmare-wang-qingxin', name='new-package.zip')
        stages = list((self.root / upgrade.STATE).glob('staging-*'))
        self.assertEqual(len(stages), 1)
        self.assertFalse((self.root / upgrade.ADDITIONS_PENDING).exists())
        self.assertEqual({p.name for p in stages[0].iterdir()},
                         {'addition-journal.json', upgrade.ADDITIONS_OUTCOME, 'addition-journal.json.publication.json'})
        return stages[0]

    def test_terminal_linkless_addition_history_is_removed_with_package(self):
        stage = self.retained_addition_history()
        with patch.object(os, 'link', side_effect=unsupported):
            self.assertEqual(self.fixture.remove(package='nightmare-wang-qingxin')['status'], 'packages-removed')
        self.assertFalse(stage.exists())
        self.assertFalse((self.root / WANG).exists())
        self.assertTrue((self.root / SUJU).exists())

    def test_missing_terminal_proof_is_not_treated_as_owned_addition_history(self):
        stage = self.retained_addition_history()
        (stage / upgrade.ADDITIONS_OUTCOME).unlink()
        before = self.fixture.snapshot()
        # An uncompleted preparation is now discovered before any resource
        # mutation. The deletion planner must still require its own terminal
        # proof even when that separate readiness gate is isolated in this test.
        with self.assertRaisesRegex(ValueError, 'additions-recovery-required'):
            self.fixture.remove(package='nightmare-wang-qingxin')
        with patch.object(removal, 'assert_resources_ready', return_value=None):
            with self.assertRaisesRegex(ValueError, 'removal-additions-terminal-proof-required'):
                removal.inspect_package_removal(self.root, 'nightmare-wang-qingxin')
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertTrue((self.root / WANG).exists())
        self.assertTrue(stage.exists())

    def test_changed_addition_publication_proof_blocks_removal_before_mutation(self):
        stage = self.retained_addition_history()
        proof = stage / 'addition-journal.json.publication.json'
        value = json.loads(proof.read_text())
        value['identity'][1] += 1
        proof.write_text(json.dumps(value))
        # Target is already retired; its identity alone is not delete authority.
        # Binding the retained object's identity must still be enforced.
        value['retainedIdentity'][1] += 1
        proof.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'metadata-publication-retained-changed'):
            self.fixture.remove(package='nightmare-wang-qingxin')
        self.assertTrue((self.root / WANG).exists())
        self.assertTrue((self.root / SUJU).exists())


class LinklessSnapshotRemovalTests(unittest.TestCase):
    def setUp(self):
        import test_author_snapshot_removal as snapshot_fixtures
        self.fixture = snapshot_fixtures.SnapshotRemovalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.links = patch.object(os, 'link', side_effect=unsupported)
        self.links.start()
        self.addCleanup(self.links.stop)

    def test_current_old_and_deactivated_snapshot_proofs_are_not_base_ownership(self):
        import author_resource_snapshots as snapshots
        self.fixture.install()
        self.fixture.install(fmt='jpeg')
        with lease.resource_write_lock(self.root):
            plan = snapshots.inspect_deactivation(self.root, self.fixture.package)
            snapshots.deactivate_locked(self.root, plan)
        self.assertEqual(self.fixture.remove()['status'], 'packages-removed')
        self.assertFalse(snapshots.managed(self.root, self.fixture.package))
        self.assertFalse(list((self.root / snapshots.STORE).rglob('*.*')))
        self.fixture.assert_base()

    def test_snapshot_removal_cancellation_restores_original_version_objects(self):
        import author_resource_snapshots as snapshots
        self.fixture.install()
        self.fixture.install(fmt='jpeg')
        before = {p: (p.read_bytes(), upgrade._identity(p.stat()))
                  for p in (self.root / snapshots.STORE).rglob('*') if p.is_file()}
        cancelled = threading.Event()
        original = upgrade._move_owned
        def move(source, target, row, identity, **kwargs):
            original(source, target, row, identity, **kwargs)
            if Path(target).parent.name == 'objects':
                cancelled.set()
        with patch.object(upgrade, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(ValueError, 'import-cancelled'):
                self.fixture.remove(cancelled=cancelled.is_set)
        for path, (data, identity) in before.items():
            self.assertEqual(path.read_bytes(), data)
            self.assertEqual(upgrade._identity(path.stat()), identity)
        self.fixture.assert_base()

    def test_modified_old_snapshot_is_preserved_during_clear_all(self):
        import author_resource_snapshots as snapshots
        self.fixture.install()
        old = self.fixture.f.view().directory
        self.fixture.install(fmt='jpeg')
        image = next(old.rglob('*.png'))
        image.write_bytes(b'private edits in old version')
        result = self.fixture.remove(None)
        self.assertGreater(result['unverifiedPreservedCount'], 0)
        self.assertEqual(image.read_bytes(), b'private edits in old version')
        self.assertTrue((old / 'manifest.json').exists())
        self.fixture.assert_base()

    def test_restored_version_and_its_history_can_be_removed_without_base_claims(self):
        import importlib.util
        if importlib.util.find_spec('author_snapshot_history') is None:
            self.skipTest('history feature is supplied by the integration branch')
        import author_snapshot_history as history
        import author_resource_snapshots as snapshots
        first = self.fixture.install()
        self.fixture.install(fmt='jpeg')
        before = {path: upgrade._identity((self.root / path).stat()) for path in self.fixture.base}
        plan = history.inspect_restore(self.root, self.fixture.package, first['snapshotTransactionId'])
        restored = history.restore_version(self.root, self.fixture.package, plan['sourceTransactionId'],
            application_idle=True, expected_plan_sha256=plan['planSha256'])
        self.assertEqual(restored['status'], 'author-version-restored')
        self.assertEqual(snapshots.SnapshotPackage(self.root, self.fixture.package).activation['snapshotId'], first['snapshotId'])
        deletion = removal.inspect_package_removal(self.root, self.fixture.package)
        self.assertFalse({row['path'] for row in deletion['entries']} & set(self.fixture.base))
        self.assertEqual(self.fixture.remove()['status'], 'packages-removed')
        self.assertFalse(list((self.root / snapshots.STORE).rglob('*.*')))
        self.fixture.assert_base()
        for path, identity in before.items():
            self.assertEqual(upgrade._identity((self.root / path).stat()), identity)


if __name__ == '__main__':
    unittest.main()
