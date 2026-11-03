"""Real Qt input/coordinator/runtime seam; synthetic capture/fit, no native IO.

Input uses NativeShortcutBackend's production action consumer. Runtime delivery
uses the real queued Qt signal. Session scheduling and visual evidence are
fixtures; this does not claim real keyboard/game/visible-pixel validation.
"""
import copy
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from client_input import NativeShortcutBackend
import test_client_coordinator as coordinator_fixture
import test_client_live_runtime as runtime_fixture


class MapIntentResetTests(unittest.TestCase):
    @contextmanager
    def chain(self, mode):
        owner = coordinator_fixture.CoordinatorTests()
        owner.setUpClass()
        owner.setUp()
        live = runtime_fixture.LiveRuntimeContracts()
        live.setUp()
        class BoundCapture(runtime_fixture.FixtureCapture):
            @property
            def snapshot(self):
                return dict(super().snapshot, hwnd="123")
        live.capture = BoundCapture()
        live.runtime._capture = live.capture
        keys = NativeShortcutBackend(owner.native)
        keys._hwnd = 123
        keys.setOrderedMapInputEnabled(True)
        c = owner.coordinator
        c.input = keys
        c.runtime = live.runtime
        c._applied_context = (mode, "profile-one")
        live.dispatch("set_context", mode=mode, profileId="profile-one")
        events, actions = [], []
        def notify(event):
            events.append(copy.deepcopy(event))
            c.runtimeEvent.emit(event)
        live.runtime.set_event_callback(notify)
        keys.mapInputRequested.connect(c._map_input_requested)
        def dispatch(action, payload):
            actions.append((action, dict(payload)))
            result = live.dispatch(action, **payload)
            owner.session.snapshot = live.runtime.snapshot()
            return result
        owner.session.dispatchWithPayload.side_effect = dispatch
        owner.session.refreshRuntime.side_effect = lambda: None
        try:
            yield owner, live, keys, events, actions
        finally:
            live.runtime.set_event_callback(None)
            keys.shutdown()
            owner.tearDown()
            live.doCleanups()

    def press(self, keys):
        keys._dispatch_action("map", dict(createdAt=1.0))

    def test_unconfirmed_preparation_timeout_retains_intent_and_next_edge_closes(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (owner, live, keys, events, actions):
                with patch("map_open_preparation.prepare_map_open_frame", return_value=None):
                    self.press(keys)
                owner.app.processEvents()
                self.assertFalse(live.runtime.snapshot()["mapOpen"])
                self.assertTrue(live.runtime.snapshot()["mapRecoveryPending"])
                self.assertTrue(keys._map_input.open_intent)
                self.press(keys)
                self.assertEqual([a for a, _ in actions], ["map", "close_map"])
                self.assertFalse(live.runtime.snapshot()["mapOpen"])
                self.assertFalse(live.runtime.snapshot()["mapRecoveryPending"])
                self.assertEqual([p["inputSequence"] for _, p in actions], [1, 2])

    def test_passive_close_next_edge_opens_but_first_miss_does_not_reset(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (owner, live, keys, events, actions):
                self.press(keys)
                owner.app.processEvents()
                detector = (patch("map_interface_observation.detect_map_interface", return_value=dict(detected=False))
                    if mode == "nightmare" else patch.object(live.runtime._engine.qt_map_ui, "observe", return_value=dict(mapOpen=False)))
                with detector:
                    live.dispatch("refresh_map")
                    owner.app.processEvents()
                    self.assertTrue(keys._map_input.open_intent)
                    self.assertTrue(live.runtime.snapshot()["mapOpen"])
                    live.dispatch("refresh_map")
                owner.app.processEvents()
                self.assertFalse(live.runtime.snapshot()["mapOpen"])
                self.press(keys)
                self.assertEqual([a for a, _ in actions], ["map", "map"])
                self.assertTrue(live.runtime.snapshot()["mapOpen"])

    def test_late_close_does_not_reset_new_open_close_open_sequence(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (owner, live, keys, events, actions):
                with patch("map_open_preparation.prepare_map_open_frame", return_value=None):
                    self.press(keys)
                # Do not deliver the close event until a later open is current.
                self.press(keys)
                self.press(keys)
                owner.app.processEvents()
                self.assertEqual([a for a, _ in actions], ["map", "close_map", "map"])
                self.assertTrue(keys._map_input.open_intent)
                self.assertTrue(live.runtime.snapshot()["mapOpen"])
                self.press(keys)
                self.assertEqual(actions[-1][0], "close_map")

    def test_display_only_revocations_do_not_change_next_close_edge(self):
        for mode in ("nightmare", "difficult"):
            for reason in ("current-frame-residual-failed", "current-floor-unavailable", "observed-floor-changed"):
                with self.subTest(mode=mode, reason=reason), self.chain(mode) as (owner, live, keys, events, actions):
                    self.press(keys)
                    live.runtime._revoke_render(reason)
                    owner.app.processEvents()
                    self.assertTrue(keys._map_input.open_intent)
                    self.press(keys)
                    self.assertEqual(actions[-1][0], "close_map")

    def test_observed_open_with_unsupported_floor_keeps_next_key_a_close(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (owner, live, keys, events, actions):
                self.press(keys)
                owner.app.processEvents()
                self.assertTrue(live.runtime.snapshot()["mapOpen"])
                self.press(keys)
                owner.app.processEvents()
                # The real observation/preparation path sees map-open and F1,
                # but this synthetic resource contract supports only F2. Do
                # not stub _observe, preparation, or call invalidate directly.
                live.runtime._engine.identity_floors = ("2",)
                with patch.object(runtime_fixture._AlignmentHost, "fit",
                                  side_effect=AssertionError("unsupported floor must not solve")):
                    self.press(keys)
                owner.app.processEvents()
                packet = next(e for e in reversed(events) if e["event"] == "render-revoked")
                self.assertEqual(packet["reason"], "floor-unknown")
                self.assertFalse(live.runtime.snapshot()["displayAuthorized"])
                self.assertIsNone(live.runtime.render_payload(packet["token"]))
                self.press(keys)
                self.assertEqual([a for a, _ in actions], ["map", "close_map", "map", "close_map"])
                self.assertFalse(actions[-1][1]["openIntent"])

    def test_runtime_floor_resource_and_focus_invalidations_do_not_reset_input(self):
        for mode in ("nightmare", "difficult"):
            for reason, closed in (("floor-unknown", False), ("resource-changed", False),
                                   ("foreground-lost", False), ("capture-context-changed", False),
                                   ("floor-changed", False)):
                with self.subTest(mode=mode, reason=reason), self.chain(mode) as (owner, live, keys, events, actions):
                    self.press(keys)
                    live.runtime.invalidate(reason)
                    owner.app.processEvents()
                    self.assertEqual(keys._map_input.open_intent, not closed)
                    packet = next(e for e in reversed(events) if e["event"] == "render-revoked")
                    self.assertEqual(packet["mapClosed"], closed)
                    self.assertEqual(packet["inputSequence"], 1)

    def test_close_packet_requires_exact_sequence_window_and_lifetime(self):
        replacements = dict(inputSequence=0, inputWindow="456", epoch=-1, roundId=-1,
            generation=-1, frameId="old-frame", cycleId=-1, mode="other", profileId="other")
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode), self.chain(mode) as (owner, live, keys, events, actions):
                self.press(keys)
                # Only a confirmed close emits mapClosed. An unobserved
                # preparation deadline now has a bounded recovery ticket.
                live.dispatch("close_map", inputSequence=1)
                packet = next(e for e in reversed(events) if e["event"] == "render-revoked")
                self.assertTrue(packet["mapClosed"])
                for field, value in replacements.items():
                    with self.subTest(field=field):
                        owner.coordinator._on_runtime_event(dict(packet, **{field: value}))
                        self.assertTrue(keys._map_input.open_intent)
                owner.app.processEvents()
                self.assertFalse(keys._map_input.open_intent)

    def test_queued_close_after_end_or_rebind_cannot_reset_same_sequence(self):
        for mode in ("nightmare", "difficult"):
            for action, payload in (("end_round", {}), ("bind_capture", {"hwnd": 456})):
                with self.subTest(mode=mode, action=action), self.chain(mode) as (owner, live, keys, events, actions):
                    self.press(keys)
                    live.dispatch("close_map", inputSequence=1)
                    live.dispatch(action, **payload)
                    owner.app.processEvents()
                    self.assertTrue(keys._map_input.open_intent)


if __name__ == "__main__":
    unittest.main()
