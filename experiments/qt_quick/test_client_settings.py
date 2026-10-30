"""Isolated persistence contracts; no installed config, game, hooks, or Tk."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine

from client_settings import ACTION_ORDER, ROOT, SettingsService


class SettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="qt-settings-test-", dir=ROOT / "work")
        self.sandbox = Path(self.temporary.name)
        (self.sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
        self.path = self.sandbox / "ui-settings.json"
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.shutdown()
        self.app.processEvents()
        self.temporary.cleanup()

    def service(self):
        service = SettingsService(self.sandbox)
        self.services.append(service)
        return service

    def write(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), "worker completion timeout")

    def test_new_file_has_no_boot_write_and_legacy_serialization(self):
        service = self.service()
        self.assertFalse(self.path.exists())
        self.assertFalse(service.save())
        self.assertTrue(service.setValue("sheetOpacity", .73))
        self.assertTrue(service.setValue("hotkey", "control+g"))
        emitted = []
        service.saved.connect(lambda: emitted.append(True))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"sheetOpacity": .73, "hotkey": "Ctrl+G"})
        self.assertEqual(emitted, [True])
        self.assertFalse(service.dirty)
        self.assertTrue(service.reload())
        self.assertEqual(service.values["referenceOpacity"], .73)

    def test_unknown_and_unsupported_fields_and_invalid_values_survive(self):
        original = {"future": {"unicode": "路线", "flags": [1, True, None]},
                    "activeConfirmation": {"mapId": "untouched"}, "sheetOpacity": 10,
                    "compactPosition": [24, 25], "experimentalAutoOverlay": False}
        self.write(original)
        service = self.service()
        self.assertFalse(service.dirty)
        self.assertIn("无效字段", service.message)
        self.assertTrue(service.setValue("orbVisible", False))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        expected = {**original, "orbVisible": False}
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), expected)
        self.assertTrue(service.setValue("sheetOpacity", .55))
        self.assertTrue(service.dirty, "explicitly editing an invalid field to its fallback repairs it")
        service.save()
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["sheetOpacity"], .55)

    def test_corrupt_or_non_object_json_is_preserved_until_reloaded(self):
        for raw in (b"{broken", b"[]", b'{"x": NaN}', b"\xff"):
            self.path.write_bytes(raw)
            service = self.service()
            self.assertIn("读取失败", service.message)
            service.setValue("sheetOpacity", .8)
            self.assertFalse(service.save())
            self.assertEqual(self.path.read_bytes(), raw)
        self.write({"preserve": 4})
        self.assertTrue(service.reload())
        self.assertFalse(service.dirty)
        self.assertEqual(self.path.read_text(), '{"preserve": 4}')

    def test_validation_and_shortcut_conflicts(self):
        service = self.service()
        before = service.values
        for key, value in (("sheetOpacity", True), ("sheetOpacity", 101),
                           ("sheetOpacity", float("nan")), ("sheetScale", .49),
                           ("interfaceOpacity", .96), ("orbVisible", "false"),
                           ("gameMode", "other"), ("routeProfileId", ""),
                           ("unknown", True), ("actionHotkeys", ["F1"]),
                           ("hotkey", "Win+G"), ("hotkey", "F9")):
            self.assertFalse(service.setValue(key, value), (key, value))
        self.assertEqual(service.values, before)
        self.assertFalse(service.dirty)
        self.assertTrue(service.setValue("hotkey", "Ctrl+Shift+MOUSE4"))
        keys = service.values["actionHotkeys"]
        self.assertEqual(len(keys), len(ACTION_ORDER))
        keys[0] = "Ctrl+Shift+MOUSE4"
        self.assertFalse(service.setValue("actionHotkeys", keys))
        keys[0] = ""
        self.assertTrue(service.setValue("actionHotkeys", keys))
        service.resetDraft()
        self.assertEqual(service.values, before)
        keys = service.values["actionHotkeys"]
        keys[0] = ""
        service.setValue("actionHotkeys", keys)
        service.setValue("hotkey", "F9")
        self.assertFalse(service.saveFields(["hotkey"]), "partial save must validate against persisted shortcuts")
        self.assertFalse(self.path.exists())
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["hotkey"], "F9")

    def test_legacy_short_action_arrays_keep_slots_and_disk_shape(self):
        for keys in (["F1", "F2", "F3", "F4", "F5", "F6"],
                     ["F1", "F2", "F3", "F4", "F5", "F6", "F7"],
                     ["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8"]):
            self.write({"actionHotkeys": keys, "hotkey": "F11"})
            service = self.service()
            expected = keys + (["F12"] if len(keys) == 6 else [])
            expected += [""] * (9 - len(expected))
            self.assertEqual(service.values["actionHotkeys"], expected)
            service.setValue("orbVisible", False)
            service.save()
            self.wait(lambda: not service.busy)
            self.assertEqual(json.loads(self.path.read_text())["actionHotkeys"], keys)

    def test_duplicate_save_and_late_completion_preserve_new_draft(self):
        service = self.service()
        entered, release = threading.Event(), threading.Event()
        original_write = service._write
        calls = []
        def delayed(payload, expected):
            calls.append(payload)
            entered.set()
            if not release.wait(4):
                raise RuntimeError("test release timeout")
            return original_write(payload, expected)
        service._write = delayed
        service.setValue("sheetOpacity", .6)
        service.save()
        self.wait(entered.is_set)
        self.assertFalse(service.save())
        self.assertFalse(service.reload())
        self.assertFalse(service.resetDraft())
        service.setValue("sheetOpacity", .8)
        release.set()
        self.wait(lambda: not service.busy)
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.path.read_text())["sheetOpacity"], .6)
        self.assertEqual(service.values["sheetOpacity"], .8)
        self.assertTrue(service.dirty)
        service.save()
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["sheetOpacity"], .8)
        self.assertFalse(service.dirty)

    def test_write_failure_and_external_change_keep_draft(self):
        self.write({"future": True})
        service = self.service()
        service.setValue("reduceMotion", True)
        with patch("client_settings.os.replace", side_effect=PermissionError("fixture denied")):
            service.save()
            self.wait(lambda: not service.busy)
        self.assertTrue(service.dirty)
        self.assertIn("fixture denied", service.message)
        service.setValue("sheetOpacity", .8)
        self.assertIn("fixture denied", service.message)
        self.assertEqual(json.loads(self.path.read_text()), {"future": True})
        self.assertEqual(list(self.sandbox.glob("*.tmp")), [])
        self.write({"future": "external"})
        service.save()
        self.wait(lambda: not service.busy)
        self.assertIn("其他操作", service.message)
        self.assertEqual(json.loads(self.path.read_text()), {"future": "external"})
        service.reload()
        self.assertFalse(service.dirty)

    def test_late_repair_of_invalid_field_is_not_cleared_by_another_save(self):
        self.write({"sheetOpacity": "invalid"})
        service = self.service()
        release = threading.Event()
        original_write = service._write
        def delayed(payload, expected):
            if not release.wait(4):
                raise RuntimeError("test release timeout")
            return original_write(payload, expected)
        service._write = delayed
        service.setValue("reduceMotion", True)
        service.save()
        service.setValue("sheetOpacity", .55)
        release.set()
        self.wait(lambda: not service.busy)
        self.assertTrue(service.dirty)
        self.assertEqual(json.loads(self.path.read_text())["sheetOpacity"], "invalid")
        service.save()
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["sheetOpacity"], .55)
        self.assertFalse(service.dirty)

    def test_position_only_save_preserves_open_mode_draft_and_errors(self):
        self.write({"gameMode": "nightmare", "sheetPosition": None, "other": "preserve"})
        service = self.service()
        service.setValue("gameMode", "difficult")
        self.assertFalse(service.setValue("sheetPosition", [True, 12]))
        self.assertFalse(service.setValue("compactPosition", [12.5, 10]))
        self.assertTrue(service.setValue("sheetPosition", [-40, 120]))
        self.assertTrue(service.saveFields(["sheetPosition"]))
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text()), {
            "gameMode": "nightmare", "sheetPosition": [-40, 120], "other": "preserve"})
        self.assertEqual(service.persistedValues["gameMode"], "nightmare")
        self.assertEqual(service.persistedValues["sheetPosition"], [-40, 120])
        self.assertEqual(service.values["gameMode"], "difficult")
        self.assertTrue(service.dirty)
        service.setValue("sheetPosition", [10, 20])
        self.write({"external": True})
        service.saveFields(["sheetPosition"])
        self.wait(lambda: not service.busy)
        self.assertIn("其他操作", service.message)
        self.assertEqual(service.persistedValues["sheetPosition"], [-40, 120])
        self.assertEqual(service.values["sheetPosition"], [10, 20])
        self.path.write_bytes(b"{corrupt")
        self.assertFalse(service.reload())
        self.assertFalse(service.saveFields(["sheetPosition"]))
        self.assertIn("读取失败", service.message)
        self.assertEqual(self.path.read_bytes(), b"{corrupt")

    def test_sandbox_marker_and_path_guard(self):
        (self.sandbox / ".qt-lab-owner").write_text("wrong", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.service()
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / ".qt-lab-owner").write_text("qt-quick-isolated-v1")
            with self.assertRaises(ValueError):
                SettingsService(Path(outside))

    def test_qml_panel_loads_and_tracks_service(self):
        service = self.service()
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("settingsService", service)
        warnings = []
        engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        if component.isLoading():
            self.wait(lambda: not component.isLoading())
        self.assertFalse(component.isError(), "\n".join(str(e) for e in component.errors()))
        panel = component.create()
        self.assertIsNotNone(panel, "\n".join(str(e) for e in component.errors()))
        panel.setProperty("width", 720)
        panel.setProperty("height", 620)
        self.app.processEvents()
        service.setValue("sheetOpacity", .8)
        self.app.processEvents()
        self.assertEqual(panel.property("preferences")["sheetOpacity"], .8)
        self.assertEqual(warnings, [])
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
