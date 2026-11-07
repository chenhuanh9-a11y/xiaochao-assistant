"""Installer control with fake registry, clock, and process enumeration only."""
import ntpath
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from game_startup_watcher import control
from game_startup_win32 import helper_process_state
from game_startup import WATCHER_NAME


class ControlTests(unittest.TestCase):
    def native(self, states=("absent",), same_user=True):
        return SimpleNamespace(interactive_user_matches=lambda: same_user,
            watcher_state=Mock(side_effect=states))

    def test_status_is_read_only_and_distinguishes_exact_opt_in(self):
        for flag, code in ((False, 0), (True, 10)):
            with patch("game_startup_watcher.enabled", return_value=flag), patch("game_startup_watcher.set_enabled") as setter:
                self.assertEqual(control("--status", Path("."), self.native()), code)
                setter.assert_not_called()

    def test_disable_waits_for_confirmed_absence_not_elapsed_sleep_alone(self):
        now = [0.]
        def sleep(delay): now[0] += delay
        native = self.native(("present", "unknown", "absent"))
        with patch("game_startup_watcher.enabled", return_value=False), patch("game_startup_watcher.set_enabled") as setter:
            self.assertEqual(control("--disable", Path("."), native, clock=lambda:now[0], sleep=sleep), 0)
            setter.assert_called_once_with(Path("."), False)
        self.assertEqual(native.watcher_state.call_count, 3)
        self.assertGreater(now[0], 0)

    def test_unknown_and_live_timeout_fail_closed_without_killing(self):
        now = [0.]
        def sleep(delay): now[0] += 1
        native = self.native(); native.watcher_state = Mock(return_value="unknown")
        with patch("game_startup_watcher.enabled", return_value=False), patch("game_startup_watcher.set_enabled"):
            self.assertEqual(control("--disable", Path("."), native, clock=lambda:now[0], sleep=sleep), 6)

    def test_account_mismatch_and_registration_errors_never_claim_success(self):
        with patch("game_startup_watcher.set_enabled") as setter:
            self.assertEqual(control("--disable", Path("."), self.native(same_user=False)), 5)
            setter.assert_not_called()
        with patch("game_startup_watcher.set_enabled", side_effect=OSError("denied")):
            self.assertEqual(control("--disable", Path("."), self.native()), 4)

    def test_restore_verifies_written_state(self):
        for enabled, code in ((True, 0), (False, 4)):
            with patch("game_startup_watcher.enabled", return_value=enabled), patch("game_startup_watcher.set_enabled") as setter:
                self.assertEqual(control("--restore-after-upgrade", Path("."), self.native()), code)
                setter.assert_called_once_with(Path("."), True)

    def test_unknown_command_has_no_side_effects(self):
        native = self.native()
        with patch("game_startup_watcher.set_enabled") as setter:
            self.assertEqual(control("--enable", Path("."), native), 2)
            setter.assert_not_called()


class EnumerationPolicyTests(unittest.TestCase):
    expected = ntpath.normcase(r"C:\Apps\Xingtu\XingtuGameWatcher.exe")
    def test_own_onefile_parent_and_child_are_excluded(self):
        rows = [(8, 7, WATCHER_NAME), (7, 6, WATCHER_NAME)]
        self.assertEqual(helper_process_state(rows, self.expected, 8, lambda pid:(self.expected, 10)), "absent")

    def test_any_other_exact_helper_blocks_replacement_including_other_sessions(self):
        rows = [(8, 7, WATCHER_NAME), (7, 6, WATCHER_NAME), (99, 1, WATCHER_NAME)]
        self.assertEqual(helper_process_state(rows, self.expected, 8, lambda pid:(self.expected, 10)), "present")

    def test_different_install_is_not_targeted(self):
        rows = [(8, 7, WATCHER_NAME), (99, 1, WATCHER_NAME)]
        self.assertEqual(helper_process_state(rows, self.expected, 8, lambda pid:(r"C:\Other\XingtuGameWatcher.exe", 10)), "absent")

    def test_unreadable_candidate_is_unknown_but_vanished_pid_is_absent(self):
        rows = [(99, 1, WATCHER_NAME)]
        for code, expected in ((5, "unknown"), (87, "absent")):
            error = OSError("test"); error.winerror = code
            self.assertEqual(helper_process_state(rows, self.expected, 8, Mock(side_effect=error)), expected)


if __name__ == "__main__": unittest.main()

class BuildContractTests(unittest.TestCase):
    def test_separate_minimal_onefile_spec_and_precise_manifest_check(self):
        root = Path(__file__).resolve().parents[1]
        spec = (root / "tools/qt-game-watcher.spec").read_text()
        self.assertIn('app/game_startup_watcher.py', spec)
        self.assertIn('a.binaries, a.datas', spec)
        self.assertIn('uac_admin=False', spec)
        self.assertIn('uac_uiaccess=False', spec)
        self.assertIn('verify_invoker_manifest(exe.name)', spec)
        self.assertNotIn('COLLECT(', spec)

    def test_install_lifecycle_never_enables_without_prior_exact_opt_in(self):
        root = Path(__file__).resolve().parents[1]
        code = (root / "tools/qt-game-watcher-lifecycle.iss").read_text()
        self.assertIn('GameWatcherWasEnabled := Code = 10', code)
        self.assertIn('if not GameWatcherRestorePending then exit', code)
        self.assertIn('if GameWatcherWasEnabled then GameWatcherRestorePending := True', code)
        self.assertIn('CompareText(GameWatcherOriginalDir', code)
        self.assertNotIn('taskkill', code.lower())
