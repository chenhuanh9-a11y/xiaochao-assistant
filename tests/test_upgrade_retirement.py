"""Real fixture upgrades retire private payloads; never reopen a committed version."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

import test_map_package_upgrade as fixture
import map_package_upgrade as upgrade
import star_map_import as importer
import map_package_recovery_plan as plans
from resource_transaction import resource_write_lock, resource_read_lock, resource_runtime_lease, assert_resources_ready


class UpgradeRetirementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.installed_old()
        self.root = self.fixture.root

    def install(self):
        return importer.install_package(self.fixture.new, self.root, application_stopped=True)

    def legacy_install(self, archive=None):
        # Reproduce A066's exact ten-field journal and retained pins using the
        # real installer; only terminal cleanup is replaced by A066's pointer clear.
        def retain(root, directory, journal):
            upgrade._clear_pointer(root, journal['transactionId'], directory)
        with patch.object(upgrade, '_cleanup_upgrade', side_effect=retain):
            result = importer.install_package(archive or self.fixture.new, self.root, application_stopped=True)
        directory = self.root / upgrade.STATE / 'upgrades' / result['upgradeTransactionId']
        journal = json.loads((directory / 'journal.json').read_text('utf-8'))
        for key in ('receiptData', 'stage', 'stagedIdentities', 'cachedArchives'):
            journal.pop(key)
        upgrade._write_journal(directory, journal)
        return result, directory

    def retire(self):
        with resource_write_lock(self.root):
            return upgrade.retire_completed_upgrades(self.root)

    def assert_retired(self):
        self.assertFalse((self.root / upgrade.PENDING).exists())
        self.assertEqual(list((self.root / upgrade.STATE).glob('staging-*')), [])
        for path in (self.root / upgrade.STATE / 'upgrades').iterdir():
            self.assertFalse((path / 'new').exists())
            self.assertFalse((path / 'backup').exists())
            self.assertLessEqual((path / 'journal.json').stat().st_size, 1024)

    def test_success_leaves_no_private_resource_copies(self):
        result = self.install()
        directory = self.root / upgrade.STATE / 'upgrades' / result['upgradeTransactionId']
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), b'q90-route')
        self.assertFalse((directory / 'new').exists(), 'successful upgrade must retire its new pins')
        self.assertFalse((directory / 'backup').exists(), 'successful upgrade must retire its old backup')
        self.assert_retired()
        with resource_write_lock(self.root):
            self.assertEqual(plans.history_locked(self.root), [])
        with self.assertRaisesRegex(importer.ImportRejected, 'no-longer-rollbackable'):
            importer.rollback_package_upgrade(self.root, result['upgradeTransactionId'], application_stopped=True)

    def test_clear_pointer_failure_cannot_rollback_committed_resources(self):
        with patch.object(upgrade, '_clear_pointer', side_effect=OSError('fixture pointer cleanup denied')):
            with self.assertRaises(Exception):
                self.install()
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), b'q90-route',
                         'cleanup failure must never restore an older committed version')
        self.assertTrue((self.root / upgrade.PENDING).exists())
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'upgrade-committed')
        self.assertFalse((self.root / upgrade.PENDING).exists())
        self.assert_retired()

    def test_partial_payload_cleanup_restarts_without_resurrecting_later_deleted_active_files(self):
        original = importer._rollback_published
        removed = []
        def partial(path, identity, digest):
            if '/upgrades/' in path.as_posix() and ('/new/' in path.as_posix() or '/backup/' in path.as_posix()):
                if removed:
                    return False
                removed.append(path)
            return original(path, identity, digest)
        with patch.object(importer, '_rollback_published', side_effect=partial):
            with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
                self.install()
        self.assertEqual(len(removed), 1)
        self.assertFalse(removed[0].exists())
        transaction = upgrade._pending_id(self.root)
        self.assertEqual(upgrade._load_journal(self.root, transaction)[1]['status'], 'committed')
        (self.root / fixture.OLD_PATH).unlink()
        self.fixture.receipt.unlink()
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'upgrade-committed')
        self.assertFalse((self.root / fixture.OLD_PATH).exists())
        self.assertFalse(self.fixture.receipt.exists())
        self.assert_retired()

    def test_failure_after_pointer_unlink_rearms_only_cleanup_witness(self):
        original = upgrade._clear_pointer
        def fail(*args):
            original(*args)
            raise OSError('fixture durability failure after unlink')
        with patch.object(upgrade, '_clear_pointer', side_effect=fail):
            with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
                self.install()
        self.assertTrue((self.root / upgrade.PENDING).exists())
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), b'q90-route')
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertTrue(result['cleanupOnly'])
        self.assert_retired()

    def test_stage_cleanup_denial_keeps_pending_and_embedded_receipt_proof(self):
        original = importer._rollback_published
        def fail(path, identity, digest):
            if any(part.startswith('staging-') for part in path.parts) and path.name.endswith('.png'):
                return False
            return original(path, identity, digest)
        with patch.object(importer, '_rollback_published', side_effect=fail):
            with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
                self.install()
        directory, journal = upgrade._load_journal(self.root, upgrade._pending_id(self.root))
        self.assertFalse((directory / 'new' / journal['newReceipt']['path']).exists())
        self.assertTrue((self.root / journal['stage']).exists())
        importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assert_retired()

    def test_real_child_loss_after_first_cleanup_unlink_resumes_forward_only(self):
        code = '''
import os,sys
from pathlib import Path
import star_map_import as i
original=i._rollback_published
def stop(path,identity,digest):
    result=original(path,identity,digest)
    if '/upgrades/' in path.as_posix() and ('/new/' in path.as_posix() or '/backup/' in path.as_posix()):os._exit(91)
    return result
i._rollback_published=stop
i.install_package(Path(sys.argv[1]),Path(sys.argv[2]),application_stopped=True)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.fixture.new), str(self.root)],
            env=dict(os.environ, PYTHONPATH=str(Path(importer.__file__).parent)), capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 91, result.stderr)
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), b'q90-route')
        with resource_write_lock(self.root):
            plan = plans.inspect_locked(self.root)
            self.assertEqual(plan['status'], 'upgrade-cleanup-pending')
            result = plans.execute_locked(self.root, plan, 'recover')
        self.assertEqual(result['status'], 'upgrade-committed')
        self.assert_retired()

    def test_receipt_publication_loss_is_committed_even_before_status_write(self):
        original = upgrade.os.link
        def stop(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(target) == self.fixture.receipt:
                raise KeyboardInterrupt('fixture loss immediately after final receipt publication')
            return result
        with patch.object(upgrade.os, 'link', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                self.install()
        self.assertEqual(upgrade._load_journal(self.root, upgrade._pending_id(self.root))[1]['status'], 'prepared')
        with resource_write_lock(self.root):
            plan = plans.inspect_locked(self.root)
            self.assertEqual(plan['status'], 'upgrade-cleanup-pending')
            result = plans.execute_locked(self.root, plan, 'recover')
        self.assertEqual(result['status'], 'upgrade-committed')
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), b'q90-route')
        self.assert_retired()

    def test_preparing_loss_and_failed_publication_restore_then_retire_all_copies(self):
        original = upgrade.os.link
        def stop(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if '/upgrades/' in Path(target).as_posix() and '/new/' in Path(target).as_posix():
                raise KeyboardInterrupt('fixture loss during pin preparation')
            return result
        with patch.object(upgrade.os, 'link', side_effect=stop):
            with self.assertRaises(KeyboardInterrupt):
                self.install()
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'rolled-back')
        self.fixture.assert_old()
        self.assert_retired()
        def fail(source, target, *args, **kwargs):
            if Path(target) == self.root / fixture.ADDED_PATH:
                raise OSError('fixture active publication denied')
            return original(source, target, *args, **kwargs)
        with patch.object(upgrade.os, 'link', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'publication denied'):
                self.install()
        self.fixture.assert_old()
        self.assert_retired()

    def test_legacy_two_generations_retire_without_requiring_old_active_version(self):
        self.legacy_install()
        third = self.fixture.package('third.zip', {fixture.OLD_PATH: b'third', fixture.ADDED_PATH: b'{}'})
        self.legacy_install(third)
        active = (self.root / fixture.OLD_PATH).read_bytes()
        self.assertEqual(self.retire()['retiredTransactions'], 2)
        self.assertEqual((self.root / fixture.OLD_PATH).read_bytes(), active)
        self.assertEqual(self.retire()['retiredTransactions'], 0)
        self.assert_retired()

    def test_legacy_unknown_or_changed_payload_is_preserved_and_never_reports_complete(self):
        _, directory = self.legacy_install()
        unknown = directory / 'user-note.txt'
        unknown.write_bytes(b'keep')
        before = (directory / 'backup' / fixture.OLD_PATH).read_bytes()
        with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
            self.retire()
        self.assertTrue((self.root / upgrade.PENDING).exists())
        self.assertEqual(unknown.read_bytes(), b'keep')
        self.assertEqual((directory / 'backup' / fixture.OLD_PATH).read_bytes(), before)
        unknown.unlink()
        replacement = self.fixture.folder / 'changed-backup'
        replacement.write_bytes(b'changed')
        replacement.replace(directory / 'backup' / fixture.OLD_PATH)
        with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
            importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual((directory / 'backup' / fixture.OLD_PATH).read_bytes(), b'changed')

    def test_owned_download_caches_retire_but_external_and_unknown_zip_remain(self):
        caches = []
        for archive in (self.fixture.old, self.fixture.new):
            data = archive.read_bytes()
            relative = f"{upgrade.STATE}/downloads/difficult-zhan10-{hashlib.sha256(data).hexdigest()}.zip"
            self.fixture.write(relative, data)
            caches.append(self.root / relative)
        self.fixture.write(upgrade.STATE + '/downloads/unknown.zip', b'unknown')
        self.install()
        self.assertTrue(all(not path.exists() for path in caches))
        self.assertTrue(self.fixture.old.exists() and self.fixture.new.exists())
        self.assertTrue((self.root / upgrade.STATE / 'downloads/unknown.zip').exists())
        self.assert_retired()

    def test_cleanup_denied_for_cache_retains_pending_until_cache_is_removed(self):
        data = self.fixture.old.read_bytes()
        relative = f"{upgrade.STATE}/downloads/difficult-zhan10-{hashlib.sha256(data).hexdigest()}.zip"
        self.fixture.write(relative, data)
        original = importer._rollback_published
        def fail(path, identity, digest):
            return False if Path(path) == self.root / relative else original(path, identity, digest)
        with patch.object(importer, '_rollback_published', side_effect=fail):
            with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-cleanup-incomplete'):
                self.install()
        self.assertTrue((self.root / relative).exists())
        importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertFalse((self.root / relative).exists())
        self.assert_retired()

    def test_reader_cannot_observe_committed_before_private_cleanup_finishes(self):
        entered, finish, reading = threading.Event(), threading.Event(), threading.Event()
        original = upgrade._cleanup_upgrade
        def pause(*args):
            entered.set()
            if not finish.wait(5):
                raise RuntimeError('fixture writer barrier timed out')
            return original(*args)
        def read():
            with resource_read_lock(self.root, wait=True):
                assert_resources_ready(self.root)
                reading.set()
                return (self.root / fixture.OLD_PATH).read_bytes()
        with resource_runtime_lease(self.root), ThreadPoolExecutor(2) as pool, \
                patch.object(upgrade, '_cleanup_upgrade', side_effect=pause):
            writer = pool.submit(self.install)
            self.assertTrue(entered.wait(3))
            reader = pool.submit(read)
            self.assertFalse(reading.wait(.1))
            finish.set()
            writer.result(5)
            self.assertEqual(reader.result(5), b'q90-route')
        self.assert_retired()


if __name__ == '__main__':
    unittest.main()
