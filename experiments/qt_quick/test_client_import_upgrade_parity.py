"""Synthetic archives only; no installed executable or native helper launch."""
import json
import zipfile
from unittest import TestCase
from unittest.mock import patch, Mock
from PySide6.QtCore import QUrl

import test_client_resources as fixtures
from test_client_resources import IMAGE, APP, row
from client_resources import importer
from client_import_handoff import ImportHandoff, helper


class ImportUpgradeParityTests(TestCase):
    setUp = fixtures.ResourceServiceTests.setUp
    cleanup = fixtures.ResourceServiceTests.cleanup
    write = fixtures.ResourceServiceTests.write
    package = fixtures.ResourceServiceTests.package
    settle = fixtures.ResourceServiceTests.settle
    install = fixtures.ResourceServiceTests.install

    def core_package(self, name, data):
        path = self.folder / name
        core = "_internal/assets/suju/core.json"
        manifest = dict(schemaVersion=1, revision=1, packageId="nightmare-suju",
            clientCompatibility=importer.CLIENT_COMPATIBILITY, requiresFiles=self.dependencies,
            files=[row(core, data)])
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("map-package.json", json.dumps(manifest))
            archive.writestr("data/" + core, data)
        return path, core

    def test_author_replacement_uses_temporary_live_journal_and_releases_lease(self):
        self.install(self.package("old.zip", b"old"))
        launch = Mock(side_effect=AssertionError("author upgrade must remain live"))
        self.service.setImportLauncher(launch)
        self.install(self.package("new.zip", b"new"))
        self.assertEqual(self.service.pendingKind, "")
        self.assertFalse(self.confirmations)
        self.assertEqual((self.root / IMAGE).read_bytes(), b"new")
        receipt = json.loads((self.root / "work/map-packages/nightmare-suju.json").read_text())
        self.assertTrue(receipt["liveImport"])
        self.assertFalse(list((self.root / "work/map-packages/lifecycle").iterdir()))
        self.assertFalse(self.lease.active)
        launch.assert_not_called()

    def test_author_replacement_changed_receipt_rejects_original_consent(self):
        self.install(self.package("old.zip", b"old"))
        receipt = self.root / "work/map-packages/nightmare-suju.json"
        original_install = self.service._install
        def changed_before_worker(path, inspection):
            value = json.loads(receipt.read_text()); value["syntheticChange"] = True
            receipt.write_text(json.dumps(value))
            original_install(path, inspection)
        with patch.object(self.service, "_install", side_effect=changed_before_worker):
            self.install(self.package("new.zip", b"new"))
        self.assertIn("reinspect", self.problems[-1]["raw"])
        self.assertEqual((self.root / IMAGE).read_bytes(), b"old")

    def test_closed_import_launch_has_no_commit_notification_and_keeps_lease(self):
        old, core = self.core_package("old.zip", b"old")
        new, _ = self.core_package("new.zip", b"new")
        importer.install_package(old, self.root, application_stopped=True)
        self.changes.clear()
        launch = Mock(return_value="request-123")
        self.service.setImportLauncher(launch)
        close = Mock()
        self.service.importCloseRequested.connect(close)
        self.install(new)
        self.service.confirmOperation(True)
        self.settle()
        close.assert_called_once()
        launch.assert_called_once()
        self.assertEqual((self.root / core).read_bytes(), b"old")
        self.assertTrue(self.lease.active)
        self.assertFalse(self.changes)
        self.service.importArchive(str(self.package("ignored.zip")))
        self.assertFalse(self.service.busy)

    def test_verified_download_uses_the_same_core_handoff(self):
        source, _ = self.core_package("download.zip", b"fixture")
        archive = self.root / "work/map-packages/downloads/package.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(source.read_bytes())
        import hashlib
        expected = dict(packageId="nightmare-suju", bytes=archive.stat().st_size,
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
        launch = Mock(return_value="download-request")
        self.service.setImportLauncher(launch)
        self.assertTrue(self.service.importVerifiedArchive(QUrl.fromLocalFile(str(archive)).toString(),
            expected, self.service.operationGeneration), self.service.message)
        self.settle()
        self.assertEqual(self.service.pendingKind, "overwrite")
        self.service.confirmOperation(False)
        launch.assert_not_called()
        self.assertFalse(self.lease.active)

    def test_live_upgrade_cancel_rolls_back_before_receipt_commit(self):
        self.install(self.package("old.zip", b"old"))
        import map_package_upgrade as upgrade
        original = upgrade.os.link
        def link_then_cancel(source, target, *args, **kwargs):
            original(source, target, *args, **kwargs)
            self.service._cancel.set()
        with patch.object(upgrade.os, "link", side_effect=link_then_cancel):
            self.install(self.package("new.zip", b"new"))
        self.assertEqual((self.root / IMAGE).read_bytes(), b"old")
        self.assertFalse(self.lease.active)

    def test_launcher_failure_preserves_resources_and_does_not_close(self):
        self.install(self.package("old.zip", b"old"))
        self.service.setImportLauncher(Mock(side_effect=FileNotFoundError("missing helper")))
        close = Mock(); self.service.importCloseRequested.connect(close)
        with patch.object(importer, "supports_live_author_import", return_value=False):
            self.install(self.package("new.zip", b"new"))
            self.service.confirmOperation(True); self.settle()
        close.assert_not_called()
        self.assertIn("missing helper", self.problems[-1]["raw"])
        self.assertFalse(self.lease.active)

    def test_owner_persists_binding_cancels_and_reads_only_matching_receipt(self):
        owner = ImportHandoff(self.root)
        inspection = {"archiveSha256": "a" * 64}
        with patch.object(helper, "launch_import_helper", return_value=object()) as launch:
            request = owner.launch(self.package(), inspection)
        self.assertEqual(launch.call_args.kwargs, {"wait_seconds": 120})
        self.assertEqual(json.loads(owner.pending.read_text())["requestId"], request)
        self.assertFalse(owner.read_receipt()["success"])
        result = dict(requestId=request, archiveSha256="a" * 64, success=True,
            parentExit="exited-matching-process-handle", completedAtUTC="fixture", added=1)
        receipt = helper.receipt_path(self.root)
        receipt.write_text(json.dumps(result))
        self.assertTrue(owner.read_receipt()["success"])
        result["requestId"] = "wrong-request"; receipt.write_text(json.dumps(result))
        self.assertFalse(owner.read_receipt()["success"])
        owner.cancel()
        self.assertTrue((receipt.parent / ("cancel-" + request)).exists())


if __name__ == "__main__":
    import unittest
    unittest.main()
