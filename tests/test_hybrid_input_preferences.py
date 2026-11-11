"""Product shortcut decoding over isolated files, without Qt or native input."""
import json
from pathlib import Path
import tempfile
import unittest

from input_preferences import read_input_preferences


class InputPreferencesTests(unittest.TestCase):
    def test_fresh_legacy_and_stored_custom_bindings_keep_product_meaning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fresh = read_input_preferences(root)
            self.assertTrue(fresh["available"])
            rows = {row["action"]: row for row in fresh["bindings"]}
            self.assertEqual(rows["toggle-panel"]["vk"], 0x78)  # Fresh F9
            self.assertFalse((root / "ui-settings.json").exists())
            path = root / "ui-settings.json"
            path.write_text("{}", encoding="utf-8")
            legacy = {row["action"]: row for row in read_input_preferences(root)["bindings"]}
            self.assertEqual(legacy["toggle-panel"]["vk"], 0x74)  # Existing F5
            source = dict(hotkey="Ctrl+M", actionHotkeys=["F9", "", "", "", "", "", "F11", "", "F10", ""])
            encoded = json.dumps(source).encode()
            path.write_bytes(encoded)
            custom = {row["action"]: row for row in read_input_preferences(root)["bindings"]}
            self.assertEqual((custom["map"]["vk"], custom["map"]["modifiers"]), (ord("M"), [0x11]))
            self.assertEqual(path.read_bytes(), encoded)

    def test_invalid_preferences_disable_bindings_without_repairing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ui-settings.json"
            for raw in ('{"hotkey":"not-a-key"}', '{"hotkey":"F9"}', '[]', '{broken', '{"x":NaN}'):
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8")
                    result = read_input_preferences(path.parent)
                    self.assertFalse(result["available"], result)
                    self.assertEqual(result["bindings"], [])
                    self.assertEqual(path.read_text(encoding="utf-8"), raw)

    def test_mouse_and_bare_modifier_bindings_are_explicit_keyboard_gaps(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ui-settings.json"
            for key in ("ALT", "MOUSE4"):
                path.write_text(json.dumps(dict(hotkey=key, actionHotkeys=[""] * 10)), encoding="utf-8")
                result = read_input_preferences(path.parent)
                self.assertTrue(result["available"], result)
                row = next(row for row in result["bindings"] if row["action"] == "map")
                self.assertFalse(row["keyboardSupported"])
