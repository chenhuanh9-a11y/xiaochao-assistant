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

    def test_real_import_repeat_remove_and_reimport_preserve_unknown(self):
        self.write("player-custom-route.json", b"keep")
        self.install()
        self.assertEqual((self.root / IMAGE).read_bytes(), b"route")
        self.assertEqual(len(self.changes), 1)
        self.assertTrue(any(r["status"] == "installed" for r in self.service.packageRows))
        self.install()
        self.assertFalse(self.confirmations)
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
        self.assertIn("marker", self.service.message)
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
