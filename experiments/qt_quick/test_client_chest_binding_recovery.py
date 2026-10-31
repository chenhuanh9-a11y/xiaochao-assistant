"""Current compiled binding conflicts; fake chest service, no actual input."""
import unittest
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import test_client_chest_auto_tap as fixtures
import chest_auto_tap as core
import client_chest_auto_tap
import client_input
import client_settings
from client_chest_auto_tap import ChestAutoTapService
from client_input import NativeShortcutBackend
from test_client_input_recovery import Source
from client_settings import SettingsService, ROOT


class ChestBindingRecoveryTests(unittest.TestCase):
    def setUp(self):
        source_root = Path(__file__).resolve().parents[2]
        for module in (core, client_chest_auto_tap, client_input, client_settings):
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(source_root), module.__file__)
        self.fixture = fixtures.ChestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.input, self.adapter = self.fixture.input, self.fixture.adapter

    def test_non_g_map_and_g_trigger_follow_confirm_round_binding_and_unbinding(self):
        self.input.snapshot["bindings"] = {"map": "M", "confirm-round": "G", "escape": "ESCAPE"}
        result = self.adapter.update(True, "G", "F", "90")
        self.assertFalse(result["success"])
        self.assertIn("确认本局", result["message"])
        self.assertNotIn("confirm-round", result["message"])
        for replacement in ("F8", ""):
            self.input.snapshot["bindings"]["confirm-round"] = replacement
            self.input.changed.emit()
            self.assertTrue(self.adapter.update(True, "G", "F", "90")["success"])
            self.assertTrue(self.adapter.snapshot["running"])
            self.input.snapshot["bindings"]["confirm-round"] = "G"
            self.input.changed.emit()
            self.assertFalse(self.adapter.snapshot["running"])
            self.assertFalse(self.adapter.snapshot["enabled"])
            self.assertIn("确认本局", self.adapter.snapshot["message"])

    def test_compiled_rebinding_disarms_active_conflict_without_saved_signal(self):
        self.input.snapshot["bindings"] = {"map": "M", "confirm-round": "F8"}
        self.assertTrue(self.adapter.update(True, "G", "F", "90")["success"])
        self.input.snapshot["bindings"]["confirm-round"] = "G"
        self.input.changed.emit()
        self.assertFalse(self.adapter.snapshot["running"])
        self.assertFalse(self.adapter.snapshot["enabled"])


class RealChestConflictRecoveryTests(unittest.TestCase):
    """Real persistence/validation + Qt adapter; sync and native input replaced."""

    def setUp(self):
        source_root = Path(__file__).resolve().parents[2]
        for module in (core, client_chest_auto_tap, client_input, client_settings):
            self.assertTrue(Path(module.__file__).resolve().is_relative_to(source_root), module.__file__)
        self.fixture = fixtures.ChestTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.adapter.shutdown()
        self.addCleanup(patch.stopall)
        patch.object(core, "_user32", None).start()
        self.send = patch.object(core, "_send_key", side_effect=AssertionError("native input forbidden")).start()
        self.source = Source()
        self.keys = NativeShortcutBackend(SimpleNamespace(is_window=lambda hwnd: hwnd == 42,
            foreground_window=lambda: 42, window_process_id=lambda hwnd: 700), source=self.source)
        self.addCleanup(self.keys.shutdown)
        self.values = dict(hotkey="C", actionHotkeys=["F6", "", "", "G", "F5", "F4", "F8", "F3", "F9"])
        self.configure("G")
        path = Path(self.fixture.temp.name) / "chest-auto-tap.json"
        path.write_text(json.dumps(dict(schema=core.SCHEMA, enabled=True, holdKey="G", tapKey="F",
                                        intervalMs=90)), encoding="utf-8")
        def service_factory(*args, **kwargs):
            service = core.ChestAutoTap(*args, **kwargs)
            service.available = True
            service.sync = Mock()  # No worker or key reads/sends, even when enabled.
            return service
        self.adapter = ChestAutoTapService(SimpleNamespace(settings_path=path.with_name("settings.json")),
            self.fixture.owner, self.keys, self.fixture.settings,
            service_factory=service_factory, native=self.fixture.native)
        self.addCleanup(self.adapter.shutdown)
        self.service = self.adapter._service

    def configure(self, confirm):
        self.values["actionHotkeys"][3] = confirm
        self.assertTrue(self.keys.configureSettings(self.values))
        self.keys.bindWindow(42)
        self.assertTrue(self.keys.enable())
        self.source.finish_start()

    def test_saved_unbound_confirm_clears_old_conflict_and_allows_explicit_g_enable(self):
        self.assertTrue(self.service.snapshot()["error"])
        self.assertFalse(self.service.snapshot()["enabled"])
        self.configure("")  # Screenshot-equivalent saved keys; no default substitution.
        self.fixture.settings.saved.emit()
        self.assertEqual(self.keys.snapshot["bindings"]["confirm-round"], "")
        self.assertEqual(self.service.snapshot()["error"], "")
        self.assertFalse(self.adapter.snapshot["enabled"])
        self.assertTrue(self.adapter.update(True, "G", "F", "90")["success"])
        self.adapter.pollOnce()
        self.assertTrue(self.adapter.snapshot["running"])
        self.assertEqual(self.service.snapshot()["error"], "")
        self.configure("G")
        self.assertFalse(self.adapter.snapshot["running"])
        self.assertIn("确认本局", self.service.snapshot()["error"])
        self.send.assert_not_called()

    def test_persisted_screenshot_bindings_do_not_refill_g_or_keep_old_reservation(self):
        # Use the real settings writer/reader, key compiler and reservation
        # consumer. Only native hook installation and input worker are replaced.
        (ROOT / "work").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="chest-binding-", dir=ROOT / "work") as name:
            sandbox = Path(name)
            (sandbox / ".qt-lab-owner").write_text("qt-quick-isolated-v1", encoding="utf-8")
            settings = SettingsService(sandbox)
            try:
                keys = ["F6", "", "", "", "F5", "F4", "F8", "F3", "F9"]
                self.assertTrue(settings.setValue("hotkey", "C"))
                self.assertTrue(settings.setValue("actionHotkeys", keys))
                self.assertTrue(settings.save())
                deadline = time.monotonic() + 5
                while settings.busy and time.monotonic() < deadline:
                    fixtures.APP.processEvents()
                    time.sleep(.002)
                self.assertFalse(settings.busy)
                self.assertTrue(settings.reload())
                self.assertEqual(settings.persistedValues["actionHotkeys"], keys)
                self.assertTrue(self.keys.configureSettings(settings.persistedValues))
                self.assertTrue(self.keys.enable())
                self.source.finish_start()
                self.assertEqual(self.keys.snapshot["bindings"]["confirm-round"], "")
                self.fixture.settings.saved.emit()
                self.assertNotIn(ord("G"), self.adapter._reserved)
                self.assertEqual(self.service.snapshot()["error"], "")
                self.assertTrue(self.adapter.update(True, "G", "F", "90")["success"])
                self.assertTrue(self.adapter.snapshot["running"])
                self.send.assert_not_called()
            finally:
                settings.shutdown()

    def test_valid_key_change_clears_only_configuration_fault(self):
        self.service._fault("发送按键失败（Windows 5）；开箱连点已停用。")
        result = self.service.update(dict(enabled=False, holdKey="F12"))
        self.assertTrue(result["success"])
        state = self.service.snapshot()
        self.assertNotIn("确认本局", state["error"])
        self.assertIn("发送按键失败", state["error"])
        self.assertFalse(state["enabled"])
        self.send.assert_not_called()

    def test_resolved_conflict_preserves_failed_release_and_pending_ledger(self):
        self.service._pending_releases[ord("F")] = 0
        self.send.side_effect = None
        self.send.return_value = 5
        self.service._release_pending()  # Fake key-up result, never calls Windows.
        before = dict(self.service._pending_releases)
        self.configure("")
        self.fixture.settings.saved.emit()
        state = self.service.snapshot()
        self.assertNotIn("确认本局", state["error"])
        self.assertIn("释放按键", state["error"])
        self.assertEqual(self.service._pending_releases, before)
        self.assertEqual(state["pendingReleaseVks"], [ord("F")])
        self.assertFalse(self.service.update(dict(enabled=True))["success"])
        self.send.assert_called_once_with(ord("F"), up=True)


if __name__ == "__main__":
    unittest.main()
