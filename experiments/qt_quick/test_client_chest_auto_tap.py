"""Fake service and native metadata only: no worker, key reads or sends."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from PySide6.QtCore import QObject, QMetaObject, QUrl, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from client_chest_auto_tap import ChestAutoTapService
from chest_auto_tap import validate

APP = QGuiApplication.instance() or QGuiApplication([])


class Owner(QObject):
    changed = Signal()
    saved = Signal()


class FakeService:
    def __init__(self, path, *, target_window, reserved):
        self.path, self.target, self.reserved = path, target_window, reserved
        self.state = dict(enabled=True, holdKey="F12", tapKey="F", intervalMs=90,
                          available=True, status="未开启", holding=False, error="")
        self.calls = []

    def snapshot(self):
        return dict(self.state)

    def update(self, settings):
        self.calls.append(("update", dict(settings)))
        if settings.get("enabled") is False:
            self.state["enabled"] = False
        try:
            value = validate(dict(self.state, **settings), self.reserved())
        except ValueError as error:
            return dict(success=False, message=str(error))
        self.state.update(value)
        if self.state["enabled"]:
            self.calls.append(("sync",))
        return dict(success=True, message="saved")

    def stop(self):
        self.calls.append(("stop",))

    def reconcile_reserved(self):
        self.calls.append(("reconcile",))
        try:
            validate(self.state, self.reserved())
            return dict(success=True)
        except ValueError as error:
            self.state["enabled"] = False
            return dict(success=False, message=str(error))


class ChestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.owner, self.input, self.settings = Owner(), Owner(), Owner()
        self.owner.boundWindow = "42"
        self.owner.closing = self.owner.textEntryActive = False
        self.owner.gameWindows = [dict(hwnd="42", pid=700)]
        self.input.snapshot = dict(enabled=True, hwnd="42", bindings={"map": "G", "escape": "ESCAPE"},
                                   closed=False, textEntryActive=False, editingBindings=False)
        self.focus, self.pid, self.valid = 42, 700, True
        self.reads = []
        def user():
            self.reads.append("api")
            return SimpleNamespace(IsWindow=lambda hwnd: self.valid,
                                   GetForegroundWindow=lambda: self.focus)
        self.native = SimpleNamespace(get_user32=user, window_process_id=lambda hwnd: self.pid,
            window_text=lambda hwnd: "game", process_image_path=lambda pid: "game/dwrg.exe",
            identity_is_allowed=lambda title, image: self.valid)
        self.adapter = ChestAutoTapService(SimpleNamespace(settings_path=Path(self.temp.name)/"ui-settings.json"),
            self.owner, self.input, self.settings, service_factory=FakeService, native=self.native)
        self.addCleanup(self.adapter.shutdown)
        self.service = self.adapter._service

    def enable(self):
        return self.adapter.update(True, "F12", "F", "90")

    def test_loading_enabled_preference_never_arms_or_starts(self):
        self.assertTrue(self.adapter.snapshot["savedEnabled"])
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.owner.changed.emit()
        self.settings.saved.emit()
        self.assertFalse(any(c[0] == "sync" for c in self.service.calls))
        self.assertEqual(self.reads, [])
        self.assertEqual(self.service.path.name, "chest-auto-tap.json")

    def test_explicit_enable_waits_for_game_foreground(self):
        self.focus = 99
        self.assertTrue(self.enable()["success"])
        self.assertTrue(self.adapter.snapshot["enabled"])
        self.assertFalse(self.adapter.snapshot["running"])
        self.assertFalse(any(c[0] == "sync" for c in self.service.calls))
        self.focus = 42
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot["running"])
        self.assertEqual(self.service.target(), 42)
        syncs = self.service.calls.count(("sync",))
        self.adapter.pollOnce()
        self.assertEqual(syncs, self.service.calls.count(("sync",)))
        self.focus = 99
        self.adapter.pollOnce()
        self.assertIsNone(self.service.target())
        self.assertFalse(self.adapter.snapshot["running"])
        self.assertEqual(self.service.calls[-1], ("stop",))

    def test_recording_text_and_input_disable_suspend(self):
        for key in ("textEntryActive", "editingBindings", "enabled"):
            self.enable()
            self.assertTrue(self.adapter.snapshot["running"])
            self.input.snapshot[key] = key != "enabled"
            self.input.changed.emit()
            self.assertFalse(self.adapter.snapshot["running"])
            self.assertIsNone(self.service.target())
            self.input.snapshot[key] = key == "enabled"
        self.owner.textEntryActive = True
        self.owner.changed.emit()
        self.assertFalse(self.adapter.snapshot["running"])

    def test_invalid_identity_or_recycled_pid_never_starts(self):
        for attribute, value in (("pid", 701), ("valid", False)):
            setattr(self, attribute, value)
            self.enable()
            self.assertFalse(self.adapter.snapshot["running"])
            self.assertFalse(any(c[0] == "sync" for c in self.service.calls))
            self.pid, self.valid = 700, True

    def test_rebinding_disarms_until_another_explicit_enable(self):
        self.enable()
        self.owner.boundWindow = "43"
        self.owner.changed.emit()
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.assertIsNone(self.service.target())
        self.owner.boundWindow = "42"
        self.owner.changed.emit()
        self.assertFalse(self.adapter.snapshot["running"])

    def test_disabled_input_prevents_native_api_and_sync(self):
        self.input.snapshot["enabled"] = False
        self.assertFalse(self.enable()["success"])
        self.assertEqual(self.reads, [])
        self.assertFalse(any(c[0] == "sync" for c in self.service.calls))

    def test_actual_bindings_reserve_combo_primary_and_reconcile(self):
        self.input.snapshot["bindings"]["test"] = "CTRL+F12"
        self.assertFalse(self.enable()["success"])
        self.input.snapshot["bindings"].pop("test")
        self.enable()
        self.input.snapshot["bindings"]["test"] = "CTRL+F12"
        self.settings.saved.emit()
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.assertFalse(self.adapter.snapshot["running"])
        self.assertIn(("reconcile",), self.service.calls)

    def test_disable_validation_error_stays_stopped(self):
        self.enable()
        result = self.adapter.update(False, "F", "F", "bad")
        self.assertFalse(result["success"])
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.assertIsNone(self.service.target())

    def test_closing_stops_and_future_commands_cannot_restart(self):
        self.enable()
        self.owner.closing = True
        self.owner.changed.emit()
        count = len(self.service.calls)
        self.assertFalse(self.enable()["success"])
        self.adapter.pollOnce()
        self.assertEqual(len(self.service.calls), count)
        self.assertIsNone(self.service.target())

    def test_qml_has_real_settings_entry_and_calls_adapter(self):
        engine = QQmlEngine()
        engine.rootContext().setContextProperty("chestAutoTapService", self.adapter)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("ClientChestAutoTap.qml"))))
        root = component.create()
        self.assertIsNotNone(root, [e.toString() for e in component.errors()])
        switch = root.findChild(QObject, "chestAutoTapEnabled")
        self.assertIsNotNone(switch)
        self.assertEqual(root.findChild(QObject, "chestHoldKey").property("currentText"), "F12")
        switch.setProperty("checked", True)
        QMetaObject.invokeMethod(switch, "clicked")
        self.assertTrue(self.adapter.snapshot["running"])
        switch.setProperty("checked", False)
        QMetaObject.invokeMethod(switch, "clicked")
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.assertIsNone(self.service.target())
        root.deleteLater()
        APP.processEvents()


if __name__ == "__main__":
    unittest.main()
