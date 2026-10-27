"""开箱连点 settings and worker loop, with Windows input calls replaced."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import chest_auto_tap  # noqa: E402
from chest_auto_tap import ChestAutoTap, validate  # noqa: E402

BASE = dict(enabled=True, tapKey="F", holdKey="F12", intervalMs=90)


class ValidateTests(unittest.TestCase):
    def test_accepts_defaults(self):
        self.assertEqual(validate(BASE, {}), BASE)

    def test_rejects_same_key_and_reserved(self):
        with self.assertRaisesRegex(ValueError, "不能和连发按键相同"):
            validate(dict(BASE, holdKey="F"), {})
        with self.assertRaisesRegex(ValueError, "星图快捷键"):
            validate(BASE, {0x7B: "星图快捷键"})
        with self.assertRaisesRegex(ValueError, "毫秒"):
            validate(dict(BASE, intervalMs="5"), {})
        with self.assertRaisesRegex(ValueError, "连发按键"):
            validate(dict(BASE, tapKey="鼠标侧键 1"), {})

    def test_mouse_trigger_allowed(self):
        self.assertEqual(validate(dict(BASE, holdKey="鼠标侧键 2"), {})["holdKey"], "鼠标侧键 2")


class StoreTests(unittest.TestCase):
    def test_default_trigger_avoids_app_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            tapper = ChestAutoTap(Path(folder) / "c.json", target_window=lambda: None,
                                  reserved=lambda: {0x77: "x"})  # F8 taken
            self.assertEqual(tapper.settings["holdKey"], "F12")

    def test_update_persists_and_rejects(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "c.json"
            tapper = ChestAutoTap(path, target_window=lambda: None)
            tapper.available = False
            self.assertFalse(tapper.update(dict(BASE))["success"])  # Windows only
            self.assertTrue(tapper.update(dict(BASE, enabled=False, tapKey="E", intervalMs=150))["success"])
            reloaded = ChestAutoTap(path, target_window=lambda: None)
            self.assertEqual((reloaded.settings["tapKey"], reloaded.settings["intervalMs"]), ("E", 150))


class WorkerTests(unittest.TestCase):
    def test_taps_only_while_held_and_in_game(self):
        events, state = [], dict(held=False, foreground=True)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(chest_auto_tap, "key_down", lambda vk: state["held"]), \
                mock.patch.object(chest_auto_tap, "same_top_window", lambda a, b: state["foreground"]), \
                mock.patch.object(chest_auto_tap, "foreground_window", lambda: 1), \
                mock.patch.object(chest_auto_tap, "_send_key", lambda vk, up: events.append((vk, up)) or 0), \
                mock.patch.object(chest_auto_tap, "PRESS_MS", 10):
            tapper = ChestAutoTap(Path(folder) / "c.json", target_window=lambda: 1)
            tapper.available = True
            self.assertTrue(tapper.update(dict(BASE, intervalMs=20))["success"])
            time.sleep(.1)
            self.assertEqual(events, [])  # Not held.
            state["held"] = True
            time.sleep(.2)
            state["held"] = False
            time.sleep(.05)
            taps = len(events)
            self.assertGreaterEqual(taps, 6)
            self.assertEqual(events[::2], [(ord("F"), False)] * (taps // 2))
            self.assertEqual(events[1::2], [(ord("F"), True)] * (taps // 2))
            state.update(held=True, foreground=False)
            time.sleep(.1)
            self.assertEqual(len(events), taps)  # Game not in front.
            self.assertIn("前台", tapper.snapshot()["status"])
            tapper.update(dict(BASE, enabled=False))
            self.assertIsNone(tapper._thread)


if __name__ == "__main__":
    unittest.main(verbosity=2)
