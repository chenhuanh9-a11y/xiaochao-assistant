"""Qt service behavior with fake registry/native boundaries, no real OS changes."""
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app"), str(ROOT / "experiments/qt_quick")]
try:
    from client_game_startup import GameStartupService
except ImportError:
    GameStartupService = None


@unittest.skipIf(GameStartupService is None, "PySide6 is not installed")
class GameStartupServiceTests(unittest.TestCase):
    def service(self, same_user=True):
        native = types.SimpleNamespace(interactive_user_matches=lambda: same_user,
                                       games=lambda: [(123, 1000)], session=7)
        self.addCleanup(patch.stopall)
        patch("client_game_startup.os", types.SimpleNamespace(name="nt")).start()
        patch.dict(sys.modules, game_startup_win32=types.SimpleNamespace(Windows=lambda: native)).start()
        patch("client_game_startup.enabled", return_value=False).start()
        return GameStartupService(".", production=True)

    def test_other_administrator_account_cannot_touch_hkcu(self):
        with patch("client_game_startup.set_enabled") as setter, patch("client_game_startup.suppress_games") as suppress:
            service = self.service(same_user=False)
            self.assertFalse(service.available)
            self.assertFalse(service.setEnabled(True))
            self.assertFalse(service.setEnabled(False))
            self.assertTrue(service.suppressCurrentGame())
            setter.assert_not_called()
            suppress.assert_not_called()

    def test_explicit_exit_records_identity_for_same_user(self):
        service = self.service()
        service._enabled = True
        with patch("client_game_startup.suppress_games") as suppress:
            self.assertTrue(service.suppressCurrentGame())
            suppress.assert_called_once_with(service.directory, [(123, 1000)], 7)

    def test_exit_record_failure_attempts_disable_and_reports_failure(self):
        service = self.service()
        service._enabled = True
        with patch("client_game_startup.suppress_games", side_effect=OSError("blocked")), patch("client_game_startup.set_enabled") as setter:
            self.assertFalse(service.suppressCurrentGame())
            setter.assert_called_once_with(service.directory, False)

    def test_default_off_explicit_exit_does_not_write_registry(self):
        service = self.service()
        with patch("client_game_startup.suppress_games") as suppress, patch("client_game_startup.set_enabled") as setter:
            self.assertTrue(service.suppressCurrentGame())
            suppress.assert_not_called()
            setter.assert_not_called()

    def test_failed_registration_does_not_claim_enabled(self):
        service = self.service()
        with patch.object(Path, "is_file", return_value=True), patch("client_game_startup.set_enabled", side_effect=OSError("blocked")):
            self.assertFalse(service.setEnabled(True))
            self.assertFalse(service.enabled)


if __name__ == "__main__":
    unittest.main()
