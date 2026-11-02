"""框选 floor recognition: region reader, mode switch, settings, editor and runtime seam."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import numpy as np
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from client_settings import ROOT, SettingsService
from client_floor_region import FloorRegionImages, FloorRegionService, suggested_rect
from client_live_runtime import LiveRuntime
from floor_control_region import apply_floor_region, locate_floor_row, read_floor_in_region, region_for_client

SIZE = (1920, 1080)
ANCHOR, TILE, TOP = 1500, 64, 40     # the selected button stays at ANCHOR
REGION = [1300 / 1920, 25 / 1080, 400 / 1920, 75 / 1080]


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
    def test_sliding_row_reads_every_floor_in_both_modes(self):
        for mode, floors in (("nightmare", (-1, 1, 2)), ("difficult", (1, 2))):
            for selected in floors:
                with self.subTest(mode=mode, selected=selected):
                    read = read_floor_in_region(hud(floors, selected, seed=selected + 5), REGION, mode)
                    self.assertEqual(read["selectedFloor"], selected, read)
                    self.assertEqual(len(read["centers"]), len(floors))

    def test_fixed_equal_slots_would_be_wrong_but_structure_is_not(self):
        # The highlight sits at the same pixels for floor 1 and floor 2.
        one, two = hud((-1, 1, 2), 1), hud((-1, 1, 2), 2)
        self.assertGreater(float(one[TOP:TOP + 44, ANCHOR - 20:ANCHOR + 20].mean()), 80)
        self.assertGreater(float(two[TOP:TOP + 44, ANCHOR - 20:ANCHOR + 20].mean()), 80)
        self.assertEqual(read_floor_in_region(one, REGION, "nightmare")["selectedFloor"], 1)
        self.assertEqual(read_floor_in_region(two, REGION, "nightmare")["selectedFloor"], 2)

    def test_cut_or_wrong_count_reads_nothing_instead_of_guessing(self):
        frame = hud((-1, 1, 2), 2)
        cut = [(ANCHOR - TILE) / 1920, REGION[1], (2 * TILE) / 1920, REGION[3]]
        self.assertIsNone(read_floor_in_region(frame, cut, "nightmare")["selectedFloor"])
        # Three buttons where difficult mode has two.
        self.assertEqual(read_floor_in_region(hud((-1, 1, 2), 1), REGION, "difficult")["reason"], "too-many-buttons")
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
    def test_row_is_found_anywhere_in_the_top_right_corner(self):
        global ANCHOR, TOP
        saved = ANCHOR, TOP
        try:
            for ANCHOR, TOP in ((1500, 40), (1100, 120), (1750, 300)):
                for mode, floors in (("nightmare", (-1, 1, 2)), ("difficult", (1, 2))):
                    for selected in floors:
                        with self.subTest(anchor=ANCHOR, top=TOP, mode=mode, selected=selected):
                            read = locate_floor_row(hud(floors, selected, seed=ANCHOR + selected), mode)
                            self.assertEqual(read["selectedFloor"], selected, read)
        finally:
            ANCHOR, TOP = saved

    def test_other_bright_items_or_light_ui_are_not_buttons(self):
        frame = np.full((1080, 1920, 3), 33, np.uint8)
        frame[60:110, 1700:1760] = 150                       # a lone filled block
        frame[200:240, 1300:1360] = 120; frame[205:235, 1325:1331] = 230
        self.assertIsNone(locate_floor_row(frame, "nightmare")["selectedFloor"])
        light = hud((-1, 1, 2), 1)
        light[:, 960:] = np.clip(light[:, 960:].astype(int) + 170, 0, 255).astype(np.uint8)
        self.assertIsNone(locate_floor_row(light, "nightmare")["selectedFloor"])
        # The row outside the top-right corner is not searched.
        low = np.full((1080, 1920, 3), 33, np.uint8)
        low[700:744] = hud((-1, 1, 2), 1)[TOP:TOP + 44]
        self.assertIsNone(locate_floor_row(low, "nightmare")["selectedFloor"])


class ModeSwitchTests(unittest.TestCase):
    fixed = dict(selectedFloor=None, layout="unlocalized", centers=None)

    def test_auto_is_the_default_and_only_searches_when_fixed_reads_nothing(self):
        frame = hud((-1, 1, 2), 2)
        out = apply_floor_region({}, "nightmare", frame, self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (2, "auto-located"))
        self.assertEqual(out["layout"], "unlocalized")   # click geometry stays with the fixed reading
        out = apply_floor_region(dict(floorControlMode="auto"), "nightmare", frame, dict(self.fixed, selectedFloor=1))
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (1, "fixed"))
        out = apply_floor_region({}, "nightmare", np.full((1080, 1920, 3), 33, np.uint8), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (None, "fixed-auto-unread"))

    def test_fixed_mode_leaves_the_reading_alone(self):
        settings = dict(floorControlMode="fixed", floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        out = apply_floor_region(settings, "nightmare", hud((-1, 1, 2), 2), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (None, "fixed"))

    def test_region_mode_reads_the_box_and_falls_back_to_auto(self):
        settings = dict(floorControlMode="region", floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        out = apply_floor_region(settings, "nightmare", hud((-1, 1, 2), -1), dict(self.fixed, selectedFloor=1))
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (-1, "region"))
        out = apply_floor_region(settings, "nightmare", np.full((1080, 1920, 3), 33, np.uint8),
                                 dict(self.fixed, selectedFloor=2))
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (2, "fixed-region-unread"))
        out = apply_floor_region(dict(floorControlMode="region"), "nightmare", hud((-1, 1, 2), 1), self.fixed)
        self.assertEqual((out["selectedFloor"], out["floorSource"]), (1, "auto-located"))

    def test_runtime_uses_the_region_only_while_the_map_is_open(self):
        runtime = LiveRuntime.__new__(LiveRuntime)
        runtime._diagnostics, runtime._latest_map_frame = None, None
        runtime._settings = dict(floorControlMode="region",
                                 floorControlRegion=dict(rect=REGION, clientSize=[1920, 1080]))
        observed = dict(mapOpen=True, floor=None, glyphs=[], reason="two-floor-highlight-ambiguous")
        engine = SimpleNamespace(pack=SimpleNamespace(mode="difficult"),
                                 qt_map_ui=SimpleNamespace(observe=lambda frame: dict(observed)))
        frame = hud((1, 2), 2)
        result, floor, floor_read = runtime._observe(engine, frame)
        self.assertEqual((floor, floor_read["selectedFloor"], floor_read["floorSource"]), ("2", 2, "region"))
        self.assertIs(runtime.latest_map_frame()[0], frame)
        runtime._settings = {}    # 自动 by default
        result, floor, floor_read = runtime._observe(engine, hud((1, 2), 1))
        self.assertEqual((floor, floor_read["floorSource"]), ("1", "auto-located"))
        observed["mapOpen"] = False
        runtime._latest_map_frame = None
        _result, floor, floor_read = runtime._observe(engine, frame)
        self.assertIsNone(floor)
        self.assertNotIn("floorSource", floor_read)
        self.assertIsNone(runtime.latest_map_frame())


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
        runtime = SimpleNamespace(latest_map_frame=lambda: self.latest)
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
        frame = hud((-1, 1, 2), 1)
        fixed = dict(selectedFloor=1, centers=[(ANCHOR + d * TILE) / 1920 for d in (-1, 0, 1)],
                     regionTop=TOP / 1080, regionHeight=44 / 1080)
        self.latest = (frame, "nightmare", fixed)
        self.assertTrue(self.service.open())
        self.assertTrue(self.service.available)
        self.assertTrue(self.service.imageUrl.startswith("image://floorregion/"))
        self.assertEqual(self.service.preview, "框内读到：1 楼")
        image = self.images.requestImage("1", None, None)
        self.assertEqual((image.width(), image.height()), (960, int(1080 * .32)))
        # Too tight: the box cuts the buttons; then a full range again.
        self.assertTrue(self.service.setDraft(ANCHOR / 1920, REGION[1], TILE / 1920, REGION[3]))
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

    def test_suggestion_covers_the_whole_slide_range(self):
        fixed = dict(selectedFloor=1, centers=[.75, .78, .81], regionTop=.02, regionHeight=.05)
        x, y, w, h = suggested_rect("nightmare", fixed, SIZE)
        self.assertLessEqual(x, .78 - 2 * .03)
        self.assertGreaterEqual(x + w, .78 + 2 * .03)
        self.assertLess(y, .02)
        self.assertGreater(y + h, .07)
        self.assertEqual(len(suggested_rect("nightmare", dict(centers=None), SIZE)), 4)

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
