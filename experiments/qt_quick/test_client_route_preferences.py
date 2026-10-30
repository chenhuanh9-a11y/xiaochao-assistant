"""Synthetic catalog/coordinate fixtures; no author assets, game or native input."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from client_paths import APP_ROOT
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from author_route_catalog import AuthorRouteCatalog
from nightmare_author_coordinate_adapter import AuthorCoordinateAdapters, CANONICAL_SCHEMA
from client_live_runtime import LiveRuntime
from client_route_preferences import coffin_variant_metadata, selected_coffin_binding
from client_settings import ROOT, SettingsService
import client_paths
import test_client_live_runtime as contracts


class CoffinPreferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / "work", prefix="qt-preference-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        variants = []
        for number in (1, 2):
            path = self.root / f"variant-0{number}.png"
            pixels = np.full((20, 24, 4), (number * 40, 30, 60, 255), np.uint8)
            cv2.imencode(".png", pixels)[1].tofile(str(path))
            variants.append(dict(id=f"variant-0{number}", displayName=f"路线{number}", layers={"1": path.name},
                                 identityStatus="display-only", displayPolicy="requires-current-shared-structure-fit"))
        catalog_path = self.root / "catalog.json"
        catalog_path.write_text(json.dumps(dict(schemaVersion=1, profiles=[
            dict(id="zhan10-coffin", displayName="全棺", authorName="fixture", routeFamilyName="fixture",
                 runtimeEnabled=False, settingsSelectable=True, alignedOverlayEligible=True,
                 maps={"nightmare-01": variants})])), encoding="utf-8")
        row = dict(usableForCurrentFrameRendering=True, crossFloorProjectionAllowed=False,
                   matrixProfileToCanonical=np.eye(3).tolist(), evidence=dict(algebraicallyExact=True))
        adapter_path = self.root / "coordinates.json"
        adapter_path.write_text(json.dumps(dict(schema=CANONICAL_SCHEMA, canonicalPackId="fixture",
            policy=dict(requiresCurrentFrameGeometryFit=True, authorizesMapIdentity=False,
                        authorizesCrossFloorProjection=False),
            profiles={p: dict(maps={"nightmare-01": dict(floors={"1": copy.deepcopy(row)})})
                      for p in ("shared-structure", "zhan10-coffin")})), encoding="utf-8")
        self.engine = contracts.fixture_engine()
        self.engine.qt_catalog = AuthorRouteCatalog(catalog_path, asset_base=self.root)
        self.engine.qt_adapters = AuthorCoordinateAdapters(adapter_path)

    def metadata(self):
        return coffin_variant_metadata(self.engine, "nightmare", "zhan10-coffin", "nightmare-01", "1")

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate())

    def service(self):
        service = SettingsService(self.root)
        self.addCleanup(service.shutdown)
        service.setValue("routeProfileId", "zhan10-coffin")
        return service

    def test_original_declarations_select_distinct_pixels_without_optional_sidecar(self):
        self.assertEqual([r["available"] for r in self.metadata()["options"]], [True, True])
        layers = [LiveRuntime._display_layer(self.engine, "nightmare-01", "1", "zhan10-coffin",
                                             variant_id=f"variant-0{i}") for i in (1, 2)]
        self.assertFalse(np.array_equal(layers[0]["pixels"], layers[1]["pixels"]))
        self.assertNotEqual(layers[0]["resourceIdentity"], layers[1]["resourceIdentity"])
        self.assertTrue(np.array_equal(layers[0]["matrixDisplayToReference"], layers[1]["matrixDisplayToReference"]))
        self.assertEqual(self.engine.rank_count, 0)

    def test_explicit_bitmap_and_native_variant_bindings_cannot_authorize_another_variant(self):
        default = selected_coffin_binding(self.engine, "nightmare-01", "1", "zhan10-coffin", "variant-01")
        self.engine.qt_optional_displays = SimpleNamespace(binding=lambda *a: default)
        rows = self.metadata()["options"]
        self.assertTrue(rows[0]["available"])
        self.assertFalse(rows[1]["available"])
        self.assertIn("另一版本", rows[1]["reason"])
        self.engine.qt_optional_displays = SimpleNamespace(binding=lambda *a: None)
        row = self.engine.qt_adapters.profiles["zhan10-coffin"]["maps"]["nightmare-01"]["floors"]["1"]
        row["evidence"]["binding"] = dict(variant="variant-01")
        self.assertFalse(self.metadata()["options"][1]["available"])

    def test_missing_bridge_and_missing_bitmap_have_explicit_reasons(self):
        del self.engine.qt_adapters.profiles["zhan10-coffin"]
        self.assertTrue(all(not r["available"] and "坐标" in r["reason"] for r in self.metadata()["options"]))
        (self.root / "variant-02.png").unlink()
        self.assertIn("未安装", self.metadata()["options"][1]["reason"])
        layer = LiveRuntime._display_layer(self.engine, "nightmare-01", "1", "zhan10-coffin", variant_id="variant-02")
        self.assertEqual(layer["displayKind"], "shared-structure")
        self.assertEqual(coffin_variant_metadata(self.engine, "difficult", "zhan10-coffin", "nightmare-01", "1")["options"], [])

    def test_selection_rechecks_context_and_preserves_unknown_preference(self):
        path = self.root / "ui-settings.json"
        path.write_text(json.dumps(dict(zhan10CoffinVariantId="future-version", future={"keep": True})), encoding="utf-8")
        service = self.service()
        metadata = self.metadata()
        runtime = SimpleNamespace(route_preferences_metadata=lambda: metadata,
                                  route_preferences_context=lambda: metadata["context"])
        service.refreshRoutePreferences(runtime)
        self.wait(lambda: service._route_job is None)
        self.assertEqual(service.values["zhan10CoffinVariantId"], "future-version")
        runtime.route_preferences_context = lambda: dict(metadata["context"], floor="2")
        self.assertFalse(service.selectCoffinVariant("variant-02"))
        runtime.route_preferences_context = lambda: metadata["context"]
        self.assertTrue(service.selectCoffinVariant("variant-02"))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["future"], {"keep": True})
        self.assertEqual(saved["zhan10CoffinVariantId"], "variant-02")

    def test_obsolete_metadata_failure_cannot_replace_latest_context(self):
        service = self.service()
        started, release = threading.Event(), threading.Event()
        def fail():
            started.set()
            release.wait(5)
            raise ValueError("obsolete-fixture-failure")
        context = self.metadata()["context"]
        old = SimpleNamespace(route_preferences_metadata=fail, route_preferences_context=lambda: context)
        metadata = dict(context=dict(context, floor="2"), options=[], message="latest fixture")
        latest = SimpleNamespace(route_preferences_metadata=lambda: metadata,
                                 route_preferences_context=lambda: metadata["context"])
        service.refreshRoutePreferences(old)
        self.assertTrue(started.wait(2))
        service.refreshRoutePreferences(latest)
        release.set()
        self.wait(lambda: service._route_job is None and service._route_pending is None)
        self.assertEqual(service.coffinVariantMessage, "latest fixture")

    def test_production_path_keeps_legacy_settings_and_uses_same_directory_atomic_write(self):
        production = self.root / "production"
        production.mkdir()
        legacy_path = production / "work/nightmare-memory/ui-settings.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(json.dumps(dict(future={"keep": True}, sheetOpacity=.4)), encoding="utf-8")
        with patch.object(client_paths, "PRODUCTION", True), patch.object(client_paths, "INSTALL_ROOT", production):
            service = SettingsService(production)
            self.addCleanup(service.shutdown)
            self.assertEqual(service.path, production / "work/nightmare-memory/ui-settings.json")
            self.assertEqual(service.values["sheetOpacity"], .4)
            service.setValue("sheetOpacity", .7)
            self.assertTrue(service.save())
            self.wait(lambda: not service.busy)
            self.assertEqual(json.loads(service.path.read_text(encoding="utf-8")), {"sheetOpacity": .7, "future": {"keep": True}})

    def test_reduce_motion_disables_existing_replay_height_animation(self):
        service = self.service()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("settingsService", service)
        engine.rootContext().setContextProperty("backend", dict(selectedTitle="fixture", selectedImage="", replayData={}))
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("Overlay.qml"))))
        if component.isLoading():
            self.wait(lambda: not component.isLoading())
        window = component.create()
        self.assertIsNotNone(window, str(component.errors()))
        try:
            behavior = window.findChild(QObject, "overlayHeightBehavior")
            self.assertTrue(behavior.property("enabled"))
            service.setValue("reduceMotion", True)
            self.app.processEvents()
            self.assertFalse(behavior.property("enabled"))
            window.setProperty("expanded", False)
            self.assertEqual(window.property("height"), 66)
            service.resetDraft()
            self.app.processEvents()
            self.assertTrue(behavior.property("enabled"))
            self.assertEqual(warnings, [])
        finally:
            window.deleteLater()
            self.app.processEvents()


class DisplayGenerationTests(unittest.TestCase):
    def test_unselected_map_metadata_matches_runtime_context(self):
        case = contracts.LiveRuntimeContracts()
        case.setUp()
        self.addCleanup(case.doCleanups)
        metadata = case.runtime.route_preferences_metadata()
        self.assertEqual(metadata["context"], case.runtime.route_preferences_context())
        self.assertEqual(metadata["options"], [])

    def test_committed_variant_change_revokes_pixels_and_preserves_identity_floor_and_pose(self):
        case = contracts.LiveRuntimeContracts()
        case.setUp()
        self.addCleanup(case.doCleanups)
        item = case.engine.pack.maps[0]
        item.id = "nightmare-01"
        case.engine.pack.by_id = {item.id: item}
        case.engine.rank = lambda *a, **k: [dict(mapId=item.id, identityHardGatePassed=True)]
        case.dispatch("set_route_profile", profileId="zhan10-coffin")
        case.begin_and_capture()
        before = case.runtime.snapshot()
        pose = case.runtime._core.pose_by_map[item.id].pose_for_floor("1").copy()
        case.runtime.configure_settings(dict(zhan10CoffinVariantId="variant-02"))
        after = case.runtime.snapshot()
        self.assertEqual((after["mapId"], after["floor"], after["roundId"]),
                         (before["mapId"], before["floor"], before["roundId"]))
        self.assertTrue(np.array_equal(case.runtime._core.pose_by_map[item.id].pose_for_floor("1"), pose))
        self.assertFalse(after["displayAuthorized"])
        self.assertIsNone(case.runtime.render_payload(before["render"]["token"]))

    def test_variant_change_during_pixel_preparation_rejects_old_result(self):
        case = contracts.LiveRuntimeContracts()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = LiveRuntime._display_layer
        def delayed(*args, **kwargs):
            layer = original(*args, **kwargs)
            case.runtime.configure_settings(dict(zhan10CoffinVariantId="variant-02"))
            return layer
        with patch.object(LiveRuntime, "_display_layer", side_effect=delayed):
            case.dispatch("start_round")
            result = case.dispatch("capture_now")
        self.assertFalse(result["success"])
        self.assertEqual(result["reason"], "stale-display-settings")
        self.assertFalse(case.runtime.snapshot()["displayAuthorized"])


if __name__ == "__main__":
    unittest.main()
