"""Listener lifecycle regression; fake native metadata/events, no OS listener."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
import unittest
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from client_input import NativeShortcutBackend
from client_native_keys import PassThroughKeyboardSource

APP = QGuiApplication.instance() or QGuiApplication([])


class Source(QObject):
    eventReceived = Signal(object)
    started = Signal(object)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = self.installed = False
        self.held = set()
        self.generation = self.starts = 0
        self.last_error = ""

    def update_keys(self, keys):
        self.keys = set(keys)
        self.generation += 1

    def update_pointer_target(self, hwnd):
        self.target = hwnd

    def start(self):
        if not self.running:
            self.starts += 1
            self.generation += 1
            self.running = True
            self.last_error = ""
        return True

    def finish_start(self):
        self.installed = True
        self.started.emit(dict(held=frozenset(self.held), generation=self.generation))
        APP.processEvents()

    def stop(self):
        self.running = self.installed = False
        return True

    def crash(self):
        self.running = self.installed = False
        self.last_error = "synthetic hook failure"
        self.failed.emit(self.last_error)
        APP.processEvents()

    def edge(self, vk, down=True, foreground=42, generation=None):
        self.eventReceived.emit(dict(vk=vk, down=down, foreground=foreground,
            held=frozenset([vk] if down else []),
            createdAt=time.monotonic(),
            generation=self.generation if generation is None else generation))
        APP.processEvents()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.foreground, self.valid = 42, {42, 43}
        self.source = Source()
        self.native = SimpleNamespace(is_window=lambda hwnd: hwnd in self.valid,
            foreground_window=lambda: self.foreground, window_process_id=lambda hwnd: 999999)
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.events = []
        self.keys.actionTriggered.connect(self.events.append)
        self.keys.configure({"map": "G", "toggle-panel": "F9"})
        self.addCleanup(self.keys.shutdown)

    def arm(self):
        self.assertTrue(self.keys.enableManagement())
        self.keys.bindWindow(42)
        self.assertTrue(self.keys.enable())
        self.source.finish_start()

    def test_thread_start_is_not_listener_installation(self):
        self.keys.bindWindow(42)
        self.assertTrue(self.keys.enable())
        self.assertFalse(self.keys.enabled)
        self.assertEqual(self.keys.snapshot["status"], "starting")
        self.source.finish_start()
        self.assertTrue(self.keys.enabled)

    def test_same_hwnd_recovers_failed_observer_without_rebinding(self):
        self.arm()
        self.source.crash()
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot["managementEnabled"])
        self.assertTrue(self.keys.recoverListening())
        self.assertEqual(self.source.starts, 2)
        self.source.finish_start()
        self.source.edge(ord("G"))
        self.source.edge(0x78, foreground=77)
        self.assertEqual(self.events, ["map", "toggle-panel"])

    def test_recovery_does_not_revive_disabled_game_or_invalid_bindings(self):
        self.arm()
        self.keys.disable()
        self.source.crash()
        self.keys.recoverListening()
        self.source.finish_start()
        self.source.edge(ord("G"))
        self.source.edge(0x78, foreground=77)
        self.assertEqual(self.events, ["toggle-panel"])
        self.assertFalse(self.keys.configure({"map": "G", "toggle-panel": "G"}))
        self.source.crash()
        count = self.source.starts
        self.assertFalse(self.keys.recoverListening())
        self.assertEqual(self.source.starts, count)

    def test_late_bind_rebind_and_foreground_are_still_explicit(self):
        self.keys.enableManagement(); self.source.finish_start()
        self.keys.bindWindow(42); self.keys.enable()
        self.keys.bindWindow(0)
        self.source.crash(); self.keys.recoverListening(); self.source.finish_start()
        self.assertFalse(self.keys.enabled)
        self.keys.bindWindow(43); self.keys.enable()
        self.foreground = 43
        self.source.edge(ord("G"), foreground=42)
        self.source.edge(ord("G"), False, foreground=42)
        self.source.edge(ord("G"), foreground=43)
        self.assertEqual(self.events, ["map"])

    def test_restart_rejects_old_generation_and_drops_old_held_edges(self):
        self.arm()
        self.source.edge(ord("G"))
        old = self.source.generation
        self.source.crash(); self.keys.recoverListening(); self.source.finish_start()
        self.source.edge(ord("G"), generation=old)
        self.source.edge(ord("G"))
        self.assertEqual(self.events, ["map", "map"])

    def test_shutdown_and_late_started_signal_cannot_restore_listener(self):
        self.arm()
        self.keys.shutdown()
        count = self.source.starts
        self.assertFalse(self.keys.recoverListening())
        self.source.started.emit(frozenset()); APP.processEvents()
        self.assertEqual(self.source.starts, count)
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot["managementEnabled"])

    def test_invalid_settings_shape_revokes_pending_start_intent(self):
        self.keys.enableManagement()
        self.keys.bindWindow(42)
        self.keys.enable()
        self.assertFalse(self.keys.configureSettings(dict(hotkey="C", actionHotkeys=["F6"])))
        self.source.finish_start()
        self.assertFalse(self.keys.enabled)
        self.assertFalse(self.keys.snapshot["managementEnabled"])
        self.assertFalse(self.keys.snapshot["gameRequested"])
        self.assertFalse(self.keys.snapshot["managementRequested"])
        self.assertFalse(self.keys.recoverListening())

    def test_source_start_does_not_claim_stopping_thread_is_available(self):
        source = PassThroughKeyboardSource()
        source._thread = SimpleNamespace(is_alive=lambda: True)
        source._stop.set()
        self.assertFalse(source.start())


if __name__ == "__main__":
    unittest.main()
