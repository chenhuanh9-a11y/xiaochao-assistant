"""Fresh explicit sibling transactions after drag, at production boundaries.

Reuse the controlled pixels/solver/native providers from the frozen reachability
test. Session QRunnables, source mailbox -> Coordinator pointer routing, runtime
guards, staged pose core and Coordinator material acceptance remain real. The
author case tests preference/currentness, not an installed author resource or
live QML Image.Ready. The material callback is the actual Coordinator slot.
"""
from copy import deepcopy
import hashlib
import threading
import unittest
from unittest.mock import patch

import numpy as np
from client_live_runtime import _AlignmentHost
import test_client_drag_group_choice as reachability
import test_client_similarity_protocol as groups


class DragGroupChoiceBoundaries(unittest.TestCase):
    mode = "nightmare"
    dispatch = reachability.DragGroupChoiceContracts.dispatch
    _stop_timers = reachability.DragGroupChoiceContracts._stop_timers
    _cleanup_input_session = reachability.DragGroupChoiceContracts._cleanup_input_session
    pointer = reachability.DragGroupChoiceContracts.pointer
    settled = reachability.DragGroupChoiceContracts.settled
    record = reachability.DragGroupChoiceContracts.record
    drive = reachability.DragGroupChoiceContracts.drive
    finish_drag = reachability.DragGroupChoiceContracts.finish_drag
    choose_target = reachability.DragGroupChoiceContracts.choose_target
    await_target_material = reachability.DragGroupChoiceContracts.await_target_material

    def setUp(self):
        reachability.DragGroupChoiceContracts.setUp(self)

    def stage_target(self):
        self.finish_drag()
        self.choose_target()
        return self.await_target_material()

    def assert_target_uncommitted(self, old_token):
        self.coordinator.acceptSiblingMaterial(old_token, True)
        self.settled()
        self.assertIsNone(self.runtime.pending_sibling_payload(old_token))
        self.assertIsNone(self.runtime.render_payload(old_token))
        self.assertNotEqual(self.runtime.snapshot()["currentGroupMemberId"], groups.IDS[1])
        self.assertNotEqual(self.coordinator.alignedData.get("token"), old_token)
        self.engine.rank_established_group.assert_not_called()

    def test_failed_target_preserves_source_pose_frame_and_desired_member(self):
        self.finish_drag()
        source = self.runtime.snapshot()["render"]
        source_token = source["token"]
        source_matrix = np.array(self.runtime.render_payload(source_token)["matrix"])
        source_core, source_group = self.runtime._core, self.runtime._group
        source_root = source_group.root_pose.copy()
        source_observation = self.runtime._last_observation
        reference = self.runtime.reference_payload()
        self.target_geometry_ok = False
        self.choose_target()
        self.drive("refresh_map")
        self.assertEqual(len(self.fit_calls), 2)
        self.assertTrue(all(row["mapId"] == groups.IDS[1] and row["marker"] == 2
                            for row in self.fit_calls), self.fit_calls)
        state = self.runtime.snapshot()
        self.assertEqual(state["currentGroupMemberId"], groups.IDS[0])
        self.assertEqual(state["desiredGroupMemberId"], groups.IDS[1])
        self.assertEqual(state["groupMemberChoiceStatus"], "awaiting-geometry")
        self.assertFalse(self.coordinator.pendingAlignedData)
        self.assertEqual(self.coordinator.alignedData.get("token"), source_token)
        np.testing.assert_allclose(self.runtime.render_payload(source_token)["matrix"], source_matrix)
        np.testing.assert_allclose(source_group.root_pose, source_root)
        self.assertIs(self.runtime._core, source_core)
        self.assertIs(self.runtime._group, source_group)
        self.assertIs(self.runtime._last_observation, source_observation)
        self.assertEqual(state["frameId"], source["frameId"])
        self.assertEqual(self.runtime.reference_payload()["token"], reference["token"])
        self.assertTrue(self.runtime._transient_cycle_current())
        self.engine.rank_established_group.assert_not_called()

    def test_only_material_ack_commits_fresh_frame_and_pointer_context(self):
        self.finish_drag()
        old_frame = self.runtime.snapshot()["frameId"]
        old_generation = self.runtime._frame_generation
        old_observation = self.runtime._last_observation
        source_token = self.runtime.snapshot()["render"]["token"]
        self.choose_target()
        token = self.await_target_material()
        pending = deepcopy(self.coordinator.pendingAlignedData)
        self.assertEqual(self.runtime.snapshot()["frameId"], old_frame)
        self.assertIs(self.runtime._last_observation, old_observation)
        self.assertEqual(self.coordinator.alignedData.get("token"), source_token)
        self.assertNotEqual(pending["frameId"], old_frame)
        self.assertEqual(pending["generation"], old_generation + 1)
        self.assertTrue(self.runtime._transient_cycle_current())
        self.coordinator.acceptSiblingMaterial(token, True)
        self.settled()
        state = self.runtime.snapshot()
        observation = self.runtime._last_observation
        self.assertEqual(state["frameId"], pending["frameId"])
        self.assertEqual(self.runtime._frame_generation, pending["generation"])
        self.assertEqual(observation["frame_id"], state["frameId"])
        self.assertEqual(observation["generation"], pending["generation"])
        self.assertEqual(observation["digest"], hashlib.sha256(observation["frame"].tobytes()).hexdigest())
        self.assertEqual(int(observation["frame"][0, 0, 0]), 2)
        self.assertEqual(observation["cycle_id"], self.initial_cycle)
        self.assertEqual(self.runtime.game_floor_click_context()["frameId"], state["frameId"])
        self.assertFalse(self.runtime._transient_cycle_current())
        self.assertIsNone(self.runtime._transient_drag)
        self.assertEqual(self.coordinator.alignedData.get("token"), token)
        self.assertIsNotNone(self.runtime.render_payload(token))
        self.assertIsNone(self.runtime.render_payload(source_token))
        self.assertTrue(self.runtime._remember_proposals[token]["accepted"])

    def test_committed_target_can_refresh_and_drag_without_old_source_offset(self):
        token = self.stage_target()
        self.coordinator.acceptSiblingMaterial(token, True)
        self.settled()
        self.frame[0, 0] = 3
        result = self.drive("refresh_map")
        self.assertTrue(result["success"], result)
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], groups.IDS[1])
        fit_count = len(self.fit_calls)
        self.pointer("down", 600, 500)
        self.assertIsNotNone(self.coordinator._pointer_drag)
        self.pointer("move", 620, 510)
        self.pointer("up", 625, 513)
        self.settled()
        self.assertTrue(self.runtime._transient_cycle_current())
        self.assertEqual(self.runtime._transient_cycle["offset"], [25., 13.])
        self.assertEqual(self.runtime._transient_cycle["current"]["mapId"], groups.IDS[1])
        self.drive("refresh_map")
        self.assertEqual(len(self.fit_calls), fit_count, "ordinary drag refresh remains observation-only")
        self.assertEqual(self.coordinator.alignedData.get("mapId"), groups.IDS[1])
        self.assertEqual(self.runtime.snapshot()["cycleId"], self.initial_cycle)
        self.engine.rank_established_group.assert_not_called()

    def test_rejected_material_keeps_source_and_desire_then_allows_fresh_retry(self):
        token = self.stage_target()
        source_token = self.coordinator.alignedData["token"]
        source_frame = self.runtime.snapshot()["frameId"]
        self.coordinator.acceptSiblingMaterial(token, False)
        self.settled()
        self.assertEqual(self.coordinator.alignedData.get("token"), source_token)
        self.assertEqual(self.runtime.snapshot()["frameId"], source_frame)
        self.assertEqual(self.runtime.snapshot()["desiredGroupMemberId"], groups.IDS[1])
        self.assertTrue(self.runtime._transient_cycle_current())
        next_token = self.await_target_material()
        self.assertNotEqual(next_token, token)
        self.assertIsNone(self.runtime.pending_sibling_payload(token))

    def test_next_real_drag_invalidates_waiting_target_material(self):
        token = self.stage_target()
        self.pointer("down", 600, 500)
        self.assertIsNotNone(self.coordinator._pointer_drag, "pending target must not swallow actual map motion")
        self.pointer("move", 620, 510)
        self.pointer("up", 625, 513)
        self.settled()
        self.assertEqual(self.runtime._transient_cycle["offset"], [65., 33.])
        self.assert_target_uncommitted(token)

    def test_material_ack_between_next_drag_down_and_move_cannot_replace_pointer_frame(self):
        token = self.stage_target()
        self.pointer("down", 600, 500)
        self.assertIsNotNone(self.coordinator._pointer_drag)
        # Real async material completion may land while the button is held,
        # before the first native move. It must not retire that active gesture.
        self.coordinator.acceptSiblingMaterial(token, True)
        self.pointer("move", 620, 510)
        self.pointer("up", 625, 513)
        self.settled()
        self.assert_target_uncommitted(token)
        self.assertTrue(self.runtime._transient_cycle_current())
        self.assertEqual(self.runtime._transient_cycle["offset"], [65., 33.])

    def test_next_real_drag_cancels_target_solver_before_material_publication(self):
        self.finish_drag()
        entered, release = threading.Event(), threading.Event()
        original = _AlignmentHost.fit
        def blocked(frame, map_id, floor, cancelled, *args, **kwargs):
            if map_id == groups.IDS[1]:
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test did not release target solver")
            return original(frame, map_id, floor, cancelled, *args, **kwargs)
        with patch.object(_AlignmentHost, "fit", side_effect=blocked):
            state = self.session.snapshot
            self.session.dispatchWithPayload("select_group_member", dict(mapId=groups.IDS[1],
                **{key: state[key] for key in ("groupId", "groupGeneration", "cycleId", "floor")}))
            try:
                self.assertTrue(entered.wait(2), "explicit target did not reach fit")
                self.pointer("down", 600, 500)
                self.assertIsNotNone(self.coordinator._pointer_drag)
                self.pointer("move", 620, 510)
            finally:
                release.set()
            self.settled()
        self.assertFalse(self.coordinator.pendingAlignedData)
        self.assertIsNone(self.runtime._pending_sibling)
        self.assertEqual(self.runtime.snapshot()["currentGroupMemberId"], groups.IDS[0])
        self.pointer("up", 625, 513)
        self.settled()
        self.assertEqual(self.runtime._transient_cycle["offset"], [65., 33.])

    def test_floor_change_expires_pending_target_material(self):
        token = self.stage_target()
        self.drive("set_floor", floor="2")
        self.assertEqual(self.runtime.snapshot()["floor"], "2")
        self.assert_target_uncommitted(token)

    def test_author_preference_change_expires_pending_target_material(self):
        token = self.stage_target()
        self.assertTrue(self.session.reconfigure(self.mode, "fixture-other-author"))
        self.settled()
        self.assertEqual(self.runtime.snapshot()["profileId"], "fixture-other-author")
        self.assert_target_uncommitted(token)

    def test_map_close_expires_pending_target_material(self):
        token = self.stage_target()
        self.keys._dispatch_action("escape", {"createdAt": 1.})
        self.settled()
        self.assertFalse(self.runtime.snapshot()["mapOpen"])
        self.assert_target_uncommitted(token)
        self.assertFalse(self.coordinator.alignedData)


class DifficultDragGroupChoiceBoundaries(DragGroupChoiceBoundaries):
    mode = "difficult"


if __name__ == "__main__":
    unittest.main()
