"""Production plaza/gallery handoff with fake HTTP, real conversion and disk IO."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PIL import Image
import numpy as np
from PySide6.QtCore import QObject, Signal

from client_custom_gallery import CustomGalleryService
from client_paths import WORK_ROOT
from client_plaza import PlazaService
from custom_maps import CustomMapLibrary, GalleryStore, RouteError
import route_plaza
from test_client_plaza import APP, until

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_custom_maps import write_package
from test_route_plaza_next import archive_fixture, FakeOpener, manifest_fixture


class FakeAccount(QObject):
    changed = Signal()

    def __init__(self, grant):
        super().__init__()
        self.service = self
        self.revision = 1
        self.loggedIn = True
        self.grant = grant
        self.calls = []

    def authorized(self, method, path, body):
        self.calls.append((method, path, body, threading.get_ident()))
        return deepcopy(self.grant)

    def logout(self):
        self.revision += 1
        self.loggedIn = False
        self.changed.emit()


def registered_library(root, *, explicit=True, legacy=False, template="nightmare-layout-01"):
    write_package(root)
    path = root / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["maps"][0]["templateId"] = template
    if legacy:
        data["sources"] = dict(legacyCatalogSha256="a" * 64)
    if explicit:
        for row in data["maps"][0]["floors"].values():
            # Current display differs from Android template art; this catches
            # accidentally using matrixImageToNative for downloaded cells.
            row["templateImageSize"] = row["imageSize"]
            row["matrixTemplateToNative"] = row["matrixImageToNative"]
            row["imageSize"] = [60, 45]
            row["matrixImageToNative"] = [[4, 0, 0], [0, 4, 0], [0, 0, 1]]
            image_path = root / row["image"]
            Image.new("RGBA", (60, 45), "#eaf2fd").save(image_path)
            row["imageSha256"] = hashlib.sha256(image_path.read_bytes()).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")
    return CustomMapLibrary(root)


class PlazaGalleryHandoffTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="plaza-gallery-handoff-", dir=WORK_ROOT)
        self.root = Path(self.temp.name)
        self.sandbox = self.root / "sandbox"
        self.sandbox.mkdir()
        (self.sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.default = self.root / "default.json"
        self.default.write_text(json.dumps(dict(id="default", name="内置图库", maps={})), encoding="utf-8")
        self.library = registered_library(self.root / "library")
        self.gallery = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        self.archive, grant = archive_fixture()
        self.account = FakeAccount(grant)
        self.opener = FakeOpener(self.archive)
        self.http_patch = patch("route_plaza.request.build_opener", return_value=self.opener)
        self.http_patch.start()
        self.api_calls = []
        self.plaza = PlazaService(self.account,
            list_function=lambda query: route_plaza.list_routes(query, api=self.fake_api))
        self.guard = self.plaza.isCurrentDownload
        self.batches, self.receipts, self.guard_threads = [], [], []
        self.release = threading.Event()
        self.entered = threading.Event()
        self.plaza.downloadReady.connect(self.handoff)
        self.gallery.plazaImportFinished.connect(self.plaza.finishImport)
        self.gallery.plazaImportFinished.connect(lambda *args: self.receipts.append(args))
        self.addCleanup(self.cleanup)
        until(lambda: bool(self.gallery.snapshot["galleries"]) and not self.gallery.busy)

    def cleanup(self):
        self.release.set()
        self.plaza.shutdown()
        self.gallery.shutdown()
        APP.processEvents()
        self.http_patch.stop()
        self.temp.cleanup()

    def fake_api(self, method, path):
        self.api_calls.append((method, path))
        return [dict(id="version-2", work_id="work-1", revision=2, content_kind="nightmare-routes",
            status="approved", modes=["nightmare"], title="测试路线", owner_username="alice",
            nightmare_map_count=1, download_count=3)]

    def handoff(self, batch):
        self.batches.append(deepcopy(batch))
        self.gallery.acceptPlazaDownload(batch, self.guard, self.plaza.beginImport)

    def download(self):
        self.plaza.search("")
        until(lambda: not self.plaza.busy)
        self.plaza.downloadRoute("version-2")
        until(lambda: not self.plaza.busy and not self.gallery.busy)

    def gallery_call(self, method, *args):
        getattr(self.gallery, method)(*args)
        until(lambda: not self.gallery.busy)
        return self.gallery.snapshot

    def test_signal_to_single_writer_to_ack_persists_native_coordinates(self):
        def guard(request_id, revision):
            self.guard_threads.append(threading.get_ident())
            return self.plaza.isCurrentDownload(request_id, revision)
        self.guard = guard
        self.download()
        self.assertEqual(self.plaza.snapshot["stage"], "installed", self.receipts)
        self.assertEqual(len(self.receipts), 1)
        self.assertIs(type(self.receipts[0][0]), int)
        self.assertEqual(self.receipts[0][0], self.batches[0]["requestId"])
        self.assertTrue(self.receipts[0][1])
        self.assertEqual(self.gallery.pool.maxThreadCount(), 1)
        self.assertEqual(len(set(self.guard_threads)), 1)
        self.assertNotEqual(self.guard_threads[0], threading.get_ident())
        self.assertNotEqual(self.account.calls[0][-1], threading.get_ident())
        self.assertEqual(set(k.lower() for k in self.opener.requests[0][0].headers), {"user-agent"})
        gid = self.gallery.snapshot["selectedGallery"]
        disk = GalleryStore(self.gallery.path, self.default)
        self.assertEqual(disk.plaza_versions(), {"work-1": "version-2"})
        self.assertTrue(disk.readonly(gid))
        self.assertTrue(disk.deletable(gid))
        self.assertEqual([(p["floor"], p["x"], p["y"]) for p in disk.route(gid, "m")],
                         [("1", 150., 30.), ("2", 30., 30.)])
        self.assertEqual(self.gallery.snapshot["markers"][0]["x"], 37.5)
        self.assertEqual(self.gallery.snapshot["markers"][0]["y"], 7.5)
        self.assertTrue(self.gallery.snapshot["readonly"])
        self.assertTrue(self.gallery.snapshot["canDelete"])

    def test_reload_copy_edit_delete_and_builtin_protection(self):
        self.download()
        self.assertEqual(self.plaza.snapshot["stage"], "installed", self.receipts)
        downloaded = self.gallery.snapshot["selectedGallery"]
        original = self.gallery.path.read_bytes()
        self.gallery_call("refresh")
        self.gallery_call("selectGallery", downloaded)
        self.gallery_call("renameGallery", "不得改名")
        self.gallery_call("addPoint", 52.5, 7.5)
        self.assertEqual(self.gallery.path.read_bytes(), original, "downloaded gallery is read-only")
        state = self.gallery_call("copyGallery", "可编辑副本")
        copied = state["selectedGallery"]
        self.assertFalse(state["readonly"])
        self.assertNotEqual(copied, downloaded)
        self.gallery_call("renameGallery", "我的路线")
        self.gallery_call("removePoint", 0)
        disk = GalleryStore(self.gallery.path, self.default)
        self.assertEqual(disk.name(copied), "我的路线")
        self.assertEqual(len(disk.route(copied, "m")), 1)
        self.assertEqual(len(disk.route(downloaded, "m")), 2)
        self.assertNotIn("source", disk.export(copied))
        self.gallery_call("selectGallery", downloaded)
        self.gallery.requestDeleteGallery()
        self.assertEqual(self.gallery.snapshot["pendingKind"], "delete")
        self.gallery_call("confirmOperation", True)
        disk = GalleryStore(self.gallery.path, self.default)
        self.assertIsNone(disk.name(downloaded))
        self.assertIsNotNone(disk.name(copied))
        self.gallery_call("selectGallery", "default")
        self.assertFalse(self.gallery.snapshot["canDelete"])
        self.gallery.requestDeleteGallery()
        self.assertEqual(self.gallery.snapshot["pendingKind"], "")
        self.assertFalse(disk.delete("default"))

    def test_logout_after_conversion_before_write_rejects_old_batch(self):
        checks = []
        def guarded(request_id, revision):
            checks.append(request_id)
            if len(checks) == 2:
                self.entered.set()
                self.release.wait(3)
            return self.plaza.isCurrentDownload(request_id, revision)
        self.guard = guarded
        self.plaza.search("")
        until(lambda: not self.plaza.busy)
        self.plaza.downloadRoute("version-2")
        until(self.entered.is_set)
        self.account.logout()
        self.release.set()
        until(lambda: not self.gallery.busy and not self.plaza.busy)
        self.assertFalse(self.gallery.path.exists())
        self.assertFalse(self.receipts[-1][1])
        self.assertNotEqual(self.plaza.snapshot["stage"], "installed")

    def test_busy_gallery_rejects_and_receipt_releases_plaza(self):
        self.gallery._run(lambda: (self.entered.set(), self.release.wait(3)))
        until(self.entered.is_set)
        self.plaza.search("")
        until(lambda: not self.plaza.busy)
        self.plaza.downloadRoute("version-2")
        until(lambda: bool(self.receipts))
        self.assertFalse(self.receipts[-1][1])
        self.assertFalse(self.plaza.busy)
        self.assertIn("其他操作", self.plaza.snapshot["message"])
        self.assertFalse(self.gallery.path.exists())
        self.release.set()
        until(lambda: not self.gallery.busy)

    def test_logout_during_accepted_write_preserves_accurate_completion(self):
        original_write = self.gallery._write
        def blocked_write(operation):
            self.entered.set()
            if not self.release.wait(3):
                raise AssertionError("isolated writer was not released")
            return original_write(operation)
        with patch.object(self.gallery, "_write", side_effect=blocked_write):
            self.plaza.search("")
            until(lambda: not self.plaza.busy)
            self.plaza.downloadRoute("version-2")
            until(self.entered.is_set)
            batch = self.batches[-1]
            self.assertEqual(self.plaza._committed_request, batch["requestId"])
            self.account.logout()
            self.assertFalse(self.plaza.isCurrentDownload(batch["requestId"], batch["accountRevision"]))
            self.assertTrue(self.plaza.busy)
            self.assertEqual(self.plaza.snapshot["stage"], "importing")
            self.assertFalse(self.plaza.snapshot["canDownload"])
            self.assertNotIn("不会导入", self.plaza.snapshot["message"])
            self.assertNotIn("未导入", self.plaza.snapshot["message"])
            self.release.set()
            until(lambda: not self.gallery.busy and not self.plaza.busy)
        self.assertEqual(len(self.receipts), 1)
        self.assertTrue(self.receipts[0][1], self.receipts)
        self.assertEqual(self.plaza.snapshot["stage"], "installed")
        self.assertEqual(self.plaza.snapshot["message"], self.receipts[0][2])
        disk = GalleryStore(self.gallery.path, self.default)
        gid = self.gallery.snapshot["selectedGallery"]
        self.assertTrue(disk.readonly(gid))
        self.assertEqual(disk.plaza_versions(), {"work-1": "version-2"})
        self.assertEqual(len(disk.route(gid, "m")), 2)

    def test_duplicate_download_updates_same_gallery_and_preserves_user_copy(self):
        self.download()
        self.assertEqual(self.plaza.snapshot["stage"], "installed", self.receipts)
        downloaded = self.gallery.snapshot["selectedGallery"]
        self.gallery_call("copyGallery", "保留副本")
        copied = self.gallery.snapshot["selectedGallery"]
        self.download()
        self.assertEqual(self.gallery.snapshot["selectedGallery"], downloaded)
        disk = GalleryStore(self.gallery.path, self.default)
        self.assertEqual(len(disk.galleries()), 3)
        self.assertIsNotNone(disk.name(copied))

    def test_partial_import_reports_unavailable_maps_and_dropped_points(self):
        manifest = manifest_fixture()
        manifest["maps"][0]["routePlan"]["points"].append(dict(floor="1", cell=999999, kind="via"))
        manifest["maps"].append(dict(routePlan=dict(templateId="nightmare-layout-28", points=[])))
        archive, grant = archive_fixture(manifest)
        grant["package"]["map_count"] = 2
        self.account.grant = grant
        self.opener.archive = archive
        self.download()
        self.assertEqual(self.plaza.snapshot["stage"], "installed", self.receipts)
        message = self.plaza.snapshot["message"]
        self.assertIn("跳过", message)
        self.assertIn("1 张", message)
        self.assertIn("1 个", message)
        self.assertEqual(message, self.gallery.message)


class TemplateRegistrationContractTests(unittest.TestCase):
    def test_legacy_provenance_explicit_override_and_native_map_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = registered_library(root / "legacy", explicit=False, legacy=True)
            self.assertEqual(legacy.templates(), {"m": "nightmare-layout-01"})
            asset = legacy.floor("m", "1")
            self.assertEqual(asset.template_size, asset.image_size)
            np.testing.assert_array_equal(asset.template_to_native, asset.to_native)
            maps, _ = route_plaza.convert_manifest(legacy, manifest_fixture())
            self.assertEqual(maps["m"]["points"][0]["x"], 150.)
            explicit = registered_library(root / "explicit", explicit=True, legacy=True)
            asset = explicit.floor("m", "1")
            self.assertEqual(asset.template_size, (120, 90))
            self.assertEqual(asset.image_size, (60, 45))
            self.assertFalse(np.array_equal(asset.template_to_native, asset.to_native))
            unknown = registered_library(root / "unknown", explicit=False, legacy=False)
            with self.assertRaisesRegex(RouteError, "缺少安卓模板坐标注册"):
                route_plaza.convert_manifest(unknown, manifest_fixture())
            native = registered_library(root / "native", explicit=False, legacy=True,
                                        template="nightmare-layout-104026")
            self.assertIsNone(native.floor("m", "1").template_size)
            self.assertIsNone(native.floor("m", "1").template_to_native)


if __name__ == "__main__":
    unittest.main()
