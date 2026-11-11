"""Pure Qt-client setting decoding contracts; no settings file is written."""
from pathlib import Path
import subprocess
import sys
import unittest

QT_QUICK_ROOT = Path(__file__).resolve().parents[1] / "experiments" / "qt_quick"
if str(QT_QUICK_ROOT) not in sys.path:
    sys.path.insert(0, str(QT_QUICK_ROOT))

from client_settings_values import ACTION_ORDER, FRESH_ACTIONS, LEGACY_ACTIONS, decode_settings


class SettingsValueTests(unittest.TestCase):
    def test_missing_action_hotkeys_keep_fresh_and_existing_defaults_distinct(self):
        fresh, fresh_invalid = decode_settings({}, existing_file=False)
        existing, existing_invalid = decode_settings({}, existing_file=True)

        self.assertEqual(fresh_invalid, {})
        self.assertEqual(existing_invalid, {})
        self.assertEqual(fresh["actionHotkeys"], FRESH_ACTIONS)
        self.assertEqual(existing["actionHotkeys"], [*LEGACY_ACTIONS, ""])
        self.assertEqual(len(existing["actionHotkeys"]), len(ACTION_ORDER))

    def test_nine_slot_shortcuts_gain_only_an_unbound_tenth_slot(self):
        stored = [f"F{index}" for index in range(1, 10)]
        values, invalid = decode_settings({"actionHotkeys": stored}, existing_file=True)

        self.assertEqual(invalid, {})
        self.assertEqual(values["actionHotkeys"], [*stored, ""])

    def test_shortcut_conflict_and_bad_picker_tail_remain_invalid(self):
        conflicting = ["G", "", "", "", "", "", "", "", "", ""]
        values, invalid = decode_settings({"hotkey": "G", "actionHotkeys": conflicting}, existing_file=True)
        self.assertIn("hotkey/actionHotkeys", invalid)
        self.assertEqual(values["hotkey"], "G")
        self.assertEqual(values["actionHotkeys"], conflicting)

        bad_tail = [*[f"F{index}" for index in range(1, 10)], "not-a-shortcut"]
        values, invalid = decode_settings({"actionHotkeys": bad_tail}, existing_file=True)
        self.assertIn("hotkey/actionHotkeys", invalid)
        self.assertEqual(values["actionHotkeys"][-1], "")

    def test_legacy_opacity_and_route_profile_context_snapshot(self):
        values, invalid = decode_settings({
            "sheetOpacity": .72,
            "routeProfileId": "legacy-global",
            "modeRouteProfiles": {"nightmare": "legacy-nightmare", "difficult": "legacy-difficult"},
            "contextRouteProfiles": {"s0:nightmare": "explicit-s0", "s1:difficult": "explicit-s1"},
        }, existing_file=True)

        self.assertEqual(invalid, {})
        self.assertEqual(values["sheetOpacity"], .72)
        self.assertEqual(values["referenceOpacity"], .72)
        self.assertEqual(values["contextRouteProfiles"]["s0:nightmare"], "explicit-s0")
        self.assertEqual(values["contextRouteProfiles"]["s0:difficult"], "legacy-difficult")
        self.assertEqual(values["contextRouteProfiles"]["s1:difficult"], "explicit-s1")
        self.assertNotIn("s1:nightmare", values["contextRouteProfiles"])

    def test_core_imports_when_qt_and_tk_imports_are_blocked(self):
        script = r'''
import importlib.abc
import sys

class BlockUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "tkinter" or fullname.startswith("tkinter."):
            raise AssertionError("Tk import attempted: " + fullname)
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise AssertionError("Qt import attempted: " + fullname)
        return None

sys.meta_path.insert(0, BlockUI())
sys.path.insert(0, sys.argv[1])
import client_settings_values
assert callable(client_settings_values.decode_settings)
'''
        result = subprocess.run([sys.executable, "-c", script, str(QT_QUICK_ROOT)],
                                cwd=QT_QUICK_ROOT.parent.parent, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
