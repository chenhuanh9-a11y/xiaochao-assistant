"""手动选图: pure picker state, Qt service, runtime gating and key routing."""
from __future__ import annotations

import copy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from test_s0_vertical_controls import synthetic_read
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem

from client_manual_map import (OPTION_KEYS, PickerState, manual_map_thumbnail_source,
                               normalize_selection_mode, option_title, picker_groups,
                               split_map_name)
from client_manual_map_service import ManualMapPickerService
from client_input import NativeShortcutBackend
from client_live_runtime import LiveRuntime, _AlignmentHost
from test_client_live_runtime import FixtureCapture, fixture_engine

APP = QGuiApplication.instance() or QGuiApplication([])


def item(map_id, name, direction="unknown"):
    return SimpleNamespace(id=map_id, name=name, direction=direction, layers={})


def write_png(directory, name, size=(64, 36), color=(40, 120, 220)):
    path = Path(directory) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def load_picker(service):
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("pickerService", service)
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
    component = QQmlComponent(engine, QUrl.fromLocalFile(
        str(Path(__file__).with_name("ClientMapPicker.qml"))))
    if component.isError():
        raise AssertionError("\n".join(str(error) for error in component.errors()))
    window = component.create()
    if window is None:
        raise AssertionError("picker component did not create a window")
    return engine, component, window, warnings


def qml_item(window, name):
    """Repeater delegates are visual children, not necessarily QObject children."""
    root = window.contentItem()
    if not isinstance(root, QQuickItem):
        return None
    pending = [root]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
    return None


def qml_source(item):
    source = item.property("source")
    return source.toString() if hasattr(source, "toString") else str(source or "")


class ThumbnailSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_nightmare_uses_entry_floor_then_1_2_b1_and_only_existing_layers(self):
        two = write_png(self.root, "two.png")
        one = write_png(self.root, "one.png")
        b1 = write_png(self.root, "b1.png")
        missing = self.root / "missing.png"
        item = SimpleNamespace(id="n1", layers={"1": missing, "2": two, "B1": b1})
        pack = SimpleNamespace(default_entry_floor="2")
        self.assertEqual(manual_map_thumbnail_source(pack, item, "nightmare"), two)
        item.layers.pop("2")
        item.layers["1"] = one
        self.assertEqual(manual_map_thumbnail_source(pack, item, "nightmare"), one)
        item.layers.pop("1")
        self.assertEqual(manual_map_thumbnail_source(pack, item, "nightmare"), b1)
        item.layers["B1"] = missing
        self.assertIsNone(manual_map_thumbnail_source(pack, item, "nightmare"))

    def test_difficult_uses_native_full_reference_or_manual_reference(self):
        native_image = write_png(self.root, "native.png")
        manual_image = write_png(self.root, "manual/n1.png")
        native = SimpleNamespace(full_reference=lambda map_id: native_image)
        item = SimpleNamespace(id="n1")
        pack = SimpleNamespace(source_root=self.root, metadata={})
        self.assertEqual(manual_map_thumbnail_source(pack, item, "difficult", native), native_image)
        pack.metadata = {"n1": {"manualReference": "manual/n1.png"}}
        self.assertEqual(manual_map_thumbnail_source(pack, item, "difficult"), manual_image)
        pack.metadata = {"n1": {"manualReference": "manual/missing.png"}}
        self.assertIsNone(manual_map_thumbnail_source(pack, item, "difficult"))
        self.assertIsNone(manual_map_thumbnail_source(pack, item, "difficult",
            SimpleNamespace(full_reference=lambda map_id: self.root / "missing.png")))


NIGHTMARE = [item("a", "右中 / 中十"), item("b", "左中 / 红房间 / 雷同图"), item("c", "左中右 / 右S"),
             item("d", "噩梦 104026"), item("e", "左右 / 右┛"), item("f", "右中 / 中一"),
             item("g", "左中右 / 右┛"), item("h", "左中右 / 右└┐"), item("i", "左中右 / 右 I / 中└┐"),
             item("j", "左中右 / 右S / 雷同图")]


class GroupingTests(unittest.TestCase):
    def test_names_split_into_group_and_route(self):
        self.assertEqual(split_map_name("左中右 / 右S / 雷同图"), ("左中右", "右S / 雷同图"))
        self.assertEqual(split_map_name("噩梦 104026"), ("", "噩梦 104026"))
        self.assertEqual(option_title(NIGHTMARE[2], "左中右"), "右S")
        self.assertEqual(option_title(NIGHTMARE[3], "其他"), "噩梦 104026")

    def test_nightmare_groups_follow_the_old_picker_order_with_other_last(self):
        groups = picker_groups(NIGHTMARE, "nightmare")
        self.assertEqual([label for label, _items in groups], ["左中右", "左中", "左右", "右中", "其他"])
        self.assertEqual([m.id for m in dict(groups)["左中右"]], ["c", "g", "h", "i", "j"])

    def test_difficult_maps_without_a_name_group_use_their_direction(self):
        maps = [item("n1", "北-红门", "上"), item("s1", "南-L门", "下"), item("n2", "北-T门", "上")]
        groups = picker_groups(maps, "difficult")
        self.assertEqual([(label, [m.id for m in items]) for label, items in groups],
                         [("上", ["n1", "n2"]), ("下", ["s1"])])

    def test_selection_mode_defaults_to_automatic(self):
        self.assertEqual(normalize_selection_mode("manual"), "manual")
        self.assertEqual(normalize_selection_mode(None), "auto")
        self.assertEqual(normalize_selection_mode("Manual"), "auto")


class PickerStateTests(unittest.TestCase):
    def test_group_then_map_with_pages_and_back(self):
        state = PickerState(picker_groups(NIGHTMARE, "nightmare"))
        self.assertEqual(state.stage, "groups")
        self.assertIsNone(state.choose(4))
        self.assertTrue(state.turn(1))
        self.assertEqual(state.choose(0), ("group", "其他"))
        self.assertTrue(state.back())
        self.assertEqual((state.stage, state.page), ("groups", 1))
        state.turn(-1)
        self.assertEqual(state.choose(0), ("group", "左中右"))
        self.assertEqual(state.page_count(), 2)
        self.assertEqual([m.id for m in state.options()], ["c", "g", "h", "i"])
        self.assertTrue(state.turn(1))
        self.assertFalse(state.turn(1))
        self.assertEqual(state.choose(0), ("map", NIGHTMARE[-1]))

    def test_single_group_starts_on_maps_and_cannot_go_back(self):
        state = PickerState(picker_groups([item("x", "地图 X"), item("y", "地图 Y")], "difficult"))
        self.assertEqual(state.stage, "maps")
        self.assertFalse(state.back())
        self.assertEqual(state.choose(1)[1].id, "y")


class FakeSettings(QObject):
    changed = Signal()
    saved = Signal()

    def __init__(self):
        super().__init__()
        self.values = {"mapSelectionMode": "manual", "gameMode": "nightmare"}
        self.persistedValues = dict(self.values)


class FakeWindows:
    def __init__(self):
        self.calls = []
        self._windows = {}

    def show(self, name):
        self.calls.append(("show", name))

    def hide(self, name):
        self.calls.append(("hide", name))


class FakePlacementWindow:
    def __init__(self, width=620, height=250):
        self._width, self._height = width, height
        self._x = self._y = 0
        self._screen = None

    def width(self): return self._width
    def height(self): return self._height
    def x(self): return self._x
    def y(self): return self._y
    def setX(self, value): self._x = value
    def setY(self, value): self._y = value
    def screen(self): return self._screen
    def setScreen(self, screen): self._screen = screen


class FakeInput(QObject):
    pickerKeyRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.keys = []

    def setPickerKeys(self, keys):
        self.keys = list(keys)


class FakeSession(QObject):
    changed = Signal()

    def __init__(self, mode="nightmare"):
        super().__init__()
        self.snapshot = dict(roundActive=True)
        self.currentMode = mode


def catalog(mode, rows):
    return dict(mode=mode, profileId="fixture", defaultEntryFloor="1",
                identity=dict(mode=mode, maps=[(row["id"], row["name"]) for row in rows]),
                maps=list(rows))


NIGHTMARE_ROWS = [dict(id=m.id, name=m.name, direction=m.direction) for m in NIGHTMARE]


class PickerServiceTests(unittest.TestCase):
    def setUp(self):
        self.settings = FakeSettings()
        self.windows = FakeWindows()
        self.input = FakeInput()
        self.session = FakeSession()
        self.commits = []
        self.catalogs = []

    def service(self, provider, geometry_provider=None):
        return ManualMapPickerService(settings=self.settings, windows=self.windows,
            input_backend=self.input, session=self.session, catalog_provider=provider,
            commit=lambda map_id: self.commits.append(map_id) or True,
            geometry_provider=geometry_provider)

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            APP.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "picker catalog timeout")

    def test_groups_paging_back_and_commit_release_keys(self):
        service = self.service(lambda: catalog("nightmare", NIGHTMARE_ROWS))
        self.assertTrue(service.open("test"))
        self.assertTrue(service.visible)
        self.assertEqual(self.input.keys, list(OPTION_KEYS + ("PageUp", "PageDown", "Backspace", "Escape")))
        self.wait(lambda: not service.busy and len(service.options) == 4)
        self.assertEqual([row["label"] for row in service.options], ["左中右", "左中", "左右", "右中"])
        self.assertTrue(service.handleKey("F1"))
        self.assertEqual(service.pageLabel, "1 / 2")
        self.assertEqual([row["label"] for row in service.options], ["右S", "右┛", "右└┐", "右 I / 中└┐"])
        self.assertTrue(service.handleKey("PageDown"))
        self.assertEqual([row["label"] for row in service.options], ["右S / 雷同图"])
        self.assertTrue(service.handleKey("Backspace"))
        self.assertEqual(service.options[0]["kind"], "group")
        self.assertTrue(service.handleKey("F1"))
        self.assertTrue(service.handleKey("PageDown"))
        self.assertTrue(service.handleKey("F1"))
        self.assertEqual(self.commits, ["j"])
        self.assertFalse(service.visible)
        self.assertEqual(self.input.keys, [])

    def test_single_group_goes_straight_to_maps_and_escape_closes(self):
        self.session.currentMode = "difficult"
        service = self.service(lambda: catalog("difficult", [
            dict(id="n1", name="北-红门", direction="上"),
            dict(id="n2", name="北-T门", direction="上")]))
        service.open("test")
        self.wait(lambda: not service.busy and service.options)
        self.assertEqual(service.options[0]["kind"], "map")
        self.assertFalse(service.handleKey("Backspace"))
        self.assertTrue(service.handleKey("Escape"))
        self.assertFalse(service.visible)
        self.assertEqual(self.input.keys, [])

    def test_end_round_and_mode_switch_discard_old_catalog(self):
        service = self.service(lambda: catalog(self.session.currentMode,
            NIGHTMARE_ROWS if self.session.currentMode == "nightmare" else
            [dict(id="n1", name="北-红门", direction="上")]))
        service.open("test")
        self.wait(lambda: not service.busy and service.options)
        self.session.snapshot["roundActive"] = False
        service._session_changed()
        self.assertFalse(service.visible)
        self.assertEqual(self.input.keys, [])

        self.session.snapshot["roundActive"] = True
        service.open("test")
        self.wait(lambda: not service.busy and service.options)
        late_generation = service._generation
        self.session.currentMode = "difficult"
        service._session_changed()
        self.wait(lambda: not service.busy and service._catalog_mode == "difficult" and service.options)
        self.assertEqual(service.options[0]["label"], "北-红门")
        # A result from the superseded nightmare request cannot replace it.
        service._catalog_finished(late_generation, catalog("nightmare", NIGHTMARE_ROWS), None)
        self.assertEqual(service._catalog_mode, "difficult")
        self.assertEqual(service.options[0]["label"], "北-红门")

    def test_switching_to_auto_closes_an_open_picker(self):
        service = self.service(lambda: catalog("nightmare", NIGHTMARE_ROWS))
        service.open("test")
        self.wait(lambda: not service.busy and service.options)
        self.settings.values["mapSelectionMode"] = "auto"
        service._settings_changed()
        self.assertFalse(service.visible)

    def test_catalog_not_ready_is_shown_as_a_message_not_options(self):
        service = self.service(lambda: dict(
            available=False, message="当前模式的地图资源未就绪，暂时不能选图"))
        self.assertTrue(service.open("test"))
        self.wait(lambda: not service.busy)
        self.assertEqual(service.options, [])
        self.assertEqual(service.message, "当前模式的地图资源未就绪，暂时不能选图")
        self.assertIn("地图资源未就绪", service.description)

    def test_picker_centers_over_the_bound_client_rectangle(self):
        area = APP.primaryScreen().availableGeometry()
        rect = dict(x=area.x()+10, y=area.y()+10,
                    width=area.width()-20, height=area.height()-20)
        window = FakePlacementWindow()
        self.windows._windows["map-picker"] = window
        service = self.service(lambda: catalog("nightmare", NIGHTMARE_ROWS),
                               geometry_provider=lambda: rect)
        service.open("test")
        self.wait(lambda: not service.busy)
        for _ in range(4):
            APP.processEvents()
        expected_x = round(rect["x"] + (rect["width"] - window.width()) / 2)
        expected_y = round(rect["y"] + (rect["height"] - window.height()) / 2)
        expected_x = max(area.x(), min(expected_x, area.x() + area.width() - window.width()))
        expected_y = max(area.y(), min(expected_y, area.y() + area.height() - window.height()))
        self.assertEqual((window.x(), window.y()), (expected_x, expected_y))
        self.assertIs(window.screen(), APP.primaryScreen())

    def test_missing_thumbnail_file_keeps_the_map_card_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.png"
            self.session.currentMode = "difficult"
            service = self.service(lambda: catalog("difficult", [dict(
                id="n1", name="北-红门", direction="上",
                thumbnailPath=str(missing), thumbnailStamp="")]))
            service.open("test")
            self.wait(lambda: not service.busy and len(service.options) == 1)
            self.assertEqual(service.options[0]["thumbnailUrl"], "")
            self.assertTrue(service.choose(0))
            self.assertEqual(self.commits, ["n1"])

    def test_stale_generation_and_mode_switch_discard_old_thumbnails(self):
        with tempfile.TemporaryDirectory() as directory:
            old_image = write_png(directory, "old.png")
            new_image = write_png(directory, "new.png")
            state = {"catalog": catalog("nightmare", [dict(
                id="old", name="旧地图", direction="上",
                thumbnailPath=str(old_image), thumbnailStamp="old-stamp")])}
            service = self.service(lambda: state["catalog"])
            service.open("test")
            self.wait(lambda: not service.busy and service.options)
            old_result, old_generation = state["catalog"], service._generation
            old_url = service.options[0]["thumbnailUrl"]
            self.assertIn("old.png", old_url)

            state["catalog"] = catalog("difficult", [dict(
                id="new", name="新地图", direction="上",
                thumbnailPath=str(new_image), thumbnailStamp="new-stamp")])
            self.session.currentMode = "difficult"
            service._session_changed()
            self.assertNotIn(old_url, [row.get("thumbnailUrl") for row in service.options])
            self.wait(lambda: not service.busy and service._catalog_mode == "difficult"
                      and service.options)
            new_url = service.options[0]["thumbnailUrl"]
            self.assertIn("new.png", new_url)
            # A result from the superseded generation cannot revive old paths.
            service._catalog_finished(old_generation, old_result, None)
            self.assertEqual(service.options[0]["thumbnailUrl"], new_url)
            self.assertEqual(service.options[0]["mapId"], "new")

    def test_qml_picker_renders_thumbnail_only_when_the_image_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_png(directory, "valid.png")
            missing = Path(directory) / "missing.png"
            self.session.currentMode = "difficult"
            state = {"rows": [dict(id="n1", name="北-红门", direction="上",
                                   thumbnailPath=str(image), thumbnailStamp="valid")]}
            service = self.service(lambda: catalog("difficult", state["rows"]))
            engine, component, window, warnings = load_picker(service)
            self.addCleanup(lambda: (engine.deleteLater(), component.deleteLater()))
            self.addCleanup(window.deleteLater)
            service.open("test")
            self.wait(lambda: not service.busy and len(service.options) == 1
                      and qml_item(window, "pickerThumbnail0") is not None)
            thumbnail = qml_item(window, "pickerThumbnail0")
            self.assertIsNotNone(qml_item(window, "pickerOption0"))
            self.assertTrue(qml_source(thumbnail))
            self.assertTrue(thumbnail.property("visible"))
            self.assertTrue(thumbnail.property("asynchronous"))
            self.wait(lambda: thumbnail.property("paintedWidth") > 0)  # Decoded.
            self.assertEqual(warnings, [])

            service.close()
            state["rows"] = [dict(id="n1", name="北-红门", direction="上",
                                  thumbnailPath=str(missing), thumbnailStamp="")]
            service.open("test")
            self.wait(lambda: not service.busy and len(service.options) == 1
                      and qml_item(window, "pickerThumbnail0") is not None)
            thumbnail = qml_item(window, "pickerThumbnail0")
            self.assertEqual(qml_source(thumbnail), "")
            self.assertFalse(thumbnail.property("visible"))
            self.assertTrue(service.choose(0))
            self.assertEqual(self.commits, ["n1"])
            self.assertEqual(warnings, [])

    def test_qml_picker_loads_and_renders_the_service_options(self):
        service = self.service(lambda: catalog("nightmare", NIGHTMARE_ROWS))
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("pickerService", service)
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        component = QQmlComponent(engine, QUrl.fromLocalFile(
            str(Path(__file__).with_name("ClientMapPicker.qml"))))
        self.assertFalse(component.isError(), "\n".join(str(error) for error in component.errors()))
        window = component.create()
        self.assertIsNotNone(window)
        service.open("test")
        self.wait(lambda: not service.busy and window.property("title") == "手动选图 · 噩梦")
        self.assertEqual(warnings, [])
        window.deleteLater()
        APP.processEvents()


class Native:
    def __init__(self):
        self.foreground = 42
        self.states = {}

    def is_window(self, hwnd): return hwnd == 42
    def foreground_window(self): return self.foreground
    def window_process_id(self, hwnd): return 9001
    def key_state(self, vk):
        result = self.states.get(vk, 0)
        self.states[vk] = result & ~1
        return result


class PickerKeyRoutingTests(unittest.TestCase):
    def setUp(self):
        self.native = Native()
        self.input = NativeShortcutBackend(self.native)
        self.actions, self.picker = [], []
        self.input.actionTriggered.connect(self.actions.append)
        self.input.pickerKeyRequested.connect(self.picker.append)
        self.assertTrue(self.input.configure({"map": "G", "candidate-next": "F1",
                                             "candidate-prev": "F2", "manual-map-picker": "Alt+1"}))
        self.input.bindWindow(42)
        self.assertTrue(self.input.enable())
        self.input.pollOnce()

    def tearDown(self):
        self.input.shutdown()

    def test_open_picker_owns_bare_keys_and_suppresses_same_primary_actions(self):
        self.input.setPickerKeys(["F1", "F2", "PageUp", "PageDown", "Backspace", "Escape"])
        self.input.pollOnce()  # Establish held state after opening.
        self.native.states[0x70] = 0x8001
        self.assertEqual(self.input.pollOnce(), [])
        self.assertEqual(self.picker, ["F1"])
        self.native.states[0x70] = 0
        self.input.pollOnce()
        self.native.states[0x22] = 0x8001
        self.assertEqual(self.input.pollOnce(), [])
        self.assertEqual(self.picker, ["F1", "PageDown"])
        # Escape keeps its fixed map-close edge and also reaches the picker.
        self.native.states[0x22] = 0
        self.input.pollOnce()
        self.native.states[0x1b] = 0x8001
        self.assertEqual(self.input.pollOnce(), ["escape"])
        self.assertEqual(self.picker, ["F1", "PageDown", "Escape"])

    def test_closing_picker_restores_action_hotkeys_and_keys_are_not_routed(self):
        self.input.setPickerKeys(["F1"])
        self.input.pollOnce()
        self.native.states[0x70] = 0x8001
        self.input.pollOnce()
        self.native.states[0x70] = 0
        self.input.pollOnce()
        self.input.setPickerKeys([])
        self.input.pollOnce()
        self.native.states[0x70] = 0x8001
        self.assertEqual(self.input.pollOnce(), ["candidate-next"])
        self.assertEqual(self.picker, ["F1"])

    def test_manual_picker_hotkey_is_not_a_picker_key(self):
        self.input.setPickerKeys(["F1"])
        self.input.pollOnce()
        self.native.states[0x12] = 0x8000
        self.native.states[0x31] = 0x8001
        self.assertEqual(self.input.pollOnce(), ["manual-map-picker"])
        self.assertEqual(self.picker, [])


class RuntimeManualModeTests(unittest.TestCase):
    def setUp(self):
        self.capture = FixtureCapture()
        self.engine = fixture_engine()
        self.engine.pack.default_entry_floor = "2"
        self.runtime = LiveRuntime(engine_factories={"nightmare": lambda: self.engine,
            "difficult": lambda: fixture_engine("difficult")}, capture_provider=self.capture,
            evidence_kind="synthetic", clock=lambda: 1)
        self.fit = dict(success=True, matrixSourceToClient=[[.5, 0, 40], [0, .5, 60], [0, 0, 1]],
            selectedRouteCurrentFrameResidual=dict(verified=True, poseVerifiedAgainstCanonicalGeometry=True,
                profileRenderDerivedFromCanonical=True, coordinatePackId="fixture-pack",
                validationBasis="shared-canonical-geometry-current-frame"))
        import numpy as np
        self.fit["matrixSourceToClient"] = np.array(self.fit["matrixSourceToClient"])
        self.patches = [patch("map_interface_observation.detect_map_interface", return_value=dict(detected=True)),
                        patch("floor_control_region.locate_vertical_floor_controls",
                              side_effect=lambda frame, mode, **kw: synthetic_read(mode)),
                        patch.object(_AlignmentHost, "fit", side_effect=lambda *a: copy.deepcopy(self.fit))]
        for entry in self.patches:
            entry.start(); self.addCleanup(entry.stop)

    def dispatch(self, action, **payload):
        state = self.runtime.snapshot()
        context = {key: state[key] for key in ("mode", "profileId", "roundId", "frameId")}
        context.update(generation=22, **payload)
        return self.runtime.dispatch(action, context)

    def test_manual_mode_skips_ranking_until_a_pick_confirms_the_same_route(self):
        self.runtime.configure_settings({"mapSelectionMode": "manual"})
        self.assertTrue(self.dispatch("start_round")["success"])
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reason"], "manual-map-selection-pending")
        self.assertEqual(self.engine.rank_count, 0)
        state = self.runtime.snapshot()
        self.assertTrue(state["manualSelectionPending"])
        self.assertTrue(state["manualMapPickAvailable"])
        self.assertEqual((state["mapOpen"], state["mapId"], state["floor"]), (True, "", "1"))
        self.assertIn("reselect_map", state["availableCommands"])
        # confirm_route stays the existing candidate-button command; the picker
        # uses only the separate availability flag.
        self.assertNotIn("confirm_route", state["availableCommands"])
        result = self.dispatch("confirm_route", mapId="fixture-map")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.engine.rank_count, 0)
        state = self.runtime.snapshot()
        self.assertEqual(state["mapId"], "fixture-map")
        self.assertFalse(state["manualSelectionPending"])
        self.assertEqual(state["capture"]["enabled"], True)

    def test_auto_mode_pick_commits_with_map_closed_without_advertising_confirm(self):
        self.runtime.configure_settings({"mapSelectionMode": "auto"})
        self.assertTrue(self.dispatch("start_round")["success"])
        # A round can be active before any map frame exists (e.g. the map key
        # was pressed but the map was not observed yet).
        self.assertTrue(self.runtime._ensure_round_active({}, None)["success"])
        state = self.runtime.snapshot()
        self.assertFalse(state["mapOpen"])
        self.assertTrue(state["manualMapPickAvailable"])
        self.assertNotIn("confirm_route", state["availableCommands"])
        result = self.dispatch("confirm_route", mapId="fixture-map")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reason"], "identity-confirmed-pose-pending")
        state = self.runtime.snapshot()
        self.assertEqual((state["mapId"], state["floor"]), ("fixture-map", "2"))
        self.assertFalse(state["manualSelectionPending"])

    def test_manual_mode_pick_also_commits_with_map_closed(self):
        self.runtime.configure_settings({"mapSelectionMode": "manual"})
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertTrue(self.runtime._ensure_round_active({}, None)["success"])
        state = self.runtime.snapshot()
        self.assertFalse(state["mapOpen"])
        self.assertTrue(state["manualSelectionPending"])
        self.assertTrue(state["manualMapPickAvailable"])
        result = self.dispatch("confirm_route", mapId="fixture-map")
        self.assertTrue(result["success"], result)
        state = self.runtime.snapshot()
        self.assertEqual((state["mapId"], state["floor"]), ("fixture-map", "2"))
        self.assertFalse(state["manualSelectionPending"])

    def test_mid_round_switch_skips_then_resumes_ranking(self):
        self.runtime.configure_settings({"mapSelectionMode": "auto"})
        self.assertTrue(self.dispatch("start_round")["success"])
        self.runtime.configure_settings({"mapSelectionMode": "manual"})
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["reason"], "manual-map-selection-pending")
        self.assertEqual(self.engine.rank_count, 0)
        state = self.runtime.snapshot()
        self.assertEqual(state["mapSelectionMode"], "manual")
        self.assertTrue(state["manualMapPickAvailable"])
        self.runtime.configure_settings({"mapSelectionMode": "auto"})
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertGreater(self.engine.rank_count, 0)
        self.assertEqual(self.runtime.snapshot()["mapSelectionMode"], "auto")

    def test_catalog_without_prepared_engine_is_not_ready_and_builds_no_factory(self):
        calls = []
        runtime = LiveRuntime(engine_factories={
            "nightmare": lambda: calls.append("nightmare") or fixture_engine(),
            "difficult": lambda: calls.append("difficult") or fixture_engine("difficult")},
            capture_provider=FixtureCapture(), evidence_kind="synthetic", clock=lambda: 1)
        result = runtime.manual_map_catalog()
        self.assertFalse(result["available"])
        self.assertEqual(result["message"], "当前模式的地图资源未就绪，暂时不能选图")
        self.assertEqual(calls, [])

    def test_auto_mode_still_requests_identity_ranking(self):
        self.runtime.configure_settings({"mapSelectionMode": "auto"})
        self.assertTrue(self.dispatch("start_round")["success"])
        result = self.dispatch("capture_now")
        self.assertTrue(result["success"], result)
        self.assertGreater(self.engine.rank_count, 0)

    def test_catalog_is_read_only_and_reports_the_current_mode(self):
        self.runtime.configure_settings({"mapSelectionMode": "manual"})
        self.assertTrue(self.dispatch("start_round")["success"])
        catalog = self.runtime.manual_map_catalog()
        self.assertEqual(catalog["mode"], "nightmare")
        self.assertEqual([row["id"] for row in catalog["maps"]], ["fixture-map"])
        self.assertEqual(catalog["identity"]["mode"], "nightmare")

    def test_catalog_resolves_thumbnail_paths_from_the_prepared_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            image = write_png(directory, "layer.png")
            self.engine.pack.default_entry_floor = "1"
            map_item = self.engine.pack.maps[0]
            map_item.direction = "上"
            map_item.layers = {"1": image}
            self.runtime.configure_settings({"mapSelectionMode": "manual"})
            self.assertTrue(self.dispatch("start_round")["success"])
            catalog = self.runtime.manual_map_catalog()
            row = catalog["maps"][0]
            self.assertEqual(row["thumbnailPath"], str(image))
            self.assertTrue(row["thumbnailStamp"])
            # A different pack must not reuse the previous pack's thumbnails.
            replacement = fixture_engine()
            replacement.pack.default_entry_floor = "1"
            replacement.pack.maps = [SimpleNamespace(id="other-map", name="换图", direction="上",
                                                    layers={"1": write_png(directory, "other.png")})]
            replacement.pack.by_id = {map_item.id: map_item for map_item in replacement.pack.maps}
            self.runtime._engine = replacement
            other = self.runtime.manual_map_catalog()["maps"][0]
            self.assertEqual(other["id"], "other-map")
            self.assertIn("other.png", other["thumbnailPath"])


if __name__ == "__main__":
    unittest.main()


class PickerFocusAndModifierTests(unittest.TestCase):
    """Picker keys are only consumed for the game/assistant and bare presses."""

    def test_poll_path_ignores_other_foreground_apps_and_modifier_chords(self):
        native = Native()
        keys = NativeShortcutBackend(native)
        self.addCleanup(keys.shutdown)
        picked, actions = [], []
        keys.pickerKeyRequested.connect(picked.append)
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.bindWindow(42)
        self.assertTrue(keys.enable())
        keys.pollOnce()
        keys.setPickerKeys(["F1"])
        keys.pollOnce()
        native.foreground = 77  # Another application owns the keyboard.
        native.window_process_id = lambda hwnd: 1234
        native.states[0x70] = 0x8001
        keys.pollOnce()
        self.assertEqual(picked, [])
        native.states[0x70] = 0
        keys.pollOnce()
        native.foreground = 42
        native.window_process_id = lambda hwnd: 9001
        keys.pollOnce()
        native.states[0x12] = 0x8000  # Alt+F1 is not a picker choice.
        native.states[0x70] = 0x8001
        keys.pollOnce()
        self.assertEqual(picked, [])
        native.states[0x12] = 0
        native.states[0x70] = 0
        keys.pollOnce()
        native.states[0x70] = 0x8001
        keys.pollOnce()
        self.assertEqual(picked, ["F1"])

    def test_hook_path_consumes_only_bare_keys_for_game_or_assistant(self):
        from unittest.mock import Mock, patch
        from client_native_keys import PassThroughKeyboardSource
        from test_client_input import Native as HookNative
        native, source = HookNative(), PassThroughKeyboardSource()
        source.start, source.stop = Mock(return_value=True), Mock(return_value=True)
        keys = NativeShortcutBackend(native, source=source)
        self.addCleanup(keys.shutdown)
        picked, actions = [], []
        keys.pickerKeyRequested.connect(picked.append)
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.enableManagement(); keys.bindWindow(42); keys.enable()
        keys.setPickerKeys(["F1", "F2"])

        def press(vk, down, foreground=42):
            native.foreground = foreground  # Match the OS consumption-time focus.
            with patch("time.monotonic", return_value=99.9):
                event = source._transition_event(vk, 0, 0, down, foreground)
            source.eventReceived.emit(event)
            with patch("time.monotonic", return_value=100.):
                APP.processEvents()

        native.process = 1234  # Foreground is not this assistant process.
        press(0x70, True, foreground=77)
        press(0x70, False, foreground=77)
        self.assertEqual(picked, [])
        press(0xA4, True)  # Left Alt held: Alt+F2 belongs to someone else.
        press(0x71, True)
        press(0x71, False)
        press(0xA4, False)
        self.assertEqual(picked, [])
        press(0x70, True)  # Bare F1 in the game goes to the picker only.
        press(0x70, False)
        self.assertEqual(picked, ["F1"])
        self.assertEqual(actions, [])
        native.process = os.getpid()  # The assistant itself is foreground.
        press(0x71, True, foreground=55)
        press(0x71, False, foreground=55)
        self.assertEqual(picked, ["F1", "F2"])

    def test_hook_path_picker_closing_synchronously_does_not_duplicate_the_action(self):
        from test_client_input import Native as HookNative
        from test_client_shortcut_delivery import FixtureSource
        native = HookNative()
        # A started hook must report running/installed, otherwise #80 keeps the
        # game actions disabled even though enable() was accepted.
        source = FixtureSource()
        keys = NativeShortcutBackend(native, source=source)
        self.addCleanup(keys.shutdown)
        picked, actions = [], []

        def select(name):
            picked.append(name)
            # ManualMapPickerService closes the picker inside this signal when
            # a pick commits, which clears the picker key set synchronously.
            keys.setPickerKeys([])

        keys.pickerKeyRequested.connect(select, Qt.DirectConnection)
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.enableManagement(); keys.bindWindow(42); keys.enable()
        keys.setPickerKeys(["F1", "F2"])

        def press(vk, down, foreground=42):
            native.foreground = foreground  # Match the OS consumption-time focus.
            with patch("time.monotonic", return_value=99.9):
                event = source._transition_event(vk, 0, 0, down, foreground)
            source.eventReceived.emit(event)
            with patch("time.monotonic", return_value=100.):
                APP.processEvents()

        press(0x70, True)
        press(0x70, False)
        self.assertEqual(picked, ["F1"])
        self.assertEqual(actions, [])
        self.assertEqual(keys._picker_keys, {})
        # Once the picker is closed, bare F1 belongs to candidate-next again.
        press(0x70, True)
        press(0x70, False)
        self.assertEqual(picked, ["F1"])
        self.assertEqual(actions, ["candidate-next"])

    def test_poll_path_picker_closing_synchronously_does_not_duplicate_the_action(self):
        native = Native()
        keys = NativeShortcutBackend(native)
        self.addCleanup(keys.shutdown)
        picked, actions = [], []

        def select(name):
            picked.append(name)
            keys.setPickerKeys([])

        keys.pickerKeyRequested.connect(select, Qt.DirectConnection)
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1"}))
        keys.bindWindow(42)
        self.assertTrue(keys.enable())
        keys.pollOnce()
        keys.setPickerKeys(["F1", "F2"])
        keys.pollOnce()
        native.states[0x70] = 0x8001
        self.assertEqual(keys.pollOnce(), [])
        self.assertEqual(picked, ["F1"])
        self.assertEqual(actions, [])
        self.assertEqual(keys._picker_keys, {})
        # A closed picker no longer consumes the next bare F1.
        native.states[0x70] = 0
        keys.pollOnce()
        native.states[0x70] = 0x8001
        self.assertEqual(keys.pollOnce(), ["candidate-next"])
        self.assertEqual(picked, ["F1"])
        self.assertEqual(actions, ["candidate-next"])


class PickerShutdownFlowTests(unittest.TestCase):
    def test_service_shutdown_revokes_picker_and_rejects_reopen(self):
        settings, session = FakeSettings(), FakeSession()
        windows, native = FakeWindows(), FakeInput()
        service = ManualMapPickerService(
            settings=settings, windows=windows, input_backend=native, session=session,
            catalog_provider=lambda: dict(mode="nightmare", maps=[], identity={}), commit=lambda _id: True)
        service.open("test")
        service.shutdown()
        self.assertFalse(service.visible)
        self.assertFalse(service.open("after-shutdown"))
