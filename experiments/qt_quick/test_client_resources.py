"""Qt adapter contract tests; every write uses a private marked clone/work root."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QUrl
from client_resources import ResourceService, ROOT, importer, manager
import client_resource_recovery as recovery

APP = QCoreApplication.instance() or QCoreApplication([])
IMAGE = "_internal/assets/suju/map-01-1.png"


def row(path, data):
    return dict(path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


class Lease:
    def __init__(self):
        self.active = False
        self.released = []
        self.allowed = True

    def acquireResourceLease(self):
        if not self.allowed:
            return ""
        assert not self.active
        self.active = True
        return "lease-1"

    def releaseResourceLease(self, value):
        assert self.active
        self.active = False
        self.released.append(value)


class ResourceServiceTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-resource-tests-", dir=ROOT / "work")
        self.folder = Path(self.temp.name)
        self.root = self.folder / "installation"
        self.root.mkdir()
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.write("_internal/version.json", json.dumps(dict(displayVersion="1.0.5", numericVersion="1.0.0.9")).encode())
        self.dependencies = []
        for index, path in enumerate(sorted(importer.BASE_DEPENDENCIES)):
            data = f"base-{index}".encode()
            self.write(path, data)
            self.dependencies.append(row(path, data))
        self.service = ResourceService(self.root)
        self.lease = Lease()
        self.service.setLeaseProvider(self.lease)
        self.confirmations = []
        self.changes = []
        self.completions = []
        self.service.importCompleted.connect(lambda *args: self.completions.append(args))
        self.service.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        self.service.resourcesChanged.connect(lambda: self.changes.append(True))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.service.shutdown()
        self.service.pool.waitForDone(15000)
        APP.processEvents()
        self.temp.cleanup()

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def package(self, name="package.zip", data=b"route"):
        path = self.folder / name
        manifest = dict(schemaVersion=1, revision=1, packageId="nightmare-suju",
            clientCompatibility=importer.CLIENT_COMPATIBILITY, requiresFiles=self.dependencies,
            files=[row(IMAGE, data)])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("map-package.json", json.dumps(manifest))
            archive.writestr("data/" + IMAGE, data)
        return path

    def settle(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            APP.processEvents()
            if not self.service.busy:
                APP.processEvents()
                return
            time.sleep(.005)
        self.fail("resource worker did not settle")

    def install(self, path=None):
        self.service.importArchive(QUrl.fromLocalFile(str(path or self.package())).toString())
        self.settle()

    def upgrade(self, *, interrupted=False):
        """Synthetic receipts and real unchanged transaction; no installed EXE."""
        self.install(self.package("old.zip", b"old"))
        receipt = self.root / "work/map-packages/nightmare-suju.json"
        self.old_receipt = receipt.read_bytes()
        archive = self.package("new.zip", b"new")
        if interrupted:
            actual_link = recovery.upgrade.os.link
            def interrupt(source, target, *args, **kwargs):
                if Path(target) == receipt:
                    raise KeyboardInterrupt("fixture process interruption")
                return actual_link(source, target, *args, **kwargs)
            with patch.object(recovery.upgrade.os, "link", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    importer.install_package(archive, self.root, application_stopped=True)
            transaction_id = recovery.upgrade._pending_id(self.root)
        else:
            result = importer.install_package(archive, self.root, application_stopped=True)
            transaction_id = result["upgradeTransactionId"]
        self.service.setRecoveryAllowedProvider(lambda: True)
        self.changes.clear()
        self.confirmations.clear()
        return transaction_id

    def request_recovery(self, transaction_id=None):
        if transaction_id is None:
            self.service.requestRecovery()
        else:
            self.service.requestRollback(transaction_id)
        self.settle()
        self.assertEqual(self.service.pendingKind, "recovery", self.service.message)
        self.assertTrue(self.lease.active)
        self.assertIn(self.service._pending[1]["planSha256"], self.confirmations[-1][1])

    def assert_old_resources(self):
        self.assertEqual((self.root / IMAGE).read_bytes(), b"old")
        self.assertEqual((self.root / "work/map-packages/nightmare-suju.json").read_bytes(), self.old_receipt)
        self.assertFalse((self.root / recovery.upgrade.PENDING).exists())
        self.assertFalse(self.lease.active)

    def test_recovery_restores_old_resources_and_rescans_with_one_lease(self):
        transaction_id = self.upgrade(interrupted=True)
        self.service.inspectRecovery()
        self.settle()
        self.assertTrue(self.service.recoveryRequired)
        self.assertEqual(self.service.recoveryTransactions[0]["transactionId"], transaction_id)
        released = len(self.lease.released)
        self.request_recovery()
        self.service.requestRecovery()  # Repeated action cannot replace consent.
        self.service.confirmOperation(True)
        self.settle()
        self.assert_old_resources()
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(len(self.lease.released), released + 1)
        self.assertFalse(self.service.recoveryRequired)
        self.assertTrue(any(row["status"] == "installed" for row in self.service.packageRows))
        self.assertTrue((self.root / f"work/map-packages/upgrades/{transaction_id}/backup" / IMAGE).is_file())

    def test_completed_transaction_rollback_cancel_and_commit(self):
        transaction_id = self.upgrade()
        self.request_recovery(transaction_id)
        self.service.confirmOperation(False)
        self.assertFalse(self.lease.active)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        self.request_recovery(transaction_id)
        self.service.confirmOperation(True)
        self.settle()
        self.assert_old_resources()
        self.service.inspectRecovery()
        self.settle()
        self.assertEqual(self.service.recoveryTransactions, [])

    def test_recovery_confirmation_rejects_changed_journal_pointer_and_files(self):
        transaction_id = self.upgrade(interrupted=True)
        directory = self.root / f"work/map-packages/upgrades/{transaction_id}"
        for target in (directory / "journal.json", self.root / recovery.upgrade.PENDING,
                       directory / "pending-pointer.json", self.root / IMAGE,
                       directory / "backup" / IMAGE):
            with self.subTest(target=target):
                self.request_recovery()
                replacement = self.folder / "replacement"
                replacement.write_bytes(target.read_bytes())
                replacement.replace(target)  # Same bytes, different owned object.
                self.service.confirmOperation(True)
                self.settle()
                self.assertIn("reinspect", self.service.message)
                self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
                self.assertTrue((self.root / recovery.upgrade.PENDING).exists())
                self.assertFalse(self.lease.active)
        self.assertEqual(self.changes, [])

    def test_recovery_conflict_retains_edits_backup_and_pending_journal(self):
        transaction_id = self.upgrade(interrupted=True)
        replacement = self.folder / "custom"
        replacement.write_bytes(b"player edit")
        replacement.replace(self.root / IMAGE)
        self.request_recovery()
        self.service.confirmOperation(True)
        self.settle()
        self.assertIn("rollback-incomplete", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"player edit")
        self.assertTrue((self.root / recovery.upgrade.PENDING).exists())
        directory = self.root / f"work/map-packages/upgrades/{transaction_id}"
        self.assertEqual((directory / "backup" / IMAGE).read_bytes(), b"old")
        self.assertEqual(json.loads((directory / "journal.json").read_bytes())["status"], "rollback-incomplete")
        self.assertEqual(self.changes, [])
        self.assertFalse(self.lease.active)

    def test_recovery_closed_phase_and_original_exe_guard_are_required(self):
        self.upgrade(interrupted=True)
        allowed = [False]
        self.service.setRecoveryAllowedProvider(lambda: allowed[0])
        self.service.requestRecovery()
        self.assertFalse(self.service.busy)
        self.assertFalse(self.lease.active)
        allowed[0] = True
        self.request_recovery()
        allowed[0] = False
        self.service.confirmOperation(True)
        self.assertFalse(self.service.busy)
        self.assertFalse(self.lease.active)
        allowed[0] = True
        self.request_recovery()
        with patch.object(importer, "_check_closed", side_effect=importer.ImportRejected("application-running-or-not-writable")):
            self.service.confirmOperation(True)
            self.settle()
        self.assertIn("application-running-or-not-writable", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        self.assertTrue((self.root / recovery.upgrade.PENDING).exists())
        self.assertEqual(self.changes, [])

    def test_recovery_phase_rechecked_after_original_closed_lock(self):
        self.upgrade(interrupted=True)
        allowed = [True]
        self.service.setRecoveryAllowedProvider(lambda: allowed[0])
        self.request_recovery()
        def phase_changed(_root):
            allowed[0] = False
        with patch.object(importer, "_check_closed", side_effect=phase_changed):
            self.service.confirmOperation(True)
            self.settle()
        self.assertIn("resource-consumers-not-stopped", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        self.assertEqual(self.changes, [])

    def test_recovery_commit_cannot_be_cancelled_and_failure_is_not_success(self):
        self.upgrade(interrupted=True)
        self.request_recovery()
        actual_execute = recovery.execute_locked
        def commit(root, plan, action):
            self.assertFalse(self.service.canCancel)
            self.service.cancel()
            return actual_execute(root, plan, action)
        with patch.object(recovery, "execute_locked", side_effect=OSError("fixture disk failure")):
            self.service.confirmOperation(True)
            self.settle()
        self.assertIn("fixture disk failure", self.service.message)
        self.assertEqual(self.changes, [])
        self.request_recovery()
        with patch.object(recovery, "execute_locked", side_effect=commit):
            self.service.confirmOperation(True)
            self.settle()
        self.assert_old_resources()
        self.assertEqual(len(self.changes), 1)

    def test_history_uses_writer_lock_without_claiming_application_closed(self):
        self.upgrade()
        with patch.object(importer, "_check_closed", side_effect=AssertionError("inspection must not claim closed")):
            self.service.inspectRecovery()
            self.settle()
        self.assertEqual(len(self.service.recoveryTransactions), 1)
        self.assertFalse(self.lease.active)

    def test_external_helper_handoff_retains_lease_and_never_claims_commit(self):
        self.upgrade(interrupted=True)
        self.service.setRecoveryAllowedProvider(lambda: False)
        requests, close_requests = [], []
        def launch(action, plan):
            requests.append((action, plan))
            return "fixture-request"
        self.service.setRecoveryLauncher(launch)
        self.service.recoveryCloseRequested.connect(lambda: close_requests.append(True))
        self.request_recovery()
        self.service.confirmOperation(True)
        self.settle()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][0], "recover")
        self.assertEqual(len(close_requests), 1)
        self.assertTrue(self.service.recoveryHandoffPending)
        self.assertTrue(self.lease.active)
        self.assertFalse(self.service.canCancel)
        self.assertEqual(self.changes, [])
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        self.service.requestRecovery()
        self.service.refresh()
        self.assertFalse(self.service.busy)
        self.service.shutdown()
        self.assertFalse(self.lease.active)

    def test_external_helper_launch_failure_does_not_close_or_write(self):
        self.upgrade(interrupted=True)
        close_requests = []
        def fail(action, plan):
            raise OSError("fixture helper missing")
        self.service.setRecoveryLauncher(fail)
        self.service.recoveryCloseRequested.connect(lambda: close_requests.append(True))
        self.request_recovery()
        self.service.confirmOperation(True)
        self.settle()
        self.assertIn("fixture helper missing", self.service.message)
        self.assertFalse(self.service.recoveryHandoffPending)
        self.assertFalse(self.lease.active)
        self.assertEqual(close_requests, [])
        self.assertEqual(self.changes, [])
        self.assertTrue((self.root / recovery.upgrade.PENDING).exists())

    def test_missing_pending_journal_is_not_reported_as_clean_history(self):
        import uuid
        self.write(recovery.upgrade.PENDING, json.dumps(dict(
            schema=recovery.upgrade.POINTER_SCHEMA, transactionId=str(uuid.uuid4()))).encode())
        self.service.inspectRecovery()
        self.settle()
        self.assertTrue(self.service.recoveryRequired)
        self.assertIn("pending-upgrade-journal-missing", self.service.message)

    def test_bound_helper_receipt_reports_actual_result_and_notifies_after_scan(self):
        transaction_id = self.upgrade()
        receipt = dict(requestId="fixture-request", planSha256="a" * 64,
            transactionId=transaction_id, action="rollback", success=False,
            reason="fixture conflict; backups retained")
        self.service.recordRecoveryReceipt(receipt)
        self.service.refresh()
        self.settle()
        self.assertIn("fixture conflict", self.service.message)
        self.assertEqual(self.changes, [])
        result = importer.rollback_package_upgrade(self.root, transaction_id, application_stopped=True)
        receipt.update(success=True, operationResult=result)
        self.service.recordRecoveryReceipt(receipt)
        self.assertEqual(self.changes, [])
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.changes), 1)
        self.assert_old_resources()
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.changes), 1)
        receipt["operationResult"] = {"status": "started"}
        with self.assertRaises(ValueError):
            self.service.recordRecoveryReceipt(receipt)

    def test_pending_rolled_back_journal_can_finish_pointer_cleanup(self):
        self.upgrade(interrupted=True)
        with patch.object(recovery.upgrade, "_clear_pointer", side_effect=KeyboardInterrupt("fixture cleanup interruption")):
            with self.assertRaises(KeyboardInterrupt):
                importer.recover_package_upgrade(self.root, application_stopped=True)
        self.service.inspectRecovery()
        self.settle()
        self.assertTrue(self.service.recoveryRequired)
        self.assertEqual(self.service.recoveryTransactions[0]["status"], "rolled-back")
        self.request_recovery()
        self.service.confirmOperation(True)
        self.settle()
        self.assert_old_resources()

    def test_empty_rollback_id_does_not_broaden_to_pending_recovery(self):
        self.upgrade(interrupted=True)
        self.service.requestRollback("")
        self.settle()
        self.assertEqual(self.service.pendingKind, "")
        self.assertIn("invalid-upgrade-transaction-id", self.service.message)
        self.assertFalse(self.lease.active)
        self.assertTrue((self.root / recovery.upgrade.PENDING).exists())

    def test_real_import_repeat_remove_and_reimport_preserve_unknown(self):
        self.write("player-custom-route.json", b"keep")
        archive = self.package()
        results = []
        real_install = importer._install_staged
        def capture_result(*args, **kwargs):
            result = real_install(*args, **kwargs)
            results.append(result)
            return result
        with patch.object(importer, "_install_staged", side_effect=capture_result):
            self.install(archive)
            self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
            self.assertEqual(len(self.changes), 1)
            self.assertNotIn("内容已存在", self.service.message)
            self.assertEqual(self.service.message, "资源安装完成，登记与状态已核对。")
            self.assertEqual(len(self.completions), 1)
            self.assertIn("安装完成", self.completions[0][1])
            self.assertTrue(any(r["status"] == "installed" for r in self.service.packageRows))
            self.install(archive)
        self.assertEqual([(r['added'], r['alreadyPresent']) for r in results], [(1, 0), (0, 1)])
        self.assertIn("资源内容已存在，登记与状态已核对", self.service.message)
        self.assertEqual(len(self.changes), 2)
        self.assertEqual(len(self.completions), 2)
        self.assertEqual(len(self.lease.released), 2)
        self.assertFalse(self.lease.active)
        self.assertFalse(self.confirmations)
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.completions), 2)
        self.service.requestRemove("nightmare-suju")
        self.settle()
        self.assertEqual(self.service.pendingKind, "remove")
        self.assertTrue(self.lease.active)
        self.assertTrue((self.root / IMAGE).exists())
        self.service.confirmOperation(True)
        self.settle()
        self.assertFalse((self.root / IMAGE).exists())
        self.assertEqual((self.root / "player-custom-route.json").read_bytes(), b"keep")
        backups = list((self.root / "work/map-package-removals").glob("*/installed-receipt.json"))
        self.assertEqual(len(backups), 1)
        self.assertFalse(self.lease.active)
        self.install()
        self.assertEqual((self.root / IMAGE).read_bytes(), b"route")

    def test_helper_success_popup_waits_for_scan_and_acknowledges_once(self):
        self.install()
        self.completions.clear()
        receipt = dict(success=True, requestId="fixture-popup-request", packageId="nightmare-suju", added=1)
        self.service.recordImportReceipt(receipt)
        self.assertFalse(self.completions)
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.completions), 1)
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.completions), 1)
        self.service.acknowledgeImportCompletion()
        self.service.recordImportReceipt(receipt)
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.completions), 1)
        self.service.recordImportReceipt(dict(receipt, success=False))
        self.service.refresh()
        self.settle()
        self.assertEqual(len(self.completions), 1)

    def test_first_import_scan_failure_does_not_publish_completion(self):
        started, finish = threading.Event(), threading.Event()
        def failed_scan(_cancel):
            started.set()
            finish.wait(3)
            raise OSError("fixture post-import scan failed")
        with patch.object(self.service, "_snapshot", side_effect=failed_scan):
            self.service.importArchive(QUrl.fromLocalFile(str(self.package())).toString())
            try:
                deadline = time.monotonic() + 3
                while not started.is_set() and time.monotonic() < deadline:
                    APP.processEvents()
                    time.sleep(.005)
                self.assertTrue(started.is_set())
                self.assertTrue(self.service.busy)
                self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
                self.assertIn("正在核对", self.service.message)
                self.assertNotIn("登记与状态已核对", self.service.message)
            finally:
                finish.set()
            self.settle()
        self.assertIn("fixture post-import scan failed", self.service.message)
        self.assertNotIn("登记与状态已核对", self.service.message)
        self.assertFalse(self.lease.active)
        self.service.refresh()
        self.settle()
        self.assertTrue(any(r["status"] == "installed" for r in self.service.packageRows))
        self.assertEqual(self.service.message, "本地资源已核对。")

    def test_first_import_scan_cancel_keeps_committed_result_without_success_footer(self):
        started, finish = threading.Event(), threading.Event()
        actual_snapshot = self.service._snapshot
        def delayed_scan(cancel):
            started.set()
            finish.wait(3)
            return actual_snapshot(cancel)
        with patch.object(self.service, "_snapshot", side_effect=delayed_scan):
            self.service.importArchive(QUrl.fromLocalFile(str(self.package())).toString())
            try:
                deadline = time.monotonic() + 3
                while not started.is_set() and time.monotonic() < deadline:
                    APP.processEvents()
                    time.sleep(.005)
                self.assertTrue(started.is_set())
                self.service.cancel()
            finally:
                finish.set()
            self.settle()
        self.assertIn("取消", self.service.message)
        self.assertNotIn("登记与状态已核对", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
        self.assertTrue((self.root / "work/map-packages/nightmare-suju.json").is_file())
        self.assertFalse(self.lease.active)
        self.service.refresh()
        self.settle()
        self.assertEqual(self.service.message, "本地资源已核对。")

    def test_existing_same_bytes_without_receipt_still_registers(self):
        self.write(IMAGE, b"route")
        receipt = self.root / "work/map-packages/nightmare-suju.json"
        self.assertFalse(receipt.exists())
        self.install(self.package())
        self.assertTrue(receipt.is_file())
        self.assertEqual(json.loads(receipt.read_text())["alreadyPresent"], 1)
        self.assertEqual(json.loads(receipt.read_text())["added"], 0)
        self.assertIn("内容已存在", self.service.message)
        self.assertEqual(len(self.changes), 1)
        self.assertFalse(self.lease.active)
        self.assertTrue(any(r["status"] == "installed" for r in self.service.packageRows))

    def test_helper_duplicate_notice_survives_scan_and_failure_is_not_duplicate(self):
        archive = self.package()
        self.install(archive)
        result = importer.install_package(archive, self.root, application_stopped=True)
        self.assertEqual((result['added'], result['alreadyPresent']), (0, 1))
        self.changes.clear()
        self.service.recordImportReceipt(dict(result, success=True))
        self.assertIn("内容已存在", self.service.message)
        self.assertIn("正在核对", self.service.message)
        self.assertFalse(self.changes)
        self.service.refresh()
        self.settle()
        self.assertIn("内容已存在，实际状态已核对", self.service.message)
        self.assertEqual(len(self.changes), 1)
        self.service.refresh()
        self.settle()
        self.assertIn("内容已存在，实际状态已核对", self.service.message)
        self.assertEqual(len(self.changes), 1)
        self.service.recordImportReceipt(dict(result, success=False, reason="fixture import failed"))
        self.service.refresh()
        self.settle()
        self.assertIn("fixture import failed", self.service.message)
        self.assertNotIn("内容已存在", self.service.message)
        self.assertEqual(len(self.changes), 1)
        self.service.recordImportReceipt(dict(result, success=True, replaced=1, replaceFiles=[{}]))
        self.assertNotIn("内容已存在", self.service.message)

    def test_owned_upgrade_confirmation_cancel_then_commit(self):
        self.install(self.package("old.zip", b"old"))
        archive = self.package("new.zip", b"new")
        self.install(archive)
        self.assertEqual(self.service.pendingKind, "overwrite")
        self.assertEqual((self.root / IMAGE).read_bytes(), b"old")
        self.service.confirmOperation(False)
        self.assertFalse(self.lease.active)
        self.install(archive)
        self.service.confirmOperation(True)
        self.settle()
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        self.assertTrue(list((self.root / "work/map-packages/upgrades").glob("*/journal.json")))

    def test_removal_rejects_changed_confirmation_inventory(self):
        self.install()
        self.service.requestRemove("nightmare-suju")
        self.settle()
        self.write(IMAGE, b"changed after confirmation")
        self.service.confirmOperation(True)
        self.settle()
        self.assertIn("reinspect", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"changed after confirmation")

    def test_upgrade_rejects_changed_archive(self):
        self.install(self.package("old.zip", b"old"))
        archive = self.package("new.zip", b"new")
        self.install(archive)
        archive.write_bytes(b"replaced archive")
        self.service.confirmOperation(True)
        self.settle()
        self.assertEqual((self.root / IMAGE).read_bytes(), b"old")
        self.assertIn("失败", self.service.message)
        self.assertFalse(self.lease.active)

    def test_removal_cancel_rolls_back_and_late_install_cancel_reports_commit(self):
        real_install = importer.install_author_package_live
        def install_then_cancel(*args, **kwargs):
            result = real_install(*args, **kwargs)
            self.service._cancel.set()  # Simulate cancellation after receipt commit.
            return result
        with patch.object(importer, "install_author_package_live", side_effect=install_then_cancel):
            self.install()
        self.assertEqual(len(self.changes), 1)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
        self.assertNotIn("已取消", self.service.message)
        self.service.requestRemove("nightmare-suju")
        self.settle()
        real_replace = manager.os.replace
        def move_then_cancel(source, target):
            real_replace(source, target)
            self.service._cancel.set()
        with patch.object(manager.os, "replace", side_effect=move_then_cancel):
            self.service.confirmOperation(True)
            self.settle()
        self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
        self.assertTrue((self.root / "work/map-packages/nightmare-suju.json").exists())
        self.assertEqual(len(self.changes), 1)
        self.assertFalse(self.lease.active)

    def test_unknown_file_conflict_never_overwrites(self):
        self.write(IMAGE, b"unknown")
        self.install()
        self.assertIn("existing-data-conflict", self.service.message)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"unknown")

    def test_pending_upgrade_explicitly_blocks_writes_and_retains_pointer(self):
        self.write("work/map-packages/pending-upgrade.json", b'{"transactionId":"interrupted"}')
        self.service.refresh()
        self.settle()
        self.assertTrue(self.service.recoveryRequired)
        self.service.importArchive(str(self.package()))
        self.assertFalse(self.service.busy)
        self.assertTrue((self.root / "work/map-packages/pending-upgrade.json").exists())

    def test_scan_off_thread_repeat_suppressed_cancel_and_shutdown_stale(self):
        started, finish = threading.Event(), threading.Event()
        worker_threads = []
        def snapshot(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            started.set()
            finish.wait(3)
            return [{"packageId":"late", "status":"installed"}]
        with patch.object(manager, "managed_author_packages_snapshot", side_effect=snapshot) as mocked:
            self.service.refresh()
            self.assertTrue(started.wait(2))
            self.service.refresh()
            self.service.cancel()
            finish.set()
            self.settle()
            self.assertEqual(mocked.call_count, 1)
            self.assertNotEqual(worker_threads[0], threading.get_ident())
            self.assertEqual(self.service.packageRows, [])
            self.assertFalse(self.lease.active)
        self.service._completed(self.service._token - 1, [{"packageId":"stale"}], None)
        self.assertEqual(self.service.packageRows, [])
        started.clear()
        finish.clear()
        with patch.object(manager, "managed_author_packages_snapshot", side_effect=snapshot):
            self.service.refresh()
            self.assertTrue(started.wait(2))
            self.service.shutdown()
            finish.set()
            self.service.pool.waitForDone(3000)
            APP.processEvents()
        self.assertEqual(self.service.packageRows, [])
        self.assertFalse(self.lease.active)

    def test_lease_rejects_mutation_before_worker_start(self):
        self.lease.allowed = False
        self.install()
        self.assertFalse((self.root / IMAGE).exists())
        self.assertIn("会话", self.service.message)

    def test_missing_marker_rejected_and_marker_change_blocks_worker(self):
        (self.root / ".qt-lab-owner").unlink()
        with self.assertRaises(ValueError):
            ResourceService(self.root)
        self.install()
        self.assertIn("marked", self.service.message)
        self.assertFalse((self.root / IMAGE).exists())

    @unittest.skipUnless(os.environ.get("QT_RESOURCE_PRIVATE_ZIP"), "private ZIP not supplied")
    def test_actual_private_zip(self):
        archive = Path(os.environ["QT_RESOURCE_PRIVATE_ZIP"])
        source = ROOT / "work/qt-lab-installation"
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read("map-package.json"))
        for dependency in manifest["requiresFiles"]:
            data = (source / dependency["path"]).read_bytes()
            self.assertEqual(row(dependency["path"], data), dependency)
            self.write(dependency["path"], data)
        self.install(archive)
        self.assertEqual(self.service.pendingKind, "")
        self.assertTrue(any(r["packageId"] == manifest["packageId"] and r["status"] == "installed"
                            for r in self.service.packageRows), self.service.message)
        self.service.requestRemove(manifest["packageId"])
        self.settle()
        self.service.confirmOperation(True)
        self.settle()
        self.assertTrue(any(r["packageId"] == manifest["packageId"] and r["status"] == "not-installed"
                            for r in self.service.packageRows), self.service.message)


if __name__ == "__main__":
    unittest.main()
