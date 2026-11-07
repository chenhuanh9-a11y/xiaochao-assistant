"""S1 data/axis seams using metadata and control fixtures, never game evidence."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]
from floor_contract import floor_contract, S0_FLOOR_NAMES
from floor_observation_policy import read_floor_evidence, decide_floor_transition, game_floor_control_hit
from season_context import SeasonContext, modes_for_season
from s1_native_resources import MODE_LAYOUTS, SOURCE_VERSION, S1NativeContext, registration
from client_providers import EngineFactories, ProviderUnavailable


def declarations(base, mode):
    """Only metadata is synthetic; no geometry/file readiness is asserted."""
    root = base / "seasons/s1" / mode
    (root / "references").mkdir(parents=True)
    interfaces = {name: "references/" + name + ".json" for name in
        ("identityPack", "typedIndex", "versionBindings", "coordinatePack", "neutralDisplay", "authorCatalog")}
    interfaces["identityEntries"] = "references/entries"
    maps = [dict(id=mid, sourceLayout=mid, sourceVersion=SOURCE_VERSION,
        identityProfileId="shared-structure", routeProfileId="shared-structure", name=mid,
        direction="unknown", shape="native", layers={str(f): f"references/{mid}-{f}.npz" for f in (-1, -2, -3, -4)})
        for mid in sorted(MODE_LAYOUTS[mode])]
    pack = dict(schemaVersion=1, season="s1", mode=mode, packId="synthetic-contract",
        floors=["-1", "-2", "-3", "-4"], defaultEntryFloor=-1, maps=maps)
    (root / interfaces["identityPack"]).write_text(json.dumps(pack), encoding="utf8")
    manifest = dict(schemaVersion=1, season="s1", mode=mode, sourceVersion=SOURCE_VERSION,
        runtimeInterfaces=interfaces, opencvVersion="intentionally-unavailable",
        capabilities=dict(sideEntries=False, floorControls=False))
    (root / "native-map-manifest.json").write_text(json.dumps(manifest), encoding="utf8")
    return root, pack, manifest


class S1FloorContractTests(unittest.TestCase):
    def test_modes_and_floor_ids_are_contextual_and_s0_exports_are_unchanged(self):
        self.assertNotIn("difficulty2", modes_for_season("s0"))
        self.assertNotIn("difficulty2", modes_for_season("s1"))
        self.assertEqual(S0_FLOOR_NAMES, {-1: "B1", 1: "1", 2: "2"})
        self.assertIsNone(floor_contract("difficulty2", season="s0"))
        for mode in modes_for_season("s1"):
            contract = floor_contract(mode, season="s1")
            self.assertEqual(contract.control_axis, "vertical")
            self.assertEqual(contract.floor_ids, ("-1", "-2", "-3", "-4"))
            self.assertEqual(contract.floor_id_for(-1), "-1")
            self.assertIsNone(contract.floor_id_for(True))

    def test_current_observation_policy_uses_the_declared_negative_identity(self):
        for mode in modes_for_season("s1"):
            contract = floor_contract(mode, season="s1")
            evidence = read_floor_evidence(dict(selectedFloor=-3), [-1], contract=contract)
            policy = decide_floor_transition(evidence, current_floor="-1", now=0, contract=contract)
            self.assertEqual(policy.target, "-3")
            self.assertTrue(policy.apply_target)
            unknown = read_floor_evidence(dict(selectedFloor=None), [-3], contract=contract)
            self.assertIsNone(unknown.stable)
            self.assertFalse(decide_floor_transition(unknown, current_floor="-1", now=0, contract=contract).apply_target)

    def test_vertical_hit_geometry_is_explicit_current_and_not_borrowed_from_s0(self):
        bounds = (100, 200, 2020, 1280)
        read = dict(layout="localized-floor-contract", season="s1", mode="nightmare", mapOpen=True,
            controlAxis="vertical", controlGeometryVerified=True,
            controlRects=[dict(floor=-(index+1), rect=[.8, .1+index*.1, .9, .19+index*.1]) for index in range(4)])
        for index in range(4):
            point = (100 + .85*1920, 200 + (.145+index*.1)*1080)
            self.assertEqual(game_floor_control_hit(point, bounds, read), -(index+1))
        self.assertIsNone(game_floor_control_hit((1732, 400), bounds, dict(read, controlGeometryVerified=False)))
        self.assertIsNone(game_floor_control_hit((1732, 400), bounds, dict(read, season="s0")))
        self.assertIsNone(game_floor_control_hit((1732, 400), bounds, dict(read, mapOpen=False)))

    def test_semantic_contract_does_not_enable_an_unvalidated_pixel_detector(self):
        import numpy as np
        from floor_control_region import read_floor_in_region, apply_floor_region
        frame = np.zeros((1080, 1920, 3), np.uint8)
        for mode in modes_for_season("s1"):
            result = read_floor_in_region(frame, [.8, .1, .1, .4], mode, season="s1")
            self.assertEqual(result["reason"], "vertical-floor-detector-unvalidated")
            stale = apply_floor_region(dict(gameSeason="s1"), mode, frame, dict(selectedFloor=2))
            self.assertIsNone(stale["selectedFloor"])


class S1RootContractTests(unittest.TestCase):
    def test_explicit_roots_are_immutable_and_cached_engines_keep_context_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, pack, _ = declarations(base, "difficult")
            roots = {"s1:difficult": root}
            factories = EngineFactories(base / "unused", context_roots=roots)
            roots["s1:difficult"] = base / "wrong"
            self.assertEqual(factories._resource_binding("difficult", "s1").root, root)
            with self.assertRaises(TypeError):
                factories.context_roots["s1:difficult"] = base
            engine = SimpleNamespace(pack=SimpleNamespace(maps=pack["maps"]), identity_floors=("-1", "-2", "-3", "-4"))
            with patch.object(factories, "_build", return_value=engine) as build:
                self.assertIs(factories.get_engine("difficult", "s1"), engine)
                self.assertIs(factories.get_engine("difficult", "s1"), engine)
                build.assert_called_once_with("difficult", season="s1")
            self.assertEqual(set(factories._engines), {"s1:difficult"})

    def test_missing_native_declaration_never_uses_the_s0_root_or_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            factories = EngineFactories(folder)
            factories._engines["s0:nightmare"] = object()
            with patch.object(factories, "_build", side_effect=AssertionError("wrong build")):
                with self.assertRaisesRegex(ProviderUnavailable, "season-resources-unavailable:s1"):
                    factories.get_engine("nightmare", "s1")
            self.assertEqual(set(factories._engines), {"s0:nightmare"})

    def test_wrong_map_mode_floor_or_path_is_not_a_registered_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            root, pack, manifest = declarations(base, "nightmare")
            path = root / manifest["runtimeInterfaces"]["identityPack"]
            original = path.read_text("utf8")
            for mutate in (lambda d: d.update(mode="difficult"), lambda d: d.update(floors=["B1", "1", "2"]),
                           lambda d: d["maps"][0].update(id="nightmare-01"),
                           lambda d: d["maps"][0]["layers"].update({"-1": "../s0.npz"})):
                value = json.loads(original)
                mutate(value)
                path.write_text(json.dumps(value), encoding="utf8")
                with self.assertRaises(ValueError):
                    registration(root, "nightmare")

    def test_opencv_version_mismatch_is_explicit_before_reading_typed_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _, _ = declarations(Path(folder), "difficulty2")
            with self.assertRaisesRegex(ValueError, "OpenCV runtime mismatch"):
                S1NativeContext(root, "difficulty2")


class S1CalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_applied_s1_rejects_stale_s0_frame_and_does_not_write_draft(self):
        from PySide6.QtCore import QObject, Signal
        from client_floor_region import FloorRegionService, FloorRegionImages
        class Settings(QObject):
            changed = Signal()
        settings = Settings()
        settings.values = dict(gameSeason="s0")
        settings.setValue = Mock(return_value=True)
        runtime = SimpleNamespace(snapshot=lambda: dict(season="s1", mode="nightmare"),
            latest_map_frame=Mock(side_effect=AssertionError("old S0 frame must not be consulted")))
        service = FloorRegionService(runtime, settings, FloorRegionImages())
        self.assertFalse(service.open())
        self.assertFalse(service.available)
        self.assertFalse(service.useDraft())
        settings.setValue.assert_not_called()

    def test_open_s0_draft_is_revoked_before_save_after_context_change(self):
        from PySide6.QtCore import QObject, Signal
        import numpy as np
        from client_floor_region import FloorRegionService, FloorRegionImages
        class Settings(QObject):
            changed = Signal()
        settings = Settings()
        settings.values = dict(gameSeason="s1")  # Saved target has not applied.
        settings.setValue = Mock(return_value=True)
        state = dict(season="s0", mode="nightmare")
        runtime = SimpleNamespace(snapshot=lambda: dict(state), latest_map_frame=lambda:
            (np.zeros((600, 1000, 3), np.uint8), "nightmare", dict(selectedFloor=None)))
        service = FloorRegionService(runtime, settings, FloorRegionImages())
        self.assertTrue(service.open())
        state["season"] = "s1"
        service.invalidateContext()
        self.assertFalse(service.available)
        self.assertFalse(service.useDraft())
        self.assertFalse(service.available)
        settings.setValue.assert_not_called()


class S1SettingsAndAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def wait(self, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.002)
        self.fail("settings worker timeout")

    def test_invalid_stored_s0_ordinary_pair_is_preserved_and_repairable(self):
        from client_paths import WORK_ROOT
        from client_settings import SettingsService
        WORK_ROOT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=WORK_ROOT) as folder:
            root = Path(folder)
            (root / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf8")
            path = root / "ui-settings.json"
            original = b'{"gameSeason":"s0","gameMode":"difficulty2","privateUnknown":"retained"}'
            path.write_bytes(original)
            service = SettingsService(root)
            try:
                self.assertEqual(service.persistedValues["gameMode"], "nightmare")
                self.assertIn("gameMode", service.message)
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(service.setValue("gameMode", "difficulty2"))  # Temporary draft pair.
                self.assertFalse(service.saveFields(["gameMode"]))
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(service.setValue("gameSeason", "s1"))  # Retired pair is not a repair target.
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(service.setValue("gameMode", "difficult"))
                self.assertTrue(service.setValue("gameSeason", "s1"))
                self.assertTrue(service.saveFields(["gameSeason", "gameMode"]))
                self.wait(lambda: not service.busy)
                saved = json.loads(path.read_text("utf8"))
                self.assertEqual((saved["gameSeason"], saved["gameMode"]), ("s1", "difficult"))
                self.assertEqual(saved["privateUnknown"], "retained")
                self.assertTrue(service.setValue("gameSeason", "s0"))
                self.assertTrue(service.saveFields(["gameSeason"]))
                self.wait(lambda: not service.busy)
                self.assertEqual(json.loads(path.read_text("utf8"))["gameSeason"], "s0")
            finally:
                service.shutdown()

    def test_qml_season_switch_uses_existing_default_when_ordinary_is_unavailable(self):
        from PySide6.QtCore import QUrl, QCoreApplication, QEvent
        from PySide6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
        from season_context import season_options
        engine = QQmlEngine()
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(ROOT / "experiments/qt_quick/SeasonSelector.qml")))
        item = component.create()
        self.assertIsNotNone(item, [str(error) for error in component.errors()])
        try:
            item.setProperty("seasons", season_options())
            item.setProperty("season", "s1")
            item.setProperty("mode", "difficulty2")
            requests = []
            item.contextRequested.connect(lambda season, mode: requests.append((season, mode)))
            expression = QQmlExpression(engine.rootContext(), item, "requestContext('s0', 'difficulty2')")
            expression.evaluate()
            self.assertFalse(expression.hasError())
            self.assertEqual(requests, [("s0", "nightmare")])
        finally:
            item.deleteLater()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

    def test_resource_invalidation_after_provider_return_cannot_refill_adapter_cache(self):
        from PIL import Image
        from client_engine import EngineAdapter
        class Engine:
            def __init__(self, label):
                self.label, self.calls = label, 0
                self.pack = SimpleNamespace(mode="nightmare", maps=[SimpleNamespace(id="fixture")])
                self.identity_floors = ("1",)
            def rank(self, *args, **kwargs):
                self.calls += 1
                return [dict(mapId="fixture", resource=self.label)]
        old, new = Engine("old"), Engine("new")
        class Registry(dict):
            def __init__(self):
                super().__init__(nightmare=lambda: old)
                self.calls, self.before_return = 0, None
            def capabilities(self, season="s0"):
                return dict(nightmare=dict(available=True), difficult=dict(available=False))
            def prepare(self, **kwargs):
                return self.capabilities()
            def invalidate(self):
                return None
            def get_engine(self, mode, season):
                self.calls += 1
                if self.calls == 1:
                    self.before_return()
                    return old
                return new
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "synthetic.png"
            Image.new("RGB", (32, 32), "gray").save(path)
            registry = Registry()
            adapter = EngineAdapter(engine_factories=registry, allowed_read_root=Path(folder))
            registry.before_return = adapter.invalidate_resources
            first = adapter.dispatch("rank_saved_frame", dict(path=str(path)))
            self.assertTrue(first["stale"], first)
            self.assertEqual(old.calls, 0)
            self.assertNotIn("s0:nightmare", adapter._engines)
            second = adapter.dispatch("rank_saved_frame", dict(path=str(path)))
            self.assertTrue(second["success"], second)
            self.assertEqual(second["candidates"][0]["resource"], "new")
            self.assertEqual((registry.calls, new.calls), (2, 1))

    def test_saved_readiness_does_not_advertise_live_commands_without_controls(self):
        from client_live_runtime import LiveRuntime
        class Registry(dict):
            def capabilities(self, season="s0"):
                return {mode: dict(available=True, prepared=True, reason="saved-frame-rank-ready",
                    liveReady=season == "s0", liveGaps=[] if season == "s0" else ["s1-floor-controls-unregistered"])
                    for mode in modes_for_season(season)}
            def prepare(self, **kwargs):
                return self.capabilities(kwargs.get("season", "s0"))
            def get_engine(self, mode, season):
                return SimpleNamespace(qt_live_ready=False, qt_live_gaps=("s1-floor-controls-unregistered",))
        capture = Mock()
        capture.snapshot = {}
        runtime = LiveRuntime(engine_factories=Registry(), capture_provider=capture)
        self.assertTrue(runtime.dispatch("set_context", dict(season="s1", mode="difficult"))["success"])
        state = runtime.snapshot()
        self.assertTrue(state["savedFrameCapabilities"]["difficult"]["available"])
        self.assertFalse(state["liveEngineAvailable"])
        self.assertEqual(state["liveUnavailableReason"], "s1-floor-controls-unregistered")
        self.assertIn("rank_saved_frame", state["availableCommands"])
        for command in ("start_round", "toggle_round", "map", "capture_now", "reselect_map"):
            self.assertNotIn(command, state["availableCommands"])
        result = runtime.dispatch("start_round")
        self.assertFalse(result["success"])
        self.assertIn("s1-floor-controls-unregistered", result["reason"])
        capture.capture.assert_not_called()
        capture.enable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
