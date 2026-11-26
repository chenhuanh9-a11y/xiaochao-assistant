"""S1 important room names and minecart exits in the Qt client.

Synthetic landmark table and art only; no account, game or network IO.
Covers the saved toggles, the editor snapshot and canvas, the in-game display
layer and the display refresh after a toggle (S1 only; S0 is unchanged).
"""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

import test_client_custom_gallery as gallery_fixture  # sets up the app import path
import map_landmarks as ml
from client_route_preferences import _with_landmarks
from client_settings import SettingsService
import test_client_coordinator as coordinator_fixture
import test_client_live_runtime as runtime_fixture
from test_client_route_plaza import item

APP = gallery_fixture.APP
until = gallery_fixture.until
HERE = Path(__file__).parent
MASK = np.ones((200, 200), np.uint8)


DEFAULT_ITEMS = [
    dict(id="r1", kind="room", label="水晶房", x=60.0, y=60.0),
    dict(id="r2", kind="room", label="普通房间", x=100.0, y=40.0),
    dict(id="c1", kind="minecart", label="矿车出口", x=140.0, y=140.0)]
# B2/B3 minecart spots are rooms named 矿车 / 矿车中转站 (minecart switch only).
CART_ROOM_ITEMS = [
    dict(id="r1", kind="room", label="水晶房", x=60.0, y=40.0),
    dict(id="e1", kind="room", label="矿车", x=140.0, y=140.0),
    dict(id="t1", kind="room", label="矿车中转站", x=60.0, y=140.0)]


def landmark_table(folder, map_id="synthetic-map", floor="1", items=None):
    doc = dict(schema=ml.SCHEMA, sourceVersion="synthetic", importantRooms={floor: ["水晶房"]},
               modes=dict(difficult={map_id: {floor: dict(
                   width=200, height=200, maskSha256=ml.mask_sha256(MASK), androidLayerMaskSha256="a" * 64,
                   structureVersionKey="", items=items or DEFAULT_ITEMS)}}))
    path = Path(folder) / "landmarks.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


class LandmarkDataCase(unittest.TestCase):
    def use_table(self, **kwargs):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(ml, "DATA_FILE", landmark_table(folder.name, **kwargs))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, ml, "_DATA_PATH", None)
        ml._DATA_PATH = None
        self.addCleanup(ml.set_preferences, ml.preferences())
        ml.set_preferences(ml.LandmarkPreferences())


class DisplayLayerTests(LandmarkDataCase):
    def layer(self, kind="shared-structure"):
        pixels = np.zeros((200, 200, 4), np.uint8)
        pixels[..., 3] = 255
        return dict(pixels=pixels, matrixDisplayToReference=np.eye(3), displayKind=kind,
                    resourceIdentity=dict(id="kept"), routeProfileId="shared-structure")

    def test_s1_layer_gets_labels_and_keeps_identity(self):
        self.use_table(map_id="106001", floor="-2")
        reference = SimpleNamespace(clean_mask=MASK)
        layer = self.layer()
        out = _with_landmarks(SimpleNamespace(qt_season="s1"), "106001", "-2", layer, reference)
        self.assertIsNot(out, layer)
        self.assertEqual(out["resourceIdentity"], dict(id="kept"))
        self.assertTrue(np.any(out["pixels"] != layer["pixels"]))
        self.assertFalse(np.any(layer["pixels"][..., :3]))  # source pixels untouched

    def test_s0_toggle_off_and_mismatch_keep_the_same_layer(self):
        self.use_table(map_id="106001", floor="-2")
        reference = SimpleNamespace(clean_mask=MASK)
        layer = self.layer()
        self.assertIs(_with_landmarks(SimpleNamespace(qt_season="s0"), "106001", "-2", layer, reference), layer)
        self.assertIs(_with_landmarks(SimpleNamespace(), "106001", "-2", layer, reference), layer)
        other = MASK.copy()
        other[0, 0] = 0
        self.assertIs(_with_landmarks(SimpleNamespace(qt_season="s1"), "106001", "-2", layer,
                                      SimpleNamespace(clean_mask=other)), layer)
        ml.set_preferences(ml.LandmarkPreferences(False, False))
        self.assertIs(_with_landmarks(SimpleNamespace(qt_season="s1"), "106001", "-2", layer, reference), layer)

    def test_route_layers_shift_labels_above_the_route(self):
        self.use_table(map_id="106001", floor="-2")
        reference = SimpleNamespace(clean_mask=MASK)
        engine = SimpleNamespace(qt_season="s1")
        def top(kind):
            layer = self.layer(kind)
            out = _with_landmarks(engine, "106001", "-2", layer, reference)
            return np.nonzero(np.any(out["pixels"] != layer["pixels"], axis=2))[0].min()
        self.assertLess(top("custom-gallery"), top("shared-structure"))


class MinecartRoomDisplayTests(LandmarkDataCase):
    layer = DisplayLayerTests.layer

    def test_game_map_b2_b3_minecart_spots_follow_the_minecart_switch_only(self):
        engine, reference = SimpleNamespace(qt_season="s1"), SimpleNamespace(clean_mask=MASK)
        for floor in ("-2", "-3"):
            self.use_table(map_id="106001", floor=floor, items=CART_ROOM_ITEMS)
            layer = self.layer()
            def drawn(rooms, carts):
                ml.set_preferences(ml.LandmarkPreferences(rooms, carts))
                out = _with_landmarks(engine, "106001", floor, layer, reference)
                return np.any(out["pixels"] != layer["pixels"], axis=2)
            carts_only = drawn(False, True)
            self.assertTrue(carts_only[120:175, 40:160].any(), floor)
            self.assertFalse(carts_only[:80].any(), floor)
            np.testing.assert_array_equal(drawn(True, True)[100:], carts_only[100:])
            self.assertFalse(drawn(True, False)[100:].any(), floor)
            self.assertFalse(drawn(False, False).any(), floor)


class GalleryCase(LandmarkDataCase):
    setUp = gallery_fixture.CustomGalleryTests.setUp
    cleanup = gallery_fixture.CustomGalleryTests.cleanup
    call = gallery_fixture.CustomGalleryTests.call


class SettingsTests(GalleryCase):
    def test_defaults_on_saved_per_field_and_reloaded(self):
        for key in ml.SETTING_KEYS:
            self.assertTrue(self.settings.persistedValues[key])
        self.assertEqual(ml.LandmarkPreferences.from_settings(self.settings.persistedValues),
                         ml.LandmarkPreferences(True, True))
        self.settings.setValue("sheetOpacity", .7)
        self.settings.setValue("mapLandmarkMinecarts", False)
        self.assertTrue(self.settings.saveFields(["mapLandmarkMinecarts"]))
        until(lambda: not self.settings.busy)
        raw = json.loads(self.settings.path.read_text())
        self.assertFalse(raw["mapLandmarkMinecarts"])
        self.assertNotIn("sheetOpacity", raw)
        restored = SettingsService(self.sandbox)
        self.addCleanup(restored.shutdown)
        self.assertEqual(ml.LandmarkPreferences.from_settings(restored.persistedValues),
                         ml.LandmarkPreferences(True, False))
        self.assertFalse(restored.setValue("mapLandmarkRooms", "false"))

    def test_settings_page_row_is_s1_only_and_edits_the_draft(self):
        from PySide6.QtQuick import QQuickView
        from test_client_settings import visual_item
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty("settingsService", self.settings)
        warnings = []
        view.engine().warnings.connect(lambda rows: warnings.extend(str(r) for r in rows))
        view.setSource(QUrl.fromLocalFile(str(HERE / "ClientSettings.qml")))
        self.assertFalse(view.errors(), "\n".join(str(e) for e in view.errors()))
        view.resize(720, 740)
        view.show()
        APP.processEvents()
        panel = view.rootObject()
        panel.setProperty("modeExpanded", True)  # 设置 → 模式与路线
        try:
            row = visual_item(panel, "settingsMapLandmarks")
            self.assertTrue(self.settings.setValue("gameSeason", "s0"))
            APP.processEvents()
            self.assertFalse(row.property("visible"))
            self.assertTrue(self.settings.setValue("gameSeason", "s1"))
            APP.processEvents()
            self.assertTrue(row.property("visible"))
            box = visual_item(panel, "setting_mapLandmarkRooms")
            self.assertTrue(box.property("checked"))
            box.toggle()
            box.toggled.emit()
            APP.processEvents()
            self.assertFalse(self.settings.values["mapLandmarkRooms"])
            self.assertTrue(self.settings.persistedValues["mapLandmarkRooms"])  # saved only by 保存更改
            self.assertEqual(warnings, [])
        finally:
            view.close()
            view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


class EditorTests(GalleryCase):
    def bind(self):
        self.use_table()
        self.service.library.landmark_binding = lambda map_id, floor: (
            dict(width=200, height=200, maskSha256=ml.mask_sha256(MASK), toImage=np.diag([.5, .5, 1.]))
            if (map_id, floor) == ("synthetic-map", "1") else None)

    def test_snapshot_lists_important_items_in_image_pixels_only(self):
        self.call("createGallery", "离线标注示例")
        self.call("selectMap", "synthetic-map")
        self.assertEqual(self.service.snapshot["landmarks"], [])  # library without bindings (S0)
        self.bind()
        self.call("selectFloor", "1")
        self.assertEqual(self.service.snapshot["landmarks"], [
            dict(kind="room", label="水晶房", x=30.0, y=30.0),
            dict(kind="minecart", label="矿车出口", x=70.0, y=70.0)])
        self.call("selectFloor", "2")
        self.assertEqual(self.service.snapshot["landmarks"], [])
        self.call("addPoint", 20., 20.)
        self.call("selectFloor", "1")
        # Labels are never saved with the route.
        self.assertNotIn("水晶房", json.dumps(self.service.snapshot["points"], ensure_ascii=False))

    def test_snapshot_lists_minecart_rooms_with_captions(self):
        self.call("createGallery", "离线标注示例")
        self.call("selectMap", "synthetic-map")
        self.use_table(items=CART_ROOM_ITEMS)
        self.service.library.landmark_binding = lambda map_id, floor: (
            dict(width=200, height=200, maskSha256=ml.mask_sha256(MASK)) if floor == "1" else None)
        self.call("selectFloor", "1")
        self.assertEqual(self.service.snapshot["landmarks"], [
            dict(kind="room", label="水晶房", x=60.0, y=40.0),
            dict(kind="minecart", label="矿车", caption="矿车", x=140.0, y=140.0),
            dict(kind="minecart", label="矿车中转站", caption="中转站", x=60.0, y=140.0)])

    def s1_view(self):
        import dataclasses
        from route_scopes import SCOPES
        view = patch.dict(SCOPES, {"legacy-nightmare": dataclasses.replace(SCOPES["legacy-nightmare"], season_id="s1")})
        view.start()
        self.addCleanup(view.stop)
        return view

    def open_editor(self):
        """Real ClientCustomGallery.qml; returns window, engine, warnings, switches, grab, click."""
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("customGalleryService", self.service)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(r.toString() for r in rows))
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 1000; height: 720
ClientCustomGallery { anchors.fill: parent; anchors.margins: 24 }
}''', QUrl.fromLocalFile(str(HERE / "map-landmarks-harness.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        art = item(window, "customMapImage")
        until(lambda: art.property("paintedWidth") > 0 and art.width() > 200)
        QTest.qWait(150)
        rooms = item(window, "mapLandmarkSwitch_mapLandmarkRooms")
        carts = item(window, "mapLandmarkSwitch_mapLandmarkMinecarts")
        rect = art.mapToScene(QPointF(0, 0))
        crop = (int(rect.x()), int(rect.y()), int(art.width()), int(art.height()))
        def grab():
            QTest.qWait(120)
            return window.grabWindow().copy(*crop)
        def click(switch, key, value):
            position = switch.mapToScene(QPointF(switch.width() / 2, switch.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            until(lambda: self.settings.persistedValues[key] is value and not self.settings.busy)
        return window, engine, warnings, rooms, carts, grab, click

    def close_editor(self, window, engine):
        window.close()
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_real_qml_minecart_rooms_follow_the_minecart_switch_only(self):
        self.s1_view()
        self.call("createGallery", "离线标注示例")
        self.call("selectMap", "synthetic-map")
        # Only minecart rooms: the 重点房间 switch must not change the canvas.
        self.use_table(items=CART_ROOM_ITEMS[1:])
        self.service.library.landmark_binding = lambda map_id, floor: (
            dict(width=200, height=200, maskSha256=ml.mask_sha256(MASK)) if floor == "1" else None)
        self.call("selectFloor", "1")
        self.assertEqual([i["caption"] for i in self.service.snapshot["landmarks"]], ["矿车", "中转站"])
        window, engine, warnings, rooms, carts, grab, click = self.open_editor()
        try:
            shown = grab()
            click(rooms, "mapLandmarkRooms", False)
            self.assertEqual(shown, grab())
            click(carts, "mapLandmarkMinecarts", False)
            hidden = grab()
            self.assertNotEqual(shown, hidden)
            click(rooms, "mapLandmarkRooms", True)
            self.assertEqual(hidden, grab())
            click(carts, "mapLandmarkMinecarts", True)
            self.assertEqual(shown, grab())
            self.assertEqual(warnings, [])
        finally:
            self.close_editor(window, engine)

    def test_real_qml_switches_hide_and_show_canvas_labels(self):
        self.bind()
        s1_view = self.s1_view()
        self.call("createGallery", "离线标注示例")
        self.call("selectMap", "synthetic-map")
        self.call("selectFloor", "1")
        self.assertEqual(self.service.snapshot["season"], "s1")
        self.assertEqual(len(self.service.snapshot["landmarks"]), 2)
        window, engine, warnings, rooms, carts, grab, click = self.open_editor()
        try:
            self.assertTrue(rooms.property("visible") and carts.property("visible"))
            self.assertTrue(rooms.property("checked") and carts.property("checked"))
            both = grab()
            click(rooms, "mapLandmarkRooms", False)
            carts_only = grab()
            self.assertNotEqual(both, carts_only)
            click(carts, "mapLandmarkMinecarts", False)
            none = grab()
            self.assertNotEqual(carts_only, none)
            click(rooms, "mapLandmarkRooms", True)
            click(carts, "mapLandmarkMinecarts", True)
            self.assertEqual(both, grab())
            restored = SettingsService(self.sandbox)
            self.addCleanup(restored.shutdown)
            self.assertTrue(restored.persistedValues["mapLandmarkRooms"])
            # S0 scope hides the switches and draws nothing extra.
            s1_view.stop()
            self.service.library.landmark_binding = lambda *args: None
            self.call("selectFloor", "1")
            QTest.qWait(120)
            self.assertFalse(rooms.property("visible"))
            self.assertEqual(warnings, [])
        finally:
            self.close_editor(window, engine)


class RuntimeTests(unittest.TestCase):
    def test_toggle_retires_current_display_in_s1_only(self):
        self.addCleanup(ml.set_preferences, ml.preferences())
        case = runtime_fixture.LiveRuntimeContracts()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.begin_and_capture()
        before = case.runtime.snapshot()
        settings = dict(case.runtime._settings)
        case.runtime.configure_settings(dict(settings, mapLandmarkRooms=False))
        self.assertEqual(ml.preferences(), ml.LandmarkPreferences(False, True))
        self.assertIsNotNone(case.runtime.render_payload(before["render"]["token"]))  # S0 unchanged
        runtime = case.runtime
        runtime._season = "s1"  # white box: the display generation is the S1 refresh signal
        generation = runtime._display_generation
        runtime.configure_settings(dict(settings, mapLandmarkRooms=False))  # unchanged
        self.assertEqual(runtime._display_generation, generation)
        runtime.configure_settings(dict(settings, mapLandmarkRooms=False, mapLandmarkMinecarts=False))
        self.assertEqual(ml.preferences(), ml.LandmarkPreferences(False, False))
        self.assertEqual(runtime._display_generation, generation + 1)


class CoordinatorTests(unittest.TestCase):
    def fixture(self):
        case = coordinator_fixture.CoordinatorTests()
        case.app = APP
        case.setUp()
        self.addCleanup(case.tearDown)
        return case

    def test_saved_toggle_refreshes_s1_display_only(self):
        case = self.fixture()
        owner = case.coordinator
        case.settings.persistedValues.update(gameSeason="s0", mapLandmarkRooms=False)
        owner._settings_saved()
        self.assertFalse(owner._pending_display_refresh)
        case.session.dispatchWithPayload.assert_not_called()
        owner._applied_context = ("nightmare", "", "s1")
        case.settings.persistedValues.update(gameSeason="s1", mapLandmarkRooms=True)
        case.session.busy = True
        owner._settings_saved()
        self.assertTrue(owner._pending_display_refresh)
        case.session.dispatchWithPayload.assert_not_called()
        case.session.busy = False
        owner._try_apply_context()
        args = case.session.dispatchWithPayload.call_args.args
        self.assertEqual(args[0], "set_context")
        self.assertEqual((args[1]["season"], args[1]["refreshDisplay"]), ("s1", True))


if __name__ == "__main__":
    unittest.main()
