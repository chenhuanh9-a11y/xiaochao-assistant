"""框选 floor recognition: region reader, mode switch, settings, editor and runtime seam."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import cv2
import numpy as np
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from client_settings import ROOT, SettingsService
from client_floor_region import DEFAULT_RECT, FloorRegionImages, FloorRegionService, suggested_rect, VIEW
from client_live_runtime import LiveRuntime
from floor_control_region import (apply_floor_region, locate_vertical_floor_controls,
                                  read_floor_in_region, region_for_client)

SIZE = (1920, 1080)
ANCHOR, TILE, TOP = 1500, 64, 40     # the selected button stays at ANCHOR
LEGACY_REGION = [1300 / 1920, 25 / 1080, 400 / 1920, 75 / 1080]
REGION = [.79, .25, .11, .27]
ORDERS = (("nightmare", (2, 1, -1)), ("difficult", (2, 1)))


def column(order, selected, *, size=SIZE, center_x=1620, top=360, pitch=60):
    """Controlled signed S0 glyphs; synthetic pixels, not game screenshots."""
    frame = np.full((1080, 1920, 3), 33, np.uint8)
    for index, floor in enumerate(order):
        x, y = center_x, top + index * pitch
        if floor == selected:
            frame[y - 32:y + 32, x - 30:x + 30] = 110
        color = (15,) * 3 if floor == selected else (225,) * 3
        offset = 4 if floor < 0 else 0
        if abs(floor) == 1:
            cv2.rectangle(frame, (x - 2 + offset, y - 10), (x + 1 + offset, y + 9), color, -1)
        else:
            cv2.polylines(frame, [np.array([(x - 6, y - 10), (x + 6, y - 10),
                (x + 6, y - 2), (x - 6, y + 4), (x - 6, y + 9), (x + 6, y + 9)])],
                False, color, 2, cv2.LINE_8)
        if floor < 0:
            cv2.rectangle(frame, (x - 13, y - 1), (x - 6, y), color, -1)
    return cv2.resize(frame, size, interpolation=cv2.INTER_NEAREST)


def hud(floors, selected, *, seed=0, size=SIZE):
    """Map HUD: dark backdrop, a row of labelled buttons sliding so the
    selected one is always at ANCHOR, and a textured highlight on it."""
    rng = np.random.default_rng(seed)
    w, h = size
    frame = np.full((h, w, 3), 33, np.uint8)
    index = floors.index(selected)
    for i in range(len(floors)):
        x = ANCHOR + (i - index) * TILE - TILE // 2
        tile = frame[TOP:TOP + 44, x:x + TILE - 2]
        if i == index:
            tile[:] = np.clip(rng.normal(110, 22, tile.shape[:2]), 70, 200).astype(np.uint8)[..., None]
        else:
            tile[:] = 46
        tile[10:34, TILE // 2 - 4:TILE // 2 + 2] = 225    # label stroke
    return frame


class RegionReaderTests(unittest.TestCase):
    def test_signed_column_reads_every_floor_in_both_modes(self):
        for mode, floors in ORDERS:
            for selected in floors:
                with self.subTest(mode=mode, selected=selected):
                    read = read_floor_in_region(column(floors, selected), REGION, mode)
                    self.assertEqual(read["selectedFloor"], selected, read)
                    self.assertEqual(read["observedOrder"], list(floors))
                    self.assertEqual(len(read["centerPoints"]), len(floors))
                    self.assertEqual(read["controlAxis"], "vertical")
                    self.assertNotIn("centers", read)

    def test_glyphs_define_floor_order_instead_of_legacy_scalar_slots(self):
        one, two = column((2, 1, -1), 1), column((2, 1, -1), 2)
        self.assertEqual(read_floor_in_region(one, REGION, "nightmare")["selectedFloor"], 1)
        self.assertEqual(read_floor_in_region(two, REGION, "nightmare")["selectedFloor"], 2)
        for selected in (-1, 1, 2):
            old = hud((-1, 1, 2), selected)
            self.assertIsNone(read_floor_in_region(old, LEGACY_REGION, "nightmare")["selectedFloor"])

    def test_cut_or_wrong_count_reads_nothing_instead_of_guessing(self):
        frame = column((2, 1, -1), 2)
        cut = [REGION[0], REGION[1], REGION[2], .13]
        self.assertIsNone(read_floor_in_region(frame, cut, "nightmare")["selectedFloor"])
        # Three buttons where difficult mode has two.
        self.assertEqual(read_floor_in_region(column((2, 1, -1), 1), REGION, "difficult")["reason"], "too-many-buttons")
        for blank in (np.full((1080, 1920, 3), 33, np.uint8), np.full((1080, 1920, 3), 160, np.uint8)):
            self.assertIsNone(read_floor_in_region(blank, REGION, "nightmare")["selectedFloor"])
        self.assertEqual(read_floor_in_region(frame, [.9, 0, .2, .1], "nightmare")["reason"], "region-invalid")

    def test_region_applies_only_to_the_shape_it_was_framed_on(self):
        saved = dict(rect=REGION, clientSize=[1920, 1080])
        self.assertEqual(region_for_client(saved, (1280, 720)), tuple(REGION))
        self.assertIsNone(region_for_client(saved, (1920, 1200)))
        self.assertIsNone(region_for_client(None, (1920, 1080)))
        self.assertIsNone(region_for_client(dict(rect=REGION, clientSize="1920x1080"), (1920, 1080)))


class AutoLocateTests(unittest.TestCase):
    def test_column_is_found_across_the_right_side_search_area(self):
        for center_x, top in ((1500, 230), (1620, 360), (1730, 490)):
            for mode, floors in ORDERS:
                for selected in floors:
                    with self.subTest(center_x=center_x, top=top, mode=mode, selected=selected):
                        read = locate_vertical_floor_controls(column(floors, selected, center_x=center_x, top=top), mode)
                        self.assertEqual(read["selectedFloor"], selected, read)

    def test_other_bright_items_or_light_ui_are_not_buttons(self):
        frame = np.full((1080, 1920, 3), 33, np.uint8)
        frame[300:350, 1700:1760] = 150                       # a lone filled block
        frame[450:490, 1500:1560] = 120; frame[455:485, 1525:1531] = 230
        self.assertIsNone(locate_vertical_floor_controls(frame, "nightmare")["selectedFloor"])
        light = column((2, 1, -1), 1)
        light[:, 960:] = np.clip(light[:, 960:].astype(int) + 170, 0, 255).astype(np.uint8)
        self.assertIsNone(locate_vertical_floor_controls(light, "nightmare")["selectedFloor"])
        for center_x, top in ((1200, 360), (1620, 800)):
            outside = column((2, 1, -1), 1, center_x=center_x, top=top)
            self.assertIsNone(locate_vertical_floor_controls(outside, "nightmare")["selectedFloor"])


class ModeSwitchTests(unittest.TestCase):
    fixed = dict(selectedFloor=None, layout="unlocalized", centers=None)

    def test_auto_is_the_default_and_only_searches_when_fixed_reads_nothing(self):
        frame = column((2, 1, -1), 2)
        out = apply_floor_region({}, "nightmare", frame, self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (2, "auto-located"))
        self.assertEqual(out["controlAxis"], "vertical")
        self.assertFalse(out["controlGeometryVerified"])
        self.assertNotIn("centers", out)
        out = apply_floor_region(dict(floorControlMode="auto"), "nightmare", frame, dict(self.fixed, selectedFloor=1))
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (1, "fixed"))
        out = apply_floor_region({}, "nightmare", np.full((1080, 1920, 3), 33, np.uint8), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (None, "fixed-auto-unread"))

    def test_fixed_mode_leaves_the_reading_alone(self):
        settings = dict(floorControlMode="fixed", floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        out = apply_floor_region(settings, "nightmare", column((2, 1, -1), 2), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (None, "fixed"))

    def test_region_mode_reads_the_box_and_falls_back_to_auto(self):
        settings = dict(floorControlMode="region", floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        stale = dict(self.fixed, selectedFloor=1, centers=[.75, .78, .81],
            controlRects=[dict(floor=1, rect=[.7, .01, .8, .08])],
            controlVertical=dict(regionTop=.01, regionHeight=.08), difficultObservation={})
        out = apply_floor_region(settings, "nightmare", column((2, 1, -1), -1), stale)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (-1, "region"))
        self.assertFalse(out["controlGeometryVerified"])
        for key in ("centers", "controlRects", "controlVertical", "difficultObservation"):
            self.assertNotIn(key, out)
        out = apply_floor_region(settings, "nightmare", np.full((1080, 1920, 3), 33, np.uint8),
                                 dict(self.fixed, selectedFloor=2))
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (2, "fixed-region-unread"))
        out = apply_floor_region(dict(floorControlMode="region"), "nightmare", column((2, 1, -1), 1), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (1, "auto-located"))

    def test_region_reading_cannot_grant_a_floor_to_another_season(self):
        frame = column((2, 1, -1), 1)
        for season in ("s1", "s2"):
            for choice in ("fixed", "auto", "region"):
                with self.subTest(season=season, choice=choice):
                    settings = dict(gameSeason=season, floorControlMode=choice,
                        floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
                    reading = apply_floor_region(settings, "nightmare", frame,
                        dict(self.fixed, selectedFloor=1))
                    self.assertIsNone(reading["selectedFloor"])
                    self.assertEqual(reading["floorSource"], "unavailable")

    def test_runtime_reads_raw_region_but_authorizes_it_only_with_map_proof(self):
        from test_client_live_runtime import FixtureCapture, fixture_engine
        runtime = LiveRuntime(engine_factories={}, capture_provider=FixtureCapture(), evidence_kind="synthetic")
        runtime._season = "s0"
        runtime._settings = dict(floorControlMode="region",
            floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        engine = fixture_engine("difficult")
        frame = column((2, 1), 2)
        with patch.object(engine.qt_map_ui, "observe", side_effect=AssertionError("legacy map sensor called")), \
             patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)) as map_sensor:
            result, floor, floor_read = runtime._observe(engine, frame)
            self.assertEqual((floor, floor_read["selectedFloor"], floor_read["floorSource"]), ("2", 2, "region"))
            self.assertTrue(result["mapOpen"])
            self.assertTrue(floor_read["controlGeometryVerified"])
            self.assertIs(runtime.latest_map_frame()[0], frame)
            self.assertEqual(map_sensor.call_count, 1)
            runtime._settings = {}
            result, floor, floor_read = runtime._observe(engine, column((2, 1), 1))
            self.assertEqual((floor, floor_read["floorSource"]), ("1", "fixed"))
            runtime._settings = dict(floorControlMode="region",
                floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
            runtime._latest_map_frame = None
            map_sensor.return_value = dict(detected=False)
            result, floor, floor_read = runtime._observe(engine, frame)
            self.assertFalse(result["mapOpen"])
            self.assertIsNone(floor)
            self.assertFalse(floor_read["controlGeometryVerified"])
            self.assertEqual(floor_read["floorSource"], "region")
            self.assertIsNone(floor_read["selectedFloor"])
            self.assertNotIn("controlRects", floor_read)
            self.assertIsNone(runtime.latest_map_frame())

    def test_runtime_observation_uses_applied_not_saved_season(self):
        from test_client_live_runtime import FixtureCapture, fixture_engine
        runtime = LiveRuntime(engine_factories={}, capture_provider=FixtureCapture(), evidence_kind="synthetic")
        for mode, floors in ORDERS:
            engine = fixture_engine(mode)
            frame = column(floors, 1)
            for choice in ("fixed", "auto", "region"):
                for applied, saved, expected in (("s0", "s1", "1"), ("s1", "s0", None)):
                    with self.subTest(mode=mode, choice=choice, applied=applied, saved=saved):
                        runtime._season = applied
                        runtime._settings = dict(gameSeason=saved, floorControlMode=choice,
                            floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
                        with patch("map_interface_observation.detect_selected_floor", side_effect=AssertionError("legacy floor sensor called")), \
                             patch.object(engine.qt_map_ui, "observe", side_effect=AssertionError("legacy map sensor called")), \
                             patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)) as map_sensor, \
                             patch("floor_control_region.locate_vertical_floor_controls", wraps=locate_vertical_floor_controls) as floor_sensor:
                            _, floor, reading = runtime._observe(engine, frame)
                        self.assertEqual(floor, expected)
                        self.assertEqual(runtime._settings["gameSeason"], saved)
                        if applied != "s0":
                            self.assertEqual(reading["floorSource"], "unavailable")
                            self.assertNotIn("centers", reading)
                            map_sensor.assert_not_called()
                            floor_sensor.assert_not_called()
                        else:
                            self.assertEqual(map_sensor.call_count, 1)
                            self.assertTrue(reading["controlGeometryVerified"])


class FloorRegionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="qt-floor-region-test-", dir=ROOT / "work")
        self.sandbox = Path(self.temporary.name)
        (self.sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.settings = SettingsService(self.sandbox)
        self.latest = None
        self.applied = dict(season="s0", mode="nightmare")
        runtime = SimpleNamespace(latest_map_frame=lambda: self.latest, snapshot=lambda: dict(self.applied))
        self.images = FloorRegionImages()
        self.service = FloorRegionService(runtime, self.settings, self.images)

    def tearDown(self):
        self.settings.shutdown()
        self.app.processEvents()
        self.temporary.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "worker completion timeout")

    def test_defaults_and_validation(self):
        self.assertEqual(self.settings.values["floorControlMode"], "auto")
        self.assertIsNone(self.settings.values["floorControlRegion"])
        self.assertFalse(self.settings.setValue("floorControlMode", "manual"))
        self.assertFalse(self.settings.setValue("floorControlRegion", dict(rect=[.9, 0, .2, .1], clientSize=[1920, 1080])))
        self.assertFalse(self.settings.setValue("floorControlRegion", dict(rect=REGION)))

    def test_without_a_map_frame_the_editor_explains_what_to_do(self):
        self.assertFalse(self.service.open())
        self.assertFalse(self.service.available)
        self.assertIn("打开一次地图", self.service.message)

    def test_frame_draft_preview_and_save_through_the_settings_page(self):
        frame = column((2, 1, -1), 1)
        fixed = locate_vertical_floor_controls(frame, "nightmare")
        self.latest = (frame, "nightmare", fixed)
        self.assertTrue(self.service.open())
        self.assertTrue(self.service.available)
        self.assertTrue(self.service.imageUrl.startswith("image://floorregion/"))
        self.assertEqual(self.service.preview, "框内读到：1 楼")
        image = self.images.requestImage("1", None, None)
        self.assertEqual((image.width(), image.height()), (960, int(1080 * VIEW[3])))
        # Too tight: the box cuts the buttons; then a full range again.
        self.assertTrue(self.service.setDraft(1620 / 1920, 360 / 1080, 30 / 1920, 64 / 1080))
        self.assertIn("没读到", self.service.preview)
        self.assertTrue(self.service.setDraft(*REGION))
        self.assertEqual(self.service.preview, "框内读到：1 楼")
        self.assertTrue(self.service.useDraft())
        self.assertFalse(self.service.available)
        self.assertEqual(self.settings.values["floorControlMode"], "region")
        self.assertEqual(self.settings.values["floorControlRegion"]["clientSize"], [1920, 1080])
        self.assertIn("已框选", self.service.summary)
        self.assertTrue(self.settings.save())
        self.wait(lambda: not self.settings.busy)
        saved = json.loads((self.sandbox / "ui-settings.json").read_text("utf-8"))
        self.assertEqual(saved["floorControlMode"], "region")
        self.assertEqual(saved["floorControlRegion"]["rect"], self.settings.persistedValues["floorControlRegion"]["rect"])

    def test_suggestion_covers_the_complete_vertical_column(self):
        for mode, order in ORDERS:
            for floor in order:
                with self.subTest(mode=mode, floor=floor):
                    frame = column(order, floor)
                    reading = locate_vertical_floor_controls(frame, mode)
                    x, y, w, h = suggested_rect(mode, reading, SIZE)
                    points = reading["centerPoints"]
                    self.assertLess(x, min(point[0] for point in points))
                    self.assertGreater(x + w, max(point[0] for point in points))
                    self.assertLess(y, min(point[1] for point in points))
                    self.assertGreater(y + h, max(point[1] for point in points))
                    self.assertEqual(read_floor_in_region(frame, (x, y, w, h), mode)["selectedFloor"], floor)
        self.assertEqual(suggested_rect("nightmare", {}, SIZE), DEFAULT_RECT)
        self.assertEqual(suggested_rect("nightmare", dict(controlAxis="vertical"), SIZE), DEFAULT_RECT)
        self.assertEqual(suggested_rect("nightmare", dict(centers=[.75, .78, .81]), SIZE), DEFAULT_RECT)
        self.assertEqual(VIEW, (.5, 0., .5, .65))
        self.assertGreater(DEFAULT_RECT[1], .1)
        self.assertGreater(DEFAULT_RECT[3], .2)
        for mode, order in ORDERS:
            frame = column(order, 1)
            self.assertEqual(read_floor_in_region(frame, DEFAULT_RECT, mode)["selectedFloor"], 1)

    def test_editor_uses_applied_context_and_invalidates_stale_drafts(self):
        frame = column((2, 1, -1), 1)
        self.latest = (frame, "nightmare", locate_vertical_floor_controls(frame, "nightmare"))
        self.assertTrue(self.settings.setValue("gameSeason", "s1"))
        self.assertTrue(self.service.open())
        self.assertTrue(self.service.available)
        before = self.settings.values["floorControlRegion"]
        self.applied["season"] = "s1"
        self.service.invalidateContext()
        self.assertFalse(self.service.available)
        self.assertEqual(self.service.imageUrl, "")
        self.assertFalse(self.service.useDraft())
        self.assertEqual(self.settings.values["floorControlRegion"], before)
        self.assertFalse(self.service.open())
        self.applied.update(season="s0", mode="difficult")
        self.assertFalse(self.service.open())
        self.applied["mode"] = "nightmare"
        self.assertTrue(self.service.open())
        self.applied["season"] = "s1"
        self.assertFalse(self.service.setDraft(*REGION))
        self.assertFalse(self.service.available)
        self.assertEqual(self.settings.values["floorControlRegion"], before)
        self.applied["season"] = "s0"
        self.assertTrue(self.service.open())
        self.applied["season"] = "s1"
        self.assertFalse(self.service.useDraft())
        self.assertFalse(self.service.available)
        self.assertEqual(self.settings.values["floorControlRegion"], before)

    def test_settings_panel_loads_with_and_without_the_service(self):
        for with_service in (True, False):
            with self.subTest(with_service=with_service):
                engine = QQmlEngine()
                engine.rootContext().setContextProperty("settingsService", self.settings)
                if with_service:
                    engine.rootContext().setContextProperty("floorRegionService", self.service)
                warnings = []
                engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
                component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientFloorRegion.qml"))))
                panel = component.create()
                self.assertIsNotNone(panel, "\n".join(str(e) for e in component.errors()))
                panel.setProperty("width", 640)
                self.app.processEvents()
                self.assertFalse(panel.property("regionMode"))
                self.settings.setValue("floorControlMode", "region")
                self.app.processEvents()
                self.assertTrue(panel.property("regionMode"))
                self.settings.setValue("floorControlMode", "fixed")
                self.app.processEvents()
                self.assertFalse(panel.property("regionMode"))
                self.settings.setValue("floorControlMode", "auto")
                self.app.processEvents()
                self.assertEqual(warnings, [])
                panel.deleteLater()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
