"""Ordered floor-click intent, synthetic capture only; no hook/game/input IO."""
import copy
import threading
import unittest
from unittest.mock import Mock, patch

import test_client_live_runtime as fixtures
from client_live_runtime import _AlignmentHost
from floor_observation_policy import game_floor_control_hit


class FloorClickContracts(unittest.TestCase):
    setUp = fixtures.LiveRuntimeContracts.setUp
    dispatch = fixtures.LiveRuntimeContracts.dispatch

    def prepare(self, mode="nightmare"):
        self.now = 10.
        self.runtime._monotonic = lambda: self.now
        self.capture.generation = 5
        self.read = dict(selectedFloor=1, layout="localized-triplet", centers=[.45, .50, .55], scores={})
        detector = patch("map_interface_observation.detect_selected_floor", side_effect=lambda *a: copy.deepcopy(self.read))
        detector.start(); self.addCleanup(detector.stop)
        if mode == "difficult":
            self.engine = fixtures.fixture_engine("difficult")
            self.engine.qt_map_ui.observe = lambda frame: dict(mapOpen=True, floor=str(self.read["selectedFloor"]),
                glyphs=[dict(center=[600., 40.]), dict(center=[720., 40.])], controlScale=[1., 1.], highlightDifference=18.)
            self.runtime._factories["difficult"] = lambda:self.engine
            self.assertTrue(self.dispatch("set_game_mode", mode="difficult")["success"])
        self.assertTrue(self.dispatch("start_round")["success"])
        self.assertTrue(self.dispatch("capture_now")["success"])
        return self.runtime.game_floor_click_context()

    def xy(self, floor, mode="nightmare"):
        if mode == "difficult": return (700 if floor == 1 else 820), 240
        return 100 + {-1:.45, 1:.50, 2:.55}[floor]*1280, 230

    def click(self, floor, context, mode="nightmare"):
        return self.runtime.observe_game_floor_click(*self.xy(floor, mode), context)

    def test_click_waits_for_fresh_matching_highlight_in_both_modes(self):
        for mode in ("nightmare", "difficult"):
            with self.subTest(mode=mode):
                if self.runtime.snapshot()["roundActive"]: self.dispatch("end_round")
                context = self.prepare(mode)
                count = self.capture.count
                accepted = self.click(2, context, mode)
                self.assertTrue(accepted["success"], accepted)
                self.assertEqual(accepted["expiresAt"], context["createdAt"] + 2.)
                self.assertEqual(self.capture.count, count)
                self.assertEqual(self.runtime.snapshot()["floor"], "2")
                self.assertEqual(self.runtime.snapshot()["observedFloor"], "1")
                self.assertTrue(self.runtime.snapshot()["floorRequired"])
                self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
                self.assertIsNone(self.runtime.reference_payload())
                old = self.dispatch("refresh_map")
                self.assertFalse(old["success"])
                self.assertEqual(self.runtime.snapshot()["floor"], "2")
                self.read["selectedFloor"] = 2
                current = self.dispatch("refresh_map")
                self.assertTrue(current["success"], current)
                self.assertEqual(self.runtime.snapshot()["floor"], "2")
                self.assertEqual(self.runtime.render_payload(current["renderToken"])["floor"], "2")
                self.assertIsNone(self.runtime._game_floor_intent)

    def test_click_overrides_manual_hold_and_lock_only_with_matching_pixels(self):
        self.prepare()
        self.dispatch("set_floor", floor="1")
        self.runtime._floor_hard_locked = True
        context = self.runtime.game_floor_click_context()
        self.assertTrue(self.click(2, context)["success"])
        self.assertEqual(self.runtime.snapshot()["floor"], "2")
        hold_until = self.runtime._manual_hold_until
        self.read["selectedFloor"] = 2
        self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.snapshot()["floor"], "2")
        self.assertTrue(self.runtime._manual_override)
        self.assertTrue(self.runtime._floor_hard_locked)
        self.assertEqual(self.runtime._manual_hold_until, hold_until)

    def test_automatic_click_has_no_new_hard_lock_and_later_stable_floor_can_return(self):
        context = self.prepare()
        self.assertTrue(self.click(2, context)["success"])
        self.assertFalse(self.runtime._floor_hard_locked)
        self.read["selectedFloor"] = 2
        self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertFalse(self.runtime._floor_hard_locked)
        self.read["selectedFloor"] = 1
        self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.snapshot()["floor"], "1")

    def test_existing_manual_hold_rejects_conflicting_pixels_until_expiry(self):
        self.prepare()
        self.dispatch("set_floor", floor="1")
        hold = self.runtime._manual_hold_until
        self.assertTrue(self.click(2, self.runtime.game_floor_click_context())["success"])
        self.read["selectedFloor"] = 2
        self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertTrue(self.runtime._manual_override)
        self.assertEqual(self.runtime._manual_hold_until, hold)
        self.read["selectedFloor"] = 1
        with patch.object(_AlignmentHost, "fit", side_effect=AssertionError("conflict reached fit")):
            blocked = self.dispatch("refresh_map")
        self.assertEqual(blocked["reason"], "current-frame-floor-conflict")
        self.assertEqual((blocked["observedFloor"], blocked["targetFloor"], blocked["fitAttempted"]), ("1", "2", False))
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.now = hold + .001
        self.assertTrue(self.dispatch("refresh_map")["success"])
        self.assertEqual(self.runtime.snapshot()["floor"], "1")

    def test_ttl_starts_at_press_and_expired_intent_does_not_force_target(self):
        context = self.prepare()
        self.now = context["createdAt"] + 2.001
        epoch = self.runtime._epoch
        self.assertEqual(self.click(2, context)["reason"], "floor-click-expired")
        self.assertEqual(self.runtime._epoch, epoch)
        context = self.runtime.game_floor_click_context()
        self.assertTrue(self.click(2, context)["success"])
        self.now = context["createdAt"] + 2.001
        result = self.dispatch("refresh_map")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.runtime.snapshot()["floor"], "1")
        self.assertIsNone(self.runtime._game_floor_intent)

    def test_queued_clicks_are_each_consumed_in_order_without_restarting_ttl(self):
        context = self.prepare()
        events = []; self.runtime.set_event_callback(events.append)
        self.now += .1
        first = self.click(2, context)
        second = self.click(1, context)
        self.assertTrue(first["success"]); self.assertTrue(second["success"])
        self.assertEqual(first["expiresAt"], second["expiresAt"])
        self.assertEqual(self.runtime._game_floor_intent["floor"], 1)
        self.assertEqual([e["clickedFloor"] for e in events if e["event"] == "game-floor-control-intent"], [2, 1])
        self.runtime.invalidate("foreground-lost")
        self.assertFalse(self.click(2, context)["success"])
        self.assertIsNone(self.runtime._game_floor_intent)

    def test_hook_context_is_nonblocking_metadata_and_never_calls_backend(self):
        self.prepare()
        self.capture.is_current = Mock(side_effect=AssertionError("hook getter called backend"))
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def own_lock():
            with self.runtime._lock:
                entered.set(); release.wait(4)
        holder = threading.Thread(target=own_lock); holder.start(); self.assertTrue(entered.wait(4))
        output = []
        reader = threading.Thread(target=lambda:(output.append(self.runtime.game_floor_click_context()), finished.set()))
        reader.start()
        try: self.assertTrue(finished.wait(.5), "hook getter blocked on runtime state")
        finally: release.set(); holder.join(4); reader.join(4)
        self.assertEqual(output, [None])
        self.assertIsNotNone(self.runtime.game_floor_click_context())
        self.capture.is_current.assert_not_called()

    def test_old_scope_bounds_and_binding_generation_are_rejected(self):
        context = self.prepare()
        for field, value in (("epoch", -1), ("cycleId", -1), ("roundId", -1), ("frameId", "old"),
                ("generation", -1), ("captureGeneration", -1), ("clientBounds", [0,0,1280,720]),
                ("fullClientSize", [100,100]), ("dpi", 120), ("mode", "difficult")):
            with self.subTest(field=field):
                stale = dict(context, **{field:value})
                self.assertEqual(self.click(2, stale)["reason"], "floor-click-context-stale")
        self.assertTrue(self.click(2, context)["success"])
        self.dispatch("close_map")
        self.assertIsNone(self.runtime._game_floor_intent)
        self.assertFalse(self.click(2, context)["success"])
        self.dispatch("capture_now")
        self.assertFalse(self.click(2, context)["success"])
        self.dispatch("end_round")
        self.assertIsNone(self.runtime.game_floor_click_context())

    def test_missing_hit_or_changed_capture_context_does_not_apply_intent(self):
        context = self.prepare()
        epoch = self.runtime._epoch
        self.runtime._last_observation["floor_read"]["centers"] = ["invalid", .50, .55]
        self.assertEqual(self.click(2, context)["reason"], "floor-click-not-a-control")
        self.assertEqual(self.runtime._epoch, epoch)
        self.runtime._last_observation["floor_read"]["centers"] = [.45, .50, .55]
        self.capture.is_current = lambda *a, **k:False
        self.assertEqual(self.click(2, context)["reason"], "capture-context-changed")
        self.assertEqual(self.runtime._epoch, epoch)
        self.assertIsNone(self.runtime._game_floor_intent)

    def test_click_invalidates_inflight_pose_without_capturing_or_publishing_late(self):
        self.prepare()
        entered, release = threading.Event(), threading.Event()
        def slow(*a):
            entered.set(); self.assertTrue(release.wait(4)); return copy.deepcopy(self.fit)
        output = []
        with patch.object(_AlignmentHost, "fit", side_effect=slow):
            worker = threading.Thread(target=lambda:output.append(self.dispatch("capture_now")))
            worker.start(); self.assertTrue(entered.wait(4))
            context = self.runtime.game_floor_click_context()
            captures = self.capture.count
            self.assertTrue(self.click(2, context)["success"])
            self.assertEqual(self.capture.count, captures)
            release.set(); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertTrue(output[0]["stale"])
        self.assertFalse(self.runtime.snapshot()["displayAuthorized"])
        self.assertEqual(self.runtime.snapshot()["floor"], "2")
        self.assertEqual(self.runtime.snapshot()["observedFloor"], "1")


class SharedFloorHitContracts(unittest.TestCase):
    def test_original_localized_triplet_and_centered_safe_viewport(self):
        read = dict(layout="localized-triplet", centers=[.45, .50, .55])
        for floor, x in ((-1,.45),(1,.50),(2,.55)):
            self.assertEqual(game_floor_control_hit((100+x*1280,230), (100,200,1380,920), read), floor)
        self.assertIsNone(game_floor_control_hit((740,230), (100,200,1380,1000), read))
        self.assertEqual(game_floor_control_hit((740,270), (100,200,1380,1000), read), 1)
        self.assertIsNone(game_floor_control_hit((740,230), (100,200,1380,920), dict(read,layout="legacy")))

    def test_original_native_glyph_midpoint_scale_and_map_open_gates(self):
        native = dict(mapOpen=True, glyphs=[dict(center=[600.,40.]),dict(center=[720.,40.])],controlScale=[1.,1.])
        read = dict(layout="difficult-two-buttons", centers=[.4,.6], difficultObservation=native)
        self.assertEqual(game_floor_control_hit((700,240), (100,200,1380,920), read), 1)
        self.assertEqual(game_floor_control_hit((760,240), (100,200,1380,920), read), 2)
        self.assertIsNone(game_floor_control_hit((820,263), (100,200,1380,920), read))
        native["mapOpen"] = False
        self.assertIsNone(game_floor_control_hit((700,240), (100,200,1380,920), read))

if __name__ == "__main__": unittest.main()
