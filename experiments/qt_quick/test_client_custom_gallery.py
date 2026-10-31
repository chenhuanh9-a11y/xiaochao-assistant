"""Synthetic gallery data, real planner/store, and one hidden QML surface."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PIL import Image
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from client_custom_gallery import CustomGalleryService, CustomMapLibrary, GalleryStore, GALLERY_SCHEMA
from client_paths import WORK_ROOT, RuntimePaths

APP = QGuiApplication.instance() or QGuiApplication([])


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.002)
    raise AssertionError("Bounded gallery operation did not complete")


def make_library(folder):
    folder.mkdir(parents=True)
    topology = dict(points=[[0, 0], [60, 0], [0, 60], [60, 60], [120, 60], [180, 60]],
                    pointRooms=["R", "R", "R", "R", "C", "D"],
                    links=[[0, 1, "room"], [0, 2, "room"], [1, 3, "room"],
                           [2, 3, "room"], [3, 4, "door"], [4, 5, "road"]])
    floors = {}
    for floor in ("1", "2"):
        image = folder / f"{floor}.png"
        Image.new("RGBA", (100, 100), "#eaf2fd").save(image)
        graph = folder / f"{floor}.json"
        graph.write_text(json.dumps(topology), encoding="utf-8")
        floors[floor] = dict(image=image.name, imageSha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            topology=graph.name, topologySha256=hashlib.sha256(graph.read_bytes()).hexdigest(),
            imageSize=[100, 100], nativeShape=[200, 200], matrixImageToNative=[[2, 0, 0], [0, 2, 0], [0, 0, 1]])
        floors[floor]["maskSha256"] = hashlib.sha256(bytes([1]) * 40000).hexdigest()
    manifest = dict(schema="xingtu-custom-maps-v1", version="synthetic-test", maps=[
        dict(mapId="synthetic-map", name="测试地图", templateNumber=1, floors=floors,
             stairs=[dict(**{"from": dict(floor="1", x=120, y=60), "to": dict(floor="2", x=120, y=60)}, cost=60)])])
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return CustomMapLibrary(folder)


class CustomGalleryTests(unittest.TestCase):
    def setUp(self):
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="qt-custom-gallery-tests-", dir=WORK_ROOT)
        self.folder = Path(self.temp.name)
        self.sandbox = self.folder / "sandbox"
        self.sandbox.mkdir()
        (self.sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.default = self.folder / "default.json"
        self.default.write_text(json.dumps(dict(id="default", name="默认路线", maps={})), encoding="utf-8")
        self.library = make_library(self.folder / "synthetic-library")
        self.service = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        self.confirmations = []
        self.service.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        until(lambda: bool(self.service.snapshot["galleries"]))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.service.shutdown()
        APP.processEvents()
        self.temp.cleanup()

    def call(self, method, *args):
        getattr(self.service, method)(*args)
        until(lambda: not self.service.busy)
        return self.service.snapshot

    def test_real_store_crud_and_default_readonly(self):
        state = self.call("addPoint", 20., 20.)
        self.assertFalse(self.service.path.exists())
        self.assertTrue(state["readonly"])
        state = self.call("copyGallery", "自己的路线")
        gid = state["selectedGallery"]
        self.assertNotEqual(gid, "default")
        self.assertFalse(state["readonly"])
        self.call("renameGallery", "新名字")
        self.assertEqual(GalleryStore(self.service.path, self.default).name(gid), "新名字")
        self.service.requestDeleteGallery()
        self.assertEqual(len(self.confirmations), 1)
        self.service.confirmOperation(False)
        self.assertTrue(self.service.path.exists())
        self.service.requestDeleteGallery()
        self.call("confirmOperation", True)
        self.assertEqual(len(self.service.snapshot["galleries"]), 1)

    def test_production_legacy_routes_migrate_once_beside_settings_and_preserve_source(self):
        # Actual production path layout under this test's private root only.
        paths = RuntimePaths(self.sandbox, True)
        paths.settings_path.parent.mkdir(parents=True, exist_ok=True)
        source = paths.settings_path.with_name("custom-routes.json")
        original = json.dumps(dict(schema="xingtu-custom-routes-v1", useInGame=True, maps={"synthetic-map": {
            "1": dict(points=[[20, 20], [120, 60]], color="#abcdef", snap=False),
            "2": dict(points=[[180, 60]], color="#abcdef")}})).encode("utf-8")
        source.write_bytes(original)
        existing = GalleryStore(paths.custom_galleries_path, self.default).create("已有路线")
        with patch("client_custom_gallery.runtime_paths", return_value=paths):
            service = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        try:
            until(lambda: bool(service.snapshot["galleries"]) and not service.busy)
            first = paths.custom_galleries_path.read_bytes()
            rows = service.snapshot["galleries"]
            self.assertEqual(len(rows), 4)  # default, existing, two separate legacy floors
            self.assertEqual(service._store.name(existing), "已有路线")
            imported = [service._store.route(row["id"], "synthetic-map") for row in rows
                        if row["id"].startswith("legacy-import-")]
            self.assertEqual(sorted(len(points) for points in imported), [1, 2])
            self.assertTrue(all(len({point["floor"] for point in points}) == 1 for points in imported))
            self.assertEqual(service.snapshot["inUse"], "")
            service.refresh()
            until(lambda: not service.busy)
            self.assertEqual(paths.custom_galleries_path.read_bytes(), first)
            self.assertEqual(len(service.snapshot["galleries"]), 4)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(json.loads(first)["legacyRouteMigrations"], [hashlib.sha256(original).hexdigest()])
        finally:
            service.shutdown()
            APP.processEvents()

    def test_source_legacy_routes_stay_isolated(self):
        source = self.service.paths.settings_path.with_name("custom-routes.json")
        original = json.dumps(dict(schema="xingtu-custom-routes-v1", maps={"synthetic-map": {
            "1": dict(points=[[20, 20]], color="#abcdef")}})).encode("utf-8")
        source.write_bytes(original)
        state = self.call("refresh")
        self.assertEqual(len(state["galleries"]), 1)
        self.assertFalse(self.service.path.exists())
        self.assertEqual(source.read_bytes(), original)

    def test_real_graph_pick_plan_edit_order_undo_and_floor(self):
        self.call("createGallery", "线路")
        self.call("addPoint", 20., 20.)
        state = self.call("addPoint", 60., 30.)
        self.assertEqual(len(state["points"]), 2)
        self.assertEqual(state["points"][1]["x"], 120)
        self.assertTrue(state["lines"])
        self.assertEqual(state["points"][0]["kind"], "room")
        self.call("selectFloor", "2")
        state = self.call("addPoint", 90., 30.)
        self.assertEqual(len(state["points"]), 3)
        self.assertFalse(state["planError"])
        self.assertEqual(state["selectedFloor"], "2")
        prior = state["points"]
        self.call("movePoint", 2, -1)
        self.assertNotEqual(self.service.snapshot["points"], prior)
        self.call("undo")
        self.assertEqual(self.service.snapshot["points"], prior)
        self.call("redo")
        self.call("removePoint", 1)
        self.assertEqual(len(self.service.snapshot["points"]), 2)
        self.service.requestClearRoute()
        self.call("confirmOperation", True)
        self.assertFalse(self.service.snapshot["points"])
        self.call("undo")
        self.assertEqual(len(self.service.snapshot["points"]), 2)
        self.call("createGallery", "另一个")
        self.assertFalse(self.service.snapshot["canUndo"])

    def test_import_export_is_existing_schema_owned_and_confirmed(self):
        payload = dict(schema=GALLERY_SCHEMA, id="foreign", name="导入图集", maps={})
        source = self.folder / "chosen.json"
        source.write_text(json.dumps(payload), encoding="utf-8")
        before = source.read_bytes()
        state = self.call("importGallery", QUrl.fromLocalFile(str(source)).toString())
        self.assertEqual(source.read_bytes(), before)
        self.assertNotEqual(state["selectedGallery"], "foreign")
        self.call("exportGallery", "route.json")
        target = self.sandbox / "exports/route.json"
        self.assertEqual(json.loads(target.read_text())["schema"], GALLERY_SCHEMA)
        self.service.exportGallery("route.json")
        self.assertTrue(self.service.snapshot["pendingKind"])
        target.write_text("external-change", encoding="utf-8")
        self.call("confirmOperation", True)
        self.assertEqual(target.read_text(), "external-change")
        self.call("exportGallery", "../escape.json")
        self.assertFalse((self.sandbox / "escape.json").exists())

    def test_export_selected_production_path_preserves_fullpath_and_conflicts(self):
        paths = RuntimePaths(self.sandbox, True)
        with patch("client_custom_gallery.runtime_paths", return_value=paths):
            service = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        try:
            until(lambda: bool(service.snapshot["galleries"]) and not service.busy)
            self.assertEqual(Path(service.exportDirectoryUrl.toLocalFile()), self.sandbox)
            # Explicit selection outside the installation, still wholly inside
            # this test's private directory; no real user destinations.
            target = self.folder / "selected-output" / "chosen.json"
            url = QUrl.fromLocalFile(str(target)).toString()
            service.exportGalleryTo(url)
            until(lambda: not service.busy)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), service._store.export("default"))
            service.exportGalleryTo(url)
            self.assertEqual(service._pending["path"], str(target))
            target.write_bytes(b"external-change")
            service.confirmOperation(True)
            until(lambda: not service.busy)
            self.assertEqual(target.read_bytes(), b"external-change")
            self.assertIn("已变化", service.message)
            service.exportGalleryTo(url)
            service.confirmOperation(True)
            until(lambda: not service.busy)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), service._store.export("default"))
            self.assertFalse((service.exports / target.name).exists())
            self.assertFalse(list(target.parent.glob(".gallery-export-*.tmp")))
            service.exports.mkdir()
            self.assertEqual(Path(service.exportDirectoryUrl.toLocalFile()), service.exports)
        finally:
            service.shutdown()
            APP.processEvents()
        self.call("exportGalleryTo", QUrl.fromLocalFile(str(self.folder / "source-escape.json")).toString())
        self.assertFalse((self.folder / "source-escape.json").exists())
        self.assertIn("当前测试数据目录", self.service.message)
        self.call("exportGalleryTo", "https://example.invalid/route.json")
        self.assertIn("本地", self.service.message)

    def test_disk_conflict_and_failed_store_write_do_not_claim_save(self):
        self.call("createGallery", "已有")
        old = self.service.path.read_bytes()
        with patch.object(GalleryStore, "_save", return_value=False):
            state = self.call("createGallery", "失败")
        self.assertEqual(self.service.path.read_bytes(), old)
        self.assertIn("保存", state["message"])
        self.assertEqual(len(self.service._store.galleries()), 2)
        data = json.loads(old)
        data["galleries"][0]["name"] = "外部修改"
        self.service.path.write_text(json.dumps(data), encoding="utf-8")
        self.call("renameGallery", "不能覆盖")
        self.assertEqual(json.loads(self.service.path.read_text())["galleries"][0]["name"], "外部修改")

    def test_corrupt_file_is_preserved_and_use_requires_real_receipt(self):
        self.call("createGallery", "路线")
        self.assertFalse(self.service.snapshot["canUse"])
        self.service.setUseHandler(lambda gid: dict(success=False, message="未应用"))
        self.service.useGallery()
        self.assertFalse(self.service.snapshot["inUse"])
        self.service.setUseHandler(lambda gid: dict(success=True, applied=False, message="正在保存"))
        self.service.useGallery()
        self.assertFalse(self.service.snapshot["inUse"])
        self.service.setUseHandler(lambda gid: dict(success=True, applied=True, message="已应用"))
        self.service.useGallery()
        self.assertEqual(self.service.snapshot["inUse"], self.service.snapshot["selectedGallery"])
        self.service.requestDeleteGallery()
        self.call("confirmOperation", True)
        self.assertEqual(len(self.service.snapshot["galleries"]), 2)
        self.service.path.write_text("{broken", encoding="utf-8")
        self.call("refresh")
        self.call("createGallery", "不能覆盖")
        self.assertEqual(self.service.path.read_text(), "{broken")

    def test_qml_surface_loads_with_real_service_and_updates(self):
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        engine.rootContext().setContextProperty("customGalleryService", self.service)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientCustomGallery.qml"))))
        self.assertEqual(len(engine.rootObjects()), 1, warnings)
        self.call("createGallery", "QML路线")
        self.call("addPoint", 20., 20.)
        APP.processEvents()
        self.assertEqual(len(engine.rootObjects()[0].property("state")["points"]), 1)
        self.assertFalse(warnings)
        engine.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
