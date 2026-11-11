"""Hidden Qt editor lifecycle using synthetic resources and the real JS planner."""
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from test_client_custom_gallery import CustomGalleryTests, until, APP
from test_scoped_routes import write_scoped_resources, add_synthetic_display_bindings
from client_custom_gallery import CustomGalleryService
from client_custom_display import CustomGalleryDisplay
from PySide6.QtCore import QUrl, QCoreApplication, QEvent, QObject
from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression


class ScopedEditorTests(unittest.TestCase):
    cleanup = CustomGalleryTests.cleanup
    call = CustomGalleryTests.call
    def setUp(self):
        CustomGalleryTests.setUp(self)
        write_scoped_resources(self.sandbox / "_internal/assets/android-route-editor")

    def test_scopes_save_reopen_undo_and_legacy_selection_is_preserved(self):
        self.call("copyGallery", "旧路线")
        legacy = self.service.snapshot["selectedGallery"]
        self.call("addPoint", 20., 20.)
        old_path = self.service.path
        old_data = old_path.read_bytes()
        for scope in ("classic-hard", "season-s1-hard", "season-s1-nightmare"):
            state = self.call("selectScope", scope)
            self.assertEqual(state["scopeId"], scope)
            self.assertEqual(state["mapCount"], 1, state)
            self.call("copyGallery", "自己的路线")
            gid = self.service.snapshot["selectedGallery"]
            self.call("addPoint", 5., 5.)
            state = self.call("addPoint", 11., 11.)
            self.assertEqual(len(state["points"]), 2, state["message"])
            self.assertTrue(state["lines"])
            self.call("undo")
            self.assertEqual(len(self.service.snapshot["points"]), 1)
            self.call("redo")
            self.assertEqual(len(self.service.snapshot["points"]), 2)
            profile_id = self.service.profileGalleryId(gid)
            self.assertIsNotNone(self.service.display_snapshot(profile_id))
            self.assertIsNotNone(self.service.display_snapshot(legacy))
            self.call("refresh")
            self.assertEqual(self.service.snapshot["selectedGallery"], gid)
            self.assertEqual(len(self.service.snapshot["points"]), 2)
        self.assertEqual(old_path.read_bytes(), old_data)
        self.call("selectScope", "legacy-nightmare")
        self.assertEqual(self.service.path.read_bytes(), old_data)
        self.service.shutdown()
        self.service = CustomGalleryService(self.sandbox, library=self.library, default_path=self.default)
        until(lambda: not self.service.busy and bool(self.service.snapshot["galleries"]))
        self.assertIsNotNone(self.service.display_snapshot(profile_id))

    def test_scope_import_cannot_silently_drop_floors_and_game_use_checks_context(self):
        self.call("selectScope", "season-s1-hard")
        self.call("copyGallery", "自己的路线")
        self.call("addPoint", 5., 5.)
        gid = self.service.snapshot["selectedGallery"]
        exported = self.service._store.export(gid)
        target = self.sandbox / "scope.json"
        target.write_text(json.dumps(exported), "utf-8")
        self.call("selectScope", "season-s1-nightmare")
        state = self.call("importGallery", str(target))
        self.assertIn("不属于", state["message"])
        display = CustomGalleryDisplay(self.service)
        qualified = "season-s1-hard~" + gid
        self.assertFalse(display.eligibility(qualified, "nightmare", "s1")["allowed"])
        self.assertTrue(display.eligibility(qualified, "difficult", "s1")["allowed"])
        self.assertFalse(display.eligibility(qualified, "difficult", "s0")["allowed"])

    def test_scoped_qml_has_actual_selectors_and_two_way_state(self):
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(r) for r in rows))
        engine.rootContext().setContextProperty("customGalleryService", self.service)
        # Existing page already permits an omitted settings owner through its guard.
        engine.rootContext().setContextProperty("settingsService", None)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientCustomGallery.qml"))))
        self.assertTrue(engine.rootObjects(), warnings)
        root = engine.rootObjects()[0]
        self.assertIsNotNone(root.findChild(QObject, "customRouteScope"))
        self.call("selectScope", "season-s1-hard")
        APP.processEvents()

        state = root.property("state")
        if hasattr(state, "toVariant"):
            state = state.toVariant()
        self.assertEqual(state["scopeId"], "season-s1-hard")
        self.assertEqual(warnings, [])
        # A completed gallery worker may have just queued asynchronous QML
        # thumbnails. Windows cannot remove their files until reads finish.
        images = [obj for obj in root.findChildren(QObject)
                  if obj.metaObject().className().startswith("QQuickImage")]
        until(lambda: all(QQmlExpression(QQmlEngine.contextForObject(obj), obj,
                                        "status !== 2").evaluate()[0] for obj in images))
        engine.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        APP.processEvents()

    def test_registered_scoped_pixels_reach_display_and_reject_foreign_masks(self):
        import numpy as np
        from shared_structure_reference import SharedStructureReference
        add_synthetic_display_bindings(self.sandbox / "_internal/assets/android-route-editor")
        display = CustomGalleryDisplay(self.service)
        for scope, mode, season, mid, floor in (
                ("classic-hard", "difficult", "s0", "map-01", "1"),
                ("season-s1-hard", "difficult", "s1", "106001", "-1"),
                ("season-s1-nightmare", "nightmare", "s1", "107001", "-1")):
            self.call("selectScope", scope)
            self.call("copyGallery", "显示路线")
            self.call("addPoint", 5., 5.)
            self.call("addPoint", 11., 11.)
            gid = self.service.profileGalleryId(self.service.snapshot["selectedGallery"])
            mask = np.ones((14, 14), np.uint8)
            reference = SharedStructureReference(mask, mask.copy(),
                dict(binding=dict(profile="shared-structure", mapId=mid, floor=floor)), self.folder / "synthetic-reference.npz")
            layer = display.layer("custom:" + gid, mid, floor, reference, mode, season)
            self.assertIsNotNone(layer, display.last_error)
            self.assertEqual(layer["pixels"].shape, (14, 14, 4))
            self.assertFalse(layer["pixels"].flags.writeable)
            self.assertGreater(np.count_nonzero(layer["pixels"][:, :, 3]), 0)
            wrong = SharedStructureReference(np.zeros_like(mask), mask.copy(), reference.metadata, reference.path)
            self.assertIsNone(display.layer("custom:" + gid, mid, floor, wrong, mode, season))
            other_season = "s1" if season == "s0" else "s0"
            self.assertIsNone(display.layer("custom:" + gid, mid, floor, reference, mode, other_season))



if __name__ == "__main__":
    unittest.main()
