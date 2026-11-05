"""Real offline loader/Qt worker with synthetic source-height images; no game IO."""
from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from map_resource_context import load_browse_context
from season_map_resources import EXPECTED_LAYOUTS, GROUPS, INDEX, SCHEMA, SOURCE_VERSION


def fixture(root):
    out = io.BytesIO()
    Image.new("RGBA", (6, 8), (80, 160, 210, 255)).save(out, "PNG")
    data = out.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    rows = []
    for layout in sorted(EXPECTED_LAYOUTS):
        group = int(layout[:3])
        difficulty, mode = GROUPS[group]
        row = dict(mapId=layout, sourceLayout=layout, sourceGroup=group,
                   sourceDifficulty=difficulty, mode=mode, floors=[])
        for height in (1, 2, 3, 4):
            floor = dict(sourceHeight=height, gameFloor=height - 5, label=f"{height - 5}层")
            for kind in ("rendered", "structure"):
                relative = f"assets/seasons/s1/{mode}/{layout}/{height}-{kind}.png"
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                floor[kind] = dict(path=relative, sha256=digest)
            row["floors"].append(floor)
        rows.append(row)
    raw = dict(schema=SCHEMA, season="s1", sourceVersion=SOURCE_VERSION, maps=rows)
    write_index(root, raw)
    return raw


def write_index(root, raw):
    path = root / INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw), encoding="utf-8")


class InventoryFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = fixture(self.root)


class OfflineInventoryTests(InventoryFixture, unittest.TestCase):
    def test_all_44_layouts_176_heights_and_both_image_types_are_consumed(self):
        layouts, images = set(), set()
        for mode, count in (("difficulty2", 15), ("difficult", 14), ("nightmare", 15)):
            context = load_browse_context(self.root, mode, season="s1")
            self.assertFalse(context.error, context.error)
            self.assertEqual(context.season, "s1")
            self.assertIsNone(context.pack)  # Browsing cannot become a runtime pack.
            self.assertEqual(len(context.profiles), 2)
            for profile in context.profiles:
                self.assertEqual((profile.map_count, profile.available_layers), (count, count * 4))
                self.assertIsNone(profile.package_id)
                for item in profile.maps:
                    layouts.add(item.id)
                    self.assertEqual(set(item.variants[0].layers),
                        {f"source-height-{height}" for height in (1, 2, 3, 4)})
                    for path in item.variants[0].layers.values():
                        images.add(path)
                        with Image.open(path) as image:
                            image.load()
                            self.assertEqual(image.size, (6, 8))
        self.assertEqual(layouts, EXPECTED_LAYOUTS)
        self.assertNotIn("106006", layouts)
        self.assertEqual(len(images), 352)

    def test_wrong_season_version_mode_group_and_floor_are_rejected(self):
        changes = [lambda d: d.update(season="s0"), lambda d: d.update(sourceVersion="old"),
            lambda d: d["maps"][0].update(mode="nightmare"),
            lambda d: d["maps"][0].update(sourceDifficulty=True),
            lambda d: d["maps"][0].update(sourceGroup=104),
            lambda d: d["maps"][0]["floors"][0].update(sourceHeight="B1"),
            lambda d: d["maps"][0]["floors"][0].update(sourceHeight=2),
            lambda d: d["maps"][0]["floors"][0].update(gameFloor=1),
            lambda d: d["maps"][0]["floors"][0].update(gameFloor=True),
            lambda d: d["maps"][0]["floors"][0].update(label="1楼"),
            lambda d: d["maps"].pop(),
            lambda d: d["maps"].__setitem__(1, deepcopy(d["maps"][0]))]
        for mutate in changes:
            with self.subTest(mutate=mutate):
                raw = deepcopy(self.raw)
                mutate(raw)
                write_index(self.root, raw)
                context = load_browse_context(self.root, "difficult", season="s1")
                self.assertTrue(context.error)
                self.assertEqual(context.profiles, ())
                self.assertEqual(dict(context.path_stamps), {})

    def test_missing_or_changed_file_is_not_preview_availability(self):
        path = self.root / self.raw["maps"][0]["floors"][0]["rendered"]["path"]
        original = path.read_bytes()
        for data in (b"broken", None):
            if data is None:
                path.unlink()
            else:
                path.write_bytes(data)
            context = load_browse_context(self.root, "nightmare", season="s1")
            self.assertTrue(context.error)
            self.assertFalse(context.profiles)
            path.write_bytes(original)

    def test_s0_paths_parent_traversal_and_absolute_paths_cannot_be_borrowed(self):
        for value in ("assets/suju/s0.png", "assets/seasons/s1/../../../../old.png", "C:/old.png"):
            raw = deepcopy(self.raw)
            raw["maps"][0]["floors"][0]["rendered"]["path"] = value
            write_index(self.root, raw)
            context = load_browse_context(self.root, "difficulty2", season="s1")
            self.assertTrue(context.error)
            self.assertFalse(context.profiles)

    def test_s1_does_not_read_s0_pack_and_s0_does_not_read_s1_index(self):
        actual = Path.read_text
        reads = []
        def record(path, *args, **kwargs):
            reads.append(path.relative_to(self.root).as_posix())
            return actual(path, *args, **kwargs)
        with patch.object(Path, "read_text", record):
            self.assertFalse(load_browse_context(self.root, "difficult", season="s1").error)
        self.assertEqual(reads, [INDEX])
        reads.clear()
        with patch.object(Path, "read_text", record):
            self.assertTrue(load_browse_context(self.root, "difficult", season="s0").error)
        self.assertNotIn(INDEX, reads)

    def test_cancel_during_hashing_abandons_complete_context(self):
        calls = 0
        def cancelled():
            nonlocal calls
            calls += 1
            return calls > 12
        with self.assertRaises(CancelledError):
            load_browse_context(self.root, "nightmare", season="s1", cancelled=cancelled)

    def test_offline_images_do_not_grant_native_resource_readiness_or_live_controls(self):
        from season_context import SeasonContext, MODES
        from floor_contract import floor_contract
        from client_providers import EngineFactories, ProviderUnavailable
        self.assertNotIn("difficulty2", MODES)
        factory = EngineFactories(self.root)
        for mode in ("nightmare", "difficult"):
            self.assertTrue(SeasonContext("s1", mode).registered)  # Contract declaration is not availability.
            self.assertEqual(floor_contract(mode, season="s1").control_axis, "vertical")
            with self.assertRaisesRegex(ProviderUnavailable, "season-resources-unavailable"):
                factory.get_engine(mode, "s1")

    def test_existing_client_self_check_decodes_the_explicit_inventory_without_gui(self):
        result = subprocess.run([sys.executable, "-X", "utf8", "-B",
            str(ROOT / "experiments/qt_quick/client.py"), "--self-check", "--offline-resource-root", str(self.root)],
            capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ready"])
        self.assertFalse(report["nativeInputStarted"])
        self.assertFalse(report["tkLoaded"])
        self.assertEqual(report["offlineGallery"]["decodedImages"], 352)
        self.assertFalse(report["offlineGallery"]["liveAvailable"])
        self.assertEqual(report["offlineGallery"]["sourceHeightToGameFloor"],
            {"1": -4, "2": -3, "3": -2, "4": -1})


class OfflineGalleryTests(InventoryFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.002)
        self.fail("gallery worker timeout")

    def backend(self):
        from backend import Backend
        paths = SimpleNamespace(installation_root=self.root, resource_root=self.root)
        with patch("backend.runtime_paths", return_value=paths), patch.object(Backend, "refresh"):
            backend = Backend(self.root)
        self.addCleanup(backend.shutdown)
        return backend

    def test_real_backend_worker_selects_all_source_heights_and_never_routes_them(self):
        backend = self.backend()
        for mode, count in (("difficulty2", 15), ("difficult", 14), ("nightmare", 15)):
            backend.setContext("s1", mode)
            self.wait(lambda: not backend.busy)
            self.assertFalse(backend.hasError, backend.message)
            self.assertEqual(backend.mapCount, count)
            self.assertEqual(len(backend.model.rows), count * 4)
            self.assertTrue(all(not p["runtimeSelectable"] for p in backend.profiles))
            backend.selectFirstLayer()
            self.assertEqual(backend.selectedFloor, "source-height-4")
            self.assertEqual(backend.model.rows[0]["gameFloor"], -1)
            for floor in backend.floorOptions[1:]:
                backend.setFloor(floor["value"])
                self.assertEqual(len(backend.model.rows), count)
                backend.selectFirstLayer()
                self.assertTrue(backend.selectedImage.startswith("file:"))
                self.assertIn("season=s1", backend.selectedImage)
            backend.setFloor("B1")
            self.assertNotEqual(backend.floor, "B1")
        backend.setContext("s0", "difficulty2")
        self.assertEqual((backend.season, backend.mode), ("s1", "nightmare"))

    def test_loaded_s1_result_cannot_publish_after_switch_to_s0(self):
        backend = self.backend()
        jobs = []
        backend._submit = lambda fn, callback: jobs.append((fn, callback))
        backend.setContext("s1", "difficulty2")
        context = load_browse_context(self.root, "difficulty2", season="s1")
        backend.setContext("s0", "difficult")
        jobs[0][1](context, None)
        self.assertEqual(backend.stale_results, 1)
        self.assertEqual(backend.profiles, [])
        self.assertEqual(backend.model.rows, [])
        self.assertEqual(backend.selectedImage, "")

    def test_content_hash_changes_preview_url_even_if_file_time_and_size_are_preserved(self):
        from backend import Backend
        descriptor = self.raw["maps"][0]["floors"][0]["rendered"]
        path = self.root / descriptor["path"]
        before = load_browse_context(self.root, "difficulty2", season="s1")
        original = path.stat()
        image = io.BytesIO()
        Image.new("RGBA", (6, 8), (80, 160, 211, 255)).save(image, "PNG")
        data = image.getvalue()
        self.assertEqual(len(data), original.st_size)
        path.write_bytes(data)
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        descriptor["sha256"] = hashlib.sha256(data).hexdigest()
        write_index(self.root, self.raw)
        after = load_browse_context(self.root, "difficulty2", season="s1")
        self.assertFalse(after.error, after.error)
        self.assertEqual(before.path_stamps[path][:2], after.path_stamps[path][:2])
        self.assertNotEqual(Backend._preview_url(path, before.path_stamps, "s1"),
                            Backend._preview_url(path, after.path_stamps, "s1"))

    def test_gallery_qml_displays_source_height_and_keeps_live_action_disabled(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, QPointF, Qt
        from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
        from PySide6.QtQuick import QQuickWindow
        from PySide6.QtTest import QTest
        from test_client_gallery_layout import FakeState, FakeCoordinator
        from client_preview import PreviewService
        from client_windows import WindowController
        backend = self.backend()
        backend.setContext("s1", "difficulty2")
        self.wait(lambda: not backend.busy)
        backend.setFloor("source-height-4")
        backend.selectFirstLayer()
        state, coordinator = FakeState(), FakeCoordinator()
        windows = WindowController()
        service = PreviewService(backend, windows)
        backend.overlayRequested.connect(service.show)
        engine = QQmlEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(map(str, rows)))
        for name, value in dict(backend=backend, resourceModel=backend.model, session=state,
                settingsService=state, resourceService=state, clientCoordinator=coordinator).items():
            engine.rootContext().setContextProperty(name, value)
        engine.rootContext().setContextProperty("previewService", service)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT / "experiments/qt_quick/ClientGallery.qml")))
        page = component.create()
        self.assertIsNotNone(page, [str(row) for row in component.errors()])
        window = QQuickWindow()
        page.setParentItem(window.contentItem())
        preview_component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT / "experiments/qt_quick/ClientPreview.qml")))
        preview_window = preview_component.create()
        self.assertIsNotNone(preview_window, [str(row) for row in preview_component.errors()])
        windows.bindWindow("preview", preview_window)
        reference = QQuickWindow()
        windows.bindWindow("reference", reference)
        windows.show("reference")
        reference_state = windows.snapshot["reference"]
        try:
            for width in (514, 1000):
                page.setWidth(width)
                page.setHeight(650)
                window.resize(width, 650)
                window.show()
                self.app.processEvents()
                expression = QQmlExpression(engine.rootContext(),
                    page.findChild(QObject, "galleryLargePreview"), "status === 1")
                def image_ready():
                    result = expression.evaluate()
                    return result[0] if isinstance(result, tuple) else result
                self.wait(image_ready)
                button = page.findChild(QObject, "galleryContextDifficulty2")
                self.assertTrue(button.isVisible())
                self.assertTrue(button.property("selected"))
                self.assertFalse(page.findChild(QObject, "galleryUseAuthor").isEnabled())
                picker = page.findChild(QObject, "galleryLayerPicker")
                self.assertIn("-1层", picker.property("displayText"))
                self.assertEqual(page.findChild(QObject, "galleryFloorPicker").property("count"), 5)
            self.assertEqual(coordinator.calls, [])
            button = page.findChild(QObject, "galleryShowReference")
            point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2))
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point.toPoint())
            self.wait(lambda: service.visible)
            preview_image = preview_window.findChild(QObject, "previewMapImage")
            self.wait(lambda: preview_image.property("progress") == 1 and preview_image.property("paintedWidth") > 0)
            self.assertEqual(service.previewData["imageUrl"], backend.selectedImage)
            self.assertEqual(service.previewData["floor"], "source-height-4")
            self.assertIn("-1层", preview_window.title())
            self.assertEqual(windows.snapshot["reference"], reference_state)
            self.assertEqual(coordinator.calls, [])
            self.assertFalse(warnings, "\n".join(warnings))
        finally:
            service.shutdown()
            windows.shutdown()
            window.hide()
            page.setParentItem(None)
            page.deleteLater()
            window.deleteLater()
            reference.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
