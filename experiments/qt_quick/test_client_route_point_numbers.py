"""Offline selected-target rules, saved Qt intent and real display consumers.

All art/topology/capture/solver inputs are synthetic; no account or game IO.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from custom_maps import marker_groups, render_game_layer
from client_custom_display import CustomGalleryDisplay, custom_profile_id
from client_settings import SettingsService
import test_client_custom_display as display_fixture
import test_client_custom_gallery as gallery_fixture
import test_client_coordinator as coordinator_fixture
import test_client_live_runtime as runtime_fixture
from test_client_route_plaza import item

APP = gallery_fixture.APP
until = gallery_fixture.until
HERE = Path(__file__).parent


def target(floor, x, y=60, room=None, **extra):
    return dict(floor=floor, x=x, y=y, kind="room" if room else "via", room=room,
                color="#FF4D67", **extra)


class NumberRulesTests(unittest.TestCase):
    def test_route_order_per_floor_and_new_start_does_not_reset(self):
        points = [target("1", 10, room="A"), target("2", 40, room="A"),
                  target("1", 30), target("B1", 80), target("2", 70, start=True),
                  target("1", 90, room="Z", start=True)]
        before = deepcopy(points)
        self.assertEqual(marker_groups(points, "1", per_floor=True),
                         [(10, 60, "1"), (30, 60, "2"), (90, 60, "3")])
        self.assertEqual(marker_groups(points, "2", per_floor=True), [(40, 60, "1"), (70, 60, "2")])
        self.assertEqual(marker_groups(points, "B1", per_floor=True), [(80, 60, "1")])
        self.assertEqual(marker_groups(points, "absent", per_floor=True), [])
        self.assertEqual(points, before)

    def test_repeated_room_and_via_keep_visit_numbers_without_overlap(self):
        points = [target("1", 10, room="R"), target("1", 50),
                  target("2", 10, room="R"), target("1", 12, room="R"),
                  target("1", 50), target("1", 10, room="R"), target("1", 10, room="R")]
        self.assertEqual(marker_groups(points, "1", per_floor=True),
                         [(10, 60, "1/3/5+1"), (50, 60, "2/4")])
        self.assertEqual(marker_groups(points, "2", per_floor=True), [(10, 60, "1")])
        self.assertEqual(marker_groups([], "1", per_floor=True), [])


class DisplayNumberTests(unittest.TestCase):
    setUp = gallery_fixture.CustomGalleryTests.setUp
    cleanup = gallery_fixture.CustomGalleryTests.cleanup
    call = gallery_fixture.CustomGalleryTests.call
    reference = display_fixture.CustomDisplayTests.reference

    def test_planned_pass_through_cells_never_become_numbered_targets(self):
        # R -> D passes through C (index 4); C is NOT a selected target.
        points = [target("1", 0, y=0, room="R"), target("1", 180)]
        cells = self.library.graph("synthetic-map").plan(points)[0]["cells"]
        self.assertIn(("1", 4), cells)
        captured = []
        from custom_maps import draw_route
        def record(draw, transform, lines, markers, **kwargs):
            captured.extend(markers)
            return draw_route(draw, transform, lines, markers, **kwargs)
        with patch("custom_maps.draw_route", side_effect=record):
            render_game_layer(self.library, "synthetic-map", "1", points, point_numbers=True)
        self.assertEqual(captured, [(0, 0, "1"), (180, 60, "2")])
        captured.clear()
        with patch("custom_maps.draw_route", side_effect=record):
            render_game_layer(self.library, "synthetic-map", "1", points, point_numbers=False)
        self.assertEqual(captured, [])

    def test_toggle_binds_cache_pixels_and_rejects_late_aba_render(self):
        self.call("createGallery", "标号测试")
        self.call("addPoint", 20., 20.)
        self.call("addPoint", 90., 30.)
        gid = self.service.snapshot["selectedGallery"]
        display = CustomGalleryDisplay(self.service)
        def layer():
            return display.layer(custom_profile_id(gid), "synthetic-map", "1", self.reference(), "nightmare")
        off = layer()
        self.assertFalse(off["resourceIdentity"]["pointNumbers"])
        display.set_point_numbers(True)
        on = layer()
        self.assertTrue(on["resourceIdentity"]["pointNumbers"])
        self.assertFalse(np.array_equal(off["pixels"], on["pixels"]))
        np.testing.assert_array_equal(off["matrixDisplayToReference"], on["matrixDisplayToReference"])
        display.set_point_numbers(False)
        np.testing.assert_array_equal(layer()["pixels"], off["pixels"])
        started, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            started.set()
            release.wait(3)
            return render_game_layer(*args, **kwargs)
        display.invalidate()
        results = []
        with patch("client_custom_display.render_game_layer", side_effect=blocked):
            worker = threading.Thread(target=lambda: results.append(layer()))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                display.set_point_numbers(True)
                display.set_point_numbers(False)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [None])
        self.assertIn("preference-changed", display.last_error)
        self.assertFalse(display._layers)

    def test_settings_default_persistence_field_isolation_and_failed_save(self):
        self.assertFalse(self.settings.persistedValues["routePointNumbers"])
        self.settings.setValue("sheetOpacity", .7)  # Unrelated draft must survive.
        self.settings.setValue("routePointNumbers", True)
        self.assertTrue(self.settings.saveFields(["routePointNumbers"]))
        until(lambda: not self.settings.busy)
        raw = json.loads(self.settings.path.read_text())
        self.assertTrue(raw["routePointNumbers"])
        self.assertNotIn("sheetOpacity", raw)
        restored = SettingsService(self.sandbox)
        self.addCleanup(restored.shutdown)
        self.assertTrue(restored.persistedValues["routePointNumbers"])
        restored.setValue("routePointNumbers", False)
        with patch.object(restored, "_write", side_effect=OSError("offline simulated disk failure")):
            self.assertTrue(restored.saveFields(["routePointNumbers"]))
            until(lambda: not restored.busy)
        self.assertTrue(restored.persistedValues["routePointNumbers"])
        self.assertIn("保存失败", restored.message)
        self.assertTrue(restored.saveFields(["routePointNumbers"]))
        until(lambda: not restored.busy)
        self.assertFalse(json.loads(restored.path.read_text())["routePointNumbers"])
        self.assertFalse(restored.setValue("routePointNumbers", "true"))
        restored.path.write_text(json.dumps(dict(routePointNumbers="true", future="keep")))
        invalid = SettingsService(self.sandbox)
        self.addCleanup(invalid.shutdown)
        self.assertFalse(invalid.persistedValues["routePointNumbers"])
        self.assertIn("无效字段", invalid.message)
        self.assertEqual(json.loads(invalid.path.read_text())["future"], "keep")

    def test_real_qml_switch_pixels_floor_resize_and_restart(self):
        self.call("createGallery", "离线标号示例")
        self.call("addPoint", 20., 20.)
        self.call("addPoint", 90., 30.)
        self.call("selectFloor", "2")
        self.call("addPoint", 30., 30.)
        self.call("selectFloor", "1")
        self.call("selectMap", "synthetic-map")
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("customGalleryService", self.service)
        engine.rootContext().setContextProperty("settingsService", self.settings)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(r.toString() for r in rows))
        engine.loadData(b'''import QtQuick
import QtQuick.Controls.Basic
ApplicationWindow { visible: true; width: 1000; height: 720
ClientCustomGallery { anchors.fill: parent; anchors.margins: 24 }
}''', QUrl.fromLocalFile(str(HERE / "route-numbers-harness.qml")))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        try:
            art = item(window, "customMapImage")
            until(lambda: art.property("paintedWidth") > 0 and art.width() > 200)
            QTest.qWait(150)
            switch = item(window, "routePointNumbersSwitch")
            self.assertFalse(switch.property("checked"))
            def pixels(name):
                QTest.qWait(100)
                image = window.grabWindow()
                folder = os.environ.get("XINGTU_QT_NUMBER_EVIDENCE_DIR")
                if folder:
                    Path(folder).mkdir(parents=True, exist_ok=True)
                    self.assertTrue(image.save(str(Path(folder) / name)))
                return image
            off = pixels("numbers-off.png")
            position = switch.mapToScene(QPointF(switch.width()/2, switch.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            until(lambda: self.settings.persistedValues["routePointNumbers"] and not self.settings.busy)
            on = pixels("numbers-on-f1.png")
            # Compare only the actual map, not the changed switch.
            rect = art.mapToScene(QPointF(0, 0))
            crop = (int(rect.x()), int(rect.y()), int(art.width()), int(art.height()))
            self.assertNotEqual(off.copy(*crop), on.copy(*crop))
            self.assertEqual([m["label"] for m in self.service.snapshot["markers"]], ["1", "2"])
            self.call("selectFloor", "2")
            self.assertEqual([m["label"] for m in self.service.snapshot["markers"]], ["1"])
            pixels("numbers-on-f2.png")
            window.resize(1180, 820)
            pixels("numbers-on-large.png")
            restored = SettingsService(self.sandbox)
            self.addCleanup(restored.shutdown)
            self.assertTrue(restored.persistedValues["routePointNumbers"])
            window.resize(1000, 720)
            self.call("selectFloor", "1")
            QTest.qWait(100)
            position = switch.mapToScene(QPointF(switch.width()/2, switch.height()/2)).toPoint()
            QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
            until(lambda: not self.settings.persistedValues["routePointNumbers"] and not self.settings.busy)
            self.assertEqual(off.copy(*crop), pixels("numbers-off-again.png").copy(*crop))
            self.assertTrue(restored.reload())
            self.assertFalse(restored.persistedValues["routePointNumbers"])
            self.assertEqual(warnings, [])
        finally:
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            APP.processEvents()


class RuntimeNumberTests(unittest.TestCase):
    def test_both_modes_without_selected_target_data_keep_existing_display(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                case = runtime_fixture.LiveRuntimeContracts()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.dispatch("set_game_mode", mode=mode)
                case.begin_and_capture()
                before = case.runtime.snapshot()
                case.runtime.configure_settings(dict(routePointNumbers=True))
                payload = case.runtime.render_payload(before["render"]["token"])
                self.assertIsNotNone(payload)
                self.assertEqual(payload["displayKind"], "shared-structure")
                self.assertEqual(before["mapId"], case.runtime.snapshot()["mapId"])

    def fixture(self):
        case = runtime_fixture.LiveRuntimeContracts()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.dispatch("set_route_profile", profileId="custom:fixture")
        def layer(*args):
            enabled = case.runtime._settings.get("routePointNumbers", False)
            return dict(pixels=np.full((20, 24, 4), 200 if enabled else 100, np.uint8),
                matrixDisplayToReference=np.eye(3), resourceIdentity=dict(pointNumbers=enabled),
                displayKind="custom-gallery", displayLabel="synthetic", routeProfileId="custom:fixture")
        case.engine.qt_custom_display = layer
        case.begin_and_capture()
        return case

    def test_toggle_reprojects_current_pixels_without_changing_identity_or_pose(self):
        case = self.fixture()
        before = case.runtime.snapshot()
        pose = case.runtime._core.pose_by_map["fixture-map"].pose_for_floor("1").copy()
        count = case.capture.count
        rank_count = case.engine.rank_count
        case.runtime.configure_settings(dict(routePointNumbers=True))
        self.assertIsNone(case.runtime.render_payload(before["render"]["token"]))
        self.assertIsNone(case.runtime.reference_payload())
        result = case.dispatch("set_context", refreshDisplay=True)
        self.assertTrue(result["success"], result)
        payload = case.runtime.render_payload(result["renderToken"])
        self.assertTrue(payload["resourceIdentity"]["pointNumbers"])
        np.testing.assert_array_equal(payload["pixels"], np.full((20,24,4), 200, np.uint8))
        after = case.runtime.snapshot()
        for field in ("mapId", "floor", "roundId", "frameId"):
            self.assertEqual(before[field], after[field])
        np.testing.assert_array_equal(pose, case.runtime._core.pose_by_map["fixture-map"].pose_for_floor("1"))
        self.assertEqual((case.capture.count, case.engine.rank_count), (count, rank_count))

    def test_end_cancel_and_preference_change_reject_late_reprojection(self):
        from client_route_reprojection import prepare_route_reprojection
        for action in ("end", "cancel", "toggle"):
            with self.subTest(action=action):
                case = self.fixture()
                cancelled = threading.Event()
                def late(*args, **kwargs):
                    result = prepare_route_reprojection(*args, **kwargs)
                    if action == "end":
                        case.dispatch("end_round")
                    elif action == "cancel":
                        cancelled.set()
                    else:
                        case.runtime.configure_settings(dict(routePointNumbers=True))
                    return result
                with patch("client_route_reprojection.prepare_route_reprojection", side_effect=late):
                    result = case.runtime.dispatch("set_context", dict(refreshDisplay=True), cancelled)
                self.assertFalse(result["success"], result)
                self.assertTrue(result["stale"])


class CoordinatorNumberTests(unittest.TestCase):
    def test_saved_toggle_waits_for_worker_and_does_not_refresh_unrelated_author(self):
        case = coordinator_fixture.CoordinatorTests()
        case.app = APP
        case.setUp()
        self.addCleanup(case.tearDown)
        owner = case.coordinator
        case.settings.persistedValues["routePointNumbers"] = True
        owner._settings_saved()
        case.session.dispatchWithPayload.assert_not_called()
        owner._applied_context = ("nightmare", "custom:fixture", "s0")
        case.runtime_state["profileId"] = "custom:fixture"
        case.settings.persistedValues.update(routePointNumbers=False, routeProfileId="custom:fixture")
        case.session.busy = True
        owner._settings_saved()
        self.assertTrue(owner._pending_display_refresh)
        case.session.dispatchWithPayload.assert_not_called()
        case.session.busy = False
        owner._session_changed()
        self.assertTrue(owner._pending_display_refresh)
        owner._try_apply_context()
        case.session.dispatchWithPayload.assert_called_once_with("set_context",
            dict(season="s0", mode="nightmare", profileId="custom:fixture", refreshDisplay=True))


if __name__ == "__main__":
    unittest.main()
