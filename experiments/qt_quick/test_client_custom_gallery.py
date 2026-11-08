"""Synthetic gallery data, real planner/store, and one hidden QML surface."""
import errno
import hashlib
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import textwrap
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
import map_package_upgrade as files

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
        from client_settings import SettingsService
        self.settings = SettingsService(self.sandbox)
        self.confirmations = []
        self.service.confirmationRequested.connect(lambda *args: self.confirmations.append(args))
        until(lambda: bool(self.service.snapshot["galleries"]))
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.settings.shutdown()
        self.service.shutdown()
        APP.processEvents()
        self.temp.cleanup()

    def call(self, method, *args):
        getattr(self.service, method)(*args)
        until(lambda: not self.service.busy)
        return self.service.snapshot

    def test_bom_gallery_survives_qt_precheck_and_store_load(self):
        original = json.dumps(dict(schema=GALLERY_SCHEMA, futureField={"keep": True},
            galleries=[dict(id="mine", name="保留", maps={})])).encode("utf-8-sig")
        self.service.path.parent.mkdir(parents=True, exist_ok=True)
        self.service.path.write_bytes(original)
        state = self.call("refresh")
        self.assertEqual(state["dataError"], "")
        self.assertIn("mine", [row["id"] for row in state["galleries"]])
        self.assertEqual(self.service.path.read_bytes(), original)
        self.assertEqual(list(self.service.path.parent.glob("*.unreadable-*.json")), [])

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

    def test_duplicate_gallery_labels_select_and_use_exact_ids(self):
        first = self.call("createGallery", "同名路线")["selectedGallery"]
        second = self.call("createGallery", "同名路线")["selectedGallery"]
        self.assertNotEqual(first, second)
        self.assertEqual([row["id"] for row in self.service.snapshot["galleries"]
                          if row["name"] == "同名路线"], [first, second])
        used = []
        self.service.setUseHandler(lambda gid: used.append(gid) or dict(success=True, applied=True))
        self.call("selectGallery", first)
        self.service.useGallery()
        self.call("selectGallery", second)
        self.service.useGallery()
        self.assertEqual(used, [first, second])
        self.assertEqual(self.service.snapshot["inUse"], second)

    def test_downloaded_gallery_stays_readonly_but_can_be_deleted(self):
        store = GalleryStore(self.service.path, self.default)
        gid = store.install_plaza("下载路线", {"synthetic-map": dict(
            points=[dict(floor="1", x=30, y=30, kind="via")])},
            dict(workId="fixture", versionId="v1", author="fixture"))
        self.call("refresh")
        state = self.call("selectGallery", gid)
        self.assertTrue(state["readonly"])
        self.assertTrue(state["canDelete"])
        self.call("renameGallery", "不应重命名")
        self.assertEqual(GalleryStore(self.service.path, self.default).name(gid), "下载路线")
        self.service.requestDeleteGallery()
        self.assertEqual(len(self.confirmations), 1)
        self.call("confirmOperation", True)
        self.assertNotIn(gid, [row["id"] for row in self.service.snapshot["galleries"]])

    def test_default_gallery_cannot_request_deletion(self):
        self.assertEqual(self.service.snapshot["selectedGallery"], "default")
        self.assertFalse(self.service.snapshot["canDelete"])
        self.service.requestDeleteGallery()
        self.assertEqual(self.confirmations, [])
        self.assertFalse(self.service.path.exists())

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
        engine.rootContext().setContextProperty("settingsService", self.settings)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientCustomGallery.qml"))))
        self.assertEqual(len(engine.rootObjects()), 1, warnings)
        self.call("createGallery", "QML路线")
        self.call("addPoint", 20., 20.)
        APP.processEvents()
        self.assertEqual(len(engine.rootObjects()[0].property("state")["points"]), 1)
        self.assertFalse(warnings)
        engine.deleteLater()
        APP.processEvents()



def _unsupported_export_link():
    return OSError(errno.EOPNOTSUPP, 'synthetic no-hardlink filesystem')


def _export_stamp(path):
    info = path.stat()
    return (info.st_dev, info.st_ino), path.read_bytes()


class CustomGalleryExportTests(unittest.TestCase):
    """Fault only link capability; use real owned moves over synthetic routes.

    POSIX uses renameat2; native Windows runs the existing same-handle adapter.
    Process loss leaves a complete staging file or complete target, without a
    package journal. These tests do not cover power loss or hostile POSIX races.
    """
    setUp = CustomGalleryTests.setUp
    cleanup = CustomGalleryTests.cleanup
    call = CustomGalleryTests.call

    def target(self, name='route.json'):
        return self.sandbox / 'exports' / name

    def assert_export(self, target):
        self.assertEqual(json.loads(target.read_text('utf-8')), self.service._store.export('default'))
        self.assertFalse(self.service.path.exists())
        self.assertFalse((target.parent / 'work').exists())

    def assert_no_temporary(self):
        self.assertEqual(list(self.target().parent.glob('.gallery-export-*.tmp')), [])

    def test_normal_export_keeps_hardlinks_and_original_feedback(self):
        original_link = os.link
        with patch('client_custom_gallery.os.link', wraps=original_link) as link, \
                patch.object(files, '_move_owned', wraps=files._move_owned) as move:
            state = self.call('exportGallery', 'route.json')
        self.assertEqual(link.call_count, 1)
        move.assert_not_called()
        self.assert_export(self.target())
        self.assertIn('已导出到', state['message'])
        self.assert_no_temporary()

    def test_explicitly_unsupported_links_export_via_same_object_move(self):
        errors = [OSError(code, 'unsupported') for code in
                  {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP}]
        for code in (1, 50):
            error = OSError(errno.EACCES, 'Windows unsupported')
            error.winerror = code
            errors.append(error)
        move = files._move_owned
        for index, error in enumerate(errors):
            with self.subTest(errno=error.errno, winerror=getattr(error, 'winerror', None)):
                before = []
                def record(source, target, row, identity, **kwargs):
                    before.append(_export_stamp(source))
                    self.assertEqual(list(before[0][0]), identity)
                    self.assertTrue(kwargs['linkless'])
                    self.assertEqual(source.parent, target.parent)
                    return move(source, target, row, identity, **kwargs)
                target = self.target(f'route-{index}.json')
                with patch('client_custom_gallery.os.link', side_effect=error), \
                        patch.object(files, '_move_owned', side_effect=record):
                    self.service._write_export(target, 'default')
                self.assertEqual(_export_stamp(target), before[0])
                self.assert_export(target)
                self.assert_no_temporary()

    def test_permission_io_space_cross_volume_and_collision_never_downgrade(self):
        errors = [OSError(code, 'synthetic real failure') for code in
                  (errno.EACCES, errno.EPERM, errno.EIO, errno.ENOSPC, errno.EXDEV, errno.EEXIST)]
        for code in (5, 32, 80, 183):
            error = OSError(errno.EOPNOTSUPP, 'Windows non-capability failure')
            error.winerror = code
            errors.append(error)
        for error in errors:
            with self.subTest(errno=error.errno, winerror=getattr(error, 'winerror', None)):
                with patch('client_custom_gallery.os.link', side_effect=error), \
                        patch.object(files, '_move_owned', wraps=files._move_owned) as move:
                    state = self.call('exportGallery', 'route.json')
                move.assert_not_called()
                self.assertFalse(self.target().exists())
                self.assertNotIn('已导出', state['message'])
                self.assertIn(error.strerror, state['message'])
                self.assert_no_temporary()

    def test_move_failure_is_reported_without_copy_or_replace(self):
        for code in (errno.ENOSYS, errno.EACCES, errno.EIO):
            with self.subTest(errno=code), \
                    patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()), \
                    patch.object(files, '_move_owned', side_effect=OSError(code, 'move unavailable')), \
                    patch('client_custom_gallery.os.replace') as replace:
                state = self.call('exportGallery', 'route.json')
                self.assertFalse(self.target().exists())
                self.assertIn('move unavailable', state['message'])
                self.assertNotIn('已导出', state['message'])
                replace.assert_not_called()
                self.assert_no_temporary()

    def test_raced_same_bytes_target_is_never_replaced(self):
        move = files._move_owned
        foreign = []
        def collide(source, target, *args, **kwargs):
            target.write_bytes(source.read_bytes())
            foreign.append(_export_stamp(target))
            self.assertNotEqual(_export_stamp(source)[0], foreign[0][0])
            return move(source, target, *args, **kwargs)
        with patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()), \
                patch.object(files, '_move_owned', side_effect=collide):
            state = self.call('exportGallery', 'route.json')
        self.assertEqual(_export_stamp(self.target()), foreign[0])
        self.assertNotIn('已导出', state['message'])
        self.assert_no_temporary()

    def test_consumed_temporary_name_reuse_is_preserved_on_success_and_interrupt(self):
        move = files._move_owned
        for failure in (None, OSError(errno.EIO, 'after move'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                target = self.target(f'reuse-{type(failure).__name__}.json')
                foreign = []
                published = []
                def reuse(source, destination, *args, **kwargs):
                    result = move(source, destination, *args, **kwargs)
                    published.append(_export_stamp(destination))
                    # Same bytes, different object: hashes alone must not own it.
                    source.write_bytes(destination.read_bytes())
                    foreign.append((source, _export_stamp(source)))
                    if failure is not None:
                        raise failure
                    return result
                with patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()), \
                        patch.object(files, '_move_owned', side_effect=reuse):
                    if failure is None:
                        self.service._write_export(target, 'default')
                    else:
                        with self.assertRaises(type(failure)):
                            self.service._write_export(target, 'default')
                self.assertEqual(_export_stamp(target), published[0])
                self.assert_export(target)
                self.assertEqual(_export_stamp(foreign[0][0]), foreign[0][1])
                self.assertNotEqual(foreign[0][1][0], published[0][0])

    def test_same_bytes_source_replacement_is_not_recaptured_or_cleaned(self):
        move = files._move_owned
        original = []
        foreign = []
        def substitute(source, target, *args, **kwargs):
            saved = source.with_suffix('.held')
            original.append((saved, _export_stamp(source)))
            source.rename(saved)
            source.write_bytes(saved.read_bytes())
            foreign.append((source, _export_stamp(source)))
            return move(source, target, *args, **kwargs)
        with patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()), \
                patch.object(files, '_move_owned', side_effect=substitute):
            state = self.call('exportGallery', 'route.json')
        self.assertFalse(self.target().exists())
        self.assertEqual(_export_stamp(original[0][0]), original[0][1])
        self.assertEqual(_export_stamp(foreign[0][0]), foreign[0][1])
        self.assertNotEqual(original[0][1][0], foreign[0][1][0])
        self.assertNotIn('已导出', state['message'])

    def test_same_bytes_target_replacement_after_move_does_not_report_success(self):
        move = files._move_owned
        original = []
        foreign = []
        def substitute(source, target, *args, **kwargs):
            result = move(source, target, *args, **kwargs)
            saved = target.with_suffix('.held')
            original.append((saved, _export_stamp(target)))
            target.rename(saved)
            target.write_bytes(saved.read_bytes())
            foreign.append(_export_stamp(target))
            return result
        with patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()), \
                patch.object(files, '_move_owned', side_effect=substitute):
            state = self.call('exportGallery', 'route.json')
        self.assertEqual(_export_stamp(original[0][0]), original[0][1])
        self.assertEqual(_export_stamp(self.target()), foreign[0])
        self.assertNotIn('已导出', state['message'])
        self.assert_no_temporary()

    def test_hardlink_source_substitution_is_detected_and_never_cleaned(self):
        link = os.link
        foreign = []
        original = []
        def substitute(source, target):
            saved = source.with_suffix('.held')
            original.append((saved, _export_stamp(source)))
            source.rename(saved)
            source.write_bytes(saved.read_bytes())
            foreign.append((source, _export_stamp(source)))
            return link(source, target)
        with patch('client_custom_gallery.os.link', side_effect=substitute):
            state = self.call('exportGallery', 'route.json')
        self.assertEqual(_export_stamp(original[0][0]), original[0][1])
        self.assertEqual(_export_stamp(foreign[0][0]), foreign[0][1])
        self.assertEqual(_export_stamp(self.target()), foreign[0][1])
        self.assertNotIn('已导出', state['message'])

    def test_hardlink_target_substitution_does_not_report_success(self):
        link = os.link
        original = []
        foreign = []
        def substitute(source, target):
            link(source, target)
            original.append(_export_stamp(source))
            target.unlink()
            target.write_bytes(source.read_bytes())
            foreign.append(_export_stamp(target))
        with patch('client_custom_gallery.os.link', side_effect=substitute):
            state = self.call('exportGallery', 'route.json')
        self.assertEqual(_export_stamp(self.target()), foreign[0])
        self.assertNotEqual(original[0][0], foreign[0][0])
        self.assertNotIn('已导出', state['message'])
        self.assert_no_temporary()

    def test_unknown_file_identity_fails_closed_and_retains_unowned_temp(self):
        from types import SimpleNamespace
        with patch('client_custom_gallery.os.fstat', return_value=SimpleNamespace(st_dev=1, st_ino=0)), \
                patch('client_custom_gallery.os.link') as link, \
                self.assertRaisesRegex(ValueError, '无法验证导出文件身份'):
            self.service._write_export(self.target(), 'default')
        link.assert_not_called()
        self.assertFalse(self.target().exists())
        self.assertEqual(len(list(self.target().parent.glob('.gallery-export-*.tmp'))), 1)

    def test_confirmation_cancel_change_and_overwrite_keep_existing_protocol(self):
        self.target().parent.mkdir()
        self.target().write_bytes(b'original external file')
        before = _export_stamp(self.target())
        with patch('client_custom_gallery.os.link', side_effect=_unsupported_export_link()) as link, \
                patch.object(files, '_move_owned', wraps=files._move_owned) as move:
            self.call('exportGallery', 'route.json')
            self.assertEqual(self.service.snapshot['pendingKind'], 'export')
            self.call('confirmOperation', False)
            self.assertEqual(_export_stamp(self.target()), before)
            self.call('exportGallery', 'route.json')
            self.target().write_bytes(b'changed after confirmation request')
            state = self.call('confirmOperation', True)
            self.assertIn('已变化', state['message'])
            self.assertEqual(self.target().read_bytes(), b'changed after confirmation request')
            self.call('exportGallery', 'route.json')
            self.call('confirmOperation', True)
            self.assert_export(self.target())
            link.assert_not_called()
            move.assert_not_called()
        self.assert_no_temporary()

    def test_fsync_failure_never_publishes_or_claims_success(self):
        with patch('client_custom_gallery.os.fsync', side_effect=OSError(errno.EIO, 'flush failed')), \
                patch('client_custom_gallery.os.link') as link:
            state = self.call('exportGallery', 'route.json')
        self.assertIn('flush failed', state['message'])
        self.assertNotIn('已导出', state['message'])
        self.assertFalse(self.target().exists())
        link.assert_not_called()
        self.assert_no_temporary()

    def test_real_process_loss_before_and_after_move_leaves_only_complete_objects(self):
        # The subprocess uses the real service method and existing publisher,
        # while only its in-memory store payload and unsupported-link result are
        # synthetic. os._exit really prevents Python finally / Qt shutdown.
        script = textwrap.dedent('''
            import errno, json, os, sys
            from pathlib import Path
            from types import SimpleNamespace
            from unittest.mock import patch
            from client_custom_gallery import CustomGalleryService
            from client_paths import runtime_paths
            import map_package_upgrade as files
            root, phase = Path(sys.argv[1]), sys.argv[2]
            target = root / 'exports' / 'route.json'
            owner = SimpleNamespace(paths=runtime_paths(root), sandbox=root,
                _store=SimpleNamespace(export=lambda gid: {'schema':'xingtu-custom-gallery-v1', 'id':gid, 'maps':{}}))
            owner._validate_export_path = lambda path: CustomGalleryService._validate_export_path(owner, path)
            move = files._move_owned
            def stop(source, target, *args, **kwargs):
                if phase == 'after':
                    move(source, target, *args, **kwargs)
                os._exit(73)
            with patch('client_custom_gallery.os.link', side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')), patch.object(files, '_move_owned', side_effect=stop):
                CustomGalleryService._write_export(owner, target, 'default')
        ''')
        for phase in ('before', 'after'):
            with self.subTest(phase=phase):
                root = self.sandbox / f'process-{phase}'
                root.mkdir()
                (root / '.qt-lab-owner').write_text('qt-quick-isolated-v1')
                result = subprocess.run([sys.executable, '-c', script, str(root), phase],
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 73, result.stdout + result.stderr)
                target = root / 'exports' / 'route.json'
                temps = list(target.parent.glob('.gallery-export-*.tmp'))
                self.assertEqual(target.exists(), phase == 'after')
                self.assertEqual(len(temps), 1 if phase == 'before' else 0)
                complete = target if phase == 'after' else temps[0]
                self.assertEqual(json.loads(complete.read_text()),
                    dict(schema='xingtu-custom-gallery-v1', id='default', maps={}))



if __name__ == "__main__":
    unittest.main()
