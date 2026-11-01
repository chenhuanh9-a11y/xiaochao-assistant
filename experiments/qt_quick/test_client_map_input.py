"""Ordered input/capture transport fixtures; no native hook, game or screen IO."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import Mock

import numpy as np
from PySide6.QtGui import QGuiApplication

from client_capture import ClientCaptureProvider
from client_input import NativeShortcutBackend
from client_map_input import OrderedMapInput, PointerEventMailbox, DragMotionAccumulator
from client_native_keys import PassThroughKeyboardSource
from client_region_capture import CaptureContextChanged
from test_client_input import Native, Backend

APP = QGuiApplication.instance() or QGuiApplication([])


class OrderedInputTests(unittest.TestCase):
    def setUp(self):
        self.native = Native()
        self.source = PassThroughKeyboardSource()
        self.source.start = Mock(return_value=True)  # Never install a hook.
        self.source.stop = Mock(return_value=True)
        self.keys = NativeShortcutBackend(self.native, source=self.source)
        self.keys.configure({"map": "G"})
        self.keys.bindWindow(42)
        self.keys.enable()
        self.events, self.old = [], []
        self.keys.mapInputRequested.connect(self.events.append)
        self.keys.actionTriggered.connect(self.old.append)
        self.addCleanup(self.keys.shutdown)

    def key(self, vk, down):
        event = self.source._transition_event(vk, 0, 0, down, self.native.foreground)
        self.keys._key_event(event)

    def test_rapid_edges_remain_open_close_open_and_do_not_use_idempotent_action(self):
        self.keys.setOrderedMapInputEnabled(True)
        for _ in range(3):
            self.key(ord("G"), True)
            self.key(ord("G"), False)
        self.assertEqual([row["openIntent"] for row in self.events], [True, False, True])
        self.assertEqual([row["sequence"] for row in self.events], [1, 2, 3])
        self.assertTrue(all(row["hwnd"] == 42 for row in self.events))
        self.assertEqual(self.old, [])
        self.assertIsNone(self.source._thread)

    def test_repeat_text_foreground_and_escape_respect_original_gates(self):
        self.keys.setOrderedMapInputEnabled(True)
        self.key(ord("G"), True)
        self.key(ord("G"), True)
        self.key(ord("G"), False)
        self.keys.setTextEntryActive(True)
        self.key(ord("G"), True); self.key(ord("G"), False)
        self.keys.setTextEntryActive(False)
        self.native.foreground = 99
        self.key(ord("G"), True); self.key(ord("G"), False)
        self.native.foreground = 42
        self.key(0x1b, True)
        self.assertEqual([(r["kind"], r["openIntent"]) for r in self.events],
                         [("map-key", True), ("map-close", False)])
        self.keys.resetMapInput()
        self.key(ord("G"), True)
        self.assertEqual((self.events[-1]["sequence"], self.events[-1]["openIntent"]), (3, True))

    def test_legacy_consumer_remains_until_explicit_ownership_transfer(self):
        self.key(ord("G"), True)
        self.assertEqual(self.old, ["map"])
        self.assertEqual(self.events, [])

    def test_pointer_motion_is_bounded_coalesced_and_keeps_complete_displacement(self):
        self.keys.setOrderedMapInputEnabled(True)
        self.source.set_pointer_context_provider(lambda: dict(epoch=1, clientBounds=[0, 0, 100, 80]))
        self.source.update_pointer_target(42)
        events = []
        self.keys.gamePointerEvent.connect(events.append)
        for phase, x, y in [("down", 20, 30), ("move", 23, 35), ("move", 31, 40), ("up", 32, 42)]:
            event = self.source._pointer_event(x, y, 42, phase=phase, created_at=10.)
            self.source._queue_pointer_event(event)
        self.keys._drain_pointer_events()
        self.assertEqual([r["phase"] for r in events], ["down", "move", "up"])
        self.assertEqual(events[1]["delta"], [11, 10])
        self.assertEqual(events[1]["totalDelta"], [11, 10])
        self.assertEqual(events[2]["totalDelta"], [12, 12])
        self.assertEqual(len({r["gestureId"] for r in events}), 1)

    def test_lost_foreground_cancels_gesture_and_no_unpressed_move_is_observed(self):
        self.source.set_pointer_context_provider(lambda: dict(epoch=1))
        self.source.update_pointer_target(42)
        self.assertIsNone(self.source._pointer_event(10, 10, 42, phase="move"))
        self.source._queue_pointer_event(self.source._pointer_event(10, 10, 42))
        self.assertIsNone(self.source._pointer_event(20, 20, 99, phase="move"))
        rows = self.source.take_pointer_events()
        self.assertEqual([r["phase"] for r in rows], ["down", "cancel"])
        self.assertIsNone(self.source._pointer_gesture)

    def test_runtime_lock_busy_after_press_does_not_cancel_physical_drag(self):
        provider = Mock(side_effect=[dict(epoch=1, clientBounds=[0, 0, 1000, 1000]), None])
        self.source.set_pointer_context_provider(provider)
        self.source.update_pointer_target(42)
        for phase, x in (("down", 300), ("move", 330), ("up", 340)):
            event = self.source._pointer_event(x, 300, 42, phase=phase)
            self.assertIsNotNone(event)
            self.source._queue_pointer_event(event)
        self.assertEqual(provider.call_count, 1)
        rows = self.source.take_pointer_events()
        self.assertEqual([r["phase"] for r in rows], ["down", "move", "up"])
        self.assertEqual(rows[-1]["totalDelta"], [40, 0])

    def test_queued_press_cannot_acquire_a_newer_frame_context(self):
        self.source.set_pointer_context_provider(lambda: dict(epoch=2, observationCreatedAt=20.))
        self.source.update_pointer_target(42)
        self.assertIsNone(self.source._pointer_event(300, 300, 42, phase="down", created_at=19.))
        self.assertIsNone(self.source._pointer_gesture)
        self.assertIsNotNone(self.source._pointer_event(300, 300, 42, phase="down", created_at=21.))

    def test_queue_overflow_cancels_instead_of_inventing_missing_edges(self):
        mailbox = PointerEventMailbox(maximum=3)
        wakes = [mailbox.post(dict(phase="down", x=i, y=0, sequence=i)) for i in range(4)]
        self.assertEqual(wakes, [True, False, False, False])
        self.assertEqual(mailbox.take()[0]["phase"], "cancel")
        self.assertTrue(mailbox.post(dict(phase="up")))

    def test_coalesced_motion_keeps_calibrated_exit_and_reentry_without_catch_up(self):
        mailbox = PointerEventMailbox()
        for x in (350, 900, 750, 700):
            mailbox.post(dict(phase="move", x=x, y=300, gestureId=1,
                context=dict(epoch=1, clientBounds=[0, 0, 1000, 1000], dragBoundaryNormalized=[.2, .2, .8, .8])))
        events = mailbox.take()
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]["motionSamples"]), 4)
        drag = DragMotionAccumulator((300, 300), (0, 0, 1000, 1000), (.2, .2, .8, .8))
        motion = drag.consume(events[0])
        self.assertEqual(motion["totalDelta"], [0., 0.])
        final = drag.consume(dict(phase="up", x=720, y=300))
        self.assertEqual(final["totalDelta"], [20., 0.])
        self.assertTrue(final["finished"])

    def test_boundary_transition_overflow_cancels_and_context_changes_do_not_coalesce(self):
        mailbox = PointerEventMailbox(maximum=3)
        for x in (300, 900, 300, 900):
            mailbox.post(dict(phase="move", x=x, y=300, gestureId=1,
                context=dict(epoch=1, clientBounds=[0, 0, 1000, 1000], dragBoundaryNormalized=[.2, .2, .8, .8])))
        self.assertEqual(mailbox.take()[0]["reason"], "pointer-motion-overflow")
        mailbox.post(dict(phase="move", x=1, y=0, gestureId=1, context=dict(epoch=1)))
        mailbox.post(dict(phase="move", x=2, y=0, gestureId=1, context=dict(epoch=2)))
        self.assertEqual(len(mailbox.take()), 2)

    def test_high_rate_motion_compacts_exactly_without_cancelling_or_boundary_jump(self):
        for boundary in (None, [.2, .2, .8, .8]):
            with self.subTest(boundary=boundary):
                mailbox = PointerEventMailbox()
                direct = DragMotionAccumulator((300, 300), (0, 0, 1000, 1000), boundary)
                context = dict(epoch=1, clientBounds=[0, 0, 1000, 1000], dragBoundaryNormalized=boundary)
                for x in [300 + i % 450 for i in range(2000)] + [900, 850, 750, 730, 710]:
                    event = dict(phase="move", x=x, y=300, gestureId=1, context=context)
                    direct.consume(event)
                    mailbox.post(event)
                rows = mailbox.take()
                self.assertEqual([r["phase"] for r in rows], ["move"])
                self.assertLessEqual(len(rows[0]["motionSamples"]), 6)
                compacted = DragMotionAccumulator((300, 300), (0, 0, 1000, 1000), boundary)
                self.assertEqual(compacted.consume(rows[0])["totalDelta"], direct.total)

    def test_plain_drag_retains_release_delta_but_cancel_never_adds_motion(self):
        drag = DragMotionAccumulator((300, 300), (0, 0, 1000, 1000))
        self.assertEqual(drag.consume(dict(phase="move", x=900, y=300))["delta"], [600., 0.])
        self.assertEqual(drag.consume(dict(phase="up", x=910, y=305))["totalDelta"], [610., 5.])
        self.assertEqual(drag.consume(dict(phase="cancel", x=1000, y=400))["delta"], [0., 0.])
        with self.assertRaises(ValueError):
            DragMotionAccumulator((5, 5), (0, 0, 1000, 1000))


class InjectedFallbackTests(unittest.TestCase):
    def capture(self, full_capture=None):
        native, backend = Native(), Backend()
        backend.capture = Mock(side_effect=OSError("fixture DC unavailable"))
        capture = ClientCaptureProvider(native, backend, full_capture=full_capture)
        capture.bindFullClient(42)
        self.assertTrue(capture.enable())
        self.addCleanup(capture.shutdown)
        return capture, backend

    def test_no_default_screen_fallback_and_original_error_is_preserved(self):
        capture, _ = self.capture()
        with self.assertRaisesRegex(OSError, "fixture DC unavailable"):
            capture.capture()

    def test_only_explicit_owner_excluding_callback_is_used_and_full_frame_contract_survives(self):
        phases = []
        def fallback(hwnd):
            phases.append(("exclude-owned-surfaces", hwnd))
            try:
                return np.full((80, 100, 3), 7, np.uint8), (10, 20, 110, 100)
            finally:
                phases.append(("restore-owned-surfaces", hwnd))
        capture, _ = self.capture(fallback)
        result = capture.capture()
        self.assertEqual(phases, [("exclude-owned-surfaces", 42), ("restore-owned-surfaces", 42)])
        self.assertEqual(result.acquisition, "full-frame-fallback-crop")
        self.assertEqual(result.roi_client_bounds, (0, 0, 100, 80))
        self.assertEqual(result.frame_bgr.shape, (80, 100, 3))

    def test_injected_fallback_cannot_smuggle_wrong_geometry(self):
        capture, _ = self.capture(lambda hwnd: (np.zeros((80, 100, 3), np.uint8), (0, 0, 100, 80)))
        with self.assertRaises(CaptureContextChanged):
            capture.capture()


if __name__ == "__main__":
    unittest.main()
