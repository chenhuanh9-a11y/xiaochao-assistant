"""Offline fixture packages; only a temporary installation is mutated."""
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import star_map_import as importer

OLD_PATH = '_internal/assets/zhan10-legacy-clean/difficult-map-29/floor-1.png'
ADDED_PATH = '_internal/references/difficult-exit-icon.json'


def row(path, data):
    return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.root = self.folder / 'install'
        self.root.mkdir()
        self.write('_internal/version.json', json.dumps(dict(displayVersion='1.0.1', numericVersion='1.0.0.5')).encode())
        self.dependencies = []
        for index, path in enumerate(sorted(importer.BASE_DEPENDENCIES)):
            data = f'fixture-base-{index}'.encode()
            self.write(path, data)
            self.dependencies.append(row(path, data))
        self.old = self.package('old.zip', {OLD_PATH: b'old-route'})
        self.new = self.package('new.zip', {OLD_PATH: b'q90-route', ADDED_PATH: b'{}'})

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def package(self, name, payload):
        archive = self.folder / name
        metadata = dict(schemaVersion=1, revision=1, packageId='difficult-zhan10',
                        clientCompatibility=importer.CLIENT_COMPATIBILITY,
                        requiresFiles=self.dependencies, files=[row(p, d) for p, d in payload.items()])
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('map-package.json', json.dumps(metadata))
            for path, data in payload.items():
                z.writestr('data/' + path, data)
        return archive

    def installed_old(self):
        importer.install_package(self.old, self.root, application_stopped=True)
        self.receipt = self.root / 'work/map-packages/difficult-zhan10.json'
        self.old_receipt = self.receipt.read_bytes()

    def assert_old(self):
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'old-route')
        self.assertEqual(self.receipt.read_bytes(), self.old_receipt)
        self.assertFalse((self.root / ADDED_PATH).exists())

    def test_unknown_existing_files_are_not_claimed_by_new_archive(self):
        self.write(OLD_PATH, b'old-route')
        with self.assertRaisesRegex(importer.ImportRejected, 'existing-data-conflict'):
            importer.inspect_package(self.new, self.root)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'old-route')

    def test_102_version_retains_existing_package_layout_and_reimport(self):
        self.write('_internal/version.json', json.dumps(dict(displayVersion='1.0.2', numericVersion='1.0.0.6')).encode())
        result = importer.install_package(self.old, self.root, application_stopped=True)
        self.assertEqual(result['packageId'], 'difficult-zhan10')
        self.assertEqual(importer.inspect_package(self.old, self.root)['alreadyPresent'], 1)
        again = importer.install_package(self.old, self.root, application_stopped=True)
        self.assertEqual(again['status'], 'already-installed')

    def test_modified_owned_file_and_bad_receipt_remain_conflicts(self):
        self.installed_old()
        self.write(OLD_PATH, b'player-custom-route')
        with self.assertRaisesRegex(importer.ImportRejected, 'existing-data-conflict'):
            importer.install_package(self.new, self.root, application_stopped=True)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'player-custom-route')
        self.write(OLD_PATH, b'old-route')
        self.receipt.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(importer.ImportRejected, 'invalid-installed-package-receipt'):
            importer.inspect_package(self.new, self.root)

    def test_inspect_classifies_same_package_upgrade_without_writes(self):
        self.installed_old()
        result = importer.inspect_package(self.new, self.root)
        self.assertEqual([r['path'] for r in result['replaceFiles']], [OLD_PATH])
        self.assertEqual([r['path'] for r in result['missingFiles']], [ADDED_PATH])
        self.assertEqual(result['previousReceiptSha256'], hashlib.sha256(self.old_receipt).hexdigest())
        self.assert_old()

    def test_owned_upgrade_and_explicit_rollback_restore_receipt_and_files(self):
        self.installed_old()
        result = importer.install_package(self.new, self.root, application_stopped=True)
        self.assertEqual(result['replaced'], 1)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'q90-route')
        self.assertEqual((self.root / ADDED_PATH).read_bytes(), b'{}')
        self.assertEqual(importer.inspect_package(self.new, self.root)['alreadyPresent'], 2)
        importer.rollback_package_upgrade(self.root, result['upgradeTransactionId'], application_stopped=True)
        self.assert_old()

    def test_identical_reinstall_preserves_receipt_and_upgrade_rollback(self):
        self.installed_old()
        result = importer.install_package(self.new, self.root, application_stopped=True)
        receipt_bytes = self.receipt.read_bytes()
        receipt_inode = self.receipt.stat().st_ino
        again = importer.install_package(self.new, self.root, application_stopped=True)
        self.assertEqual(again['status'], 'already-installed')
        self.assertEqual(again['upgradeTransactionId'], result['upgradeTransactionId'])
        self.assertEqual(self.receipt.read_bytes(), receipt_bytes)
        self.assertEqual(self.receipt.stat().st_ino, receipt_inode)
        importer.rollback_package_upgrade(self.root, result['upgradeTransactionId'], application_stopped=True)
        self.assert_old()

    def test_publish_failure_rolls_back_old_resource_and_receipt(self):
        self.installed_old()
        import map_package_upgrade as transaction
        actual_link = transaction.os.link
        def fail(source, target, *args, **kwargs):
            if Path(target) == self.root / ADDED_PATH:
                raise OSError('fixture disk failure')
            return actual_link(source, target, *args, **kwargs)
        with patch.object(transaction.os, 'link', side_effect=fail):
            with self.assertRaises(Exception):
                importer.install_package(self.new, self.root, application_stopped=True)
        self.assert_old()

    def test_interrupted_publish_retains_journal_and_explicit_recovery_restores_old(self):
        self.installed_old()
        import map_package_upgrade as transaction
        actual_link = transaction.os.link
        def interrupt(source, target, *args, **kwargs):
            if Path(target) == self.root / ADDED_PATH:
                raise KeyboardInterrupt('fixture process interruption')
            return actual_link(source, target, *args, **kwargs)
        with patch.object(transaction.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_package(self.new, self.root, application_stopped=True)
        with self.assertRaisesRegex(importer.ImportRejected, 'upgrade-recovery-required'):
            importer.inspect_package(self.new, self.root)
        importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assert_old()

    def test_rollback_preserves_user_edits_after_success(self):
        self.installed_old()
        result = importer.install_package(self.new, self.root, application_stopped=True)
        # Replace, rather than edit a hard link retained as the new transaction witness.
        custom = self.folder / 'custom'
        custom.write_bytes(b'player-custom-route')
        custom.replace(self.root / OLD_PATH)
        receipt_bytes = self.receipt.read_bytes()
        with self.assertRaises(importer.ImportRejected):
            importer.rollback_package_upgrade(self.root, result['upgradeTransactionId'], application_stopped=True)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'player-custom-route')
        self.assertEqual((self.root / ADDED_PATH).read_bytes(), b'{}')
        self.assertEqual(self.receipt.read_bytes(), receipt_bytes)

    def test_recovery_requires_closed_application_and_rejects_invalid_transaction_id(self):
        with self.assertRaisesRegex(importer.ImportRejected, 'close-application-first'):
            importer.recover_package_upgrade(self.root)
        with self.assertRaises(importer.ImportRejected):
            importer.rollback_package_upgrade(self.root, '../escape', application_stopped=True)

    def test_direct_cli_reports_transaction_rejection_as_json(self):
        script = Path(importer.__file__).resolve()
        result = subprocess.run([sys.executable, '-B', str(script), 'rollback', '../escape',
                                 str(self.root), '--application-closed'],
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(json.loads(result.stdout)['success'])
        self.assertIn('invalid-upgrade-transaction-id', json.loads(result.stdout)['reason'])
        self.assertNotIn('Traceback', result.stderr)

    def test_dependency_catalog_is_not_relaxed_for_owned_upgrades(self):
        self.installed_old()
        self.write(self.dependencies[0]['path'], b'different-base-catalog')
        with self.assertRaisesRegex(importer.ImportRejected, 'missing-or-incompatible-dependency'):
            importer.inspect_package(self.new, self.root)
        self.assert_old()


if __name__ == '__main__':
    unittest.main()
