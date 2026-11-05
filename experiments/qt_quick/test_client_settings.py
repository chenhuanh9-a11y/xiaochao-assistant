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
from PySide6.QtCore import QObject, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickView
from PySide6.QtTest import QTest

from client_settings import ACTION_ORDER, LEGACY_ACTIONS, ROOT, SettingsService
from client_control_layout import preset_layout


def visual_item(parent, name):
    """Repeater delegates have visual parents distinct from QObject ownership."""
    if parent.objectName() == name:
        return parent
    for child in parent.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


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

    def test_failure_evidence_defaults_only_when_unconfigured(self):
        service = self.service()
        self.assertTrue(service.persistedValues['automaticFailureEvidence'])
        self.assertFalse(self.path.exists())
        for payload, expected in (({'unrelated': 1}, True),
                                  ({'automaticFailureEvidence': False}, False),
                                  ({'automaticFailureEvidence': True}, True),
                                  ({'automaticFailureEvidence': 'false'}, False)):
            with self.subTest(payload=payload):
                self.write(payload)
                self.assertTrue(service.reload())
                self.assertIs(service.persistedValues['automaticFailureEvidence'], expected)
                self.assertEqual(json.loads(self.path.read_text()), payload)

    def test_unreadable_settings_do_not_enable_failure_sampling(self):
        self.path.write_text('{broken', encoding='utf-8')
        service = self.service()
        self.assertTrue(service.loadFailed)
        self.assertFalse(service.persistedValues['automaticFailureEvidence'])
        self.write({})
        self.assertTrue(service.reload())
        self.assertFalse(service.loadFailed)
        self.assertTrue(service.persistedValues['automaticFailureEvidence'])

    def test_capture_completion_preserves_newer_target_draft(self):
        from threading import Event
        service = self.service()
        entered, release = Event(), Event()
        original_write = service._write
        def blocked_write(payload, expected):
            entered.set()
            release.wait(3)
            return original_write(payload, expected)
        service._write = blocked_write
        service.beginShortcutCapture("hotkey")
        service.captureShortcutKey(Qt.Key_F20, 0, "", False)
        self.assertTrue(service.confirmShortcutCapture())
        self.assertTrue(entered.wait(2))
        try:
            self.assertTrue(service.setValue("hotkey", "F21"))
        finally:
            release.set()
        self.wait(lambda: not service.busy)
        self.assertEqual(service.persistedValues["hotkey"], "F20")
        self.assertEqual(service.values["hotkey"], "F21")
        self.assertTrue(service.dirty)

    def test_capture_commit_isolates_action_index_and_preserves_failed_state(self):
        from PySide6.QtCore import Qt
        from unittest.mock import patch
        service = self.service()
        baseline = service.persistedValues
        actions = service.values["actionHotkeys"]
        actions[1] = "F22"
        service.setValue("actionHotkeys", actions)
        service.setValue("hotkey", "F21")
        service.beginShortcutCapture("toggle-panel")
        service.captureShortcutKey(Qt.Key_F20, 0, "", False)
        self.assertTrue(service.confirmShortcutCapture())
        self.assertFalse(service.confirmShortcutCapture())
        self.assertFalse(service.beginShortcutCapture("hotkey"))
        self.wait(lambda: not service.busy)
        self.assertEqual(service.persistedValues["hotkey"], baseline["hotkey"])
        self.assertEqual(service.persistedValues["actionHotkeys"][1], baseline["actionHotkeys"][1])
        self.assertEqual(service.values["actionHotkeys"][1], "F22")
        self.assertEqual(service.values["hotkey"], "F21")
        self.assertEqual(self.service().persistedValues["actionHotkeys"][0], "F20")
        before = service.persistedValues
        service.beginShortcutCapture("hotkey")
        service.captureShortcutKey(Qt.Key_F19, 0, "", False)
        with patch.object(service, "_write", side_effect=OSError("fixture failure")):
            self.assertTrue(service.confirmShortcutCapture())
            service.cancelShortcutCapture()
            self.wait(lambda: not service.busy)
        self.assertEqual(service.persistedValues, before)
        self.assertEqual(self.service().persistedValues, before)
        self.assertIn("fixture failure", service.message)

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
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"sheetOpacity": .73, "hotkey": "Ctrl+G", "actionHotkeys": service.persistedValues["actionHotkeys"]})
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
            expected += [""] * (len(ACTION_ORDER) - len(expected))
            self.assertEqual(service.values["actionHotkeys"], expected)
            service.setValue("orbVisible", False)
            service.save()
            self.wait(lambda: not service.busy)
            self.assertEqual(json.loads(self.path.read_text())["actionHotkeys"], keys)

    def test_nine_item_actions_never_gain_an_invented_picker_chord(self):
        nine = ["F5", "F6", "F7", "F8", "F9", "F10", "F11", "", ""]
        cases = [
            (nine, "G"),
            (nine[:7] + ["ALT", ""], "G"),  # bare ALT bound: Alt+1 would disable shortcuts
            (nine[:7] + ["Alt+1", ""], "G"),
            (nine, "Alt+1"),
            (nine, "1"),
        ]
        for keys, map_key in cases:
            with self.subTest(map=map_key, keys=keys):
                self.write({"actionHotkeys": keys, "hotkey": map_key})
                service = self.service()
                self.assertEqual(service.values["actionHotkeys"], [*keys, ""])
                self.assertNotIn("hotkey/actionHotkeys", service.message)
                service.setValue("orbVisible", False)
                service.save()
                self.wait(lambda: not service.busy)
                # Readable nine-slot arrays stay untouched until actually edited.
                self.assertEqual(json.loads(self.path.read_text())["actionHotkeys"], keys)
                service.shutdown()

    def test_bare_alt_file_keeps_every_native_shortcut_registrable(self):
        from client_input import NativeShortcutBackend
        from test_client_input import Native
        keys = ["ALT", "F6", "F7", "F8", "F9", "F10", "F11", "", ""]
        self.write({"actionHotkeys": keys, "hotkey": "G"})
        service = self.service()
        backend = NativeShortcutBackend(Native())
        self.addCleanup(backend.shutdown)
        self.assertTrue(backend.configureSettings(service.values))

    def test_fresh_picker_unbound_allows_bare_alt_map_and_action_keys(self):
        service = self.service()
        self.assertEqual(service.values["actionHotkeys"][9], "")
        self.assertTrue(service.setValue("hotkey", "ALT"), service.message)
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        restored = self.service()
        self.assertEqual(restored.persistedValues["hotkey"], "ALT")
        self.assertEqual(restored.persistedValues["actionHotkeys"][9], "")
        self.assertTrue(restored.setValue("hotkey", "G"))
        actions = restored.values["actionHotkeys"]
        actions[1] = "ALT"
        self.assertTrue(restored.setValue("actionHotkeys", actions), restored.message)
        self.assertTrue(restored.save())
        self.wait(lambda: not restored.busy)
        restarted = self.service()
        self.assertEqual(restarted.persistedValues["actionHotkeys"], actions)
        from client_input import NativeShortcutBackend
        from test_client_input import Native
        backend = NativeShortcutBackend(Native())
        self.addCleanup(backend.shutdown)
        self.assertTrue(backend.configureSettings(restarted.persistedValues))

    def test_binding_slot_ten_on_a_nine_slot_file_appends_it(self):
        keys = ["F5", "F6", "F7", "F8", "F9", "F10", "F11", "", ""]
        self.write({"actionHotkeys": keys})
        service = self.service()
        edited = service.values["actionHotkeys"]
        edited[9] = "Ctrl+Alt+M"
        self.assertTrue(service.setValue("actionHotkeys", edited))
        self.assertTrue(service.save())
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["actionHotkeys"], [*keys, "Ctrl+Alt+M"])

    def test_existing_file_without_action_array_leaves_picker_unbound(self):
        # data is not None but the key is absent: load starts from the legacy
        # nine slots; slot 10 stays unbound like a stored nine-slot array.
        self.write({"hotkey": "G"})
        service = self.service()
        self.assertEqual(service.values["actionHotkeys"], [*LEGACY_ACTIONS, ""])

    def test_map_selection_mode_defaults_validates_and_preserves_unknown(self):
        service = self.service()
        self.assertEqual(service.values["mapSelectionMode"], "auto")
        self.assertTrue(service.setValue("mapSelectionMode", "manual"))
        self.assertFalse(service.setValue("mapSelectionMode", "Manual"))
        service.save()
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["mapSelectionMode"], "manual")
        self.write({"mapSelectionMode": "unknown"})
        service = self.service()
        self.assertEqual(service.values["mapSelectionMode"], "auto")
        self.assertIn("mapSelectionMode", service.message)

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

    def test_layout_order_presets_partial_save_and_independent_undo(self):
        original = preset_layout("common")
        original["order"] = ["round", "display", "recognition", "mode", "manual_map"]
        self.write({"gameControlLayout": original, "future": {"retained": True}})
        service = self.service()
        service.setValue("gameMode", "difficult")
        self.assertFalse(service.moveControlModule("round", -1))
        self.assertFalse(service.moveControlModule("unknown", 1))
        self.assertFalse(service.moveControlModule("mode", 2))
        self.assertTrue(service.moveControlModule("mode", -1))
        layout = service.values["gameControlLayout"]
        self.assertEqual(layout["order"], ["round", "display", "mode", "recognition", "manual_map"])
        layout["enabled"] = ["mode", "round"]
        layout["display"]["referencePosition"] = True
        service.setValue("gameControlLayout", layout)
        service.saveFields(["gameControlLayout"])
        self.wait(lambda: not service.busy)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["gameControlLayout"]["enabled"], ["round", "mode"])
        self.assertTrue(saved["gameControlLayout"]["display"]["referencePosition"])
        self.assertEqual(saved["future"], {"retained": True})
        self.assertNotIn("gameMode", saved)
        self.assertTrue(service.dirty)
        service.setControlLayoutPreset("compact")
        self.assertEqual(service.values["gameControlLayout"], preset_layout("compact"))
        service.resetControlLayout()
        self.assertEqual(service.values["gameControlLayout"], saved["gameControlLayout"])
        self.assertEqual(service.values["gameMode"], "difficult")
        self.assertFalse(service.setControlLayoutPreset("unknown"))

    def test_shortcut_record_confirm_conflict_cancel_and_guard(self):
        service = self.service()
        guard = []
        service.shortcutRecordingChanged.connect(guard.append)
        original = service.values
        self.assertFalse(service.beginShortcutCapture("unknown"))
        self.assertTrue(service.beginShortcutCapture("hotkey"))
        self.assertTrue(service.captureShortcutKey(Qt.Key_K, Qt.ControlModifier.value | Qt.ShiftModifier.value, "k", False))
        self.assertEqual(service.shortcutCandidate, "Ctrl+Shift+K")
        self.assertEqual(service.values, original, "recording must not edit the draft")
        self.assertTrue(service.confirmShortcutCapture())
        self.wait(lambda: not service.busy)
        self.assertEqual(service.values["hotkey"], "Ctrl+Shift+K")
        self.assertEqual(guard, [True, False])
        service.beginShortcutCapture("toggle-panel")
        service.captureShortcutKey(Qt.Key_K, Qt.ControlModifier.value | Qt.ShiftModifier.value, "k", False)
        self.assertFalse(service.confirmShortcutCapture())
        self.assertTrue(service.shortcutRecording)
        self.assertIn("重复", service.shortcutMessage)
        self.assertEqual(service.values["actionHotkeys"], original["actionHotkeys"])
        service.cancelShortcutCapture()
        self.assertEqual(guard, [True, False, True, False])
        service.beginShortcutCapture("hotkey")
        service.captureShortcutKey(Qt.Key_F4, 0, "", False)
        service.cancelShortcutCapture()
        self.assertEqual(service.values["hotkey"], "Ctrl+Shift+K")
        service.beginShortcutCapture("hotkey")
        service.clearShortcutCandidate()
        self.assertTrue(service.confirmShortcutCapture())
        self.wait(lambda: not service.busy)
        self.assertEqual(service.values["hotkey"], "")
        self.assertTrue(self.path.exists())

    def test_future_layout_requires_explicit_preset_before_local_repair(self):
        future = {"schemaVersion": 200, "unknown": "preserve"}
        self.write({"gameControlLayout": future})
        service = self.service()
        self.assertFalse(service.controlLayoutDirty)
        service.setValue("sheetOpacity", .7)
        service.saveFields(["sheetOpacity"])
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["gameControlLayout"], future)
        service.setControlLayoutPreset("compact")
        self.assertTrue(service.controlLayoutDirty)
        self.assertTrue(service.saveFields(["gameControlLayout"]))
        self.wait(lambda: not service.busy)
        self.assertEqual(json.loads(self.path.read_text())["gameControlLayout"], preset_layout("compact"))
        self.assertFalse(service.controlLayoutDirty)

    def test_shortcut_local_key_variants_and_release_do_not_replace_primary(self):
        service = self.service()
        service.beginShortcutCapture("hotkey")
        self.assertFalse(service.captureShortcutKey(Qt.Key_Control, Qt.ControlModifier.value, "", False))
        self.assertTrue(service.captureShortcutKey(Qt.Key_A, Qt.ControlModifier.value, "a", False))
        self.assertFalse(service.captureShortcutKey(Qt.Key_Control, 0, "", True))
        self.assertEqual(service.shortcutCandidate, "Ctrl+A")
        service.beginShortcutCapture("hotkey")
        self.assertTrue(service.captureShortcutKey(Qt.Key_Shift, 0, "", True))
        self.assertEqual(service.shortcutCandidate, "SHIFT")
        service.beginShortcutCapture("hotkey")
        self.assertTrue(service.captureShortcutKey(Qt.Key_2, Qt.KeypadModifier.value, "2", False))
        self.assertEqual(service.shortcutCandidate, "NUMPAD2")
        service.beginShortcutCapture("hotkey")
        self.assertTrue(service.captureShortcutMouse(Qt.BackButton.value, Qt.AltModifier.value))
        self.assertEqual(service.shortcutCandidate, "Alt+MOUSE4")
        service.beginShortcutCapture("hotkey")
        self.assertFalse(service.captureShortcutKey(Qt.Key_Meta, Qt.MetaModifier.value, "", False))
        self.assertFalse(service.shortcutCandidateReady)
        service.captureShortcutKey(Qt.Key_Escape, 0, "", False)
        self.assertFalse(service.shortcutRecording)

    def test_qml_layout_and_local_capture_short_flow(self):
        service = self.service()
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty("settingsService", service)
        warnings = []
        view.engine().warnings.connect(lambda items: warnings.extend(str(item) for item in items))
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        self.assertFalse(view.errors(), "\n".join(str(e) for e in view.errors()))
        view.resize(720, 740)
        view.show()
        self.app.processEvents()
        panel = view.rootObject()
        try:
            visual_item(panel, "layout_preset_common").clicked.emit()
            self.app.processEvents()
            visual_item(panel, "layout_up_round").clicked.emit()
            self.assertEqual(service.values["gameControlLayout"]["order"], ["display", "mode", "manual_map", "round", "recognition"])
            visual_item(panel, "layout_save").clicked.emit()
            self.wait(lambda: not service.busy)
            self.assertEqual(json.loads(self.path.read_text())["gameControlLayout"], service.persistedValues["gameControlLayout"])
            visual_item(panel, "record_hotkey").clicked.emit()
            self.app.processEvents()
            self.assertTrue(visual_item(panel, "shortcutCaptureArea").property("activeFocus"))
            QTest.keyClick(view, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
            self.app.processEvents()
            self.assertEqual(service.shortcutCandidate, "Ctrl+Shift+K")
            self.assertEqual(service.values["hotkey"], "G")
            service.setValue("hotkey", "F21")
            self.app.processEvents()
            self.assertEqual(visual_item(panel, "setting_hotkey").property("text"), "G")
            shortcut_field = visual_item(panel, "setting_hotkey")
            self.assertTrue(shortcut_field.property("readOnly"))
            shortcut_field.forceActiveFocus()
            shortcut_field.editingFinished.emit()
            visual_item(panel, "shortcutCaptureArea").forceActiveFocus()
            self.app.processEvents()
            self.assertEqual(shortcut_field.property("text"), "G")
            self.assertEqual(service.values["hotkey"], "F21")
            visual_item(panel, "shortcutCaptureConfirm").clicked.emit()
            self.wait(lambda: not service.busy)
            self.assertFalse(service.shortcutRecording)
            self.assertEqual(service.values["hotkey"], "Ctrl+Shift+K")
            self.assertEqual(json.loads(self.path.read_text())["hotkey"], "Ctrl+Shift+K")
            from unittest.mock import patch
            visual_item(panel, "record_hotkey").clicked.emit()
            self.app.processEvents()
            QTest.keyClick(view, Qt.Key_F19)
            with patch.object(service, "_write", side_effect=OSError("qml save failure")):
                visual_item(panel, "shortcutCaptureConfirm").clicked.emit()
                self.assertTrue(service.busy)
                for name in ("shortcutCaptureConfirm", "shortcutCaptureAgain", "shortcutCaptureClear", "shortcutCaptureCancel"):
                    self.assertFalse(visual_item(panel, name).property("enabled"), name)
                self.wait(lambda: not service.busy)
            self.app.processEvents()
            self.assertEqual(visual_item(panel, "setting_hotkey").property("text"), "Ctrl+Shift+K")
            self.assertIn("qml save failure", service.shortcutMessage)
            self.assertEqual(json.loads(self.path.read_text())["hotkey"], "Ctrl+Shift+K")
            self.assertEqual(warnings, [])
        finally:
            service.cancelShortcutCapture()
            view.close()
            view.deleteLater()
            self.app.processEvents()

    def test_qml_season_author_draft_save_reaches_runtime_and_rejects_stale_options(self):
        from client_live_runtime import LiveRuntime
        from client_session import SessionService
        from test_client_coordinator import CoordinatorTests
        self.write(dict(gameSeason="s0", gameMode="nightmare", routeProfileId="profile-one",
            modeRouteProfiles={"nightmare":"profile-one", "difficult":"other"},
            contextRouteProfiles={"s0:nightmare":"profile-one", "s0:difficult":"other"}))
        service = self.service()
        # Real settings writer, coordinator save handler, session worker and runtime;
        # only native input/window discovery and display consumers are fakes.
        harness = CoordinatorTests()
        harness.app = self.app
        harness.setUp()
        coordinator = harness.coordinator
        runtime = LiveRuntime(engine_factories={})
        runtime.configure_settings(service.persistedValues)
        runtime.dispatch("set_context", dict(season="s0", mode="nightmare", profileId="profile-one"))
        session = SessionService(runtime)
        coordinator.settings, coordinator.session, coordinator.runtime = service, session, runtime
        coordinator._bound = ""
        service.changed.connect(coordinator._draft_changed)
        service.saved.connect(coordinator._settings_saved)
        session.changed.connect(coordinator._session_changed)
        view = QQuickView()
        view.setResizeMode(QQuickView.SizeRootObjectToView)
        view.rootContext().setContextProperty("settingsService", service)
        view.setSource(QUrl.fromLocalFile(str(Path(__file__).with_name("ClientSettings.qml"))))
        self.assertFalse(view.errors())
        view.resize(900, 1000)
        view.show()
        panel = view.rootObject()
        panel.setProperty("modeExpanded", True)
        panel.setProperty("profilesMode", "nightmare")
        panel.setProperty("profilesSeason", "s0")
        panel.setProperty("profileOptions", [{"id":"profile-one", "name":"One"}, {"id":"profile-two", "name":"Two"}])
        self.app.processEvents()
        picker = visual_item(panel, "setting_routeProfileId")
        def click_settings_action(name):
            from PySide6.QtCore import QPointF, QPoint
            item = visual_item(panel, name)
            point = item.mapToScene(QPointF(item.width()/2, item.height()/2))
            self.assertTrue(item.isVisible())
            self.assertGreaterEqual(point.x(), 0)
            self.assertGreaterEqual(point.y(), 0)
            self.assertLess(point.x(), view.width())
            self.assertLess(point.y(), view.height())
            QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(round(point.x()), round(point.y())))
            self.app.processEvents()
        try:
            self.assertEqual(picker.property("currentIndex"), 0)
            picker.forceActiveFocus()
            QTest.keyClick(view, Qt.Key_Down)
            self.app.processEvents()
            self.assertEqual(service.values["contextRouteProfiles"]["s0:nightmare"], "profile-two")
            self.assertEqual(runtime.snapshot()["profileId"], "profile-one")
            click_settings_action("settingsReset")
            self.assertEqual(service.values["contextRouteProfiles"]["s0:nightmare"], "profile-one")
            picker.forceActiveFocus()
            QTest.keyClick(view, Qt.Key_Down)
            click_settings_action("settingsSave")
            self.wait(lambda: not service.busy and not session.busy and runtime.snapshot()["profileId"] == "profile-two")
            self.assertEqual(session.appliedProfile, "profile-two")
            self.assertEqual(service.persistedValues["contextRouteProfiles"]["s0:difficult"], "other")
            self.assertEqual(json.loads(self.path.read_text())["contextRouteProfiles"]["s0:nightmare"], "profile-two")
            # Same difficulty with new season must disable still-published S0 rows.
            context_picker = visual_item(panel, "setting_gameContextSeason")
            context_picker.forceActiveFocus()
            QTest.keyClick(view, Qt.Key_Down)
            self.app.processEvents()
            self.assertEqual((service.values["gameSeason"], service.values["gameMode"]), ("s1", "nightmare"))
            self.assertFalse(picker.property("enabled"))
            self.assertEqual(picker.property("currentIndex"), -1)
            before = service.values["contextRouteProfiles"]
            picker.activated.emit(0)  # A queued activation from the obsolete model.
            self.assertEqual(service.values["contextRouteProfiles"], before)
            panel.setProperty("profilesSeason", "s1")
            panel.setProperty("profilesBusy", True)
            self.assertFalse(picker.property("enabled"))
            panel.setProperty("profileOptions", [])
            panel.setProperty("profilesBusy", False)
            self.assertFalse(picker.property("enabled"))
            self.assertEqual("当前作者未安装", picker.property("displayText"))
            self.assertEqual(runtime.snapshot()["season"], "s0")
            click_settings_action("settingsReset")
            self.assertEqual(service.values["gameSeason"], "s0")
        finally:
            coordinator._closed = True
            session.shutdown()
            harness.tearDown()
            view.close()
            view.deleteLater()
            from PySide6.QtCore import QCoreApplication, QEvent
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            self.app.processEvents()

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
        panel.setProperty("modeExpanded", True)
        self.app.processEvents()
        manual = visual_item(panel, "setting_mapSelectionMode_manual")
        self.assertIsNotNone(manual)
        manual.clicked.emit()
        self.wait(lambda: not service.busy)
        self.assertEqual(service.values["mapSelectionMode"], "manual")
        self.assertEqual(json.loads(self.path.read_text())["mapSelectionMode"], "manual")
        self.assertEqual(warnings, [])
        panel.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main(verbosity=2)
