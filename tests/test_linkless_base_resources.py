"""Synthetic bytes, real filesystem moves/leases and actual process termination.

os.link alone is faulted as an unsupported filesystem operation. Atomic rename,
hashing, file identity, journaling, rollback and recovery are production code.
Windows executes the native same-handle move; POSIX coverage is not a substitute.
"""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('app', 'tools', 'tests')]
import managed_base_resources as base
import map_package_upgrade as transaction
import qt_startup_diagnostics as diagnostics
import resource_transaction as lease
import test_managed_base_resources as fixtures


def unsupported(*args, **kwargs):
    raise OSError(errno.EOPNOTSUPP, 'fixture filesystem has no hard links')


class LinklessBaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManagedBaseResourcesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.project()
        self.root = self.fixture.target

    def repair(self):
        with patch.object(os, 'link', side_effect=unsupported):
            return base.repair_managed_base_resources(self.root)

    def recover(self):
        with patch.object(os, 'link', side_effect=unsupported):
            return base.recover_managed_base_resources(self.root)

    def before_upgrade(self):
        self.repair()
        before = {name: (path.read_bytes(), transaction._identity(path.stat()))
                  for name in [base.STATE, *('_internal/' + name for name in self.fixture.resources)]
                  for path in [self.root / name]}
        self.fixture.upgrade_candidate()
        return before

    def assert_original(self, before):
        for name, (data, identity) in before.items():
            path = self.root / name
            self.assertEqual(path.read_bytes(), data, name)
            self.assertEqual(transaction._identity(path.stat()), identity, name)

    def crash_repair(self, after='new'):
        script = '''
import errno, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import managed_base_resources as base
import map_package_upgrade as transaction
root = Path(sys.argv[2])
def unsupported(*a, **k):
    raise OSError(errno.EOPNOTSUPP, 'unsupported fixture')
os.link = unsupported
real_move = transaction._move_owned
def move(source, target, row, identity, **kwargs):
    real_move(source, target, row, identity, **kwargs)
    relative = Path(source).relative_to(root).parts
    if 'base-repairs' in relative and sys.argv[3] in relative:
        os._exit(23)
transaction._move_owned = move
if sys.argv[3] == 'backup':
    base.recover_managed_base_resources(root)
else:
    base.repair_managed_base_resources(root)
'''
        child = subprocess.run([sys.executable, '-B', '-c', script, str(ROOT / 'app'), str(self.root), after],
                               capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 23, child.stderr)

    def test_real_move_installs_complete_base_then_noop(self):
        result = self.repair()
        self.assertEqual(result['publication'], 'move-v1')
        self.assertEqual(result['changed'], len(self.fixture.resources))
        for name in self.fixture.resources:
            self.assertEqual((self.root / '_internal' / name).read_bytes(),
                             (self.fixture.stage / '_internal' / name).read_bytes())
        self.assertFalse((self.root / base.PENDING).exists())
        self.assertEqual(self.repair()['status'], 'healthy')

    def test_real_move_upgrades_known_files_and_preserves_unknown_data(self):
        self.before_upgrade()
        user = self.root / '_internal/assets/custom-maps/private.png'
        user.parent.mkdir(parents=True, exist_ok=True)
        user.write_bytes(b'user data')
        identity = transaction._identity(user.stat())
        self.assertEqual(self.repair()['publication'], 'move-v1')
        for name in self.fixture.resources:
            self.assertEqual((self.root / '_internal' / name).read_bytes(), ('next:' + name).encode())
        self.assertEqual(user.read_bytes(), b'user data')
        self.assertEqual(transaction._identity(user.stat()), identity)

    def test_actual_process_loss_then_recovery_preserves_original_objects(self):
        before = self.before_upgrade()
        self.crash_repair()
        pointer = json.loads((self.root / base.PENDING).read_text())
        journal = json.loads((self.root / base.HISTORY / pointer['transactionId'] / 'journal.json').read_text())
        self.assertEqual(journal['publication'], 'move-v1')
        self.assertEqual(self.recover()['status'], 'rolled-back')
        self.assert_original(before)
        self.assertEqual(self.repair()['status'], 'repaired')

    def test_actual_process_loss_during_rollback_can_resume(self):
        before = self.before_upgrade()
        self.crash_repair()
        self.crash_repair(after='backup')
        self.assertEqual(self.recover()['status'], 'rolled-back')
        self.assert_original(before)

    def test_same_bytes_different_object_after_crash_is_preserved(self):
        self.before_upgrade()
        self.crash_repair()
        path = self.root / '_internal/difficult-native-manifest.json'
        original = transaction._identity(path.stat())
        replacement = self.root / 'external-replacement'
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)
        self.assertNotEqual(transaction._identity(path.stat()), original)
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-rollback-incomplete'):
            self.recover()
        self.assertEqual(path.read_bytes(), b'next:difficult-native-manifest.json')
        self.assertTrue((self.root / base.PENDING).exists())

    def test_modified_object_after_crash_is_preserved(self):
        self.before_upgrade()
        self.crash_repair()
        path = self.root / '_internal/difficult-native-manifest.json'
        path.write_bytes(b'modified by another writer')
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-rollback-incomplete'):
            self.recover()
        self.assertEqual(path.read_bytes(), b'modified by another writer')

    def test_ordinary_failure_rolls_back_before_returning(self):
        before = self.before_upgrade()
        real_move = transaction._move_owned
        failed = False
        def move(source, target, row, identity, **kwargs):
            nonlocal failed
            real_move(source, target, row, identity, **kwargs)
            if not failed and 'base-repairs' in Path(source).parts and 'new' in Path(source).parts:
                failed = True
                raise OSError(errno.EIO, 'fixture write failure after move')
        with patch.object(transaction, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(OSError, 'fixture write failure'):
                self.repair()
        self.assert_original(before)
        self.assertFalse((self.root / base.PENDING).exists())

    def test_new_target_race_never_overwrites_external_object(self):
        self.before_upgrade()
        real_move = transaction._move_owned
        attacked = []
        def move(source, target, row, identity, **kwargs):
            if not attacked and 'base-repairs' in Path(source).parts and 'new' in Path(source).parts:
                Path(target).write_bytes(b'new external object')
                attacked.append(Path(target))
            return real_move(source, target, row, identity, **kwargs)
        with patch.object(transaction, '_move_owned', side_effect=move):
            with self.assertRaisesRegex(base.ManagedBaseResourceError, 'rollback-incomplete'):
                self.repair()
        self.assertEqual(attacked[0].read_bytes(), b'new external object')
        self.assertTrue((self.root / base.PENDING).exists())

    def test_unavailable_atomic_move_blocks_before_journal_or_resource_changes(self):
        with patch.object(transaction, '_move_owned', side_effect=OSError(errno.ENOSYS, 'unavailable')):
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'resource-atomic-move-unavailable'):
                self.repair()
        self.assertFalse((self.root / base.PENDING).exists())
        self.assertFalse((self.root / base.HISTORY).exists())
        self.assertFalse((self.root / '_internal/difficult-native-manifest.json').exists())

    def test_permission_and_unknown_link_errors_do_not_silently_downgrade(self):
        for error in (PermissionError(errno.EACCES, 'denied'), OSError('unknown filesystem error')):
            with self.subTest(error=type(error).__name__), patch.object(os, 'link', side_effect=error):
                with self.assertRaisesRegex(lease.ResourceTransactionError, 'resource-hardlinks-unavailable'):
                    base.repair_managed_base_resources(self.root)
        self.assertFalse((self.root / base.HISTORY).exists())

    def test_reported_windows_invalid_function_uses_verified_move_protocol(self):
        error = OSError(errno.EINVAL, 'invalid function fixture')
        error.winerror = 1
        with patch.object(os, 'link', side_effect=error):
            self.assertEqual(base.repair_managed_base_resources(self.root)['publication'], 'move-v1')

    def test_false_success_capability_adapter_is_rejected(self):
        with patch.object(transaction, '_move_owned', return_value=None):
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'resource-atomic-move-unavailable'):
                self.repair()
        self.assertFalse((self.root / base.HISTORY).exists())

    def test_cross_volume_target_blocks_before_probe(self):
        directory = self.root / '_internal/assets'
        directory.mkdir(parents=True, exist_ok=True)
        (self.root / 'work/map-packages').mkdir(parents=True, exist_ok=True)
        (directory / 'preserved.bin').write_bytes(b'unchanged resource')
        def snapshot():
            return {path.relative_to(self.root).as_posix():
                    (transaction._identity(path.stat()), path.stat().st_mtime_ns,
                     path.read_bytes() if path.is_file() else None)
                    for path in self.root.rglob('*')}
        before = snapshot()
        original = Path.stat
        class OtherVolumeStat:
            def __init__(self, info):
                self.info = info
                self.st_dev = info.st_dev + 1

            def __getattr__(self, name):
                # Rebuilding the stat tuple loses Windows reparse attributes
                # and nanosecond/birth-time fields. Change only the device.
                return getattr(self.info, name)

        actual = original(directory)
        alternate = OtherVolumeStat(actual)
        self.assertNotEqual(alternate.st_dev, actual.st_dev)
        for name in dir(actual):
            if name.startswith('st_') and name != 'st_dev':
                self.assertEqual(getattr(alternate, name), getattr(actual, name), name)
        def other_volume(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            return OtherVolumeStat(info) if path == directory else info
        with patch.object(Path, 'stat', new=other_volume), \
             patch.object(lease.tempfile, 'TemporaryDirectory', side_effect=AssertionError('must not probe')) as probe, \
             patch.object(os, 'link', side_effect=AssertionError('must not link')) as link, \
             patch.object(transaction, '_write_new', side_effect=AssertionError('must not write')) as write, \
             patch.object(transaction, '_move_owned', side_effect=AssertionError('must not move')) as move:
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'resource-cross-volume-target') as caught:
                lease.preflight_resource_publication(self.root, ['_internal/assets/new'])
        self.assertEqual(caught.exception.code, 'resource-cross-volume-target')
        self.assertEqual(caught.exception.details, {'path': '_internal/assets/new'})
        for operation in (probe, link, write, move):
            operation.assert_not_called()
        self.assertEqual(snapshot(), before)

    def test_commit_journal_write_uncertainty_never_restores_previous_version(self):
        self.before_upgrade()
        original = base._write_journal
        def write(directory, journal):
            original(directory, journal)
            if journal['status'] == 'committed':
                raise OSError(errno.EIO, 'commit sync outcome uncertain')
        with patch.object(base, '_write_journal', side_effect=write):
            with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-cleanup-incomplete'):
                self.repair()
        self.assertEqual(self.recover()['status'], 'committed')
        for name in self.fixture.resources:
            self.assertEqual((self.root / '_internal' / name).read_bytes(), ('next:' + name).encode())

    def test_corrupt_journal_never_authorizes_rollback(self):
        self.before_upgrade()
        self.crash_repair()
        pointer = json.loads((self.root / base.PENDING).read_text())
        directory = self.root / base.HISTORY / pointer['transactionId']
        journal = json.loads((directory / 'journal.json').read_text())
        journal['publication'] = 'unrecognized-mode'
        (directory / 'journal.json').write_text(json.dumps(journal))
        path = self.root / '_internal/difficult-native-manifest.json'
        before = path.read_bytes()
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'invalid-base-repair-journal'):
            self.recover()
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue((self.root / base.PENDING).exists())

    def test_pending_marker_same_byte_replacement_is_never_adopted(self):
        self.before_upgrade()
        self.crash_repair()
        path = self.root / base.PENDING
        foreign = self.root / 'foreign-marker'
        foreign.write_bytes(path.read_bytes())
        os.replace(foreign, path)
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-pending-pointer-changed'):
            self.recover()
        self.assertTrue(path.exists())
        self.assertEqual((self.root / '_internal/difficult-native-manifest.json').read_bytes(),
                         b'next:difficult-native-manifest.json')

    def test_compact_terminal_marker_keeps_original_identity_guard(self):
        with patch.object(base, '_clear_pending', side_effect=OSError('marker busy')):
            with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-cleanup-incomplete'):
                self.repair()
        pointer = self.root / base.PENDING
        transaction_id = json.loads(pointer.read_text())['transactionId']
        directory, journal = base._load_history(self.root, transaction_id)
        self.assertEqual(journal['schema'], base.RETIRED_SCHEMA)
        self.assertEqual(journal['pendingStamp']['identity'], transaction._identity(pointer.stat()))
        self.assertLessEqual((directory / 'journal.json').stat().st_size, base.MAX_RETIRED_BYTES)
        foreign = self.root / 'replacement-marker'
        foreign.write_bytes(pointer.read_bytes())
        os.replace(foreign, pointer)
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-pending-pointer-changed'):
            self.recover()
        self.assertTrue(pointer.exists())

    def test_complete_marker_and_stamp_precede_first_active_mutation(self):
        original = transaction._move_owned
        observed = []
        def move(source, target, row, identity, **kwargs):
            if Path(target) == self.root / base.PENDING:
                self.assertFalse(Path(target).exists())
                value = json.loads(Path(source).read_text())
                _, journal = base._load_history(self.root, value['transactionId'])
                self.assertEqual(journal['pendingStamp']['identity'], transaction._identity(Path(source).stat()))
                self.assertEqual(journal['status'], 'preparing')
                self.assertFalse((self.root / '_internal/difficult-native-manifest.json').exists())
                observed.append(True)
            return original(source, target, row, identity, **kwargs)
        with patch.object(transaction, '_move_owned', side_effect=move):
            self.repair()
        self.assertEqual(observed, [True])

    def test_prepublication_marker_interruption_reuses_only_persisted_source(self):
        original = transaction._move_owned
        def stop(source, target, row, identity, **kwargs):
            if Path(target) == self.root / base.PENDING:
                raise KeyboardInterrupt('before complete marker move')
            return original(source, target, row, identity, **kwargs)
        with patch.object(transaction, '_move_owned', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                self.repair()
        self.assertFalse((self.root / base.PENDING).exists())
        directory = next((self.root / base.HISTORY).iterdir())
        staged = directory / 'pending-pointer.json'
        identity = transaction._identity(staged.stat())
        # A startup notices unresolved history, arms the exact recorded marker,
        # and blocks until the explicit recovery runs; it does not guess success.
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-cleanup-incomplete'):
            self.repair()
        self.assertEqual(transaction._identity((self.root / base.PENDING).stat()), identity)
        self.assertEqual(self.recover()['status'], 'rolled-back')
        self.assertFalse((self.root / '_internal/difficult-native-manifest.json').exists())

    def test_terminal_marker_rearm_and_partial_rearm_pruning_remain_bounded(self):
        first = self.repair()
        directory, _ = base._load_history(self.root, first['transactionId'])
        with patch.object(os, 'link', side_effect=unsupported), lease.resource_write_lock(self.root):
            base._ensure_pending(self.root, first['transactionId'])
        self.assertEqual(self.recover()['status'], 'committed')
        original = transaction._move_owned
        def stop(source, target, row, identity, **kwargs):
            if Path(target) == self.root / base.PENDING:
                raise KeyboardInterrupt('rearm marker not yet published')
            return original(source, target, row, identity, **kwargs)
        with patch.object(transaction, '_move_owned', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                base._ensure_pending(self.root, first['transactionId'])
        self.assertTrue((directory / 'pending-pointer.json').exists())
        self.fixture.upgrade_candidate()
        with patch.object(base, 'MAX_RETIRED_RECORDS', 1):
            self.repair()
        self.assertFalse(directory.exists())
        self.assertEqual(self.repair()['status'], 'healthy')

    def test_changed_private_marker_before_publication_is_preserved(self):
        original = transaction._move_owned
        def stop(source, target, row, identity, **kwargs):
            if Path(target) == self.root / base.PENDING:
                raise KeyboardInterrupt('staged marker')
            return original(source, target, row, identity, **kwargs)
        with patch.object(transaction, '_move_owned', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                self.repair()
        directory = next((self.root / base.HISTORY).iterdir())
        private = directory / 'pending-pointer.json'
        replacement = self.root / 'replacement-private-marker'
        replacement.write_bytes(private.read_bytes())
        os.replace(replacement, private)
        identity = transaction._identity(private.stat())
        with self.assertRaisesRegex(base.ManagedBaseResourceError, 'base-cleanup-incomplete'):
            self.repair()
        self.assertEqual(transaction._identity(private.stat()), identity)
        self.assertFalse((self.root / base.PENDING).exists())

    def test_linked_ancestor_is_rejected_before_any_capability_probe(self):
        elsewhere = self.fixture.folder / 'elsewhere'
        elsewhere.mkdir()
        linked = self.root / 'linked'
        try:
            linked.symlink_to(elsewhere, target_is_directory=True)
        except OSError:
            self.skipTest('symlink creation unavailable')
        with patch.object(os, 'link', side_effect=AssertionError('must not probe')):
            with self.assertRaisesRegex(lease.ResourceTransactionError, 'resource-linked-target'):
                lease.preflight_resource_publication(self.root, ['linked/new'])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_other_process_cannot_enter_during_linkless_repair(self):
        from resource_transaction import resource_runtime_lease
        with resource_runtime_lease(self.root):
            self.repair()
            script = 'import sys; sys.path.insert(0,sys.argv[1]); from resource_transaction import resource_runtime_lease; resource_runtime_lease(sys.argv[2])'
            child = subprocess.run([sys.executable, '-c', script, str(ROOT / 'app'), str(self.root)],
                                   capture_output=True, text=True, timeout=20)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('resource-busy', child.stderr)

    def test_private_diagnostic_reports_only_fixed_location_and_capabilities(self):
        with patch.object(os, 'link', side_effect=OSError(errno.EINVAL, 'C:/Users/private-user')):
            with patch.object(transaction, '_move_owned', side_effect=OSError(errno.ENOSYS, 'private-volume')):
                with self.assertRaises(lease.ResourceTransactionError) as caught:
                    lease.preflight_resource_publication(self.root, ['_internal/assets/test'])
        code, text = diagnostics._details(caught.exception, 'run-client')
        self.assertEqual(code, 'QT-STARTUP-RESOURCE')
        for item in ('resourceLocation=work/map-packages', 'resourceSubstage=atomic-move-probe',
                     'filesystem.hardlinks=unsupported', 'filesystem.atomicNoReplace=unavailable'):
            self.assertIn(item, text)
        for item in ('private-user', 'private-volume', str(self.root)):
            self.assertNotIn(item, text)
        caught.exception.details = dict(resourceLocation='C:/Users/private-user',
            resourceSubstage=['bad'], filesystemCapabilities=dict(hardlinks={'private': 'bad'}))
        self.assertNotIn('private', diagnostics._details(caught.exception, 'run-client')[1])


if __name__ == '__main__':
    unittest.main()
