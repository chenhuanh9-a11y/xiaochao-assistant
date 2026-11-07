"""Settings mouse input -> real writer -> real panel, with isolated data/services."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, QUrl, Property
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

import test_client_settings as settings_fixture
import test_settings_accessibility as accessibility
from test_client_control_stability import State, BoundCoordinator
from client_control_layout import preset_layout


class LayoutState(State):
    currentSeason = Property(str, lambda self: self.data.get("season", "s0"), notify=State.changed)
    appliedSeason = Property(str, lambda self: self.data.get("season", "s0"), notify=State.changed)


class LayoutApplicationTests(unittest.TestCase):
    setUpClass = classmethod(settings_fixture.SettingsTests.setUpClass.__func__)
    setUp = settings_fixture.SettingsTests.setUp
    tearDown = settings_fixture.SettingsTests.tearDown
    service = settings_fixture.SettingsTests.service
    write = settings_fixture.SettingsTests.write
    wait = settings_fixture.SettingsTests.wait
    create_view = accessibility.SettingsAccessibilityTests.create_view
    close_view = accessibility.SettingsAccessibilityTests.close_view
    item = accessibility.SettingsAccessibilityTests.item
    click = accessibility.SettingsAccessibilityTests.click

    def panel(self, service, season="s1"):
        state = LayoutState()
        state.data.update(season=season, roundActive=False, roundPreparing=False, mapOpen=False,
                          identityPhase="UNLOCKED", groupId="", groupLocked=False,
                          mapId="", provisionalMapId="", observedFloor="")
        coordinator = BoundCoordinator(service)
        windows = QObject()
        engine = QQmlApplicationEngine()
        warnings = []
        engine.warnings.connect(lambda rows: warnings.extend(str(row) for row in rows))
        for name, value in dict(settingsService=service, session=state,
                                clientCoordinator=coordinator, windows=windows).items():
            engine.rootContext().setContextProperty(name, value)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientControls.qml"))))
        self.assertTrue(engine.rootObjects(), warnings)
        window = engine.rootObjects()[0]
        window.show()
        QTest.qWait(30)
        def close():
            window.close()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()
        self.addCleanup(close)
        # Keep context objects alive for the engine's lifetime.
        window.context_owners = (state, coordinator, windows, engine)
        window.qml_warnings = warnings
        return window

    def value(self, panel, key):
        value = panel.property(key)
        return value.toVariant() if hasattr(value, "toVariant") else value

    def visual(self, panel, name):
        return settings_fixture.visual_item(panel.contentItem(), name)

    def displayed_options(self, panel):
        def walk(item):
            yield item
            for child in item.childItems():
                yield from walk(child)
        return {item.property("settingKey") for item in walk(panel.contentItem())
                if item.property("settingKey") and item.isVisible()}

    def test_saved_custom_layout_reaches_panel_in_both_seasons(self):
        layout = preset_layout()
        layout.update(order=["mode", "display", "round", "manual_map", "recognition"],
                      enabled=["mode", "display", "round"])
        layout["display"] = dict(alignedOpacity=True, referenceOpacity=True, referencePosition=True)
        self.write(dict(gameControlLayout=layout, unrelated={"keep": True}))
        service = self.service()
        for season in ("s0", "s1"):
            with self.subTest(season=season):
                panel = self.panel(service, season)
                self.assertEqual(self.value(panel, "layout"), layout)
                self.assertEqual(self.value(panel, "activeModules"), ["mode", "display", "round"])
                self.assertEqual(self.displayed_options(panel), {"sheetOpacity", "referenceOpacity"})
                self.assertTrue(self.visual(panel, "controlEditReferencePosition").isVisible())
                mode = self.visual(panel, "controlModeNightmare")
                display = self.visual(panel, "controlAlignedVisibility")
                action = self.visual(panel, "controlRoundAction")
                self.assertLess(mode.mapToScene(QPointF()).y(), display.mapToScene(QPointF()).y())
                self.assertLess(display.mapToScene(QPointF()).y(), action.mapToScene(QPointF()).y())
                self.assertEqual(panel.qml_warnings, [])

    def test_mouse_edits_preview_reset_save_and_restart(self):
        service = self.service()
        panel = self.panel(service)
        view = self.create_view(service)
        self.click(view, "controlLayoutToggle")
        self.click(view, "layout_display_referenceOpacity")
        self.assertTrue(service.controlLayoutDirty)
        self.assertIn("referenceOpacity", self.displayed_options(panel))
        self.click(view, "layout_enabled_mode")
        self.assertEqual(self.value(panel, "activeModules"), ["display", "round"])
        self.assertIsNone(self.visual(panel, "controlModeNightmare"))
        self.click(view, "layout_reset")
        self.assertEqual(self.value(panel, "activeModules"), ["display", "mode", "round"])
        self.assertNotIn("referenceOpacity", self.displayed_options(panel))
        self.assertFalse(self.path.exists())
        self.click(view, "layout_preset_common")
        self.click(view, "layout_up_mode")
        self.click(view, "layout_save")
        self.wait(lambda: not service.busy)
        expected = service.persistedValues["gameControlLayout"]
        self.assertEqual(json.loads(self.path.read_text())["gameControlLayout"], expected)
        self.assertEqual(self.value(panel, "activeModules"), ["mode", "display", "round"])
        reopened = self.panel(self.service())
        self.assertEqual(self.value(reopened, "layout"), expected)
        self.assertEqual(self.displayed_options(reopened), {"sheetOpacity", "referenceOpacity"})
        self.assertEqual(view.qml_warnings + panel.qml_warnings + reopened.qml_warnings, [])

    def test_s0_optional_modules_and_s1_automatic_controls_remain_scoped(self):
        service = self.service()
        panel = self.panel(service, "s0")
        layout = preset_layout("common")
        layout["enabled"] = list(layout["order"])
        self.assertTrue(service.setValue("gameControlLayout", layout))
        self.app.processEvents()
        self.assertEqual(self.value(panel, "activeModules"), layout["order"])
        state = panel.context_owners[0]
        state.data["season"] = "s1"
        state.changed.emit()
        self.app.processEvents()
        self.assertEqual(self.value(panel, "activeModules"), ["display", "mode", "round"])
        self.assertIsNone(self.visual(panel, "controlReselectMap"))
        self.assertEqual(self.visual(panel, "controlGameContextSeason").property("displayText"), "S1 · 磷光症")
        self.assertEqual(service.values["gameControlLayout"]["enabled"], layout["enabled"])
        state.data["season"] = "s0"
        state.changed.emit()
        self.app.processEvents()
        self.assertEqual(self.value(panel, "activeModules"), layout["order"])
        self.assertIsNotNone(self.visual(panel, "controlReselectMap"))
        self.assertEqual(self.visual(panel, "controlGameContextSeason").property("displayText"), "S0 · 厄运之女")
        layout["enabled"] = []
        self.assertTrue(service.setValue("gameControlLayout", deepcopy(layout)))
        self.app.processEvents()
        self.assertEqual(self.value(panel, "activeModules"), [])
        self.assertIsNone(self.visual(panel, "controlRoundAction"))
        self.assertEqual(panel.qml_warnings, [])

    def test_legacy_layout_migrates_for_display_without_rewriting_on_read(self):
        from control_layout import preset_layout as legacy_preset
        old = legacy_preset("common")
        old["order"] = ["round", "map", "display", "recognition"]
        self.write(dict(gameControlLayout=old, unrelated={"keep": True}))
        before = self.path.read_bytes()
        service = self.service()
        panel = self.panel(service, "s0")
        self.assertEqual(self.value(panel, "layout")["schemaVersion"], 2)
        self.assertEqual(self.value(panel, "activeModules"), ["round", "mode", "manual_map", "display"])
        self.assertEqual(self.displayed_options(panel), {"sheetOpacity", "referenceOpacity"})
        state = panel.context_owners[0]
        state.data["season"] = "s1"
        state.changed.emit()
        self.app.processEvents()
        self.assertEqual(self.value(panel, "activeModules"), ["round", "mode", "display"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(panel.qml_warnings, [])

    def test_s1_selector_fits_the_real_panel_clipping_ancestors(self):
        panel = self.panel(self.service(), "s1")
        for name in ("controlGameContextSeason", "controlModeNightmare",
                     "controlModeDifficult", "controlGameContextDifficulty2"):
            item = self.visual(panel, name)
            self.assertTrue(item.isVisible(), name)
            ancestor = item.parentItem()
            while ancestor is not None:
                if ancestor.clip():
                    top = item.mapToItem(ancestor, QPointF())
                    bottom = item.mapToItem(ancestor, QPointF(item.width(), item.height()))
                    self.assertGreaterEqual(top.x(), -0.01, name)
                    self.assertGreaterEqual(top.y(), -0.01, name)
                    self.assertLessEqual(bottom.x(), ancestor.width() + 0.01, name)
                    self.assertLessEqual(bottom.y(), ancestor.height() + 0.01, name)
                ancestor = ancestor.parentItem()
        self.assertEqual(panel.qml_warnings, [])

    def test_selector_reexpands_after_narrow_panel_and_season_changes(self):
        panel = self.panel(self.service(), "s1")
        state = panel.context_owners[0]
        for season in ("s1", "s0", "s1"):
            state.data["season"] = season
            state.changed.emit()
            for width, stacked in ((500, False), (240, True), (500, False)):
                panel.setWidth(width)
                QTest.qWait(30)
                selector = self.visual(panel, "controlGameContext")
                self.assertEqual(selector.property("stacked"), stacked, (season, width, selector.width()))
        self.assertEqual(panel.qml_warnings, [])


if __name__ == "__main__":
    unittest.main()
