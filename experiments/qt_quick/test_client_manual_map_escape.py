"""Internal Esc ownership with fake OS input; no hook registration or game."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from client_input import NativeShortcutBackend
from test_client_manual_map import APP, Native
from test_client_shortcut_delivery import FixtureSource


class PickerEscapeTests(unittest.TestCase):
    def run_case(self, hook):
        native = Native()
        source = FixtureSource() if hook else None
        keys = NativeShortcutBackend(native, source=source)
        self.addCleanup(keys.shutdown)
        actions, picks = [], []
        keys.actionTriggered.connect(actions.append)
        self.assertTrue(keys.configure({"map": "G", "candidate-next": "F1",
                                        "end-round": "Ctrl+F1"}))
        keys.enableManagement()
        keys.bindWindow(42)
        self.assertTrue(keys.enable())
        keys.pollOnce() if not hook else None

        def press(vk, down=True):
            if hook:
                event = source._transition_event(vk, 0, 0, down, 42)
                source.eventReceived.emit(event)
                APP.processEvents()
            else:
                native.states[vk] = 0x8001 if down else 0
                keys.pollOnce()

        def opened():
            keys.setPickerKeys(["Escape", "F1"])
            if not hook:
                keys.pollOnce()

        def close(name):
            picks.append(name)
            keys.setPickerKeys([])  # Real service closes synchronously.
        keys.pickerKeyRequested.connect(close)
        opened()
        press(0x1b)
        self.assertEqual(picks, ["Escape"])
        self.assertEqual(actions, [])
        for _ in range(3):
            press(0x1b)
        self.assertEqual(actions, [])
        opened()  # Reopen while Esc is still held.
        press(0x1b)
        self.assertEqual(picks, ["Escape"])
        self.assertEqual(actions, [])
        keys.setPickerKeys([])  # External close while held.
        press(0x1b)
        self.assertEqual(actions, [])
        press(0x1b, False)
        press(0x1b)
        self.assertEqual(actions, ["escape"])
        opened()  # Open during an ordinary Esc hold.
        press(0x1b)
        self.assertEqual(actions, ["escape"])
        self.assertEqual(picks, ["Escape"])
        press(0x1b, False)
        press(0x11)
        press(0x1b)
        self.assertEqual(picks, ["Escape"])
        self.assertEqual(actions, ["escape"])
        press(0x1b, False)
        press(0x70)
        press(0x70, False)
        self.assertEqual(actions, ["escape", "end-round"])
        press(0x11, False)
        press(0x70)
        self.assertEqual(picks, ["Escape", "F1"])
        self.assertEqual(actions, ["escape", "end-round"])

    def test_polling_escape_ownership_and_held_transitions(self):
        self.run_case(False)

    def test_native_escape_ownership_and_held_transitions(self):
        self.run_case(True)

    def test_polling_open_with_unsampled_escape_does_not_leak_action(self):
        native = Native()
        keys = NativeShortcutBackend(native)
        self.addCleanup(keys.shutdown)
        keys.configure({"map": "G"})
        keys.bindWindow(42)
        keys.enable()
        keys.pollOnce()
        actions, picks = [], []
        keys.actionTriggered.connect(actions.append)
        keys.pickerKeyRequested.connect(picks.append)
        native.states[0x1b] = 0x8001
        keys.setPickerKeys(["Escape"])
        keys.pollOnce()
        self.assertEqual((actions, picks), ([], []))
