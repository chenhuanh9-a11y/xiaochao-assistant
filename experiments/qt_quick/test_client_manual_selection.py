"""Pure picker and offscreen Qt flow; no native input, capture or user settings."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem
from client_manual_selection import ManualSelectionService
from manual_selection_model import (PickerState, manual_identity_skipped, manual_selection_scope,
    manual_selection_is_current, normalize_selection_mode, picker_groups, plan_manual_selection)

APP = QGuiApplication.instance() or QGuiApplication([])


def pack():
    items = [SimpleNamespace(id=f"map-{i}", name=f"左中右 / 地图{i}", layers={}) for i in range(6)]
    items += [SimpleNamespace(id="other", name="独立地图", layers={})]
    return SimpleNamespace(mode="nightmare", maps=items, by_id={item.id: item for item in items},
                           floors=("1", "2", "B1"), default_entry_floor="2")


def state():
    return dict(mode="nightmare", cycleId=3, floor="1", roundActive=True,
        resourceIdentity="pack-1", manualSelectionGeneration=4, mapSelectionMode="manual",
        availableCommands=["set_map_selection_mode", "select_manual_map"])


class FakeSession(QObject):
    changed = Signal()
    def __init__(self):
        super().__init__()
        self.state = state()
        self.calls = []
    snapshot = Property("QVariantMap", lambda self: deepcopy(self.state), notify=changed)
    @Slot(str, "QVariantMap")
    def dispatchWithPayload(self, action, payload):
        self.calls.append((action, dict(payload)))


class PickerModelTests(unittest.TestCase):
    def test_category_order_four_per_page_and_back_restores_group_page(self):
        items = [dict(id=str(n), name=f"组{n} / 图") for n in range(6)]
        items += [dict(id="last", name="独立图"), dict(id="first", name="左中右 / 图")]
        groups = picker_groups(items)
        self.assertEqual(groups[0][0], "左中右")
        self.assertEqual(groups[-1][0], "其他")
        model = PickerState(groups)
        self.assertEqual(len(model.options()), 4)
        model.turn(1); model.choose(1)
        self.assertEqual(model.stage, "maps")
        self.assertTrue(model.back()); self.assertEqual(model.page, 1)
        self.assertIsNone(model.choose(-1))
        single = PickerState(picker_groups(pack().maps[:6]))
        self.assertEqual(single.stage, "maps"); self.assertEqual(single.page_count(), 2)
        single.turn(1); self.assertEqual(len(single.options()), 2)
        self.assertFalse(single.back()); self.assertFalse(single.turn(1))

    def test_plan_carries_no_geometry_and_rejects_every_changed_scope(self):
        current = state()
        payload = dict(manual_selection_scope(current).payload(), mapId="map-0")
        original = deepcopy(current)
        plan = plan_manual_selection(current, payload, pack().by_id, floors=pack().floors)
        self.assertTrue(plan.accepted and plan.changed)
        self.assertEqual(plan.floor, "1")
        self.assertEqual(current, original)
        self.assertTrue(manual_selection_is_current(plan, current))
        for changed in (dict(mode="difficult"), dict(cycleId=4), dict(floor="2"),
                        dict(manualSelectionGeneration=5), dict(resourceIdentity="pack-2"),
                        dict(mapSelectionMode="auto"), dict(roundActive=False), dict(resourceLocked=True)):
            self.assertFalse(manual_selection_is_current(plan, dict(current, **changed)), changed)
        self.assertFalse(hasattr(plan, "matrix")); self.assertFalse(hasattr(plan, "liveAuthorization"))
        self.assertFalse(plan_manual_selection(current, dict(payload, mapId="unknown"), pack().by_id,
                                             floors=pack().floors).accepted)
        self.assertEqual(normalize_selection_mode(None), "auto")
        self.assertEqual(normalize_selection_mode([]), "auto")
        self.assertTrue(manual_identity_skipped("manual"))
        self.assertFalse(manual_identity_skipped("manual", locked=True))
        self.assertFalse(manual_identity_skipped("auto"))

    def test_prepared_round_seeds_only_declared_floor(self):
        current = dict(state(), roundActive=False, roundPreparing=True, roundPrepared=True,
                       floor="", floorRequired=True)
        payload = dict(manual_selection_scope(current).payload(), mapId="map-0")
        plan = plan_manual_selection(current, payload, pack().by_id, floors=pack().floors, default_floor="2")
        self.assertTrue(plan.accepted); self.assertEqual(plan.floor, "2")
        self.assertFalse(plan_manual_selection(current, payload, pack().by_id, floors=()).accepted)


class ManualSelectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.session = FakeSession()
        self.service = ManualSelectionService(self.session)
        self.pack = pack()
        self.service.refresh_catalog(self.pack, "pack-1")
        self.addCleanup(self.service.shutdown)

    def test_selects_catalog_map_with_exact_scope_not_automatic_candidate(self):
        self.assertTrue(self.service.open())
        self.assertTrue(self.service.choose(0))
        self.service.turn(1)
        revision = self.service.snapshot["revision"]
        self.assertTrue(self.service.choose(1, revision))
        action, payload = self.session.calls[-1]
        self.assertEqual(action, "select_manual_map")
        self.assertEqual(payload, dict(manual_selection_scope(state()).payload(), mapId="map-5"))
        self.assertFalse(self.service.visible)
        self.assertFalse(self.service.command("manual-picker-1"))
        self.assertNotIn("mapId", self.session.state)

    def test_late_click_floor_resource_and_round_invalidation_no_optimistic_mode(self):
        self.service.open(); self.service.choose(0)
        revision = self.service.snapshot["revision"]
        self.session.state["floor"] = "2"; self.session.changed.emit()
        self.assertTrue(self.service.visible)
        self.assertFalse(self.service.choose(0, revision))
        self.service.setMode("auto")
        self.assertEqual(self.session.calls[-1], ("set_map_selection_mode", {"selectionMode": "auto"}))
        self.assertEqual(self.service.snapshot["mode"], "manual")
        self.session.state.update(roundActive=False, manualSelectionGeneration=5)
        self.session.changed.emit()
        self.assertFalse(self.service.visible)
        self.assertEqual(self.service.snapshot["mode"], "manual")
        self.assertFalse(self.service.open())
        self.session.state.update(roundActive=True, resourceIdentity="pack-2")
        self.session.changed.emit()
        self.assertFalse(self.service.open())
        self.service.refresh_catalog(self.pack, "pack-2")
        self.assertTrue(self.service.open())

    def test_qml_creates_four_stable_cards_and_no_invented_shortcuts(self):
        self.service.open(); self.service.choose(0)
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("manualSelection", self.service)
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientManualSelection.qml"))))
        self.assertFalse(component.isError(), [e.toString() for e in component.errors()])
        page = component.create()
        self.assertIsNotNone(page, [e.toString() for e in component.errors()])
        def cards(item):
            return [child for child in item.childItems() if child.objectName().startswith("manualPickerOption_")] + [
                result for child in item.childItems() for result in cards(child)]
        before = cards(page)
        self.assertEqual(len(before), 4)
        self.service.turn(1); APP.processEvents()
        self.assertEqual(cards(page), before)
        self.assertEqual(self.service.snapshot["keyLabels"], {})
        self.assertEqual(sum(card.isEnabled() for card in before), 2)
        self.service.refresh_catalog(self.pack, "pack-1")
        self.assertTrue(self.service.visible)  # same inventory refresh is a no-op
        self.assertEqual(warnings, [])
        page.deleteLater(); APP.processEvents()


if __name__ == "__main__":
    unittest.main()
