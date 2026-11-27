"""Qt service and QML UI integration tests for real-time route length display."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

import test_client_custom_gallery as gallery_fixture
from test_client_route_plaza import item

APP = gallery_fixture.APP
until = gallery_fixture.until
HERE = Path(__file__).parent


class ClientRouteLengthTests(unittest.TestCase):
    setUp = gallery_fixture.CustomGalleryTests.setUp
    cleanup = gallery_fixture.CustomGalleryTests.cleanup
    call = gallery_fixture.CustomGalleryTests.call

    def test_realtime_length_multifloor_undo_clear_and_schema_preserved(self):
        self.call("createGallery", "方案A")
        state = self.call("selectMap", "synthetic-map")
        # Empty route => 0 格
        self.assertEqual(state["routeLength"]["totalUnits"], 0.0)
        self.assertEqual(state["routeLength"]["totalText"], "0 格")
        self.assertEqual([f["text"] for f in state["routeLength"]["floors"]], ["0 格", "0 格"])

        # Single point => still 0 格
        state = self.call("addPoint", 20.0, 20.0)
        self.assertEqual(len(state["points"]), 1)
        self.assertEqual(state["routeLength"]["totalUnits"], 0.0)
        self.assertEqual(state["routeLength"]["totalText"], "0 格")

        # Second point on floor 1: native path 0->1->3->4->5 = 240 px, ref=200 => 120.0 格
        state = self.call("addPoint", 90.0, 30.0)
        self.assertEqual(len(state["points"]), 2)
        self.assertAlmostEqual(state["routeLength"]["totalUnits"], 120.0, places=5)
        self.assertEqual(state["routeLength"]["totalText"], "120.0 格")
        self.assertFalse(state["routeLength"]["hasMultiFloorRoute"])
        by_floor = {f["id"]: f for f in state["routeLength"]["floors"]}
        self.assertEqual(by_floor["1"]["text"], "120.0 格")
        self.assertEqual(by_floor["2"]["text"], "0 格")

        # Third point on floor 2 via stairs:
        # ("1",5)->("1",4)=60px + stair(excluded) + ("2",4)->("2",5)=60px
        # Floor 1 = 300px (150.0 格), Floor 2 = 60px (30.0 格), Total = 180.0 格
        self.call("selectFloor", "2")
        state = self.call("addPoint", 90.0, 30.0)
        self.assertEqual(len(state["points"]), 3)
        self.assertTrue(state["routeLength"]["hasMultiFloorRoute"])
        self.assertAlmostEqual(state["routeLength"]["totalUnits"], 180.0, places=5)
        self.assertEqual(state["routeLength"]["totalText"], "180.0 格")
        by_floor = {f["id"]: f for f in state["routeLength"]["floors"]}
        self.assertEqual(by_floor["1"]["text"], "150.0 格")
        self.assertEqual(by_floor["2"]["text"], "30.0 格")

        # Map list row reflects saved length and multi-floor breakdown
        map_row = next(m for m in state["maps"] if m["id"] == "synthetic-map")
        self.assertTrue(map_row["drawn"])
        self.assertEqual(map_row["routeLengthText"], "180.0 格")
        self.assertIn("一楼 150.0", map_row["routeLengthFloorText"])
        self.assertIn("二楼 30.0", map_row["routeLengthFloorText"])

        # Saved JSON format stays unchanged (no length fields persisted)
        raw = json.loads(self.service.path.read_text(encoding="utf-8"))
        saved_map = raw["galleries"][0]["maps"]["synthetic-map"]
        self.assertEqual(set(saved_map.keys()), {"points"})

        # Undo returns immediately to 120.0 格, redo restores 180.0 格
        state = self.call("undo")
        self.assertEqual(state["routeLength"]["totalText"], "120.0 格")
        state = self.call("redo")
        self.assertEqual(state["routeLength"]["totalText"], "180.0 格")

        # Clear route returns to 0 格
        self.service.requestClearRoute()
        state = self.call("confirmOperation", True)
        self.assertEqual(state["routeLength"]["totalUnits"], 0.0)
        self.assertEqual(state["routeLength"]["totalText"], "0 格")

    def test_old_saved_gallery_computes_length_and_compares_two_routes(self):
        # Create two galleries on the same map: 路线长 (180 格) and 路线短 (90 格)
        self.call("createGallery", "路线长")
        self.call("selectMap", "synthetic-map")
        self.call("addPoint", 20.0, 20.0)
        self.call("addPoint", 90.0, 30.0)
        self.call("selectFloor", "2")
        self.call("addPoint", 90.0, 30.0)

        self.call("createGallery", "路线短")
        self.call("selectMap", "synthetic-map")
        self.call("selectFloor", "1")
        self.call("addPoint", 20.0, 20.0)
        self.call("addPoint", 60.0, 30.0)  # native (120, 60) -> 180 px => 90.0 格

        # Reload fresh service from the saved JSON on disk (simulating opening old route file)
        self.service.shutdown()
        from client_custom_gallery import CustomGalleryService
        self.service = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        until(lambda: bool(self.service.snapshot["galleries"]) and not self.service.busy)

        galleries = {g["name"]: g["id"] for g in self.service.snapshot["galleries"]}
        state_short = self.call("selectGallery", galleries["路线短"])
        short_card = next(m for m in state_short["maps"] if m["id"] == "synthetic-map")
        self.assertEqual(short_card["routeLengthText"], "90.0 格")
        self.assertEqual(len(state_short["mapComparisons"]), 1)
        self.assertEqual(state_short["mapComparisons"][0]["galleryName"], "路线长")
        self.assertEqual(state_short["mapComparisons"][0]["totalText"], "180.0 格")

        state_long = self.call("selectGallery", galleries["路线长"])
        long_card = next(m for m in state_long["maps"] if m["id"] == "synthetic-map")
        self.assertEqual(long_card["routeLengthText"], "180.0 格")
        self.assertLess(short_card["routeLengthUnits"], long_card["routeLengthUnits"])

    def test_real_qml_displays_lengths_segment_selection_and_gallery_cards(self):
        self.call("createGallery", "路线甲")
        self.call("selectMap", "synthetic-map")
        self.call("addPoint", 20.0, 20.0)
        self.call("addPoint", 90.0, 30.0)
        self.call("selectFloor", "2")
        self.call("addPoint", 90.0, 30.0)

        self.call("createGallery", "路线乙")
        self.call("selectMap", "synthetic-map")
        self.call("selectFloor", "1")
        self.call("addPoint", 20.0, 20.0)
        self.call("addPoint", 60.0, 30.0)

        galleries = {g["name"]: g["id"] for g in self.service.snapshot["galleries"]}
        self.call("selectGallery", galleries["路线甲"])
        self.call("selectMap", "synthetic-map")
        self.call("selectFloor", "1")

        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(r.toString() for r in rows))
        engine.rootContext().setContextProperty("customGalleryService", self.service)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        engine.loadData(
            b"""import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 1080; height: 740
    ClientCustomGallery { anchors.fill: parent; anchors.margins: 20 }
}""",
            QUrl.fromLocalFile(str(HERE / "route-length-harness.qml")),
        )
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        try:
            art = item(window, "customMapImage")
            until(lambda: art.property("paintedWidth") > 0 and art.width() > 300)
            QTest.qWait(100)

            total_lbl = item(window, "customRouteTotalLength")
            f1_lbl = item(window, "customFloorLength_1")
            f2_lbl = item(window, "customFloorLength_2")
            hint_lbl = item(window, "customRouteLengthHint")
            cmp_lbl = item(window, "customRouteComparisons")
            self.assertIn("180.0 格", total_lbl.property("text"))
            self.assertEqual(f1_lbl.property("text"), "一楼 150.0 格")
            self.assertEqual(f2_lbl.property("text"), "二楼 30.0 格")
            self.assertIn("同一张图内比较", hint_lbl.property("text"))
            self.assertTrue(cmp_lbl.isVisible())
            self.assertIn("路线乙 90.0 格", cmp_lbl.property("text"))

            # Click segment badge for point index 2 (cross-floor segment 2->3: 30+30 = 60.0 格)
            seg_bar = item(window, "customSelectedSegmentBar")
            self.assertFalse(seg_bar.isVisible())
            badge = item(window, "customPointSegmentBadge_2")
            self.assertIsNotNone(badge)
            pos = badge.mapToScene(QPointF(badge.width() / 2, badge.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
            until(lambda: seg_bar.isVisible())
            seg_text = item(window, "customSelectedSegmentText").property("text")
            self.assertIn("2→3", seg_text)
            self.assertIn("60.0 格", seg_text)
            self.assertIn("跨层不计", seg_text)

            # Clear selected segment
            clear_btn = item(window, "customClearSelectedSegment")
            cpos = clear_btn.mapToScene(QPointF(clear_btn.width() / 2, clear_btn.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, cpos)
            until(lambda: not seg_bar.isVisible())

            # Return to gallery browse view and verify saved map card displays length
            self.service.browseGalleries()
            until(lambda: not self.service.snapshot["editing"])
            QTest.qWait(100)
            card_len = item(window, "customMapCardLength_synthetic-map")
            card_floors = item(window, "customMapCardFloors_synthetic-map")
            self.assertIn("180.0 格", card_len.property("text"))
            self.assertTrue(card_floors.isVisible())
            self.assertIn("一楼 150.0", card_floors.property("text"))
            self.assertIn("二楼 30.0", card_floors.property("text"))
            self.assertEqual(warnings, [])
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
