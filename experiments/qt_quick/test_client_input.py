"""Fake native API tests only: no real key reads, registration, or capture."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import unittest

import numpy as np
from PySide6.QtGui import QGuiApplication

from client_input import NativeShortcutBackend
from client_capture import CaptureUnavailable, ClientCaptureProvider
from client_region_capture import CaptureContextChanged, ClientCaptureContext

APP = QGuiApplication.instance() or QGuiApplication([])


class Native:
    def __init__(self):
        self.valid = True
        self.foreground = 42
        self.process = 9001
        self.states = {}
        self.reads = []
        self.calls = []
        self.failure = False

    def is_window(self, hwnd):
        self.calls.append(("is_window", hwnd))
        if self.failure:
            raise OSError("fixture native failure")
        return self.valid and hwnd == 42

    def foreground_window(self): return self.foreground
    def window_process_id(self, hwnd): return self.process
    def key_state(self, vk):
        self.reads.append(vk)
        result = self.states.get(vk, 0)
        self.states[vk] = result & ~1  # Windows' consumable short-press latch.
        return result


class Backend:
    def __init__(self):
        self.current = ClientCaptureContext((100, 80), (10, 20, 110, 100), 96)
        self.captures = []
        self.on_capture = None

    def context(self, hwnd): return self.current
    def capture(self, hwnd, region):
        self.captures.append((hwnd, region))
        if self.on_capture:
            self.on_capture()
        left, top, right, bottom = region
        return np.zeros((bottom-top, right-left, 3), dtype=np.uint8)


class InputTests(unittest.TestCase):
    def setUp(self):
        self.native = Native()
        self.keys = NativeShortcutBackend(self.native)
        self.events = []
        self.keys.actionTriggered.connect(self.events.append)

    def tearDown(self): self.keys.shutdown()

    def arm(self, bindings=None):
        self.assertTrue(self.keys.configure(bindings or {"map": "G", "end-round": "F10"}))
        self.keys.bindWindow(42)
        self.assertTrue(self.keys.enable())
        self.keys.pollOnce()  # Arm/drain without emitting a held key.

    def test_constructor_configure_bind_and_disabled_poll_do_not_read_keys(self):
        self.assertEqual(self.native.calls, [])
        self.keys.configure({"map": "G"})
        self.keys.bindWindow(42)
        self.keys.pollOnce()
        self.assertEqual(self.native.reads, [])
        self.assertFalse(self.keys.enabled)

    def test_requires_configuration_and_explicit_valid_window(self):
        self.assertFalse(self.keys.enable())
        self.assertTrue(self.keys.configure({"map": "G"}))
        self.assertFalse(self.keys.enable())
        self.keys.bindWindow(42)
        self.native.valid = False
        self.assertFalse(self.keys.enable())
        self.assertEqual(self.native.reads, [])

    def test_duplicate_unknown_modifier_conflict_and_alias_contract(self):
        self.assertFalse(self.keys.configure({"map": "Ctrl+G", "end-round": "Control+G"}))
        self.assertEqual(self.keys.snapshot["status"], "conflict")
        self.assertFalse(self.keys.configure({"map": "CTRL", "end-round": "Ctrl+G"}))
        self.assertFalse(self.keys.configure({"invented": "G"}))
        self.assertEqual(self.keys.snapshot["status"], "unknown")
        self.assertTrue(self.keys.configure({"map": "PGUP"}))
        self.assertEqual(self.keys.snapshot["bindings"]["map"], "PAGEUP")
        self.assertFalse(self.keys.configure({"map": "Ctrl+G", "end-round": "Control+G"}))
        self.keys.bindWindow(42)
        self.assertFalse(self.keys.enable())  # Invalid edits cannot revive old bindings.

    def test_level_edges_repeat_short_press_and_no_registration(self):
        self.arm()
        self.native.states[ord("G")] = 0x8001
        self.assertEqual(self.keys.pollOnce(), ["map"])
        self.assertEqual(self.keys.pollOnce(), [])
        self.native.states[ord("G")] = 0
        self.keys.pollOnce()
        self.native.states[ord("G")] = 1
        self.assertEqual(self.keys.pollOnce(), ["map"])
        self.assertEqual(self.events, ["map", "map"])
        self.assertFalse(self.keys.snapshot["globalRegistration"])
        self.assertEqual(self.keys.snapshot["readPermission"], "unknown")

    def test_fixed_escape_close_edge_is_game_foreground_only(self):
        self.arm()
        self.native.states[0x1b] = 0x8001
        self.assertEqual(self.keys.pollOnce(), ["escape"])
        self.assertEqual(self.keys.pollOnce(), [])
        self.native.foreground = 77
        self.native.states[0x1b] = 0
        self.keys.pollOnce()
        self.native.states[0x1b] = 1
        self.assertEqual(self.keys.pollOnce(), [])

    def test_focus_return_held_key_is_rearmed_and_management_stays_available(self):
        self.arm()
        self.native.foreground = 77
        self.native.states[ord("G")] = 0x8001
        self.native.states[0x79] = 0x8001
        self.assertEqual(self.keys.pollOnce(), ["end-round"])
        self.native.foreground = 42
        self.assertEqual(self.keys.pollOnce(), [])
        self.native.states[ord("G")] = 0
        self.keys.pollOnce()
        self.native.states[ord("G")] = 0x8000
        self.assertEqual(self.keys.pollOnce(), ["map"])

    def test_held_at_enable_binding_edit_and_text_entry_are_suppressed(self):
        self.native.states[ord("G")] = 0x8000
        self.arm()
        self.assertEqual(self.events, [])
        for guard in (self.keys.setTextEntryActive, self.keys.setEditingBindings):
            guard(True)
            self.native.states[ord("G")] = 0
            self.keys.pollOnce()
            self.native.states[ord("G")] = 0x8001
            self.assertEqual(self.keys.pollOnce(), [])
            guard(False)
            self.assertEqual(self.keys.pollOnce(), [])
            self.native.states[ord("G")] = 0
            self.keys.pollOnce()
        self.native.states[ord("G")] = 0x8001
        self.assertEqual(self.keys.pollOnce(), ["map"])

    def test_local_toggle_panel_owns_assistant_foreground(self):
        self.arm({"toggle-panel": "F9", "end-round": "F10"})
        self.native.foreground = 77
        self.native.process = os.getpid()
        self.native.states[0x78] = self.native.states[0x79] = 0x8001
        self.assertEqual(self.keys.pollOnce(), ["end-round"])

    def test_shared_primary_sample_extra_modifiers_and_released_latch(self):
        self.arm({"map": "G", "end-round": "Ctrl+G"})
        self.native.reads.clear()
        self.native.states[0x11] = 0x8000
        self.native.states[ord("G")] = 1
        self.assertEqual(self.keys.pollOnce(), ["end-round"])
        self.assertEqual(self.native.reads.count(ord("G")), 1)
        self.native.states[0x11] = 1
        self.native.states[ord("G")] = 1
        self.assertEqual(self.keys.pollOnce(), [])
        self.native.states[0x5b] = 0x8000
        self.native.states[ord("G")] = 1
        self.assertEqual(self.keys.pollOnce(), [])

    def test_native_failure_lost_binding_disable_and_shutdown(self):
        self.arm()
        self.native.failure = True
        self.assertEqual(self.keys.pollOnce(), [])
        self.assertEqual(self.keys.snapshot["status"], "failed")
        self.assertFalse(self.keys.enabled)
        self.native.failure = False
        self.keys.bindWindow(42)
        self.assertTrue(self.keys.enable())
        self.native.valid = False
        self.keys.pollOnce()
        self.assertEqual(self.keys.snapshot["status"], "unknown")
        self.keys.shutdown()
        self.native.valid = True
        self.assertFalse(self.keys.enable())
        before = len(self.native.reads)
        self.keys.pollOnce()
        self.assertEqual(len(self.native.reads), before)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.native, self.backend = Native(), Backend()
        self.capture = ClientCaptureProvider(self.native, self.backend)

    def tearDown(self): self.capture.shutdown()

    def arm(self, region=(0, 0, 100, 80)):
        self.capture.bind(42, region)
        self.assertTrue(self.capture.enable())

    def test_constructor_bind_enable_do_not_capture(self):
        self.assertEqual(self.native.calls, [])
        self.arm()
        self.assertEqual(self.backend.captures, [])
        self.assertEqual(self.native.reads, [])

    def test_exact_client_region_metadata_and_owned_pixels(self):
        self.arm((5, 6, 30, 40))
        result = self.capture.capture()
        self.assertEqual(result.frame_bgr.shape, (34, 25, 3))
        self.assertEqual(result.roi_screen_bounds, (15, 26, 40, 60))
        self.assertEqual(result.acquisition, "client-dc-roi")
        self.assertEqual(self.backend.captures, [(42, (5, 6, 30, 40))])

    def test_disabled_invalid_rectangle_wrong_foreground_and_lost_window(self):
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        for region in ((0, 0, 0, 10), (-1, 0, 5, 10), (0, 0, 3.5, 4)):
            with self.assertRaises((ValueError, TypeError)): self.capture.bind(42, region)
        self.arm()
        self.native.foreground = 77
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        self.native.valid = False
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        self.assertEqual(self.backend.captures, [])

    def test_client_resize_and_bound_context_mismatch_reject_without_capture(self):
        self.arm((0, 0, 101, 80))
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        original = self.backend.current
        self.capture.bind(42, (0, 0, 100, 80), expected_context=original)
        self.capture.enable()
        self.backend.current = ClientCaptureContext((100, 80), (20, 20, 120, 100), 96)
        with self.assertRaises(CaptureContextChanged): self.capture.capture()
        self.assertEqual(self.backend.captures, [])

    def test_geometry_foreground_disable_and_cancellation_during_capture_discard(self):
        self.arm()
        def moved():
            self.backend.current = ClientCaptureContext((100, 80), (30, 20, 130, 100), 96)
        self.backend.on_capture = moved
        with self.assertRaises(CaptureContextChanged): self.capture.capture()
        self.backend.on_capture = lambda: setattr(self.native, "foreground", 77)
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        self.native.foreground = 42
        self.backend.on_capture = self.capture.disable
        with self.assertRaises(CaptureUnavailable): self.capture.capture()
        self.capture.enable()
        cancelled = threading.Event()
        self.backend.on_capture = cancelled.set
        with self.assertRaises(CaptureUnavailable): self.capture.capture(cancelled)

    def test_pre_cancelled_and_shutdown_are_deterministic(self):
        self.arm()
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(CaptureUnavailable): self.capture.capture(cancelled)
        self.assertEqual(self.backend.captures, [])
        self.capture.shutdown()
        self.assertFalse(self.capture.enable())
        with self.assertRaises(CaptureUnavailable): self.capture.bind(42, (0, 0, 100, 80))

    def test_post_capture_current_guard_rejects_focus_move_resize_dpi_and_generation(self):
        self.arm()
        generation = self.capture.generation
        frame = self.capture.capture()
        context = frame.context
        count = len(self.backend.captures)
        self.assertTrue(self.capture.is_current(context, generation))
        self.native.foreground = 77
        self.assertFalse(self.capture.is_current(context, generation))
        self.native.foreground = 42
        for changed in (
            ClientCaptureContext((100, 80), (20, 20, 120, 100), 96),
            ClientCaptureContext((110, 80), (10, 20, 120, 100), 96),
            ClientCaptureContext((100, 80), (10, 20, 110, 100), 144),
        ):
            self.backend.current = changed
            self.assertFalse(self.capture.is_current(context, generation))
        self.backend.current = context
        self.native.valid = False
        self.assertFalse(self.capture.is_current(context, generation))
        self.native.valid = True
        self.capture.disable()
        self.assertFalse(self.capture.is_current(context, generation))
        self.capture.enable()
        self.assertFalse(self.capture.is_current(context, generation))
        self.assertTrue(self.capture.is_current(context, self.capture.generation))
        self.assertEqual(len(self.backend.captures), count)

    def test_current_guard_fails_closed_on_native_error_or_disable_during_query(self):
        self.arm()
        frame = self.capture.capture()
        generation = self.capture.generation
        self.native.failure = True
        self.assertFalse(self.capture.is_current(frame.context, generation))
        self.native.failure = False
        original = self.backend.context
        def query(hwnd):
            self.capture.disable()
            return original(hwnd)
        self.backend.context = query
        self.assertFalse(self.capture.is_current(frame.context, generation))
        self.assertEqual(len(self.backend.captures), 1)


if __name__ == "__main__": unittest.main()
