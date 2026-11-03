"""Real temporary files and subprocess exclusion; unsupported links are fault injection."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import test_map_package_upgrade as fixtures
OLD_PATH = fixtures.OLD_PATH
import star_map_import as importer


class TransactionRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.UpgradeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root

    def test_dependency_failure_has_bounded_machine_data(self):
        row = self.fixture.dependencies[0]
        (self.root / row['path']).unlink()
        with self.assertRaises(importer.ImportRejected) as caught:
            importer.inspect_package(self.fixture.old, self.root)
        error = caught.exception
        self.assertEqual(error.code, 'missing-or-incompatible-dependency')
        self.assertEqual(error.details, dict(path=row['path'], expectedBytes=row['bytes'],
            actualBytes=None, expectedSha256=row['sha256'], actualSha256=None, missing=True))
        self.assertNotIn(str(self.root), json.dumps(error.details))

    def test_unsupported_links_fail_preflight_before_any_publication(self):
        with patch.object(importer.os, 'link', side_effect=OSError('injected unsupported operation')):
            with self.assertRaises(importer.ImportRejected) as caught:
                importer.install_package(self.fixture.old, self.root, application_stopped=True)
        self.assertEqual(caught.exception.code, 'resource-hardlinks-unavailable')
        self.assertFalse((self.root / OLD_PATH).exists())
        self.assertFalse((self.root / 'work/map-packages/difficult-zhan10.json').exists())
        self.assertFalse((self.root / 'work/map-packages/pending-upgrade.json').exists())

    def test_runtime_lease_excludes_other_process_but_allows_local_writer(self):
        from resource_transaction import resource_runtime_lease
        with resource_runtime_lease(self.root):
            imported = importer.install_package(self.fixture.old, self.root, application_stopped=True)
            self.assertEqual(imported['added'], 1)
            script = ("import sys; sys.path.insert(0, sys.argv[1]); "
                      "from resource_transaction import resource_runtime_lease; "
                      "resource_runtime_lease(sys.argv[2])")
            other = subprocess.run([sys.executable, '-B', '-c', script, str(APP), str(self.root)],
                                   capture_output=True, text=True)
            self.assertNotEqual(other.returncode, 0)
            self.assertIn('resource-busy', other.stderr)
        with resource_runtime_lease(self.root):
            pass

    def test_interrupted_addition_blocks_startup_until_explicit_recovery(self):
        from resource_transaction import resource_runtime_lease, assert_resources_ready, ResourceTransactionError
        real_link = importer.os.link
        def interrupt(source, target, *args, **kwargs):
            real_link(source, target, *args, **kwargs)
            if Path(target) == self.root / OLD_PATH:
                raise KeyboardInterrupt('fixture process loss after first publication')
        with patch.object(importer.os, 'link', side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_package(self.fixture.old, self.root, application_stopped=True)
        with resource_runtime_lease(self.root):
            with self.assertRaisesRegex(ResourceTransactionError, 'additions-recovery-required'):
                assert_resources_ready(self.root)
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'additions-rolled-back')
        self.assertFalse((self.root / OLD_PATH).exists())
        with resource_runtime_lease(self.root):
            assert_resources_ready(self.root)

    def child(self, script, *args):
        return subprocess.Popen([sys.executable, '-B', '-c',
            'import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); ' + script,
            str(APP), str(self.root), *map(str, args)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE, text=True)

    def test_runtime_blocks_real_helper_and_writer_until_closed(self):
        from resource_transaction import resource_runtime_lease
        script = ("import star_map_import_helper as h; "
                  "h.wait_for_application_exit=lambda *a, **k: 'exited-matching-process-handle'; "
                  "code,result=h.run_helper(archive=sys.argv[3],install_root=sys.argv[2],"
                  "parent_pid=1,parent_created='fixture',archive_sha256=sys.argv[4],request_id='lease-fixture'); "
                  "print(result['errorCode'] if code else 'installed'); sys.exit(code)")
        with resource_runtime_lease(self.root):
            helper = self.child(script, self.fixture.old, importer._sha(self.fixture.old))
            out, err = helper.communicate(timeout=20)
            self.assertEqual(helper.returncode, 2, err)
            self.assertIn('resource-busy', out)
            self.assertFalse((self.root / OLD_PATH).exists())
        helper = self.child(script, self.fixture.old, importer._sha(self.fixture.old))
        out, err = helper.communicate(timeout=20)
        self.assertEqual(helper.returncode, 0, err)
        self.assertEqual(out.strip(), 'installed')

    def test_writer_process_excludes_main_then_releases_on_process_exit(self):
        from resource_transaction import resource_runtime_lease, ResourceTransactionError
        writer = self.child("from resource_transaction import resource_write_lock; "
            "lock=resource_write_lock(sys.argv[2]); lock.__enter__(); print('locked',flush=True); input()")
        try:
            self.assertEqual(writer.stdout.readline().strip(), 'locked')
            with self.assertRaisesRegex(ResourceTransactionError, 'resource-busy'):
                resource_runtime_lease(self.root)
            writer.communicate('\n', timeout=20)
            self.assertEqual(writer.returncode, 0)
        finally:
            if writer.poll() is None:
                writer.kill()
                writer.communicate()
        with resource_runtime_lease(self.root):
            pass

    @unittest.skipUnless(sys.platform == 'win32', 'Windows installer handle sharing')
    def test_installer_share_zero_handle_and_runtime_are_mutually_exclusive(self):
        from resource_transaction import resource_runtime_lease, ResourceTransactionError
        script = (
            "import ctypes; from ctypes import wintypes; "
            "p=Path(sys.argv[2])/'work/map-packages/import.lock'; p.parent.mkdir(parents=True,exist_ok=True); "
            "k=ctypes.WinDLL('kernel32',use_last_error=True); "
            "k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,"
            "wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]; k.CreateFileW.restype=wintypes.HANDLE; "
            "h=k.CreateFileW(str(p),0xC0000000,0,None,4,0x80,None); "
            "ok=h!=ctypes.c_void_p(-1).value; print('locked' if ok else 'busy',flush=True); "
            "input() if ok else None")
        with resource_runtime_lease(self.root):
            installer = self.child(script)
            out, err = installer.communicate(timeout=20)
            self.assertEqual(out.strip(), 'busy', err)
        installer = self.child(script)
        try:
            self.assertEqual(installer.stdout.readline().strip(), 'locked')
            with self.assertRaisesRegex(ResourceTransactionError, 'resource-busy'):
                resource_runtime_lease(self.root)
            installer.communicate('\n', timeout=20)
        finally:
            if installer.poll() is None:
                installer.kill()
                installer.communicate()
        with resource_runtime_lease(self.root):
            pass

    def test_finally_releases_runtime_lease(self):
        from resource_transaction import resource_runtime_lease
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with resource_runtime_lease(self.root):
                raise RuntimeError('fixture')
        with resource_runtime_lease(self.root):
            pass

    def test_real_process_loss_keeps_addition_marker_and_recovery_witnesses(self):
        from resource_transaction import resource_runtime_lease, assert_resources_ready, ResourceTransactionError
        script = '''
import os
import star_map_import as i
real_link = os.link
def publish(source, target, *args, **kwargs):
    real_link(source, target, *args, **kwargs)
    if Path(target) == Path(sys.argv[2]) / sys.argv[4]:
        os._exit(23)
os.link = publish
i.install_package(sys.argv[3], sys.argv[2], application_stopped=True)
'''
        child = self.child(script, self.fixture.old, OLD_PATH)
        out, err = child.communicate(timeout=20)
        self.assertEqual(child.returncode, 23, err)
        self.assertTrue((self.root / OLD_PATH).exists())
        with resource_runtime_lease(self.root):
            with self.assertRaisesRegex(ResourceTransactionError, 'additions-recovery-required'):
                assert_resources_ready(self.root)
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'additions-rolled-back')
        self.assertFalse((self.root / OLD_PATH).exists())

    def test_committed_addition_interruption_completes_without_removing_resources(self):
        import map_package_upgrade as upgrade
        with patch.object(upgrade, 'recover_pending_additions', side_effect=KeyboardInterrupt('after receipt commit')):
            with self.assertRaises(KeyboardInterrupt):
                importer.install_package(self.fixture.old, self.root, application_stopped=True)
        receipt = self.root / 'work/map-packages/difficult-zhan10.json'
        before = receipt.read_bytes()
        result = importer.recover_package_upgrade(self.root, application_stopped=True)
        self.assertEqual(result['status'], 'additions-committed')
        self.assertEqual(receipt.read_bytes(), before)
        self.assertEqual((self.root / OLD_PATH).read_bytes(), b'old-route')

    def test_unsupported_upgrade_links_leave_old_receipt_and_no_pending_or_journal(self):
        self.fixture.installed_old()
        with patch.object(importer.os, 'link', side_effect=OSError('injected unsupported operation')):
            with self.assertRaisesRegex(importer.ImportRejected, 'resource-hardlinks-unavailable'):
                importer.install_package(self.fixture.new, self.root, application_stopped=True)
        self.fixture.assert_old()
        self.assertFalse((self.root / 'work/map-packages/pending-upgrade.json').exists())
        self.assertFalse((self.root / 'work/map-packages/upgrades').exists())

    def test_helper_keeps_os_lease_until_atomic_receipt_and_releases_on_receipt_error(self):
        import star_map_import_helper as helper
        from resource_transaction import resource_runtime_lease
        def write_receipt(*args, **kwargs):
            child = self.child('from resource_transaction import resource_runtime_lease; resource_runtime_lease(sys.argv[2])')
            out, err = child.communicate(timeout=20)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn('resource-busy', err)
            raise OSError('fixture receipt unavailable')
        with patch.object(helper, 'wait_for_application_exit', return_value='exited-matching-process-handle'), \
             patch.object(helper, '_write_receipt', side_effect=write_receipt):
            with self.assertRaisesRegex(OSError, 'receipt unavailable'):
                helper.run_helper(archive=self.fixture.old, install_root=self.root,
                    parent_pid=1, parent_created='fixture', archive_sha256=importer._sha(self.fixture.old),
                    request_id='receipt-lease-fixture')
        with resource_runtime_lease(self.root):
            pass

    def test_helper_abnormal_exception_releases_runtime_lease(self):
        import star_map_import_helper as helper
        from resource_transaction import resource_runtime_lease
        with patch.object(helper, 'wait_for_application_exit', return_value='exited-matching-process-handle'), \
             patch.object(helper, 'install_package', side_effect=KeyboardInterrupt('fixture interrupted helper')):
            with self.assertRaises(KeyboardInterrupt):
                helper.run_helper(archive=self.fixture.old, install_root=self.root,
                    parent_pid=1, parent_created='fixture', archive_sha256=importer._sha(self.fixture.old),
                    request_id='abnormal-helper-fixture')
        with resource_runtime_lease(self.root):
            pass

    def test_each_pending_marker_blocks_inspection_before_archive_read(self):
        from resource_transaction import resource_write_lock, assert_resources_ready, ResourceTransactionError
        markers = [('upgrade', 'upgrade-recovery-required'), ('base-repair', 'base-recovery-required'),
                   ('additions', 'additions-recovery-required'), ('removal', 'removal-recovery-required')]
        for name, code in markers:
            with resource_write_lock(self.root):
                marker = self.root / f'work/map-packages/pending-{name}.json'
                marker.write_bytes(b'{}')
                try:
                    with self.assertRaisesRegex(ResourceTransactionError, code):
                        assert_resources_ready(self.root)
                    with patch.object(importer, '_open_archive', side_effect=AssertionError('must not read archive')):
                        with self.assertRaisesRegex(importer.ImportRejected, code):
                            importer.inspect_package(self.fixture.old, self.root)
                finally:
                    marker.unlink()


if __name__ == '__main__':
    unittest.main()
