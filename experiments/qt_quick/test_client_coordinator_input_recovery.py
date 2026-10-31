"""Real discovery/settings entry points with fake catalog, native API and hook."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from client_coordinator import ClientCoordinator
from client_input import NativeShortcutBackend
import test_client_coordinator as fixtures
from test_client_input_recovery import Source


class CoordinatorInputRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.CoordinatorTests.setUpClass()
        self.fixture = fixtures.CoordinatorTests()
        self.fixture.setUp()
        self.fixture.tearDown()  # Retain service fixtures, replace the coordinator.
        f = self.fixture
        f.settings.persistedValues.update(hotkey="C",
            actionHotkeys=["F6", "", "", "", "F5", "F4", "F8", "F3", "F9"])
        f.session.snapshot.update(roundActive=False, mapOpen=False)
        f.session.stop = Mock()
        self.source = Source()
        self.native = SimpleNamespace(is_window=lambda hwnd: hwnd == 42,
            foreground_window=lambda: 42, window_process_id=lambda hwnd: 700)
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.addCleanup(self.keys.shutdown)
        self.keys.configureSettings(f.settings.persistedValues)
        self.keys.bindWindow(42)
        self.keys.enableManagement()
        self.keys.enable()
        self.row = dict(hwnd="42", pid=700, processCreateTime=1,
                        executable="fixture/dwrg.exe", width=100, height=80)
        self.catalog = Mock(return_value=[self.row])
        guard = patch("client_coordinator.another_assistant_running", return_value=False)
        self.other_client = guard.start()
        self.addCleanup(guard.stop)
        with patch("client_input.NativeWindowAPI", return_value=self.native):
            self.c = f.coordinator = ClientCoordinator(backend=f.backend, settings=f.settings,
                resources=f.resources, session=f.session, windows=f.windows, native_input=self.keys,
                capture=SimpleNamespace(disable=Mock()), runtime=f.runtime,
                images=f.images, window_catalog=self.catalog)
        self.addCleanup(f.tearDown)
        self.c._foreground_timer.stop()
        self.c._tracking_timer.stop()
        self.addCleanup(self.c._discovery_timer.stop)
        self.addCleanup(self.c._startup_timer.stop)
        self.c._automatic_live = True
        self.c._bound = "42"
        self.c._binding_identity = self.c._window_identity(self.row)
        self.c._window_rows = [self.row]

    def test_discovery_recovers_same_hwnd_without_new_capture_binding(self):
        self.source.finish_start()
        self.source.crash()
        self.assertFalse(self.keys.enabled)
        self.c._discover_game()
        self.assertEqual(self.source.starts, 2)
        self.assertFalse(self.keys.enabled)  # Thread start alone is insufficient.
        self.source.finish_start()
        self.assertTrue(self.keys.enabled)
        self.assertEqual(self.c.boundWindow, "42")
        self.fixture.session.dispatchWithPayload.assert_not_called()
        self.other_client.assert_called_once_with()

    def test_discovery_preserves_explicit_disable_and_other_client_guard(self):
        self.source.finish_start()
        self.c.setInputEnabled(False)
        self.source.crash()
        self.other_client.return_value = True
        self.c._discover_game()
        self.assertEqual(self.source.starts, 1)
        self.catalog.assert_not_called()
        self.other_client.return_value = False
        self.c._discover_game()
        self.source.finish_start()
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot["gameRequested"])
        self.assertTrue(self.keys.snapshot["managementEnabled"])
        self.assertEqual(self.source.starts, 2)

    def test_settings_save_during_start_keeps_pending_game_intent(self):
        self.assertFalse(self.keys.enabled)
        self.assertTrue(self.keys.snapshot["gameRequested"])
        self.fixture.settings.persistedValues["hotkey"] = "M"
        self.fixture.settings.saved.emit()
        self.assertTrue(self.keys.snapshot["gameRequested"])
        self.assertFalse(self.keys.enabled)
        self.source.finish_start()
        self.assertTrue(self.keys.enabled)
        self.assertEqual(self.keys.snapshot["bindings"]["map"], "M")


if __name__ == "__main__":
    unittest.main()
