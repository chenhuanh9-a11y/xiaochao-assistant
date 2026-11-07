"""Settings entry points with real QML/settings writer, synthetic native services.

No installed preferences, game, global input, or author assets are accessed.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPointF, Qt, QUrl, Signal
from PySide6.QtQuick import QQuickView, QQuickWindow
from PySide6.QtTest import QTest

from client_control_layout import load_layout, preset_layout
from control_layout import preset_layout as legacy_preset
import test_client_settings as settings_fixture
import test_client_coordinator as coordinator_fixture


class SettingsAccessibilityTests(unittest.TestCase):
    setUpClass = classmethod(settings_fixture.SettingsTests.setUpClass.__func__)
    setUp = settings_fixture.SettingsTests.setUp
    tearDown = settings_fixture.SettingsTests.tearDown
    service = settings_fixture.SettingsTests.service
    write = settings_fixture.SettingsTests.write
    wait = settings_fixture.SettingsTests.wait

    def create_view(self, service, coordinator=None):
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty("settingsService", service)
        if coordinator is not None:
            view.rootContext().setContextProperty("clientCoordinator", coordinator)
        view.qml_warnings = []
        view.engine().warnings.connect(lambda rows: view.qml_warnings.extend(str(row) for row in rows))
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        self.assertFalse(view.errors(), "\n".join(str(row) for row in view.errors()))
        view.resize(900, 1000)
        view.show()
        self.app.processEvents()
        self.addCleanup(self.close_view, view)
        return view

    def close_view(self, view):
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def item(self, view, name):
        item = settings_fixture.visual_item(view.rootObject(), name)
        self.assertIsNotNone(item, name)
        return item

    def click(self, view, name):
        QTest.qWait(20)  # Wait for real visibility/layout changes before hit testing.
        item = self.item(view, name)
        self.assertTrue(item.isVisible(), name)
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        self.assertTrue(view.geometry().size().width() > point.x() >= 0, name)
        self.assertTrue(view.geometry().size().height() > point.y() >= 0, name)
        QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, point)
        self.app.processEvents()

    def test_fresh_defaults_include_mode_and_round_without_rewriting_existing_layouts(self):
        service = self.service()
        self.assertEqual(service.values["gameControlLayout"]["enabled"], ["display", "mode", "round"])
        self.assertFalse(self.path.exists(), "Loading defaults must not persist preferences")
        for name in ("compact", "common"):
            self.assertTrue({"mode", "round"} <= set(preset_layout(name)["enabled"]))
        old_compact = preset_layout()
        old_compact["enabled"] = ["display", "round"]
        custom = preset_layout()
        custom.update(order=["round", "recognition", "manual_map", "mode", "display"], enabled=["manual_map"])
        custom["display"]["referencePosition"] = True
        hidden = preset_layout()
        hidden["enabled"] = []
        for original in (old_compact, custom, hidden, legacy_preset("compact")):
            with self.subTest(layout=original):
                expected = deepcopy(original)
                self.write({"gameControlLayout": original, "unrelated": {"keep": True}})
                self.assertTrue(service.reload())
                self.assertNotIn("mode", service.values["gameControlLayout"]["enabled"])
                self.assertEqual(service.values["gameControlLayout"], load_layout(original))
                service.setValue("referenceOpacity", .61)
                self.assertTrue(service.saveFields(["referenceOpacity"]))
                self.wait(lambda: not service.busy)
                self.assertEqual(json.loads(self.path.read_text())["gameControlLayout"], expected)
                self.assertEqual(json.loads(self.path.read_text())["unrelated"], {"keep": True})

    def test_opacity_and_layout_controls_are_visible_without_advanced_disclosures(self):
        service = self.service()
        view = self.create_view(service)
        page = view.rootObject()
        for width, height in ((540, 740), (720, 740), (1200, 900)):
            with self.subTest(width=width):
                view.resize(width, height)
                QTest.qWait(30)
                self.assertFalse(page.property("advancedDisplay"))
                self.assertFalse(page.property("advancedShortcuts"))
                for name in ("setting_referenceOpacity", "setting_interfaceOpacity", "settingsEditReferencePosition", "controlLayoutToggle"):
                    self.assertTrue(self.item(view, name).isVisible(), name)
                shortcut_column = self.item(view, "settingsShortcutColumn")
                layout_toggle = self.item(view, "controlLayoutToggle")
                self.assertEqual(layout_toggle.parentItem(), shortcut_column)
                self.assertGreater(layout_toggle.y(), self.item(view, "record_action_6").mapToItem(shortcut_column, QPointF()).y())
                self.assertLess(layout_toggle.mapToItem(page, QPointF()).y(), self.item(view, "modeSettingsToggle").mapToItem(page, QPointF()).y())
                self.click(view, "controlLayoutToggle")
                QTest.qWait(30)
                self.assertTrue(self.item(view, "controlLayoutEditor").isVisible())
                for name in ("layout_enabled_manual_map", "layout_up_manual_map", "layout_down_manual_map"):
                    item = self.item(view, name)
                    left = item.mapToItem(shortcut_column, QPointF()).x()
                    self.assertGreaterEqual(left, -.1)
                    self.assertLessEqual(left + item.width(), shortcut_column.width() + .1, name)
                self.click(view, "controlLayoutToggle")
                QTest.qWait(30)
                self.assertFalse(self.item(view, "controlLayoutEditor").isVisible())
        for key, percent in (("referenceOpacity", 42), ("interfaceOpacity", 71)):
            slider = self.item(view, "setting_" + key)
            slider.forceActiveFocus()
            difference = percent - round(slider.property("value"))
            for _ in range(abs(difference)):
                QTest.keyClick(view, Qt.Key_Right if difference > 0 else Qt.Key_Left)
            self.app.processEvents()
            self.assertAlmostEqual(service.values[key], percent / 100)
        self.click(view, "settingsSave")
        self.wait(lambda: not service.busy)
        self.assertAlmostEqual(json.loads(self.path.read_text())["referenceOpacity"], .42)
        self.assertAlmostEqual(json.loads(self.path.read_text())["interfaceOpacity"], .71)
        self.assertEqual(view.qml_warnings, [])

    def test_settings_position_edit_finishes_and_cancels_without_showing_hidden_controls(self):
        self.write(dict(qtControlsVisible=False, sheetPosition=[100, 100], sheetPositionSpace="qt-logical-v1"))
        service = self.service()
        harness = coordinator_fixture.CoordinatorTests()
        harness.app = self.app
        harness.setUp()
        self.addCleanup(harness.tearDown)
        owner = harness.coordinator
        owner.settings = service
        reference = QQuickWindow()
        reference.resize(120, 100)
        reference.setPosition(100, 100)
        self.addCleanup(reference.deleteLater)
        harness.windows._windows["reference"] = reference
        harness.windows.snapshot["reference"] = dict(clickThrough=True, visible=False)
        owner._reference = dict(key="reference:fixture")
        view = self.create_view(service, owner)
        for finish in (False, True):
            self.click(view, "settingsEditReferencePosition")
            self.app.processEvents()
            self.assertTrue(owner.referenceEditing)
            self.assertTrue(self.item(view, "settingsFinishReferencePosition").isVisible())
            self.assertTrue(self.item(view, "settingsCancelReferencePosition").isVisible())
            self.assertFalse(self.item(view, "settingsEditReferencePosition").isVisible())
            owner.referencePointer("press", 110, 110)
            owner.referencePointer("move", 140, 150)
            owner.referencePointer("release", 140, 150)
            self.assertEqual((reference.x(), reference.y()), (130, 140))
            target = "settingsFinishReferencePosition" if finish else "settingsCancelReferencePosition"
            self.click(view, target)
            self.wait(lambda: not service.busy)
            self.assertFalse(owner.referenceEditing)
            self.assertTrue(self.item(view, "settingsEditReferencePosition").isVisible())
            expected = [130, 140] if finish else [100, 100]
            self.assertEqual(json.loads(self.path.read_text())["sheetPosition"], expected)
            self.assertEqual([reference.x(), reference.y()], expected)
            self.assertFalse(service.values["qtControlsVisible"])
            self.assertTrue(all(call.args == ("reference",) for call in harness.windows.show.call_args_list))
        self.assertEqual(view.qml_warnings, [])

    def test_pending_preview_can_cancel_retry_and_report_missing_resource_from_settings(self):
        service = self.service()
        harness = coordinator_fixture.CoordinatorTests()
        harness.app = self.app
        harness.setUp()
        self.addCleanup(harness.tearDown)
        owner = harness.coordinator
        owner.settings = service
        view = self.create_view(service, owner)
        cancel = self.item(view, "settingsCancelReferencePosition")
        message = self.item(view, "settingsReferencePositionMessage")
        self.click(view, "settingsEditReferencePosition")
        self.assertIn("请先导入图包", message.property("text"))
        self.assertTrue(cancel.isVisible())
        self.click(view, "settingsCancelReferencePosition")
        self.assertFalse(message.isVisible())

        class Provider(QObject):
            ready = Signal(object)
            failed = Signal(str)

        provider = Provider()
        provider.request, provider.cancel = Mock(), Mock()
        owner.setReferencePreviewProvider(provider)
        self.click(view, "settingsEditReferencePosition")
        self.click(view, "settingsEditReferencePosition")  # A repeated click reuses the existing request.
        provider.request.assert_called_once()
        context = owner._reference_preview_context
        self.assertIn("正在准备位置预览", message.property("text"))
        self.click(view, "settingsCancelReferencePosition")
        provider.cancel.assert_called_once()
        provider.ready.emit(dict(context=context, requestId=1, pixels=object(), previewOnly=True,
            referenceAuthorized=False, liveAuthorization=False))
        self.assertFalse(owner.referenceEditing)
        self.assertFalse(owner.referenceData)
        self.click(view, "settingsEditReferencePosition")
        self.assertEqual(provider.request.call_count, 2)
        provider.failed.emit("synthetic missing fixture")
        self.assertIn("读取失败", message.property("text"))
        self.click(view, "settingsCancelReferencePosition")
        self.assertFalse(message.isVisible())
        self.assertFalse(self.path.exists())
        self.assertEqual(view.qml_warnings, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
